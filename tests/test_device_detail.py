"""Tests for the selora_ai/get_device_detail websocket handler."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.selora_ai import (
    _automation_references_device,
    _handle_websocket_get_device_detail,
)

# Access the original coroutine behind the @async_response decorator
_device_detail_handler = _handle_websocket_get_device_detail.__wrapped__


# ── _automation_references_device ───────────────────────────────────


class TestAutomationReferencesEntities:
    """Tests for the entity reference walker."""

    def test_exact_match_in_action_target(self):
        auto = {
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [
                {"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}
            ],
        }
        assert _automation_references_device(auto, {"light.kitchen"})

    def test_no_match(self):
        auto = {
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [
                {"action": "light.turn_on", "target": {"entity_id": "light.bedroom"}}
            ],
        }
        assert not _automation_references_device(auto, {"light.kitchen"})

    def test_no_false_positive_on_substring(self):
        """'light.living' should NOT match 'light.living_room'."""
        auto = {
            "action": [
                {
                    "action": "light.turn_on",
                    "target": {"entity_id": "light.living_room"},
                }
            ],
        }
        assert not _automation_references_device(auto, {"light.living"})

    def test_match_in_trigger_entity_id(self):
        auto = {
            "trigger": [
                {"platform": "state", "entity_id": "binary_sensor.motion"}
            ],
            "action": [],
        }
        assert _automation_references_device(auto, {"binary_sensor.motion"})

    def test_match_in_condition(self):
        auto = {
            "trigger": [],
            "condition": [
                {"condition": "state", "entity_id": "sensor.temp", "state": "on"}
            ],
            "action": [],
        }
        assert _automation_references_device(auto, {"sensor.temp"})

    def test_entity_list_in_target(self):
        auto = {
            "action": [
                {
                    "action": "light.turn_on",
                    "target": {
                        "entity_id": ["light.a", "light.b", "light.c"]
                    },
                }
            ],
        }
        assert _automation_references_device(auto, {"light.b"})
        assert not _automation_references_device(auto, {"light.d"})

    def test_empty_automation(self):
        assert not _automation_references_device({}, {"light.kitchen"})

    def test_non_string_values_ignored(self):
        auto = {"trigger": [{"platform": "time", "at": 800}]}
        assert not _automation_references_device(auto, {"800"})

    def test_device_id_match(self):
        """Device triggers store device_id, not entity_id."""
        auto = {
            "trigger": [
                {"platform": "device", "device_id": "abc123", "type": "turned_on"}
            ],
            "action": [],
        }
        assert _automation_references_device(auto, {"abc123"})
        assert not _automation_references_device(auto, {"xyz789"})

    def test_csv_entity_id_string(self):
        """Comma-separated entity_id strings should match."""
        auto = {
            "action": [
                {
                    "action": "light.turn_on",
                    "target": {"entity_id": "light.kitchen, light.dining"},
                }
            ],
        }
        assert _automation_references_device(auto, {"light.kitchen"})
        assert _automation_references_device(auto, {"light.dining"})
        assert not _automation_references_device(auto, {"light.din"})

    def test_template_match(self):
        """Entity IDs inside Jinja templates should match."""
        auto = {
            "condition": [
                {
                    "condition": "template",
                    "value_template": "{{ is_state('light.kitchen', 'on') }}",
                }
            ],
        }
        assert _automation_references_device(auto, {"light.kitchen"})
        assert not _automation_references_device(auto, {"light.kitch"})


# ── Websocket handler ─────────────────────────────────────────────────


@pytest.fixture
def mock_connection():
    """Create a mock websocket connection with admin permissions."""
    conn = MagicMock()
    conn.user.is_admin = True
    return conn


@pytest.fixture
def device_data():
    """Sample device data returned by _tool_get_device."""
    return {
        "device_id": "abc123",
        "name": "Kitchen Light",
        "area": "Kitchen",
        "manufacturer": "Philips",
        "model": "Hue",
        "entities": [
            {"entity_id": "light.kitchen", "domain": "light", "state": "on", "name": "Kitchen Light"},
        ],
    }


@pytest.mark.asyncio
async def test_device_detail_success(hass: HomeAssistant, mock_connection, device_data):
    """Test successful device detail retrieval."""
    msg = {"id": 1, "type": "selora_ai/get_device_detail", "device_id": "abc123"}

    with (
        patch(
            "custom_components.selora_ai.mcp_server._tool_get_device",
            new_callable=AsyncMock,
            return_value=device_data,
        ),
        patch(
            "custom_components.selora_ai.automation_utils._read_automations_yaml",
            return_value=[],
        ),
        patch(
            "custom_components.selora_ai._get_pattern_store",
            return_value=None,
        ),
    ):
        await _device_detail_handler(hass, mock_connection, msg)

    mock_connection.send_result.assert_called_once()
    result = mock_connection.send_result.call_args[0][1]
    assert result["name"] == "Kitchen Light"
    assert result["state_history"] == []
    assert result["linked_automations"] == []
    assert result["related_patterns"] == []


@pytest.mark.asyncio
async def test_device_detail_not_found(hass: HomeAssistant, mock_connection):
    """Test device not found returns error."""
    msg = {"id": 1, "type": "selora_ai/get_device_detail", "device_id": "bad_id"}

    with patch(
        "custom_components.selora_ai.mcp_server._tool_get_device",
        new_callable=AsyncMock,
        return_value={"error": "Device bad_id not found"},
    ):
        await _device_detail_handler(hass, mock_connection, msg)

    mock_connection.send_error.assert_called_once_with(1, "not_found", "Device bad_id not found")


@pytest.mark.asyncio
async def test_device_detail_linked_automations(
    hass: HomeAssistant, mock_connection, device_data
):
    """Test that automations referencing device entities are found."""
    automations = [
        {
            "id": "auto1",
            "alias": "Kitchen on at sunset",
            "description": "Turns on kitchen light",
            "trigger": [{"platform": "sun", "event": "sunset"}],
            "action": [
                {"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}
            ],
        },
        {
            "id": "auto2",
            "alias": "Bedroom routine",
            "description": "Unrelated",
            "trigger": [{"platform": "time", "at": "22:00"}],
            "action": [
                {"action": "light.turn_off", "target": {"entity_id": "light.bedroom"}}
            ],
        },
    ]
    msg = {"id": 1, "type": "selora_ai/get_device_detail", "device_id": "abc123"}

    with (
        patch(
            "custom_components.selora_ai.mcp_server._tool_get_device",
            new_callable=AsyncMock,
            return_value=device_data,
        ),
        patch(
            "custom_components.selora_ai.automation_utils._read_automations_yaml",
            return_value=automations,
        ),
        patch(
            "custom_components.selora_ai._get_pattern_store",
            return_value=None,
        ),
    ):
        await _device_detail_handler(hass, mock_connection, msg)

    result = mock_connection.send_result.call_args[0][1]
    assert len(result["linked_automations"]) == 1
    assert result["linked_automations"][0]["alias"] == "Kitchen on at sunset"


@pytest.mark.asyncio
async def test_device_detail_related_patterns(
    hass: HomeAssistant, mock_connection, device_data
):
    """Test that patterns referencing device entities are found."""
    mock_store = AsyncMock()
    mock_store.get_patterns = AsyncMock(
        return_value=[
            {
                "pattern_id": "pat1",
                "type": "time_based",
                "description": "Kitchen on at 6pm",
                "entity_ids": ["light.kitchen"],
                "confidence": 0.85,
                "status": "active",
            },
            {
                "pattern_id": "pat2",
                "type": "correlation",
                "description": "Unrelated pattern",
                "entity_ids": ["sensor.outdoor_temp"],
                "confidence": 0.7,
                "status": "active",
            },
        ]
    )
    msg = {"id": 1, "type": "selora_ai/get_device_detail", "device_id": "abc123"}

    with (
        patch(
            "custom_components.selora_ai.mcp_server._tool_get_device",
            new_callable=AsyncMock,
            return_value=device_data,
        ),
        patch(
            "custom_components.selora_ai.automation_utils._read_automations_yaml",
            return_value=[],
        ),
        patch(
            "custom_components.selora_ai._get_pattern_store",
            return_value=mock_store,
        ),
    ):
        await _device_detail_handler(hass, mock_connection, msg)

    result = mock_connection.send_result.call_args[0][1]
    assert len(result["related_patterns"]) == 1
    assert result["related_patterns"][0]["pattern_id"] == "pat1"
