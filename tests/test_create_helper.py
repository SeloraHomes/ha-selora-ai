"""Tests for the panel-executed helper creation.

Storage-collection helpers (``input_select`` and friends) are created by each
component's admin-only ``<domain>/create`` websocket command, which only the
panel can call. The backend validates with the component's own schema and
proposes a closed intent; the panel builds the fixed call from it.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai.tool_executor import ToolExecutor, create_helper_fields
from custom_components.selora_ai.tool_registry import (
    COMMAND_TOOL_NAMES,
    CONFIG_TOOL_NAMES,
    TOOL_MAP,
)


def _executor(hass: HomeAssistant) -> ToolExecutor:
    return ToolExecutor(hass, MagicMock(), is_admin=True)


@pytest.fixture
async def helpers_loaded(hass: HomeAssistant) -> None:
    for domain in ("input_select", "input_boolean", "input_number", "counter", "timer"):
        assert await async_setup_component(hass, domain, {})


# ── Registration ────────────────────────────────────────────────────────────


def test_create_helper_is_panel_only_and_in_both_lanes() -> None:
    tool = TOOL_MAP["create_helper"]
    assert tool.panel_only
    assert tool.requires_admin
    assert "create_helper" in COMMAND_TOOL_NAMES
    assert "create_helper" in CONFIG_TOOL_NAMES


def test_the_panel_tool_is_not_derived_onto_mcp() -> None:
    """Its description promises a Create button MCP has none of; MCP gets
    `TOOL_CREATE_HELPER_DIRECT` instead (`tests/test_mcp_storage_helpers.py`)."""
    from custom_components.selora_ai import mcp_server

    assert "create_helper" not in mcp_server._DERIVED_MCP_TOOLS.values()


def test_list_helpers_no_longer_says_creation_is_impossible() -> None:
    """Left beside create_helper, the denial would contradict it inside one
    schema — the model then sends the user to Settings, which is the reply
    this tool exists to replace."""
    description = TOOL_MAP["list_helpers"].description.lower()
    assert "not possible" not in description
    assert "create_helper" in description


# ── Proposal ────────────────────────────────────────────────────────────────


async def test_an_input_select_becomes_a_closed_intent(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    result = await _executor(hass).execute(
        "create_helper",
        {
            "domain": "input_select",
            "name": "Alarm mode",
            "options": ["disarmed", "armed_home", "armed_away"],
            "initial": "disarmed",
            "icon": "mdi:shield-home",
            # Not an input_select field: dropped, not refused.
            "min": 3,
        },
    )

    assert result["requires_approval"] is True
    action = result["client_action"]
    assert action["kind"] == "create_helper"
    assert action["domain"] == "input_select"
    assert action["fields"] == {
        "name": "Alarm mode",
        "options": ["disarmed", "armed_home", "armed_away"],
        "initial": "disarmed",
        "icon": "mdi:shield-home",
    }
    # Nothing was created.
    assert hass.states.get("input_select.alarm_mode") is None


@pytest.mark.parametrize(
    ("arguments", "fragment"),
    [
        ({"domain": "input_select", "name": "Mode", "options": []}, "would refuse"),
        (
            {"domain": "input_select", "name": "Mode", "options": ["a", "b"], "initial": "c"},
            "would refuse",
        ),
        ({"domain": "input_select", "name": "Mode", "options": ["a", "a"]}, "would refuse"),
        ({"domain": "input_number", "name": "Level"}, "would refuse"),
        ({"domain": "input_boolean", "name": "Guest", "icon": "account"}, "would refuse"),
        ({"domain": "hue", "name": "x"}, "not a helper integration"),
    ],
    ids=[
        "no-options",
        "initial-not-an-option",
        "duplicate-options",
        "no-range",
        "bad-icon",
        "not-creatable",
    ],
)
async def test_what_ha_would_reject_is_refused_before_the_button(
    hass: HomeAssistant, helpers_loaded: None, arguments: dict[str, Any], fragment: str
) -> None:
    result = await _executor(hass).execute("create_helper", arguments)
    assert fragment in result["error"]


async def test_an_existing_helper_of_that_name_is_refused(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    """HA would not refuse it — the collection suffixes the id — so the user
    would end up with two helpers of the same name."""
    hass.states.async_set("input_boolean.guest_toggle", "off", {"friendly_name": "Guest Mode"})

    result = await _executor(hass).execute(
        "create_helper", {"domain": "input_boolean", "name": "guest  mode"}
    )

    assert "already exists as input_boolean.guest_toggle" in result["error"]


async def test_an_unloaded_component_is_refused(hass: HomeAssistant) -> None:
    result = await _executor(hass).execute(
        "create_helper", {"domain": "input_button", "name": "Doorbell"}
    )
    assert "not loaded" in result["error"]


async def test_a_timer_duration_is_sent_in_the_form_ha_stores(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    """The panel compares a retry field by field against what HA stored."""
    result = await _executor(hass).execute(
        "create_helper", {"domain": "timer", "name": "Exit delay", "duration": "00:00:30"}
    )
    assert result["client_action"]["fields"]["duration"] == "0:00:30"


def test_counter_bounds_take_the_counter_spelling() -> None:
    fields = create_helper_fields(
        {"domain": "counter", "name": "Visits", "min": 0, "max": 10, "icon": ""}
    )
    assert fields == {"name": "Visits", "minimum": 0, "maximum": 10}


def test_the_proposal_becomes_a_client_action_card() -> None:
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    result = synthesize_approval_from_tool_log(
        {"intent": "answer", "response": "Created the helper."},
        [
            {
                "tool": "create_helper",
                "arguments": {},
                "result": {
                    "requires_approval": True,
                    "client_action": {
                        "kind": "create_helper",
                        "domain": "input_boolean",
                        "name": "Guest mode",
                        "fields": {"name": "Guest mode"},
                    },
                },
            }
        ],
        None,
    )

    approval = result["command_approval"]
    assert approval["approval_kind"] == "client_action"
    assert approval["client_actions"][0]["kind"] == "create_helper"
    # The model's premature success claim is replaced by the pending line.
    assert "Created" not in result["response"]


# ── Reporting the outcome ───────────────────────────────────────────────────


async def _session_with_helper_proposal(hass: HomeAssistant) -> tuple[Any, str, str]:
    from custom_components.selora_ai.const import DOMAIN
    from custom_components.selora_ai.conversation_store import ConversationStore

    store = ConversationStore(hass)
    hass.data.setdefault(DOMAIN, {})["_conv_store"] = store
    await store.append_message("sess-1", "user", "make me an alarm mode")
    await store.append_message(
        "sess-1",
        "assistant",
        "Nothing has been created yet.",
        intent="command_approval",
        command_approval={
            "proposal_id": "prop-1",
            "approval_kind": "client_action",
            "calls": [],
            "deletes": [],
            "actions": [],
            "client_actions": [
                {
                    "kind": "create_helper",
                    "domain": "input_select",
                    "name": "Alarm mode",
                    "fields": {"name": "Alarm mode", "options": ["disarmed"]},
                }
            ],
        },
    )
    return store, "sess-1", "prop-1"


async def _report(hass: HomeAssistant, entity_id: str) -> tuple[Any, MagicMock]:
    from custom_components.selora_ai.websocket import tokens

    store, session_id, proposal_id = await _session_with_helper_proposal(hass)
    connection = MagicMock()
    connection.user.is_admin = True
    handler = inspect.unwrap(tokens._handle_websocket_client_action_result)
    await handler(
        hass,
        connection,
        {
            "id": 1,
            "session_id": session_id,
            "proposal_id": proposal_id,
            "results": [
                {
                    "ok": True,
                    "kind": "create_helper",
                    "detail": {"entity_id": entity_id, "name": "Alarm mode"},
                }
            ],
        },
    )
    return await store.get_session(session_id), connection


async def test_a_reported_helper_is_named_with_its_entity_id(hass: HomeAssistant) -> None:
    """The resumed turn wires automations to it, and the id may carry a suffix."""
    hass.states.async_set("input_select.alarm_mode_2", "disarmed", {"friendly_name": "Alarm mode"})

    session, connection = await _report(hass, "input_select.alarm_mode_2")

    connection.send_error.assert_not_called()
    assert session["messages"][1]["approval_status"] == "approved"
    assert "input_select.alarm_mode_2" in session["messages"][-1]["content"]


@pytest.mark.parametrize(
    "entity_id",
    ["lock.front_door", "input_select.something_else", "input_select.missing"],
    ids=["wrong-domain", "wrong-name", "no-such-entity"],
)
async def test_a_reported_id_that_does_not_check_out_is_left_out(
    hass: HomeAssistant, entity_id: str
) -> None:
    """The panel says whether its work succeeded, not what it was done to."""
    hass.states.async_set("lock.front_door", "locked", {"friendly_name": "Alarm mode"})
    hass.states.async_set("input_select.something_else", "a", {"friendly_name": "Other"})

    session, _ = await _report(hass, entity_id)

    content = session["messages"][-1]["content"]
    assert "Alarm mode" in content
    assert entity_id not in content


async def test_a_renamed_helper_does_not_block_its_old_name(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    """ "Vacation mode" may still live at input_boolean.guest_mode; a new
    "Guest mode" is not a duplicate — HA suffixes its id."""
    hass.states.async_set("input_boolean.guest_mode", "off", {"friendly_name": "Vacation mode"})

    result = await _executor(hass).execute(
        "create_helper", {"domain": "input_boolean", "name": "Guest mode"}
    )

    assert result.get("requires_approval") is True, result
