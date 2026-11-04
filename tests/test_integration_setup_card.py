"""Tests for the setup card: a step of an integration's setup the user fills
in themselves, in the chat.

A login or a sign-in never goes through the model, so ``add_integration``
hands such a step to a card, and the panel posts it straight to Home
Assistant's flow. These cover the server's side: the card is collected from
the tool log, a link handed out over MCP opens a chat holding it, and what the
panel reports is checked against Home Assistant before it is recorded.
"""

from __future__ import annotations

from collections.abc import Iterator
import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import ConfigFlow
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_config_flow,
    mock_integration,
    mock_platform,
)
import voluptuous as vol

from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.conversation_store import ConversationStore
from custom_components.selora_ai.device_manager import DeviceManager
from custom_components.selora_ai.llm_client.command_policy import (
    _pending_client_actions_from_log,
)
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.websocket import devices as ws_devices
from custom_components.selora_ai.websocket import tokens as ws_tokens


class _LoginFlow(ConfigFlow):
    """A cloud account, asked for its password up front."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {vol.Required("username"): str, vol.Required("password"): str}
                ),
            )
        return self.async_create_entry(title=user_input["username"], data=user_input)


@pytest.fixture
def cloud(hass: HomeAssistant) -> Iterator[None]:
    mock_integration(
        hass,
        MockModule(
            "cloudy",
            async_setup_entry=AsyncMock(return_value=True),
            partial_manifest={"config_flow": True},
        ),
    )
    mock_platform(hass, "cloudy.config_flow", None)
    with mock_config_flow("cloudy", _LoginFlow):
        yield


def _connection(*, admin: bool = True) -> MagicMock:
    connection = MagicMock()
    connection.user.is_admin = admin
    return connection


def _store(hass: HomeAssistant) -> ConversationStore:
    store = ConversationStore(hass)
    hass.data.setdefault(DOMAIN, {})["_conv_store"] = store
    return store


async def test_a_login_step_becomes_a_card_in_chat(hass: HomeAssistant, cloud: None) -> None:
    executor = ToolExecutor(hass, DeviceManager(hass), is_admin=True)

    result = await executor.execute("add_integration", {"domain": "cloudy"})

    (flow,) = hass.config_entries.flow.async_progress()
    assert result["requires_approval"] is True
    assert _pending_client_actions_from_log([{"tool": "add_integration", "result": result}]) == [
        {
            "kind": "integration_setup",
            "flow_id": flow["flow_id"],
            "domain": "cloudy",
            "label": "cloudy",
        }
    ]


async def test_a_setup_link_opens_a_chat_holding_its_card(hass: HomeAssistant, cloud: None) -> None:
    store = _store(hass)
    flow = await hass.config_entries.flow.async_init("cloudy", context={"source": "user"})
    connection = _connection()
    handler = inspect.unwrap(ws_devices._handle_websocket_integration_setup_open)

    await handler(hass, connection, {"id": 1, "flow_id": flow["flow_id"], "language": "fr"})

    connection.send_error.assert_not_called()
    session = await store.get_session(connection.send_result.call_args.args[1]["session_id"])
    (message,) = session["messages"]
    assert message["approval_status"] == "pending"
    assert message["command_approval"]["client_actions"] == [
        {
            "kind": "integration_setup",
            "flow_id": flow["flow_id"],
            "domain": "cloudy",
            "label": "cloudy",
        }
    ]
    # Said where what is typed goes, in the user's language.
    assert "Home Assistant" in message["content"]
    assert "jamais à moi" in message["content"]


@pytest.mark.parametrize("admin", [True, False])
async def test_a_setup_link_opens_nothing_it_should_not(hass: HomeAssistant, admin: bool) -> None:
    _store(hass)
    connection = _connection(admin=admin)
    handler = inspect.unwrap(ws_devices._handle_websocket_integration_setup_open)

    await handler(hass, connection, {"id": 1, "flow_id": "not-a-flow"})

    connection.send_result.assert_not_called()
    connection.send_error.assert_called_once()


async def _report(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    store = _store(hass)
    await store.append_message(
        "sess-1",
        "assistant",
        "This step is yours.",
        intent="command_approval",
        command_approval={
            "proposal_id": "prop-1",
            "approval_kind": "client_action",
            "calls": [],
            "deletes": [],
            "actions": [],
            "client_actions": [
                {"kind": "integration_setup", "flow_id": "f1", "domain": "hue", "label": "Hue"}
            ],
        },
    )
    handler = inspect.unwrap(ws_tokens._handle_websocket_client_action_result)
    await handler(
        hass,
        _connection(),
        {
            "id": 1,
            "session_id": "sess-1",
            "proposal_id": "prop-1",
            "results": [
                {"ok": True, "kind": "integration_setup", "detail": {"entry_id": entry_id}}
            ],
        },
    )
    return await store.get_session("sess-1")


async def test_a_finished_setup_is_recorded_from_home_assistant(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain="hue", title="Hue Bridge")
    entry.add_to_hass(hass)

    session = await _report(hass, entry.entry_id)

    assert session["messages"][0]["approval_status"] == "approved"
    assert session["messages"][-1]["content"] == "Set up Hue (Hue Bridge)."


@pytest.mark.parametrize("domain", ["sonos", None], ids=["other-integration", "no-such-entry"])
async def test_a_reported_entry_that_does_not_check_out_is_not_recorded(
    hass: HomeAssistant, domain: str | None
) -> None:
    """The panel says whether its work succeeded, not what it made."""
    entry_id = "missing"
    if domain:
        entry = MockConfigEntry(domain=domain, title="Kitchen")
        entry.add_to_hass(hass)
        entry_id = entry.entry_id

    session = await _report(hass, entry_id)

    assert session["messages"][0]["approval_status"] == "denied"
    assert "no record" in session["messages"][-1]["content"]
