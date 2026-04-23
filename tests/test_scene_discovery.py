"""Tests for scene_discovery and area-enriched entity collection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.selora_ai.scene_discovery import get_area_names


@pytest.fixture(autouse=True)
def _enable_custom_component(enable_custom_integrations):
    """Auto-enable custom integrations so our domain is discoverable."""


# ── get_area_names ───────────────────────────────────────────────────


class TestGetAreaNames:
    async def test_returns_area_names(self, hass) -> None:
        mock_area1 = MagicMock()
        mock_area1.name = "Living Room"
        mock_area2 = MagicMock()
        mock_area2.name = "Bedroom"
        mock_area_reg = MagicMock()
        mock_area_reg.async_list_areas.return_value = [mock_area1, mock_area2]

        with patch(
            "homeassistant.helpers.area_registry.async_get",
            return_value=mock_area_reg,
        ):
            names = await get_area_names(hass)

        assert names == ["Living Room", "Bedroom"]


# ── Area enrichment in _collect_entity_states ────────────────────────


class TestEntityAreaEnrichment:
    """Verify that _collect_entity_states annotates entities with area_name."""

    def test_entity_with_direct_area(self, hass) -> None:
        """Entity directly assigned to an area gets area_name in snapshot."""
        from custom_components.selora_ai import _collect_entity_states

        hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen Light"})

        mock_area = MagicMock()
        mock_area.id = "area_k"
        mock_area.name = "Kitchen"
        mock_area_reg = MagicMock()
        mock_area_reg.async_list_areas.return_value = [mock_area]

        entity_entry = MagicMock()
        entity_entry.area_id = "area_k"
        entity_entry.device_id = None
        entity_entry.disabled = False
        mock_entity_reg = MagicMock()
        mock_entity_reg.async_get.return_value = entity_entry

        mock_device_reg = MagicMock()

        with (
            patch(
                "homeassistant.helpers.area_registry.async_get",
                return_value=mock_area_reg,
            ),
            patch(
                "homeassistant.helpers.entity_registry.async_get",
                return_value=mock_entity_reg,
            ),
            patch(
                "homeassistant.helpers.device_registry.async_get",
                return_value=mock_device_reg,
            ),
        ):
            states = _collect_entity_states(hass)

        light = next(s for s in states if s["entity_id"] == "light.kitchen")
        assert light["area_name"] == "Kitchen"

    def test_entity_inherits_area_from_device(self, hass) -> None:
        """Entity with no direct area inherits from its device."""
        from custom_components.selora_ai import _collect_entity_states

        hass.states.async_set("switch.fan", "off", {"friendly_name": "Fan Switch"})

        mock_area = MagicMock()
        mock_area.id = "area_br"
        mock_area.name = "Bedroom"
        mock_area_reg = MagicMock()
        mock_area_reg.async_list_areas.return_value = [mock_area]

        entity_entry = MagicMock()
        entity_entry.area_id = None
        entity_entry.device_id = "device_1"
        entity_entry.disabled = False
        mock_entity_reg = MagicMock()
        mock_entity_reg.async_get.return_value = entity_entry

        device_entry = MagicMock()
        device_entry.area_id = "area_br"
        mock_device_reg = MagicMock()
        mock_device_reg.async_get.return_value = device_entry

        with (
            patch(
                "homeassistant.helpers.area_registry.async_get",
                return_value=mock_area_reg,
            ),
            patch(
                "homeassistant.helpers.entity_registry.async_get",
                return_value=mock_entity_reg,
            ),
            patch(
                "homeassistant.helpers.device_registry.async_get",
                return_value=mock_device_reg,
            ),
        ):
            states = _collect_entity_states(hass)

        fan = next(s for s in states if s["entity_id"] == "switch.fan")
        assert fan["area_name"] == "Bedroom"

    def test_entity_without_area(self, hass) -> None:
        """Entity with no area assignment omits area_name from snapshot."""
        from custom_components.selora_ai import _collect_entity_states

        hass.states.async_set("light.hallway", "on", {"friendly_name": "Hallway"})

        mock_area_reg = MagicMock()
        mock_area_reg.async_list_areas.return_value = []

        entity_entry = MagicMock()
        entity_entry.area_id = None
        entity_entry.device_id = None
        entity_entry.disabled = False
        mock_entity_reg = MagicMock()
        mock_entity_reg.async_get.return_value = entity_entry

        mock_device_reg = MagicMock()

        with (
            patch(
                "homeassistant.helpers.area_registry.async_get",
                return_value=mock_area_reg,
            ),
            patch(
                "homeassistant.helpers.entity_registry.async_get",
                return_value=mock_entity_reg,
            ),
            patch(
                "homeassistant.helpers.device_registry.async_get",
                return_value=mock_device_reg,
            ),
        ):
            states = _collect_entity_states(hass)

        hallway = next(s for s in states if s["entity_id"] == "light.hallway")
        assert "area_name" not in hallway


# ── _format_entity_line includes area ────────────────────────────────


class TestFormatEntityLineArea:
    """Verify that _format_entity_line includes area when present."""

    def test_area_included_in_line(self) -> None:
        from custom_components.selora_ai.llm_client import _format_entity_line

        entity = {
            "entity_id": "light.kitchen",
            "state": "on",
            "area_name": "Kitchen",
            "attributes": {"friendly_name": "Kitchen Light"},
        }
        line = _format_entity_line(entity)
        assert 'area="Kitchen"' in line

    def test_area_omitted_when_empty(self) -> None:
        from custom_components.selora_ai.llm_client import _format_entity_line

        entity = {
            "entity_id": "light.hallway",
            "state": "on",
            "attributes": {"friendly_name": "Hallway Light"},
        }
        line = _format_entity_line(entity)
        assert "area=" not in line

    def test_area_placed_after_friendly_name(self) -> None:
        from custom_components.selora_ai.llm_client import _format_entity_line

        entity = {
            "entity_id": "light.kitchen",
            "state": "on",
            "area_name": "Kitchen",
            "attributes": {"friendly_name": "Kitchen Light", "brightness": 200},
        }
        line = _format_entity_line(entity)
        friendly_pos = line.index("friendly_name=")
        area_pos = line.index("area=")
        brightness_pos = line.index("brightness=")
        assert friendly_pos < area_pos < brightness_pos
