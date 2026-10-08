"""Tests for changing and deleting UI-created helpers, in chat and over MCP.

Home Assistant's helper update REPLACES the stored item, so most of what can go
wrong here is quiet: a change that wipes the settings it did not name.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai.helper_manager import CREATABLE_HELPER_DOMAINS
from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import definitions as mcp_definitions
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import (
    COMMAND_TOOL_NAMES,
    CONFIG_TOOL_NAMES,
    TOOL_MAP,
)


@pytest.fixture
async def helpers_loaded(hass: HomeAssistant) -> None:
    for domain in CREATABLE_HELPER_DOMAINS:
        assert await async_setup_component(hass, domain, {})
    await hass.async_block_till_done()


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    handler = mcp_dispatch._get_tool_handlers()[f"selora_{tool}"]
    return await handler(hass, arguments)


async def _house_mode(hass: HomeAssistant) -> str:
    created = await _mcp(
        hass,
        "create_helper",
        domain="input_select",
        name="House mode",
        icon="mdi:home",
        options=["Home", "Away"],
    )
    return created["entity_id"]


# ── Registration ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tool", ["update_helper", "delete_helper"])
def test_registered_in_both_lanes_and_admin_gated(tool: str) -> None:
    assert TOOL_MAP[tool].requires_admin
    assert not TOOL_MAP[tool].panel_only
    assert tool in COMMAND_TOOL_NAMES
    assert tool in CONFIG_TOOL_NAMES
    assert f"selora_{tool}" in mcp_access._ADMIN_TOOLS


def test_a_chat_delete_is_a_confirmed_kind() -> None:
    """Both allowlists, or the card is silently dropped."""
    from custom_components.selora_ai.llm_client.command_policy import (
        _DELETE_KINDS,
        _DELETE_TOOLS,
    )

    assert "delete_helper" in _DELETE_TOOLS
    assert "helper" in _DELETE_KINDS


def test_the_mcp_delete_says_it_runs_immediately() -> None:
    (tool,) = [t for t in mcp_definitions._TOOL_DEFINITIONS if t.name == "selora_delete_helper"]
    assert "IMMEDIATELY" in tool.description
    assert "remaining_intent" not in tool.inputSchema["properties"]


# ── Update ──────────────────────────────────────────────────────────────────


async def test_an_update_keeps_every_setting_it_did_not_name(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    """HA's update replaces the whole item; passing only the options would have
    dropped the icon."""
    entity_id = await _house_mode(hass)

    result = await _mcp(
        hass, "update_helper", entity_id=entity_id, options=["Home", "Away", "Night"]
    )

    assert result["status"] == "updated", result
    assert result["changed"] == ["options"]
    attributes = hass.states.get(entity_id).attributes
    assert attributes["options"] == ["Home", "Away", "Night"]
    assert attributes["icon"] == "mdi:home"
    assert attributes["friendly_name"] == "House mode"


async def test_chat_updates_directly(hass: HomeAssistant, helpers_loaded: None) -> None:
    """Every setting can be set back, so no card."""
    entity_id = await _house_mode(hass)
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)

    result = await executor.execute("update_helper", {"entity_id": entity_id, "icon": "mdi:sofa"})

    assert result["status"] == "updated", result
    assert hass.states.get(entity_id).attributes["icon"] == "mdi:sofa"


async def test_an_optional_setting_is_cleared_explicitly(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    entity_id = await _house_mode(hass)

    await _mcp(hass, "update_helper", entity_id=entity_id, clear=["icon"])

    assert "icon" not in hass.states.get(entity_id).attributes


async def test_counter_bounds_take_the_counter_spelling(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    created = await _mcp(hass, "create_helper", domain="counter", name="Visits", max=10)

    await _mcp(hass, "update_helper", entity_id=created["entity_id"], max=20)

    assert hass.states.get(created["entity_id"]).attributes["maximum"] == 20


async def test_a_counter_bound_is_cleared_by_its_parameter_name(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    created = await _mcp(hass, "create_helper", domain="counter", name="Visits", max=10)

    result = await _mcp(hass, "update_helper", entity_id=created["entity_id"], clear=["max"])

    assert result["status"] == "updated", result
    assert result["changed"] == ["maximum"]
    assert hass.states.get(created["entity_id"]).attributes.get("maximum") is None


async def test_a_timer_takes_a_new_duration(hass: HomeAssistant, helpers_loaded: None) -> None:
    created = await _mcp(hass, "create_helper", domain="timer", name="Laundry", duration="0:45:00")

    await _mcp(hass, "update_helper", entity_id=created["entity_id"], duration="1:00:00")

    assert hass.states.get(created["entity_id"]).attributes["duration"] == "1:00:00"


async def test_a_rename_onto_another_helper_is_refused(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    await _mcp(hass, "create_helper", domain="input_boolean", name="Guest mode")
    other = await _mcp(hass, "create_helper", domain="input_boolean", name="Party mode")

    clash = await _mcp(hass, "update_helper", entity_id=other["entity_id"], name="guest MODE")
    own = await _mcp(hass, "update_helper", entity_id=other["entity_id"], name="PARTY mode")

    assert "already exists as input_boolean.guest_mode" in clash["error"]
    assert own["status"] == "updated", own


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "Nothing to change"),
        ({"clear": ["pattern"]}, "has no pattern setting"),
        ({"icon": "mdi:x", "clear": ["icon"]}, "not both"),
        ({"options": ["Home", "Home"]}, "would refuse"),
    ],
)
async def test_unusable_changes_are_refused_and_change_nothing(
    hass: HomeAssistant, helpers_loaded: None, arguments: dict[str, Any], message: str
) -> None:
    entity_id = await _house_mode(hass)

    result = await _mcp(hass, "update_helper", entity_id=entity_id, **arguments)

    assert message in result["error"]
    assert hass.states.get(entity_id).attributes["options"] == ["Home", "Away"]


async def test_a_yaml_helper_is_refused_with_where_to_change_it(hass: HomeAssistant) -> None:
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"from_yaml": {"name": "From YAML"}}}
    )
    await hass.async_block_till_done()

    result = await _mcp(hass, "update_helper", entity_id="input_boolean.from_yaml", icon="mdi:x")

    assert "configuration.yaml" in result["error"]


async def test_a_non_helper_is_refused(hass: HomeAssistant, helpers_loaded: None) -> None:
    hass.states.async_set("light.kitchen", "on")

    result = await _mcp(hass, "update_helper", entity_id="light.kitchen", name="X")

    assert "not a helper that can be changed here" in result["error"]


async def test_an_unknown_helper_points_at_list_helpers(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    result = await _mcp(hass, "update_helper", entity_id="input_boolean.nope", name="X")

    assert "list_helpers" in result["error"]


# ── Delete ──────────────────────────────────────────────────────────────────


async def test_mcp_deletes_and_names_what_used_it(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    created = await _mcp(hass, "create_helper", domain="input_boolean", name="Guest mode")
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "alias": "Welcome",
                "triggers": [{"trigger": "state", "entity_id": created["entity_id"]}],
                "actions": [],
            }
        },
    )
    await hass.async_block_till_done()

    result = await _mcp(hass, "delete_helper", entity_id=created["entity_id"])

    assert result["status"] == "deleted", result
    assert result["was_used_by"] == ["1 automation"]
    await hass.async_block_till_done()
    assert hass.states.get(created["entity_id"]) is None


async def test_an_update_hands_back_the_settings_it_replaced(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    """The result is the only undo: the old settings put the helper back."""
    entity_id = await _house_mode(hass)

    result = await _mcp(hass, "update_helper", entity_id=entity_id, options=["Night"])
    # Only what the update changed.
    assert result["previous"] == {"options": ["Home", "Away"]}

    await _mcp(hass, "update_helper", entity_id=entity_id, **result["previous"])
    assert hass.states.get(entity_id).attributes["options"] == ["Home", "Away"]


async def test_a_deleted_helper_can_be_created_again_from_the_result(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    entity_id = await _house_mode(hass)

    result = await _mcp(hass, "delete_helper", entity_id=entity_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id) is None

    again = await _mcp(hass, "create_helper", domain="input_select", **result["previous"])
    await hass.async_block_till_done()
    attributes = hass.states.get(again["entity_id"]).attributes
    assert attributes["options"] == ["Home", "Away"]
    assert attributes["icon"] == "mdi:home"


async def test_chat_proposes_a_card_and_deletes_nothing(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    entity_id = await _house_mode(hass)
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)

    result = await executor.execute("delete_helper", {"entity_id": entity_id})

    assert result["requires_approval"] is True
    assert result["delete"]["kind"] == "helper"
    assert result["delete"]["label"] == "Delete the House mode helper"
    assert hass.states.get(entity_id) is not None


async def _confirm(hass: HomeAssistant, descriptor: dict[str, Any]) -> MagicMock:
    """Tap Delete. Returns the store; the connection is ``store.connection``."""
    from custom_components.selora_ai import _resolve_delete_approval

    store = MagicMock()
    store.set_approval_status = AsyncMock()
    store.append_message = AsyncMock(return_value={"role": "assistant"})
    store.connection = MagicMock()
    await _resolve_delete_approval(
        hass,
        store.connection,
        {"id": 1},
        store,
        "sess",
        0,
        {"approval_kind": "delete", "deletes": [descriptor]},
        "delete",
        language="en",
    )
    return store


async def test_confirming_the_card_deletes_the_helper(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    entity_id = await _house_mode(hass)
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    card = await executor.execute("delete_helper", {"entity_id": entity_id})

    store = await _confirm(hass, card["delete"])

    store.set_approval_status.assert_awaited_once_with("sess", 0, "approved")
    await hass.async_block_till_done()
    assert hass.states.get(entity_id) is None


async def test_a_helper_changed_since_the_card_is_not_deleted(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    """Its entity_id comes from its name, so one deleted and remade under that
    name answers to the card — the content is what the user approved."""
    entity_id = await _house_mode(hass)
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    card = await executor.execute("delete_helper", {"entity_id": entity_id})
    await _mcp(hass, "update_helper", entity_id=entity_id, options=["Holiday"])

    store = await _confirm(hass, card["delete"])

    # Nothing removed: the card stays pending, with the reason, so it can be
    # retried rather than shown as a terminal "Deleted".
    store.set_approval_status.assert_not_awaited()
    (_, code, detail), _ = store.connection.send_error.call_args
    assert code == "delete_failed"
    assert "changed since it was shown" in detail
    assert hass.states.get(entity_id) is not None


async def test_a_setting_the_update_added_is_cleared_on_the_way_back(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    created = await _mcp(hass, "create_helper", domain="input_boolean", name="Guest mode")
    entity_id = created["entity_id"]

    result = await _mcp(hass, "update_helper", entity_id=entity_id, icon="mdi:account")
    assert result["previous"]["clear"] == ["icon"]

    await _mcp(hass, "update_helper", entity_id=entity_id, **result["previous"])
    assert "icon" not in hass.states.get(entity_id).attributes


async def test_a_counter_comes_back_in_the_tools_own_spelling(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    """Stored as minimum/maximum, taken as min/max: the stored names would be
    ignored on the way back."""
    created = await _mcp(hass, "create_helper", domain="counter", name="Visits", max=10)

    result = await _mcp(hass, "update_helper", entity_id=created["entity_id"], min=2, max=20)
    assert result["previous"]["max"] == 10
    assert result["previous"]["clear"] == ["min"]

    await _mcp(hass, "update_helper", entity_id=created["entity_id"], **result["previous"])
    attributes = hass.states.get(created["entity_id"]).attributes
    assert attributes["maximum"] == 10
    assert attributes.get("minimum") is None


async def test_a_deleted_schedule_comes_back_with_its_week(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    week = {"monday": [{"from": "07:00", "to": "09:00"}]}
    created = await _mcp(hass, "create_helper", domain="schedule", name="School", schedule=week)

    result = await _mcp(hass, "delete_helper", entity_id=created["entity_id"])
    assert result["previous"]["schedule"]["monday"][0]["from"].startswith("07:00")

    again = await _mcp(hass, "create_helper", domain="schedule", **result["previous"])
    assert "error" not in again, again
    # A live schedule keeps a timer for its next block.
    await _mcp(hass, "delete_helper", entity_id=again["entity_id"])
    await hass.async_block_till_done()
