"""Tests for cache_invalidation — keeping learned caches in sync with removals."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.selora_ai.cache_invalidation import (
    StaleCacheInvalidator,
    _prune_memory_suggestions,
    async_clear_all_caches,
    async_invalidate_references,
)
from custom_components.selora_ai.const import DOMAIN, STALE_CACHE_PURGE_DELAY
from custom_components.selora_ai.pattern_store import PatternStore

from .conftest import MockStore


@pytest.fixture
def pattern_store(hass):
    """PatternStore backed by a MockStore."""
    with patch("custom_components.selora_ai.pattern_store.Store") as mock_cls:
        store_inst = MockStore()
        mock_cls.return_value = store_inst
        ps = PatternStore(hass)
        ps._store = store_inst
        yield ps


async def _seed(ps: PatternStore) -> None:
    ts = datetime.now(UTC).isoformat()
    await ps.record_state_change("light.gone", "on", "off", ts)
    await ps.record_state_change("light.kept", "on", "off", ts)
    await ps.save_pattern(
        {
            "pattern_id": "p_gone",
            "type": "time_based",
            "confidence": 0.8,
            "entity_ids": ["light.gone"],
            "description": "Stale",
        }
    )
    await ps.save_suggestion(
        {
            "suggestion_id": "s_gone",
            "pattern_id": "p_gone",
            "description": "Stale",
            "automation_data": {
                "alias": "Stale",
                "trigger": [{"platform": "state", "entity_id": "light.gone"}],
                "action": [{"service": "light.turn_on", "target": {"entity_id": "light.gone"}}],
            },
        }
    )


def _memory_caches(gone: str, kept: str) -> tuple[list, list]:
    proactive = [
        {"suggestion_id": "a", "automation_data": {"action": [{"entity_id": gone}]}},
        {"suggestion_id": "b", "automation_data": {"action": [{"entity_id": kept}]}},
    ]
    # collector-shaped: the item IS the automation dict
    latest = [
        {"alias": "x", "action": [{"entity_id": gone}]},
        {"alias": "y", "action": [{"entity_id": kept}]},
    ]
    return proactive, latest


def test_prune_memory_suggestions_both_shapes(hass):
    """Prunes stale entries from proactive (wrapped) and latest (bare) caches."""
    proactive, latest = _memory_caches("light.gone", "light.kept")
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["proactive_suggestions"] = proactive
    hass.data[DOMAIN]["latest_suggestions"] = latest

    removed = _prune_memory_suggestions(hass, {"light.gone"})

    assert removed == 2
    assert [s["suggestion_id"] for s in hass.data[DOMAIN]["proactive_suggestions"]] == ["b"]
    assert [s["alias"] for s in hass.data[DOMAIN]["latest_suggestions"]] == ["y"]


def test_prune_memory_suggestions_no_domain_data(hass):
    """Never raises when the integration data isn't set up."""
    hass.data.pop(DOMAIN, None)
    assert _prune_memory_suggestions(hass, {"light.gone"}) == 0


@pytest.mark.asyncio
async def test_async_invalidate_references_purges_store_and_memory(hass, pattern_store):
    """Combined invalidation clears both persisted and in-memory stale refs."""
    await _seed(pattern_store)
    proactive, latest = _memory_caches("light.gone", "light.kept")
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["proactive_suggestions"] = proactive
    hass.data[DOMAIN]["latest_suggestions"] = latest

    counts = await async_invalidate_references(hass, pattern_store, {"light.gone"})

    assert counts == {
        "history": 1,
        "patterns": 1,
        "suggestions": 1,
        "memory_suggestions": 2,
    }
    data = await pattern_store._get_loaded_data()
    assert "light.gone" not in data["state_history"]
    assert "p_gone" not in data["patterns"]
    assert "s_gone" not in data["suggestions"]


@pytest.mark.asyncio
async def test_async_clear_all_caches(hass, pattern_store):
    """Break-glass clears learned data and empties in-memory caches."""
    await _seed(pattern_store)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["proactive_suggestions"] = [{"suggestion_id": "a"}]
    hass.data[DOMAIN]["latest_suggestions"] = [{"alias": "x"}]

    counts = await async_clear_all_caches(hass, pattern_store)

    assert counts["patterns"] == 1
    assert counts["memory_suggestions"] == 2
    data = await pattern_store._get_loaded_data()
    assert data["patterns"] == {}
    assert hass.data[DOMAIN]["proactive_suggestions"] == []
    assert hass.data[DOMAIN]["latest_suggestions"] == []


@pytest.mark.asyncio
async def test_async_clear_all_caches_without_pattern_store(hass):
    """With pattern detection disabled (no store), still clears memory caches."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["proactive_suggestions"] = [{"suggestion_id": "a"}]
    hass.data[DOMAIN]["latest_suggestions"] = [{"alias": "x"}, {"alias": "y"}]

    counts = await async_clear_all_caches(hass, None)

    assert counts == {
        "history": 0,
        "patterns": 0,
        "suggestions": 0,
        "memory_suggestions": 3,
    }
    assert hass.data[DOMAIN]["proactive_suggestions"] == []
    assert hass.data[DOMAIN]["latest_suggestions"] == []


@pytest.mark.asyncio
async def test_invalidator_purges_on_entity_removed(hass, pattern_store):
    """A registry 'remove' event triggers a debounced purge of stale refs."""
    await _seed(pattern_store)

    invalidator = StaleCacheInvalidator(hass, pattern_store)
    invalidator.async_start()
    try:
        hass.bus.async_fire(
            er.EVENT_ENTITY_REGISTRY_UPDATED,
            {"action": "remove", "entity_id": "light.gone"},
        )
        await hass.async_block_till_done()

        # Debounced — nothing purged until the delay elapses.
        data = await pattern_store._get_loaded_data()
        assert "light.gone" in data["state_history"]

        async_fire_time_changed(
            hass, datetime.now(UTC) + timedelta(seconds=STALE_CACHE_PURGE_DELAY + 1)
        )
        await hass.async_block_till_done()

        data = await pattern_store._get_loaded_data()
        assert "light.gone" not in data["state_history"]
        assert "p_gone" not in data["patterns"]
        assert "s_gone" not in data["suggestions"]
    finally:
        invalidator.async_stop()


@pytest.mark.asyncio
async def test_invalidator_purges_device_only_suggestion_on_device_removed(hass, pattern_store):
    """Removing a device drops a device-only suggestion (no entity_id present)."""
    # A suggestion that targets a device via the device-action form only.
    await pattern_store.save_suggestion(
        {
            "suggestion_id": "s_device",
            "pattern_id": "",
            "description": "Device-only automation",
            "automation_data": {
                "alias": "Device only",
                "trigger": [{"platform": "device", "device_id": "dev123", "domain": "sensor"}],
                "action": [{"device_id": "dev123", "domain": "light", "type": "turn_on"}],
            },
        }
    )
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["latest_suggestions"] = [
        {"alias": "stale-dev", "action": [{"device_id": "dev123", "type": "turn_on"}]},
        {"alias": "live", "action": [{"entity_id": "light.kept"}]},
    ]

    invalidator = StaleCacheInvalidator(hass, pattern_store)
    invalidator.async_start()
    try:
        hass.bus.async_fire(
            dr.EVENT_DEVICE_REGISTRY_UPDATED,
            {"action": "remove", "device_id": "dev123"},
        )
        await hass.async_block_till_done()
        async_fire_time_changed(
            hass, datetime.now(UTC) + timedelta(seconds=STALE_CACHE_PURGE_DELAY + 1)
        )
        await hass.async_block_till_done()

        data = await pattern_store._get_loaded_data()
        assert "s_device" not in data["suggestions"]
        assert [s["alias"] for s in hass.data[DOMAIN]["latest_suggestions"]] == ["live"]
    finally:
        invalidator.async_stop()


@pytest.mark.asyncio
async def test_invalidator_prunes_memory_without_pattern_store(hass):
    """With pattern detection off (store=None), removals still prune memory."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["latest_suggestions"] = [
        {"alias": "stale", "action": [{"entity_id": "light.gone"}]},
        {"alias": "live", "action": [{"entity_id": "light.kept"}]},
    ]

    invalidator = StaleCacheInvalidator(hass, None)
    invalidator.async_start()
    try:
        hass.bus.async_fire(
            er.EVENT_ENTITY_REGISTRY_UPDATED,
            {"action": "remove", "entity_id": "light.gone"},
        )
        await hass.async_block_till_done()
        async_fire_time_changed(
            hass, datetime.now(UTC) + timedelta(seconds=STALE_CACHE_PURGE_DELAY + 1)
        )
        await hass.async_block_till_done()

        assert [s["alias"] for s in hass.data[DOMAIN]["latest_suggestions"]] == ["live"]
    finally:
        invalidator.async_stop()


@pytest.mark.asyncio
async def test_invalidator_ignores_create_and_stops_cleanly(hass, pattern_store):
    """'create' actions are ignored, and stop() cancels the pending timer."""
    await _seed(pattern_store)
    invalidator = StaleCacheInvalidator(hass, pattern_store)
    invalidator.async_start()

    hass.bus.async_fire(
        er.EVENT_ENTITY_REGISTRY_UPDATED,
        {"action": "create", "entity_id": "light.new"},
    )
    await hass.async_block_till_done()
    assert invalidator._cancel_flush is None
    assert not invalidator._pending

    # A pending removal that is cancelled by stop() must not fire.
    hass.bus.async_fire(
        er.EVENT_ENTITY_REGISTRY_UPDATED,
        {"action": "remove", "entity_id": "light.gone"},
    )
    await hass.async_block_till_done()
    invalidator.async_stop()

    async_fire_time_changed(
        hass, datetime.now(UTC) + timedelta(seconds=STALE_CACHE_PURGE_DELAY + 1)
    )
    await hass.async_block_till_done()

    data = await pattern_store._get_loaded_data()
    assert "light.gone" in data["state_history"]
