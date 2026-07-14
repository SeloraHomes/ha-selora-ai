"""Tests for InsightsEngine (Layer 2) and HealthMonitor flapping detection."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
import pytest

from custom_components.selora_ai.health_monitor import HealthMonitor
from custom_components.selora_ai.health_store import HealthStore
from custom_components.selora_ai.insights import InsightsEngine

from .conftest import MockStore


@pytest.fixture
def health_store(hass):
    with patch("custom_components.selora_ai.health_store.Store") as mock_cls:
        store_inst = MockStore()
        mock_cls.return_value = store_inst
        hs = HealthStore(hass)
        hs._store = store_inst
        yield hs


@pytest.mark.asyncio
async def test_signal_becomes_insight_with_rule(health_store, hass):
    await health_store.record_signal(
        kind="battery_low",
        target="sensor.front_door_battery",
        target_kind="entity",
        severity="warning",
        evidence={"battery_level": 8},
    )
    engine = InsightsEngine(hass, health_store)
    insights = await engine.async_get_insights()

    assert len(insights) == 1
    ins = insights[0]
    assert ins["kind"] == "fix"
    assert ins["severity"] == "warning"
    assert "8%" in ins["detail"]
    assert ins["insight_id"] == "signal:battery_low:sensor.front_door_battery"


@pytest.mark.asyncio
async def test_binary_battery_insight_renders_low_not_percent(health_store, hass):
    """A binary battery sensor (on == low) has no percentage — the insight says
    'is low', not a bogus '0%'."""
    await health_store.record_signal(
        kind="battery_low",
        target="binary_sensor.door_battery",
        target_kind="entity",
        severity="warning",
        evidence={"battery_low": True},  # no numeric level
    )
    (ins,) = await InsightsEngine(hass, health_store).async_get_insights()
    assert "is low" in ins["detail"]
    assert "%" not in ins["detail"]


@pytest.mark.asyncio
async def test_dismissed_insight_is_filtered(health_store, hass):
    await health_store.record_signal(
        kind="unavailable",
        target="light.hallway",
        target_kind="entity",
        severity="warning",
        evidence={"unavailable_seconds": 600},
    )
    engine = InsightsEngine(hass, health_store)
    (ins,) = await engine.async_get_insights()
    await engine.set_insight_status(ins["insight_id"], "dismissed")

    assert await engine.async_get_insights() == []


@pytest.mark.asyncio
async def test_insights_sorted_by_severity(health_store, hass):
    await health_store.record_signal(
        kind="battery_low",
        target="sensor.a",
        target_kind="entity",
        severity="warning",
        evidence={"battery_level": 5},
    )
    await health_store.record_signal(
        kind="integration_error",
        target="zwave",
        target_kind="integration",
        severity="critical",
        evidence={"reason": "setup_retry"},
    )
    engine = InsightsEngine(hass, health_store)
    insights = await engine.async_get_insights()
    assert [i["severity"] for i in insights] == ["critical", "warning"]


@pytest.mark.asyncio
async def test_pattern_suggestion_becomes_improvement(health_store, hass):
    class _FakePatternStore:
        async def get_suggestions(self, status=None):
            return [
                {
                    "suggestion_id": "sug1",
                    "description": "Turn off lights at midnight",
                    "evidence_summary": "Seen 5 nights",
                    "automation_data": {"alias": "x"},
                    "created_at": "2026-01-01T00:00:00+00:00",
                }
            ]

    engine = InsightsEngine(hass, health_store, _FakePatternStore())
    insights = await engine.async_get_insights()
    assert len(insights) == 1
    assert insights[0]["kind"] == "improvement"
    assert insights[0]["suggested_action"]["type"] == "automation"
    assert insights[0]["suggested_action"]["suggestion_id"] == "sug1"


@pytest.mark.asyncio
async def test_monitor_detects_flapping(health_store, hass):
    monitor = HealthMonitor(hass, health_store)
    base = datetime.now(UTC)

    # 8 alternating availability flips within the flap window (> threshold 6).
    for i in range(8):
        state_str = "unavailable" if i % 2 == 0 else "on"
        new_state = SimpleNamespace(state=state_str, last_updated=base + timedelta(seconds=i))
        monitor.handle_state_change("light.flappy", new_state, None)

    await monitor.async_scan()

    signals = await health_store.get_signals(kind="flapping")
    assert len(signals) == 1
    assert signals[0]["target"] == "light.flappy"
    assert signals[0]["evidence"]["transitions"] >= 6


@pytest.mark.asyncio
async def test_monitor_resolves_when_flapping_stops(health_store, hass):
    monitor = HealthMonitor(hass, health_store)
    base = datetime.now(UTC)
    for i in range(8):
        state_str = "unavailable" if i % 2 == 0 else "on"
        new_state = SimpleNamespace(state=state_str, last_updated=base + timedelta(seconds=i))
        monitor.handle_state_change("light.flappy", new_state, None)
    await monitor.async_scan()
    assert len(await health_store.get_signals(status="active", kind="flapping")) == 1

    # Clear the in-memory transitions and re-scan: the signal should resolve.
    monitor._tracks["light.flappy"].transitions.clear()
    await monitor.async_scan()
    assert await health_store.get_signals(status="active", kind="flapping") == []


@pytest.mark.asyncio
async def test_monitor_drains_inflight_periodic_scan_on_stop(health_store, hass):
    """A periodic scan in flight during a reload must be cancelled/drained on
    unload — otherwise it finishes holding a stale store and clobbers the new
    entry's store."""
    monitor = HealthMonitor(hass, health_store)
    started = asyncio.Event()
    release = asyncio.Event()
    state = {"completed": False}

    async def blocking_scan() -> None:
        started.set()
        await release.wait()
        state["completed"] = True

    monitor.async_scan = blocking_scan  # type: ignore[method-assign]

    # Fire a periodic tick the way async_track_time_interval would.
    monitor._periodic_scan(None)
    await asyncio.wait_for(started.wait(), timeout=1)
    assert any(not t.done() for t in monitor._tasks)

    await monitor.async_stop()

    assert monitor._tasks == set()
    assert state["completed"] is False  # cancelled before finishing


@pytest.mark.asyncio
async def test_monitor_drains_inflight_rescan_on_stop(health_store, hass):
    """An externally-requested websocket rescan (async_request_scan) in flight
    must also be tracked and drained on unload."""
    monitor = HealthMonitor(hass, health_store)
    started = asyncio.Event()
    release = asyncio.Event()
    state = {"completed": False}

    async def blocking_scan() -> None:
        started.set()
        await release.wait()
        state["completed"] = True

    monitor.async_scan = blocking_scan  # type: ignore[method-assign]

    req = asyncio.create_task(monitor.async_request_scan())
    await asyncio.wait_for(started.wait(), timeout=1)
    assert any(not t.done() for t in monitor._tasks)

    await monitor.async_stop()

    assert state["completed"] is False
    with suppress(asyncio.CancelledError):
        await req
    assert monitor._tasks == set()


@pytest.mark.asyncio
async def test_monitor_drops_tracker_when_entity_removed(health_store, hass):
    """An entity leaving the state machine (state_changed with new_state=None)
    must free its tracker, so a churning dynamic integration can't grow _tracks
    without bound."""
    monitor = HealthMonitor(hass, health_store)
    base = datetime.now(UTC)
    new_state = SimpleNamespace(state="on", last_updated=base)
    monitor.handle_state_change("light.ephemeral", new_state, None)
    assert "light.ephemeral" in monitor._tracks

    monitor.handle_entity_removed("light.ephemeral")
    assert "light.ephemeral" not in monitor._tracks

    # Idempotent: removing an unknown/already-gone entity is a no-op.
    monitor.handle_entity_removed("light.never_seen")


@pytest.mark.asyncio
async def test_unavailable_duration_survives_restart(health_store, hass):
    """After an HA restart, an offline device's last_changed resets to boot
    time; the reported duration must still reflect the persisted first_seen."""
    monitor = HealthMonitor(hass, health_store)
    # A signal first recorded 3 days ago (persisted across the restart).
    await health_store.record_signal(
        kind="unavailable",
        target="media_player.gone",
        target_kind="entity",
        severity="warning",
        evidence={"unavailable_seconds": 30},
    )
    data = await health_store._get_loaded_data()
    sid = next(iter(data["signals"]))
    data["signals"][sid]["first_seen"] = (datetime.now(UTC) - timedelta(days=3)).isoformat()

    # Post-restart: the entity is unavailable but last_changed is "just now".
    hass.states.async_set("media_player.gone", "unavailable")
    await monitor.async_scan()

    sig = (await health_store.get_signals(status="active", kind="unavailable"))[0]
    # ~3 days from first_seen, not the seconds since boot.
    assert sig["evidence"]["unavailable_seconds"] >= 3 * 24 * 3600 - 300


def test_usually_unavailable_ratio_gate():
    """The availability-ratio gate is config-agnostic: usually-absent devices
    are gated, a device that was reliably present then died is not, and an
    under-observed entity is never gated."""
    from custom_components.selora_ai.health_monitor import (
        _AVAIL_MIN_OBSERVED_SECS,
        _EntityTrack,
        _usually_unavailable,
    )

    now = 1_000_000.0
    obs = _AVAIL_MIN_OBSERVED_SECS * 4  # plenty of history

    # Available only 10% of the time → usually-absent → gated.
    beacon = _EntityTrack(
        available_secs=obs * 0.1, observed_secs=obs, last_avail_ts=now, last_available=False
    )
    assert _usually_unavailable(beacon, now) is True

    # Available 90% then just went offline → real fault → NOT gated.
    real = _EntityTrack(
        available_secs=obs * 0.9, observed_secs=obs, last_avail_ts=now, last_available=False
    )
    assert _usually_unavailable(real, now) is False

    # Barely observed → no trustworthy ratio → NOT gated.
    fresh = _EntityTrack(
        available_secs=0.0, observed_secs=600.0, last_avail_ts=now, last_available=False
    )
    assert _usually_unavailable(fresh, now) is False


@pytest.mark.asyncio
async def test_scan_gates_usually_unavailable_device(health_store, hass, monkeypatch):
    """End-to-end: a low-availability device is only gated when it's actively
    flapping (a transient/roaming device). A device that went unavailable and
    STAYED that way — whether it was reliably present before or just has a low
    ratio — is a dead device and must still be flagged (the Sonos case)."""
    from collections import deque

    from custom_components.selora_ai.health_monitor import (
        _AVAIL_MIN_OBSERVED_SECS,
        _INTERMITTENT_MIN_TRANSITIONS,
        _EntityTrack,
    )

    monkeypatch.setattr(
        "custom_components.selora_ai.health_monitor.HEALTH_UNAVAILABLE_GRACE_SECS", 0
    )
    monitor = HealthMonitor(hass, health_store)
    hass.states.async_set("sensor.roaming_tag", "unavailable")
    hass.states.async_set("sensor.dead_lowratio", "unavailable")
    hass.states.async_set("sensor.dead_real", "unavailable")

    now = datetime.now(UTC).timestamp()
    obs = _AVAIL_MIN_OBSERVED_SECS * 4
    # Roaming tag: low ratio AND actively flapping (recent transitions) -> gated.
    monitor._tracks["sensor.roaming_tag"] = _EntityTrack(
        available_secs=obs * 0.1,
        observed_secs=obs,
        last_avail_ts=now,
        last_available=False,
        transitions=deque(
            (now - i * 60 for i in range(_INTERMITTENT_MIN_TRANSITIONS + 2)), maxlen=64
        ),
    )
    # Low ratio but died-and-stayed (no recent flapping) -> flagged.
    monitor._tracks["sensor.dead_lowratio"] = _EntityTrack(
        available_secs=obs * 0.1, observed_secs=obs, last_avail_ts=now, last_available=False
    )
    # Reliably present then died (high ratio) -> flagged.
    monitor._tracks["sensor.dead_real"] = _EntityTrack(
        available_secs=obs * 0.95, observed_secs=obs, last_avail_ts=now, last_available=False
    )

    await monitor.async_scan()
    targets = {
        s["target"] for s in await health_store.get_signals(status="active", kind="unavailable")
    }
    assert "sensor.roaming_tag" not in targets  # low ratio AND flapping -> transient, gated
    assert "sensor.dead_lowratio" in targets  # low ratio but stayed dead -> flagged
    assert "sensor.dead_real" in targets  # reliably present then died -> flagged


@pytest.mark.asyncio
async def test_monitor_ignores_transient_ble_and_trackers(health_store, hass, monkeypatch):
    """BLE beacons (ibeacon) and device_trackers go unavailable when out of
    range — normal presence behaviour, not a device fault. They must not raise
    an offline signal, while a real device still does."""
    monkeypatch.setattr(
        "custom_components.selora_ai.health_monitor.HEALTH_UNAVAILABLE_GRACE_SECS", 0
    )
    ent_reg = er.async_get(hass)
    beacon = ent_reg.async_get_or_create(
        "sensor", "ibeacon", "b1", suggested_object_id="beacon_distance"
    )
    tracker = ent_reg.async_get_or_create(
        "device_tracker", "gps", "t1", suggested_object_id="a_phone"
    )
    real = ent_reg.async_get_or_create("sensor", "zha", "s1", suggested_object_id="real_sensor")
    for eid in (beacon.entity_id, tracker.entity_id, real.entity_id):
        hass.states.async_set(eid, "unavailable")

    await HealthMonitor(hass, health_store).async_scan()
    targets = {
        s["target"] for s in await health_store.get_signals(status="active", kind="unavailable")
    }
    assert real.entity_id in targets  # a real device is still flagged
    assert beacon.entity_id not in targets  # BLE beacon skipped
    assert tracker.entity_id not in targets  # device_tracker skipped


@pytest.mark.asyncio
async def test_scan_suppresses_partial_device_unavailability(health_store, hass, monkeypatch):
    """A device with a mix of available + unavailable entities is only partially
    down (a by-design idle sub-entity) — no signal. A device whose every entity
    is unavailable is genuinely down — flagged."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    monkeypatch.setattr(
        "custom_components.selora_ai.health_monitor.HEALTH_UNAVAILABLE_GRACE_SECS", 0
    )
    entry = MockConfigEntry(domain="wallbox", entry_id="wb_e", title="Wallbox")
    entry.add_to_hass(hass)
    from homeassistant.helpers import device_registry as dr

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    # Partial device: charging entity idle+unavailable, status entity available.
    charger = dev_reg.async_get_or_create(
        config_entry_id="wb_e", identifiers={("wallbox", "c1")}, name="EV Charger"
    )
    charging = ent_reg.async_get_or_create(
        "sensor", "wallbox", "chg", device_id=charger.id, config_entry=entry
    )
    status = ent_reg.async_get_or_create(
        "sensor", "wallbox", "sta", device_id=charger.id, config_entry=entry
    )
    hass.states.async_set(charging.entity_id, "unavailable")
    hass.states.async_set(status.entity_id, "not_connected")

    # Wholly-down device: both entities unavailable.
    dead = dev_reg.async_get_or_create(
        config_entry_id="wb_e", identifiers={("wallbox", "d1")}, name="Hub"
    )
    d1 = ent_reg.async_get_or_create(
        "sensor", "wallbox", "d1a", device_id=dead.id, config_entry=entry
    )
    d2 = ent_reg.async_get_or_create(
        "sensor", "wallbox", "d1b", device_id=dead.id, config_entry=entry
    )
    hass.states.async_set(d1.entity_id, "unavailable")
    hass.states.async_set(d2.entity_id, "unavailable")

    await HealthMonitor(hass, health_store).async_scan()
    targets = {
        s["target"] for s in await health_store.get_signals(status="active", kind="unavailable")
    }
    assert charging.entity_id not in targets  # partial device -> suppressed
    assert d1.entity_id in targets  # wholly-down device -> flagged
    assert d2.entity_id in targets


@pytest.mark.asyncio
async def test_scan_flags_offline_device_with_retained_config_entities(
    health_store, hass, monkeypatch
):
    """The real Sonos case: the primary media_player goes unavailable, but the
    config entities (bass/treble/crossfade/loudness) keep showing their cached
    values. Reachability is judged by the primary entity, so the speaker is
    still flagged despite the config entities reading as set."""
    from homeassistant.const import EntityCategory
    from homeassistant.helpers import device_registry as dr
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    monkeypatch.setattr(
        "custom_components.selora_ai.health_monitor.HEALTH_UNAVAILABLE_GRACE_SECS", 0
    )
    entry = MockConfigEntry(domain="sonos", entry_id="sonos_e", title="Sonos")
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    speaker = dev_reg.async_get_or_create(
        config_entry_id="sonos_e", identifiers={("sonos", "bedroom")}, name="Bedroom"
    )
    # Primary entity (no category): the media player — offline.
    media = ent_reg.async_get_or_create(
        "media_player", "sonos", "bedroom", device_id=speaker.id, config_entry=entry
    )
    hass.states.async_set(media.entity_id, "unavailable")
    # Config entities hold their last cached values while the speaker is gone.
    for domain, obj, val in (
        ("switch", "crossfade", "off"),
        ("switch", "loudness", "off"),
        ("number", "bass", "5"),
        ("number", "treble", "4"),
    ):
        cfg = ent_reg.async_get_or_create(
            domain,
            "sonos",
            obj,
            device_id=speaker.id,
            config_entry=entry,
            entity_category=EntityCategory.CONFIG,
        )
        hass.states.async_set(cfg.entity_id, val)

    await HealthMonitor(hass, health_store).async_scan()
    targets = {
        s["target"] for s in await health_store.get_signals(status="active", kind="unavailable")
    }
    assert media.entity_id in targets  # primary offline -> flagged despite config values


@pytest.mark.asyncio
async def test_scan_ignores_idle_speaker_with_unavailable_config_entity(
    health_store, hass, monkeypatch
):
    """A reachable speaker (media_player reports a real state) isn't flagged just
    because a by-design config entity is unavailable — the false-positive guard."""
    from homeassistant.const import EntityCategory
    from homeassistant.helpers import device_registry as dr
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    monkeypatch.setattr(
        "custom_components.selora_ai.health_monitor.HEALTH_UNAVAILABLE_GRACE_SECS", 0
    )
    entry = MockConfigEntry(domain="sonos", entry_id="sonos_e2", title="Sonos")
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    speaker = dev_reg.async_get_or_create(
        config_entry_id="sonos_e2", identifiers={("sonos", "kitchen")}, name="Kitchen"
    )
    media = ent_reg.async_get_or_create(
        "media_player", "sonos", "kitchen", device_id=speaker.id, config_entry=entry
    )
    hass.states.async_set(media.entity_id, "idle")  # reachable, just not playing
    cfg = ent_reg.async_get_or_create(
        "switch",
        "sonos",
        "surround",
        device_id=speaker.id,
        config_entry=entry,
        entity_category=EntityCategory.CONFIG,
    )
    hass.states.async_set(cfg.entity_id, "unavailable")  # by-design idle sub-entity

    await HealthMonitor(hass, health_store).async_scan()
    targets = {
        s["target"] for s in await health_store.get_signals(status="active", kind="unavailable")
    }
    assert cfg.entity_id not in targets  # reachable device -> not flagged


@pytest.mark.asyncio
async def test_flagged_device_stays_flagged_when_entity_vanishes(health_store, hass, monkeypatch):
    """A device already flagged offline must NOT flicker off when its integration
    briefly drops the entity from the state machine (Sonos rediscovery churn) —
    while the registry entry persists (enabled, entry loaded), keep the signal."""
    from unittest.mock import MagicMock

    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.helpers import device_registry as dr
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    monkeypatch.setattr(
        "custom_components.selora_ai.health_monitor.HEALTH_UNAVAILABLE_GRACE_SECS", 0
    )
    entry = MockConfigEntry(domain="sonos", entry_id="sonos_e3", title="Sonos")
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    speaker = dev_reg.async_get_or_create(
        config_entry_id="sonos_e3", identifiers={("sonos", "bedroom")}, name="Bedroom"
    )
    media = ent_reg.async_get_or_create(
        "media_player", "sonos", "bedroom", device_id=speaker.id, config_entry=entry
    )
    # It was flagged on a previous scan (active signal persisted)...
    await health_store.record_signal(
        kind="unavailable",
        target=media.entity_id,
        target_kind="entity",
        severity="warning",
        evidence={"unavailable_seconds": 3600},
        device_id=speaker.id,
    )
    # ...and now the integration has dropped it: no state object at all.
    assert hass.states.get(media.entity_id) is None

    # The entry is LOADED (stub the lookup rather than mock_state, which would
    # schedule a lingering config-entries save the test harness flags).
    loaded = MagicMock()
    loaded.state = ConfigEntryState.LOADED
    with patch.object(hass.config_entries, "async_get_entry", return_value=loaded):
        await HealthMonitor(hass, health_store).async_scan()
    targets = {
        s["target"] for s in await health_store.get_signals(status="active", kind="unavailable")
    }
    assert media.entity_id in targets  # sticky: kept despite vanishing


@pytest.mark.asyncio
async def test_flagged_device_not_resolved_by_unknown_state(health_store, hass, monkeypatch):
    """An entity reappearing as ``unknown`` during the remove/re-add churn does
    NOT count as recovery — the outage signal is kept until a genuinely
    reachable state appears (consistent with _UNREACHABLE_STATES)."""
    from unittest.mock import MagicMock

    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.helpers import device_registry as dr
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    monkeypatch.setattr(
        "custom_components.selora_ai.health_monitor.HEALTH_UNAVAILABLE_GRACE_SECS", 0
    )
    entry = MockConfigEntry(domain="sonos", entry_id="sonos_e4", title="Sonos")
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    speaker = dev_reg.async_get_or_create(
        config_entry_id="sonos_e4", identifiers={("sonos", "bedroom")}, name="Bedroom"
    )
    media = ent_reg.async_get_or_create(
        "media_player", "sonos", "bedroom", device_id=speaker.id, config_entry=entry
    )
    await health_store.record_signal(
        kind="unavailable",
        target=media.entity_id,
        target_kind="entity",
        severity="warning",
        evidence={"unavailable_seconds": 3600},
        device_id=speaker.id,
    )
    hass.states.async_set(media.entity_id, "unknown")  # reappears, but not recovered

    # LOADED entry via a stubbed lookup (mock_state would leave a lingering save).
    loaded = MagicMock()
    loaded.state = ConfigEntryState.LOADED
    with patch.object(hass.config_entries, "async_get_entry", return_value=loaded):
        await HealthMonitor(hass, health_store).async_scan()
    targets = {
        s["target"] for s in await health_store.get_signals(status="active", kind="unavailable")
    }
    assert media.entity_id in targets  # unknown != recovered -> still flagged


@pytest.mark.asyncio
async def test_silent_flags_only_periodic_reporters(health_store, hass, monkeypatch):
    """A periodic sensor (state_class) gone quiet is flagged silent; event-driven
    entities with the same update pattern (a light, a plain sensor with no
    state_class) are NOT — their update gaps aren't a reporting cadence."""
    monkeypatch.setattr("custom_components.selora_ai.health_monitor.HEALTH_SILENT_MIN_SECS", 10)
    monitor = HealthMonitor(hass, health_store)
    start = datetime.now(UTC) - timedelta(seconds=60)

    def build_cadence(eid: str, state_str: str) -> None:
        # 6 updates 1s apart → ema ~1s, 5 interval samples, last update ~55s ago.
        for i in range(6):
            ns = SimpleNamespace(state=state_str, last_updated=start + timedelta(seconds=i))
            monitor.handle_state_change(eid, ns, None)

    build_cadence("sensor.power", "120")
    build_cadence("sensor.last_button", "single")
    build_cadence("sensor.llm_calls", "6")
    build_cadence("light.desk", "on")
    hass.states.async_set("sensor.power", "120", {"state_class": "measurement"})
    hass.states.async_set("sensor.last_button", "single")  # no state_class
    # total_increasing counter (energy, LLM calls) — cumulative, event-driven.
    hass.states.async_set("sensor.llm_calls", "6", {"state_class": "total_increasing"})
    hass.states.async_set("light.desk", "on")  # event-driven, no state_class

    await monitor.async_scan()
    silent = {s["target"] for s in await health_store.get_signals(status="active", kind="silent")}
    assert "sensor.power" in silent  # periodic reporter → flagged
    assert "sensor.last_button" not in silent  # no cadence → not flagged
    assert "sensor.llm_calls" not in silent  # cumulative counter → not flagged
    assert "light.desk" not in silent  # event-driven → not flagged


@pytest.mark.asyncio
async def test_get_audit_scans_health_before_building(hass):
    """The on-load audit handler reconciles health signals (monitor scan) BEFORE
    building the audit, so the live page reflects current state, not a stale
    cache — same guarantee as the explicit Re-run."""
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.selora_ai.const import DOMAIN
    from custom_components.selora_ai.websocket.insights import _handle_get_audit

    handler = _handle_get_audit.__wrapped__
    order: list[str] = []
    monitor = MagicMock()
    monitor.async_request_scan = AsyncMock(side_effect=lambda: order.append("scan"))
    runner = MagicMock()
    # Must use the TRACKED path so a reload mid-load can drain it (async_stop).
    runner.async_run_tracked = AsyncMock(
        side_effect=lambda *a, **k: order.append("audit") or {"status": "ok"}
    )
    hass.data[DOMAIN] = {
        "e1": {"insights_engine": object(), "audit_runner": runner, "health_monitor": monitor}
    }
    conn = MagicMock()
    conn.user.is_admin = True

    await handler(hass, conn, {"id": 1})

    assert order == ["scan", "audit"]  # scan first, then audit reads fresh signals
    runner.async_run.assert_not_called()  # untracked path must not be used
    conn.send_result.assert_called_once()


@pytest.mark.asyncio
async def test_rerun_scans_health_before_audit(hass):
    """The Re-run handler reconciles health signals (monitor scan) BEFORE
    rebuilding the audit, so a recovered device / changed battery isn't shown
    stale from the last scheduled scan."""
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.selora_ai.const import DOMAIN
    from custom_components.selora_ai.websocket.insights import _handle_rerun_audit

    handler = _handle_rerun_audit.__wrapped__
    order: list[str] = []
    monitor = MagicMock()
    monitor.async_request_scan = AsyncMock(side_effect=lambda: order.append("scan"))
    runner = MagicMock()
    runner.async_run_tracked = AsyncMock(
        side_effect=lambda *a, **k: order.append("audit") or {"status": "ok"}
    )
    hass.data[DOMAIN] = {
        "e1": {
            "insights_engine": object(),
            "audit_runner": runner,
            "health_monitor": monitor,
        }
    }
    conn = MagicMock()
    conn.user.is_admin = True

    await handler(hass, conn, {"id": 1})

    assert order == ["scan", "audit"]  # scan first, then audit reads fresh signals
    conn.send_result.assert_called_once()


@pytest.mark.asyncio
async def test_binary_battery_sensor_flagged_when_on(health_store, hass):
    """A binary_sensor with device_class battery: on == low (flagged, no bogus
    numeric level), off == ok (not flagged)."""
    hass.states.async_set("binary_sensor.door_batt", "on", {"device_class": "battery"})
    hass.states.async_set("binary_sensor.window_batt", "off", {"device_class": "battery"})

    await HealthMonitor(hass, health_store).async_scan()
    sigs = {
        s["target"]: s for s in await health_store.get_signals(status="active", kind="battery_low")
    }
    assert "binary_sensor.door_batt" in sigs  # on → low
    assert "binary_sensor.window_batt" not in sigs  # off → ok
    assert "battery_level" not in sigs["binary_sensor.door_batt"]["evidence"]  # no fake %


@pytest.mark.asyncio
async def test_battery_low_detected_for_out_of_scope_domain(health_store, hass):
    """Low battery is flagged even for a domain outside COLLECTOR_DOMAINS (a
    valve): the battery reading is that device's only health signal. Guards the
    intentional scope-exemption of _detect_battery against a future 'fix'."""
    from custom_components.selora_ai.const import COLLECTOR_DOMAINS

    assert "valve" not in COLLECTOR_DOMAINS  # premise: valve isn't tracked
    hass.states.async_set("valve.garden", "open", {"battery_level": 5})

    await HealthMonitor(hass, health_store).async_scan()
    targets = {
        s["target"] for s in await health_store.get_signals(status="active", kind="battery_low")
    }
    assert "valve.garden" in targets


def test_count_availability_transitions():
    """Counts available<->unavailable flips across a recorder-style sequence
    (State-like objects), which is how intermittency survives a restart."""
    from custom_components.selora_ai.health_monitor import _count_availability_transitions

    seq = [
        SimpleNamespace(state="dry"),
        SimpleNamespace(state="unavailable"),
        SimpleNamespace(state="dry"),
        SimpleNamespace(state="unavailable"),
        SimpleNamespace(state="dry"),
    ]
    assert _count_availability_transitions(seq) == 4  # 4 flips → intermittent

    # A device that was up then died once = a single transition (not intermittent).
    assert (
        _count_availability_transitions(
            [SimpleNamespace(state="on"), SimpleNamespace(state="unavailable")]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_intermittent_unavailability_flagged(health_store, hass, monkeypatch):
    """A sensor that goes in and out over the day (a range issue) is flagged
    intermittent — not a flat dead-device outage — and the insight reframes it."""
    from custom_components.selora_ai.health_monitor import (
        _INTERMITTENT_MIN_TRANSITIONS,
        _EntityTrack,
    )

    monkeypatch.setattr(
        "custom_components.selora_ai.health_monitor.HEALTH_UNAVAILABLE_GRACE_SECS", 0
    )
    monitor = HealthMonitor(hass, health_store)
    now = datetime.now(UTC).timestamp()
    hass.states.async_set("binary_sensor.leak", "unavailable")
    track = _EntityTrack(last_available=False, last_avail_ts=now)
    for i in range(_INTERMITTENT_MIN_TRANSITIONS):
        track.transitions.append(now - i * 3600)  # hourly flips over the day
    monitor._tracks["binary_sensor.leak"] = track

    await monitor.async_scan()
    sig = (await health_store.get_signals(status="active", kind="unavailable"))[0]
    assert sig["evidence"].get("intermittent") is True

    # The deterministic insight reframes it as a range issue, not a failure.
    insight = (await InsightsEngine(hass, health_store).async_get_insights())[0]
    assert "drop" in insight["title"].lower() or "range" in insight["detail"].lower()


@pytest.mark.asyncio
async def test_scan_skips_selora_excluded_entities(health_store, hass, monkeypatch):
    """An entity muted with the 'Selora exclude' label (the same one the
    suggestion ignore-list uses) raises no health signal."""
    from homeassistant.helpers import label_registry as lr

    from custom_components.selora_ai.const import SELORA_EXCLUDE_LABEL_NAME

    monkeypatch.setattr(
        "custom_components.selora_ai.health_monitor.HEALTH_UNAVAILABLE_GRACE_SECS", 0
    )
    label = lr.async_get(hass).async_create(name=SELORA_EXCLUDE_LABEL_NAME)
    ent_reg = er.async_get(hass)
    muted = ent_reg.async_get_or_create("sensor", "d", "m", suggested_object_id="muted")
    ent_reg.async_update_entity(muted.entity_id, labels={label.label_id})
    other = ent_reg.async_get_or_create("sensor", "d", "o", suggested_object_id="other")
    hass.states.async_set(muted.entity_id, "unavailable")
    hass.states.async_set(other.entity_id, "unavailable")

    await HealthMonitor(hass, health_store).async_scan()
    targets = {
        s["target"] for s in await health_store.get_signals(status="active", kind="unavailable")
    }
    assert muted.entity_id not in targets  # muted via the exclude label
    assert other.entity_id in targets


@pytest.mark.asyncio
async def test_monitor_ignores_unknown_state(health_store, hass):
    """``unknown`` is a valid no-value state (TTS/notify services, etc.), not
    offline — it must not raise an unavailable signal."""
    monitor = HealthMonitor(hass, health_store)
    hass.states.async_set("sensor.tts_like", "unknown")
    await monitor.async_scan()
    assert await health_store.get_signals(status="active", kind="unavailable") == []


@pytest.mark.asyncio
async def test_critical_repair_issue_raises_integration_error(health_store, hass):
    """An active critical repair issue surfaces as an integration_error signal
    keyed to the affected integration."""
    ir.async_create_issue(
        hass,
        "zwave_js",
        "node_dead",
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="node_dead",
    )
    monitor = HealthMonitor(hass, health_store)
    await monitor.async_scan()

    signals = await health_store.get_signals(status="active", kind="integration_error")
    assert any(s["target"] == "zwave_js" for s in signals)


@pytest.mark.asyncio
async def test_ignored_repair_issue_is_skipped(health_store, hass):
    """A repair issue the user ignored in the Repairs UI (active=True but
    dismissed_version set) must NOT raise a signal."""
    ir.async_create_issue(
        hass,
        "zwave_js",
        "node_dead",
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="node_dead",
    )
    ir.async_ignore_issue(hass, "zwave_js", "node_dead", ignore=True)

    monitor = HealthMonitor(hass, health_store)
    await monitor.async_scan()

    signals = await health_store.get_signals(status="active", kind="integration_error")
    assert not any(s["target"] == "zwave_js" for s in signals)


@pytest.mark.asyncio
async def test_repair_issue_attributed_to_affected_integration(health_store, hass):
    """An issue raised on behalf of another integration (domain = creator,
    issue_domain = affected) is blamed on the affected integration."""
    ir.async_create_issue(
        hass,
        "homeassistant",  # creator
        "config_entry_reauth",
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="config_entry_reauth",
        issue_domain="spotify",  # affected integration
    )
    monitor = HealthMonitor(hass, health_store)
    await monitor.async_scan()

    signals = await health_store.get_signals(status="active", kind="integration_error")
    targets = {s["target"] for s in signals}
    assert "spotify" in targets
    assert "homeassistant" not in targets
