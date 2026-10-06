"""Changing a group's options after it exists: hiding its members, and how a
sensor group combines them.

Both were set at creation and fixed thereafter, so "stop hiding the lamps
behind the group" or "make it the max, not the average" meant deleting the
group — breaking every automation that targets it — and building a new one.
Runs Home Assistant's real group flow.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai.group_manager import SENSOR_STATISTICS
from custom_components.selora_ai.mcp_server.definitions import _TOOL_DEFINITIONS
from custom_components.selora_ai.mcp_server.groups import _tool_create_group, _tool_update_group
from custom_components.selora_ai.mcp_server.names import TOOL_UPDATE_GROUP
from custom_components.selora_ai.tool_registry import TOOL_MAP


@pytest.fixture
async def home(hass: HomeAssistant) -> HomeAssistant:
    assert await async_setup_component(hass, "group", {})
    registry = er.async_get(hass)
    for unique_id in ("lamp", "ceiling"):
        entry = registry.async_get_or_create(
            "light", "demo", unique_id, suggested_object_id=unique_id
        )
        hass.states.async_set(entry.entity_id, "off")
    for unique_id, value in (("temp_a", "20.0"), ("temp_b", "24.0")):
        entry = registry.async_get_or_create(
            "sensor", "demo", unique_id, suggested_object_id=unique_id
        )
        hass.states.async_set(entry.entity_id, value, {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    return hass


async def _group(
    hass: HomeAssistant, name: str, entities: list[str], **extra: Any
) -> dict[str, Any]:
    result = await _tool_create_group(hass, {"name": name, "entities": entities, **extra})
    await hass.async_block_till_done()
    assert result.get("status") == "created", result
    return result


def _hidden(hass: HomeAssistant, entity_id: str) -> Any:
    return er.async_get(hass).async_get(entity_id).hidden_by


async def test_members_are_hidden_then_shown_again(home: HomeAssistant) -> None:
    group = await _group(home, "Lounge", ["light.lamp", "light.ceiling"])

    hidden = await _tool_update_group(home, {"entity_id": group["entity_id"], "hide_members": True})
    assert hidden["hide_members"] is True
    assert _hidden(home, "light.lamp") is er.RegistryEntryHider.INTEGRATION

    shown = await _tool_update_group(home, {"entity_id": group["entity_id"], "hide_members": False})
    assert shown["hide_members"] is False
    assert _hidden(home, "light.lamp") is None


async def test_a_member_another_hidden_group_claims_stays_hidden(home: HomeAssistant) -> None:
    lounge = await _group(home, "Lounge", ["light.lamp", "light.ceiling"], hide_members=True)
    await _group(home, "Lamps", ["light.lamp"], hide_members=True)

    await _tool_update_group(home, {"entity_id": lounge["entity_id"], "hide_members": False})

    assert _hidden(home, "light.lamp") is er.RegistryEntryHider.INTEGRATION
    assert _hidden(home, "light.ceiling") is None


async def test_a_user_hidden_member_is_left_alone(home: HomeAssistant) -> None:
    group = await _group(home, "Lounge", ["light.lamp", "light.ceiling"], hide_members=True)
    er.async_get(home).async_update_entity("light.ceiling", hidden_by=er.RegistryEntryHider.USER)

    await _tool_update_group(home, {"entity_id": group["entity_id"], "hide_members": False})

    assert _hidden(home, "light.ceiling") is er.RegistryEntryHider.USER


async def test_a_sensor_group_changes_its_statistic(home: HomeAssistant) -> None:
    group = await _group(home, "Temperature", ["sensor.temp_a", "sensor.temp_b"])
    assert home.states.get(group["entity_id"]).state == "22.0"

    result = await _tool_update_group(home, {"entity_id": group["entity_id"], "statistic": "max"})
    await home.async_block_till_done()

    assert result["statistic"] == "max"
    assert home.states.get(group["entity_id"]).state == "24.0"


@pytest.mark.parametrize(
    ("arguments", "says"),
    [
        ({"statistic": "max"}, "Only a sensor group"),
        ({"hide_members": False}, "Nothing to change"),
    ],
)
async def test_what_a_group_cannot_take_changes_nothing(
    home: HomeAssistant, arguments: dict[str, Any], says: str
) -> None:
    group = await _group(home, "Lounge", ["light.lamp", "light.ceiling"])

    result = await _tool_update_group(home, {"entity_id": group["entity_id"], **arguments})

    assert says in result["error"]


async def test_an_unknown_statistic_is_refused(home: HomeAssistant) -> None:
    group = await _group(home, "Temperature", ["sensor.temp_a", "sensor.temp_b"])

    result = await _tool_update_group(home, {"entity_id": group["entity_id"], "statistic": "avg"})

    assert "statistic must be one of" in result["error"]


def test_both_schemas_offer_the_same_statistics() -> None:
    (mcp,) = [t for t in _TOOL_DEFINITIONS if t.name == TOOL_UPDATE_GROUP]
    chat = TOOL_MAP["update_group"].to_anthropic()["input_schema"]["properties"]

    assert mcp.inputSchema["properties"]["statistic"]["enum"] == list(SENSOR_STATISTICS)
    assert chat["statistic"]["enum"] == list(SENSOR_STATISTICS)
    assert "hide_members" in mcp.inputSchema["properties"] and "hide_members" in chat


async def test_turning_hiding_off_while_removing_shows_the_removed_ones_too(
    home: HomeAssistant,
) -> None:
    group = await _group(home, "Lounge", ["light.lamp", "light.ceiling"], hide_members=True)

    await _tool_update_group(
        home,
        {
            "entity_id": group["entity_id"],
            "hide_members": False,
            "remove_entities": ["light.ceiling"],
        },
    )

    assert _hidden(home, "light.lamp") is None
    assert _hidden(home, "light.ceiling") is None
