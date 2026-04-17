"""Abstract base class for LLM providers.

Each provider implements the HTTP-level details (payload format, headers,
response parsing, streaming) while LLMClient handles business logic.

Template methods (send_request, raw_request, send_request_stream) are
concrete — they call abstract hooks so that subclasses only implement the
parts that differ between providers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
import json
import logging
import re
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import DEFAULT_LLM_TIMEOUT

if TYPE_CHECKING:
    from ..tool_registry import ToolDef

_LOGGER = logging.getLogger(__name__)

_API_KEY_RE = re.compile(
    r"(sk-(?:ant-)?[A-Za-z0-9]{2})[A-Za-z0-9_-]{10,}",
)
_PROVIDER_KEY_ECHO_RE = re.compile(
    r"(Incorrect API key provided: )[^\s\"',}]+",
)


def _sanitize_error(text: str) -> str:
    """Strip API keys / bearer tokens from error bodies."""
    text = _PROVIDER_KEY_ECHO_RE.sub(r"\1[REDACTED]", text)
    text = _API_KEY_RE.sub(r"\1***", text)
    return text


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
    def model(self) -> str:
        """Return the configured model name."""
        return self._model

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

    # -- Concrete template methods -----------------------------------------

    def _get_session(self) -> aiohttp.ClientSession:
        return async_get_clientsession(self._hass)

    async def send_request(
        self,
        system: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 1024,
    ) -> tuple[str | None, str | None]:
        """Send a request and return (response_text, error_message)."""
        try:
            session = self._get_session()
            payload = self.build_payload(system, messages, max_tokens=max_tokens)

            async with session.post(
                self._endpoint,
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_LLM_TIMEOUT),
                json=payload,
            ) as resp:
                if resp.status != 200:
                    body = _sanitize_error(await resp.text())
                    error_msg = f"HTTP {resp.status}: {body[:200]}"
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

                text = self.extract_text_response(data)
                if text is None:
                    _LOGGER.error(
                        "%s response missing expected content: %s",
                        self.provider_name,
                        data,
                    )
                    return None, "Response missing expected content"
                return text, None

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
        payload = self.build_payload(system, messages, tools=tools, max_tokens=4096)

        async with session.post(
            self._endpoint,
            headers=self._get_headers(),
            timeout=aiohttp.ClientTimeout(total=DEFAULT_LLM_TIMEOUT),
            json=payload,
        ) as resp:
            if resp.status != 200:
                body = _sanitize_error(await resp.text())
                raise ConnectionError(f"HTTP {resp.status}: {body[:200]}")
            return await resp.json()

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
        payload = self.build_payload(
            system, messages, tools=tools, stream=True, max_tokens=max_tokens
        )

        async with session.post(
            self._endpoint,
            headers=self._get_headers(),
            timeout=aiohttp.ClientTimeout(total=DEFAULT_LLM_TIMEOUT),
            json=payload,
        ) as resp:
            if resp.status != 200:
                body = _sanitize_error(await resp.text())
                _LOGGER.error("LLM stream failed: %s", body[:200])
                raise ConnectionError(f"LLM stream: HTTP {resp.status}: {body[:200]}")
            yield resp

    async def send_request_stream(
        self,
        system: str,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[str]:
        """Async generator that yields text chunks from an SSE stream."""
        try:
            session = self._get_session()
            payload = self.build_payload(system, messages, stream=True)

            async with session.post(
                self._endpoint,
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_LLM_TIMEOUT),
                json=payload,
            ) as resp:
                if resp.status != 200:
                    body = _sanitize_error(await resp.text())
                    _LOGGER.error(
                        "LLM stream failed: %s returned %s: %s",
                        self.provider_name,
                        resp.status,
                        body[:200],
                    )
                    try:
                        err_data = json.loads(body)
                        err_msg = err_data.get("error", {}).get("message", body[:200])
                    except (ValueError, AttributeError):
                        err_msg = body[:200]
                    raise ConnectionError(f"{self.provider_name}: {err_msg}")

                buffer = ""
                async for raw_chunk in resp.content.iter_any():
                    buffer += raw_chunk.decode("utf-8")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        text = self.parse_stream_line(line)
                        if text:
                            yield text
        except Exception as exc:
            _LOGGER.exception("Streaming request to %s failed", self.provider_name)
            raise ConnectionError(
                f"Failed to connect to {self.provider_name}: {_sanitize_error(str(exc))}"
            ) from exc
