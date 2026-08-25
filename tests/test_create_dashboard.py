"""Tests for the panel-executed dashboard creation.

Creating a dashboard ENTRY needs `lovelace/dashboards/create`, an admin-only
websocket command. The integration is not a websocket client; the panel is. So
the backend only ever PROPOSES, and the security of the whole arrangement rests
on the proposal being a closed, validated intent the panel rebuilds into a fixed
call — never a payload shaped by the model.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import (
    COMMAND_TOOL_NAMES,
    CONFIG_TOOL_NAMES,
    TOOL_MAP,
)


def _executor(hass: HomeAssistant, *, is_admin: bool = True) -> ToolExecutor:
    return ToolExecutor(hass, MagicMock(), is_admin=is_admin)


# ── Registration ────────────────────────────────────────────────────────────


def test_create_dashboard_is_registered_in_both_lanes() -> None:
    assert "create_dashboard" in TOOL_MAP
    assert "create_dashboard" in COMMAND_TOOL_NAMES
    assert "create_dashboard" in CONFIG_TOOL_NAMES


def test_create_dashboard_is_not_on_mcp() -> None:
    """It only works where a panel is connected. MCP, scheduled actions and any
    unattended run have none, so offering it there would advertise a capability
    that cannot fire."""
    from custom_components.selora_ai import mcp_server

    assert "selora_create_dashboard" not in {t.name for t in mcp_server._TOOL_DEFINITIONS}
    assert "create_dashboard" not in mcp_server._DERIVED_MCP_TOOLS.values()


def test_the_description_warns_against_claiming_success() -> None:
    """The reply is written before the panel has done anything."""
    description = TOOL_MAP["create_dashboard"].description
    assert "do NOT say the dashboard exists until the result comes back" in description


def test_no_dashboard_tool_denies_that_creating_one_is_possible() -> None:
    """The denial `add_dashboard_view` used to carry outlived the limitation.
    Left in place beside `create_dashboard` it is a flat contradiction inside
    one schema, and the model resolves it by appending a page to an unrelated
    dashboard and calling that the answer — the exact failure the denial was
    written to prevent, arrived at from the other side. No test sees this: the
    tools work, the turn succeeds, and the user gets the wrong thing."""
    for name, tool in TOOL_MAP.items():
        if "dashboard" not in name:
            continue
        text = tool.description.lower()
        assert "does not allow" not in text, name
        assert "cannot be created" not in text, name
        # Sending the user to Settings is right only where no panel can act —
        # which is create_dashboard's own out-of-panel case.
        if name != "create_dashboard":
            assert "settings > dashboards" not in text, name


def test_add_dashboard_view_points_at_the_tool_that_does_create_one() -> None:
    """Saying what it is not is not enough — the model needs the alternative
    named, or it treats the page as the closest available thing."""
    description = TOOL_MAP["add_dashboard_view"].description
    assert "A page is not a dashboard" in description
    assert "create_dashboard" in description
    # Scoped to WHICH dashboard the page lands on, never to whether a dashboard
    # was asked for. A resumed turn replays a request opening "create a new X
    # dashboard", so a guard phrased around the ask fires on the one turn whose
    # whole job is to give that dashboard its first page.
    assert "just created has no pages" in description


def test_create_dashboard_claims_the_request_by_name() -> None:
    """It lost the request to add_dashboard_view once already."""
    description = TOOL_MAP["create_dashboard"].description
    assert "THIS is the tool for" in description


def test_create_dashboard_says_to_call_it_not_to_describe_it() -> None:
    """Told only that the tool proposes and the user then taps Create, the model
    answered with prose about the dashboard it could make and called nothing —
    promising a confirmation card the user was never shown, which reads as the
    request having been carried out while nothing happened. The card IS the ask,
    which is the same rule the prompt already states for REVIEW service calls."""
    description = TOOL_MAP["create_dashboard"].description
    assert "CALL IT" in description
    assert "calling it IS how the user is asked" in description
    # And nothing in it may invite the narration that replaced the call.
    assert "Say what will be on it" not in description


def test_the_prompt_generalizes_the_card_rule_past_service_calls() -> None:
    """`execute_command` had this rule; every confirmation-carded TOOL needs it,
    because the failure is identical — asked in prose, nothing called."""
    from custom_components.selora_ai.llm_client.prompts import _SHARED_STATE_QUERY_RULES

    assert "CALL THE TOOL" in _SHARED_STATE_QUERY_RULES
    assert "promises the user a card they were never shown" in _SHARED_STATE_QUERY_RULES


# ── Proposal ────────────────────────────────────────────────────────────────


async def test_creating_nothing_yet_returns_a_closed_intent(hass: HomeAssistant) -> None:
    result = await _executor(hass).execute(
        "create_dashboard", {"title": "Kitchen", "icon": "mdi:chef-hat"}
    )

    assert result["requires_approval"] is True
    action = result["client_action"]
    assert action["kind"] == "create_dashboard"
    assert action["title"] == "Kitchen"
    assert action["url_path"] == "kitchen"
    assert action["icon"] == "mdi:chef-hat"
    # One word, so HA needs telling explicitly.
    assert action["allow_single_word"] is True


async def test_the_intent_carries_only_allowlisted_fields(hass: HomeAssistant) -> None:
    """The panel rebuilds a fixed call from these. Anything extra riding along
    would be a field the model chose reaching a privileged command.

    `remaining_intent` is deliberately NOT here: it is turn metadata that
    brings the model back after the button, and the panel executes none of it,
    so it rides on the tool result rather than the descriptor."""
    result = await _executor(hass).execute("create_dashboard", {"title": "Kitchen"})

    assert set(result["client_action"]) == {
        "kind",
        "title",
        "url_path",
        "icon",
        "require_admin",
        "show_in_sidebar",
        "allow_single_word",
        "label",
    }


async def test_a_multiword_title_needs_no_single_word_flag(hass: HomeAssistant) -> None:
    result = await _executor(hass).execute("create_dashboard", {"title": "Ground Floor"})
    action = result["client_action"]

    assert action["url_path"] == "ground-floor"
    assert action["allow_single_word"] is False


async def test_a_title_is_required(hass: HomeAssistant) -> None:
    result = await _executor(hass).execute("create_dashboard", {"title": "   "})
    assert "title is required" in result["error"]


async def test_an_unusable_url_path_is_refused(hass: HomeAssistant) -> None:
    result = await _executor(hass).execute("create_dashboard", {"title": "!!!"})
    assert "usable URL path" in result["error"]


async def test_a_colliding_url_path_is_refused_before_the_button(
    hass: HomeAssistant,
) -> None:
    """Validated here so the user is never shown a button that fails when
    pressed — HA would reject it, but only after the click."""
    from homeassistant.components.frontend import async_register_built_in_panel

    async_register_built_in_panel(hass, "custom", frontend_url_path="kitchen")

    result = await _executor(hass).execute("create_dashboard", {"title": "Kitchen"})

    assert "already uses the URL" in result["error"]


# ── Card ────────────────────────────────────────────────────────────────────


def test_the_proposal_becomes_a_client_action_card() -> None:
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    result = synthesize_approval_from_tool_log(
        {"intent": "answer", "response": "I can make that."},
        [
            {
                "tool": "create_dashboard",
                "arguments": {},
                "result": {
                    "requires_approval": True,
                    "client_action": {"kind": "create_dashboard", "title": "Kitchen"},
                },
            }
        ],
        None,
    )

    approval = result["command_approval"]
    assert result["intent"] == "command_approval"
    assert approval["approval_kind"] == "client_action"
    assert approval["client_actions"][0]["title"] == "Kitchen"
    # No server-side resolver runs for this card.
    assert approval["calls"] == []
    assert approval["deletes"] == []


@pytest.mark.parametrize(
    "descriptor",
    [
        {"kind": "run_anything", "title": "x"},
        {"title": "x"},
        "not a mapping",
    ],
    ids=["unknown-kind", "no-kind", "not-a-dict"],
)
def test_an_unrecognised_client_action_is_ignored(descriptor: Any) -> None:
    """Allowlisted at BOTH ends — the tool name and the kind — so a tool cannot
    smuggle a kind the panel would act on."""
    from custom_components.selora_ai.llm_client.command_policy import (
        _pending_client_actions_from_log,
    )

    assert not _pending_client_actions_from_log(
        [
            {
                "tool": "create_dashboard",
                "arguments": {},
                "result": {"requires_approval": True, "client_action": descriptor},
            }
        ]
    )


def test_another_tool_cannot_emit_a_client_action() -> None:
    from custom_components.selora_ai.llm_client.command_policy import (
        _pending_client_actions_from_log,
    )

    assert not _pending_client_actions_from_log(
        [
            {
                "tool": "add_dashboard_view",
                "arguments": {},
                "result": {
                    "requires_approval": True,
                    "client_action": {"kind": "create_dashboard", "title": "x"},
                },
            }
        ]
    )


@pytest.mark.parametrize(
    ("supplied", "ok"),
    [("mdi:chef-hat", True), ("chef-hat", False), ("", True), ("  ", True)],
)
async def test_the_icon_is_validated_before_the_button(
    hass: HomeAssistant, supplied: str, ok: bool
) -> None:
    """DashboardsCollection's schema uses cv.icon, so anything it rejects makes
    a Create button that always fails after the user presses it."""
    result = await _executor(hass).execute(
        "create_dashboard", {"title": "Kitchen", "icon": supplied}
    )

    if ok:
        assert result["requires_approval"] is True
    else:
        assert "not a usable icon" in result["error"]


# ── Reporting the real outcome ──────────────────────────────────────────────
#
# These drive the websocket handler itself. The store's signatures are the kind
# of thing that looks right and raises at runtime, and nothing else here calls
# it — the tool tests all stop at the proposal.


async def _session_with_proposal(
    hass: HomeAssistant, url_path: str = "kitchen", title: str = "Kitchen"
) -> tuple[Any, str, str]:
    from custom_components.selora_ai.const import DOMAIN
    from custom_components.selora_ai.conversation_store import ConversationStore

    store = ConversationStore(hass)
    hass.data.setdefault(DOMAIN, {})["_conv_store"] = store
    session_id = "sess-1"
    proposal_id = "prop-1"
    await store.append_message(session_id, "user", "make me a dashboard")
    await store.append_message(
        session_id,
        "assistant",
        "Ready when you are.",
        intent="command_approval",
        command_approval={
            "proposal_id": proposal_id,
            "approval_kind": "client_action",
            "calls": [],
            "deletes": [],
            "actions": [],
            # `url_path` as every real proposal carries it — the validated slug
            # `async_propose_dashboard` put there. The recorder reads the target
            # off this descriptor rather than off the panel's report, so a
            # fixture without it exercises only the fallback.
            "client_actions": [
                {"kind": "create_dashboard", "title": title, "url_path": url_path}
            ],
        },
    )
    return store, session_id, proposal_id


def _admin_connection() -> MagicMock:
    connection = MagicMock()
    connection.user.is_admin = True
    return connection


async def test_a_reported_success_resolves_the_card(hass: HomeAssistant) -> None:
    import inspect

    from custom_components.selora_ai.websocket import tokens

    store, session_id, proposal_id = await _session_with_proposal(hass)
    connection = _admin_connection()

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
                    "kind": "create_dashboard",
                    "detail": {"url_path": "kitchen", "title": "Kitchen"},
                }
            ],
        },
    )

    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once()
    assert connection.send_result.call_args[0][1] == {"ok": True}

    session = await store.get_session(session_id)
    # The outcome reached the transcript, and the card is resolved.
    assert "/kitchen" in session["messages"][-1]["content"]
    assert session["messages"][1]["approval_status"] == "approved"


async def test_a_reported_failure_says_what_went_wrong(hass: HomeAssistant) -> None:
    """Surfaced verbatim: usually the user's own permissions or a colliding
    url_path, and a generic message sends them looking in the wrong place."""
    import inspect

    from custom_components.selora_ai.websocket import tokens

    store, session_id, proposal_id = await _session_with_proposal(hass)
    connection = _admin_connection()

    handler = inspect.unwrap(tokens._handle_websocket_client_action_result)
    await handler(
        hass,
        connection,
        {
            "id": 1,
            "session_id": session_id,
            "proposal_id": proposal_id,
            "results": [
                {"ok": False, "kind": "create_dashboard", "detail": "url_path already exists"}
            ],
        },
    )

    assert connection.send_result.call_args[0][1] == {"ok": False}
    session = await store.get_session(session_id)
    assert "url_path already exists" in session["messages"][-1]["content"]
    assert session["messages"][1]["approval_status"] == "denied"


async def test_an_unknown_proposal_is_refused(hass: HomeAssistant) -> None:
    import inspect

    from custom_components.selora_ai.websocket import tokens

    _store, session_id, _proposal_id = await _session_with_proposal(hass)
    connection = _admin_connection()

    handler = inspect.unwrap(tokens._handle_websocket_client_action_result)
    await handler(
        hass,
        connection,
        {"id": 1, "session_id": session_id, "proposal_id": "nope", "results": [{"ok": True}]},
    )

    connection.send_error.assert_called_once()
    connection.send_result.assert_not_called()


async def test_a_non_admin_cannot_report_a_result(hass: HomeAssistant) -> None:
    import inspect

    from custom_components.selora_ai.websocket import tokens

    _store, session_id, proposal_id = await _session_with_proposal(hass)
    connection = MagicMock()
    connection.user.is_admin = False

    handler = inspect.unwrap(tokens._handle_websocket_client_action_result)
    await handler(
        hass,
        connection,
        {
            "id": 1,
            "session_id": session_id,
            "proposal_id": proposal_id,
            "results": [{"ok": True}],
        },
    )

    connection.send_error.assert_called_once()
    connection.send_result.assert_not_called()


def test_a_client_action_survives_an_explicit_approval_intent() -> None:
    """A model answering with intent "command_approval" of its own would
    otherwise have the descriptor discarded, or turned into a server-resolved
    card whose confirm button has nothing to run."""
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    result = synthesize_approval_from_tool_log(
        {"intent": "command_approval", "response": "Shall I?"},
        [
            {
                "tool": "create_dashboard",
                "arguments": {},
                "result": {
                    "requires_approval": True,
                    "client_action": {"kind": "create_dashboard", "title": "Kitchen"},
                },
            }
        ],
        None,
    )

    assert result["command_approval"]["approval_kind"] == "client_action"
    assert result["command_approval"]["client_actions"][0]["title"] == "Kitchen"


async def test_the_status_is_written_before_the_result_message(
    hass: HomeAssistant,
) -> None:
    """append_message prunes a middle message at the session cap, shifting the
    located index — the status would land on the message just appended and
    leave the card pending forever."""
    import inspect

    from custom_components.selora_ai.websocket import tokens

    store, session_id, proposal_id = await _session_with_proposal(hass)
    order: list[str] = []
    real_set, real_append = store.set_approval_status, store.append_message

    async def _set(*args: Any, **kwargs: Any) -> Any:
        order.append("status")
        return await real_set(*args, **kwargs)

    async def _append(*args: Any, **kwargs: Any) -> Any:
        order.append("append")
        return await real_append(*args, **kwargs)

    store.set_approval_status = _set  # type: ignore[method-assign]
    store.append_message = _append  # type: ignore[method-assign]

    handler = inspect.unwrap(tokens._handle_websocket_client_action_result)
    await handler(
        hass,
        _admin_connection(),
        {
            "id": 1,
            "session_id": session_id,
            "proposal_id": proposal_id,
            "results": [{"ok": True, "kind": "create_dashboard", "detail": {}}],
        },
    )

    assert order == ["status", "append"]


async def test_a_duplicate_report_is_refused(hass: HomeAssistant) -> None:
    """Two tabs, or a double click, would both pass the lookup and append
    contradictory outcomes while racing the status write."""
    import inspect

    from custom_components.selora_ai.websocket import tokens

    _store, session_id, proposal_id = await _session_with_proposal(hass)
    handler = inspect.unwrap(tokens._handle_websocket_client_action_result)
    payload = {
        "id": 1,
        "session_id": session_id,
        "proposal_id": proposal_id,
        "results": [{"ok": True, "kind": "create_dashboard", "detail": {}}],
    }

    tokens._in_flight_approvals.add(proposal_id)
    try:
        connection = _admin_connection()
        await handler(hass, connection, payload)
        connection.send_error.assert_called_once()
        assert connection.send_error.call_args[0][1] == "in_flight"
    finally:
        tokens._in_flight_approvals.discard(proposal_id)


async def test_the_guard_is_released_after_a_report(hass: HomeAssistant) -> None:
    """A guard that leaks makes the proposal unreportable forever."""
    import inspect

    from custom_components.selora_ai.websocket import tokens

    _store, session_id, proposal_id = await _session_with_proposal(hass)
    handler = inspect.unwrap(tokens._handle_websocket_client_action_result)
    await handler(
        hass,
        _admin_connection(),
        {
            "id": 1,
            "session_id": session_id,
            "proposal_id": proposal_id,
            "results": [{"ok": True, "kind": "create_dashboard", "detail": {}}],
        },
    )
    assert proposal_id not in tokens._in_flight_approvals


async def test_a_delete_card_cannot_be_resolved_by_this_endpoint(
    hass: HomeAssistant,
) -> None:
    """_find_pending_approval returns any pending proposal, so without a kind
    check an authenticated admin could mark a deletion approved and append a
    fabricated outcome for work nothing performed."""
    import inspect

    from custom_components.selora_ai.const import DOMAIN
    from custom_components.selora_ai.conversation_store import ConversationStore
    from custom_components.selora_ai.websocket import tokens

    store = ConversationStore(hass)
    hass.data.setdefault(DOMAIN, {})["_conv_store"] = store
    await store.append_message(
        "sess-1",
        "assistant",
        "Delete it?",
        intent="command_approval",
        command_approval={
            "proposal_id": "p1",
            "approval_kind": "delete",
            "calls": [],
            "deletes": [{"kind": "area", "target_id": "study", "label": "Study"}],
            "actions": [],
        },
    )
    connection = _admin_connection()

    handler = inspect.unwrap(tokens._handle_websocket_client_action_result)
    await handler(
        hass,
        connection,
        {
            "id": 1,
            "session_id": "sess-1",
            "proposal_id": "p1",
            "results": [{"ok": True, "kind": "create_dashboard", "detail": {}}],
        },
    )

    connection.send_error.assert_called_once()
    assert connection.send_error.call_args[0][1] == "wrong_kind"
    session = await store.get_session("sess-1")
    assert session["messages"][0].get("approval_status") != "approved"


async def test_results_must_answer_what_the_card_proposed(hass: HomeAssistant) -> None:
    """Otherwise the transcript records an outcome for something never offered."""
    import inspect

    from custom_components.selora_ai.websocket import tokens

    _store, session_id, proposal_id = await _session_with_proposal(hass)
    connection = _admin_connection()

    handler = inspect.unwrap(tokens._handle_websocket_client_action_result)
    await handler(
        hass,
        connection,
        {
            "id": 1,
            "session_id": session_id,
            "proposal_id": proposal_id,
            # The card proposed one create_dashboard; this claims two.
            "results": [
                {"ok": True, "kind": "create_dashboard", "detail": {}},
                {"ok": True, "kind": "create_dashboard", "detail": {}},
            ],
        },
    )

    connection.send_error.assert_called_once()
    assert connection.send_error.call_args[0][1] == "mismatch"


def test_create_dashboard_is_withheld_from_assist() -> None:
    """Assist renders no command_approval card and cannot resolve one, so the
    model would call this and produce a proposal nothing can act on. A
    description saying "panel only" does not stop that; withholding the schema
    does."""
    assert TOOL_MAP["create_dashboard"].panel_only is True


def _tool_names(**kwargs: Any) -> set[str]:
    from unittest.mock import MagicMock

    from custom_components.selora_ai.llm_client.client import LLMClient

    client = MagicMock(spec=LLMClient)
    client._provider = MagicMock(is_low_context=False)
    client._provider.format_tool = lambda t: {"name": t.name}
    return {t["name"] for t in LLMClient._get_tools_for_provider(client, **kwargs)}


def test_a_panel_only_tool_needs_the_caller_to_declare_the_panel() -> None:
    """`for_assist=False` is NOT the same question, which is what made the model
    answer a panel user with "open this in the Selora panel". An MCP chat turn
    passes `for_assist=False` and has no panel either, so the tool was offered
    on a surface that cannot execute it — leaving the model to work out from its
    own context which surface it was on, which nothing in that context says.

    So the gate is what the CALLER declares, and it defaults to withheld."""
    assert "create_dashboard" in _tool_names(panel_available=True)
    # Said nothing: withheld.
    assert "create_dashboard" not in _tool_names()
    # The old gate, on its own, is not enough — this is the MCP chat case.
    assert "create_dashboard" not in _tool_names(for_assist=False)
    assert "create_dashboard" not in _tool_names(for_assist=True)
    # A declared panel does not smuggle anything else in: every panel_only
    # tool, and nothing else.
    assert _tool_names(panel_available=True) - _tool_names() == {
        "create_dashboard",
        "delete_dashboard",
    }


def test_the_panel_chat_handlers_declare_the_panel() -> None:
    """The whole fix rests on the three panel entry points saying so — the
    handler, its correction round, and the streaming path. Miss one and the
    tool silently disappears from that path with nothing to explain why."""
    import pathlib

    source = pathlib.Path("custom_components/selora_ai/__init__.py").read_text()
    assert source.count("panel_available=True") == 3


@pytest.mark.parametrize(
    ("language", "fragment"),
    [("fr", "tableau de bord"), ("de", "Dashboard"), ("es", "panel"), (None, "Created the")],
)
async def test_the_outcome_is_written_in_the_conversation_language(
    hass: HomeAssistant, language: str | None, fragment: str
) -> None:
    """A French conversation that answers in English breaks the per-turn
    language invariant."""
    import inspect

    from custom_components.selora_ai.websocket import tokens

    store, session_id, proposal_id = await _session_with_proposal(hass, "cuisine", "Cuisine")
    payload: dict[str, Any] = {
        "id": 1,
        "session_id": session_id,
        "proposal_id": proposal_id,
        "results": [
            {
                "ok": True,
                "kind": "create_dashboard",
                "detail": {"url_path": "cuisine", "title": "Cuisine"},
            }
        ],
    }
    if language:
        payload["language"] = language

    handler = inspect.unwrap(tokens._handle_websocket_client_action_result)
    await handler(hass, _admin_connection(), payload)

    session = await store.get_session(session_id)
    assert fragment in session["messages"][-1]["content"]


async def test_a_failure_is_also_localized(hass: HomeAssistant) -> None:
    import inspect

    from custom_components.selora_ai.websocket import tokens

    store, session_id, proposal_id = await _session_with_proposal(hass)
    handler = inspect.unwrap(tokens._handle_websocket_client_action_result)
    await handler(
        hass,
        _admin_connection(),
        {
            "id": 1,
            "session_id": session_id,
            "proposal_id": proposal_id,
            "language": "fr",
            "results": [{"ok": False, "kind": "create_dashboard", "detail": "déjà pris"}],
        },
    )

    session = await store.get_session(session_id)
    assert "n'a pas fonctionné" in session["messages"][-1]["content"]


@pytest.mark.parametrize(
    "key",
    [
        "client_action_title",
        "client_action_create_dashboard",
        "client_action_confirm",
        "client_action_working",
        "client_action_done",
        "client_action_failed",
    ],
)
def test_every_client_action_key_is_in_every_catalog(key: str) -> None:
    """`_t` never warns on a missing key — it silently returns the English
    fallback, so a gap is invisible until a non-English user sees it."""
    import json
    import pathlib

    base = pathlib.Path("custom_components/selora_ai")
    locales = [p for p in (base / "translations").glob("*.json")]
    assert len(locales) == 13

    assert key in json.loads((base / "strings.json").read_text())["common"]
    for path in locales:
        catalog = json.loads(path.read_text())
        assert key in catalog.get("common", {}), f"{key} missing from {path.name}"


def _log(*tools: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for tool in tools:
        if tool == "create_dashboard":
            entries.append(
                {
                    "tool": tool,
                    "arguments": {},
                    "result": {
                        "requires_approval": True,
                        "client_action": {
                            "kind": "create_dashboard",
                            "title": "Kitchen",
                            "label": "Create the Kitchen dashboard at /kitchen",
                        },
                    },
                }
            )
        elif tool == "delete_area":
            entries.append(
                {
                    "tool": tool,
                    "arguments": {},
                    "result": {
                        "requires_approval": True,
                        "delete": {
                            "kind": "area",
                            "target_id": "study",
                            "entity_id": "",
                            "label": "Study",
                        },
                    },
                }
            )
    return entries


def test_a_delete_beside_a_client_action_is_not_lost() -> None:
    """One command_approval fits per message. A client action is a proposal, so
    deferring it loses nothing the user cannot re-request — but a request
    silently dropped is exactly the failure this section exists to avoid."""
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    result = synthesize_approval_from_tool_log(
        {"intent": "answer", "response": "Right."},
        _log("create_dashboard", "delete_area"),
        None,
    )

    # The delete card wins...
    assert result["command_approval"]["approval_kind"] == "delete"
    # ...and the deferred dashboard is named, not dropped.
    assert "Kitchen dashboard" in result["response"]
    assert "Ask me again" in result["response"]


def test_a_client_action_alone_still_gets_its_card() -> None:
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    result = synthesize_approval_from_tool_log(
        {"intent": "answer", "response": "Right."}, _log("create_dashboard"), None
    )
    assert result["command_approval"]["approval_kind"] == "client_action"


def test_the_deferral_notice_is_localized() -> None:
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    result = synthesize_approval_from_tool_log(
        {"intent": "answer", "response": "D'accord."},
        _log("create_dashboard", "delete_area"),
        None,
        language="fr",
    )
    assert "Redemandez" in result["response"]


def test_the_card_carries_the_resolved_turn_language() -> None:
    """hass.language is only the UI locale — a French message on an English-UI
    install must still get a French outcome, and only the turn knows."""
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    result = synthesize_approval_from_tool_log(
        {"intent": "answer", "response": "Bien."}, _log("create_dashboard"), None, language="fr"
    )
    assert result["command_approval"]["language"] == "fr"


@pytest.mark.parametrize(
    ("supplied", "expected_admin", "expected_sidebar"),
    [
        ({}, False, True),
        ({"require_admin": "false", "show_in_sidebar": "false"}, False, False),
        ({"require_admin": "true", "show_in_sidebar": "true"}, True, True),
        ({"require_admin": True, "show_in_sidebar": False}, True, False),
    ],
    ids=["defaults", "string-false", "string-true", "real-bools"],
)
async def test_string_booleans_do_not_invert_visibility(
    hass: HomeAssistant,
    supplied: dict[str, Any],
    expected_admin: bool,
    expected_sidebar: bool,
) -> None:
    """bool("false") is True, and some providers emit JSON-schema booleans as
    strings — which would create the dashboard with the opposite visibility."""
    result = await _executor(hass).execute("create_dashboard", {"title": "Kitchen", **supplied})

    action = result["client_action"]
    assert action["require_admin"] is expected_admin
    assert action["show_in_sidebar"] is expected_sidebar


@pytest.mark.parametrize(
    ("language", "fragment"),
    [
        ("fr", "tableau de bord"),
        ("de", "Dashboard"),
        ("es", "panel"),
        ("it", "dashboard"),
        ("nl", "dashboard"),
        ("pt", "painel"),
        ("hu", "vezérlőpultot"),
        ("ru", "Панель"),
        ("ja", "ダッシュボード"),
        ("ko", "대시보드"),
        ("zh-Hans", "仪表板"),
        ("zh-Hant", "仪表板"),
    ],
)
def test_every_supported_locale_has_a_created_line(language: str, fragment: str) -> None:
    """_normalize_lang returns these as supported and they all received frontend
    strings in this change, so a backend gap would show as one English line in
    an otherwise translated conversation."""
    from custom_components.selora_ai.llm_client.command_policy import (
        dashboard_created_line,
    )

    assert fragment in dashboard_created_line("Cuisine", "cuisine", language)


@pytest.mark.parametrize(
    "language", ["fr", "de", "es", "it", "nl", "pt", "hu", "ru", "ja", "ko", "zh-Hans"]
)
def test_every_supported_locale_has_a_failure_line(language: str) -> None:
    from custom_components.selora_ai.llm_client.command_policy import (
        action_failed_line,
    )

    line = action_failed_line("boom", language)
    assert "boom" in line
    assert "That did not work" not in line


@pytest.mark.parametrize("language", ["fr", "de", "hu", "ja", "zh-Hans"])
def test_every_supported_locale_has_a_deferral_line(language: str) -> None:
    from custom_components.selora_ai.llm_client.command_policy import (
        _deferred_client_action_line,
    )

    line = _deferred_client_action_line([{"label": "X"}], language)
    assert "Ask me again" not in line


def test_the_streamed_parser_resolves_the_turn_language(hass: HomeAssistant) -> None:
    """architect_chat_stream resolves locally and the caller passes the UI
    locale, so a French message on an English-UI install reached the
    synthesizer as "en" and every deterministic outcome came out English."""
    from unittest.mock import MagicMock, patch

    from custom_components.selora_ai.llm_client.client import LLMClient

    client = MagicMock(spec=LLMClient)
    client._hass = hass
    client._usage = MagicMock()
    client._provider = MagicMock()
    client._provider.convert_response_text = lambda t: t

    with patch("custom_components.selora_ai.llm_client.client.parse_streamed_response") as parser:
        LLMClient.parse_streamed_response(
            client,
            '{"intent": "answer", "response": "ok"}',
            user_message="Crée un tableau de bord pour la cuisine",
            language="en",
        )

    # Detected from the message, not the "en" the panel supplied.
    assert parser.call_args.kwargs["language"] == "fr"


def test_the_pending_card_does_not_claim_success() -> None:
    """The websocket call runs in the panel, if and when the button is tapped.
    The model has usually already narrated the dashboard as created by the time
    the proposal comes back, and carrying that prose through tells the user it
    exists — possibly having never tapped anything."""
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    result = synthesize_approval_from_tool_log(
        {
            "intent": "answer",
            "response": "I've created the Kitchen dashboard — you'll find it at /kitchen.",
        },
        _log("create_dashboard"),
        None,
    )

    assert result["command_approval"]["approval_kind"] == "client_action"
    # The model's claim is gone, not merely followed by a hint.
    assert "I've created" not in result["response"]
    assert "/kitchen" not in result["response"]
    assert "Nothing has been created yet" in result["response"]


def test_the_pending_wording_is_localized() -> None:
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    result = synthesize_approval_from_tool_log(
        {"intent": "answer", "response": "J'ai créé le tableau de bord."},
        _log("create_dashboard"),
        None,
        language="fr",
    )
    assert "Rien n'a encore été créé" in result["response"]


def test_an_executed_write_beside_a_client_action_is_acknowledged() -> None:
    """Overriding the response must not drop the only mention of something that
    really happened — "turn the porch light off and make me a dashboard" fires
    the light and holds the dashboard."""
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    tool_log = _log("create_dashboard")
    tool_log.insert(
        0,
        {
            "tool": "execute_command",
            "arguments": {"service": "light.turn_off", "entity_id": "light.porch"},
            "result": {
                "executed": True,
                "service": "light.turn_off",
                "entity_ids": ["light.porch"],
            },
        },
    )

    result = synthesize_approval_from_tool_log(
        {"intent": "answer", "response": "Done both."}, tool_log, None
    )

    assert result["command_approval"]["approval_kind"] == "client_action"
    # The executed light is still acknowledged...
    assert "porch" in result["response"].lower()
    # ...and the dashboard is still only pending.
    assert "Nothing has been created yet" in result["response"]
    # No entity tile: on a dashboard turn it reads as a preview of the layout
    # that was saved, and nothing has been saved at all.
    assert "[[entit" not in result["response"]


def test_an_explicit_service_approval_beside_a_client_action_wins() -> None:
    """The model's own command_approval reaches the same card slot by a
    different route. Weighing only the tool log returned the client card and
    discarded the service calls outright."""
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    result = synthesize_approval_from_tool_log(
        {
            "intent": "command_approval",
            "response": "Shall I unlock it?",
            "command_approval": {
                "calls": [{"service": "lock.unlock", "entity_id": "lock.front"}],
            },
        },
        _log("create_dashboard"),
        None,
    )

    # The real action the user asked for survives...
    assert result["command_approval"].get("approval_kind") != "client_action"
    assert result["command_approval"]["calls"][0]["service"] == "lock.unlock"
    # ...with the quick-actions that make it resolvable.
    assert any(
        str(a.get("value", "")).startswith("approve:") for a in result.get("quick_actions") or []
    )
    # ...and the deferred dashboard is named, not silently dropped.
    assert "Kitchen dashboard" in result["response"]
    assert "Ask me again" in result["response"]


def test_an_empty_explicit_approval_does_not_beat_a_client_action() -> None:
    """An approval card with nothing to run is not a competing proposal, and
    deferring to it would leave the user an unresolvable card."""
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    result = synthesize_approval_from_tool_log(
        {
            "intent": "command_approval",
            "response": "Shall I?",
            "command_approval": {"calls": []},
        },
        _log("create_dashboard"),
        None,
    )
    assert result["command_approval"]["approval_kind"] == "client_action"


@pytest.mark.parametrize(
    "language", ["fr", "de", "es", "it", "nl", "pt", "hu", "ru", "ja", "ko", "zh"]
)
def test_every_supported_locale_has_a_pending_line(language: str) -> None:
    from custom_components.selora_ai.llm_client.command_policy import (
        client_action_pending_hint,
    )

    localized = client_action_pending_hint(language)
    assert localized
    assert localized != client_action_pending_hint("en")


def test_a_command_beside_a_client_action_is_not_dropped() -> None:
    """A command intent reaches its own return further down, so a client action
    selected ahead of it took the card slot AND emptied `calls` — losing a
    device command the user asked for in the same breath."""
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    result = synthesize_approval_from_tool_log(
        {
            "intent": "command",
            "response": "Porch light off.",
            "calls": [{"service": "light.turn_off", "entity_id": "light.porch"}],
        },
        _log("create_dashboard"),
        None,
    )

    # The command survives with its calls...
    assert result["intent"] == "command"
    assert result["calls"][0]["service"] == "light.turn_off"
    # ...and the deferred dashboard is named, not silently dropped.
    assert "Kitchen dashboard" in result["response"]
    assert "Ask me again" in result["response"]


def test_a_callless_command_does_not_beat_a_client_action() -> None:
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    result = synthesize_approval_from_tool_log(
        {"intent": "command", "response": "Right.", "calls": []},
        _log("create_dashboard"),
        None,
    )
    assert result["command_approval"]["approval_kind"] == "client_action"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Кухня", "kukhnia"),
        ("厨房", "chu-fang"),
        ("Café", "cafe"),
        ("Küche Öl", "kuche-ol"),
        ("My Kitchen", "my-kitchen"),
    ],
)
async def test_a_title_in_the_users_own_script_still_gets_a_path(
    hass: HomeAssistant, title: str, expected: str
) -> None:
    """13 locales ship. A title with no ASCII in it reduced to an empty slug and
    was refused as having no usable URL path, and an accented one silently lost
    its accented letters."""
    from custom_components.selora_ai.dashboard_manager import async_propose_dashboard

    result = await async_propose_dashboard(hass, title=title)

    assert "error" not in result, result
    assert result["client_action"]["url_path"] == expected
    # The title itself is untouched — only the path is transliterated.
    assert result["client_action"]["title"] == title


async def test_a_title_with_nothing_sluggable_is_still_refused(hass: HomeAssistant) -> None:
    """HA's slugify substitutes the literal "unknown" when nothing survives, so
    this would quietly land at /unknown — and the next one would collide."""
    from custom_components.selora_ai.dashboard_manager import async_propose_dashboard

    result = await async_propose_dashboard(hass, title="!!!")

    assert "error" in result
    assert "usable URL path" in result["error"]


# ── A created dashboard has to be usable ────────────────────────────────────


async def _new_entry(
    hass: HomeAssistant, url_path: str = "office", *, require_admin: bool = False
) -> Any:
    """Register a dashboard entry the way `lovelace/dashboards/create` does:
    the ENTRY exists and no document is stored against it."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA
    from homeassistant.components.lovelace.dashboard import LovelaceStorage
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "lovelace", {"lovelace": {"mode": "storage"}})
    await hass.async_block_till_done()

    data = hass.data[LOVELACE_DATA]
    data.dashboards[url_path] = LovelaceStorage(
        hass,
        {
            "id": url_path,
            "url_path": url_path,
            "title": "Office",
            "mode": "storage",
            "require_admin": require_admin,
            "show_in_sidebar": True,
            "icon": None,
        },
    )
    return data


async def test_a_dashboard_with_no_document_reads_as_auto_generated(
    hass: HomeAssistant,
) -> None:
    """Why the seeding exists. Storage cannot tell a brand-new dashboard from a
    generated Overview — both have no stored config — so every write to the
    dashboard we were just asked to create is refused."""
    data = await _new_entry(hass)

    assert (await data.dashboards["office"].async_get_info())["mode"] == "auto-gen"


async def test_a_created_dashboard_can_be_filled(hass: HomeAssistant) -> None:
    """The point of creating one. Without the seed every one of these comes back
    refused with the Take control note, on a dashboard Selora just made."""
    from custom_components.selora_ai.dashboard_manager import (
        async_initialize_created_dashboard,
    )

    await _new_entry(hass)
    hass.states.async_set("light.office", "on")

    assert await async_initialize_created_dashboard(hass, "office") is True

    executor = _executor(hass)
    listed = await executor.execute("list_dashboards", {})
    assert [d for d in listed["dashboards"] if d["url_path"] == "office"][0]["editable"] is True

    added = await executor.execute(
        "add_dashboard_view", {"dashboard_target": "office", "title": "Lights"}
    )
    assert added.get("status") == "created", added

    placed = await executor.execute(
        "insert_dashboard_card",
        {
            "dashboard_target": "office",
            "card": {"type": "entities", "entities": ["light.office"]},
        },
    )
    assert placed.get("ok") is True, placed


async def test_seeding_never_overwrites_a_document(hass: HomeAssistant) -> None:
    """A re-report — the panel retries, or the card is tapped after a reload —
    must not blank a dashboard that has since been filled."""
    from custom_components.selora_ai.dashboard_manager import (
        async_initialize_created_dashboard,
    )

    data = await _new_entry(hass)
    await data.dashboards["office"].async_save({"views": [{"title": "Lights", "cards": []}]})

    assert await async_initialize_created_dashboard(hass, "office") is False

    kept = await data.dashboards["office"].async_load(False)
    assert kept["views"][0]["title"] == "Lights"


async def test_an_admin_only_dashboard_is_seeded_through_the_handler(
    hass: HomeAssistant,
) -> None:
    """The panel's leg is over by the time the report arrives, so the scope the
    ToolExecutor opened is gone and CALLER_IS_ADMIN is back to its
    deny-by-default False — under which `_writable_dashboard` reports a
    require_admin dashboard as ABSENT. Without the handler re-establishing the
    caller, the seed is skipped for exactly the dashboards the user asked to be
    admin-only, and every later write to one is refused with the Take control
    note. Driven through the handler, because the wrapper is what is under
    test."""
    import inspect

    from custom_components.selora_ai.websocket import tokens

    data = await _new_entry(hass, require_admin=True)
    _store, session_id, proposal_id = await _session_with_proposal(hass, "office", "Office")

    handler = inspect.unwrap(tokens._handle_websocket_client_action_result)
    await handler(
        hass,
        _admin_connection(),
        {
            "id": 1,
            "session_id": session_id,
            "proposal_id": proposal_id,
            "results": [
                {
                    "ok": True,
                    "kind": "create_dashboard",
                    "detail": {"url_path": "office", "title": "Office"},
                }
            ],
        },
    )

    # A document exists, so the next request can fill it.
    assert await data.dashboards["office"].async_load(False) == {"views": []}
    assert (await data.dashboards["office"].async_get_info())["mode"] == "storage"


async def test_the_report_cannot_seed_a_dashboard_it_did_not_propose(
    hass: HomeAssistant,
) -> None:
    """Which dashboard an outcome is about comes from the stored descriptor, not
    from the report. The seed writes an empty document into any dashboard whose
    config is unset — and a GENERATED Overview is exactly that state, so a
    report naming it would replace the page the user can see with a blank one.
    The panel is trusted to say whether its work succeeded, never what it was
    done to."""
    import inspect

    from homeassistant.components.lovelace.const import LOVELACE_DATA

    from custom_components.selora_ai.websocket import tokens

    await _new_entry(hass)
    default = hass.data[LOVELACE_DATA].dashboards[None]
    assert (await default.async_get_info())["mode"] == "auto-gen"

    store, session_id, proposal_id = await _session_with_proposal(hass, "office", "Office")
    connection = _admin_connection()

    handler = inspect.unwrap(tokens._handle_websocket_client_action_result)
    await handler(
        hass,
        connection,
        {
            "id": 1,
            "session_id": session_id,
            "proposal_id": proposal_id,
            # The card proposed /office; this claims the default Overview.
            "results": [
                {
                    "ok": True,
                    "kind": "create_dashboard",
                    "detail": {"url_path": "lovelace", "title": "Overview"},
                }
            ],
        },
    )

    # Untouched: still generated, still whatever HA renders for it.
    assert (await default.async_get_info())["mode"] == "auto-gen"
    # And recorded as the failure it is, rather than as a dashboard created.
    assert connection.send_result.call_args[0][1] == {"ok": False}
    session = await store.get_session(session_id)
    assert session["messages"][1]["approval_status"] == "denied"
    assert "lovelace" in session["messages"][-1]["content"]


async def test_the_outcome_line_names_the_dashboard_the_card_proposed(
    hass: HomeAssistant,
) -> None:
    """The transcript is written from the descriptor too. A report is free to
    carry a title, and a wrong one would have the reply announce a dashboard
    under a name that exists nowhere."""
    import inspect

    from custom_components.selora_ai.websocket import tokens

    await _new_entry(hass)
    store, session_id, proposal_id = await _session_with_proposal(hass, "office", "Office")

    handler = inspect.unwrap(tokens._handle_websocket_client_action_result)
    await handler(
        hass,
        _admin_connection(),
        {
            "id": 1,
            "session_id": session_id,
            "proposal_id": proposal_id,
            "results": [
                {
                    "ok": True,
                    "kind": "create_dashboard",
                    # Same dashboard, a title nobody proposed.
                    "detail": {"url_path": "office", "title": "Something Else"},
                }
            ],
        },
    )

    text = (await store.get_session(session_id))["messages"][-1]["content"]
    assert "Office" in text
    assert "Something Else" not in text


async def test_seeding_an_unknown_dashboard_is_not_fatal(hass: HomeAssistant) -> None:
    """The create itself succeeded. A missing entry is worth a debug line, not a
    failed report."""
    from custom_components.selora_ai.dashboard_manager import (
        async_initialize_created_dashboard,
    )

    await _new_entry(hass)
    assert await async_initialize_created_dashboard(hass, "nope") is False


async def test_the_created_dashboard_reply_carries_its_card(hass: HomeAssistant) -> None:
    """The one reply announcing a brand-new dashboard is written by the report
    handler, not the synthesizer — the panel did the work and no LLM turn
    produced the line — so it would have been the only dashboard answer with no
    way in."""
    import inspect

    from custom_components.selora_ai.websocket import tokens

    await _new_entry(hass)
    store, session_id, proposal_id = await _session_with_proposal(hass, "office", "Office")
    connection = _admin_connection()

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
                    "kind": "create_dashboard",
                    "detail": {"url_path": "office", "title": "Office"},
                }
            ],
        },
    )

    text = (await store.get_session(session_id))["messages"][-1]["content"]
    assert "[[dashboard:/office|Office]]" in text


def test_the_card_marker_cannot_be_broken_out_of() -> None:
    """The title is whatever the user named the dashboard."""
    from custom_components.selora_ai.websocket.tokens import _dashboard_card_marker

    assert _dashboard_card_marker("office", "Off]ice|X") == "[[dashboard:/office|OfficeX]]"
    # No title is not a broken marker.
    assert _dashboard_card_marker("office", "") == "[[dashboard:/office]]"


def test_the_card_is_the_ask_rule_covers_proposal_blocks_too() -> None:
    """An automation and a scene are not tools — you create one by emitting its
    JSON block, and the card that block renders is how the user is asked. The
    rule was written for tools, so it never reached them, and the model
    answered "I can create the scene, then add a card for it" with no block
    attached: nothing created, nothing offered, the user waiting for a card
    that was not coming."""
    from custom_components.selora_ai.llm_client.prompts import _SHARED_STATE_QUERY_RULES

    assert "EMIT THE BLOCK" in _SHARED_STATE_QUERY_RULES
    # Named so the rule cannot be read as covering only the tool case.
    assert "PROPOSAL BLOCKS" in _SHARED_STATE_QUERY_RULES
    assert "scene" in _SHARED_STATE_QUERY_RULES


# ── Deleting a dashboard ────────────────────────────────────────────────────


async def test_delete_dashboard_proposes_and_names_the_blast_radius(
    hass: HomeAssistant,
) -> None:
    """HA deletes the dashboard and its stored document together, so every view
    and card goes with it. A count is the difference between an informed
    confirmation and a surprised one."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    data = await _new_entry(hass)
    hass.states.async_set("light.office", "on")
    await data.dashboards["office"].async_save(
        {
            "views": [
                {"title": "One", "cards": [{"type": "tile", "entity": "light.office"}]},
                {"title": "Two", "cards": []},
            ]
        }
    )
    assert LOVELACE_DATA in hass.data

    result = await _executor(hass).execute("delete_dashboard", {"dashboard_target": "office"})

    assert result["requires_approval"] is True
    action = result["client_action"]
    assert action["kind"] == "delete_dashboard"
    assert action["url_path"] == "office"
    assert action["view_count"] == 2
    assert action["card_count"] == 1


async def test_the_default_dashboard_cannot_be_deleted(hass: HomeAssistant) -> None:
    """Home Assistant always keeps one, so the card would fail when pressed."""
    await _new_entry(hass)
    for target in ("lovelace", ""):
        result = await _executor(hass).execute("delete_dashboard", {"dashboard_target": target})
        assert "error" in result, target
        assert "default dashboard" in result["error"]


async def test_a_dashboard_merely_named_default_is_deletable(hass: HomeAssistant) -> None:
    """`/default` is a URL path a user can genuinely have — HA allows
    single-word paths and this module's own create tool makes them. Reserving
    the STRING made that dashboard undeletable while telling its owner it was
    the built-in Overview."""
    await _new_entry(hass, "default")

    result = await _executor(hass).execute("delete_dashboard", {"dashboard_target": "default"})

    assert "error" not in result, result
    assert result["client_action"]["url_path"] == "default"


async def test_a_card_that_is_not_a_mapping_still_counts(hass: HomeAssistant) -> None:
    """Lovelace storage is free-form, so a stored card need not be a dict. It
    has no tree to walk, but the delete removes it all the same — filtering it
    out reported a dashboard holding one as having no cards at all."""
    data = await _new_entry(hass)
    await data.dashboards["office"].async_save(
        {"views": [{"title": "One", "cards": ["not a mapping", {"type": "markdown"}]}]}
    )

    result = await _executor(hass).execute("delete_dashboard", {"dashboard_target": "office"})

    assert result["client_action"]["card_count"] == 2


async def test_the_delete_card_fingerprints_the_dashboard(hass: HomeAssistant) -> None:
    """The id is not immutable either: HA derives it from the url_path, so
    deleting a dashboard frees BOTH handles the panel matches on. A new one at
    the same path inherits them, and only the metadata tells the two apart."""
    await _new_entry(hass)

    result = await _executor(hass).execute("delete_dashboard", {"dashboard_target": "office"})

    assert result["client_action"]["expected"] == {
        "title": "Office",
        "icon": "",
        "require_admin": False,
        "show_in_sidebar": True,
    }


async def test_a_yaml_dashboard_is_refused_with_the_reason(hass: HomeAssistant) -> None:
    """It lives in configuration.yaml; the websocket delete cannot touch it."""
    from unittest.mock import patch

    from homeassistant.components.lovelace.const import MODE_YAML

    data = await _new_entry(hass)
    with patch.object(type(data.dashboards["office"]), "mode", MODE_YAML):
        result = await _executor(hass).execute("delete_dashboard", {"dashboard_target": "office"})
    assert "YAML-mode" in result["error"]


async def test_an_unreadable_dashboard_can_still_be_deleted(hass: HomeAssistant) -> None:
    """The document is read for the label only. An auto-generated board cannot
    be loaded, and refusing to delete it would leave the user stuck with a
    dashboard nothing here can remove."""
    await _new_entry(hass)  # no document saved → reads as auto-gen

    result = await _executor(hass).execute("delete_dashboard", {"dashboard_target": "office"})

    assert result["requires_approval"] is True


def test_delete_dashboard_is_a_client_action_and_panel_only() -> None:
    """Removing the ENTRY needs DashboardsCollection, which lovelace publishes
    only to its admin-only websocket commands — the same wall creation hits."""
    from custom_components.selora_ai.llm_client.command_policy import (
        _CLIENT_ACTION_KINDS,
        _CLIENT_ACTION_TOOLS,
    )

    assert TOOL_MAP["delete_dashboard"].panel_only is True
    assert TOOL_MAP["delete_dashboard"].requires_admin is True
    # Both allowlists, or the card is built and then dropped.
    assert "delete_dashboard" in _CLIENT_ACTION_TOOLS
    assert "delete_dashboard" in _CLIENT_ACTION_KINDS


def test_delete_dashboard_is_not_on_mcp() -> None:
    """Like create, it only works where a panel is connected."""
    from custom_components.selora_ai import mcp_server

    assert "selora_delete_dashboard" not in {t.name for t in mcp_server._TOOL_DEFINITIONS}


async def test_the_delete_card_carries_the_collection_id(hass: HomeAssistant) -> None:
    """A url_path is reusable. Delete a dashboard, make another at the same
    path before the card is tapped, and a card approved for the first would
    remove the second. The id is what HA deletes by and the only stable handle."""
    await _new_entry(hass)

    result = await _executor(hass).execute("delete_dashboard", {"dashboard_target": "office"})

    assert result["client_action"]["dashboard_id"] == "office"
    assert result["client_action"]["url_path"] == "office"


def test_a_delete_card_does_not_say_nothing_was_created() -> None:
    """The pending line negates COMPLETION, which is its whole job — so it has
    to name the right verb. "Nothing has been created yet" above a Delete
    button describes the opposite of what is about to happen."""
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    result = synthesize_approval_from_tool_log(
        {"intent": "answer", "response": "I have deleted the Office dashboard."},
        [
            {
                "tool": "delete_dashboard",
                "arguments": {},
                "result": {
                    "requires_approval": True,
                    "client_action": {
                        "kind": "delete_dashboard",
                        "url_path": "office",
                        "title": "Office",
                    },
                },
            }
        ],
        None,
    )

    text = result["response"]
    assert "deleted yet" in text
    assert "created" not in text
    # And the premature claim is still gone.
    assert "I have deleted" not in text


def test_a_create_card_still_says_created() -> None:
    from custom_components.selora_ai.llm_client.command_policy import (
        _pending_hint_for,
    )

    assert "created" in _pending_hint_for([{"kind": "create_dashboard"}], None)
    assert "deleted" in _pending_hint_for([{"kind": "delete_dashboard"}], None)
    # A mixed card falls back to the general wording rather than picking one.
    mixed = _pending_hint_for([{"kind": "create_dashboard"}, {"kind": "delete_dashboard"}], None)
    assert "created" in mixed


@pytest.mark.parametrize(
    "language", ["fr", "de", "es", "it", "nl", "pt", "hu", "ru", "ja", "ko", "zh"]
)
def test_every_locale_has_a_delete_pending_line(language: str) -> None:
    from custom_components.selora_ai.llm_client.command_policy import (
        _pending_hint_for,
    )

    localized = _pending_hint_for([{"kind": "delete_dashboard"}], language)
    assert localized
    assert localized != _pending_hint_for([{"kind": "delete_dashboard"}], "en")


async def test_an_unreadable_dashboard_reports_unknown_not_empty(
    hass: HomeAssistant,
) -> None:
    """`_load_or_reason` returns nothing precisely when the dashboard renders
    content we cannot enumerate — a generated Overview is covered in cards. So
    "0 views, 0 cards" on an IRREVERSIBLE delete would be a false blast radius,
    and a false one invites the tap. The counts are omitted instead."""
    await _new_entry(hass)

    action = (await _executor(hass).execute("delete_dashboard", {"dashboard_target": "office"}))[
        "client_action"
    ]

    assert "view_count" not in action
    assert "card_count" not in action


async def test_the_delete_count_includes_cards_inside_containers(
    hass: HomeAssistant,
) -> None:
    """A grid holding two tiles is ONE addressable card and all of it goes with
    the dashboard. `_flat_cards` yields what a card index can name, which is
    the wrong question for a blast radius."""
    data = await _new_entry(hass)
    hass.states.async_set("light.office", "on")
    await data.dashboards["office"].async_save(
        {
            "views": [
                {
                    "title": "One",
                    "cards": [
                        {
                            "type": "grid",
                            "cards": [
                                {"type": "tile", "entity": "light.office"},
                                {"type": "tile", "entity": "light.office"},
                            ],
                        },
                        {"type": "markdown", "content": "hi"},
                    ],
                }
            ]
        }
    )

    action = (await _executor(hass).execute("delete_dashboard", {"dashboard_target": "office"}))[
        "client_action"
    ]

    assert action["view_count"] == 1
    # The grid, its two tiles, and the markdown card.
    assert action["card_count"] == 4
