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


def _card(turn: ChatTurn, *kinds: str) -> dict[str, Any]:
    """The turn's one dashboard card, of one of ``kinds``."""
    approval = turn.done.get("command_approval") or {}
    got = [a.get("kind") for a in approval.get("client_actions") or []]
    assert len(got) == 1 and got[0] in kinds, (
        f"expected a card of {kinds}, got {got}\n"
        f"tools: {[c['tool'] for c in turn.tool_calls]}\nreply: {turn.done.get('response')}"
    )
    return approval


async def _confirm(hass: HomeAssistant, home: ChatHarness, approval: dict[str, Any]) -> None:
    """What the panel does on a tap: run the action, then report it."""
    action = approval["client_actions"][0]
    with caller_scope(True):
        if action["kind"] == "delete_dashboard":
            await mcp_dashboards._tool_delete_dashboard(
                hass, {"dashboard_target": action["url_path"]}
            )
        else:
            await mcp_dashboards._tool_create_dashboard(
                hass, {"title": action["title"], "url_path": action["url_path"]}
            )
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
                {
                    "ok": True,
                    "kind": action["kind"],
                    "detail": {"url_path": action["url_path"], "title": action.get("title")},
                }
            ],
        },
    )


def _synthetic_spinner(turn: ChatTurn) -> bool:
    return any(e.get("synthetic") for e in turn.events if e.get("type") == "token")


async def test_a_delete_that_was_the_whole_request_says_it_once(
    hass: HomeAssistant, home: ChatHarness, run: int
) -> None:
    await _cameras_dashboard(hass)
    approval = _card(await home.live("Remove the Cameras dashboard"), "delete_dashboard")
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
    """Two writes, one card per message: either may come first. Confirming it
    must bring back the OTHER, never the same one again."""
    await _cameras_dashboard(hass)
    first = _card(
        await home.live("Remove the Cameras dashboard and create a new one called Security"),
        "delete_dashboard",
        "create_dashboard",
    )
    await _confirm(hass, home, first)

    resumed = await home.live("", resume_proposal_id=first["proposal_id"])

    assert not resumed.errors, resumed.errors
    assert not resumed.done.get("discarded"), resumed.done
    done_kind = first["client_actions"][0]["kind"]
    other = "create_dashboard" if done_kind == "delete_dashboard" else "delete_dashboard"
    called = [c["tool"] for c in resumed.tool_calls]
    assert other in called and done_kind not in called, (
        f"first: {done_kind}\ntools: {called}\nreply: {resumed.done.get('response')}"
    )
