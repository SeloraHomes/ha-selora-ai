"""Tests for zones, created, changed and deleted through the helper tools.

A zone is the same kind of storage collection as an ``input_*`` helper, so it
goes through ``helper_manager`` on both surfaces: chat proposes it for the
panel to create, MCP creates it directly, and both change and delete it in place.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.registry_manager import helper_overview
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import TOOL_MAP


@pytest.fixture
async def zones_loaded(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "zone", {})
    await hass.async_block_till_done()


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    handler = mcp_dispatch._get_tool_handlers()[f"selora_{tool}"]
    return await handler(hass, arguments)


async def _work(hass: HomeAssistant) -> dict[str, Any]:
    return await _mcp(
        hass,
        "create_helper",
        domain="zone",
        name="Work",
        latitude=45.5017,
        longitude=-73.5673,
        radius=250,
        icon="mdi:briefcase",
    )


def test_the_helper_tools_take_a_place() -> None:
    properties = TOOL_MAP["create_helper"].to_anthropic()["input_schema"]["properties"]
    for name in ("latitude", "longitude", "radius", "passive"):
        assert name in properties
    assert "radius" in TOOL_MAP["update_helper"].to_anthropic()["input_schema"]["properties"]
    for tool in ("create_helper", "update_helper", "delete_helper", "list_helpers"):
        assert "zone" in TOOL_MAP[tool].description


async def test_mcp_creates_a_zone(hass: HomeAssistant, zones_loaded: None) -> None:
    result = await _work(hass)

    assert result["status"] == "created", result
    assert result["entity_id"] == "zone.work"
    attributes = hass.states.get("zone.work").attributes
    assert attributes["latitude"] == 45.5017
    assert attributes["longitude"] == -73.5673
    assert attributes["radius"] == 250
    assert attributes["passive"] is False


async def test_chat_proposes_a_zone_card(hass: HomeAssistant, zones_loaded: None) -> None:
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)

    result = await executor.execute(
        "create_helper",
        {"domain": "zone", "name": "School", "latitude": 45.0, "longitude": -73.0},
    )

    action = result["client_action"]
    assert action["label"] == "Create the School zone"
    assert action["fields"] == {
        "name": "School",
        "latitude": 45.0,
        "longitude": -73.0,
        "radius": 100,
        "passive": False,
    }
    assert hass.states.get("zone.school") is None


async def test_a_zone_needs_a_place(hass: HomeAssistant, zones_loaded: None) -> None:
    result = await _mcp(hass, "create_helper", domain="zone", name="Gym", latitude=95, longitude=0)

    assert "would refuse" in result["error"]
    assert hass.states.get("zone.gym") is None


async def test_moving_a_zone_keeps_the_rest(hass: HomeAssistant, zones_loaded: None) -> None:
    await _work(hass)

    result = await _mcp(hass, "update_helper", entity_id="zone.work", radius=400, passive=True)

    assert result["status"] == "updated", result
    attributes = hass.states.get("zone.work").attributes
    assert attributes["radius"] == 400
    assert attributes["passive"] is True
    assert attributes["latitude"] == 45.5017
    assert attributes["icon"] == "mdi:briefcase"


async def test_mcp_deletes_a_zone(hass: HomeAssistant, zones_loaded: None) -> None:
    await _work(hass)

    result = await _mcp(hass, "delete_helper", entity_id="zone.work")

    assert result["status"] == "deleted", result
    await hass.async_block_till_done()
    assert hass.states.get("zone.work") is None


async def test_the_home_zone_points_at_the_general_settings(
    hass: HomeAssistant, zones_loaded: None
) -> None:
    assert hass.states.get("zone.home") is not None

    result = await _mcp(hass, "update_helper", entity_id="zone.home", radius=50)

    assert "Settings → System → General" in result["error"]


async def test_zones_are_listed_with_the_helpers(hass: HomeAssistant, zones_loaded: None) -> None:
    await _work(hass)

    listed = await helper_overview(hass, "zone")

    assert [h["entity_id"] for h in listed["helpers"]] == ["zone.work"]


async def test_chat_deletes_a_zone_through_a_card(hass: HomeAssistant, zones_loaded: None) -> None:
    await _work(hass)
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)

    result = await executor.execute("delete_helper", {"entity_id": "zone.work"})

    assert result["delete"]["label"] == "Delete the Work zone"
    assert hass.states.get("zone.work") is not None
