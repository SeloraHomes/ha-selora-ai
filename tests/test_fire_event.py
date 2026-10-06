"""Firing an event on Home Assistant's bus over MCP.

Event-triggered automations, Node-RED flows and "test my automation by
simulating its trigger" all need an event fired, and there was no tool. An
event can start anything an automation does, so it asks first and names the
automations listening; Home Assistant's own events are refused, since firing
one reports something that did not happen.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import Event, HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import TOOL_FIRE_EVENT


@pytest.fixture
async def doorbell(hass: HomeAssistant) -> HomeAssistant:
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": [
                {
                    "id": "chime",
                    "alias": "Chime",
                    "triggers": [{"trigger": "event", "event_type": "doorbell_pressed"}],
                    "actions": [{"event": "chimed"}],
                },
                {
                    "id": "other",
                    "alias": "Other",
                    "triggers": [{"trigger": "event", "event_type": "something_else"}],
                    "actions": [],
                },
            ]
        },
    )
    await hass.async_block_till_done()
    return hass


async def _fire(hass: HomeAssistant, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[TOOL_FIRE_EVENT](hass, arguments)


async def test_it_asks_first_naming_what_listens(doorbell: HomeAssistant) -> None:
    seen: list[Event] = []
    doorbell.bus.async_listen("doorbell_pressed", seen.append)

    asked = await _fire(doorbell, event_type="doorbell_pressed")
    await doorbell.async_block_till_done()

    assert asked["requires_confirmation"] is True
    assert asked["automations_triggered"] == [{"entity_id": "automation.chime", "name": "Chime"}]
    assert seen == []


async def test_a_confirmed_event_fires_with_its_data(doorbell: HomeAssistant) -> None:
    seen: list[Event] = []
    chimed: list[Event] = []
    doorbell.bus.async_listen("doorbell_pressed", seen.append)
    doorbell.bus.async_listen("chimed", chimed.append)

    result = await _fire(
        doorbell, event_type="doorbell_pressed", data={"button": "front"}, confirmed=True
    )
    await doorbell.async_block_till_done()

    assert result["status"] == "fired"
    assert seen[0].data == {"button": "front"}
    # The automation it named really ran.
    assert len(chimed) == 1


@pytest.mark.parametrize(
    "event_type",
    [
        "state_changed",
        "homeassistant_stop",
        "call_service",
        "entity_registry_updated",
        "automation_triggered",
    ],
)
async def test_home_assistants_own_events_are_refused(
    doorbell: HomeAssistant, event_type: str
) -> None:
    seen: list[Event] = []
    doorbell.bus.async_listen(event_type, seen.append)

    result = await _fire(doorbell, event_type=event_type, confirmed=True)
    await doorbell.async_block_till_done()

    assert "Home Assistant's own events" in result["error"]
    assert seen == []


@pytest.mark.parametrize(
    ("arguments", "says"),
    [
        ({"event_type": "has space"}, "no spaces"),
        ({"event_type": "x" * 65}, "1 to 64"),
        ({"event_type": "ok", "data": "text"}, "data is an object"),
    ],
)
async def test_a_malformed_event_is_refused(
    hass: HomeAssistant, arguments: dict[str, Any], says: str
) -> None:
    result = await _fire(hass, confirmed=True, **arguments)

    assert says in result["error"]


async def test_a_device_event_can_be_simulated(hass: HomeAssistant) -> None:
    """What testing a button automation needs."""
    result = await _fire(hass, event_type="zha_event", data={"command": "on"}, confirmed=True)

    assert result["status"] == "fired"


def test_firing_needs_admin() -> None:
    assert TOOL_FIRE_EVENT in mcp_access._ADMIN_TOOLS
