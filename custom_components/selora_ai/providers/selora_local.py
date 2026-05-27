"""Selora AI Local — local-inference provider (Qwen 2.5 1.5B + LoRAs).

Speaks directly to a `llama-server` process on the management host. The
integration owns LoRA selection, the single-flight swap lock, and the
JSON-envelope repair pipeline that defends against Qwen 1.5B drift.

Earlier versions of the stack ran a Python LoRA-router process between
the integration and `llama-server`; that layer was retired and its
behavior folded into this provider.

Per-request flow on the management host:

1. The provider maps the LLMClient call kind (``chat_command``,
   ``chat_automation``, …) to a specialist model id
   (``selora-v1-{specialist}``) and then to a LoRA index 0–3.
2. The provider takes the per-instance ``asyncio.Lock`` and POSTs to
   ``llama-server``'s ``/lora-adapters`` endpoint, activating just the
   target adapter (other scales 0.0). Cached when already active.
3. The OpenAI-compat ``/v1/chat/completions`` request is sent.
4. The full response runs through ``normalize_response_content`` —
   markdown fences, single-quoted strings, unknown intents, and
   missing-alias automations all get repaired before HA's automation
   validator sees the JSON.
5. Lock is released. Streaming requests hold the lock for the lifetime
   of the upstream stream so a concurrent call can't swap LoRA mid-tokens.

The single-flight lock matters because llama-server's
``/lora-adapters`` POST switches the active adapter for ALL subsequent
requests until the next switch; without serialization, two concurrent
calls would race.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
import json
import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant

from ..const import DEFAULT_SELORA_LOCAL_HOST, HEALTH_CHECK_TIMEOUT
from ._qwen_repair import normalize_response_content
from .openai_compat import OpenAICompatibleProvider

_LOGGER = logging.getLogger(__name__)

# Specialist model id → LoRA index. Order MUST match the order of
# ``--lora`` flags passed to ``llama-server`` in the management-host
# entrypoint. Index = position in --lora list (0-based).
_MODEL_TO_LORA_IDX: dict[str, int] = {
    "selora-v1-commands": 0,
    "selora-v1-automations": 1,
    "selora-v1-answers": 2,
    "selora-v1-clarifications": 3,
}
_NUM_LORAS = len(_MODEL_TO_LORA_IDX)
_DEFAULT_LORA_IDX = 0  # commands

# LoRA specialist per LLMClient call ``kind``. Anything not listed falls
# back to ``commands``. The four ``chat_*`` kinds are emitted by
# LLMClient's low-context architect path (``_classify_chat_intent``): a
# cheap regex pre-classifier picks the right specialist BEFORE the call
# so the LoRA can produce its native output format. Cloud providers
# ignore this — they only ever see ``chat``.
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

# Per-intent max_tokens ceilings. At ~5 tok/s on the 1.7B target, every
# unnecessary output token costs ~200ms of wall time, so cap aggressively
# per shape. Used as ``min(caller_value, cap)`` — callers that ask for
# less still get less. Kinds not listed pass the caller value through.
_MAX_TOKENS_PER_KIND: dict[str, int] = {
    "chat_answer": 192,  # 1-3 sentence prose reply
    "chat_clarification": 96,  # one short follow-up question
    "chat_command": 320,  # JSON envelope + 1-2 service calls
    "chat_automation": 1024,  # multi-trigger/action JSON (kept generous)
    "suggestions": 1024,  # pattern → automation generation
    "command": 320,
    "session_title": 32,
    "health_check": 16,
}

# Call kinds whose output is plain prose (no JSON envelope to repair)
# AND for which the integration is fine landing on intent=answer
# downstream. These stream natively; everything else (including
# chat_clarification — short anyway, and needs intent re-tagging via
# extract_text_response on the non-streaming path) collapses to a
# single chunk after the non-streaming round-trip.
_PROSE_KINDS: frozenset[str] = frozenset({"chat_answer", "session_title"})

# Call kinds where the model emits plain prose but the parser must
# still see a specific intent in the wrapped envelope. The provider
# rewrites intent=answer → the kind's intent after normalize runs.
_KIND_TO_INTENT: dict[str, str] = {
    "chat_clarification": "clarification",
}


class SeloraLocalProvider(OpenAICompatibleProvider):
    """Selora AI Local provider (talks directly to llama-server)."""

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
        # Per-task LoRA selection. ContextVar so concurrent calls (e.g.
        # a background analysis cycle overlapping a panel chat request)
        # don't trample each other.
        self._call_kind: ContextVar[str | None] = ContextVar("selora_local_call_kind", default=None)
        # Single-flight lock around (set-LoRA, do-chat). llama-server's
        # /lora-adapters endpoint switches the active adapter globally,
        # so two concurrent calls would race without serialization.
        self._lora_lock = asyncio.Lock()
        # Cache the currently-active LoRA index so repeat calls to the
        # same specialist skip the extra HTTP roundtrip.
        self._active_lora_idx: int | None = None

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
        # Q4_K_M Qwen 2.5 1.5B on the hub runs at ctx_size=8192. The
        # caller-side prompt-budget logic still treats this as
        # "low-context" relative to cloud GPT-4o etc.
        return True

    @property
    def supports_streaming(self) -> bool:
        # Prose intents (answer/clarification) skip JSON-envelope repair
        # and stream natively; JSON intents (command/automation) still
        # wait for the full payload so normalize_response_content can run.
        # send_request_stream enforces this per-call by routing JSON
        # intents through the non-streaming path.
        return True

    def set_call_kind(self, kind: str | None) -> None:
        self._call_kind.set(kind)

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
        # Set self._model so the parent payload + the usage callback
        # (which reports against self._model) both see the right
        # specialist for this call.
        self._model = self._resolve_model()
        # Clamp max_tokens to the per-intent ceiling so we never burn
        # decode time running to the default 1024 ceiling on intents
        # that only need ~64 tokens of output.
        cap = _MAX_TOKENS_PER_KIND.get(self._call_kind.get() or "")
        if cap is not None:
            max_tokens = min(max_tokens, cap)
        payload = super().build_payload(
            system,
            messages,
            tools=tools,
            stream=stream,
            max_tokens=max_tokens,
        )
        # OpenAICompatibleProvider.build_payload accepts max_tokens but
        # never writes it to the body, so we must set it here ourselves —
        # without this, llama-server falls back to its own default ceiling
        # (typically n_ctx/2) and the per-intent caps are meaningless.
        payload["max_tokens"] = max_tokens
        # llama-server accepts the basic OpenAI chat fields. Strip
        # extensions that some servers reject:
        # - tools: not supported by llama-server's compat layer (we also
        #   disable tool-calling in the low-context chat branch upstream
        #   — this is defensive).
        # - stream_options: 2024 OpenAI extension; harmless when ignored
        #   but a strict Pydantic validator may 422.
        payload.pop("tools", None)
        payload.pop("stream_options", None)
        # Mitigate Qwen 2.5's tendency to spiral into repetition loops
        # at temp=0. The integration sends temperature=0 for
        # deterministic JSON; without a repeat penalty the model often
        # repeats `{"service":...}` blocks until max_tokens is hit. 1.15
        # is the llama.cpp default sweet spot.
        payload.setdefault("repeat_penalty", 1.15)
        payload.setdefault("repeat_last_n", 256)
        return payload

    # ── LoRA activation ──────────────────────────────────────────────

    async def _activate_lora(self, idx: int) -> None:
        """POST /lora-adapters with scales [0,...,1@idx,...,0]."""
        if self._active_lora_idx == idx:
            return
        scales = [{"id": i, "scale": 1.0 if i == idx else 0.0} for i in range(_NUM_LORAS)]
        session = self._get_session()
        async with session.post(
            f"{self._host}/lora-adapters",
            json=scales,
            timeout=aiohttp.ClientTimeout(total=HEALTH_CHECK_TIMEOUT),
        ) as resp:
            if resp.status >= 400:
                body = (await resp.text())[:200]
                raise aiohttp.ClientResponseError(
                    request_info=resp.request_info,
                    history=resp.history,
                    status=resp.status,
                    message=f"/lora-adapters {resp.status}: {body}",
                )
        self._active_lora_idx = idx
        _LOGGER.debug("LoRA activated: idx=%d", idx)

    @asynccontextmanager
    async def _with_active_lora(self) -> AsyncIterator[None]:
        """Take the single-flight lock and activate the LoRA matching
        the current call kind. Holds the lock until the body exits — for
        streaming, that's the full lifetime of the upstream stream."""
        idx = _MODEL_TO_LORA_IDX.get(self._resolve_model(), _DEFAULT_LORA_IDX)
        async with self._lora_lock:
            await self._activate_lora(idx)
            yield

    # ── Request overrides ────────────────────────────────────────────

    async def send_request(  # type: ignore[override]
        self,
        system: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 1024,
        log_errors: bool = True,
        timeout: float | None = None,
    ) -> tuple[str | None, str | None]:
        try:
            async with self._with_active_lora():
                return await super().send_request(
                    system,
                    messages,
                    max_tokens=max_tokens,
                    log_errors=log_errors,
                    timeout=timeout,
                )
        except aiohttp.ClientError as exc:
            if log_errors:
                _LOGGER.exception("LoRA activation failed")
            return None, f"LoRA activation failed: {exc}"

    async def raw_request(  # type: ignore[override]
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        async with self._with_active_lora():
            return await super().raw_request(system, messages, tools=tools)

    async def send_request_stream(  # type: ignore[override]
        self,
        system: str,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[str]:
        # Prose intents (answer/clarification) emit plain text — no JSON
        # envelope to repair, so chunks can flow through unchanged. JSON
        # intents (command/automation) require the full payload to run
        # through normalize_response_content; for those we collapse to a
        # single chunk after the non-streaming round-trip.
        if (self._call_kind.get() or "") in _PROSE_KINDS:
            async with self._with_active_lora():
                async for chunk in super().send_request_stream(system, messages):
                    yield chunk
            return

        result, error = await self.send_request(system, messages)
        if error:
            # Stream consumers (architect_chat_stream → websocket handler)
            # only surface errors to the user when the generator raises
            # ConnectionError; silently returning would persist an empty
            # assistant message and report a successful "done" event.
            raise ConnectionError(f"{self.provider_name}: {error}")
        if result:
            yield result

    # ── Response normalization ───────────────────────────────────────

    def extract_text_response(self, response_data: dict[str, Any]) -> str | None:
        text = super().extract_text_response(response_data)
        if text is None:
            return None
        normalized = normalize_response_content(text)
        # The LoRA emits plain prose for chat_clarification (per the
        # prose-only system prompt), which normalize_response_content
        # wraps as {"intent":"answer",...} via coerce_to_answer. Re-tag
        # to the call's true intent so parse_architect_response and the
        # panel both classify the response correctly.
        target_intent = _KIND_TO_INTENT.get(self._call_kind.get() or "")
        if target_intent is None:
            return normalized
        try:
            body = json.loads(normalized)
        except json.JSONDecodeError:
            return normalized
        if isinstance(body, dict) and body.get("intent") == "answer":
            body["intent"] = target_intent
            return json.dumps(body, separators=(",", ":"))
        return normalized

    # ── Health check ─────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Verify llama-server is reachable on /health."""
        try:
            session = self._get_session()
            async with session.get(
                f"{self._host}/health",
                timeout=aiohttp.ClientTimeout(total=HEALTH_CHECK_TIMEOUT),
            ) as resp:
                return resp.status == 200
        except aiohttp.ClientError, TimeoutError:
            _LOGGER.exception("Selora Local health check failed")
            return False
