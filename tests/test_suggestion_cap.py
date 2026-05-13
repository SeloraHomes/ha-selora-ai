"""Tests for dynamic suggestion cap logic."""
from __future__ import annotations

import json
from math import ceil
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.selora_ai.const import (
    DEFAULT_DEVICES_PER_SUGGESTION,
    DEFAULT_MAX_SUGGESTIONS_CEILING,
    DEFAULT_MIN_SUGGESTIONS,
)


def _make_entity_entry(entity_id: str, device_id: str | None = None) -> MagicMock:
    """Create a mock entity registry entry."""
    entry = MagicMock()
    entry.entity_id = entity_id
    entry.device_id = device_id
    return entry


def _make_device(device_id: str) -> MagicMock:
    """Create a mock device registry entry."""
    device = MagicMock()
    device.id = device_id
    return device


def _make_collector(
    device_ids: list[str],
    entity_map: dict[str, list[str]],
    automations_yaml: list[dict] | None = None,
):
    """Create a DataCollector with mocked registries.

    Args:
        device_ids: list of device IDs in the device registry.
        entity_map: device_id → list of entity_ids (entity registry).
        automations_yaml: automation configs as they appear in automations.yaml.
    """
    from custom_components.selora_ai.collector import DataCollector

    hass = MagicMock()
    hass.config.config_dir = "/config"
    hass.async_add_executor_job = AsyncMock(return_value=automations_yaml or [])

    # Mock device registry
    devices = {did: _make_device(did) for did in device_ids}
    mock_device_reg = MagicMock()
    mock_device_reg.devices = devices

    # Mock entity registry
    entities = {}
    idx = 0
    for device_id, eids in entity_map.items():
        for eid in eids:
            entities[str(idx)] = _make_entity_entry(eid, device_id)
            idx += 1
    mock_entity_reg = MagicMock()
    mock_entity_reg.entities.values.return_value = list(entities.values())

    collector = DataCollector.__new__(DataCollector)
    collector._hass = hass

    return collector, mock_device_reg, mock_entity_reg


class TestDynamicCap:
    """Test _calculate_dynamic_cap scaling logic."""

    async def _run(self, collector, mock_device_reg, mock_entity_reg):
        """Call _calculate_dynamic_cap with patched registries."""
        with (
            patch(
                "custom_components.selora_ai.collector.dr.async_get",
                return_value=mock_device_reg,
            ),
            patch(
                "custom_components.selora_ai.collector.er.async_get",
                return_value=mock_entity_reg,
            ),
        ):
            return await collector._calculate_dynamic_cap()

    async def test_small_home_floor(self):
        """Small home (5 devices, 0 covered) gets MIN_SUGGESTIONS."""
        device_ids = [f"dev_{i}" for i in range(5)]
        entity_map = {f"dev_{i}": [f"light.lamp_{i}"] for i in range(5)}
        collector, dreg, ereg = _make_collector(device_ids, entity_map)
        cap = await self._run(collector, dreg, ereg)
        # ceil(5/5) = 1, floor is 3
        assert cap == DEFAULT_MIN_SUGGESTIONS

    async def test_large_home_scales(self):
        """Large home (40 uncovered devices) scales up."""
        device_ids = [f"dev_{i}" for i in range(40)]
        entity_map = {f"dev_{i}": [f"light.lamp_{i}"] for i in range(40)}
        collector, dreg, ereg = _make_collector(device_ids, entity_map)
        cap = await self._run(collector, dreg, ereg)
        expected = min(
            ceil(40 / DEFAULT_DEVICES_PER_SUGGESTION),
            DEFAULT_MAX_SUGGESTIONS_CEILING,
        )
        assert cap == expected

    async def test_ceiling_respected(self):
        """Even with 100 devices, cap doesn't exceed ceiling."""
        device_ids = [f"dev_{i}" for i in range(100)]
        entity_map = {f"dev_{i}": [f"switch.s_{i}"] for i in range(100)}
        collector, dreg, ereg = _make_collector(device_ids, entity_map)
        cap = await self._run(collector, dreg, ereg)
        assert cap <= DEFAULT_MAX_SUGGESTIONS_CEILING

    async def test_covered_devices_reduce_cap(self):
        """Devices already covered by automations reduce the cap."""
        device_ids = [f"dev_{i}" for i in range(10)]
        entity_map = {f"dev_{i}": [f"light.lamp_{i}"] for i in range(10)}
        # 2 devices are covered by automations in automations.yaml
        automations = [
            {
                "alias": "Auto 0",
                "trigger": [{"platform": "state", "entity_id": "light.lamp_0"}],
                "action": [{"service": "light.turn_on", "entity_id": "light.lamp_1"}],
            },
        ]
        collector, dreg, ereg = _make_collector(
            device_ids, entity_map, automations_yaml=automations
        )
        cap = await self._run(collector, dreg, ereg)
        # 8 uncovered out of 10 -> ceil(8/5) = 2, but floor is 3
        assert cap == DEFAULT_MIN_SUGGESTIONS

    async def test_many_covered_lowers_cap(self):
        """When most devices are covered, cap stays at floor."""
        device_ids = [f"dev_{i}" for i in range(20)]
        entity_map = {f"dev_{i}": [f"light.lamp_{i}"] for i in range(20)}
        # 18 of 20 covered via automations.yaml
        automations = [
            {
                "alias": f"Auto {i}",
                "trigger": [{"platform": "state", "entity_id": f"light.lamp_{i}"}],
                "action": [{"service": "light.turn_on"}],
            }
            for i in range(18)
        ]
        collector, dreg, ereg = _make_collector(
            device_ids, entity_map, automations_yaml=automations
        )
        cap = await self._run(collector, dreg, ereg)
        # 2 uncovered -> ceil(2/5) = 1, floor is 3
        assert cap == DEFAULT_MIN_SUGGESTIONS

    async def test_empty_home(self):
        """Home with no devices still gets minimum suggestions."""
        collector, dreg, ereg = _make_collector([], {})
        cap = await self._run(collector, dreg, ereg)
        assert cap == DEFAULT_MIN_SUGGESTIONS

    async def test_device_without_entities_counts_as_uncovered(self):
        """A device with no entities in the entity registry is uncovered."""
        device_ids = [f"dev_{i}" for i in range(25)]
        # Only 5 devices have entities (and none are covered by automations)
        entity_map = {f"dev_{i}": [f"light.lamp_{i}"] for i in range(5)}
        collector, dreg, ereg = _make_collector(device_ids, entity_map)
        cap = await self._run(collector, dreg, ereg)
        # All 25 uncovered -> ceil(25/5) = 5, above floor
        assert cap == ceil(25 / DEFAULT_DEVICES_PER_SUGGESTION)


class TestParseSuggestionsPassthrough:
    """Test that _parse_suggestions returns all valid suggestions without truncation."""

    def test_all_valid_returned(self):
        """All valid suggestions are returned regardless of _max_suggestions."""
        from unittest.mock import MagicMock

        from custom_components.selora_ai.llm_client import LLMClient
        from custom_components.selora_ai.llm_client.parsers import parse_suggestions

        client = LLMClient.__new__(LLMClient)
        client._max_suggestions = 3
        client._provider = MagicMock(provider_name="Anthropic (test)")

        suggestions = [
            {
                "alias": f"Auto {i}",
                "trigger": {"platform": "state"},
                "action": {"service": "light.turn_on"},
            }
            for i in range(7)
        ]
        raw_response = json.dumps(suggestions)

        result = parse_suggestions(raw_response, client.provider_name)
        assert len(result) == 7

    def test_invalid_filtered_valid_kept(self):
        """Invalid suggestions are filtered but valid ones are not truncated."""
        from unittest.mock import MagicMock

        from custom_components.selora_ai.llm_client import LLMClient
        from custom_components.selora_ai.llm_client.parsers import parse_suggestions

        client = LLMClient.__new__(LLMClient)
        client._max_suggestions = 2
        client._provider = MagicMock(provider_name="Anthropic (test)")

        suggestions = [
            {"alias": "Good 1", "trigger": {"platform": "state"}, "action": {"service": "light.turn_on"}},
            {"bad": "no alias or trigger"},
            {"alias": "Good 2", "trigger": {"platform": "time"}, "action": {"service": "light.turn_off"}},
            {"alias": "Good 3", "trigger": {"platform": "state"}, "action": {"service": "switch.turn_on"}},
        ]
        raw_response = json.dumps(suggestions)

        result = parse_suggestions(raw_response, client.provider_name)
        # 3 valid out of 4 — all returned, no truncation
        assert len(result) == 3
