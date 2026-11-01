"""The events card shows what the reply confirmed (``show_camera_events``).

A search by description ranks every event and fills its limit, so the card
drew two dozen people under a reply that found two wearing a backpack. Only
the model, reading the descriptions, knows which answer the question: it names
them, and the card folds the rest away.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant

from custom_components.selora_ai.frigate.events import show_events
from custom_components.selora_ai.llm_client import LLMClient
from custom_components.selora_ai.mcp_server.definitions import _DERIVED_MCP_TOOLS
from custom_components.selora_ai.providers import create_provider
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import FRIGATE_TOOL_NAMES, TOOL_MAP

BACKPACK = "1793470197.253093-1wv7iz"
ANSWER = "Two people wore a backpack: at 10:29 and 11:58."


def test_only_event_ids_are_passed_on() -> None:
    result = show_events({"ids": [BACKPACK, BACKPACK, "../../etc", " "]})

    assert result["shown"] == [BACKPACK]
    assert result["not_event_ids"] == ["../../etc"]


def test_none_confirmed_is_an_answer() -> None:
    assert show_events({"ids": []}) == {"shown": []}


def test_the_tool_is_for_the_panel_and_frigate_only() -> None:
    tool = TOOL_MAP["show_camera_events"]

    # Assist and MCP have no card to draw it on.
    assert tool.panel_only
    assert tool.requires_admin
    assert "show_camera_events" in FRIGATE_TOOL_NAMES
    assert "show_camera_events" not in set(_DERIVED_MCP_TOOLS.values())


def _client(hass: HomeAssistant) -> LLMClient:
    return LLMClient(hass, create_provider("anthropic", hass, api_key="k"))


def _stream_rounds(provider: Any, rounds: list[tuple[str, list[dict[str, Any]]]]) -> list[int]:
    """Round i streams ``rounds[i]``'s text and asks for its tool calls."""
    requests: list[int] = []

    async def _raw_stream(_system: str, _messages: list, tools: Any = None, **_kw: Any):  # noqa: ANN202
        requests.append(len(requests))
        yield MagicMock()

    async def _stream(_resp: Any, tool_calls: list, content_blocks: list):  # noqa: ANN202
        text, calls = rounds.pop(0) if rounds else ("", [])
        tool_calls.extend(calls)
        if text:
            content_blocks.append({"type": "text", "text": text})
            yield text

    provider.raw_request_stream = _raw_stream
    provider.stream_with_tools = _stream
    provider.append_streaming_tool_results = MagicMock()
    return requests


def _show(ids: list[str]) -> dict[str, Any]:
    return {"id": "s1", "name": "show_camera_events", "arguments": {"ids": ids}}


async def _stream(hass: HomeAssistant, client: LLMClient) -> tuple[list[str], ToolExecutor]:
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    out = [
        chunk
        async for chunk in client._stream_request_with_tools(
            system="s", messages=[], tool_executor=executor, tools=[{"name": "t"}]
        )
        if isinstance(chunk, str) and chunk.strip()
    ]
    return out, executor


async def test_picking_beside_the_answer_ends_the_turn(hass: HomeAssistant) -> None:
    client = _client(hass)
    requests = _stream_rounds(client._provider, [(ANSWER, [_show([BACKPACK])]), ("again", [])])

    out, executor = await _stream(hass, client)

    # The next round would only say the answer again.
    assert len(requests) == 1
    assert ANSWER in "".join(out)
    assert "again" not in "".join(out)
    assert executor.call_log[-1]["result"] == {"shown": [BACKPACK]}


async def test_picking_before_the_answer_still_gets_one(hass: HomeAssistant) -> None:
    client = _client(hass)
    requests = _stream_rounds(client._provider, [("", [_show([BACKPACK])]), (ANSWER, [])])

    out, _ = await _stream(hass, client)

    assert len(requests) == 2
    assert ANSWER in "".join(out)


async def test_the_non_streaming_loop_ends_on_the_pick_too(hass: HomeAssistant) -> None:
    client = _client(hass)
    provider = client._provider
    provider.raw_request = AsyncMock(
        return_value={
            "content": [
                {"type": "text", "text": ANSWER},
                {
                    "type": "tool_use",
                    "id": "s1",
                    "name": "show_camera_events",
                    "input": {"ids": [BACKPACK]},
                },
            ]
        }
    )

    text, error, log = await client._send_request_with_tools(
        system="s",
        messages=[],
        tool_executor=ToolExecutor(hass, MagicMock(), is_admin=True),
        tools=[{"name": "t"}],
    )

    assert error is None
    assert text == ANSWER
    assert provider.raw_request.await_count == 1
    assert log[-1]["result"] == {"shown": [BACKPACK]}
