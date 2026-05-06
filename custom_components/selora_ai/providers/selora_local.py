"""Selora AI Local — local-inference HA add-on (libselora / Phi-3.5 INT8).

Speaks the OpenAI chat-completions protocol over HTTP. The add-on listens on
:5310 by default and exposes four LoRA specialists as model ids
(``selora-v1-{commands,automations,answers,clarifications}``).

The user never picks a specialist directly — the integration knows what the
current call is for (executing an action vs. building an automation vs.
answering a question vs. asking a clarification) and routes via
``set_call_kind`` set by ``LLMClient._usage_scope``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextvars import ContextVar
import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant

from ..const import DEFAULT_LLM_TIMEOUT, DEFAULT_SELORA_LOCAL_HOST
from .openai_compat import OpenAICompatibleProvider

_LOGGER = logging.getLogger(__name__)

# LoRA specialist per LLMClient call ``kind``. Anything not listed falls back
# to ``commands`` — the README's documented default.
#
# The four ``chat_*`` kinds are emitted by LLMClient's low-context architect
# path (see ``_classify_chat_intent``): a cheap regex pre-classifier picks
# the right specialist BEFORE the call so the LoRA can produce its native
# output format. Cloud providers ignore this — they only ever see ``chat``.
_KIND_TO_LORA: dict[str, str] = {
    "suggestions": "selora-v1-automations",  # pattern → automation generation
    "command": "selora-v1-commands",  # explicit "do X" command path
    "chat": "selora-v1-commands",  # raw chat (no pre-classifier)
    "chat_command": "selora-v1-commands",  # pre-classified: device control
    "chat_automation": "selora-v1-automations",  # pre-classified: rule building
    "chat_answer": "selora-v1-answers",  # pre-classified: Q&A / small talk
    "chat_clarification": "selora-v1-clarifications",  # pre-classified: ask back
    "chat_tool_round": "selora-v1-commands",  # continuation of chat with tools
    "session_title": "selora-v1-answers",  # short generative summary
    "health_check": "selora-v1-commands",  # any LoRA works
    "raw": "selora-v1-commands",  # send_request fallback
}
_DEFAULT_LORA = "selora-v1-commands"

# Phi-3.5 chat-template stop tokens. libselora's current sampler doesn't
# treat these as EOS, so the model prints them as plain text and then
# hallucinates further user/assistant turns. We strip everything from
# the first marker onward AND include them in the OpenAI ``stop`` param
# so the server can honor them once libselora ships a sampler upgrade.
_STOP_MARKERS: tuple[str, ...] = ("<|im_end|>", "<|endoftext|>", "<|end|>")
_MAX_MARKER_LEN = max(len(m) for m in _STOP_MARKERS)


def _truncate_at_stop(text: str) -> tuple[str, bool]:
    """Return (text-up-to-first-marker, found_any). No-op when no marker."""
    earliest = -1
    for marker in _STOP_MARKERS:
        idx = text.find(marker)
        if idx >= 0 and (earliest < 0 or idx < earliest):
            earliest = idx
    if earliest < 0:
        return text, False
    return text[:earliest], True


class SeloraLocalProvider(OpenAICompatibleProvider):
    """Selora AI Local provider (HA add-on, OpenAI-compatible)."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        host: str = "",
        **_kwargs: Any,
    ) -> None:
        super().__init__(
            hass,
            model=_DEFAULT_LORA,
            host=host or DEFAULT_SELORA_LOCAL_HOST,
            api_key="",
        )
        # Per-task LoRA selection. ContextVar so concurrent calls (e.g. a
        # background analysis cycle overlapping a panel chat request) don't
        # trample each other.
        self._call_kind: ContextVar[str | None] = ContextVar("selora_local_call_kind", default=None)
        # Per-call latch: once we've seen a stop marker in the SSE stream,
        # suppress every following chunk. Same concurrency reasoning as
        # _call_kind. Reset by ``set_call_kind`` at the start of each call.
        self._stop_seen: ContextVar[bool] = ContextVar("selora_local_stop_seen", default=False)
        # Trailing carry-over for cross-chunk stop-marker detection. SSE
        # frames split mid-token, so "<|im_end|>" frequently arrives as
        # "…today?<|im" + "_end|>…" in two chunks. We hold back the last
        # MAX_MARKER_LEN-1 chars of every emit and re-check on the next
        # chunk; flushed at end-of-stream by send_request_stream.
        self._stream_carry: ContextVar[str] = ContextVar("selora_local_stream_carry", default="")

    @property
    def provider_type(self) -> str:
        return "selora_local"

    @property
    def provider_name(self) -> str:
        return "Selora AI Local"

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def is_low_context(self) -> bool:
        # libselora ships with max_seq=1024 — anything larger gets truncated
        # by the engine before the model sees it.
        return True

    @property
    def supports_streaming(self) -> bool:
        # Disable streaming so the v2 router's `_normalize_automation_json`
        # post-processor can run on the full payload before the integration
        # parses it. With streaming on, the bytes pass through chunk-by-chunk
        # and the validator sees malformed automation output (missing alias,
        # singular keys, etc.) before the normalizer has a chance to fix it.
        # When the router is updated to also normalize streamed responses,
        # flip this back to True for the typing-animation effect.
        return False

    def set_call_kind(self, kind: str | None) -> None:
        self._call_kind.set(kind)
        self._stop_seen.set(False)
        self._stream_carry.set("")

    def _resolve_model(self) -> str:
        return _KIND_TO_LORA.get(self._call_kind.get(), _DEFAULT_LORA)

    def build_payload(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        # Swap the model into self._model so the parent payload + the usage
        # callback (which reports against self._model) both see the right
        # specialist for this call.
        self._model = self._resolve_model()
        payload = super().build_payload(
            system,
            messages,
            tools=tools,
            stream=stream,
            max_tokens=max_tokens,
        )
        # libselora ships with max_seq=1024 (input + output combined), so
        # the OpenAI default max_tokens=1024 leaves zero room for the
        # prompt. Cap output to a value that fits even with a few hundred
        # tokens of context. The engine has been observed to RST the
        # connection ("Connection reset by peer") when this is left at
        # its default.
        payload["max_tokens"] = min(int(payload.get("max_tokens", max_tokens)), 256)
        # The add-on's OpenAI-compat surface accepts the basic chat
        # fields only. Strip extensions some servers reject:
        # - tools: not implemented (we also disable tool-calling in the
        #   low-context chat branch upstream — this is defensive).
        # - stream_options: 2024 OpenAI extension; harmless when the
        #   server ignores it but a strict Pydantic validator may 422.
        payload.pop("tools", None)
        payload.pop("stream_options", None)
        # Tell the server to stop on Phi-3.5's chat-template tokens.
        # libselora's current sampler ignores this (greedy-only, no
        # custom stops), but we set it for forward-compatibility — and
        # we still post-process below as a safety net.
        payload["stop"] = list(_STOP_MARKERS)
        return payload

    # ── Stop-token defusal ────────────────────────────────────────────

    def extract_text_response(self, response_data: dict[str, Any]) -> str | None:
        text = super().extract_text_response(response_data)
        if text is None:
            return None
        truncated, _ = _truncate_at_stop(text)
        return truncated

    def parse_stream_line(self, line: str) -> str | None:
        # Once we've seen a stop marker for this call, swallow every
        # subsequent token — the model is hallucinating past EOS.
        if self._stop_seen.get():
            return None
        chunk = super().parse_stream_line(line)
        if not chunk:
            return chunk

        # Concatenate any held-back tail with this chunk so a marker
        # split across SSE frames is still detected.
        combined = self._stream_carry.get() + chunk
        truncated, found = _truncate_at_stop(combined)
        if found:
            self._stop_seen.set(True)
            self._stream_carry.set("")
            return truncated or None

        # No marker yet. Emit the safe prefix; hold back the trailing
        # window in case it's the start of a marker that completes in
        # the next chunk. Flushed by send_request_stream at end-of-stream.
        hold = _MAX_MARKER_LEN - 1
        if len(combined) <= hold:
            self._stream_carry.set(combined)
            return None
        emit = combined[:-hold]
        self._stream_carry.set(combined[-hold:])
        return emit

    async def send_request_stream(  # type: ignore[override]
        self,
        system: str,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[str]:
        # Wrap the parent stream so we can flush the carry-over tail
        # (held back by parse_stream_line for cross-chunk marker safety)
        # at end-of-stream. Without this, the last few chars of every
        # response would be lost.
        async for piece in super().send_request_stream(system, messages):
            yield piece
        if not self._stop_seen.get():
            tail = self._stream_carry.get()
            if tail:
                self._stream_carry.set("")
                yield tail

    async def health_check(self) -> bool:
        """Check the add-on is reachable on /health."""
        try:
            session = self._get_session()
            async with session.get(
                f"{self._host}/health",
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_LLM_TIMEOUT),
            ) as resp:
                return resp.status == 200
        except Exception:
            _LOGGER.exception("Selora Local health check failed")
            return False
