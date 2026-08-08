"""A tool call written as prose must not end the turn.

Some models (DeepSeek in particular) emit the call they want to make as
ordinary assistant text instead of a structured tool_use block. No provider
here parses that form, so ``extract_tool_calls`` / ``stream_with_tools``
return nothing and both loops read the round as a FINAL ANSWER — the
investigation stops mid-flight with rounds still unspent, and the user has to
type "continue" to restart it.

Both loops now recognise the shape (the leak guard tripped, or
``strip_leaked_tool_markup`` changed the text) and hand the model a
correction round instead, bounded by ``_MAX_LEAK_RETRIES``.

Companion to ``test_tool_markup_leak.py``, which covers the detection itself.
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN202
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from custom_components.selora_ai.llm_client import LLMClient
from custom_components.selora_ai.llm_client.client import _MAX_LEAK_RETRIES
from custom_components.selora_ai.providers import create_provider

# Fullwidth pipes: the real DeepSeek delimiter (see test_tool_markup_leak).
LEAKED_TEXT = 'Let me check the history.\n\n<｜DSML｜invoke name="get_entity_history">'


def _make_client(hass) -> LLMClient:
    provider = create_provider("anthropic", hass, api_key="test-key")
    return LLMClient(hass, provider)


def _texts(messages: list[dict[str, Any]], role: str) -> list[str]:
    return [str(m.get("content", "")) for m in messages if m.get("role") == role]


class TestNonStreamingLoop:
    async def test_leaked_call_retries_instead_of_ending_the_turn(self, hass) -> None:
        client = _make_client(hass)
        provider = client._provider
        provider.raw_request = AsyncMock(return_value={"stub": True})
        provider.extract_tool_calls = MagicMock(return_value=[])
        # Round 1 leaks; round 2 answers properly.
        replies = [LEAKED_TEXT, "The light turned on at 22:04 each night."]
        provider.extract_text_response = MagicMock(side_effect=lambda _r: replies.pop(0))

        messages: list[dict[str, Any]] = []
        text, error, _log = await client._send_request_with_tools(
            system="s", messages=messages, tool_executor=MagicMock(), tools=[{"name": "t"}]
        )

        assert error is None
        # The turn continued to a real answer rather than stopping at the leak.
        assert text == "The light turned on at 22:04 each night."
        # And the model was told why nothing ran.
        assert any("did NOT run" in t or "NO tool ran" in t for t in _texts(messages, "user"))

    async def test_leaked_prose_is_preserved_for_the_retry(self, hass) -> None:
        """The correction round needs the model's own words, minus the markup,
        or it re-orients from nothing."""
        client = _make_client(hass)
        provider = client._provider
        provider.raw_request = AsyncMock(return_value={"stub": True})
        provider.extract_tool_calls = MagicMock(return_value=[])
        replies = [LEAKED_TEXT, "done"]
        provider.extract_text_response = MagicMock(side_effect=lambda _r: replies.pop(0))

        messages: list[dict[str, Any]] = []
        await client._send_request_with_tools(
            system="s", messages=messages, tool_executor=MagicMock(), tools=[{"name": "t"}]
        )

        said = _texts(messages, "assistant")
        assert said == ["Let me check the history."]
        assert "DSML" not in said[0]

    async def test_retries_are_bounded(self, hass) -> None:
        """A model that leaks every round must not burn the whole budget."""
        client = _make_client(hass)
        provider = client._provider
        provider.raw_request = AsyncMock(return_value={"stub": True})
        provider.extract_tool_calls = MagicMock(return_value=[])
        provider.extract_text_response = MagicMock(return_value=LEAKED_TEXT)

        messages: list[dict[str, Any]] = []
        text, error, _log = await client._send_request_with_tools(
            system="s", messages=messages, tool_executor=MagicMock(), tools=[{"name": "t"}]
        )

        assert error is None
        assert len(_texts(messages, "user")) == _MAX_LEAK_RETRIES
        # Falls through to the normal final-answer handling: stripped, not raw.
        assert text is None or "DSML" not in text

    async def test_clean_final_answer_is_untouched(self, hass) -> None:
        """No leak means no retry — the ordinary path must not gain a round."""
        client = _make_client(hass)
        provider = client._provider
        provider.raw_request = AsyncMock(return_value={"stub": True})
        provider.extract_tool_calls = MagicMock(return_value=[])
        provider.extract_text_response = MagicMock(return_value="Your kitchen light is on.")

        messages: list[dict[str, Any]] = []
        text, error, _log = await client._send_request_with_tools(
            system="s", messages=messages, tool_executor=MagicMock(), tools=[{"name": "t"}]
        )

        assert (text, error) == ("Your kitchen light is on.", None)
        assert messages == []
        assert provider.raw_request.await_count == 1


def _stub_stream(provider, rounds: list[list[str]], seen_tools: list | None = None) -> None:
    """Make ``provider`` stream ``rounds[i]`` chunks on round i, no tool calls.

    When ``seen_tools`` is given, the ``tools`` argument of each round's
    request is appended to it — that is how a test tells a tool-enabled round
    from the forced-final one.
    """

    async def _raw_stream(_system, _messages, tools=None):
        if seen_tools is not None:
            seen_tools.append(tools)
        yield MagicMock()

    provider.raw_request_stream = _raw_stream

    async def _stream_with_tools(_resp, _tool_calls, content_blocks):
        for chunk in rounds.pop(0) if rounds else []:
            content_blocks.append({"type": "text", "text": chunk})
            yield chunk

    provider.stream_with_tools = _stream_with_tools


class TestStreamingLoop:
    async def test_leaked_call_retries_instead_of_ending_the_turn(self, hass) -> None:
        client = _make_client(hass)
        _stub_stream(
            client._provider,
            [[LEAKED_TEXT], ["The light turned on at 22:04 each night."]],
        )

        messages: list[dict[str, Any]] = []
        out = [
            chunk
            async for chunk in client._stream_request_with_tools(
                system="s", messages=messages, tool_executor=MagicMock(), tools=[{"name": "t"}]
            )
        ]

        joined = "".join(out)
        # The markup never reaches the panel...
        assert "DSML" not in joined
        # ...and the turn carried on to a real answer instead of stopping.
        assert "22:04" in joined
        assert any("NO tool ran" in t for t in _texts(messages, "user"))

    async def test_clean_round_does_not_retry(self, hass) -> None:
        client = _make_client(hass)
        _stub_stream(client._provider, [["Your kitchen light is on."]])

        messages: list[dict[str, Any]] = []
        out = [
            chunk
            async for chunk in client._stream_request_with_tools(
                system="s", messages=messages, tool_executor=MagicMock(), tools=[{"name": "t"}]
            )
        ]

        assert "".join(out) == "Your kitchen light is on."
        assert messages == []


class TestRetryRoundKeepsItsTools:
    """A leaked round observed nothing, so it must not be charged against the
    budget. Charging it pushes the retry onto the forced-final round, which
    withholds tools — the model is then told to re-issue a real tool call
    while the request carries no tool definitions, and the retry directive
    contradicts the final-round directive telling it not to write tool syntax.
    """

    def _shrink_budget(self, monkeypatch, rounds: int = 2) -> None:
        """Two rounds: index 0 tool-enabled, index 1 forced-final."""
        monkeypatch.setattr(
            "custom_components.selora_ai.llm_client.client.MAX_TOOL_CALL_ROUNDS", rounds
        )

    async def test_non_streaming_retry_still_has_tools(self, hass, monkeypatch) -> None:
        self._shrink_budget(monkeypatch)
        client = _make_client(hass)
        provider = client._provider
        seen_tools: list = []

        async def _raw_request(_system, _messages, tools=None):
            seen_tools.append(tools)
            return {"stub": True}

        provider.raw_request = _raw_request
        provider.extract_tool_calls = MagicMock(return_value=[])
        replies = [LEAKED_TEXT, "Answer."]
        provider.extract_text_response = MagicMock(side_effect=lambda _r: replies.pop(0))

        await client._send_request_with_tools(
            system="s", messages=[], tool_executor=MagicMock(), tools=[{"name": "t"}]
        )

        # Round 0 leaked; the retry round must still carry tools rather than
        # being the tool-less final round.
        assert seen_tools[0] is not None
        assert seen_tools[1] is not None, "retry landed on the tool-less final round"

    async def test_streaming_retry_still_has_tools(self, hass, monkeypatch) -> None:
        self._shrink_budget(monkeypatch)
        client = _make_client(hass)
        seen_tools: list = []
        _stub_stream(client._provider, [[LEAKED_TEXT], ["Answer."]], seen_tools)

        async for _ in client._stream_request_with_tools(
            system="s", messages=[], tool_executor=MagicMock(), tools=[{"name": "t"}]
        ):
            pass

        assert seen_tools[0] is not None
        assert seen_tools[1] is not None, "retry landed on the tool-less final round"

    async def test_budget_extension_is_bounded(self, hass, monkeypatch) -> None:
        """The extension is granted per retry, so a model that leaks every
        round still terminates: the first round plus _MAX_LEAK_RETRIES
        corrections, after which the leak falls through to the normal
        final-answer handling instead of extending the budget again."""
        self._shrink_budget(monkeypatch)
        client = _make_client(hass)
        seen_tools: list = []
        _stub_stream(client._provider, [[LEAKED_TEXT]] * 10, seen_tools)

        async for _ in client._stream_request_with_tools(
            system="s", messages=[], tool_executor=MagicMock(), tools=[{"name": "t"}]
        ):
            pass

        assert len(seen_tools) == 1 + _MAX_LEAK_RETRIES


class TestLeakDetectedOnlyAtFlush:
    """A stream cut off mid-marker ("...<invo") is dropped by
    ``MarkupLeakGuard.flush``, not by ``feed``. If that path does not latch
    ``suppressed``, the truncated leak is the one shape that still ends the
    turn with nothing executed."""

    def test_flush_latches_suppressed(self) -> None:
        from custom_components.selora_ai.llm_client.parsers import MarkupLeakGuard

        guard = MarkupLeakGuard()
        assert guard.feed("Let me check. <invo") == "Let me check."
        assert not guard.suppressed, "not decidable until the stream ends"
        # The marker is dropped; only the whitespace held ahead of it survives.
        assert guard.flush().strip() == ""
        assert guard.suppressed, "flush dropped a partial marker — that is a leak"

    def test_flush_on_ordinary_trailing_text_does_not_suppress(self) -> None:
        from custom_components.selora_ai.llm_client.parsers import MarkupLeakGuard

        guard = MarkupLeakGuard()
        guard.feed("The value is 5 < 9")
        guard.flush()
        assert not guard.suppressed

    async def test_truncated_leak_triggers_a_retry(self, hass) -> None:
        client = _make_client(hass)
        _stub_stream(
            client._provider,
            [["Let me check the history. <invo"], ["The light turned on at 22:04."]],
        )

        messages: list[dict[str, Any]] = []
        out = [
            chunk
            async for chunk in client._stream_request_with_tools(
                system="s", messages=messages, tool_executor=MagicMock(), tools=[{"name": "t"}]
            )
        ]

        assert "22:04" in "".join(out)
        assert any("NO tool ran" in t for t in _texts(messages, "user"))


class TestStreamedNarrationIsCarried:
    """``append_streaming_tool_results`` used to synthesize an assistant turn
    holding only ``tool_calls`` — no ``content``. The model then saw its own
    calls and their results with no memory of what it had said, re-oriented
    from scratch, and re-narrated the same sentence every round."""

    def _provider(self, hass):
        return create_provider("openai", hass, api_key="k", model="gpt-5.4")

    def test_narration_is_attached_to_the_first_assistant_turn(self, hass) -> None:
        provider = self._provider(hass)
        messages: list[dict[str, Any]] = []
        provider.append_streaming_tool_results(
            messages,
            [{"type": "text", "text": "Let me check "}, {"type": "text", "text": "the history."}],
            [{"id": "1", "name": "get_entity_history", "arguments": {}}],
            [{"ok": True}],
        )
        assistant = [m for m in messages if m["role"] == "assistant"]
        assert assistant[0]["content"] == "Let me check the history."
        assert assistant[0]["tool_calls"][0]["function"]["name"] == "get_entity_history"

    def test_narration_is_not_repeated_across_parallel_calls(self, hass) -> None:
        provider = self._provider(hass)
        messages: list[dict[str, Any]] = []
        provider.append_streaming_tool_results(
            messages,
            [{"type": "text", "text": "Checking both."}],
            [
                {"id": "1", "name": "a", "arguments": {}},
                {"id": "2", "name": "b", "arguments": {}},
            ],
            [{"ok": True}, {"ok": True}],
        )
        carried = [m.get("content") for m in messages if m["role"] == "assistant"]
        assert carried == ["Checking both.", None]

    def test_no_narration_leaves_the_message_unchanged(self, hass) -> None:
        provider = self._provider(hass)
        messages: list[dict[str, Any]] = []
        provider.append_streaming_tool_results(
            messages, [], [{"id": "1", "name": "a", "arguments": {}}], [{"ok": True}]
        )
        assistant = [m for m in messages if m["role"] == "assistant"]
        assert "content" not in assistant[0]
