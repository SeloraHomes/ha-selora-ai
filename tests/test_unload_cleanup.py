"""Tests for ``async_unload_entry`` cleanup correctness.

Memory-leak guard at the lifecycle level: every reload of the
integration must drop every reference it created during setup. If
unload skips a listener, a coroutine task, or a cached object, those
ghosts accumulate one-per-reload and leak across the lifetime of the
HA process.

We don't drive ``async_setup_entry`` end-to-end (that pulls in the
LLM client, recorder, dispatcher, etc.). Instead we stage the
``hass.data[DOMAIN]`` layout the way setup leaves it — collector,
pattern store, background tasks, listener unsubs, scheduled-task
tracker, JWT validator, ancillary caches — then call
``async_unload_entry`` and assert the layout is empty.

When new state is added to setup, mirror it here. The test is a
contract: "anything setup creates, unload must destroy."
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai import async_unload_entry
from custom_components.selora_ai.const import (
    CONF_ENTRY_TYPE,
    DOMAIN,
    ENTRY_TYPE_DEVICE,
    ENTRY_TYPE_LLM,
)


def _make_entry(entry_type: str = ENTRY_TYPE_LLM) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id=f"test_entry_{entry_type}",
        data={CONF_ENTRY_TYPE: entry_type},
    )


async def _make_pending_task() -> asyncio.Task[None]:
    """A task that will live forever unless cancelled.

    Using ``asyncio.sleep`` with a far-future delay so the test fails
    loudly if unload doesn't cancel it (the test would otherwise hang).
    """

    async def _forever() -> None:
        await asyncio.sleep(3600)

    return asyncio.ensure_future(_forever())


@pytest.mark.asyncio
async def test_device_entry_unloads_without_runtime_state(hass) -> None:
    """Device-onboarding entries carry no runtime state, so unload is a
    no-op that returns True. Verify the short-circuit so a regression
    that drops the early return doesn't crash on missing keys.
    """
    entry = _make_entry(entry_type=ENTRY_TYPE_DEVICE)
    # No hass.data setup — device entries never populate it.
    result = await async_unload_entry(hass, entry)
    assert result is True


@pytest.mark.asyncio
async def test_unload_drops_entry_specific_data(hass) -> None:
    """Per-entry dict under ``hass.data[DOMAIN][entry_id]`` must be
    popped so subsequent reloads don't see stale collector/llm refs.
    """
    entry = _make_entry()
    collector = MagicMock()
    collector.async_stop = AsyncMock()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "llm": MagicMock(),
        "collector": collector,
        "device_manager": MagicMock(),
        "unsub_discovery": None,
        "_background_tasks": [],
    }

    await async_unload_entry(hass, entry)

    assert entry.entry_id not in hass.data.get(DOMAIN, {}), (
        "Unload left the per-entry data in place — a reload would see "
        "stale references to the prior collector/llm instances."
    )
    collector.async_stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_unload_cancels_tracked_background_tasks(hass) -> None:
    """Every task appended to ``_background_tasks`` must be cancelled
    and awaited, so coroutine frames + traceback objects don't survive
    the unload.
    """
    entry = _make_entry()
    t1 = await _make_pending_task()
    t2 = await _make_pending_task()
    collector = MagicMock()
    collector.async_stop = AsyncMock()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "collector": collector,
        "_background_tasks": [t1, t2],
        "unsub_discovery": None,
    }

    await async_unload_entry(hass, entry)

    assert t1.cancelled(), "Background task 1 was not cancelled on unload"
    assert t2.cancelled(), "Background task 2 was not cancelled on unload"


@pytest.mark.asyncio
async def test_unload_invokes_discovery_unsub(hass) -> None:
    """The discovery unsubscribe callback registered at setup time
    must fire on unload so the listener doesn't outlive the entry.
    """
    entry = _make_entry()
    unsub = MagicMock()
    collector = MagicMock()
    collector.async_stop = AsyncMock()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "collector": collector,
        "_background_tasks": [],
        "unsub_discovery": unsub,
    }

    await async_unload_entry(hass, entry)

    unsub.assert_called_once()


@pytest.mark.asyncio
async def test_unload_stops_pattern_engine_and_flushes_store(hass) -> None:
    """Pattern engine timers + initial-scan tasks must stop; pattern
    store must flush so in-memory state changes hit disk before
    teardown.
    """
    entry = _make_entry()
    collector = MagicMock()
    collector.async_stop = AsyncMock()
    pattern_engine = MagicMock()
    pattern_engine.async_stop = AsyncMock()
    pattern_store = MagicMock()
    pattern_store.flush = AsyncMock()
    unsub_state = MagicMock()
    unsub_enrichment = MagicMock()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "collector": collector,
        "_background_tasks": [],
        "unsub_discovery": None,
        "pattern_engine": pattern_engine,
        "pattern_store": pattern_store,
        "unsub_state_listener": unsub_state,
        "unsub_enrichment": unsub_enrichment,
    }

    await async_unload_entry(hass, entry)

    pattern_engine.async_stop.assert_awaited_once()
    pattern_store.flush.assert_awaited_once()
    unsub_state.assert_called_once()
    unsub_enrichment.assert_called_once()


@pytest.mark.asyncio
async def test_unload_closes_mcp_token_store_and_clears_shared_keys(hass) -> None:
    """Shared singletons under ``hass.data[DOMAIN]`` (token store, JWT
    validator, suggestion caches, scheduled-task tracker) must drop on
    unload — otherwise they leak references to the old config entry's
    settings.
    """
    entry = _make_entry()
    collector = MagicMock()
    collector.async_stop = AsyncMock()
    mcp_token_store = MagicMock()
    mcp_token_store.async_close = AsyncMock()
    scheduled_tracker = MagicMock()
    scheduled_tracker.cancel_all_pending = MagicMock(return_value=2)

    hass.data.setdefault(DOMAIN, {}).update(
        {
            entry.entry_id: {
                "collector": collector,
                "_background_tasks": [],
                "unsub_discovery": None,
            },
            "mcp_token_store": mcp_token_store,
            "selora_jwt_validator": MagicMock(),
            "_scheduled_tasks": scheduled_tracker,
            "proactive_suggestions": {"foo": "bar"},
            "latest_suggestions": [{"x": 1}],
            "_conv_store": MagicMock(),
            "_scene_store": MagicMock(),
        }
    )

    await async_unload_entry(hass, entry)

    mcp_token_store.async_close.assert_awaited_once()
    scheduled_tracker.cancel_all_pending.assert_called_once()

    leaked = [
        k
        for k in (
            "mcp_token_store",
            "selora_jwt_validator",
            "_scheduled_tasks",
            "proactive_suggestions",
            "latest_suggestions",
            "_conv_store",
            "_scene_store",
        )
        if k in hass.data.get(DOMAIN, {})
    ]
    assert not leaked, (
        f"Shared keys survived unload: {leaked}. Each one holds references "
        f"to the prior entry's runtime; failing to drop them leaks one "
        f"copy per reload across the lifetime of HA."
    )


@pytest.mark.asyncio
async def test_unload_is_safe_when_optional_state_is_missing(hass) -> None:
    """A partial setup (e.g. failed mid-init) leaves only some keys.
    Unload must tolerate missing pattern engine / store / scheduled
    tracker rather than KeyError-ing and orphaning the rest.
    """
    entry = _make_entry()
    collector = MagicMock()
    collector.async_stop = AsyncMock()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "collector": collector,
        "_background_tasks": [],
        "unsub_discovery": None,
        # Deliberately no pattern_engine / pattern_store / unsub_*.
    }

    # Must not raise.
    result = await async_unload_entry(hass, entry)
    assert result is True
    assert entry.entry_id not in hass.data.get(DOMAIN, {})
