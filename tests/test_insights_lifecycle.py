"""Insights startup must not create foreground tasks that HA bootstrap awaits.

The delayed initial scan/publish sleep for 60-90s. If they were scheduled via
``hass.async_create_task`` (a foreground task), ``async_block_till_done`` during
startup or a config-entry reload would wait on the sleeper and could trip the
bootstrap watchdog. ``async_start`` instead arms an untracked ``async_call_later``
timer, so it returns immediately and the real work runs as a background task
well after bootstrap.
"""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai.health_monitor import HealthMonitor
from custom_components.selora_ai.insights_export import InsightsExporter


@pytest.mark.asyncio
async def test_health_monitor_start_arms_timer_not_task(hass: HomeAssistant) -> None:
    monitor = HealthMonitor(hass, store=SimpleNamespace())  # type: ignore[arg-type]
    await monitor.async_start(interval=3600)
    try:
        # A timer is armed; no background task exists yet for bootstrap to await.
        assert monitor._unsub_initial is not None
        assert monitor._tasks == set()
        # Would hang for the full delay here if the initial scan were a
        # foreground sleeper task; instead it returns at once.
        await hass.async_block_till_done()
        assert monitor._tasks == set()
    finally:
        await monitor.async_stop()


@pytest.mark.asyncio
async def test_insights_exporter_start_arms_timer_not_task(hass: HomeAssistant) -> None:
    exporter = InsightsExporter(hass, health_store=None, insights_engine=None)  # type: ignore[arg-type]
    await exporter.async_start(cadence=3600, retention=3)
    try:
        assert exporter._unsub_initial is not None
        assert exporter._tasks == set()
        await hass.async_block_till_done()
        assert exporter._tasks == set()
    finally:
        await exporter.async_stop()
