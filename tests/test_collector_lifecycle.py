"""Lifecycle tests for DataCollector's deferred initial cycle."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState

from custom_components.selora_ai.collector import DataCollector


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
    """
    collector = _make_collector(hass)
    collector._collect_analyze_log = AsyncMock()

    # Park the boot-grace sleep deterministically: the test harness
    # fast-forwards asyncio.sleep, so replace it with an awaitable that
    # suspends until cancelled, letting us cancel exactly mid-sleep.
    sleeping = asyncio.Event()

    async def _park(_seconds: float) -> None:
        sleeping.set()
        await asyncio.Future()  # never resolves → suspends until cancelled

    # HA still starting → async_start defers the first cycle behind the grace.
    hass.set_state(CoreState.starting)
    try:
        with patch(
            "custom_components.selora_ai.collector.asyncio.sleep", _park
        ):
            await collector.async_start()
            # Fire the started event so the cycle is scheduled, then wait until
            # it has entered the (parked) boot-grace sleep.
            hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
            await asyncio.wait_for(sleeping.wait(), timeout=5)

            assert collector._initial_cycle_task is not None
            assert not collector._initial_cycle_task.done()

            # Cancel mid-grace (disable/reload).
            await collector.async_stop()
    finally:
        hass.set_state(CoreState.running)

    # The cancellation must have aborted before collection ran.
    collector._collect_analyze_log.assert_not_awaited()
    collector._llm.analyze_home_data.assert_not_awaited()
