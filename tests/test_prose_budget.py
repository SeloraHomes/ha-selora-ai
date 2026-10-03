"""An answer that runs past the prose budget is abandoned and condensed.

The prompt asks for a short plan before a long how-to, and models ignore it
once they have device data in hand — the alarm-panel question produced pages of
YAML. So the streaming loop enforces it: past ``CHAT_PROSE_BUDGET_CHARS`` it
stops reading, tells the consumer to drop what was shown, and asks for the plan
in one tool-less, output-capped round.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai.const import (
    CHAT_CONDENSE_MAX_TOKENS,
    CHAT_PROSE_BUDGET_CHARS,
    STREAM_RESET,
)
from custom_components.selora_ai.llm_client import LLMClient
from custom_components.selora_ai.llm_client.client import _over_prose_budget
from custom_components.selora_ai.providers import create_provider

PLAN = "1. Pick the sensors. 2. Create the panel. Which part first?"


def _client(hass: HomeAssistant) -> LLMClient:
    return LLMClient(hass, create_provider("anthropic", hass, api_key="k"))


def _stub(provider: Any, rounds: list[list[str]], requests: list[dict[str, Any]]) -> list[int]:
    """Stream ``rounds[i]`` on round i; record each request's tools/kwargs.

    Returns a list that receives, per round, how many chunks were READ — which
    is how a test sees the loop stop consuming.
    """
    consumed: list[int] = []

    async def _raw_stream(_system: str, _messages: list, tools: Any = None, **kwargs: Any):  # noqa: ANN202
        requests.append({"tools": tools, **kwargs})
        yield MagicMock()

    async def _stream(_resp: Any, _tool_calls: list, content_blocks: list):  # noqa: ANN202
        chunks = rounds.pop(0) if rounds else []
        consumed.append(0)
        for chunk in chunks:
            consumed[-1] += 1
            content_blocks.append({"type": "text", "text": chunk})
            yield chunk

    provider.raw_request_stream = _raw_stream
    provider.stream_with_tools = _stream
    return consumed


async def _run(client: LLMClient, messages: list[dict[str, Any]]) -> list[str]:
    return [
        chunk
        async for chunk in client._stream_request_with_tools(
            system="s", messages=messages, tool_executor=MagicMock(), tools=[{"name": "t"}]
        )
    ]


async def test_an_overlong_answer_is_replaced_by_a_plan(hass: HomeAssistant) -> None:
    client = _client(hass)
    requests: list[dict[str, Any]] = []
    page = "Add this YAML to configuration.yaml and restart. " * 20  # ~1000 chars
    consumed = _stub(client._provider, [[page] * 10, [PLAN]], requests)
    messages: list[dict[str, Any]] = []

    out = await _run(client, messages)

    # The consumer is told to drop what it showed, and gets the plan after.
    assert STREAM_RESET in out
    after = out[out.index(STREAM_RESET) + 1 :]
    assert "".join(after) == PLAN
    # Reading stopped at the budget instead of paying for all ten pages.
    assert consumed[0] < 10
    # The rewrite carries no tools and a small output cap.
    assert requests[1]["tools"] is None
    assert requests[1]["max_tokens"] == CHAT_CONDENSE_MAX_TOKENS
    # The model sees what it wrote, so it condenses rather than starts over.
    assert messages[-2]["role"] == "assistant"
    assert messages[-1]["role"] == "user"
    assert "120 words" in messages[-1]["content"]


async def test_a_short_answer_streams_untouched(hass: HomeAssistant) -> None:
    client = _client(hass)
    requests: list[dict[str, Any]] = []
    _stub(client._provider, [["Your kitchen light is on."]], requests)

    out = await _run(client, [])

    assert out == ["Your kitchen light is on."]
    assert len(requests) == 1


async def test_the_rewrite_happens_once(hass: HomeAssistant) -> None:
    """The condensing round is bounded by its token cap, not by a second
    rewrite — which would only repeat the first."""
    client = _client(hass)
    requests: list[dict[str, Any]] = []
    long = "x" * (CHAT_PROSE_BUDGET_CHARS + 10)
    _stub(client._provider, [[long], [long], [PLAN]], requests)

    out = await _run(client, [])

    assert out.count(STREAM_RESET) == 1
    assert len(requests) == 2


def test_a_proposal_is_exempt() -> None:
    """A long automation is the automation's length, not verbosity."""
    body = "Here it is.\n```automation\n{" + '"a": 1, ' * 1000 + "}\n```"
    assert not _over_prose_budget(body)


def test_entity_markers_do_not_count() -> None:
    """A "which lights are on?" answer is long because the home is."""
    markers = "[[entities:" + ",".join(f"light.l{i}" for i in range(600)) + "]]"
    assert not _over_prose_budget("These are on:\n" + markers)
    assert _over_prose_budget("y" * (CHAT_PROSE_BUDGET_CHARS + 1))


async def test_the_handler_drops_the_discarded_text(hass: HomeAssistant) -> None:
    """End to end: the panel is told to reset, and neither the reply nor the
    transcript keeps a word of the abandoned answer."""
    from tests.chat_harness import ChatHarness

    harness = await ChatHarness.create(hass)
    turn = await harness.stream(
        "how do I make an alarm panel?",
        chunks=["Add this to configuration.yaml: " + "lots of YAML " * 40, STREAM_RESET, PLAN],
    )

    assert {"type": "reset"} in turn.events
    # The rewrite STREAMS after the reset — it is shorter than what was
    # discarded, so a send cursor left behind would skip all of it.
    after_reset = turn.events[turn.events.index({"type": "reset"}) + 1 :]
    streamed = "".join(e.get("text", "") for e in after_reset if e.get("type") == "token")
    assert streamed == PLAN
    assert turn.done["response"] == PLAN
    messages = await harness.messages()
    assert "configuration.yaml" not in messages[-1]["content"]


# ── "How can I…?" turns ─────────────────────────────────────────────────────

ALARM_QUESTION = (
    "How can I use https://www.home-assistant.io/integrations/alarm_control_panel/ "
    "to create an alarm panel with the devices I have already (door sensors, etc)"
)


@pytest.mark.parametrize(
    "message",
    [
        ALARM_QUESTION,
        "how do I set up presence detection?",
        "What's the best way to monitor my energy use?",
        "Comment puis-je créer une alarme avec mes capteurs ?",
        "Wie kann ich eine Alarmanlage einrichten?",
        "¿Cómo puedo crear una alarma?",
        "Come posso creare un allarme?",
    ],
)
def test_a_howto_question_is_recognised(message: str) -> None:
    from custom_components.selora_ai.llm_client.intent import _is_howto_request

    assert _is_howto_request(message)


@pytest.mark.parametrize(
    "message",
    [
        "Create an automation that turns the porch light on at sunset",
        "set up an alarm panel with my door sensors",
        "turn off the kitchen light",
        "which lights are on?",
    ],
)
def test_a_request_to_do_it_is_not_a_howto(message: str) -> None:
    """An imperative asks for the thing done — tools or a proposal, never a
    question back."""
    from custom_components.selora_ai.llm_client.intent import _is_howto_request

    assert not _is_howto_request(message)


def test_the_howto_shape_rides_on_the_current_message(hass: HomeAssistant) -> None:
    """In the request itself, last — the system-prompt rule was ignored."""
    client = _client(hass)

    howto = client._build_chat_messages(ALARM_QUESTION, [], None, None)[-1]["content"]
    plain = client._build_chat_messages("turn off the kitchen light", [], None, None)[-1]["content"]

    assert "ANSWER SHAPE FOR THIS TURN" in howto
    assert howto.rstrip().endswith("until they answer.")
    assert "ANSWER SHAPE FOR THIS TURN" not in plain


def test_a_howto_turn_has_the_tighter_budget() -> None:
    from custom_components.selora_ai.const import CHAT_HOWTO_BUDGET_CHARS

    text = "z" * (CHAT_HOWTO_BUDGET_CHARS + 1)
    assert _over_prose_budget(text, CHAT_HOWTO_BUDGET_CHARS)
    assert not _over_prose_budget(text)


async def test_the_budget_counts_the_whole_reply_not_each_round(hass: HomeAssistant) -> None:
    """Narration before a tool call is in the same bubble; a how-to split
    across rounds must not get the budget once per round."""
    from unittest.mock import AsyncMock

    from custom_components.selora_ai.const import CHAT_HOWTO_BUDGET_CHARS

    client = _client(hass)
    half = "y" * (CHAT_HOWTO_BUDGET_CHARS - 200)
    rounds = [([half], True), ([half], False), ([PLAN], False)]

    async def _raw_stream(_system: str, _messages: list, tools: Any = None, **_kw: Any):  # noqa: ANN202
        yield MagicMock()

    async def _stream(_resp: Any, tool_calls: list, content_blocks: list):  # noqa: ANN202
        chunks, calls_tool = rounds.pop(0)
        for chunk in chunks:
            content_blocks.append({"type": "text", "text": chunk})
            yield chunk
        if calls_tool:
            tool_calls.append({"id": "t1", "name": "get_entity_state", "arguments": {}})

    client._provider.raw_request_stream = _raw_stream
    client._provider.stream_with_tools = _stream
    executor = MagicMock()
    executor.execute = AsyncMock(return_value={"state": "on"})
    executor.call_log = []

    out = [
        chunk
        async for chunk in client._stream_request_with_tools(
            system="s",
            messages=[],
            tool_executor=executor,
            tools=[{"name": "t"}],
            prose_budget=CHAT_HOWTO_BUDGET_CHARS,
        )
    ]

    assert STREAM_RESET in out
    assert "".join(c for c in out[out.index(STREAM_RESET) + 1 :] if isinstance(c, str)).endswith(
        PLAN
    )


async def test_a_turn_without_a_stream_inherits_no_cut(hass: HomeAssistant) -> None:
    """The provider outlives the turn: a canned greeting after a cut reply
    must not be reported as cut off too."""
    client = _client(hass)
    client._provider._last_stream_terminated = False
    client._provider._last_finish_reason = "length"

    async for _ in client.architect_chat_stream("hi", []):
        pass

    assert not client._provider.last_stream_unterminated
    assert not client._provider.last_response_truncated
