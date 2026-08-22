"""Continuing the work a confirmed card left unfinished.

A turn that proposes a confirmation card ENDS there: the tool loop
short-circuits on `requires_approval`, and the handler that runs after the
button appends one line and stops. So "create a dashboard for the Office with
my Office devices" created an empty dashboard and dropped the rest, silently,
while the transcript read as complete.

Resumption is the general fix. The model declares what remains, the card shows
it, and confirming re-enters the streaming chat path to finish the job.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai import _resume_request
from custom_components.selora_ai.tool_executor import ToolExecutor


def _approved(**approval: Any) -> list[dict[str, Any]]:
    base: dict[str, Any] = {
        "proposal_id": "p1",
        "approval_kind": "client_action",
        "remaining_intent": "add the Office lights",
        "resume_depth": 0,
    }
    base.update(approval)
    return [
        {"role": "user", "content": "office dashboard with my devices"},
        {
            "role": "assistant",
            "content": "Nothing has been created yet.",
            "command_approval": base,
            "approval_status": approval.pop("_status", "approved"),
        },
    ]


def _messages(status: str = "approved", **approval: Any) -> list[dict[str, Any]]:
    out = _approved(**approval)
    out[1]["approval_status"] = status
    return out


# ── The trigger ─────────────────────────────────────────────────────────────


def test_an_approved_card_with_work_left_resumes() -> None:
    directive, error = _resume_request(_messages(), "p1")
    assert error is None
    assert directive is not None
    # It names what is left and forbids redoing what just happened.
    assert "add the Office lights" in directive
    assert "do not repeat the step that just completed" in directive


def test_a_denied_card_does_not_resume() -> None:
    """The user said no. Continuing anyway is the opposite of asking."""
    directive, error = _resume_request(_messages("denied"), "p1")
    assert directive is None
    assert "not approved" in error


def test_a_pending_card_does_not_resume() -> None:
    directive, error = _resume_request(_messages("pending"), "p1")
    assert directive is None
    assert "not approved" in error


def test_a_card_with_nothing_left_does_not_resume() -> None:
    """Most cards. "Unlock the front door" is the whole request, and a model
    round to discover that would be paid on every approval."""
    directive, error = _resume_request(_messages(remaining_intent=None), "p1")
    assert directive is None
    assert "nothing left" in error


def test_an_unknown_proposal_is_refused() -> None:
    directive, error = _resume_request(_messages(), "nope")
    assert directive is None
    assert "No such proposal" in error


# ── The cap ─────────────────────────────────────────────────────────────────


def test_a_resumed_card_cannot_resume_again() -> None:
    """Depth 1 is stamped on any card a resumed turn proposes. Without the cap
    a model that keeps proposing one more step runs unbounded on the user's
    account, each round costing them tokens."""
    directive, error = _resume_request(_messages(resume_depth=1), "p1")
    assert directive is None
    assert "already been continued" in error


@pytest.mark.parametrize("depth", [1, 2, 7])
def test_the_cap_holds_at_any_depth(depth: int) -> None:
    _, error = _resume_request(_messages(resume_depth=depth), "p1")
    assert "already been continued" in error


def test_a_missing_depth_reads_as_zero() -> None:
    """Proposals stored before this shipped have no `resume_depth`, and a
    KeyError or a refusal would both be wrong — they are first-generation."""
    messages = _messages()
    del messages[1]["command_approval"]["resume_depth"]
    directive, error = _resume_request(messages, "p1")
    assert error is None
    assert directive is not None


# ── Nothing is trusted from the client ──────────────────────────────────────


def test_the_directive_comes_from_the_store_not_the_caller() -> None:
    """The panel sends an id and nothing else. It could type any message it
    likes anyway, but it must not be able to bypass the approval or the cap."""
    import inspect

    source = inspect.getsource(_resume_request)
    # Reads the stored proposal only.
    assert "stored_messages" in source
    assert "msg[" not in source


# ── The model has to be able to declare it ──────────────────────────────────


async def test_create_dashboard_reports_the_remaining_work(hass: HomeAssistant) -> None:
    result = await ToolExecutor(hass, None, is_admin=True).execute(
        "create_dashboard",
        {"title": "Office", "remaining_intent": "add the Office lights and climate"},
    )
    assert result["remaining_intent"] == "add the Office lights and climate"
    # Turn metadata, not something the panel executes.
    assert "remaining_intent" not in result["client_action"]


async def test_no_declared_work_means_no_resumption(hass: HomeAssistant) -> None:
    result = await ToolExecutor(hass, None, is_admin=True).execute(
        "create_dashboard", {"title": "Office"}
    )
    # Absent, not null: the key exists only when there is something to say.
    assert "remaining_intent" not in result


async def test_the_declaration_is_sanitized(hass: HomeAssistant) -> None:
    """It is echoed onto the card and back into a later prompt."""
    result = await ToolExecutor(hass, None, is_admin=True).execute(
        "create_dashboard",
        {"title": "Office", "remaining_intent": "x" * 400},
    )
    assert len(result["remaining_intent"]) <= 200


def test_the_approval_carries_it_to_the_card() -> None:
    """The turn that declared it is over by the time the button is pressed, so
    it has to be on the persisted proposal — nothing else remembers."""
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    result = synthesize_approval_from_tool_log(
        {"intent": "answer", "response": "Right."},
        [
            {
                "tool": "create_dashboard",
                "arguments": {},
                "result": {
                    "requires_approval": True,
                    "remaining_intent": "add the Office lights",
                    "client_action": {
                        "kind": "create_dashboard",
                        "title": "Office",
                        "label": "Create the Office dashboard at /office",
                    },
                },
            }
        ],
        None,
    )

    approval = result["command_approval"]
    assert approval["remaining_intent"] == "add the Office lights"
    assert approval["resume_depth"] == 0


# ── The transcript stays the user's own ─────────────────────────────────────


async def test_a_resumption_persists_no_user_message(hass: HomeAssistant) -> None:
    """The directive is written by the SERVER. Storing it would put words in
    the user's mouth in their own transcript, and every later turn would read
    it back as something they said — which is also how a model ends up
    "remembering" instructions nobody gave it."""
    from tests.chat_harness import ChatHarness

    harness = await ChatHarness.create(hass)
    # The harness only learns its session id from a turn, and this test seeds
    # the session BEFORE the turn — a resumption always follows one.
    harness.session_id = "sess-resume"
    await harness.store.append_message(harness.session_id, "user", "office dashboard")
    await harness.store.append_message(
        harness.session_id,
        "assistant",
        "Nothing has been created yet.",
        intent="command_approval",
        command_approval={
            "proposal_id": "p1",
            "approval_kind": "client_action",
            "remaining_intent": "add the Office lights",
            "resume_depth": 0,
            "calls": [],
        },
        approval_status="approved",
    )

    before = await harness.messages()
    turn = await harness.stream("", chunks="Added the Office lights.", resume_proposal_id="p1")
    after = await harness.messages()

    # The model was asked to continue, with the declared remainder.
    asked = turn.asked
    assert "add the Office lights" in str(asked.get("user_message") or asked)
    # One new message, and it is the assistant's.
    assert len(after) == len(before) + 1
    assert after[-1]["role"] == "assistant"
    # Nowhere in the transcript did the user "say" the directive.
    assert not any(
        m["role"] == "user" and "Continue with what was left" in (m.get("content") or "")
        for m in after
    )


async def test_a_card_proposed_by_a_resumption_is_capped(hass: HomeAssistant) -> None:
    """Depth 1, so it cannot be continued again. Stamped where the turn knows
    it is a resumption, onto the payload that gets PERSISTED — the resume check
    reads the stored proposal, not the in-flight one."""
    from tests.chat_harness import ChatHarness

    harness = await ChatHarness.create(hass)
    # The harness only learns its session id from a turn, and this test seeds
    # the session BEFORE the turn — a resumption always follows one.
    harness.session_id = "sess-resume"
    await harness.store.append_message(harness.session_id, "user", "office dashboard")
    await harness.store.append_message(
        harness.session_id,
        "assistant",
        "Nothing has been created yet.",
        intent="command_approval",
        command_approval={
            "proposal_id": "p1",
            "approval_kind": "client_action",
            "remaining_intent": "add the Office lights",
            "resume_depth": 0,
            "calls": [],
        },
        approval_status="approved",
    )

    await harness.stream(
        "",
        chunks=(
            '```json\n{"intent": "command_approval", "response": "Confirm?", '
            '"command_approval": {"calls": [{"service": "lock.unlock", '
            '"entity_id": "lock.front"}]}}\n```'
        ),
        session_id=harness.session_id,
        resume_proposal_id="p1",
    )

    messages = await harness.messages()
    new_approval = messages[-1].get("command_approval")
    assert new_approval is not None, messages[-1]
    assert new_approval["resume_depth"] == 1
    # And the resolver refuses it even once approved — which is the cap. (An
    # unapproved card is refused for the other reason, so approve it first or
    # the assertion proves nothing.)
    await harness.store.set_approval_status(harness.session_id, len(messages) - 1, "approved")
    _, error = _resume_request(await harness.messages(), new_approval["proposal_id"])
    assert "already been continued" in error


# ── Deletions are steps too ─────────────────────────────────────────────────


DELETE_TOOLS = [
    "delete_automation",
    "delete_scene",
    "delete_group",
    "delete_area",
    "delete_floor",
    "delete_category",
    "delete_script",
    "delete_label",
]


@pytest.mark.parametrize("tool", DELETE_TOOLS)
def test_every_delete_tool_can_declare_remaining_work(tool: str) -> None:
    """ "Delete the old scene and rebuild it" is one request. Miss a tool here
    and its deletions silently become the whole turn."""
    from custom_components.selora_ai.tool_registry import TOOL_MAP

    assert "remaining_intent" in {p.name for p in TOOL_MAP[tool].params}


def test_the_delete_card_carries_the_declaration() -> None:
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    result = synthesize_approval_from_tool_log(
        {"intent": "answer", "response": "Right."},
        [
            {
                "tool": "delete_scene",
                "arguments": {},
                "result": {
                    "requires_approval": True,
                    "remaining_intent": "rebuild it with the new schedule",
                    "delete": {
                        "kind": "scene",
                        "target_id": "movie",
                        "entity_id": "scene.movie",
                        "label": "Movie Night",
                    },
                },
            }
        ],
        None,
    )

    approval = result["command_approval"]
    assert approval["approval_kind"] == "delete"
    assert approval["remaining_intent"] == "rebuild it with the new schedule"
    assert approval["resume_depth"] == 0


async def test_the_declaration_rides_on_any_carded_tool(hass: HomeAssistant) -> None:
    """Lifted once in the executor rather than in each handler. Those handlers
    are the `_preview_delete_*` functions, which MCP shares — and eight copies
    is eight chances for the next delete tool to be the one that forgets."""
    from homeassistant.helpers import area_registry as ar

    ar.async_get(hass).async_create("Study")

    result = await ToolExecutor(hass, None, is_admin=True).execute(
        "delete_area",
        {"area": "Study", "remaining_intent": "recreate it on the first floor"},
    )

    assert result.get("requires_approval") is True, result
    assert result["remaining_intent"] == "recreate it on the first floor"


async def test_a_tool_that_executes_now_declares_nothing(hass: HomeAssistant) -> None:
    """Only a result that is asking for approval can be resumed from. Anywhere
    else there is no card to come back from, and the field would advertise a
    continuation that never arrives."""
    hass.states.async_set("light.office", "off")

    result = await ToolExecutor(hass, None, is_admin=True).execute(
        "get_entity_state",
        {"entity_id": "light.office", "remaining_intent": "should be ignored"},
    )

    assert "remaining_intent" not in result


async def test_mcp_previews_are_untouched(hass: HomeAssistant) -> None:
    """The reason the lift is in the executor. MCP has no panel, so no card and
    nothing to resume — the shared preview must not grow a chat-only field."""
    from homeassistant.helpers import area_registry as ar

    from custom_components.selora_ai.mcp_server import _preview_delete_area

    ar.async_get(hass).async_create("Study")
    result = await _preview_delete_area(hass, {"area": "Study", "remaining_intent": "recreate it"})
    assert result.get("requires_approval") is True, result
    assert "remaining_intent" not in result


# ── A scene is proposed, not carded — same question, different door ─────────


def _scene_messages(status: str = "saved", **extra: Any) -> list[dict[str, Any]]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": "I can create the scene.",
        "scene": {"name": "Office Fan And Lights"},
        "scene_status": status,
        "scene_id": "selora_scene_office",
        "remaining_intent": "add a tile for it to the Office dashboard",
    }
    message.update(extra)
    return [{"role": "user", "content": "make a scene and add it"}, message]


def test_an_accepted_scene_continues_the_request() -> None:
    """ "Create a scene AND add it to the dashboard" is one request. The turn
    ended at the card and the scene entity did not exist until the user
    accepted, so acceptance is the first moment the rest can be done — and
    before this the model said "once it's created, I'll add a dashboard tile"
    and then never did."""
    directive, error = _resume_request(_scene_messages(), "selora_scene_office")

    assert error is None
    assert "add a tile for it to the Office dashboard" in directive


def test_a_declined_scene_does_not_continue() -> None:
    directive, error = _resume_request(_scene_messages("declined"), "selora_scene_office")
    assert directive is None
    assert "not saved" in error


def test_a_scene_with_nothing_left_does_not_continue() -> None:
    """Most scenes. "Create a movie night scene" is the whole request."""
    messages = _scene_messages()
    del messages[1]["remaining_intent"]
    directive, error = _resume_request(messages, "selora_scene_office")
    assert directive is None
    assert "No such proposal" in error


def test_the_scene_handle_is_the_id_not_a_position() -> None:
    """Accepting returns the scene_id, and message indices shift as a session
    is pruned — so the id is the only stable handle."""
    _, error = _resume_request(_scene_messages(), "some_other_scene")
    assert "No such proposal" in error
