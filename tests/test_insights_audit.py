"""Tests for AuditRunner and the websocket device-signal grouping helpers.

The audit is now a deterministic run of the check catalog (see
test_insights_checks.py for the checks/score themselves); here we cover the
runner's task lifecycle (drain/cancel on unload) and the per-device signal
grouping used to collapse related signals into one card.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.health_store import HealthStore
from custom_components.selora_ai.insights_audit import AuditRunner
from custom_components.selora_ai.websocket.insights import _build_fixes, _group_signals

from .conftest import MockStore


@pytest.fixture
def health_store(hass):
    with patch("custom_components.selora_ai.health_store.Store") as mock_cls:
        store_inst = MockStore()
        mock_cls.return_value = store_inst
        hs = HealthStore(hass)
        hs._store = store_inst
        yield hs


def _blocking_checks(started: asyncio.Event, release: asyncio.Event, state: dict):
    """A stand-in for async_run_checks that blocks until released, so an
    in-flight audit run can be caught mid-flight."""

    async def _run(_hass) -> list:
        started.set()
        await release.wait()
        state["completed"] = True
        return []

    return _run


# ── Runner lifecycle (drain/cancel on unload) ─────────────────────────


@pytest.mark.asyncio
async def test_inflight_periodic_run_cancelled_on_stop(hass, health_store, monkeypatch) -> None:
    """A periodic run in flight during a reload must be cancelled/drained on
    unload — otherwise it finishes holding a stale HealthStore and saves the
    whole document over the replacement entry's store."""
    started, release, state = asyncio.Event(), asyncio.Event(), {"completed": False}
    monkeypatch.setattr(
        "custom_components.selora_ai.insights_checks.async_run_checks",
        _blocking_checks(started, release, state),
    )
    runner = AuditRunner(hass, health_store)
    await runner.async_start(interval_hours=24)

    runner._periodic_run(None)
    await asyncio.wait_for(started.wait(), timeout=1)
    assert any(not t.done() for t in runner._tasks)

    await runner.async_stop()

    assert runner._tasks == set()
    assert state["completed"] is False  # cancelled before finishing


@pytest.mark.asyncio
async def test_not_settling_when_ha_already_running(hass, health_store) -> None:
    """HA already up at setup (a reload / late-added entry) → devices are online,
    so nothing to settle: the score is trustworthy immediately."""
    hass.set_state(CoreState.running)
    runner = AuditRunner(hass, health_store)
    await runner.async_start(interval_hours=24)
    try:
        assert runner.is_settling() is False
    finally:
        await runner.async_stop()


@pytest.mark.asyncio
async def test_settling_window_from_boot_until_grace(hass, health_store) -> None:
    """A home that boots with the entry present is "settling" until HA fires
    STARTED and a fixed grace elapses — then it latches to trusted."""
    hass.set_state(CoreState.not_running)
    runner = AuditRunner(hass, health_store)
    await runner.async_start(interval_hours=24)
    try:
        # Before STARTED: settling, no deadline yet, panel told to poll shortly.
        assert runner.is_settling() is True
        assert runner._settle_deadline is None
        assert runner.settle_retry_seconds() == 15

        # STARTED fires → grace window opens; still settling, retry ≤ grace.
        runner._on_ha_started(None)
        assert runner.is_settling() is True
        assert 0 < runner.settle_retry_seconds() <= 90

        # Grace elapsed → settled, and it latches (never flips back).
        runner._settle_deadline = datetime.now(UTC) - timedelta(seconds=1)
        assert runner.is_settling() is False
        assert runner._settled is True
        assert runner.is_settling() is False
    finally:
        await runner.async_stop()


@pytest.mark.asyncio
async def test_periodic_run_spawns_tracked_task_and_persists(hass, health_store) -> None:
    """A normal periodic tick runs as a TRACKED task that completes, persists
    its result, and removes itself from the tracking set."""
    runner = AuditRunner(hass, health_store)
    await runner.async_start(interval_hours=24)

    runner._periodic_run(None)
    await asyncio.gather(*list(runner._tasks))  # let the spawned run finish
    await hass.async_block_till_done()

    audit = await health_store.get_last_audit()
    assert audit is not None
    assert audit["status"] == "ok"
    await runner.async_stop()  # drains any straggler task
    assert runner._tasks == set()


@pytest.mark.asyncio
async def test_manual_rerun_tracked_and_drained_on_stop(hass, health_store, monkeypatch) -> None:
    """A manual rerun (async_run_tracked) in flight during a reload must be
    tracked and cancelled/drained on unload."""
    started, release, state = asyncio.Event(), asyncio.Event(), {"completed": False}
    monkeypatch.setattr(
        "custom_components.selora_ai.insights_checks.async_run_checks",
        _blocking_checks(started, release, state),
    )
    runner = AuditRunner(hass, health_store)

    task = asyncio.create_task(runner.async_run_tracked())
    await asyncio.wait_for(started.wait(), timeout=1)
    assert any(not t.done() for t in runner._tasks)

    await runner.async_stop()

    assert state["completed"] is False  # cancelled before finishing
    with suppress(asyncio.CancelledError):
        await task
    assert runner._tasks == set()


# ── Device-signal grouping (websocket helpers) ────────────────────────


@pytest.mark.asyncio
async def test_group_signals_collapses_per_device(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain="hue", entry_id="hue_e", title="Hue")
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id="hue_e",
        identifiers={("hue", "d1")},
        name="Living Room Lamp",
    )
    for slug in ("a", "b"):
        ent_reg.async_get_or_create(
            "sensor",
            "hue",
            slug,
            device_id=device.id,
            config_entry=entry,
            suggested_object_id=f"lamp_{slug}",
        )

    signals = [
        {
            "signal_id": f"unavailable:sensor.lamp_{s}",
            "kind": "unavailable",
            "target": f"sensor.lamp_{s}",
            "target_kind": "entity",
            "severity": "warning",
            "evidence": {"unavailable_seconds": 600},
        }
        for s in ("a", "b")
    ]

    groups = _group_signals(hass, signals)
    assert len(groups) == 1
    g = groups[0]
    assert g["name"] == "Living Room Lamp"
    assert g["device_id"] == device.id
    assert len(g["items"]) == 2
    assert {i["entity_id"] for i in g["items"]} == {"sensor.lamp_a", "sensor.lamp_b"}


@pytest.mark.asyncio
async def test_fully_down_device_becomes_offline_fix(hass: HomeAssistant) -> None:
    """Every entity of a device unavailable -> group fully_down + an 'offline' fix."""
    entry = MockConfigEntry(domain="sonos", entry_id="s2", title="Sonos")
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id="s2", identifiers={("sonos", "x")}, name="Bedroom"
    )
    eids = []
    for slug in ("a", "b"):
        e = ent_reg.async_get_or_create(
            "switch",
            "sonos",
            slug,
            device_id=device.id,
            config_entry=entry,
            suggested_object_id=f"bed_{slug}",
        )
        eids.append(e.entity_id)
        hass.states.async_set(e.entity_id, "unavailable")
    signals = [
        {
            "signal_id": f"unavailable:{eid}",
            "kind": "unavailable",
            "target": eid,
            "target_kind": "entity",
            "severity": "warning",
            "evidence": {"unavailable_seconds": 1800},
        }
        for eid in eids
    ]
    groups = _group_signals(hass, signals)
    assert groups[0]["fully_down"] is True
    fixes = _build_fixes(groups)
    assert any(f["kind"] == "unavailable" and "offline" in f["title"].lower() for f in fixes)


@pytest.mark.asyncio
async def test_single_entity_device_becomes_offline_fix(hass: HomeAssistant) -> None:
    """A device with exactly one enabled stateful entity, unavailable, is fully
    down (matches the monitor's retention) -> it still gets an offline fix."""
    entry = MockConfigEntry(domain="shelly", entry_id="sh1", title="Shelly")
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id="sh1", identifiers={("shelly", "y")}, name="Garage Sensor"
    )
    e = ent_reg.async_get_or_create(
        "binary_sensor",
        "shelly",
        "only",
        device_id=device.id,
        config_entry=entry,
        suggested_object_id="garage",
    )
    hass.states.async_set(e.entity_id, "unavailable")
    signals = [
        {
            "signal_id": f"unavailable:{e.entity_id}",
            "kind": "unavailable",
            "target": e.entity_id,
            "target_kind": "entity",
            "severity": "warning",
            "evidence": {"unavailable_seconds": 1800},
        }
    ]
    groups = _group_signals(hass, signals)
    assert groups[0]["fully_down"] is True
    fixes = _build_fixes(groups)
    assert any(f["kind"] == "unavailable" and "offline" in f["title"].lower() for f in fixes)


@pytest.mark.asyncio
async def test_low_battery_becomes_fix(hass: HomeAssistant) -> None:
    """A battery_low signal surfaces as a 'Replace battery' fix with the level."""
    entry = MockConfigEntry(domain="zha", entry_id="z1", title="ZHA")
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    e = ent_reg.async_get_or_create(
        "sensor", "zha", "batt", config_entry=entry, suggested_object_id="outdoor_batt"
    )
    hass.states.async_set(e.entity_id, "12")
    signals = [
        {
            "signal_id": f"battery_low:{e.entity_id}",
            "kind": "battery_low",
            "target": e.entity_id,
            "target_kind": "entity",
            "severity": "warning",
            "evidence": {"battery_level": 12},
        }
    ]
    fixes = _build_fixes(_group_signals(hass, signals))
    assert len(fixes) == 1
    assert "battery" in fixes[0]["title"].lower()
    assert "12%" in fixes[0]["title"]
    assert e.entity_id in fixes[0]["entities"]
