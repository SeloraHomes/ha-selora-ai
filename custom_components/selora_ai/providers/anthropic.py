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
)
from .base import LLMProvider

if TYPE_CHECKING:
    from ..tool_registry import ToolDef

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

    async def stream_with_tools(
        self,
        resp: aiohttp.ClientResponse,
        tool_calls: list[dict[str, Any]],
        content_blocks: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        """Stream Anthropic SSE, yielding text tokens and collecting tool calls."""
        current_block: dict[str, Any] | None = None
        tool_input_json = ""

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

    # -- Health check ------------------------------------------------------

    async def health_check(self) -> bool:
        result, _error = await self.send_request(
            system="Respond with 'ok'",
            messages=[{"role": "user", "content": "Hi"}],
        )
        return result is not None
