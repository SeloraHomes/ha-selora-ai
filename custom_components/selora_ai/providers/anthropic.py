"""Anthropic (Claude) LLM provider — /v1/messages format."""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.core import HomeAssistant

from ..const import (
    ANTHROPIC_API_VERSION,
    ANTHROPIC_MESSAGES_ENDPOINT,
    DEFAULT_ANTHROPIC_HOST,
    DEFAULT_ANTHROPIC_MODEL,
    HEALTH_CHECK_TIMEOUT,
)
from .base import LLMProvider

if TYPE_CHECKING:
    from ..tool_registry import ToolDef
    from ..types import LLMUsageInfo

_LOGGER = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    """Anthropic Claude API provider."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        api_key: str = "",
        model: str = "",
        **_kwargs: Any,
    ) -> None:
        super().__init__(
            hass,
            model=model or DEFAULT_ANTHROPIC_MODEL,
            host=DEFAULT_ANTHROPIC_HOST,
            api_key=api_key,
        )

    # -- Identity ----------------------------------------------------------

    @property
    def provider_type(self) -> str:
        return "anthropic"

    @property
    def provider_name(self) -> str:
        return f"Anthropic ({self._model})"

    # -- HTTP plumbing -----------------------------------------------------

    def _get_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["x-api-key"] = self._api_key
            headers["anthropic-version"] = ANTHROPIC_API_VERSION
        return headers

    @property
    def _endpoint(self) -> str:
        return f"{self._host}{ANTHROPIC_MESSAGES_ENDPOINT}"

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
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
        if stream:
            payload["stream"] = True
        return payload

    def extract_text_response(self, response_data: dict[str, Any]) -> str | None:
        for block in response_data.get("content", []):
            if block.get("type") == "text":
                return block.get("text")
        return None

    def extract_usage(self, response_data: dict[str, Any]) -> LLMUsageInfo | None:
        usage = response_data.get("usage")
        if not isinstance(usage, dict):
            return None
        info: LLMUsageInfo = {}
        if "input_tokens" in usage:
            info["input_tokens"] = int(usage["input_tokens"])
        if "output_tokens" in usage:
            info["output_tokens"] = int(usage["output_tokens"])
        if "cache_creation_input_tokens" in usage:
            info["cache_creation_input_tokens"] = int(usage["cache_creation_input_tokens"])
        if "cache_read_input_tokens" in usage:
            info["cache_read_input_tokens"] = int(usage["cache_read_input_tokens"])
        return info or None

    def extract_tool_calls(self, response_data: dict[str, Any]) -> list[dict[str, Any]]:
        calls = []
        for block in response_data.get("content", []):
            if block.get("type") == "tool_use":
                calls.append(
                    {
                        "id": block["id"],
                        "name": block["name"],
                        "arguments": block.get("input", {}),
                    }
                )
        return calls

    def append_tool_result(
        self,
        messages: list[dict[str, Any]],
        response_data: dict[str, Any],
        tool_call: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        result_json = json.dumps(result, ensure_ascii=False, default=str)
        messages.append({"role": "assistant", "content": response_data["content"]})
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call["id"],
                        "content": result_json,
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
        messages.append({"role": "assistant", "content": content_blocks})
        for tc, res in zip(tool_calls, results, strict=True):
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tc["id"],
                            "content": json.dumps(res, ensure_ascii=False, default=str),
                        }
                    ],
                }
            )

    # -- Tool formatting ---------------------------------------------------

    def format_tool(self, tool: ToolDef) -> dict[str, Any]:
        return tool.to_anthropic()

    # -- Streaming ---------------------------------------------------------

    def parse_stream_line(self, line: str) -> str | None:
        if not line.startswith("data: "):
            return None
        raw = line[6:]
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None
        if obj.get("type") == "content_block_delta":
            return obj.get("delta", {}).get("text")
        return None

    def parse_stream_usage(self, line: str) -> LLMUsageInfo | None:
        if not line.startswith("data: "):
            return None
        try:
            obj = json.loads(line[6:])
        except (json.JSONDecodeError, ValueError):
            return None
        evt = obj.get("type", "")
        info: LLMUsageInfo = {}
        if evt == "message_start":
            usage = obj.get("message", {}).get("usage", {})
            for key in (
                "input_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            ):
                if key in usage:
                    info[key] = int(usage[key])  # type: ignore[literal-required]
        elif evt == "message_delta":
            usage = obj.get("usage", {})
            if "output_tokens" in usage:
                info["output_tokens"] = int(usage["output_tokens"])
        return info or None

    async def stream_with_tools(
        self,
        resp: aiohttp.ClientResponse,
        tool_calls: list[dict[str, Any]],
        content_blocks: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        """Stream Anthropic SSE, yielding text tokens and collecting tool calls."""
        current_block: dict[str, Any] | None = None
        tool_input_json = ""
        # Anthropic emits input_tokens in `message_start.usage` and the
        # final output_tokens in `message_delta.usage`; merge them so the
        # call counts toward both totals.
        stream_usage: LLMUsageInfo = {}

        buffer = ""
        async for raw_chunk in resp.content.iter_any():
            buffer += raw_chunk.decode("utf-8")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line or not line.startswith("data: "):
                    continue
                try:
                    event = json.loads(line[6:])
                except (json.JSONDecodeError, ValueError):
                    continue

                usage_part = self.parse_stream_usage(line)
                if usage_part:
                    stream_usage.update(usage_part)

                event_type = event.get("type", "")

                if event_type == "content_block_start":
                    block = event.get("content_block", {})
                    if block.get("type") == "text":
                        current_block = {"type": "text", "text": ""}
                    elif block.get("type") == "tool_use":
                        current_block = {
                            "type": "tool_use",
                            "id": block.get("id", ""),
                            "name": block.get("name", ""),
                        }
                        tool_input_json = ""

                elif event_type == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            yield text
                            if current_block and current_block["type"] == "text":
                                current_block["text"] += text
                    elif delta.get("type") == "input_json_delta":
                        tool_input_json += delta.get("partial_json", "")

                elif event_type == "content_block_stop":
                    if current_block:
                        if current_block["type"] == "text":
                            content_blocks.append(current_block)
                        elif current_block["type"] == "tool_use":
                            try:
                                args = json.loads(tool_input_json) if tool_input_json else {}
                            except json.JSONDecodeError:
                                args = {}
                            content_blocks.append(
                                {
                                    "type": "tool_use",
                                    "id": current_block["id"],
                                    "name": current_block["name"],
                                    "input": args,
                                }
                            )
                            tool_calls.append(
                                {
                                    "id": current_block["id"],
                                    "name": current_block["name"],
                                    "arguments": args,
                                }
                            )
                        current_block = None
                        tool_input_json = ""

        self._report_usage(stream_usage or None)

    # -- Health check ------------------------------------------------------

    async def health_check(self) -> bool:
        """Validate the Anthropic API key without burning a chat completion.

        ``GET /v1/models`` requires the ``x-api-key`` header and returns 401
        when the key is missing or invalid — an authoritative key check
        that completes in well under a second on a healthy upstream.
        """
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
                        "Anthropic health check failed: HTTP %s: %s",
                        resp.status,
                        body,
                    )
                    return False
                return True
        except (aiohttp.ClientError, TimeoutError):
            _LOGGER.exception("Anthropic health check failed")
            return False
