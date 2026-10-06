"""Putting an entity, device or area setting back to its default.

The update tools could set a name, icon, area or floor but never remove one,
since a blank value reads as "not set": a renamed device could not get its
vendor name back, an entity moved to another room could not follow its device
again, an area could not leave its floor. ``clear`` names what to remove.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import (
    TOOL_UPDATE_AREA,
    TOOL_UPDATE_DEVICE,
    TOOL_UPDATE_ENTITY,
)
from custom_components.selora_ai.tool_executor import ToolExecutor


@pytest.fixture
def home(hass: HomeAssistant) -> dict[str, Any]:
    entry = MockConfigEntry(domain="hue")
    entry.add_to_hass(hass)
    kitchen = ar.async_get(hass).async_create("Kitchen")
    lounge = ar.async_get(hass).async_create("Lounge")
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("hue", "bulb-1")},
        name="Hue bulb",
        suggested_area="Kitchen",
    )
    entity = er.async_get(hass).async_get_or_create(
        "light",
        "hue",
        "bulb-1",
        device_id=device.id,
        original_name="Bulb",
        original_icon="mdi:lightbulb",
    )
    return {"device": device, "entity": entity, "kitchen": kitchen, "lounge": lounge}


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[tool](hass, arguments)


async def test_a_device_gets_its_vendor_name_and_no_area_back(
    hass: HomeAssistant, home: dict[str, Any]
) -> None:
    device = home["device"]
    await _mcp(hass, TOOL_UPDATE_DEVICE, device=device.id, new_name="Pendant")

    result = await _mcp(hass, TOOL_UPDATE_DEVICE, device=device.id, clear=["name", "area"])

    assert result["status"] == "updated", result
    assert result["name"] == "Hue bulb"
    assert result["entities_without_area"] == 1
    updated = dr.async_get(hass).async_get(device.id)
    assert updated.name_by_user is None
    assert updated.area_id is None


async def test_an_entity_follows_its_device_again(
    hass: HomeAssistant, home: dict[str, Any]
) -> None:
    entity_id = home["entity"].entity_id
    er.async_get(hass).async_update_entity(
        entity_id, name="Island", icon="mdi:lamp", area_id=home["lounge"].id
    )

    result = await _mcp(
        hass, TOOL_UPDATE_ENTITY, entity_id=entity_id, clear=["name", "icon", "area"]
    )

    assert result["changed"] == ["area_id", "icon", "name"]
    entry = er.async_get(hass).async_get(entity_id)
    assert (entry.name, entry.icon, entry.area_id) == (None, None, None)
    # Its room is its device's again.
    dr.async_get(hass).async_update_device(home["device"].id, area_id=home["kitchen"].id)
    hass.states.async_set(entity_id, "on")
    state = await _mcp(hass, "selora_get_entity_state", entity_id=entity_id)
    assert state["area"] == "Kitchen"


async def test_an_area_leaves_its_floor(hass: HomeAssistant, home: dict[str, Any]) -> None:
    floor = fr.async_get(hass).async_create("Ground")
    ar.async_get(hass).async_update(home["kitchen"].id, floor_id=floor.floor_id, icon="mdi:pot")

    result = await _mcp(hass, TOOL_UPDATE_AREA, area="Kitchen", clear=["floor", "icon"])

    assert result["changed"] == ["floor_id", "icon"]
    kitchen = ar.async_get(hass).async_get_area(home["kitchen"].id)
    assert (kitchen.floor_id, kitchen.icon) == (None, None)


async def test_clearing_what_is_not_set_changes_nothing(
    hass: HomeAssistant, home: dict[str, Any]
) -> None:
    result = await _mcp(hass, TOOL_UPDATE_AREA, area="Lounge", clear=["floor"])

    assert result["status"] == "unchanged"


@pytest.mark.parametrize(
    ("tool", "arguments", "says"),
    [
        (TOOL_UPDATE_DEVICE, {"new_name": "Pendant", "clear": ["name"]}, "set and cleared"),
        (TOOL_UPDATE_DEVICE, {"clear": ["icon"]}, "clear takes name, area"),
        (TOOL_UPDATE_ENTITY, {"icon": "mdi:x", "clear": ["icon"]}, "set and cleared"),
        (TOOL_UPDATE_AREA, {"floor": "Ground", "clear": ["floor"]}, "set and cleared"),
    ],
)
async def test_a_contradictory_or_unknown_clear_changes_nothing(
    hass: HomeAssistant, home: dict[str, Any], tool: str, arguments: dict[str, Any], says: str
) -> None:
    target = {
        TOOL_UPDATE_DEVICE: {"device": home["device"].id},
        TOOL_UPDATE_ENTITY: {"entity_id": home["entity"].entity_id},
        TOOL_UPDATE_AREA: {"area": "Kitchen"},
    }[tool]

    result = await _mcp(hass, tool, **target, **arguments)

    assert says in result["error"]
    assert dr.async_get(hass).async_get(home["device"].id).name_by_user is None
    assert not fr.async_get(hass).async_list_floors()


async def test_chat_clears_through_the_same_reading(
    hass: HomeAssistant, home: dict[str, Any]
) -> None:
    device = home["device"]
    dr.async_get(hass).async_update_device(device.id, name_by_user="Pendant")

    result = await ToolExecutor(hass, MagicMock(), is_admin=True).execute(
        "update_device", {"device": device.id, "clear": ["name"]}
    )

    assert result["status"] == "updated", result
    assert dr.async_get(hass).async_get(device.id).name_by_user is None
