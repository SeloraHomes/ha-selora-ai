"""Setup must leave nothing pending that a wait for quiescence has to sit out.

``hass.async_create_task`` registers the task with HA, so everything that
waits for HA to go quiet — bootstrap, a config-entry reload, and every test's
``async_block_till_done()`` — waits for it to finish. A task whose body is
``await asyncio.sleep(delay)`` therefore makes each of those wait the whole
delay for a sleep nobody wants the answer to.

That is not hypothetical: the startup telemetry snapshot slept
``TELEMETRY_SNAPSHOT_STARTUP_DELAY`` (120s) and the initial network discovery
slept 30s, so a conversation test doing 400ms of work took a flat 120 seconds,
which is what made the Allen benchmark (one HomeAssistant per case) unusable.

The delays themselves are wanted — registries need to be populated, the cloud
proxy is cold right after boot. So each is armed as a TIMER and fires a
BACKGROUND task, which nothing blocks on. These tests pin both halves: the
wait returns immediately, and the deferred work still happens.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.selora_ai import _INITIAL_DISCOVERY_DELAY_SECONDS
from custom_components.selora_ai.const import (
    CONF_COLLECTOR_ENABLED,
    CONF_DISCOVERY_ENABLED,
    CONF_ENTRY_TYPE,
    CONF_INSIGHTS_ENABLED,
    DOMAIN,
    ENTRY_TYPE_LLM,
    TELEMETRY_SNAPSHOT_STARTUP_DELAY,
)

# Every deferral setup arms. The wait must sit out none of them.
_LONGEST_STARTUP_DELAY = max(TELEMETRY_SNAPSHOT_STARTUP_DELAY, _INITIAL_DISCOVERY_DELAY_SECONDS)
# Generous enough to survive a loaded CI runner, far below any of the delays.
_QUIESCE_TIMEOUT = 10.0


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id="startup_delays",
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_LLM,
            "llm_provider": "ollama",
            "ollama_host": "http://127.0.0.1:1",
            "ollama_model": "test-model",
        },
        # The benchmark's own configuration: everything that would make an
        # LLM call per case is off, so what is left under test is the
        # deferrals setup arms unconditionally.
        options={
            CONF_DISCOVERY_ENABLED: True,
            CONF_COLLECTOR_ENABLED: False,
            CONF_INSIGHTS_ENABLED: False,
            "pattern_detection_enabled": False,
        },
    )


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    """Set up a real config entry with the network kept out of it."""
    await async_setup_component(hass, "homeassistant", {})
    await async_setup_component(hass, "http", {})
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.selora_ai.llm_client.LLMClient.health_check",
        AsyncMock(return_value=True),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await asyncio.wait_for(hass.async_block_till_done(), timeout=_QUIESCE_TIMEOUT)
    return entry


@pytest.mark.asyncio
async def test_setup_leaves_nothing_to_wait_out(hass: HomeAssistant) -> None:
    """``async_block_till_done()`` after setup must return promptly.

    A failure here is a timeout, not an assertion: the regression it guards
    makes the wait take two minutes, so the test would otherwise pass slowly
    rather than fail.
    """
    entry = await _setup(hass)

    await asyncio.wait_for(hass.async_block_till_done(), timeout=_QUIESCE_TIMEOUT)

    # And no tracked task is merely sleeping toward one of the delays.
    sleeping = [
        task for task in asyncio.all_tasks() if not task.done() and "sleep" in repr(task.get_coro())
    ]
    assert not sleeping, f"setup left a sleeping task: {sleeping}"

    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_startup_telemetry_snapshot_still_fires(hass: HomeAssistant) -> None:
    """The delay is deferral, not removal — the snapshot must still be sent."""
    entry = await _setup(hass)

    with patch(
        "custom_components.selora_ai.telemetry.TelemetryClient.async_send_snapshot",
        AsyncMock(),
    ) as send_snapshot:
        async_fire_time_changed(
            hass,
            dt_util.utcnow() + timedelta(seconds=TELEMETRY_SNAPSHOT_STARTUP_DELAY + 1),
        )
        await asyncio.wait_for(hass.async_block_till_done(), timeout=_QUIESCE_TIMEOUT)

    send_snapshot.assert_awaited()

    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_initial_discovery_still_fires(hass: HomeAssistant) -> None:
    """Same for the one-off startup network discovery."""
    entry = await _setup(hass)

    with patch(
        "custom_components.selora_ai.device_manager.DeviceManager.discover_network_devices",
        AsyncMock(return_value={"summary": {}}),
    ) as discover:
        async_fire_time_changed(
            hass,
            dt_util.utcnow() + timedelta(seconds=_INITIAL_DISCOVERY_DELAY_SECONDS + 1),
        )
        await asyncio.wait_for(hass.async_block_till_done(), timeout=_QUIESCE_TIMEOUT)

    discover.assert_awaited()

    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_unload_cancels_the_unfired_startup_timers(hass: HomeAssistant) -> None:
    """A timer outliving its entry would spawn its task against a torn-down
    entry on the next reload's clock.

    Asserted by RELOADING the same entry_id rather than just unloading: the
    callbacks return early when the entry is gone, so an uncancelled timer
    firing into an unloaded hass is indistinguishable from a cancelled one.
    Firing it into a RELOADED entry is not — the stale timer finds live entry
    data, does the work a second time, and blanks the unsub handle the fresh
    timer needs.
    """
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)

    # Same entry_id, set up again — the reload the stale timer would land in.
    with patch(
        "custom_components.selora_ai.llm_client.LLMClient.health_check",
        AsyncMock(return_value=True),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await asyncio.wait_for(hass.async_block_till_done(), timeout=_QUIESCE_TIMEOUT)

    with (
        patch(
            "custom_components.selora_ai.telemetry.TelemetryClient.async_send_snapshot",
            AsyncMock(),
        ) as send_snapshot,
        patch(
            "custom_components.selora_ai.device_manager.DeviceManager.discover_network_devices",
            AsyncMock(return_value={"summary": {}}),
        ) as discover,
    ):
        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(seconds=_LONGEST_STARTUP_DELAY + 1)
        )
        await asyncio.wait_for(hass.async_block_till_done(), timeout=_QUIESCE_TIMEOUT)

    # Once — from the reload's own timers. Twice means the unloaded entry's
    # timers were still armed.
    assert send_snapshot.await_count == 1
    assert discover.await_count == 1

    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_nothing_deferred_survives_an_unload(hass: HomeAssistant) -> None:
    """With no reload to fire into, the timers must simply do nothing."""
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)

    with (
        patch(
            "custom_components.selora_ai.telemetry.TelemetryClient.async_send_snapshot",
            AsyncMock(),
        ) as send_snapshot,
        patch(
            "custom_components.selora_ai.device_manager.DeviceManager.discover_network_devices",
            AsyncMock(return_value={"summary": {}}),
        ) as discover,
    ):
        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(seconds=_LONGEST_STARTUP_DELAY + 1)
        )
        await asyncio.wait_for(hass.async_block_till_done(), timeout=_QUIESCE_TIMEOUT)

    send_snapshot.assert_not_awaited()
    discover.assert_not_awaited()
