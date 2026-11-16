"""Google Gemini LLM provider — native REST API.

Uses Google's native ``generateContent`` / ``streamGenerateContent``
endpoints with query-parameter authentication (``?key=…``) to avoid
"Multiple authentication credentials" errors inside Home Assistant.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
import json
import logging
from typing import TYPE_CHECKING, Any
import uuid

import aiohttp
from homeassistant.core import HomeAssistant

from ..const import DEFAULT_GEMINI_HOST, DEFAULT_GEMINI_MODEL, DEFAULT_LLM_TIMEOUT
from .base import LLMProvider, model_is_known_large

if TYPE_CHECKING:
    from ..tool_registry import ToolDef
    from ..types import LLMUsageInfo

_LOGGER = logging.getLogger(__name__)

# Gemini 3 models sign each step's function call with a ``thoughtSignature``
# and refuse the next request (HTTP 400, "Function call is missing a
# thought_signature in functionCall parts") unless the call is replayed with
# it. A call the model never made — one we write into the history ourselves —
# has no signature to replay; Google documents this value as the one that
# skips the check for such a call. The field is protobuf ``bytes``, which
# REST JSON carries base64-encoded as every real signature arrives, so the
# sentinel goes out encoded the same way (the API also takes it bare).
SYNTHETIC_THOUGHT_SIGNATURE = base64.b64encode(b"skip_thought_signature_validator").decode()

# Stamped on a response so the calls of one model turn are replayed as one
# turn. The signature rides on the turn's FIRST call only; a parallel call
# split into a turn of its own would be an unsigned step.
_ROUND_KEY = "_selora_round"


def _tool_call_from_part(part: dict[str, Any], call_id: str) -> dict[str, Any] | None:
    """The neutral tool call in a ``functionCall`` part, keeping its signature."""
    fc = part.get("functionCall")
    if not isinstance(fc, dict) or not fc.get("name"):
        return None
    call: dict[str, Any] = {
        "id": call_id,
        "name": fc["name"],
        "arguments": fc.get("args", {}),
    }
    signature = part.get("thoughtSignature")
    if isinstance(signature, str) and signature:
        call["thought_signature"] = signature
    return call


def _replayed_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    """A tool call as it goes back into the history, signature included."""
    replayed: dict[str, Any] = {
        "name": tool_call["name"],
        "arguments": tool_call["arguments"],
    }
    if tool_call.get("thought_signature"):
        replayed["thought_signature"] = tool_call["thought_signature"]
    elif tool_call.get("synthetic"):
        replayed["thought_signature"] = SYNTHETIC_THOUGHT_SIGNATURE
    return replayed


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
    def provider_type(self) -> str:
        return "gemini"

    @property
    def provider_name(self) -> str:
        return f"Google Gemini ({self._model})"

    @property
    def supports_vision(self) -> bool:
        return True

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
            if isinstance(content, list):
                # Neutral content blocks (see base.supports_vision) — text
                # becomes a text part, images become inlineData parts.
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and block.get("text"):
                        parts.append({"text": block["text"]})
                    elif block.get("type") == "image":
                        parts.append(
                            {
                                "inlineData": {
                                    "mimeType": block.get("media_type", "image/png"),
                                    "data": block.get("data", ""),
                                }
                            }
                        )
            elif content:
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
                    call_part: dict[str, Any] = {
                        "functionCall": {
                            "name": tc["name"],
                            "args": tc["arguments"],
                        }
                    }
                    if tc.get("thought_signature"):
                        call_part["thoughtSignature"] = tc["thought_signature"]
                    parts.append(call_part)

            if parts:
                contents.append({"role": gemini_role, "parts": parts})

        return system_instruction, contents

    @property
    def holds_full_tool_schema(self) -> bool:
        """Per MODEL. Gemini 1.0 Pro served exactly 32K — the threshold with no
        margin for prompt, snapshot and reply — while 1.5 onward is ≥1M, so this
        cannot be a blanket yes either."""
        return model_is_known_large(self._model)

    def build_payload(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        max_tokens: int = 1024,  # noqa: ARG002 — see the note on generationConfig below
    ) -> dict[str, Any]:
        """Build a native ``generateContent`` body.

        No ``generationConfig.maxOutputTokens``: on the 2.5 series that cap
        counts thinking tokens as well as answer tokens, so a chat turn's
        1024 can be spent entirely on the thinking pass and return an empty
        candidate with ``finishReason: MAX_TOKENS``. The caller's
        ``max_tokens`` is therefore a budget this backend does not enforce
        — it runs against the model's own output limit instead.
        """
        system_instruction, contents = self._to_gemini_messages(system, messages)

        payload: dict[str, Any] = {"contents": contents}
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        if tools:
            # Gemini expects [{"functionDeclarations": [decl1, decl2, ...]}]
            payload["tools"] = [{"functionDeclarations": tools}]

        return payload

    def extract_text_response(self, response_data: dict[str, Any]) -> str | None:
        candidates = response_data.get("candidates", [])
        if not candidates:
            return None
        parts = candidates[0].get("content", {}).get("parts", [])
        texts = [p["text"] for p in parts if "text" in p]
        return "".join(texts) if texts else None

    @staticmethod
    def _usage_from_metadata(meta: dict[str, Any]) -> LLMUsageInfo | None:
        """Read a Gemini ``usageMetadata`` block into the neutral usage shape.

        ``candidatesTokenCount`` covers only the tokens in the answer. A
        2.5-series model also emits ``thoughtsTokenCount`` for its thinking
        pass, which Google bills at the output rate — counting the answer
        alone under-reports the cost of exactly the models this provider
        defaults to. The two are summed into ``output_tokens``.
        """
        info: LLMUsageInfo = {}
        if "promptTokenCount" in meta:
            info["input_tokens"] = int(meta["promptTokenCount"])
        output = int(meta.get("candidatesTokenCount", 0)) + int(meta.get("thoughtsTokenCount", 0))
        if output:
            info["output_tokens"] = output
        return info or None

    def extract_usage(self, response_data: dict[str, Any]) -> LLMUsageInfo | None:
        meta = response_data.get("usageMetadata")
        if not isinstance(meta, dict):
            return None
        return self._usage_from_metadata(meta)

    def extract_tool_calls(self, response_data: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = response_data.get("candidates", [])
        if not candidates:
            return []
        parts = candidates[0].get("content", {}).get("parts", [])
        result: list[dict[str, Any]] = []
        for i, part in enumerate(parts):
            call = _tool_call_from_part(part, f"call_{i}")
            if call is not None:
                result.append(call)
        return result

    def append_tool_result(
        self,
        messages: list[dict[str, Any]],
        response_data: dict[str, Any],
        tool_call: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        # The caller hands over one call at a time; every call of the same
        # response joins the model turn the first one opened.
        round_id = response_data.setdefault(_ROUND_KEY, uuid.uuid4().hex)
        if (
            len(messages) >= 2
            and messages[-2].get(_ROUND_KEY) == round_id
            and "_tool_responses" in messages[-1]
        ):
            messages[-2]["_tool_calls"].append(_replayed_call(tool_call))
            messages[-1]["_tool_responses"].append({"name": tool_call["name"], "result": result})
            return
        if response_data.get("candidates"):
            messages.append(
                {
                    "role": "assistant",
                    _ROUND_KEY: round_id,
                    "_tool_calls": [_replayed_call(tool_call)],
                }
            )
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
                "_tool_calls": [_replayed_call(tc) for tc in tool_calls],
            }
        )
        # Append all function responses in one user turn
        messages.append(
            {
                "role": "user",
                "_tool_responses": [
                    {"name": tc["name"], "result": res}
                    # strict=False: cancel/watchdog early-break can leave
                    # results shorter than tool_calls; pair what we have.
                    for tc, res in zip(tool_calls, results, strict=False)
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
        except (
            json.JSONDecodeError,
            ValueError,
        ):
            return None
        candidates = obj.get("candidates", [])
        if not candidates:
            return None
        parts = candidates[0].get("content", {}).get("parts", [])
        texts = [p["text"] for p in parts if "text" in p]
        return "".join(texts) if texts else None

    def stream_line_ends_stream(self, line: str) -> bool | None:
        # The last chunk carries ``finishReason``; there is no ``[DONE]``.
        if not line.startswith("data: "):
            return False
        try:
            obj = json.loads(line[6:])
        except ValueError:
            return False
        candidates = obj.get("candidates") if isinstance(obj, dict) else None
        finish = candidates[0].get("finishReason") if candidates else None
        if finish:
            self._note_finish_reason(finish)
            return True
        return False

    def parse_stream_usage(self, line: str) -> LLMUsageInfo | None:
        if not line.startswith("data: "):
            return None
        try:
            obj = json.loads(line[6:])
        except (
            json.JSONDecodeError,
            ValueError,
        ):
            return None
        meta = obj.get("usageMetadata")
        if not isinstance(meta, dict):
            return None
        return self._usage_from_metadata(meta)

    async def stream_with_tools(
        self,
        resp: aiohttp.ClientResponse,
        tool_calls: list[dict[str, Any]],
        content_blocks: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        """Stream Gemini SSE, yielding text and collecting tool calls."""
        stream_usage: LLMUsageInfo = {}
        self._note_finish_reason(None)
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
                except (
                    json.JSONDecodeError,
                    ValueError,
                ):
                    continue

                usage_part = self.parse_stream_usage(line)
                if usage_part:
                    stream_usage.update(usage_part)

                candidates = event.get("candidates", [])
                if not candidates:
                    continue
                # ``MAX_TOKENS`` on the final candidate is Gemini saying the
                # reply was cut at the output cap. Normalized by the base
                # class, which compares casefolded.
                finish = candidates[0].get("finishReason")
                if finish:
                    self._note_finish_reason(finish)
                parts = candidates[0].get("content", {}).get("parts", [])
                for part in parts:
                    if "text" in part:
                        yield part["text"]
                    call = _tool_call_from_part(part, f"call_{len(tool_calls)}")
                    if call is not None:
                        tool_calls.append(call)

        self._report_usage(stream_usage or None)

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
            timeout=aiohttp.ClientTimeout(connect=15, sock_read=DEFAULT_LLM_TIMEOUT),
            data=self._encode_body(payload),
        ) as resp:
            await self._raise_if_rate_limited(resp)
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
        *,
        max_tokens: int = 1024,
        timeout: float | None = None,
    ) -> AsyncIterator[str]:
        """Yield text chunks from a streaming Gemini request."""
        session = self._get_session()
        payload = self.build_payload(system, messages, stream=True, max_tokens=max_tokens)

        async with session.post(
            self._stream_endpoint,
            headers=self._get_headers(),
            timeout=aiohttp.ClientTimeout(connect=15, sock_read=timeout or DEFAULT_LLM_TIMEOUT),
            data=self._encode_body(payload),
        ) as resp:
            await self._raise_if_rate_limited(resp)
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
                except (
                    ValueError,
                    AttributeError,
                ):
                    err_msg = body[:200]
                raise ConnectionError(f"{self.provider_name}: {err_msg}")

            buffer = ""
            stream_usage: LLMUsageInfo = {}
            self._note_finish_reason(None)
            ended = False
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
                    ended = bool(self.stream_line_ends_stream(line)) or ended
                    text = self.parse_stream_line(line)
                    if text:
                        yield text
            self._last_stream_terminated = ended
            self._report_usage(stream_usage or None)

    # -- Health check ------------------------------------------------------

    async def health_check(self) -> bool:
        """Verify the Gemini API key and model."""
        from ..const import HEALTH_CHECK_TIMEOUT

        # Query the specific model endpoint — validates both key and model in one call.
        url = f"{self._host}/models/{self._model}?key={self._api_key}"
        try:
            session = self._get_session()
            async with session.get(
                url,
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=HEALTH_CHECK_TIMEOUT),
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
