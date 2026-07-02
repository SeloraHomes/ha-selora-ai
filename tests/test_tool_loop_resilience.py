"""The non-streaming tool loop must degrade gracefully on a malformed
provider response — not crash the websocket handler.

Regression: ``_send_request_with_tools`` previously caught only
``ConnectionError`` around ``raw_request``; a parse error in
``extract_tool_calls`` / ``extract_text_response`` / ``append_tool_result``
(missing tool-call keys, a non-JSON 200 body) escaped to HA's WS framework
as an opaque error AND skipped ``architect_chat``'s "already executed —
don't retry" guard, risking a re-fired command on retry. It must now
return ``(None, error, tool_calls_log)`` like the ConnectionError path and
the streaming loop, preserving the executed-tool log.
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN202
from unittest.mock import AsyncMock, MagicMock


from custom_components.selora_ai.llm_client import LLMClient
from custom_components.selora_ai.providers import create_provider


def _make_client(hass) -> LLMClient:
    provider = create_provider("anthropic", hass, api_key="test-key")
    return LLMClient(hass, provider)


async def test_parse_crash_returns_error_tuple_not_raise(hass) -> None:
    client = _make_client(hass)
    provider = client._provider
    provider.raw_request = AsyncMock(return_value={"stub": True})
    # Simulate a malformed response: extraction raises mid-parse.
    provider.extract_tool_calls = MagicMock(side_effect=KeyError("name"))

    text, error, log = await client._send_request_with_tools(
        system="s",
        messages=[],
        tool_executor=MagicMock(),
        tools=[],
    )

    assert text is None
    assert error  # a non-empty error string, surfaced to architect_chat
    assert log == []


async def test_parse_crash_preserves_executed_tool_log(hass) -> None:
    """A command executed in an earlier round must remain in the returned
    log so architect_chat's double-execute guard can warn the user."""
    client = _make_client(hass)
    provider = client._provider
    provider.raw_request = AsyncMock(return_value={"stub": True})
    provider.append_tool_result = MagicMock()

    # Round 1: a write (executed) mixed with a read so the loop does NOT
    # short-circuit and proceeds to round 2. Round 2: extraction crashes.
    rounds = [
        [
            {"name": "execute_command", "arguments": {}, "id": "1"},
            {"name": "get_entity_state", "arguments": {}, "id": "2"},
        ]
    ]

    def _extract(_resp):
        if rounds:
            return rounds.pop(0)
        raise KeyError("name")

    provider.extract_tool_calls = MagicMock(side_effect=_extract)

    async def _execute(name, _args):
        if name == "execute_command":
            return {"executed": True}
        return {"data": "ok"}

    tool_executor = MagicMock()
    tool_executor.execute = AsyncMock(side_effect=_execute)

    text, error, log = await client._send_request_with_tools(
        system="s",
        messages=[],
        tool_executor=tool_executor,
        tools=[{"name": "x"}],
    )

    assert text is None
    assert error
    assert any(entry["tool"] == "execute_command" for entry in log)
