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


# ── A stream nobody ended ───────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["openai", "selora_cloud"])
async def test_a_stream_with_no_terminator_is_unterminated(hass, provider_name: str) -> None:
    """No finish_reason and no [DONE]: something between us and the model
    dropped the tail, and the text alone cannot say so."""
    provider = create_provider(provider_name, hass, api_key="k", model="gpt-5.4")
    body = 'data: {"choices": [{"delta": {"content": "Verify it, because"}}]}\n\n'
    assert await _drain(provider, body) == "Verify it, because"
    assert provider.last_stream_unterminated is True
    assert provider.last_response_truncated is False


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["stop", None], ids=["finish-reason", "done-only"])
async def test_either_terminator_ends_a_stream_cleanly(hass, finish: str | None) -> None:
    provider = create_provider("openai", hass, api_key="k", model="gpt-5.4")
    await _drain(provider, _openai_sse(finish))
    assert provider.last_stream_unterminated is False


@pytest.mark.asyncio
async def test_a_clean_stream_clears_an_earlier_cut(hass) -> None:
    provider = create_provider("openai", hass, api_key="k", model="gpt-5.4")
    await _drain(provider, 'data: {"choices": [{"delta": {"content": "x"}}]}\n\n')
    await _drain(provider, _openai_sse("stop"))
    assert provider.last_stream_unterminated is False


# ── The parser applies the verdict to prose ─────────────────────────────────


def _answer(text: str = "Verify the entity ID, because") -> dict[str, Any]:
    return {"intent": "answer", "response": text}


@pytest.mark.parametrize(
    ("finish", "unterminated", "reason"),
    [("length", False, "output_cap"), (None, True, "unreported"), ("stop", True, "unreported")],
)
def test_a_cut_answer_is_marked_and_keeps_its_text(
    finish: str | None, unterminated: bool, reason: str
) -> None:
    from custom_components.selora_ai.llm_client.parsers import mark_cut_prose

    result = mark_cut_prose(_answer(), finish_reason=finish, unterminated=unterminated)

    assert result["validation_error"] == "truncated_response"
    assert result["truncation_reason"] == reason
    # What arrived is what the user would retry for — not replaced.
    assert result["response"] == "Verify the entity ID, because"


def test_a_clean_answer_is_left_alone() -> None:
    from custom_components.selora_ai.llm_client.parsers import mark_cut_prose

    result = mark_cut_prose(_answer(), finish_reason="stop", unterminated=False)
    assert "validation_error" not in result


@pytest.mark.parametrize(
    "result",
    [
        {"intent": "automation", "response": "Here it is", "automation": {"alias": "x"}},
        {"intent": "command", "response": "Done", "calls": [{"service": "light.turn_on"}]},
        {"intent": "answer", "response": "x", "validation_error": "truncated_response"},
    ],
    ids=["proposal", "command", "already-marked"],
)
def test_a_parsed_payload_is_not_marked(result: dict[str, Any]) -> None:
    """The card is what the user acts on, and it parsed whole."""
    from custom_components.selora_ai.llm_client.parsers import mark_cut_prose

    assert mark_cut_prose(dict(result), finish_reason="length", unterminated=True) == result


# ── The plain text stream (no tools) tracks it too ──────────────────────────


class _FakePost:
    def __init__(self, body: str) -> None:
        self._resp = _FakeResponse(body)
        self._resp.status = 200  # type: ignore[attr-defined]

    async def __aenter__(self) -> _FakeResponse:
        return self._resp

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _FakeSession:
    def __init__(self, body: str) -> None:
        self._body = body

    def post(self, *_a: Any, **_kw: Any) -> _FakePost:
        return _FakePost(self._body)


async def _drain_plain(provider: Any, body: str) -> str:
    provider._get_session = lambda: _FakeSession(body)
    return "".join([t async for t in provider.send_request_stream("s", [], max_tokens=10)])


@pytest.mark.asyncio
async def test_a_plain_stream_with_no_terminator_is_unterminated(hass: Any) -> None:
    """The suggestions analysis streams without tools; a gateway cutting it
    after a few seconds left half a JSON array that read as a full reply."""
    provider = create_provider("openai", hass, api_key="k", model="gpt-5.4")
    body = 'data: {"choices": [{"delta": {"content": "[{\\"alias\\": "}}]}\n\n'
    assert await _drain_plain(provider, body) == '[{"alias": '
    assert provider.last_stream_unterminated is True


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["stop", None], ids=["finish-reason", "done-only"])
async def test_a_plain_stream_ended_by_the_backend_is_terminated(
    hass: Any, finish: str | None
) -> None:
    provider = create_provider("openai", hass, api_key="k", model="gpt-5.4")
    await _drain_plain(provider, 'data: {"choices": [{"delta": {"content": "x"}}]}\n\n')
    await _drain_plain(provider, _openai_sse(finish))
    assert provider.last_stream_unterminated is False
    assert provider.last_finish_reason == finish


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_name", "ended", "cut"),
    [
        ("anthropic", _anthropic_sse("end_turn"), _anthropic_sse("end_turn").split("\n\n")[1]),
        ("gemini", _gemini_sse("STOP"), _gemini_sse("STOP").split("\n\n")[0]),
    ],
)
async def test_anthropic_and_gemini_plain_streams_report_a_cut(
    hass: Any, provider_name: str, ended: str, cut: str
) -> None:
    provider = create_provider(provider_name, hass, api_key="k")
    await _drain_plain(provider, cut + "\n\n")
    assert provider.last_stream_unterminated is True
    await _drain_plain(provider, ended)
    assert provider.last_stream_unterminated is False
