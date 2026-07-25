"""Tests for HealthStore — signal upsert/resolve, cap, export sequence."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from custom_components.selora_ai.health_store import HealthStore, health_signal_id

from .conftest import MockStore


@pytest.fixture
def health_store(hass):
    with patch("custom_components.selora_ai.health_store.Store") as mock_cls:
        store_inst = MockStore()
        mock_cls.return_value = store_inst
        hs = HealthStore(hass)
        hs._store = store_inst
        yield hs, store_inst


@pytest.mark.asyncio
async def test_record_signal_creates_then_upserts(health_store):
    hs, _ = health_store
    sid = await hs.record_signal(
        kind="flapping",
        target="light.kitchen",
        target_kind="entity",
        severity="warning",
        evidence={"transitions": 6},
    )
    assert sid == health_signal_id("flapping", "light.kitchen")

    # Re-detection upserts the same record and bumps count.
    await hs.record_signal(
        kind="flapping",
        target="light.kitchen",
        target_kind="entity",
        severity="warning",
        evidence={"transitions": 9},
    )
    active = await hs.get_active_signals()
    assert len(active) == 1
    assert active[0]["count"] == 2
    assert active[0]["evidence"]["transitions"] == 9


@pytest.mark.asyncio
async def test_get_signals_returns_decoupled_snapshot(health_store):
    """A caller (exporter/sensor) holds get_signals results across awaits while
    a concurrent scan resolves/updates the store. The returned snapshot must not
    change under it, or the export could show a since-resolved signal as active
    or mix data from two scans (nested evidence copied too)."""
    hs, _ = health_store
    await hs.record_signal(
        kind="unavailable",
        target="light.x",
        target_kind="entity",
        severity="warning",
        evidence={"unavailable_seconds": 100},
    )

    snapshot = await hs.get_active_signals()
    assert len(snapshot) == 1

    # A concurrent scan resolves the signal and re-records it with new evidence.
    await hs.resolve_signal("unavailable", "light.x")
    await hs.record_signal(
        kind="unavailable",
        target="light.x",
        target_kind="entity",
        severity="critical",
        evidence={"unavailable_seconds": 999},
    )

    # The earlier snapshot is frozen: still active-list content, old severity,
    # old nested evidence — untouched by the mutations above.
    assert snapshot[0]["status"] == "active"
    assert snapshot[0]["severity"] == "warning"
    assert snapshot[0]["evidence"]["unavailable_seconds"] == 100


@pytest.mark.asyncio
async def test_record_signal_stores_device_id(health_store):
    hs, _ = health_store
    await hs.record_signal(
        kind="unavailable",
        target="light.kitchen",
        target_kind="entity",
        severity="warning",
        evidence={},
        device_id="dev-123",
    )
    active = await hs.get_active_signals()
    assert active[0]["device_id"] == "dev-123"

    # Integration/device targets carry no device_id.
    await hs.record_signal(
        kind="integration_error",
        target="hue",
        target_kind="integration",
        severity="critical",
        evidence={},
    )
    by_target = {s["target"]: s for s in await hs.get_active_signals()}
    assert by_target["hue"]["device_id"] is None


@pytest.mark.asyncio
async def test_resolve_and_reactivate(health_store):
    hs, _ = health_store
    await hs.record_signal(
        kind="unavailable",
        target="sensor.door",
        target_kind="entity",
        severity="warning",
        evidence={},
    )
    original_first_seen = (await hs.get_active_signals())[0]["first_seen"]
    assert await hs.resolve_signal("unavailable", "sensor.door") is True
    assert await hs.get_active_signals() == []

    # Re-detection of a resolved condition reactivates it as a NEW episode:
    # count accumulates across episodes, but first_seen resets to the new
    # episode start so the duration isn't anchored to the prior outage.
    await hs.record_signal(
        kind="unavailable",
        target="sensor.door",
        target_kind="entity",
        severity="warning",
        evidence={},
    )
    active = await hs.get_active_signals()
    assert len(active) == 1
    assert active[0]["count"] == 2
    assert active[0]["first_seen"] != original_first_seen


@pytest.mark.asyncio
async def test_reactivation_clears_stale_insight_override(health_store):
    """A dismiss (or other override) on one episode must not survive into a
    later, distinct recurrence — the insight_id is stable across episodes, so
    reactivation has to drop the override or every future outage stays hidden."""
    hs, _ = health_store
    override_key = "signal:unavailable:sensor.door"

    async def record() -> None:
        await hs.record_signal(
            kind="unavailable",
            target="sensor.door",
            target_kind="entity",
            severity="warning",
            evidence={},
        )

    await record()
    # User dismisses this outage's insight.
    await hs.set_insight_override(override_key, "dismissed")
    assert await hs.get_insight_overrides() == {override_key: "dismissed"}

    # A refresh WITHIN the same episode (still active) must keep the override —
    # acknowledging an ongoing outage should stick.
    await record()
    assert await hs.get_insight_overrides() == {override_key: "dismissed"}

    # The outage clears, then recurs later: the new episode is a distinct
    # problem, so the stale override is dropped and the insight resurfaces.
    assert await hs.resolve_signal("unavailable", "sensor.door") is True
    await record()
    assert await hs.get_insight_overrides() == {}


@pytest.mark.asyncio
async def test_cap_drops_resolved_first(health_store, monkeypatch):
    hs, _ = health_store
    monkeypatch.setattr("custom_components.selora_ai.health_store.HEALTH_MAX_SIGNALS", 2)
    for i in range(2):
        await hs.record_signal(
            kind="battery_low",
            target=f"sensor.batt_{i}",
            target_kind="entity",
            severity="warning",
            evidence={},
        )
    await hs.resolve_signal("battery_low", "sensor.batt_0")
    # Third distinct signal breaches the cap of 2; the resolved one is dropped.
    await hs.record_signal(
        kind="battery_low",
        target="sensor.batt_2",
        target_kind="entity",
        severity="warning",
        evidence={},
    )
    all_signals = await hs.get_signals()
    targets = {s["target"] for s in all_signals}
    assert len(all_signals) == 2
    assert "sensor.batt_0" not in targets  # resolved-oldest dropped first


@pytest.mark.asyncio
async def test_export_sequence_monotonic_and_restart_proof(health_store):
    hs, store_inst = health_store

    assert await hs.next_export_sequence(1000) == 1000
    # Same epoch again -> persisted+1 floor keeps it strictly increasing.
    assert await hs.next_export_sequence(1000) == 1001
    # Clock steps backward -> still monotonic.
    assert await hs.next_export_sequence(500) == 1002

    # Simulate a restart: a fresh store instance loads the persisted data.
    persisted = store_inst.saved_data[-1]
    with patch("custom_components.selora_ai.health_store.Store") as mock_cls:
        restarted = MockStore(initial_data=persisted)
        mock_cls.return_value = restarted
        hs2 = HealthStore(hs._hass)
        hs2._store = restarted
        # Epoch far below the persisted counter must NOT reset to a low value.
        assert await hs2.next_export_sequence(700) == 1003


@pytest.mark.asyncio
async def test_insight_overrides_roundtrip(health_store):
    hs, _ = health_store
    assert await hs.get_insight_overrides() == {}
    await hs.set_insight_override("signal:flapping:light.x", "dismissed")
    assert await hs.get_insight_overrides() == {"signal:flapping:light.x": "dismissed"}


@pytest.mark.asyncio
async def test_prune_resolved_drops_orphaned_overrides(health_store):
    """An ack/dismiss override for a signal that no longer exists is pruned, so
    the override map can't grow without bound; a non-signal override is kept."""
    hs, _ = health_store
    # A live signal + its override, an orphaned signal-override, and a
    # non-signal override that must survive.
    await hs.record_signal(
        kind="flapping",
        target="light.live",
        target_kind="entity",
        severity="warning",
        evidence={},
    )
    await hs.set_insight_override(
        f"signal:{health_signal_id('flapping', 'light.live')}", "dismissed"
    )
    await hs.set_insight_override("signal:unavailable:light.gone", "dismissed")  # no such signal
    await hs.set_insight_override("suggestion:abc", "dismissed")  # not a signal override

    await hs.prune_resolved()

    overrides = await hs.get_insight_overrides()
    assert f"signal:{health_signal_id('flapping', 'light.live')}" in overrides  # live → kept
    assert "signal:unavailable:light.gone" not in overrides  # orphaned → pruned
    assert overrides["suggestion:abc"] == "dismissed"  # non-signal → untouched


@pytest.mark.asyncio
async def test_set_last_audit_skips_write_when_only_timestamp_moved(health_store):
    """The audit re-runs every health scan (~96x/day); persisting an unchanged
    record each time is pure flash wear on SD-card installs."""
    hs, store_inst = health_store

    first = {"status": "ok", "score": 91, "band": "A", "generated_at": "2026-07-24T10:00:00+00:00"}
    await hs.set_last_audit(first)
    assert len(store_inst.saved_data) == 1

    # Same home, next scan — identical apart from the timestamp: no disk write.
    await hs.set_last_audit({**first, "generated_at": "2026-07-24T10:15:00+00:00"})
    assert len(store_inst.saved_data) == 1
    # ...but the in-memory record is still refreshed for readers.
    assert (await hs.get_last_audit())["generated_at"] == "2026-07-24T10:15:00+00:00"


@pytest.mark.asyncio
async def test_set_last_audit_persists_when_content_changes(health_store):
    hs, store_inst = health_store

    base = {"status": "ok", "score": 91, "band": "A", "generated_at": "2026-07-24T10:00:00+00:00"}
    await hs.set_last_audit(base)
    assert len(store_inst.saved_data) == 1

    # Score moved → must persist.
    await hs.set_last_audit({**base, "score": 84, "generated_at": "2026-07-24T10:15:00+00:00"})
    assert len(store_inst.saved_data) == 2

    # Nested findings changed while the score happens to match → must persist.
    await hs.set_last_audit(
        {
            **base,
            "score": 84,
            "checks": [{"check_id": "offline_devices", "severity": "warning"}],
            "generated_at": "2026-07-24T10:30:00+00:00",
        }
    )
    assert len(store_inst.saved_data) == 3


@pytest.mark.asyncio
async def test_set_last_audit_checkpoints_a_stale_timestamp(health_store):
    """Unchanged content must still be checkpointed once the stored timestamp ages.

    sensor.py reads `generated_at` from the persisted audit and exposes it as the
    Home Health sensor's `last_scan`; skipping every unchanged write outright made
    that read back an arbitrarily old time after a restart.
    """
    hs, store_inst = health_store
    base = {"status": "ok", "score": 91, "band": "A", "generated_at": "2026-07-24T10:00:00+00:00"}
    await hs.set_last_audit(base)
    assert len(store_inst.saved_data) == 1

    # Well inside the checkpoint window — still skipped.
    await hs.set_last_audit({**base, "generated_at": "2026-07-24T10:45:00+00:00"})
    assert len(store_inst.saved_data) == 1

    # Past the window — persisted even though nothing about the home changed.
    await hs.set_last_audit({**base, "generated_at": "2026-07-24T11:15:00+00:00"})
    assert len(store_inst.saved_data) == 2
    assert store_inst.saved_data[-1]["meta"]["last_audit"]["generated_at"] == (
        "2026-07-24T11:15:00+00:00"
    )

    # The window restarts from the checkpoint, not from the original write.
    await hs.set_last_audit({**base, "generated_at": "2026-07-24T11:50:00+00:00"})
    assert len(store_inst.saved_data) == 2


@pytest.mark.asyncio
async def test_set_last_audit_persists_when_timestamp_is_unparseable(health_store):
    """An unusable timestamp must not freeze the persisted copy forever."""
    hs, store_inst = health_store
    base = {"status": "ok", "score": 91, "band": "A", "generated_at": "not-a-timestamp"}
    await hs.set_last_audit(base)
    assert len(store_inst.saved_data) == 1
    await hs.set_last_audit({**base, "generated_at": "also-not-a-timestamp"})
    assert len(store_inst.saved_data) == 2


@pytest.mark.asyncio
async def test_checkpoint_baseline_survives_a_restart(hass):
    """After a restart the baseline must come from DISK, not the in-memory record.

    The in-memory audit is refreshed on every call even when the save is skipped,
    so deriving the baseline from it slid the window forward each scan and the
    checkpoint could never fire — leaving the persisted `generated_at` (surfaced
    as the sensor's `last_scan`) stale indefinitely.
    """
    base = {"status": "ok", "score": 91, "band": "A"}
    # A store that comes up with a record already on disk, timestamped 11:50.
    persisted = {
        "signals": {},
        "meta": {"last_audit": {**base, "generated_at": "2026-07-24T11:50:00+00:00"}},
    }
    with patch("custom_components.selora_ai.health_store.Store") as mock_cls:
        store_inst = MockStore(persisted)
        mock_cls.return_value = store_inst
        hs = HealthStore(hass)
        hs._store = store_inst

        # Three unchanged 15-minute scans. Each one is inside the window measured
        # from the DISK timestamp (11:50) until the third.
        await hs.set_last_audit({**base, "generated_at": "2026-07-24T12:05:00+00:00"})
        assert len(store_inst.saved_data) == 0
        await hs.set_last_audit({**base, "generated_at": "2026-07-24T12:20:00+00:00"})
        assert len(store_inst.saved_data) == 0
        await hs.set_last_audit({**base, "generated_at": "2026-07-24T12:35:00+00:00"})
        # 11:50 -> 12:35 is 45min, still inside the hour.
        assert len(store_inst.saved_data) == 0
        # 11:50 -> 12:55 crosses it: the disk copy must be refreshed.
        await hs.set_last_audit({**base, "generated_at": "2026-07-24T12:55:00+00:00"})
        assert len(store_inst.saved_data) == 1
        assert store_inst.saved_data[-1]["meta"]["last_audit"]["generated_at"] == (
            "2026-07-24T12:55:00+00:00"
        )


@pytest.mark.asyncio
async def test_any_write_refreshes_the_checkpoint_baseline(health_store):
    """A save driven by another caller flushes `last_audit` too, so it advances the
    baseline — no second write for a record already on disk.

    The baseline is the timestamp OF THE PERSISTED RECORD, not the wall-clock time
    of the flush: set_last_scan writes whatever `last_audit` currently holds.
    """
    hs, store_inst = health_store
    base = {"status": "ok", "score": 91, "band": "A"}

    await hs.set_last_audit({**base, "generated_at": "2026-07-24T10:00:00+00:00"})
    assert len(store_inst.saved_data) == 1  # first record always persists

    # Unchanged, 20min later -> in-memory only. Disk still holds 10:00.
    await hs.set_last_audit({**base, "generated_at": "2026-07-24T10:20:00+00:00"})
    assert len(store_inst.saved_data) == 1

    # set_last_scan writes the whole document, flushing the 10:20 audit with it.
    await hs.set_last_scan("2026-07-24T10:21:00+00:00")
    assert len(store_inst.saved_data) == 2

    # 11:10 is 70min past the ORIGINAL write but only 50min past what the flush
    # actually persisted (10:20), so no checkpoint is owed. Without tracking the
    # baseline in _save this would have written again for data already on disk.
    await hs.set_last_audit({**base, "generated_at": "2026-07-24T11:10:00+00:00"})
    assert len(store_inst.saved_data) == 2

    # 11:25 is past an hour from the persisted 10:20 -> checkpoint.
    await hs.set_last_audit({**base, "generated_at": "2026-07-24T11:25:00+00:00"})
    assert len(store_inst.saved_data) == 3


@pytest.mark.asyncio
async def test_checkpoint_corrects_a_future_persisted_timestamp(hass):
    """A persisted stamp ahead of the current audit must be rewritten, not waited out.

    A fast-clock boot (RPi with no RTC) or a restored backup can leave
    `generated_at` in the future. Subtracting gives a negative delta, so the
    window never elapses and the sensor's `last_scan` stays in the future across
    restarts until wall time catches up.
    """
    base = {"status": "ok", "score": 91, "band": "A"}
    persisted = {
        "signals": {},
        # Written under a clock running months fast.
        "meta": {"last_audit": {**base, "generated_at": "2027-01-01T00:00:00+00:00"}},
    }
    with patch("custom_components.selora_ai.health_store.Store") as mock_cls:
        store_inst = MockStore(persisted)
        mock_cls.return_value = store_inst
        hs = HealthStore(hass)
        hs._store = store_inst

        # NTP has since corrected the clock backwards.
        await hs.set_last_audit({**base, "generated_at": "2026-07-24T12:00:00+00:00"})

        assert len(store_inst.saved_data) == 1, "a future stamp must be corrected now"
        assert store_inst.saved_data[-1]["meta"]["last_audit"]["generated_at"] == (
            "2026-07-24T12:00:00+00:00"
        )

        # Having corrected it, normal throttling resumes from the new baseline.
        await hs.set_last_audit({**base, "generated_at": "2026-07-24T12:15:00+00:00"})
        assert len(store_inst.saved_data) == 1


def test_checkpoint_due_treats_a_backwards_clock_as_due():
    """Unit-level: the predicate itself must not return False on a negative delta."""
    from datetime import UTC, datetime

    from custom_components.selora_ai.health_store import _checkpoint_due

    future = datetime(2027, 1, 1, tzinfo=UTC)
    now = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
    assert _checkpoint_due(future, now) is True
    # Same instant is not due; a full window is.
    assert _checkpoint_due(now, now) is False
    assert _checkpoint_due(datetime(2026, 7, 24, 10, 0, tzinfo=UTC), now) is True
