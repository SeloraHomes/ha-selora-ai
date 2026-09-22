"""Providers report WHY a stream stopped.

A backend that hits its output cap ends the stream cleanly, so the reply that
arrives is indistinguishable from a finished one except by its shape. Nothing
read ``finish_reason`` before, which is why a turn cut off mid-JSON reached the
user as a confident summary of an automation that was never written. The answer
is per-backend vocabulary — OpenAI ``length``, Anthropic ``max_tokens``, Gemini
``MAX_TOKENS`` — normalized by the base class.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from custom_components.selora_ai.providers import create_provider


class _FakeContent:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    async def iter_any(self):  # noqa: ANN201 — mirrors aiohttp's signature
        # One chunk per SSE line, so a provider that buffers across chunk
        # boundaries is exercised the way the real transport does it.
        for line in self._body.splitlines(keepends=True):
            yield line


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self.content = _FakeContent(body)


async def _drain(provider: Any, body: str) -> str:
    tool_calls: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    out: list[str] = []
    async for text in provider.stream_with_tools(_FakeResponse(body), tool_calls, blocks):
        out.append(text)
    return "".join(out)


def _openai_sse(finish_reason: str | None) -> str:
    chunks: list[dict[str, Any]] = [{"choices": [{"delta": {"content": "half a "}}]}]
    if finish_reason is not None:
        chunks.append({"choices": [{"delta": {}, "finish_reason": finish_reason}]})
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def _anthropic_sse(stop_reason: str) -> str:
    events: list[dict[str, Any]] = [
        {"type": "content_block_start", "content_block": {"type": "text"}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "half a "}},
        {"type": "message_delta", "delta": {"stop_reason": stop_reason}},
    ]
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events)


def _gemini_sse(finish_reason: str) -> str:
    events: list[dict[str, Any]] = [
        {"candidates": [{"content": {"parts": [{"text": "half a "}]}}]},
        {"candidates": [{"finishReason": finish_reason, "content": {"parts": []}}]},
    ]
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events)


@pytest.mark.asyncio
async def test_openai_reports_the_output_cap(hass) -> None:
    provider = create_provider("openai", hass, api_key="k", model="gpt-5.4")
    assert await _drain(provider, _openai_sse("length")) == "half a "
    assert provider.last_finish_reason == "length"
    assert provider.last_response_truncated is True


@pytest.mark.asyncio
async def test_openai_normal_stop_is_not_truncation(hass) -> None:
    provider = create_provider("openai", hass, api_key="k", model="gpt-5.4")
    await _drain(provider, _openai_sse("stop"))
    assert provider.last_response_truncated is False


@pytest.mark.asyncio
async def test_a_new_stream_clears_the_previous_answer(hass) -> None:
    """The tool loop asks once per round. A reason left from the round before
    would describe the wrong request."""
    provider = create_provider("openai", hass, api_key="k", model="gpt-5.4")
    await _drain(provider, _openai_sse("length"))
    assert provider.last_response_truncated is True
    await _drain(provider, _openai_sse(None))
    assert provider.last_finish_reason is None
    assert provider.last_response_truncated is False


@pytest.mark.asyncio
async def test_anthropic_reports_max_tokens(hass) -> None:
    provider = create_provider("anthropic", hass, api_key="k")
    assert await _drain(provider, _anthropic_sse("max_tokens")) == "half a "
    assert provider.last_response_truncated is True


@pytest.mark.asyncio
async def test_anthropic_end_turn_is_not_truncation(hass) -> None:
    provider = create_provider("anthropic", hass, api_key="k")
    await _drain(provider, _anthropic_sse("end_turn"))
    assert provider.last_response_truncated is False


@pytest.mark.asyncio
async def test_gemini_reports_max_tokens_in_its_own_case(hass) -> None:
    provider = create_provider("gemini", hass, api_key="k")
    assert await _drain(provider, _gemini_sse("MAX_TOKENS")) == "half a "
    assert provider.last_response_truncated is True


@pytest.mark.asyncio
async def test_gemini_stop_is_not_truncation(hass) -> None:
    provider = create_provider("gemini", hass, api_key="k")
    await _drain(provider, _gemini_sse("STOP"))
    assert provider.last_response_truncated is False


@pytest.mark.asyncio
async def test_a_backend_that_says_nothing_is_not_assumed_truncated(hass) -> None:
    """``None`` means the backend did not say, which is not the same as "it was
    cut off" — the conservative answer keeps the behaviour that was there."""
    provider = create_provider("ollama", hass, host="http://localhost:11434", model="llama4")
    await _drain(provider, _openai_sse(None))
    assert provider.last_finish_reason is None
    assert provider.last_response_truncated is False
