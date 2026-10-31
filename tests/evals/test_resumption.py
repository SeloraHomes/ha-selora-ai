"""After a confirmed card, does a real model finish the job, and only the job?

The card ends the turn; confirming it resumes the request. With nothing left
the model can only restate the outcome line the confirmation already wrote,
so that turn must come back `discarded`. With something left it must do it.
Graded on what the turns DID, never on wording.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai.helpers import caller_scope
from custom_components.selora_ai.mcp_server import dashboards as mcp_dashboards
from custom_components.selora_ai.websocket import tokens

from ..chat_harness import ChatHarness, ChatTurn

pytestmark = pytest.mark.eval


async def _cameras_dashboard(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "lovelace", {"lovelace": {"mode": "storage"}})
    await hass.async_block_till_done()
    with caller_scope(True):
        created = await mcp_dashboards._tool_create_dashboard(hass, {"title": "Cameras"})
    assert "error" not in created, created


def _card(turn: ChatTurn) -> dict[str, Any]:
    approval = turn.done.get("command_approval") or {}
    kinds = [a.get("kind") for a in approval.get("client_actions") or []]
    assert kinds == ["delete_dashboard"], (
        f"expected a delete card, got {kinds}\n"
        f"tools: {[c['tool'] for c in turn.tool_calls]}\nreply: {turn.done.get('response')}"
    )
    return approval


async def _confirm(hass: HomeAssistant, home: ChatHarness, approval: dict[str, Any]) -> None:
    """What the panel does on a tap: delete, report, then resume."""
    with caller_scope(True):
        await mcp_dashboards._tool_delete_dashboard(hass, {"dashboard_target": "cameras"})
    connection = MagicMock()
    connection.user.is_admin = True
    await inspect.unwrap(tokens._handle_websocket_client_action_result)(
        hass,
        connection,
        {
            "id": 1,
            "session_id": home.session_id,
            "proposal_id": approval["proposal_id"],
            "results": [
                {"ok": True, "kind": "delete_dashboard", "detail": {"url_path": "cameras"}}
            ],
        },
    )


def _synthetic_spinner(turn: ChatTurn) -> bool:
    return any(e.get("synthetic") for e in turn.events if e.get("type") == "token")


async def test_a_delete_that_was_the_whole_request_says_it_once(
    hass: HomeAssistant, home: ChatHarness, run: int
) -> None:
    await _cameras_dashboard(hass)
    approval = _card(await home.live("Remove the Cameras dashboard"))
    await _confirm(hass, home, approval)
    before = await home.messages()

    resumed = await home.live("", resume_proposal_id=approval["proposal_id"])

    assert not resumed.errors, resumed.errors
    called = [c["tool"] for c in resumed.tool_calls]
    assert "delete_dashboard" not in called, called
    assert not _synthetic_spinner(resumed)
    assert resumed.done.get("discarded") is True, (
        f"tools: {called}\nreply: {resumed.done.get('response')}"
    )
    assert await home.messages() == before


async def test_a_delete_with_more_to_do_does_the_rest(
    hass: HomeAssistant, home: ChatHarness, run: int
) -> None:
    await _cameras_dashboard(hass)
    approval = _card(
        await home.live("Remove the Cameras dashboard and create a new one called Security")
    )
    await _confirm(hass, home, approval)

    resumed = await home.live("", resume_proposal_id=approval["proposal_id"])

    assert not resumed.errors, resumed.errors
    called = [c["tool"] for c in resumed.tool_calls]
    assert not resumed.done.get("discarded"), resumed.done
    assert "create_dashboard" in called, f"tools: {called}\nreply: {resumed.done.get('response')}"
