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
