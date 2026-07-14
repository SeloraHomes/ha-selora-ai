"""InsightsExporter — atomic file handoff to the Selora OS host.

The integration NEVER authenticates to or POSTs to Selora Connect. It publishes
a checksummed, atomically-written artifact under ``<config>/selora_ai/insights/``;
the Selora OS host copies it VM->host and owns the upload.

Contract (see the ``project-insights-export`` memory):
  * Immutable numbered artifacts + an atomic manifest pointer. The manifest is
    written LAST via ``os.replace`` — that rename is the commit point. Because
    each ``insights-<seq>.json.gz`` is immutable, the manifest->artifact pair
    the host reads is always internally consistent; there is no torn read.
  * Sequence is restart-proof: ``max(persisted+1, epoch_seconds)`` (HealthStore).
  * Host opt-in via a ``.export_enabled`` marker file — absent means write
    nothing, so self-hosted installs stay clean.
  * Orphaned ``.tmp-*`` files (from a mid-publish crash) are swept at startup.
  * Full snapshot every publish — a host that misses generations loses nothing.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .const import (
    DEFAULT_INSIGHTS_EXPORT_CADENCE,
    DEFAULT_INSIGHTS_EXPORT_RETENTION,
    INSIGHTS_EXPORT_ARTIFACT_DIR,
    INSIGHTS_EXPORT_MANIFEST_NAME,
    INSIGHTS_EXPORT_MARKER_NAME,
    INSIGHTS_EXPORT_PATH_PARTS,
    INSIGHTS_EXPORT_SCHEMA_VERSION,
    INSIGHTS_EXPORT_TMP_PREFIX,
)
from .insights_roster import build_home_roster

if TYPE_CHECKING:
    from .health_store import HealthStore
    from .insights import InsightsEngine
    from .types import (
        InsightsExportManifest,
    )

_LOGGER = logging.getLogger(__name__)

# Fixed-width zero-padded sequence so filenames sort chronologically as strings.
_SEQ_WIDTH = 15

# Delay before the first publish, so registries are populated and the initial
# job fires well after HA bootstrap (never blocking startup).
_INITIAL_DELAY_SECONDS = 90


class InsightsExporter:
    """Publishes health signals + insights as an atomic, host-copyable file."""

    def __init__(
        self,
        hass: HomeAssistant,
        health_store: HealthStore,
        insights_engine: InsightsEngine,
        *,
        installation_id: str | None = None,
    ) -> None:
        self._hass = hass
        self._health_store = health_store
        self._insights = insights_engine
        self._installation_id = installation_id
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._unsub_initial: CALLBACK_TYPE | None = None
        # Every in-flight publish task — the delayed initial publish AND each
        # periodic tick. Tracked so async_stop can cancel/drain them on unload:
        # a publish that outlives its entry races the replacement exporter on
        # separate locks + stale sequence state, and could overwrite the
        # manifest with an older generation or collide on a tmp artifact name.
        self._tasks: set[asyncio.Task[None]] = set()
        # The in-flight atomic manifest write, running on an executor thread.
        # Retained so async_stop can await the WRITE ITSELF (a worker thread is
        # not cancellable once started) instead of just cancelling the awaiting
        # task — otherwise the write would finish after unload and could clobber
        # the replacement exporter's newer manifest with an older sequence.
        self._write_future: asyncio.Future[Any] | None = None
        self._cadence = DEFAULT_INSIGHTS_EXPORT_CADENCE
        self._retention = DEFAULT_INSIGHTS_EXPORT_RETENTION
        # Serialize publishes so the delayed initial publish and a timer tick
        # can't interleave and leave the manifest pointing at an older artifact.
        self._publish_lock = asyncio.Lock()

    # ── Paths ────────────────────────────────────────────────────────────

    @property
    def base_dir(self) -> Path:
        return Path(self._hass.config.config_dir, *INSIGHTS_EXPORT_PATH_PARTS)

    @property
    def artifact_dir(self) -> Path:
        return self.base_dir / INSIGHTS_EXPORT_ARTIFACT_DIR

    @property
    def manifest_path(self) -> Path:
        return self.base_dir / INSIGHTS_EXPORT_MANIFEST_NAME

    @property
    def marker_path(self) -> Path:
        return self.base_dir / INSIGHTS_EXPORT_MARKER_NAME

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def async_start(
        self,
        cadence: int = DEFAULT_INSIGHTS_EXPORT_CADENCE,
        retention: int = DEFAULT_INSIGHTS_EXPORT_RETENTION,
    ) -> None:
        self._cadence = cadence
        self._retention = max(1, retention)
        await self._hass.async_add_executor_job(self._sweep_orphan_tmp)
        self._unsub_timer = async_track_time_interval(
            self._hass, self._periodic_publish, timedelta(seconds=cadence)
        )
        # Arm the first publish with an untracked timer, NOT
        # ``async_create_task``: a task created during setup is awaited by HA
        # bootstrap / config-entry reload, so one that sleeps 90s here would
        # stall startup until the bootstrap watchdog fires. ``async_call_later``
        # just arms a timer and returns (same pattern as the audit runner).
        self._unsub_initial = async_call_later(
            self._hass, _INITIAL_DELAY_SECONDS, self._initial_publish
        )

    @callback
    def _initial_publish(self, _now: datetime) -> None:
        self._unsub_initial = None
        self._spawn_publish("selora_ai_initial_export")

    @callback
    def _periodic_publish(self, _now: datetime) -> None:
        # async_track_time_interval would otherwise run the coroutine as an
        # UNTRACKED task; spawn it tracked so async_stop can drain an in-flight
        # periodic publish on unload.
        self._spawn_publish("selora_ai_periodic_export")

    @callback
    def _spawn_publish(self, name: str) -> None:
        # Fires well after bootstrap, so a background task here never blocks
        # startup; it just must not be garbage-collected mid-flight, and must
        # be cancellable on unload.
        task = self._hass.async_create_background_task(self._scheduled_publish(None), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def async_stop(self) -> None:
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        if self._unsub_initial:
            self._unsub_initial()
            self._unsub_initial = None
        # Cancel and drain EVERY in-flight publish (initial + periodic) so an
        # old publish can't finish after unload and race the replacement
        # exporter — overwriting the manifest with an older generation or
        # colliding on a tmp artifact name.
        pending = [t for t in self._tasks if not t.done()]
        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        # Cancelling the task above does NOT stop a _publish_blocking already
        # running on an executor thread — it keeps writing files. Await the
        # retained write future so unload only returns once the atomic manifest
        # write has finished; the replacement exporter (set up after unload
        # completes) then can't publish a newer sequence that this stale worker
        # would overwrite.
        write_future = self._write_future
        if write_future is not None and not write_future.done():
            with suppress(Exception):
                await write_future
        self._write_future = None

    async def _scheduled_publish(self, _now: datetime | None) -> None:
        try:
            await self.async_publish()
        except Exception:  # noqa: BLE001 — a scheduled callback must not kill its timer
            _LOGGER.exception("Insights export failed")

    # ── Publish ──────────────────────────────────────────────────────────

    async def async_publish(self) -> InsightsExportManifest | None:
        """Publish under a lock so concurrent publishes can't interleave and
        leave the manifest pointing at an older artifact."""
        async with self._publish_lock:
            return await self._do_publish()

    async def _do_publish(self) -> InsightsExportManifest | None:
        """Build the envelope and atomically publish it — if the host opted in.

        Returns the written manifest, or ``None`` when the marker is absent.
        """
        if not await self._hass.async_add_executor_job(self.marker_path.exists):
            return None

        generated_at = datetime.now(UTC)
        epoch_seconds = int(generated_at.timestamp())
        seq = await self._health_store.next_export_sequence(epoch_seconds)

        signals = await self._health_store.get_active_signals()
        collection: dict[str, Any] = {"status": "ok", "partial_reason": None}
        try:
            insights = await self._insights.async_get_insights()
        except Exception:  # noqa: BLE001 — publish partial rather than nothing
            _LOGGER.exception("Insight build failed; exporting signals-only")
            insights = []
            collection = {"status": "partial", "partial_reason": "insight_build_failed"}

        inventory = self._gather_inventory()
        # Resolve custom-component domains + human integration names here
        # (async) and thread them into the synchronous roster builder: it flags
        # each integration's ``custom`` and labels it with the manifest name.
        from homeassistant.loader import (  # noqa: PLC0415 — local, one call site
            async_get_custom_components,
            async_get_integrations,
        )

        try:
            custom_domains = set(await async_get_custom_components(self._hass))
        except Exception:  # noqa: BLE001 — custom flag is best-effort, never fail the export
            _LOGGER.debug("Could not resolve custom components; custom flags default to False")
            custom_domains = set()

        domains = {entry.domain for entry in self._hass.config_entries.async_entries()}
        integration_names: dict[str, str] = {}
        integration_urls: dict[str, str] = {}
        try:
            for domain, integration in (await async_get_integrations(self._hass, domains)).items():
                if not isinstance(integration, Exception):
                    integration_names[domain] = integration.name
                    # Manifest documentation URL (e.g. the integration's docs
                    # page) so a roster consumer can link each app; "" when the
                    # manifest omits it.
                    if integration.documentation:
                        integration_urls[domain] = integration.documentation
        except Exception:  # noqa: BLE001 — names/urls are best-effort; fall back to title/domain
            _LOGGER.debug("Could not resolve integration names; falling back to title/domain")

        roster = build_home_roster(self._hass, custom_domains, integration_names, integration_urls)
        if roster["truncated"]:
            collection = {**collection, "roster_truncated": True}
        generated_iso = generated_at.isoformat()
        next_expected = (generated_at + timedelta(seconds=self._cadence)).isoformat()

        envelope = {
            "schema_version": INSIGHTS_EXPORT_SCHEMA_VERSION,
            "sequence": seq,
            "generated_at": generated_iso,
            "signals": signals,
            "insights": insights,
            "inventory": inventory,
            "roster": roster,
            "collection": collection,
        }
        summary = {
            "signals_active": len(signals),
            "signals_critical": sum(1 for s in signals if s.get("severity") == "critical"),
            "insights_total": len(insights),
            "integrations": len(roster["integrations"]),
            "devices": len(roster["devices"]),
            "entities": len(roster["entities"]),
            "entities_unavailable": roster["unavailable_total"],
            "entities_disabled": roster["disabled_total"],
        }
        producer = {"integration_version": _integration_version(), "ha_version": HA_VERSION}
        if self._installation_id:
            producer["installation_id"] = self._installation_id

        # The atomic write runs on an executor thread that can't be cancelled
        # once started. Retain the future and SHIELD it: if the entry reloads
        # mid-write, the awaiting task is cancelled but the write keeps running,
        # and async_stop drains this future before unload returns — so the stale
        # worker can never land its lower sequence after the replacement
        # exporter has published a newer one.
        write_future = self._hass.async_add_executor_job(
            _publish_blocking,
            self.base_dir,
            self.artifact_dir,
            self.manifest_path,
            seq,
            envelope,
            summary,
            producer,
            collection,
            generated_iso,
            next_expected,
            self._cadence,
            self._retention,
        )
        self._write_future = write_future
        try:
            manifest = await asyncio.shield(write_future)
        finally:
            # Clear only when the write is actually finished (success or error);
            # on cancellation the write is still running, so leave the reference
            # for async_stop to drain.
            if write_future.done():
                self._write_future = None
        _LOGGER.debug(
            "Published insights export seq=%d (%d signals, %d insights)",
            seq,
            len(signals),
            len(insights),
        )
        return manifest

    def _sweep_orphan_tmp(self) -> int:
        """Remove ``.tmp-*`` files left by a crash mid-publish (blocking)."""
        removed = 0
        for directory in (self.base_dir, self.artifact_dir):
            if not directory.is_dir():
                continue
            for entry in directory.iterdir():
                if entry.name.startswith(INSIGHTS_EXPORT_TMP_PREFIX):
                    with suppress(OSError):
                        entry.unlink()
                        removed += 1
        if removed:
            _LOGGER.info("Swept %d orphaned export tmp files", removed)
        return removed

    def _gather_inventory(self) -> dict[str, int]:
        ent_reg = er.async_get(self._hass)
        dev_reg = dr.async_get(self._hass)
        area_reg = ar.async_get(self._hass)
        return {
            "entities": len(ent_reg.entities),
            "devices": len(dev_reg.devices),
            "areas": len(area_reg.areas),
            "integrations": len({e.domain for e in self._hass.config_entries.async_entries()}),
        }


# ── Blocking filesystem helpers (run on the executor) ─────────────────


def _publish_blocking(
    base_dir: Path,
    artifact_dir: Path,
    manifest_path: Path,
    seq: int,
    envelope: dict[str, Any],
    summary: dict[str, int],
    producer: dict[str, str],
    collection: dict[str, Any],
    generated_iso: str,
    next_expected: str,
    cadence: int,
    retention: int,
) -> InsightsExportManifest:
    """Atomic publish: immutable artifact first, manifest pointer last."""
    # Owner-only dirs (0o700): the artifacts hold household identities + state.
    # Created explicitly (not via parents=True, which leaves parents at the
    # default umask). exist_ok keeps a user's own perms on an existing dir.
    base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    artifact_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    raw = json.dumps(envelope, separators=(",", ":"), default=str).encode("utf-8")
    payload = gzip.compress(raw)

    artifact_name = f"insights-{seq:0{_SEQ_WIDTH}d}.json.gz"
    artifact_path = artifact_dir / artifact_name
    tmp_artifact = artifact_dir / f"{INSIGHTS_EXPORT_TMP_PREFIX}{seq}-{os.getpid()}.gz"
    try:
        _atomic_bytes(tmp_artifact, artifact_path, payload)
    finally:
        with suppress(OSError):
            if tmp_artifact.exists():
                tmp_artifact.unlink()
    _fsync_dir(artifact_dir)

    sha = hashlib.sha256(payload).hexdigest()
    manifest: InsightsExportManifest = {
        "schema_version": INSIGHTS_EXPORT_SCHEMA_VERSION,
        "sequence": seq,
        "status": "complete" if collection.get("status", "ok") == "ok" else "partial",
        "generated_at": generated_iso,
        "cadence_seconds": cadence,
        "next_expected_at": next_expected,
        "artifact": {
            "path": f"{INSIGHTS_EXPORT_ARTIFACT_DIR}/{artifact_name}",
            "sha256": sha,
            "size_bytes": len(payload),
            "encoding": "gzip",
        },
        "summary": summary,
        "producer": producer,
        "collection": collection,
    }

    manifest_bytes = json.dumps(manifest, indent=2, default=str).encode("utf-8")
    tmp_manifest = base_dir / f"{INSIGHTS_EXPORT_TMP_PREFIX}manifest-{os.getpid()}.json"
    try:
        _atomic_bytes(tmp_manifest, manifest_path, manifest_bytes)  # commit point
    finally:
        with suppress(OSError):
            if tmp_manifest.exists():
                tmp_manifest.unlink()
    _fsync_dir(base_dir)

    _prune_artifacts(artifact_dir, retention)
    return manifest


def _atomic_bytes(tmp: Path, dest: Path, data: bytes) -> None:
    """Write ``data`` to ``tmp`` (fsynced) then atomically rename onto ``dest``.

    The artifact carries the household's identities + state, so it's written
    owner-only (0o600) rather than inheriting the process umask (typically a
    world-readable 0o644), keeping it private on a shared host. The mode is
    carried onto ``dest`` by the rename.
    """
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        os.fchmod(fh.fileno(), 0o600)  # explicit: covers a pre-existing tmp
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, dest)


def _fsync_dir(path: Path) -> None:
    """fsync a directory so a rename survives a VM crash/snapshot."""
    with suppress(OSError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _prune_artifacts(artifact_dir: Path, retention: int) -> int:
    """Keep the newest ``retention`` artifacts; delete older ones."""
    artifacts = sorted(
        (p for p in artifact_dir.glob("insights-*.json.gz")),
        key=lambda p: p.name,
    )
    stale = artifacts[:-retention] if retention > 0 else artifacts
    removed = 0
    for path in stale:
        with suppress(OSError):
            path.unlink()
            removed += 1
    return removed


def _integration_version() -> str:
    with suppress(OSError, ValueError, KeyError):
        manifest = Path(__file__).parent / "manifest.json"
        with manifest.open(encoding="utf-8") as fh:
            return str(json.load(fh).get("version", "unknown"))
    return "unknown"
