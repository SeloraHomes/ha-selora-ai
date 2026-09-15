"""Lifecycle tests for DataCollector's deferred initial cycle."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.selora_ai.collector import _INITIAL_CYCLE_BOOT_GRACE, DataCollector


def _make_collector(hass) -> DataCollector:
    """Create a DataCollector with a mock LLM client."""
    llm = MagicMock()
    llm.analyze_home_data = AsyncMock(return_value=[])
    llm._max_suggestions = 3
    return DataCollector(hass, llm)


@pytest.mark.asyncio
async def test_initial_cycle_cancelled_during_boot_grace_skips_collection(
    hass,
) -> None:
    """Stopping the collector during the boot-grace window must abort the
    initial cycle, not fall through to the expensive collect/analyze run.

    The window is an armed timer rather than a task sleeping it out (a
    tracked sleeping task makes every ``async_block_till_done()`` wait the
    whole window), so what has to be cancelled is the timer — and the proof
    is that the grace can elapse afterwards without collecting.
    """
    collector = _make_collector(hass)
    collector._collect_analyze_log = AsyncMock()

    # HA still starting → async_start defers the first cycle behind the grace.
    hass.set_state(CoreState.starting)
    try:
        await collector.async_start()
        # Fire the started event so the grace timer is armed.
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()

        assert collector._initial_cycle_unsub is not None
        assert collector._initial_cycle_task is None

        # Stop mid-grace (disable/reload).
        await collector.async_stop()
    finally:
        hass.set_state(CoreState.running)

    # Let the window elapse: the cancelled timer must not fire.
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=_INITIAL_CYCLE_BOOT_GRACE + 1)
    )
    await hass.async_block_till_done()

    assert collector._initial_cycle_task is None
    collector._collect_analyze_log.assert_not_awaited()
    collector._llm.analyze_home_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_boot_grace_does_not_block_till_done(hass) -> None:
    """The grace window must not be a pending task.

    ``async_create_task`` registers the task with HA, so a task sleeping out
    the window holds up bootstrap, every config-entry reload, and every
    test's ``async_block_till_done()`` for the full minute.
    """
    collector = _make_collector(hass)
    collector._collect_analyze_log = AsyncMock()

    hass.set_state(CoreState.starting)
    try:
        await collector.async_start()
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await asyncio.wait_for(hass.async_block_till_done(), timeout=5)
        collector._collect_analyze_log.assert_not_awaited()
    finally:
        await collector.async_stop()
        hass.set_state(CoreState.running)


@pytest.mark.asyncio
async def test_initial_cycle_runs_when_the_grace_elapses(hass) -> None:
    """The deferral must still fire — a window nothing collects after is
    the same bug as no initial cycle at all.
    """
    collector = _make_collector(hass)
    collector._collect_analyze_log = AsyncMock()

    hass.set_state(CoreState.starting)
    try:
        await collector.async_start()
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()

        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(seconds=_INITIAL_CYCLE_BOOT_GRACE + 1)
        )
        await hass.async_block_till_done()

        collector._collect_analyze_log.assert_awaited_once()
    finally:
        await collector.async_stop()
        hass.set_state(CoreState.running)
