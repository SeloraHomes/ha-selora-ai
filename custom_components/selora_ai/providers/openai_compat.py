"""Shared base for OpenAI-compatible LLM providers.

OpenAI, Ollama, and other compatible backends (LMStudio, Groq, Together, etc.)
all share the /v1/chat/completions format. This class implements the common
logic; thin subclasses only override identity and defaults.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
import logging
from typing import TYPE_CHECKING, Any

import aiohttp

from .base import LLMProvider

if TYPE_CHECKING:
    from ..tool_registry import ToolDef
    from ..types import LLMUsageInfo

_LOGGER = logging.getLogger(__name__)


class OpenAICompatibleProvider(LLMProvider):
    """Shared implementation for OpenAI-compatible chat completions APIs."""

    # -- HTTP plumbing -----------------------------------------------------

    def _get_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    @property
    def _endpoint(self) -> str:
        return f"{self._host}/v1/chat/completions"

    # -- Payload & response ------------------------------------------------

    def build_payload(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, *messages],
        }
        if tools:
            payload["tools"] = tools
        if stream:
            payload["stream"] = True
            # Ask the server for a final chunk with token usage so the
            # streaming path can be tracked. Servers that don't support
            # this option ignore it.
            payload["stream_options"] = {"include_usage": True}
        return payload

    def extract_text_response(self, response_data: dict[str, Any]) -> str | None:
        choices = response_data.get("choices", [])
        if not choices:
            return None
        return choices[0].get("message", {}).get("content")

    def extract_usage(self, response_data: dict[str, Any]) -> LLMUsageInfo | None:
        usage = response_data.get("usage")
        if not isinstance(usage, dict):
            return None
        info: LLMUsageInfo = {}
        if "prompt_tokens" in usage:
            info["input_tokens"] = int(usage["prompt_tokens"])
        if "completion_tokens" in usage:
            info["output_tokens"] = int(usage["completion_tokens"])
        return info or None

    def extract_tool_calls(self, response_data: dict[str, Any]) -> list[dict[str, Any]]:
        choices = response_data.get("choices", [])
        if not choices:
            return []
        message = choices[0].get("message", {})
        tool_calls = message.get("tool_calls")
        if not tool_calls:
            return []
        result = []
        for tc in tool_calls:
            try:
                args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError, KeyError, TypeError:
                args = {}
            result.append(
                {
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "arguments": args,
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
        result_json = json.dumps(result, ensure_ascii=False, default=str)
        assistant_msg = response_data["choices"][0]["message"]
        messages.append(assistant_msg)
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": result_json,
            }
        )

    def append_streaming_tool_results(
        self,
        messages: list[dict[str, Any]],
        content_blocks: list[dict[str, Any]],
        tool_calls: list[dict[str, Any]],
        results: list[dict[str, Any]],
    ) -> None:
        for tc, res in zip(tool_calls, results, strict=True):
            messages.append(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"]),
                            },
                        }
                    ],
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(res, ensure_ascii=False, default=str),
                }
            )

    # -- Tool formatting ---------------------------------------------------

    def format_tool(self, tool: ToolDef) -> dict[str, Any]:
        return tool.to_openai()

    # -- Streaming ---------------------------------------------------------

    def parse_stream_line(self, line: str) -> str | None:
        if not line.startswith("data: "):
            return None
        raw = line[6:]
        if raw.strip() == "[DONE]":
            return None
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError, ValueError:
            return None
        choices = obj.get("choices", [])
        if choices:
            return choices[0].get("delta", {}).get("content")
        return None

    def parse_stream_usage(self, line: str) -> LLMUsageInfo | None:
        if not line.startswith("data: "):
            return None
        raw = line[6:]
        if raw.strip() == "[DONE]":
            return None
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError, ValueError:
            return None
        # Final chunk (stream_options.include_usage) carries usage on the
        # event itself, with empty `choices`.
        usage = obj.get("usage")
        if not isinstance(usage, dict):
            return None
        info: LLMUsageInfo = {}
        if "prompt_tokens" in usage:
            info["input_tokens"] = int(usage["prompt_tokens"])
        if "completion_tokens" in usage:
            info["output_tokens"] = int(usage["completion_tokens"])
        return info or None

    async def stream_with_tools(
        self,
        resp: aiohttp.ClientResponse,
        tool_calls: list[dict[str, Any]],
        content_blocks: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        """Stream OpenAI/Ollama SSE, yielding text tokens and collecting tool calls."""
        tc_accum: dict[int, dict[str, str]] = {}
        stream_usage: LLMUsageInfo = {}

        buffer = ""
        async for raw_chunk in resp.content.iter_any():
            buffer += raw_chunk.decode("utf-8")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line or not line.startswith("data: "):
                    continue
                raw = line[6:]
                if raw.strip() == "[DONE]":
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError, ValueError:
                    continue

                usage_part = self.parse_stream_usage(line)
                if usage_part:
                    stream_usage.update(usage_part)

                choices = event.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})

                content = delta.get("content")
                if content:
                    yield content

                for tc_delta in delta.get("tool_calls", []):
                    idx = tc_delta.get("index", 0)
                    if idx not in tc_accum:
                        tc_accum[idx] = {"id": tc_delta.get("id", ""), "name": "", "arguments": ""}
                    fn = tc_delta.get("function", {})
                    if fn.get("name"):
                        tc_accum[idx]["name"] = fn["name"]
                    if fn.get("arguments"):
                        tc_accum[idx]["arguments"] += fn["arguments"]

        # Finalize accumulated tool calls
        for _idx, tc_data in sorted(tc_accum.items()):
            try:
                args = json.loads(tc_data["arguments"]) if tc_data["arguments"] else {}
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(
                {
                    "id": tc_data["id"],
                    "name": tc_data["name"],
                    "arguments": args,
                }
            )

        self._report_usage(stream_usage or None)

    # -- Health check ------------------------------------------------------

    async def health_check(self) -> bool:
        """Verify the API key works without burning a chat completion.

        ``GET /v1/models`` requires the Authorization header and returns 401
        when the key is missing or invalid. That's an authoritative key
        check for OpenAI in well under a second — far better than a real
        chat completion that can take 5–15 s on a healthy upstream and
        DEFAULT_LLM_TIMEOUT (120 s) on a slow one.

        Subclasses whose ``/v1/models`` endpoint is public (e.g. OpenRouter)
        must override this with an authenticated probe that actually
        validates the key.
        """
        from ..const import HEALTH_CHECK_TIMEOUT

        try:
            session = self._get_session()
            async with session.get(
                f"{self._host}/v1/models",
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=HEALTH_CHECK_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    body = (await resp.text())[:200]
                    _LOGGER.error(
                        "%s health check failed: HTTP %s: %s",
                        self.provider_name,
                        resp.status,
                        body,
                    )
                    return False
                return True
        except aiohttp.ClientError, TimeoutError:
            _LOGGER.exception("%s health check failed", self.provider_name)
            return False
