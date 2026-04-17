"""Google Gemini LLM provider — native REST API.

Uses Google's native ``generateContent`` / ``streamGenerateContent``
endpoints with query-parameter authentication (``?key=…``) to avoid
"Multiple authentication credentials" errors inside Home Assistant.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.core import HomeAssistant

from ..const import DEFAULT_GEMINI_HOST, DEFAULT_GEMINI_MODEL, DEFAULT_LLM_TIMEOUT
from .base import LLMProvider

if TYPE_CHECKING:
    from ..tool_registry import ToolDef

_LOGGER = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    """Google Gemini API provider via native REST endpoints."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        api_key: str = "",
        model: str = "",
        host: str = "",
        **_kwargs: Any,
    ) -> None:
        super().__init__(
            hass,
            model=model or DEFAULT_GEMINI_MODEL,
            host=host or DEFAULT_GEMINI_HOST,
            api_key=api_key,
        )

    # -- Identity ----------------------------------------------------------

    @property
    def provider_name(self) -> str:
        return f"Google Gemini ({self._model})"

    # -- HTTP plumbing -----------------------------------------------------

    def _get_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    @property
    def _endpoint(self) -> str:
        return f"{self._host}/models/{self._model}:generateContent?key={self._api_key}"

    @property
    def _stream_endpoint(self) -> str:
        return (
            f"{self._host}/models/{self._model}:streamGenerateContent?key={self._api_key}&alt=sse"
        )

    # -- Payload & response ------------------------------------------------

    @staticmethod
    def _to_gemini_messages(
        system: str,
        messages: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Convert OpenAI-style messages to Gemini format.

        Returns (system_instruction, contents).
        """
        system_instruction: dict[str, Any] | None = None
        if system:
            system_instruction = {"parts": [{"text": system}]}

        contents: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            # Gemini uses "model" instead of "assistant"
            gemini_role = "model" if role == "assistant" else "user"

            parts: list[dict[str, Any]] = []
            content = msg.get("content")
            if content:
                parts.append({"text": content})

            # Tool call results from our side
            tool_responses = msg.get("_tool_responses")
            if tool_responses:
                for tr in tool_responses:
                    parts.append(
                        {
                            "functionResponse": {
                                "name": tr["name"],
                                "response": tr["result"],
                            }
                        }
                    )

            # Tool calls the model made
            tool_calls = msg.get("_tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    parts.append(
                        {
                            "functionCall": {
                                "name": tc["name"],
                                "args": tc["arguments"],
                            }
                        }
                    )

            if parts:
                contents.append({"role": gemini_role, "parts": parts})

        return system_instruction, contents

    def build_payload(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        system_instruction, contents = self._to_gemini_messages(system, messages)

        payload: dict[str, Any] = {"contents": contents}
        if system_instruction:
            payload["system_instruction"] = system_instruction
        if tools:
            # Gemini expects [{"function_declarations": [decl1, decl2, ...]}]
            payload["tools"] = [{"function_declarations": tools}]

        return payload

    def extract_text_response(self, response_data: dict[str, Any]) -> str | None:
        candidates = response_data.get("candidates", [])
        if not candidates:
            return None
        parts = candidates[0].get("content", {}).get("parts", [])
        texts = [p["text"] for p in parts if "text" in p]
        return "".join(texts) if texts else None

    def extract_tool_calls(self, response_data: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = response_data.get("candidates", [])
        if not candidates:
            return []
        parts = candidates[0].get("content", {}).get("parts", [])
        result: list[dict[str, Any]] = []
        for i, part in enumerate(parts):
            fc = part.get("functionCall")
            if fc:
                result.append(
                    {
                        "id": f"call_{i}",
                        "name": fc["name"],
                        "arguments": fc.get("args", {}),
                    }
                )
        return result

    def append_tool_result(
        self,
        messages: list[dict[str, Any]],
        response_data: dict[str, Any],
        tool_call: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        # Append the model's response (with the function call)
        if response_data.get("candidates"):
            messages.append(
                {
                    "role": "assistant",
                    "_tool_calls": [
                        {
                            "name": tool_call["name"],
                            "arguments": tool_call["arguments"],
                        }
                    ],
                }
            )
        # Append our function response
        messages.append(
            {
                "role": "user",
                "_tool_responses": [
                    {
                        "name": tool_call["name"],
                        "result": result,
                    }
                ],
            }
        )

    def append_streaming_tool_results(
        self,
        messages: list[dict[str, Any]],
        content_blocks: list[dict[str, Any]],
        tool_calls: list[dict[str, Any]],
        results: list[dict[str, Any]],
    ) -> None:
        # Append the model's tool calls
        messages.append(
            {
                "role": "assistant",
                "_tool_calls": [
                    {"name": tc["name"], "arguments": tc["arguments"]} for tc in tool_calls
                ],
            }
        )
        # Append all function responses in one user turn
        messages.append(
            {
                "role": "user",
                "_tool_responses": [
                    {"name": tc["name"], "result": res}
                    for tc, res in zip(tool_calls, results, strict=True)
                ],
            }
        )

    # -- Tool formatting ---------------------------------------------------

    def format_tool(self, tool: ToolDef) -> dict[str, Any]:
        """Return a single function declaration for Gemini."""
        openai_fmt = tool.to_openai()
        func = openai_fmt["function"]
        return {
            "name": func["name"],
            "description": func["description"],
            "parameters": func.get("parameters", {}),
        }

    # -- Streaming ---------------------------------------------------------

    def parse_stream_line(self, line: str) -> str | None:
        if not line.startswith("data: "):
            return None
        raw = line[6:]
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None
        candidates = obj.get("candidates", [])
        if not candidates:
            return None
        parts = candidates[0].get("content", {}).get("parts", [])
        texts = [p["text"] for p in parts if "text" in p]
        return "".join(texts) if texts else None

    async def stream_with_tools(
        self,
        resp: aiohttp.ClientResponse,
        tool_calls: list[dict[str, Any]],
        content_blocks: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        """Stream Gemini SSE, yielding text and collecting tool calls."""
        buffer = ""
        async for raw_chunk in resp.content.iter_any():
            buffer += raw_chunk.decode("utf-8")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line or not line.startswith("data: "):
                    continue
                raw = line[6:]
                try:
                    event = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue

                candidates = event.get("candidates", [])
                if not candidates:
                    continue
                parts = candidates[0].get("content", {}).get("parts", [])
                for part in parts:
                    if "text" in part:
                        yield part["text"]
                    fc = part.get("functionCall")
                    if fc:
                        tool_calls.append(
                            {
                                "id": f"call_{len(tool_calls)}",
                                "name": fc["name"],
                                "arguments": fc.get("args", {}),
                            }
                        )

    # -- Overrides for native endpoint -------------------------------------

    async def raw_request_stream(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
    ) -> AsyncIterator[aiohttp.ClientResponse]:
        """Open a streaming connection to the native Gemini endpoint."""
        session = self._get_session()
        payload = self.build_payload(
            system, messages, tools=tools, stream=True, max_tokens=max_tokens
        )

        async with session.post(
            self._stream_endpoint,
            headers=self._get_headers(),
            timeout=aiohttp.ClientTimeout(total=DEFAULT_LLM_TIMEOUT),
            json=payload,
        ) as resp:
            if resp.status != 200:
                from .base import _sanitize_error

                body = _sanitize_error(await resp.text())
                _LOGGER.error("Gemini stream failed: %s", body[:200])
                raise ConnectionError(f"Gemini stream: HTTP {resp.status}: {body[:200]}")
            yield resp

    async def send_request_stream(
        self,
        system: str,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[str]:
        """Yield text chunks from a streaming Gemini request."""
        session = self._get_session()
        payload = self.build_payload(system, messages, stream=True)

        async with session.post(
            self._stream_endpoint,
            headers=self._get_headers(),
            timeout=aiohttp.ClientTimeout(total=DEFAULT_LLM_TIMEOUT),
            json=payload,
        ) as resp:
            if resp.status != 200:
                from .base import _sanitize_error

                body = _sanitize_error(await resp.text())
                _LOGGER.error(
                    "Gemini stream failed: %s returned %s: %s",
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

    # -- Health check ------------------------------------------------------

    async def health_check(self) -> bool:
        """Verify the Gemini API key and model."""
        # Query the specific model endpoint — validates both key and model in one call.
        url = f"{self._host}/models/{self._model}?key={self._api_key}"
        try:
            session = self._get_session()
            async with session.get(
                url,
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_LLM_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    _LOGGER.error(
                        "Gemini health check failed: HTTP %s: %s",
                        resp.status,
                        body[:200],
                    )
                    return False
                return True
        except Exception:
            _LOGGER.exception("Gemini health check failed")
            return False
