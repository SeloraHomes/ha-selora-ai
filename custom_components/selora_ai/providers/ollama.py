"""Ollama LLM provider — local, OpenAI-compatible, no API key needed."""

from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant

from ..const import (
    CONTEXT_WINDOW_PROBE_TTL_S,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    HEALTH_CHECK_TIMEOUT,
)
from .base import _positive_int
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

# GGUF header key holding the model's trained context length. It is
# architecture-prefixed — "llama.context_length", "qwen3.context_length",
# "gemma3.context_length" — and Ollama's catalog grows new architectures
# constantly, so match on the suffix instead of maintaining a list that
# would silently go stale. The suffix has to be exact: the same map
# carries "<arch>.embedding_length", "<arch>.attention.key_length" and
# friends.
_CONTEXT_LENGTH_SUFFIX = ".context_length"

# Modelfile PARAMETER name that sets the served window.
_NUM_CTX_PARAMETER = "num_ctx"


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
        # Served context window, discovered from POST /api/show by
        # async_refresh_capabilities. None = never asked or the probe
        # failed; it does NOT mean "unlimited" (see
        # LLMProvider.context_window). The fetch timestamp uses None as
        # its never-fetched sentinel because time.monotonic() counts from
        # boot: a 0.0 sentinel reads as "just fetched" on a host with less
        # uptime than the TTL and would suppress the first probe.
        self._context_window: int | None = None
        self._context_probe_at: float | None = None

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

    @property
    def context_window(self) -> int | None:
        # Discovered from POST /api/show; None until a probe succeeds.
        # See LLMProvider.context_window — None is "unknown", not
        # "unlimited".
        return self._context_window

    async def async_refresh_capabilities(self) -> None:
        """Ask Ollama about the configured model's tools and context window.

        Vision is inferred from the model name (``_VISION_MODEL_HINTS``),
        so what is left to discover is tool support and the served context
        window — and ``POST /api/show`` reports both in one body, so the
        broad hook makes a single request and reads both out of it (see
        ``_async_probe_show``).

        On the context window, that body answers with two relevant pieces:

        * ``parameters`` — the model's Modelfile ``PARAMETER`` lines. A
          ``num_ctx`` there is what the server will actually allocate,
          because Ollama resolves the window as
          default < Modelfile < per-request options, and this provider
          sends no per-request options (see the note below). This is the
          reported value.
        * ``model_info`` — the GGUF header, carrying the model's
          **trained** context length under an architecture-prefixed key
          (see ``_CONTEXT_LENGTH_SUFFIX``). Advisory only: it neither
          raises nor caps the reported window, because llama.cpp serves
          whatever window it was asked for. It is used to tell the user
          when their model is being served in less room than it was
          trained for — a 40K-trained model routinely runs in 4K.

        So the window is known only when the Modelfile sets ``num_ctx``.
        Without it the daemon's own default applies, which ``/api/show``
        does not report and no constant can stand in for — the value is
        unknown rather than guessed (see ``_apply_context_window``).

        Note on the missing per-request field: Ollama's *native*
        ``/api/chat`` accepts ``options.num_ctx``, but this provider
        speaks the OpenAI-compatible ``/v1/chat/completions`` endpoint,
        whose request schema has no context field at any level — upstream
        maps only stop / num_predict / temperature / seed / penalties /
        top_p onto Ollama options, and the PR proposing ``num_ctx`` there
        was closed unmerged. Sending one would be silently dropped, so we
        read the window and report it rather than pretend to set it. If a
        model is running smaller than it was trained for, the log line in
        ``_apply_context_window`` tells the user the two knobs that do work.
        """
        await self._async_probe_show(want_context=True)

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

        The context window lives in the same response but is deliberately
        left to the broad hook: this one runs per turn and must stay on the
        tool TTL, which is the shorter and more urgent of the two.
        """
        await self._async_probe_show(want_context=False)

    async def _async_probe_show(self, *, want_context: bool) -> None:
        """Run ``POST /api/show`` once and update whichever answer is due.

        Both discovered capabilities come out of this one body, so the
        request is shared — but each keeps its own TTL and its own
        failure semantics, because they fail differently:

        * Tools stamp only on a *conclusive* answer, so a blip re-probes
          on the next turn instead of pinning a capable model tool-less.
        * The context window stamps before the request, so a stopped
          Ollama isn't re-probed on every panel load.

        Never raises: a failure leaves both cached values untouched.
        """
        now = time.monotonic()
        tools_due = (
            self._capabilities_fetched_at is None
            or now - self._capabilities_fetched_at >= _CAPABILITIES_TTL_S
        )
        context_due = want_context and (
            self._context_probe_at is None
            or now - self._context_probe_at >= CONTEXT_WINDOW_PROBE_TTL_S
        )
        if not tools_due and not context_due:
            return
        if context_due:
            self._context_probe_at = now
        try:
            session = self._get_session()
            async with session.post(
                f"{self._host}{_SHOW_PATH}",
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=HEALTH_CHECK_TIMEOUT),
                data=self._encode_body({"model": self._model}),
            ) as resp:
                if resp.status != 200:
                    if not tools_due or resp.status >= 500 or resp.status in _RETRYABLE_STATUSES:
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
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            _LOGGER.debug("Ollama /api/show probe failed (%s); keeping cached values", exc)
            return
        # Shape-check the body rather than trusting it: this runs on the
        # per-turn chat gate, so a TypeError/AttributeError here would take
        # down a chat turn, not just a capability read.
        if not isinstance(data, dict):
            return
        if tools_due:
            self._apply_tool_capability(data, now)
        if context_due:
            self._apply_context_window(data)

    def _apply_tool_capability(self, data: dict[str, Any], now: float) -> None:
        """Record whether the model's template accepts a tool schema."""
        # The list check is not only about raising — ``in`` against a bare
        # string is a substring test, so a scalar
        # ``"capabilities": "tools_only"`` would otherwise report tool
        # support that isn't there.
        raw = data.get("capabilities")
        capabilities = raw if isinstance(raw, list) else []
        self._tools_capable = _TOOLS_CAPABILITY in capabilities
        self._capabilities_fetched_at = now

    def _apply_context_window(self, data: dict[str, Any]) -> None:
        """Record the served window from a body the probe managed to read.

        Only reached for a 200 whose body parsed as an object, so the
        absence of a ``num_ctx`` here is the server's answer, not a
        failure — which is why this may write ``None`` where the
        transport- and status-failure paths must not. A model recreated
        under the same name without ``PARAMETER num_ctx`` swaps a known
        window for the daemon default, and entry.data doesn't change, so
        nothing else would ever invalidate the old number; keeping it would
        report 32768 for a 4096-token window on every refresh from then on.
        Unknown is the safe state for this property, so a read that finds
        no window resets to it.
        """
        model_info = data.get("model_info")
        trained = self._trained_context_length(model_info) if isinstance(model_info, dict) else None
        parameters = data.get("parameters")
        configured = self._modelfile_num_ctx(parameters) if isinstance(parameters, str) else None

        if configured is None:
            # Without a Modelfile ``num_ctx`` the daemon's own default
            # applies, and ``/api/show`` never reports it. It is not a
            # constant either: the operator sets it with
            # ``OLLAMA_CONTEXT_LENGTH``, and it has changed between Ollama
            # releases. Substituting a compiled-in guess could report a
            # window *larger* than a server configured smaller — callers
            # would size prompts to room that isn't there, which is the one
            # direction this property must never be wrong in. The trained
            # length is no substitute: it is what the model was trained
            # for, not what the runtime allocates.
            if self._context_window is not None:
                _LOGGER.debug(
                    "Ollama model '%s' no longer sets `PARAMETER num_ctx`; "
                    "served context window is unknown again",
                    self._model,
                )
            self._context_window = None
            return
        # Reported as configured, deliberately not clamped to ``trained``:
        # llama.cpp allocates the window it is asked for and extrapolates
        # past the trained length (with a warning), so the larger number is
        # the one really being served. Clamping would under-report it and
        # make prompt sizing truncate room that exists.
        window = configured

        previous = self._context_window
        self._context_window = window
        if previous != window and trained is not None and window < trained:
            _LOGGER.info(
                "Ollama model '%s' is being served with a %d-token context window although it "
                "was trained for %d. The OpenAI-compatible API has no per-request context "
                "setting, so raise it with `PARAMETER num_ctx` in the model's Modelfile or the "
                "OLLAMA_CONTEXT_LENGTH environment variable on the Ollama server.",
                self._model,
                window,
                trained,
            )

    @staticmethod
    def _trained_context_length(model_info: dict[str, Any]) -> int | None:
        """Pull the trained context length out of a GGUF header map."""
        for key, value in model_info.items():
            if key.endswith(_CONTEXT_LENGTH_SUFFIX) and (parsed := _positive_int(value)):
                return parsed
        return None

    @staticmethod
    def _modelfile_num_ctx(parameters: str) -> int | None:
        """Parse ``num_ctx`` out of the Modelfile PARAMETER block.

        ``parameters`` is the raw text Ollama echoes back — one
        ``<name><whitespace><value>`` per line, e.g. ``num_ctx  32768``
        — and is absent entirely for a model that sets no parameters.
        """
        for line in parameters.splitlines():
            # split(maxsplit=1) collapses the run of padding spaces Ollama
            # uses to align values, and tolerates a tab.
            parts = line.split(maxsplit=1)
            if len(parts) == 2 and parts[0] == _NUM_CTX_PARAMETER:
                return _positive_int(parts[1])
        return None

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
