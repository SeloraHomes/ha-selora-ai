"""Abstract base class for LLM providers.

Each provider implements the HTTP-level details (payload format, headers,
response parsing, streaming) while LLMClient handles business logic.

Template methods (send_request, raw_request, send_request_stream) are
concrete — they call abstract hooks so that subclasses only implement the
parts that differ between providers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
import json
import logging
import math
import re
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import (
    DEFAULT_LLM_TIMEOUT,
    DEFAULT_QUOTA_BACKOFF_SECONDS,
    EVENT_LLM_QUOTA_EXCEEDED,
)
from ..telemetry import record_activity
from ..types import LLMUsageInfo

if TYPE_CHECKING:
    from ..tool_registry import ToolDef

UsageCallback = Callable[[str, str, LLMUsageInfo], None]

_LOGGER = logging.getLogger(__name__)

_API_KEY_RE = re.compile(
    r"(sk-(?:ant-)?[A-Za-z0-9]{2})[A-Za-z0-9_-]{10,}",
)
_PROVIDER_KEY_ECHO_RE = re.compile(
    r"(Incorrect API key provided: )[^\s\"',}]+",
)

# Attempt indices for one request: the original body, then at most one
# retry with a body ``repair_payload`` corrected. A second repair is not
# offered — a server that rejects the corrected body is telling us
# something the error body no longer explains, and the user is better
# served by that error than by a provider guessing in a loop.
_REQUEST_ATTEMPTS = (0, 1)


def _sanitize_error(text: str) -> str:
    """Strip API keys / bearer tokens from error bodies."""
    text = _PROVIDER_KEY_ECHO_RE.sub(r"\1[REDACTED]", text)
    text = _API_KEY_RE.sub(r"\1***", text)
    return text


class RateLimitError(ConnectionError):
    """Raised when the upstream LLM returns HTTP 429 (quota / rate limit).

    Inherits from ``ConnectionError`` so existing handlers (e.g. the
    tool-calling loops in ``LLMClient._send_request_with_tools`` and
    ``_stream_request_with_tools``) continue to treat 429s as a
    controlled error. New code can ``isinstance(err, RateLimitError)``
    to access ``provider`` / ``retry_after`` for richer feedback.
    """

    def __init__(self, provider: str, message: str, retry_after: int | None) -> None:
        super().__init__(message)
        self.provider = provider
        self.retry_after = retry_after
        self.message = message


def _positive_int(value: object) -> int | None:
    """Coerce a scalar from a server's JSON metadata to a positive int.

    Capability probes read numbers straight out of upstream payloads, so
    the type is whatever the server felt like emitting: llama-server
    sends ``n_ctx`` as a JSON number, Ollama echoes GGUF integers that
    can arrive as ``40960.0``, and Modelfile ``PARAMETER`` lines are
    plain text. Anything non-numeric, non-integral, or ``<= 0`` is
    rejected as "no reading" rather than guessed at. Bools are rejected
    explicitly — ``isinstance(True, int)`` is True in Python, and a flag
    silently becoming a 1-token window is exactly the kind of nonsense
    these probes exist to prevent.

    Every rejection returns ``None``; nothing here raises. The callers are
    probes that promise a malformed body is non-fatal, and one of them
    runs on the chat path, so an exception escaping this coercion would
    cost a turn rather than a capability reading.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        # NaN and ±inf reach here: Python's json module accepts the
        # non-standard NaN/Infinity literals by default, and a numeric
        # literal too large for a float (1e400) parses to inf. int()
        # raises ValueError on the former and OverflowError on the latter,
        # so neither may be handed to it.
        if not math.isfinite(value):
            return None
        as_int = int(value)
        return as_int if as_int > 0 and float(as_int) == value else None
    if isinstance(value, str):
        try:
            as_int = int(value.strip())
        except (
            TypeError,
            ValueError,
        ):
            return None
        return as_int if as_int > 0 else None
    return None


def _parse_retry_after(value: str | None) -> int | None:
    """Parse a Retry-After header value into seconds.

    RFC 7231 allows either delta-seconds (an int) or an HTTP-date. We
    only handle delta-seconds — the date form is rare in practice for
    LLM APIs and not worth the parsing surface. Returns ``None`` for
    missing or malformed values.
    """
    if not value:
        return None
    try:
        seconds = int(value.strip())
    except (
        TypeError,
        ValueError,
    ):
        return None
    if seconds < 0:
        return None
    # Cap at 1h — protects the UI alert window from a server returning
    # an absurdly long backoff.
    return min(seconds, 3600)


class LLMProvider(ABC):
    """Abstract interface for an LLM HTTP backend."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        model: str = "",
        host: str = "",
        api_key: str = "",
    ) -> None:
        self._hass = hass
        self._model = model
        self._host = host.rstrip("/") if host else ""
        self._api_key = api_key
        self._usage_callback: UsageCallback | None = None

    # -- Usage tracking ----------------------------------------------------

    def set_usage_callback(self, callback: UsageCallback | None) -> None:
        """Register a callback invoked with token usage after each call."""
        self._usage_callback = callback

    def set_call_kind(self, kind: str | None) -> None:  # noqa: B027
        """Hint to the provider what *kind* of call is about to be made.

        Default: no-op. Providers that route to different backing models
        based on the call's purpose (e.g. SeloraLocal LoRA specialists)
        override this.
        """

    def set_chat_context(  # noqa: B027
        self,
        *,
        user_message: str = "",
        entities: list[Any] | None = None,
        existing_automations: list[dict[str, Any]] | None = None,
        history: list[dict[str, str]] | None = None,
        language: str | None = None,
    ) -> None:
        """Forward the raw chat context to the provider before send_request.

        Default: no-op. Selora AI Local overrides this to rebuild the
        outgoing system prompt + user content so each LoRA receives the
        EXACT format it was trained on (per-specialist system prompt,
        ``USER REQUEST: …`` body, multi-turn history). Cloud providers
        ignore it — they consume LLMClient's pre-built ``system`` and
        ``messages`` directly.
        """

    def convert_response_text(self, text: str) -> str:
        """Post-process a complete response text before LLMClient parses it.

        Default: pass-through. Selora AI Local overrides this to
        translate v0.4.2 slim output schemas (``{r, q}`` / ``{c, r}``
        / ``{q, o}``) into the ``{intent, response, calls/automation}``
        envelope LLMClient's parser expects. Called by
        ``parse_streamed_response`` after the stream finishes — the
        non-streaming path already runs the same conversion inside
        ``extract_text_response``.
        """
        return text

    def reset_streaming_state(self) -> None:  # noqa: B027
        """Drop any per-turn streaming state accumulated by the provider.

        Default: no-op. Providers that buffer raw chunks across a
        stream (e.g. Selora AI Local's ``_raw_response_buffer`` used
        by ``convert_response_text``) override this so a previous
        turn's buffer can't taint the next turn — particularly when a
        greeting short-circuit in ``architect_chat_stream`` yields a
        plain reply WITHOUT touching the streaming machinery that
        would normally reset state at the start of a real call.
        """

    def extract_usage(self, response_data: dict[str, Any]) -> LLMUsageInfo | None:
        """Extract token usage from a non-streaming response body.

        Default: no usage. Subclasses override for providers that return
        a usage block (Anthropic, OpenAI, Gemini).
        """
        return None

    def parse_stream_usage(self, line: str) -> LLMUsageInfo | None:
        """Extract token usage from a single SSE line during streaming.

        Default: no usage. Subclasses override to capture usage frames
        emitted by their stream protocol (Anthropic ``message_delta``,
        OpenAI final chunk with ``stream_options.include_usage``, Gemini
        ``usageMetadata`` per chunk).
        """
        return None

    def _report_usage(self, usage: LLMUsageInfo | None) -> None:
        """Forward usage to the registered callback, swallowing callback errors."""
        if not usage or not self._usage_callback:
            return
        if not (usage.get("input_tokens") or usage.get("output_tokens")):
            return
        model = usage.get("model") or self._model
        try:
            self._usage_callback(self.provider_type, model, usage)
        except Exception:  # noqa: BLE001 — never let telemetry break a request
            _LOGGER.exception("LLM usage callback failed")

    # -- Identity ----------------------------------------------------------

    @property
    @abstractmethod
    def provider_type(self) -> str:
        """Provider key matching a ``LLM_PROVIDER_*`` constant, e.g. ``'anthropic'``."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable name, e.g. 'Anthropic (claude-sonnet-4-6)'."""

    @property
    def requires_api_key(self) -> bool:
        """Whether this provider needs an API key to function."""
        return True

    @property
    def has_api_key(self) -> bool:
        """Whether an API key has been configured."""
        return bool(self._api_key)

    @property
    def is_configured(self) -> bool:
        """Whether the provider has the credentials it needs to make requests.

        Distinct from ``has_api_key`` so OAuth-style providers (Selora Cloud)
        can signal "unlinked" without claiming an API key. Defaults to
        ``has_api_key`` for key-based providers and ``True`` for those that
        don't need one (e.g. local Ollama).
        """
        return self.has_api_key if self.requires_api_key else True

    @property
    def model(self) -> str:
        """Return the configured model name."""
        return self._model

    @property
    def is_low_context(self) -> bool:
        """Whether this provider has a tight context window (≲2K tokens).

        LLMClient uses this to switch to a minimal system prompt and an
        aggressively filtered entity list — full home-state dumps don't
        fit in something like Selora AI Local's 1024-token window.
        """
        return False

    @property
    def is_local(self) -> bool:
        """Whether the model runs on the local network (no internet round-trip).

        Distinct from ``is_low_context``: that describes context-window
        capacity, this describes provider locality. Local backends have
        negligible first-token latency and emit stream keepalives, so the
        chat watchdog keeps a strict idle timeout for them; cloud
        providers get a looser floor to absorb proxy/first-token latency.
        """
        return False

    @property
    def supports_vision(self) -> bool:
        """Whether the provider accepts image content in user messages.

        A provider returning True must handle user messages whose
        ``content`` is a list of neutral blocks instead of a plain string:
        ``{"type": "text", "text": str}`` and
        ``{"type": "image", "media_type": str, "data": <base64 str>}``.
        ``build_payload`` converts those into the provider's wire format.
        Gated at the websocket layer, so providers returning False never
        receive image blocks.
        """
        return False

    @property
    def supports_tools(self) -> bool:
        """Whether the model behind this provider accepts a tool schema.

        Defaults to True — every cloud API this integration talks to takes
        a ``tools`` array on every model it exposes. Backends that serve
        arbitrary user-supplied models (Ollama) override this, because
        tool support there is a property of the *model's chat template*:
        a model without a tool block makes the server reject the whole
        request (Ollama answers HTTP 400 ``"<model> does not support
        tools"``), so the schema has to be withheld rather than sent
        hopefully. LLMClient gates tool-schema emission on this flag
        alongside ``is_low_context``.
        """
        return True

    @property
    def context_window(self) -> int | None:
        """Total tokens the backend serves for one request, or ``None``.

        ``None`` means **unknown** — never "unlimited". It is the value
        before anything has asked the server, after a probe failed, and
        for every provider that has no way to report it. Callers must
        read it as "no information" and keep whatever conservative
        behaviour they already had; deciding a prompt fits because the
        window is ``None`` gets that decision exactly backwards.

        The number is the *served* window (prompt + completion together),
        not the model's trained maximum: a model trained for 40K tokens
        loaded into a 4K runtime window serves 4K. Providers that can ask
        their backend populate it from ``async_refresh_capabilities``.
        """
        return None

    async def async_refresh_capabilities(self) -> None:  # noqa: B027 — deliberate no-op hook
        """Refresh every dynamically discovered capability.

        Covers ``supports_vision`` and ``context_window`` — anything the
        server owns and the client can only learn by asking. No-op for
        providers whose capabilities are static. Providers that route to a
        server-side-configured model (Selora Cloud), expose a catalog of
        them (OpenRouter), or run against a user-managed local server
        (Selora AI Local, Ollama) override this to ask the backend. Called
        from the ``get_config`` websocket handler and before an
        attachment-bearing turn, i.e. where a UI affordance depends on the
        answer; must never raise. This is the broad hook and may cost a
        request — do not call it on the per-turn chat path, which wants
        ``async_refresh_tool_capability`` instead.
        """

    async def async_refresh_tool_capability(self) -> None:  # noqa: B027 — deliberate no-op hook
        """Refresh ``supports_tools`` alone.

        Split out from ``async_refresh_capabilities`` because the chat tool
        gate runs on every tool-bearing turn and must not pay for the vision
        discovery (a catalog fetch, and for Selora Cloud a token refresh)
        that no text turn needs. No-op by default, which is correct for
        every provider whose ``supports_tools`` is the static True: there is
        nothing to discover. Ollama overrides it — its answer is per-model
        and mutable — and must keep it cached and non-raising.
        """

    # -- HTTP plumbing (abstract) ------------------------------------------

    @abstractmethod
    def _get_headers(self) -> dict[str, str]:
        """Build per-request HTTP headers."""

    @property
    @abstractmethod
    def _endpoint(self) -> str:
        """Full URL for the chat/messages endpoint."""

    # -- Payload & response (abstract) -------------------------------------

    @abstractmethod
    def build_payload(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Build the provider-specific request body."""

    @abstractmethod
    def extract_text_response(self, response_data: dict[str, Any]) -> str | None:
        """Pull the assistant's text from a response JSON body."""

    @abstractmethod
    def extract_tool_calls(self, response_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse tool calls from the response body."""

    @abstractmethod
    def append_tool_result(
        self,
        messages: list[dict[str, Any]],
        response_data: dict[str, Any],
        tool_call: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Append the assistant's tool request and our result to messages."""

    @abstractmethod
    def append_streaming_tool_results(
        self,
        messages: list[dict[str, Any]],
        content_blocks: list[dict[str, Any]],
        tool_calls: list[dict[str, Any]],
        results: list[dict[str, Any]],
    ) -> None:
        """Append tool results gathered during streaming to messages."""

    # -- Tool formatting (abstract) ----------------------------------------

    @abstractmethod
    def format_tool(self, tool: ToolDef) -> dict[str, Any]:
        """Serialize a ToolDef for this provider's API."""

    # -- Streaming (abstract) ----------------------------------------------

    @abstractmethod
    def parse_stream_line(self, line: str) -> str | None:
        """Extract a text token from a single SSE line."""

    @abstractmethod
    async def stream_with_tools(
        self,
        resp: aiohttp.ClientResponse,
        tool_calls: list[dict[str, Any]],
        content_blocks: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        """Stream SSE, yielding text tokens and collecting tool calls."""
        yield ""  # pragma: no cover — abstract

    # -- Health check (abstract) -------------------------------------------

    @abstractmethod
    async def health_check(self) -> bool:
        """Verify the LLM backend is reachable and configured."""

    # -- Payload repair ----------------------------------------------------

    def prepare_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Last word on a request body, applied after ``build_payload``.

        Subclasses build a body in layers — the compatible base writes the
        common shape and each subclass adds its own routing and sampling
        settings on top — so anything that must survive all of them cannot
        live inside ``build_payload``. This hook runs on the assembled
        body at the request boundary. Default: unchanged.
        """
        return payload

    def repair_payload(
        self,
        payload: dict[str, Any],
        status: int,
        body: str,
    ) -> dict[str, Any] | None:
        """Return a corrected request body for a rejected request, or ``None``.

        A vendor can change which parameters a *model* accepts without
        changing its API version — OpenAI's reasoning families reject
        ``max_tokens`` and name the replacement in the error body — so a
        payload that is right for one model on an endpoint is wrong for
        the next one the user types into the settings field. Hardcoding
        which model wants which spelling means shipping a release every
        time a vendor adds a family; the error body already carries the
        answer, so the provider reads it instead of guessing.

        Returning a dict makes the request template methods retry **once**
        with it. Providers that can't repair anything return ``None``
        (the default), which keeps the original error verbatim. An
        implementation must be conservative: repair only what the server
        explicitly asked for, since the alternative to a retry is a clear
        error message rather than a wrong one.
        """
        return None

    def _repaired_payload(
        self,
        payload: dict[str, Any],
        status: int,
        body: str,
        attempt: int,
    ) -> dict[str, Any] | None:
        """Ask ``repair_payload`` for a corrected body on the first attempt.

        Swallows an exception from the hook: the caller is already holding
        a perfectly good upstream error, and a bug in a repair heuristic
        must not replace it with a traceback on paths (``raw_request``,
        the streaming generators) that would otherwise let it escape.
        """
        if attempt != 0:
            return None
        try:
            return self.repair_payload(payload, status, body)
        except Exception:  # noqa: BLE001 — a repair bug must not mask the real error
            _LOGGER.exception("%s payload repair failed", self.provider_name)
            return None

    # -- Concrete template methods -----------------------------------------

    def _get_session(self) -> aiohttp.ClientSession:
        return async_get_clientsession(self._hass)

    def _encode_body(self, payload: dict[str, Any]) -> bytes:
        """Pre-serialise a JSON payload to bytes.

        We pass these bytes via aiohttp's ``data=`` kwarg instead of
        ``json=`` so the request goes out with a fixed
        ``Content-Length``. With ``json=``, Python 3.14 + aiohttp 3.13.5
        has been observed sending ``Transfer-Encoding: chunked`` and
        terminating the stream before the closing zero-chunk, which our
        gateway rejects with ``400 unexpected EOF``. Forcing a known
        body length avoids the chunked path entirely. The
        ``Content-Type`` header is set on the headers dict by callers
        (or by ``_get_headers``).
        """
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def _emit_quota_exceeded(self, retry_after: int | None, body: str) -> None:
        """Fire a HA event so the panel can surface a quota alert.

        Best-effort — never raise from here. The event is also useful
        for users wiring their own automations (notifications, etc.).
        """
        try:
            self._hass.bus.async_fire(
                EVENT_LLM_QUOTA_EXCEEDED,
                {
                    "provider": self.provider_type,
                    "model": self._model,
                    "retry_after": retry_after
                    if retry_after is not None
                    else DEFAULT_QUOTA_BACKOFF_SECONDS,
                    "message": body[:200],
                },
            )
            record_activity(self._hass, "llm_quota_exceeded")
        except Exception:  # noqa: BLE001 — telemetry must never break a request
            _LOGGER.exception("Failed to fire quota-exceeded event")

    async def _raise_if_rate_limited(self, resp: aiohttp.ClientResponse) -> None:
        """If ``resp`` is a 429, fire the quota event and raise RateLimitError.

        Centralizes the 429 handling so providers that override the base
        request methods (e.g. Gemini's native streaming endpoints) can
        opt in with one call instead of duplicating the parse + emit +
        raise logic. Returns silently for any other status.
        """
        if resp.status != 429:
            return
        body = _sanitize_error(await resp.text())
        retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
        self._emit_quota_exceeded(retry_after, body)
        hint = f" Try again in {retry_after}s." if retry_after else ""
        _LOGGER.warning(
            "LLM rate limited: %s (retry_after=%s, body=%s)",
            self.provider_name,
            retry_after,
            body[:300],
        )
        raise RateLimitError(
            self.provider_type,
            f"{self.provider_name} rate limit exceeded.{hint}",
            retry_after,
        )

    async def send_request(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1024,
        log_errors: bool = True,
        timeout: float | None = None,
    ) -> tuple[str | None, str | None]:
        """Send a request and return (response_text, error_message).

        ``log_errors=False`` suppresses the loud "LLM Request failed" log on
        non-200 responses. The error message is still returned so the caller
        can decide whether to retry, surface the failure, or stay silent —
        used by retry-on-cold-start paths to keep the first attempt quiet.

        ``timeout`` overrides the default per-request HTTP timeout (seconds);
        used by the hourly analysis cycle to allow longer reasoning calls.
        """
        try:
            session = self._get_session()
            payload = self.prepare_payload(
                self.build_payload(system, messages, max_tokens=max_tokens)
            )
            request_timeout = timeout if timeout is not None else DEFAULT_LLM_TIMEOUT

            for attempt in _REQUEST_ATTEMPTS:
                async with session.post(
                    self._endpoint,
                    headers=self._get_headers(),
                    timeout=aiohttp.ClientTimeout(total=request_timeout),
                    data=self._encode_body(payload),
                ) as resp:
                    if resp.status == 429:
                        # Helper fires the quota event and raises a
                        # RateLimitError; convert to the (None, error)
                        # tuple this method returns.
                        try:
                            await self._raise_if_rate_limited(resp)
                        except RateLimitError as exc:
                            return None, str(exc)
                    if resp.status != 200:
                        body = _sanitize_error(await resp.text())
                        repaired = self._repaired_payload(payload, resp.status, body, attempt)
                        if repaired is not None:
                            payload = repaired
                            continue
                        error_msg = f"HTTP {resp.status}: {body[:200]}"
                        if log_errors:
                            _LOGGER.error(
                                "LLM Request failed: %s returned %s: %s",
                                self.provider_name,
                                resp.status,
                                body[:500],
                            )
                        return None, error_msg

                    try:
                        data = await resp.json()
                    except Exception as exc:
                        body = await resp.text()
                        error_msg = f"JSON Parse Error: {str(exc)}"
                        _LOGGER.error("Failed to parse LLM JSON response: %s. Body: %s", exc, body)
                        return None, error_msg

                    self._report_usage(self.extract_usage(data))
                    text = self.extract_text_response(data)
                    if text is None:
                        _LOGGER.error(
                            "%s response missing expected content: %s",
                            self.provider_name,
                            data,
                        )
                        return None, "Response missing expected content"
                    return text, None
            return None, "Request abandoned after payload repair"

        except Exception as exc:
            _LOGGER.exception("Request to %s failed", self.provider_name)
            return None, _sanitize_error(str(exc))

    async def raw_request(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Low-level request returning the full parsed JSON response body."""
        session = self._get_session()
        payload = self.prepare_payload(
            self.build_payload(system, messages, tools=tools, max_tokens=4096)
        )

        for attempt in _REQUEST_ATTEMPTS:
            async with session.post(
                self._endpoint,
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_LLM_TIMEOUT),
                data=self._encode_body(payload),
            ) as resp:
                await self._raise_if_rate_limited(resp)
                if resp.status != 200:
                    body = _sanitize_error(await resp.text())
                    repaired = self._repaired_payload(payload, resp.status, body, attempt)
                    if repaired is not None:
                        payload = repaired
                        continue
                    raise ConnectionError(f"HTTP {resp.status}: {body[:200]}")
                data = await resp.json()
                self._report_usage(self.extract_usage(data))
                return data
        raise ConnectionError("Request abandoned after payload repair")

    async def raw_request_stream(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
    ) -> AsyncIterator[aiohttp.ClientResponse]:
        """Open a streaming HTTP connection and yield the response context.

        This is a single-item async generator that yields the open response
        so the caller can pass it to stream_with_tools(). The ``async with``
        in the generator keeps the response alive until the caller is done.

        Usage::

            async for resp in provider.raw_request_stream(system, msgs, tools=tools):
                async for text in provider.stream_with_tools(resp, ...):
                    yield text
        """
        session = self._get_session()
        payload = self.prepare_payload(
            self.build_payload(system, messages, tools=tools, stream=True, max_tokens=max_tokens)
        )

        # SSE streams: bound the initial connect and the gap between bytes,
        # not the wall-clock total — long completions can legitimately take
        # tens of seconds end-to-end and a `total=` timeout cuts them short.
        for attempt in _REQUEST_ATTEMPTS:
            async with session.post(
                self._endpoint,
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(connect=15, sock_read=DEFAULT_LLM_TIMEOUT),
                data=self._encode_body(payload),
            ) as resp:
                await self._raise_if_rate_limited(resp)
                if resp.status != 200:
                    body = _sanitize_error(await resp.text())
                    repaired = self._repaired_payload(payload, resp.status, body, attempt)
                    if repaired is not None:
                        payload = repaired
                        continue
                    _LOGGER.error("LLM stream failed: %s", body[:200])
                    raise ConnectionError(f"LLM stream: HTTP {resp.status}: {body[:200]}")
                yield resp
                return

    async def send_request_stream(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        """Async generator that yields text chunks from an SSE stream."""
        try:
            session = self._get_session()
            payload = self.prepare_payload(
                self.build_payload(system, messages, stream=True, max_tokens=max_tokens)
            )

            # See raw_request_stream above for why this uses connect +
            # sock_read instead of a wall-clock total.
            for attempt in _REQUEST_ATTEMPTS:
                async with session.post(
                    self._endpoint,
                    headers=self._get_headers(),
                    timeout=aiohttp.ClientTimeout(connect=15, sock_read=DEFAULT_LLM_TIMEOUT),
                    data=self._encode_body(payload),
                ) as resp:
                    await self._raise_if_rate_limited(resp)
                    if resp.status != 200:
                        body = _sanitize_error(await resp.text())
                        repaired = self._repaired_payload(payload, resp.status, body, attempt)
                        if repaired is not None:
                            payload = repaired
                            continue
                        _LOGGER.error(
                            "LLM stream failed: %s returned %s: %s",
                            self.provider_name,
                            resp.status,
                            body[:200],
                        )
                        try:
                            err_data = json.loads(body)
                            err_msg = err_data.get("error", {}).get("message", body[:200])
                        except (
                            ValueError,
                            AttributeError,
                        ):
                            err_msg = body[:200]
                        raise ConnectionError(f"{self.provider_name}: {err_msg}")

                    buffer = ""
                    stream_usage: LLMUsageInfo = {}
                    async for raw_chunk in resp.content.iter_any():
                        buffer += raw_chunk.decode("utf-8")
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if not line:
                                continue
                            usage_part = self.parse_stream_usage(line)
                            if usage_part:
                                stream_usage.update(usage_part)
                            text = self.parse_stream_line(line)
                            if text:
                                yield text
                    self._report_usage(stream_usage or None)
                    return
        except RateLimitError:
            # Already structured + telemetered — let it bubble up so the
            # panel surfaces a quota alert instead of a generic error.
            raise
        except Exception as exc:
            _LOGGER.exception("Streaming request to %s failed", self.provider_name)
            raise ConnectionError(
                f"Failed to connect to {self.provider_name}: {_sanitize_error(str(exc))}"
            ) from exc
