"""Drive the chat websocket handlers end to end.

`selora_ai/chat` and `selora_ai/chat_stream` are where a turn's context is
assembled, the LLM is called, the reply is parsed, side effects are applied, and
the result is both persisted and sent to the panel. Every one of those steps has
produced a bug that unit tests on the surrounding helpers could not see: a
helper returning the right value proves nothing about a handler that forgets to
pass it, persist it, or send it.

What this harness stubs is exactly one thing — the provider round trip.
`architect_chat` / `architect_chat_stream` are replaced with scripted results,
and everything else is real: the handler, the real `LLMClient` (so the streaming
path runs the real `parse_streamed_response` over the scripted text), a real
`ConversationStore` writing to HA's test store, real `automations.yaml` on disk.
Assertions can therefore cover the whole seam: what the LLM was ASKED (recorded
call kwargs), what the panel was TOLD (the result / `done` payload), and what
was KEPT (the persisted session).

Usage::

    harness = await ChatHarness.create(hass)
    turn = await harness.chat("make me an automation", reply={...})
    assert turn.done["refining_automation_id"] == "selora_ai_aaa"
    assert turn.asked["automation_context"] is None
    assert (await harness.messages())[-1]["automation_status"] == "pending"

The two entry points mirror each other: :meth:`chat` scripts the parsed reply
`architect_chat` returns; :meth:`stream` scripts the raw text the provider
streams and lets the real parser derive the reply from it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import yaml

from custom_components.selora_ai import (
    _handle_websocket_chat,
    _handle_websocket_chat_stream,
)
from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.conversation_store import ConversationStore
from custom_components.selora_ai.llm_client.client import LLMClient

# The registered handlers are wrapped by @async_response (a sync callback that
# schedules the coroutine); __wrapped__ is the underlying awaitable.
_CHAT = _handle_websocket_chat.__wrapped__
_CHAT_STREAM = _handle_websocket_chat_stream.__wrapped__

# Driving __wrapped__ skips the @websocket_command voluptuous schema HA applies
# to every real message, so the harness re-applies it: a payload carrying a key
# the schema rejects has to fail here rather than only in production.
_SCHEMAS = {
    _CHAT: _handle_websocket_chat._ws_schema,
    _CHAT_STREAM: _handle_websocket_chat_stream._ws_schema,
}


class FakeConnection:
    """Collects everything a handler sends, in order."""

    def __init__(self, *, is_admin: bool = True) -> None:
        self.user = SimpleNamespace(is_admin=is_admin, id="test-user")
        self.results: list[dict[str, Any]] = []
        self.errors: list[tuple[str, str]] = []
        self.messages: list[dict[str, Any]] = []
        # The streaming handler registers a cancel callback here so the panel's
        # stop button can kill the turn mid-stream. Calling it is how a test
        # exercises that path.
        self.subscriptions: dict[int, Any] = {}
        # Set to raise on the next send, to exercise the client-gone paths.
        self.reset_after: int | None = None

    def send_result(self, msg_id: int, payload: dict[str, Any] | None = None) -> None:
        self.results.append(payload if payload is not None else {})

    def send_error(self, msg_id: int, code: str, message: str) -> None:
        self.errors.append((code, message))

    def send_message(self, message: dict[str, Any]) -> None:
        if self.reset_after is not None and len(self.messages) >= self.reset_after:
            raise ConnectionResetError("Cannot write to closing transport")
        self.messages.append(message)


@dataclass
class ChatTurn:
    """One handler invocation: what it was asked, told, and kept."""

    session_id: str
    connection: FakeConnection
    architect_calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def asked(self) -> dict[str, Any]:
        """The kwargs of the turn's first LLM call.

        Where "did the handler build the context it was supposed to" is
        answered — `automation_context`, `refining_context`, `history`.
        """
        assert self.architect_calls, "the handler never called the LLM"
        return self.architect_calls[0]

    @property
    def events(self) -> list[dict[str, Any]]:
        """Every websocket event, unwrapped to its payload."""
        return [m.get("event", m) for m in self.connection.messages]

    @property
    def done(self) -> dict[str, Any]:
        """The turn's terminal payload: the ``done`` event, or the result.

        One accessor for both handlers on purpose — the streaming path ends in
        an event and the non-streaming one in a result, but they carry the same
        keys and the panel reads them the same way, so a test for either should
        not have to know which it drove.
        """
        for payload in reversed(self.events):
            if payload.get("type") == "done":
                return payload
        assert self.connection.results, "the handler produced neither a done event nor a result"
        return self.connection.results[-1]

    @property
    def errors(self) -> list[tuple[str, str]]:
        return self.connection.errors

    def tokens(self) -> str:
        """The streamed prose, as the panel would concatenate it."""
        return "".join(str(p.get("text", "")) for p in self.events if p.get("type") == "token")


class ChatHarness:
    """A hass with a chat-ready Selora entry, plus the two handler drivers."""

    def __init__(self, hass: HomeAssistant, llm: LLMClient, store: ConversationStore) -> None:
        self.hass = hass
        self.llm = llm
        self.store = store
        self.session_id: str | None = None

    @classmethod
    async def create(
        cls,
        hass: HomeAssistant,
        *,
        provider: str = "anthropic",
        automations: Iterable[dict[str, Any]] | None = None,
    ) -> ChatHarness:
        """Wire up the pieces the handlers look for in ``hass``.

        A real `LLMClient` over a real provider object: the handlers branch on
        provider flags (`is_local`, `is_low_context`) and the retry budget is
        read off it, so a stub would decide those for the code under test.
        """
        from custom_components.selora_ai.providers import create_provider

        llm = LLMClient(hass, provider=create_provider(provider, hass, api_key="test-key"))
        store = ConversationStore(hass)
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN]["_conv_store"] = store
        # `_find_llm` walks the per-entry dicts under DOMAIN.
        hass.data[DOMAIN]["harness_entry"] = {"llm": llm}
        harness = cls(hass, llm, store)
        harness.write_automations(list(automations or []))
        return harness

    # ── the home the handlers read ──────────────────────────────────

    def write_automations(self, entries: list[dict[str, Any]]) -> Path:
        """Replace automations.yaml — the source the refine context reads."""
        path = Path(self.hass.config.config_dir) / "automations.yaml"
        path.write_text(yaml.safe_dump(entries), encoding="utf-8")
        return path

    async def messages(self) -> list[dict[str, Any]]:
        """The persisted session, as a reopened panel would load it."""
        if self.session_id is None:
            return []
        session = await self.store.get_session(self.session_id)
        return list((session or {}).get("messages", []))

    async def save_proposal(self, message_index: int, automation_id: str) -> None:
        """Accept a proposal the way the panel does, minus the write.

        The panel's accept flow writes the automation and then calls
        ``set_automation_status``; a test that only needs the session to LOOK
        like it accepted one can skip the write and call
        :meth:`write_automations` with whatever the file should hold.
        """
        assert self.session_id is not None
        await self.store.set_automation_status(
            self.session_id, message_index, "saved", automation_id=automation_id
        )

    # ── the drivers ─────────────────────────────────────────────────

    async def chat(
        self,
        message: str,
        *,
        reply: dict[str, Any] | list[dict[str, Any]],
        session_id: str | None = None,
        is_admin: bool = True,
        **extra: Any,
    ) -> ChatTurn:
        """Drive ``selora_ai/chat``. ``reply`` is what `architect_chat` returns.

        A list scripts consecutive calls, which is how the validation-retry
        loop is exercised: first an invalid proposal, then the correction.
        """
        replies = list(reply) if isinstance(reply, list) else [reply]
        turn = self._new_turn(session_id, is_admin)

        async def _architect(_self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
            turn.architect_calls.append(self._recorded(args, kwargs))
            return replies[min(len(turn.architect_calls) - 1, len(replies) - 1)]

        with patch.object(LLMClient, "architect_chat", autospec=True, side_effect=_architect):
            await self._invoke(_CHAT, message, turn, extra)
        return turn

    async def stream(
        self,
        message: str,
        *,
        chunks: Iterable[str] | str,
        retry_reply: dict[str, Any] | None = None,
        session_id: str | None = None,
        is_admin: bool = True,
        **extra: Any,
    ) -> ChatTurn:
        """Drive ``selora_ai/chat_stream`` over scripted provider text.

        The text goes through the real `parse_streamed_response`, so a test can
        script exactly what a model emitted — fenced block, bare block,
        malformed JSON — and assert on what the handler made of it.

        ``retry_reply`` scripts the non-streamed correction round that a
        validation failure triggers (`_retry_invalid_automation` calls
        `architect_chat`).
        """
        text_chunks = [chunks] if isinstance(chunks, str) else list(chunks)
        turn = self._new_turn(session_id, is_admin)

        async def _stream(_self: Any, *args: Any, **kwargs: Any) -> AsyncIterator[str]:
            turn.architect_calls.append(self._recorded(args, kwargs))
            for chunk in text_chunks:
                yield chunk

        async def _architect(_self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
            turn.architect_calls.append(self._recorded(args, kwargs))
            return retry_reply or {"intent": "answer", "response": "corrected"}

        with (
            patch.object(LLMClient, "architect_chat_stream", autospec=True, side_effect=_stream),
            patch.object(LLMClient, "architect_chat", autospec=True, side_effect=_architect),
            # The title round trip is a background task on a real provider.
            patch.object(LLMClient, "generate_session_title", autospec=True, return_value="Title"),
        ):
            await self._invoke(_CHAT_STREAM, message, turn, extra)
        return turn

    # ── internals ───────────────────────────────────────────────────

    def _new_turn(self, session_id: str | None, is_admin: bool) -> ChatTurn:
        resolved = session_id if session_id is not None else self.session_id
        return ChatTurn(session_id=resolved or "", connection=FakeConnection(is_admin=is_admin))

    @staticmethod
    def _recorded(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
        """Normalise a call to kwargs, so a test never asserts on arg position.

        ``args`` is ``(self, user_message, entities)`` for both entry points.
        """
        recorded = dict(kwargs)
        if len(args) > 1:
            recorded["user_message"] = args[1]
        if len(args) > 2:
            recorded["entities"] = args[2]
        return recorded

    async def _invoke(
        self,
        handler: Any,
        message: str,
        turn: ChatTurn,
        extra: dict[str, Any],
    ) -> None:
        msg: dict[str, Any] = {"id": 1, "type": "", "message": message, **extra}
        msg["type"] = "selora_ai/chat" if handler is _CHAT else "selora_ai/chat_stream"
        if turn.session_id:
            msg["session_id"] = turn.session_id
        msg = _SCHEMAS[handler](msg)
        await handler(self.hass, turn.connection, msg)
        # Carry the session forward so a follow-up turn continues the
        # conversation without the test having to thread the id.
        resolved = turn.done.get("session_id") if not turn.connection.errors else None
        if resolved:
            turn.session_id = str(resolved)
            self.session_id = str(resolved)
