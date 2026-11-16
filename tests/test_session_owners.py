"""Each conversation belongs to the user who started it.

Every panel admin shares one ``ConversationStore``, so ownership is what keeps
one person's chats out of another's sidebar, and an MCP client's conversations
apart from the panel's.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.conversation_store import (
    _SESSION_MAX_COUNT,
    ConversationStore,
    mcp_session_owner,
)
from custom_components.selora_ai.helpers import caller_scope, get_automation_store
from custom_components.selora_ai.mcp_server.chat import _tool_chat, _tool_list_sessions
from custom_components.selora_ai.websocket import automations as automations_ws
from custom_components.selora_ai.websocket import sessions as sessions_ws

PACKAGE = Path(__file__).parent.parent / "custom_components" / "selora_ai"

ALICE = "user-alice"
BOB = "user-bob"


def _store(hass: HomeAssistant) -> ConversationStore:
    store = ConversationStore(hass)
    hass.data.setdefault(DOMAIN, {})["_conv_store"] = store
    return store


def _connection(user_id: str) -> MagicMock:
    connection = MagicMock()
    connection.user.is_admin = True
    connection.user.id = user_id
    return connection


async def _call(hass: HomeAssistant, handler: Any, user_id: str, **fields: Any) -> MagicMock:
    """Run a websocket handler as ``user_id``; returns the connection."""
    connection = _connection(user_id)
    # HA stores False for a command whose schema holds nothing but its type.
    schema = handler._ws_schema
    msg = schema({"id": 1, **fields}) if schema else {"id": 1, **fields}
    await inspect.unwrap(handler)(hass, connection, msg)
    return connection


async def _conversation(store: ConversationStore, owner: str, text: str) -> str:
    session = await store.create_session(owner=owner)
    await store.append_message(session["id"], "user", text)
    return session["id"]


# ── The panel ────────────────────────────────────────────────────────


async def test_each_user_lists_only_their_own_conversations(hass: HomeAssistant) -> None:
    store = _store(hass)
    alices = await _conversation(store, ALICE, "turn on the porch light")
    bobs = await _conversation(store, BOB, "when does my alarm ring")

    connection = await _call(
        hass, sessions_ws._handle_websocket_get_sessions, ALICE, type="selora_ai/get_sessions"
    )

    listed = [s["id"] for s in connection.send_result.call_args[0][1]]
    assert listed == [alices]
    assert bobs not in listed


@pytest.mark.parametrize(
    ("handler", "fields"),
    [
        (sessions_ws._handle_websocket_get_session, {"type": "selora_ai/get_session"}),
        (
            sessions_ws._handle_websocket_rename_session,
            {"type": "selora_ai/rename_session", "title": "mine now"},
        ),
        (sessions_ws._handle_websocket_delete_session, {"type": "selora_ai/delete_session"}),
    ],
)
async def test_another_users_conversation_is_not_found(
    hass: HomeAssistant, handler: Any, fields: dict[str, Any]
) -> None:
    """Not found rather than forbidden: it is not there for this user."""
    store = _store(hass)
    bobs = await _conversation(store, BOB, "when does my alarm ring")

    connection = await _call(hass, handler, ALICE, session_id=bobs, **fields)

    connection.send_result.assert_not_called()
    assert connection.send_error.call_args[0][1] == "not_found"
    session = await store.get_session(bobs)
    assert session is not None
    assert session["title"] == "when does my alarm ring"


async def test_a_chat_cannot_continue_another_users_conversation(hass: HomeAssistant) -> None:
    from tests.chat_harness import ChatHarness

    harness = await ChatHarness.create(hass)
    bobs = await _conversation(harness.store, BOB, "when does my alarm ring")

    turn = await harness.chat(
        "what did I ask?", reply={"intent": "answer", "response": "x"}, session_id=bobs
    )

    assert turn.errors == [("not_found", "Session not found")]
    assert turn.architect_calls == []
    session = await harness.store.get_session(bobs)
    assert session is not None
    assert len(session["messages"]) == 1


async def test_a_new_conversation_belongs_to_its_creator(hass: HomeAssistant) -> None:
    store = _store(hass)

    connection = await _call(
        hass, sessions_ws._handle_websocket_new_session, ALICE, type="selora_ai/new_session"
    )
    session_id = connection.send_result.call_args[0][1]["session_id"]
    await store.append_message(session_id, "user", "hello")

    assert [s["id"] for s in await store.list_sessions(ALICE)] == [session_id]
    assert await store.list_sessions(BOB) == []


async def test_an_automations_history_hides_another_users_conversation(
    hass: HomeAssistant,
) -> None:
    """The lineage names the conversation that wrote each version; another
    user's is left out like a version that came from no conversation."""
    store = _store(hass)
    bobs = await _conversation(store, BOB, "make the hallway light follow motion")
    alices = await _conversation(store, ALICE, "and dim it after 10pm")
    automation_store = get_automation_store(hass)
    await automation_store.add_version("selora_ai_x", "alias: x", {}, "created", bobs)
    await automation_store.add_version("selora_ai_x", "alias: x", {}, "dimmed", alices)

    connection = await _call(
        hass,
        automations_ws._handle_websocket_get_automation_lineage,
        ALICE,
        type="selora_ai/get_automation_lineage",
        automation_id="selora_ai_x",
    )

    lineage = connection.send_result.call_args[0][1]
    assert [e["session_id"] for e in lineage] == [None, alices]
    assert [e["session_preview"] for e in lineage] == [None, "and dim it after 10pm"]


# ── Sessions stored before owners ────────────────────────────────────


async def test_older_conversations_belong_to_the_home_owner(
    hass: HomeAssistant, hass_owner_user: Any, hass_admin_user: Any
) -> None:
    store = _store(hass)
    await store._ensure_loaded()
    assert store._data is not None
    store._data["sessions"]["old"] = {
        "id": "old",
        "title": "from before",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "messages": [{"role": "user", "content": "from before", "timestamp": ""}],
    }

    assert [s["id"] for s in await store.list_sessions(hass_owner_user.id)] == ["old"]
    assert await store.list_sessions(hass_admin_user.id) == []
    assert await store.belongs_to_other("old", hass_admin_user.id)


async def test_older_conversations_are_never_shared_while_the_store_loads(
    hass: HomeAssistant, hass_owner_user: Any, hass_admin_user: Any
) -> None:
    """A request arriving while the first one awaits the owner lookup must not
    see the loaded sessions without it."""
    old = {
        "id": "old",
        "title": "from before",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "messages": [{"role": "user", "content": "from before", "timestamp": ""}],
    }
    store = _store(hass)
    release = asyncio.Event()
    looking_up = asyncio.Event()

    async def _load() -> dict[str, Any]:
        return {"sessions": {"old": old}}

    async def _slow_owner(_hass: HomeAssistant) -> str:
        looking_up.set()
        await release.wait()
        return hass_owner_user.id

    with (
        patch.object(store._store, "async_load", _load),
        patch("custom_components.selora_ai.conversation_store._ha_owner_id", _slow_owner),
    ):
        first = asyncio.create_task(store.list_sessions(hass_owner_user.id))
        await looking_up.wait()
        second = asyncio.create_task(store.list_sessions(hass_admin_user.id))
        await asyncio.sleep(0)
        release.set()
        owners, admins = await asyncio.gather(first, second)

    assert [s["id"] for s in owners] == ["old"]
    assert admins == []


# ── A turn in flight ─────────────────────────────────────────────────


async def test_a_conversation_deleted_mid_turn_comes_back_to_its_owner(
    hass: HomeAssistant, hass_owner_user: Any
) -> None:
    """The reply of a turn in flight recreates the session it was answering;
    it stays with the user who asked, not the HA owner."""
    store = _store(hass)
    alices = await _conversation(store, ALICE, "turn on the porch light")
    await store.delete_session(alices)

    await store.append_message(alices, "assistant", "Done.")

    assert [s["id"] for s in await store.list_sessions(ALICE)] == [alices]
    assert await store.list_sessions(hass_owner_user.id) == []


# ── The session cap ──────────────────────────────────────────────────


async def test_one_users_chatting_never_evicts_anothers_conversations(
    hass: HomeAssistant,
) -> None:
    store = _store(hass)
    bobs = await _conversation(store, BOB, "when does my alarm ring")
    for i in range(_SESSION_MAX_COUNT + 1):
        await _conversation(store, ALICE, f"question {i}")

    assert len(await store.list_sessions(ALICE)) == _SESSION_MAX_COUNT
    assert [s["id"] for s in await store.list_sessions(BOB)] == [bobs]


# ── MCP ──────────────────────────────────────────────────────────────


async def test_mcp_lists_only_the_callers_own_conversations(hass: HomeAssistant) -> None:
    """Even a client holding Alice's own token does not see her panel chats."""
    store = _store(hass)
    await _conversation(store, ALICE, "panel chat")
    agents = await _conversation(store, mcp_session_owner(ALICE), "agent chat")
    await _conversation(store, mcp_session_owner("mcp_token:other"), "other agent")

    with caller_scope(True, caller_id=ALICE):
        listed = await _tool_list_sessions(hass)

    assert [s["session_id"] for s in listed] == [agents]


async def test_mcp_cannot_continue_a_panel_conversation(hass: HomeAssistant) -> None:
    store = _store(hass)
    panel = await _conversation(store, ALICE, "panel chat")

    with (
        caller_scope(True, caller_id=ALICE),
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setattr("custom_components.selora_ai.mcp_server.chat._get_llm", lambda _h: MagicMock())
        result = await _tool_chat(hass, {"message": "what did I ask?", "session_id": panel})

    assert "not found" in result["error"]
    session = await store.get_session(panel)
    assert session is not None
    assert len(session["messages"]) == 1


async def test_mcp_conversations_need_a_caller(hass: HomeAssistant) -> None:
    store = _store(hass)
    await _conversation(store, mcp_session_owner(ALICE), "agent chat")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("custom_components.selora_ai.mcp_server.chat._get_llm", lambda _h: MagicMock())
        result = await _tool_chat(hass, {"message": "hello"})

    assert "error" in result
    assert await _tool_list_sessions(hass) == []


# ── Every entry point is guarded ─────────────────────────────────────


def _handlers_taking_a_session_id() -> list[tuple[str, ast.AsyncFunctionDef]]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if '"session_id"' not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            decorators = "".join(ast.unparse(d) for d in node.decorator_list)
            if "websocket_command" in decorators and "'session_id'" in decorators:
                found.append((f"{path.relative_to(PACKAGE)}:{node.name}", node))
    return found


def test_every_websocket_command_taking_a_session_id_checks_its_owner() -> None:
    """A new handler that reads or writes a session by id must refuse another
    user's — the one check that keeps conversations private."""
    handlers = _handlers_taking_a_session_id()
    assert len(handlers) >= 20, "the scan stopped finding the handlers"
    unguarded = [
        name
        for name, node in handlers
        if not any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "_require_session_owner"
            for call in ast.walk(node)
        )
    ]
    assert unguarded == []
