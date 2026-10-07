"""Tests for people, created, changed and deleted through the helper tools.

A person is the same kind of storage collection as a zone, so it goes through
``helper_manager`` on both surfaces. What is particular to a person: the
device trackers it names must exist, and its login (``user_id``) is never set
from here.
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
async def people(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "person", {})
    await hass.async_block_till_done()
    hass.states.async_set("device_tracker.alex_phone", "home")
    hass.states.async_set("device_tracker.alex_watch", "home")


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    handler = mcp_dispatch._get_tool_handlers()[f"selora_{tool}"]
    return await handler(hass, arguments)


async def _alex(hass: HomeAssistant) -> dict[str, Any]:
    return await _mcp(
        hass,
        "create_helper",
        domain="person",
        name="Alex",
        device_trackers=["device_tracker.alex_phone"],
    )


def test_the_helper_tools_take_a_person() -> None:
    for tool in ("create_helper", "update_helper"):
        assert "device_trackers" in TOOL_MAP[tool].to_anthropic()["input_schema"]["properties"]
    for tool in ("create_helper", "update_helper", "delete_helper", "list_helpers"):
        assert "person" in TOOL_MAP[tool].description


async def test_mcp_creates_a_person(hass: HomeAssistant, people: None) -> None:
    result = await _alex(hass)

    assert result["status"] == "created", result
    assert result["entity_id"] == "person.alex"
    state = hass.states.get("person.alex")
    assert state.attributes["device_trackers"] == ["device_tracker.alex_phone"]
    assert state.state == "home"


async def test_chat_proposes_a_person_card(hass: HomeAssistant, people: None) -> None:
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)

    result = await executor.execute(
        "create_helper",
        {"domain": "person", "name": "Sam", "device_trackers": ["device_tracker.alex_watch"]},
    )

    action = result["client_action"]
    assert action["label"] == "Create the Sam person"
    assert action["fields"] == {"name": "Sam", "device_trackers": ["device_tracker.alex_watch"]}
    assert hass.states.get("person.sam") is None


async def test_a_tracker_that_does_not_exist_is_refused(hass: HomeAssistant, people: None) -> None:
    """The schema checks only the domain: a typo is stored, and the person
    never shows as home."""
    result = await _mcp(
        hass,
        "create_helper",
        domain="person",
        name="Alex",
        device_trackers=["device_tracker.alex_phnoe"],
    )

    assert "device_tracker.alex_phnoe" in result["error"]
    assert hass.states.get("person.alex") is None


async def test_a_login_is_never_linked_from_here(hass: HomeAssistant, people: None) -> None:
    user = await hass.auth.async_create_user("Alex")

    created = await _mcp(hass, "create_helper", domain="person", name="Alex", user_id=user.id)
    changed = await _mcp(hass, "update_helper", entity_id="person.alex", clear=["user_id"])

    assert hass.states.get("person.alex").attributes.get("user_id") is None
    assert created["status"] == "created"
    assert "Settings → People" in changed["error"]


async def test_renaming_keeps_the_trackers(hass: HomeAssistant, people: None) -> None:
    """Core's update schema defaults device_trackers to []; a rename passed on
    alone would untrack the person."""
    await _alex(hass)

    result = await _mcp(hass, "update_helper", entity_id="person.alex", name="Alexandra")

    assert result["status"] == "updated", result
    state = hass.states.get("person.alex")
    assert state.name == "Alexandra"
    assert state.attributes["device_trackers"] == ["device_tracker.alex_phone"]


async def test_trackers_are_replaced_and_checked(hass: HomeAssistant, people: None) -> None:
    await _alex(hass)

    swapped = await _mcp(
        hass,
        "update_helper",
        entity_id="person.alex",
        device_trackers=["device_tracker.alex_phone", "device_tracker.alex_watch"],
    )
    typo = await _mcp(
        hass, "update_helper", entity_id="person.alex", device_trackers=["device_tracker.nope"]
    )

    assert swapped["status"] == "updated", swapped
    assert "device_tracker.nope" in typo["error"]
    assert hass.states.get("person.alex").attributes["device_trackers"] == [
        "device_tracker.alex_phone",
        "device_tracker.alex_watch",
    ]


async def test_a_person_from_yaml_is_pointed_at_the_file(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "person", {"person": [{"id": "pat", "name": "Pat"}]})
    await hass.async_block_till_done()

    result = await _mcp(hass, "update_helper", entity_id="person.pat", name="Patricia")

    assert "configuration.yaml" in result["error"]


async def test_people_are_listed_with_the_helpers(hass: HomeAssistant, people: None) -> None:
    await _alex(hass)

    listed = await helper_overview(hass, "person")

    assert [h["entity_id"] for h in listed["helpers"]] == ["person.alex"]


async def test_chat_deletes_a_person_through_a_card(hass: HomeAssistant, people: None) -> None:
    await _alex(hass)
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)

    result = await executor.execute("delete_helper", {"entity_id": "person.alex"})

    assert result["delete"]["label"] == "Delete the Alex person"
    assert hass.states.get("person.alex") is not None


async def test_mcp_deletes_a_person(hass: HomeAssistant, people: None) -> None:
    await _alex(hass)

    result = await _mcp(hass, "delete_helper", entity_id="person.alex")

    assert result["status"] == "deleted", result
    await hass.async_block_till_done()
    assert hass.states.get("person.alex") is None
