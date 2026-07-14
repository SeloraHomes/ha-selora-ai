"""Tests for the atomic insights export contract.

The guarantees under test are the ones the Selora OS host relies on:
  * the manifest points to a complete, checksum-verifiable artifact
  * published artifacts are immutable (no torn reads across a re-publish)
  * retention prunes old generations, leaving the newest intact
  * orphaned tmp files from a crash are swept
  * staleness is computable (the "HA went silent" host signal)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import gzip
import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai.insights_export import (
    InsightsExporter,
    _prune_artifacts,
    _publish_blocking,
)


def _publish(base: Path, seq: int, *, retention: int = 3, collection=None) -> dict:
    base_dir = base
    artifact_dir = base / "exports"
    manifest_path = base / "manifest.json"
    collection = collection or {"status": "ok", "partial_reason": None}
    generated = datetime.now(UTC)
    envelope = {
        "schema_version": 1,
        "sequence": seq,
        "generated_at": generated.isoformat(),
        "signals": [{"signal_id": "flapping:light.x", "severity": "warning"}],
        "insights": [],
        "inventory": {"entities": 1},
        "collection": collection,
    }
    return _publish_blocking(
        base_dir,
        artifact_dir,
        manifest_path,
        seq,
        envelope,
        {"signals_active": 1},
        {"integration_version": "0.0.0", "ha_version": "test"},
        collection,
        generated.isoformat(),
        (generated + timedelta(seconds=900)).isoformat(),
        900,
        retention,
    )


def test_export_artifacts_and_dirs_are_owner_only(tmp_path):
    """The export carries household identities + state, so its dir is 0o700 and
    the artifact + manifest are 0o600 — not the world-readable umask default."""
    import stat

    _publish(tmp_path, 1000)
    artifact_dir = tmp_path / "exports"
    artifact = next(artifact_dir.glob("insights-*.json.gz"))
    manifest = tmp_path / "manifest.json"

    assert stat.S_IMODE(artifact_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o600


def test_manifest_points_to_verifiable_complete_artifact(tmp_path):
    manifest = _publish(tmp_path, 1000)

    manifest_file = tmp_path / "manifest.json"
    assert manifest_file.exists()
    on_disk = json.loads(manifest_file.read_text())
    assert on_disk == manifest
    assert manifest["status"] == "complete"
    assert manifest["sequence"] == 1000

    artifact = tmp_path / manifest["artifact"]["path"]
    assert artifact.exists()
    payload = artifact.read_bytes()
    # sha and size in the manifest match the artifact bytes exactly.
    assert hashlib.sha256(payload).hexdigest() == manifest["artifact"]["sha256"]
    assert len(payload) == manifest["artifact"]["size_bytes"]
    # The envelope round-trips.
    envelope = json.loads(gzip.decompress(payload))
    assert envelope["sequence"] == 1000
    assert envelope["signals"][0]["signal_id"] == "flapping:light.x"


def test_partial_collection_marks_manifest_partial(tmp_path):
    manifest = _publish(
        tmp_path, 1, collection={"status": "partial", "partial_reason": "insight_build_failed"}
    )
    assert manifest["status"] == "partial"
    assert manifest["collection"]["partial_reason"] == "insight_build_failed"


def test_published_artifacts_are_immutable(tmp_path):
    """A re-publish must never mutate an already-published artifact — this is
    what makes the host's copy race-free."""
    m1 = _publish(tmp_path, 1, retention=5)
    artifact1 = tmp_path / m1["artifact"]["path"]
    bytes_before = artifact1.read_bytes()

    _publish(tmp_path, 2, retention=5)

    assert artifact1.exists()
    assert artifact1.read_bytes() == bytes_before  # untouched by seq 2


def test_retention_prunes_oldest_keeps_newest(tmp_path):
    for seq in (1, 2, 3):
        _publish(tmp_path, seq, retention=2)

    remaining = sorted(p.name for p in (tmp_path / "exports").glob("insights-*.json.gz"))
    assert len(remaining) == 2
    # Newest two survive; oldest (seq 1) pruned.
    assert remaining[0].endswith("000002.json.gz")
    assert remaining[1].endswith("000003.json.gz")

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["sequence"] == 3


def test_prune_keeps_all_when_retention_exceeds_count(tmp_path):
    artifact_dir = tmp_path / "exports"
    artifact_dir.mkdir()
    for seq in (1, 2):
        (artifact_dir / f"insights-{seq:015d}.json.gz").write_bytes(b"x")
    removed = _prune_artifacts(artifact_dir, retention=5)
    assert removed == 0
    assert len(list(artifact_dir.glob("*.json.gz"))) == 2


def test_sweep_orphan_tmp_removes_only_tmp(tmp_path):
    exporter = _stub_exporter(tmp_path)
    exporter.base_dir.mkdir(parents=True)
    exporter.artifact_dir.mkdir(parents=True)
    (exporter.base_dir / ".tmp-manifest-123.json").write_bytes(b"junk")
    (exporter.artifact_dir / ".tmp-9-42.gz").write_bytes(b"junk")
    keep = exporter.artifact_dir / "insights-000000000000001.json.gz"
    keep.write_bytes(b"real")

    removed = exporter._sweep_orphan_tmp()

    assert removed == 2
    assert keep.exists()
    assert not (exporter.base_dir / ".tmp-manifest-123.json").exists()


def _stub_exporter(tmp_path: Path) -> InsightsExporter:
    """An exporter whose only real dependency is config_dir (for sync path ops)."""
    hass = SimpleNamespace(config=SimpleNamespace(config_dir=str(tmp_path)))
    return InsightsExporter(hass, health_store=None, insights_engine=None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_exporter_drains_inflight_publish_on_stop(hass: HomeAssistant) -> None:
    """A periodic publish in flight during a reload must be cancelled/drained on
    unload, so the old exporter can't race the replacement one — overwriting the
    manifest with an older generation or colliding on a tmp artifact name."""
    exporter = InsightsExporter(hass, health_store=None, insights_engine=None)  # type: ignore[arg-type]
    started = asyncio.Event()
    release = asyncio.Event()
    state = {"completed": False}

    async def blocking_publish() -> None:
        started.set()
        await release.wait()
        state["completed"] = True

    exporter.async_publish = blocking_publish  # type: ignore[method-assign]

    # Fire a periodic tick the way async_track_time_interval would.
    exporter._periodic_publish(None)
    await asyncio.wait_for(started.wait(), timeout=1)
    assert any(not t.done() for t in exporter._tasks)

    await exporter.async_stop()

    assert exporter._tasks == set()
    assert state["completed"] is False  # cancelled before finishing


class _FakeStore:
    async def next_export_sequence(self, epoch_seconds: int) -> int:
        return epoch_seconds

    async def get_active_signals(self) -> list:
        return []


class _FakeInsights:
    async def async_get_insights(self) -> list:
        return []


@pytest.mark.asyncio
async def test_stop_waits_for_executor_write_to_finish(hass: HomeAssistant) -> None:
    """The atomic manifest write runs on an executor thread that can't be
    cancelled once started. async_stop must WAIT for that write to finish, not
    just cancel the awaiting task — otherwise the stale worker could land its
    lower sequence after the replacement exporter published a newer one."""
    exporter = InsightsExporter(hass, health_store=_FakeStore(), insights_engine=_FakeInsights())  # type: ignore[arg-type]
    # Host opt-in marker so _do_publish proceeds all the way to the write.
    exporter.base_dir.mkdir(parents=True, exist_ok=True)
    exporter.marker_path.write_text("")

    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    def blocking_publish(*args: object, **kwargs: object) -> dict:
        started.set()
        release.wait(5)
        completed.set()
        return {"sequence": args[3]}  # seq is the 4th positional arg

    roster = {
        "truncated": False,
        "integrations": [],
        "devices": [],
        "entities": [],
        "unavailable_total": 0,
        "disabled_total": 0,
    }

    with (
        patch(
            "custom_components.selora_ai.insights_export._publish_blocking",
            blocking_publish,
        ),
        patch(
            "custom_components.selora_ai.insights_export.build_home_roster",
            return_value=roster,
        ),
    ):
        # Fire a publish and wait until the write is running on the worker thread.
        exporter._periodic_publish(None)
        await hass.async_add_executor_job(started.wait, 5)
        assert exporter._write_future is not None
        assert not exporter._write_future.done()

        # async_stop must block on the in-flight write, not return immediately.
        stop_task = asyncio.create_task(exporter.async_stop())
        await asyncio.sleep(0.05)
        assert not stop_task.done()  # still draining the executor write
        assert not completed.is_set()

        # Let the write finish; only then may async_stop complete.
        release.set()
        await asyncio.wait_for(stop_task, timeout=5)

    assert completed.is_set()  # the write ran to completion before unload returned
    assert exporter._write_future is None
