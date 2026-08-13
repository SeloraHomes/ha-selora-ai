"""Shared base for OpenAI-compatible LLM providers.

OpenAI, Ollama, and other compatible backends (LMStudio, Groq, Together, etc.)
all share the /v1/chat/completions format. This class implements the common
logic; thin subclasses only override identity and defaults.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
import logging
import re
from typing import TYPE_CHECKING, Any, cast

import aiohttp

from .base import LLMProvider

if TYPE_CHECKING:
    from ..tool_registry import ToolDef
    from ..types import LLMUsageInfo, OpenAIChatPayload

_LOGGER = logging.getLogger(__name__)

# The two spellings of the output-token cap on the chat-completions
# schema. Which one a request must use is a property of the *model*, not
# of the endpoint: OpenAI's reasoning families (o-series, GPT-5) reject
# ``max_tokens`` outright, while every other OpenAI-compatible backend —
# Ollama, LMStudio, gateways — only knows that name. The model field in
# settings is free text, so the pair is resolved per instance
# (``_token_cap_key``) and corrected from the server's own error when the
# guess is wrong (``repair_payload``).
TOKEN_CAP_KEYS = ("max_tokens", "max_completion_tokens")

# OpenAI answers a wrong spelling with HTTP 400 and names the replacement:
# "Unsupported parameter: 'max_tokens' is not supported with this model.
# Use 'max_completion_tokens' instead." Read the instruction rather than
# matching the sentence — other gateways phrase the surrounding prose
# differently while quoting the parameter the same way.
_USE_INSTEAD_RE = re.compile(r"[Uu]se '(?P<replacement>[A-Za-z0-9_]+)' instead")

# A 400 can also name a value the request has to carry rather than a key
# to rename: "Function tools with reasoning_effort are not supported for
# <model> in /v1/chat/completions. To use function tools, use
# /v1/responses or set reasoning_effort to 'none'." The parameter is one
# we never send — the model applies its own default — so the request
# cannot be fixed by editing what it already contains, only by stating
# the value explicitly.
_SET_PARAM_RE = re.compile(
    r"set '?(?P<param>[A-Za-z0-9_]+)'? to '(?P<value>[A-Za-z0-9_.-]+)'",
)

# A parameter another backend then rejects outright, so a directive
# picked up from one model can be dropped again rather than poisoning
# every later request.
_UNSUPPORTED_PARAM_RE = re.compile(
    r"[Uu]nsupported parameter: '(?P<param>[A-Za-z0-9_]+)'",
)

# Parameters a server is allowed to dictate. Narrow on purpose: honouring
# any "set X to Y" found in an error would let a backend quietly rewrite
# the sampling settings this integration chooses deliberately (a pinned
# temperature keeps structured tool-call output near-deterministic). What
# belongs here is a switch whose value only the server knows — the
# reasoning knob, whose very existence depends on the model behind a
# free-text name.
SERVER_DIRECTED_PARAMS = frozenset({"reasoning_effort"})


class OpenAICompatibleProvider(LLMProvider):
    """Shared implementation for OpenAI-compatible chat completions APIs."""

    #: Spelling of the output-token cap this backend accepts. Subclasses
    #: whose API mandates the other one override it; ``repair_payload``
    #: flips it at runtime when a model disagrees.
    _token_cap_key: str = "max_tokens"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Parameters this backend told us to send, learned from its own
        # error bodies (see repair_payload). Per instance, never
        # persisted: a config change rebuilds the provider, and one
        # retried request re-learns whatever the new model needs.
        self._server_directed_params: dict[str, Any] = {}

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

    @staticmethod
    def _adapt_image_blocks(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert neutral image blocks (see base.supports_vision) to
        OpenAI vision content: ``{"type": "image_url", "image_url": {"url":
        "data:<mime>;base64,<data>"}}``. Messages without neutral image
        blocks pass through untouched.
        """
        adapted: list[dict[str, Any]] = []
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list) or not any(
                isinstance(b, dict) and b.get("type") == "image" for b in content
            ):
                adapted.append(msg)
                continue
            blocks: list[dict[str, Any]] = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "image":
                    mime = b.get("media_type", "image/png")
                    blocks.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b.get('data', '')}"},
                        }
                    )
                else:
                    blocks.append(b)
            adapted.append({**msg, "content": blocks})
        return adapted

    def build_payload(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        max_tokens: int = 1024,
    ) -> OpenAIChatPayload:
        payload: OpenAIChatPayload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                *self._adapt_image_blocks(messages),
            ],
        }
        # Serialize the output cap into the body. Without this the
        # OpenAI-compatible cloud providers (OpenAI, OpenRouter, Selora
        # Cloud) fall back to the server's default completion cap and
        # the caller-chosen budget — e.g. the scaled analysis budget —
        # is silently ignored, truncating large responses. The key is
        # per-instance (see TOKEN_CAP_KEYS), so it goes in dynamically.
        cast("dict[str, Any]", payload)[self._token_cap_key] = max_tokens
        if tools:
            payload["tools"] = tools
        if stream:
            payload["stream"] = True
            # Ask the server for a final chunk with token usage so the
            # streaming path can be tracked. Servers that don't support
            # this option ignore it.
            payload["stream_options"] = {"include_usage": True}
        return payload

    def prepare_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply what this backend has already told us it needs.

        Runs after ``build_payload`` and after every subclass addition to
        it, so a directive learned from the server is the last word on the
        body. Nothing to apply on a provider that has never been
        corrected.
        """
        payload.update(self._server_directed_params)
        return payload

    def repair_payload(
        self,
        payload: dict[str, Any],
        status: int,
        body: str,
    ) -> dict[str, Any] | None:
        """Correct a rejected body using the instruction in the error itself.

        Three shapes, all of them the server naming its own remedy on a
        400 — a rename ("Use 'X' instead"), a value the request must state
        explicitly ("set X to 'Y'"), and the retraction of one
        ("Unsupported parameter: 'X'"). Everything else returns ``None``
        so the caller reports the real error.

        Each correction is remembered on the instance, so one rejected
        request pays the retry and every later call is right the first
        time. Which model needs which is never assumed: the same free-text
        model field can name a reasoning model, a legacy snapshot, or a
        local build, and the error body is the only thing that actually
        knows.
        """
        if status != 400:
            return None
        return (
            self._repair_token_cap_key(payload, body)
            or self._repair_directed_param(payload, body)
            or self._repair_rejected_param(payload, body)
        )

    def _repair_token_cap_key(self, payload: dict[str, Any], body: str) -> dict[str, Any] | None:
        """Rename the output-token cap when the server names the other spelling."""
        match = _USE_INSTEAD_RE.search(body)
        if not match:
            return None
        replacement = match.group("replacement")
        if replacement not in TOKEN_CAP_KEYS or replacement in payload:
            return None
        stale = next((key for key in TOKEN_CAP_KEYS if key != replacement), None)
        if stale is None or stale not in payload:
            return None
        repaired = dict(payload)
        repaired[replacement] = repaired.pop(stale)
        self._token_cap_key = replacement
        _LOGGER.debug(
            "%s: model rejected '%s'; retrying with '%s'",
            self.provider_name,
            stale,
            replacement,
        )
        return repaired

    def _repair_directed_param(self, payload: dict[str, Any], body: str) -> dict[str, Any] | None:
        """State a parameter's value explicitly when the server asks for it."""
        match = _SET_PARAM_RE.search(body)
        if not match:
            return None
        param = match.group("param")
        value = match.group("value")
        if param not in SERVER_DIRECTED_PARAMS or payload.get(param) == value:
            return None
        repaired = dict(payload)
        repaired[param] = value
        self._server_directed_params[param] = value
        _LOGGER.debug(
            "%s: model requires %s=%r; retrying with it set",
            self.provider_name,
            param,
            value,
        )
        return repaired

    def _repair_rejected_param(self, payload: dict[str, Any], body: str) -> dict[str, Any] | None:
        """Withdraw a directed parameter a backend now calls unsupported.

        Only parameters learned from a previous directive are withdrawn.
        Anything else in the body is ours by construction — dropping
        ``tools`` or the token cap to make a request succeed would answer
        the user with a silently degraded turn instead of an error.
        """
        match = _UNSUPPORTED_PARAM_RE.search(body)
        if not match:
            return None
        param = match.group("param")
        if param not in self._server_directed_params:
            return None
        self._server_directed_params.pop(param)
        if param not in payload:
            return None
        repaired = dict(payload)
        repaired.pop(param)
        _LOGGER.debug(
            "%s: model rejects '%s'; retrying without it",
            self.provider_name,
            param,
        )
        return repaired

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
            fn = tc.get("function") or {}
            name = fn.get("name")
            tc_id = tc.get("id")
            # Skip a malformed tool_call missing its id/name rather than
            # KeyError-ing the whole turn on a flaky gateway response.
            if not tc_id or not name:
                continue
            try:
                args = json.loads(fn["arguments"])
            except (
                json.JSONDecodeError,
                KeyError,
                TypeError,
            ):
                args = {}
            result.append(
                {
                    "id": tc_id,
                    "name": name,
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
        # The prose this round streamed, collected by stream_with_tools. Carried
        # on the FIRST synthesized assistant message only — repeating it per
        # tool call would read as the model having said it several times.
        narration = "".join(
            str(block.get("text", "")) for block in content_blocks if block.get("type") == "text"
        ).strip()
        # strict=False: cancel/watchdog early-break can leave results
        # shorter than tool_calls; pair what we have.
        for tc, res in zip(tool_calls, results, strict=False):
            assistant_msg: dict[str, Any] = {
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
            if narration:
                assistant_msg["content"] = narration
                narration = ""
            messages.append(assistant_msg)
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
        except (
            json.JSONDecodeError,
            ValueError,
        ):
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
        except (
            json.JSONDecodeError,
            ValueError,
        ):
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
                except (
                    json.JSONDecodeError,
                    ValueError,
                ):
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
                    # Also recorded, not just yielded: append_streaming_tool_results
                    # replays this round back to the model, and without the prose
                    # the model sees its own tool calls with no memory of what it
                    # said — so it re-orients from scratch and re-narrates the
                    # same sentence every round.
                    content_blocks.append({"type": "text", "text": content})
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
        except (
            aiohttp.ClientError,
            TimeoutError,
        ):
            _LOGGER.exception("%s health check failed", self.provider_name)
            return False
