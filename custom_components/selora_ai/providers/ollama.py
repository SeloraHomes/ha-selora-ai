"""Ollama LLM provider — local, OpenAI-compatible, no API key needed."""

from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant

from ..const import (
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    HEALTH_CHECK_TIMEOUT,
)
from .openai_compat import OpenAICompatibleProvider

_LOGGER = logging.getLogger(__name__)

# Substrings identifying multimodal model families in Ollama's catalog.
# Vision is per-model there — a text-only model silently ignores (or
# errors on) image_url blocks, so the capability flag has to look at the
# configured model name rather than the provider.
_VISION_MODEL_HINTS = (
    "llava",
    "llama4",
    "llama3.2-vision",
    "qwen2.5vl",
    "qwen3-vl",
    "qwen-vl",
    "gemma3",
    "minicpm-v",
    "moondream",
    "granite3.2-vision",
    "mistral-small3",
    "pixtral",
    "vision",
)

# Tool calling is per-model on Ollama and lives in the model's chat
# template, not in the server: a template without a tool block makes
# Ollama reject the whole request with HTTP 400 "<model> does not support
# tools" rather than ignoring the schema. ``POST /api/show`` reports the
# model's capabilities, and lists this string for templates that accept a
# tool schema — authoritative, so a hand-maintained model list can't rot.
_SHOW_PATH = "/api/show"
_TOOLS_CAPABILITY = "tools"

# How long a settled answer is trusted. Shorter than the cloud providers'
# 900s because an Ollama model is mutable under a fixed name — `ollama pull
# llama4` or recreating a custom model swaps the chat template without
# touching entry.data, so nothing reloads the entry — and because a stale
# True here is fatal rather than merely degraded: every turn dies on HTTP
# 400 until the cache expires. The probe is a local request, so paying it
# a few times an hour costs nothing.
_CAPABILITIES_TTL_S = 300.0

# Non-5xx statuses that still mean "ask again later". Reverse proxies in
# front of Ollama emit these under load or during a model swap, and a
# recovered tool-capable server must not stay pinned tool-less.
_RETRYABLE_STATUSES = frozenset({408, 425, 429})


class OllamaProvider(OpenAICompatibleProvider):
    """Ollama local LLM provider."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        model: str = "",
        host: str = "",
        **_kwargs: Any,
    ) -> None:
        super().__init__(
            hass,
            model=model or DEFAULT_OLLAMA_MODEL,
            host=host or DEFAULT_OLLAMA_HOST,
            api_key="",
        )
        # Discovered from /api/show by async_refresh_capabilities; None =
        # never answered (treated as no-tools). A model swap under the same
        # name changes the answer without changing entry.data, so the value
        # is TTL-cached rather than held for the life of the instance.
        # The fetch timestamp uses None as its never-fetched sentinel:
        # time.monotonic() counts from boot, so on a freshly started host a
        # 0.0 sentinel would read as "fetched recently" and suppress the
        # first probe.
        self._tools_capable: bool | None = None
        self._capabilities_fetched_at: float | None = None

    @property
    def provider_type(self) -> str:
        return "ollama"

    @property
    def provider_name(self) -> str:
        return f"Ollama ({self._model})"

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def is_local(self) -> bool:
        return True

    @property
    def supports_vision(self) -> bool:
        model = self._model.lower()
        return any(hint in model for hint in _VISION_MODEL_HINTS)

    @property
    def supports_tools(self) -> bool:
        # Per-model on Ollama — asked via async_refresh_capabilities,
        # never assumed. Unknown means False: sending a tool schema to a
        # model whose template can't take one loses the entire turn to an
        # HTTP 400, while withholding it only loses tool calling.
        return self._tools_capable is True

    async def async_refresh_capabilities(self) -> None:
        """Refresh the discovered capabilities — here, only tool support.

        Vision is inferred from the model name (``_VISION_MODEL_HINTS``), so
        the tool probe is the only thing to do.
        """
        await self.async_refresh_tool_capability()

    async def async_refresh_tool_capability(self) -> None:
        """Ask Ollama whether the configured model's template accepts tools.

        ``POST /api/show`` returns a ``capabilities`` array that lists
        ``"tools"`` for models whose template has a tool block. Only a
        conclusive answer is cached, and only for ``_CAPABILITIES_TTL_S``
        — a model can be re-pulled under the same name, which swaps its
        template without touching entry.data, so nothing else would ever
        invalidate the value. An inconclusive probe (transport failure,
        5xx, or a retryable 408/425/429) neither caches nor stamps, so the
        next call retries immediately and keeps whatever was last known
        rather than flapping to False on a blip. Never raises — this runs
        from the ``get_config`` websocket handler and, more importantly, on
        every tool-bearing chat turn.
        """
        now = time.monotonic()
        if (
            self._capabilities_fetched_at is not None
            and now - self._capabilities_fetched_at < _CAPABILITIES_TTL_S
        ):
            return
        try:
            session = self._get_session()
            async with session.post(
                f"{self._host}{_SHOW_PATH}",
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=HEALTH_CHECK_TIMEOUT),
                data=self._encode_body({"model": self._model}),
            ) as resp:
                if resp.status != 200:
                    if resp.status >= 500 or resp.status in _RETRYABLE_STATUSES:
                        # The server is reachable but not answering for a
                        # reason that says nothing about the model: still
                        # loading, rate-limited, or a proxy timeout. Leave
                        # the cache untouched so the next refresh retries
                        # instead of pinning a capable model tool-less.
                        _LOGGER.debug(
                            "Ollama /api/show returned HTTP %s; retrying capability probe later",
                            resp.status,
                        )
                        return
                    # The remaining 4xx are definitive: an Ollama predating
                    # /api/show 404s, a model that isn't pulled 404s too,
                    # and a server too old for the `model` field 400s.
                    self._tools_capable = False
                    self._capabilities_fetched_at = now
                    return
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError, ValueError):
            _LOGGER.debug("Ollama capabilities fetch failed; keeping cached value")
            return
        # Shape-check the body rather than trusting it: this runs on the
        # per-turn chat gate, so a TypeError/AttributeError here would take
        # down a chat turn, not just a capability read. The list check is
        # not only about raising — ``in`` against a bare string is a
        # substring test, so a scalar ``"capabilities": "tools_only"``
        # would otherwise report tool support that isn't there.
        raw = data.get("capabilities") if isinstance(data, dict) else None
        capabilities = raw if isinstance(raw, list) else []
        self._tools_capable = _TOOLS_CAPABILITY in capabilities
        self._capabilities_fetched_at = now

    async def health_check(self) -> bool:
        """Check Ollama is reachable and the model is pulled."""
        try:
            session = self._get_session()
            async with session.get(
                f"{self._host}/api/tags",
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=HEALTH_CHECK_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
                models = [m.get("name", "") for m in data.get("models", [])]
                if not any(self._model in m for m in models):
                    _LOGGER.warning(
                        "Model '%s' not found in Ollama. Available: %s",
                        self._model,
                        models,
                    )
                    return False
                return True
        except Exception:
            _LOGGER.exception("Ollama health check failed")
            return False
