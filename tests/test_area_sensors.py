"""The sensors that give an area its temperature and humidity.

Home Assistant's area cards show a room's temperature and humidity from the
sensors chosen on the area, and the area tools could not choose them — "show
the bedroom thermometer on the bedroom card" ended at "do it in Settings".
Home Assistant's own check decides what is a temperature or humidity sensor.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
import pytest

from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import TOOL_LIST_AREAS, TOOL_UPDATE_AREA


@pytest.fixture
def bedroom(hass: HomeAssistant) -> ar.AreaEntry:
    hass.states.async_set("sensor.bedroom_temp", "20.5", {"device_class": "temperature"})
    hass.states.async_set("sensor.bedroom_rh", "48", {"device_class": "humidity"})
    hass.states.async_set("sensor.power", "120", {"device_class": "power"})
    return ar.async_get(hass).async_create("Bedroom")


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[tool](hass, arguments)


async def test_an_area_gets_its_sensors_and_lists_them(
    hass: HomeAssistant, bedroom: ar.AreaEntry
) -> None:
    result = await _mcp(
        hass,
        TOOL_UPDATE_AREA,
        area="Bedroom",
        temperature_sensor="sensor.bedroom_temp",
        humidity_sensor="sensor.bedroom_rh",
    )

    assert result["changed"] == ["humidity_entity_id", "temperature_entity_id"]
    area = ar.async_get(hass).async_get_area(bedroom.id)
    assert area.temperature_entity_id == "sensor.bedroom_temp"
    listed = (await _mcp(hass, TOOL_LIST_AREAS))["areas"][0]
    assert listed["temperature_sensor"] == "sensor.bedroom_temp"
    assert listed["humidity_sensor"] == "sensor.bedroom_rh"


async def test_a_sensor_of_the_wrong_kind_is_refused_by_home_assistant(
    hass: HomeAssistant, bedroom: ar.AreaEntry
) -> None:
    result = await _mcp(hass, TOOL_UPDATE_AREA, area="Bedroom", temperature_sensor="sensor.power")

    assert "not a temperature sensor" in result["error"]
    assert ar.async_get(hass).async_get_area(bedroom.id).temperature_entity_id is None


async def test_a_refusal_leaves_no_floor_behind(hass: HomeAssistant, bedroom: ar.AreaEntry) -> None:
    from homeassistant.helpers import floor_registry as fr

    result = await _mcp(
        hass, TOOL_UPDATE_AREA, area="Bedroom", floor="Attic", temperature_sensor="sensor.power"
    )

    assert "not a temperature sensor" in result["error"]
    assert list(fr.async_get(hass).async_list_floors()) == []


async def test_a_sensor_is_cleared(hass: HomeAssistant, bedroom: ar.AreaEntry) -> None:
    ar.async_get(hass).async_update(bedroom.id, humidity_entity_id="sensor.bedroom_rh")

    result = await _mcp(hass, TOOL_UPDATE_AREA, area="Bedroom", clear=["humidity_sensor"])

    assert result["changed"] == ["humidity_entity_id"]
    assert ar.async_get(hass).async_get_area(bedroom.id).humidity_entity_id is None


async def test_a_core_without_area_sensors_says_so(
    hass: HomeAssistant, bedroom: ar.AreaEntry
) -> None:
    """HA 2025.1's areas have no such fields; the tool must not pretend."""
    from unittest.mock import patch

    from custom_components.selora_ai import registry_manager

    old_entry = type("OldAreaEntry", (), {"id": bedroom.id, "name": "Bedroom", "floor_id": None})()
    with patch.object(registry_manager, "resolve_area", return_value=(old_entry, None)):
        result = await _mcp(
            hass, TOOL_UPDATE_AREA, area="Bedroom", temperature_sensor="sensor.bedroom_temp"
        )

    assert "no area temperature or humidity sensors" in result["error"]
