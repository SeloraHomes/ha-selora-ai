"""Selora AI Local — response + streaming: stop-token defusal, slim-output
conversion, and the visible-diff streaming path."""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
import logging
from typing import Any

from ....const import (
    SELORA_LOCAL_DEFAULT_INTENT,
    SELORA_LOCAL_KIND_TO_INTENT,
)
from .slim_parser import (
    _SELORA_LOCAL_VISIBLE_VALUE_KEYS,
    _selora_local_extract_visible,
)

_LOGGER = logging.getLogger(__name__)


# Selora AI Local — Phi-3.5 + Qwen3 ChatML stop tokens.
_SELORA_LOCAL_STOP_MARKERS: tuple[str, ...] = ("<|im_end|>", "<|endoftext|>", "<|end|>")
_SELORA_LOCAL_MAX_MARKER_LEN = max(len(m) for m in _SELORA_LOCAL_STOP_MARKERS)


def _selora_local_truncate_at_stop(text: str) -> tuple[str, bool]:
    """Return (text-up-to-first-marker, found_any)."""
    earliest = -1
    for marker in _SELORA_LOCAL_STOP_MARKERS:
        idx = text.find(marker)
        if idx >= 0 and (earliest < 0 or idx < earliest):
            earliest = idx
    if earliest < 0:
        return text, False
    return text[:earliest], True


# Call kinds whose output is plain prose (no JSON envelope to repair) AND for which the integration is fine landing on intent=answer downstream.
_PROSE_KINDS: frozenset[str] = frozenset({"chat_answer", "session_title"})


class _StreamingMixin:
    """Response + streaming concerns for the Selora AI Local provider."""

    def extract_text_response(self, response_data: dict[str, Any]) -> str | None:
        text = super().extract_text_response(response_data)
        if text is None:
            return None
        truncated, _ = _selora_local_truncate_at_stop(text)
        # Convert v0.4.2 slim output schemas to the {intent, response, calls/automation/scene} envelope before handing back to LLMClient.
        converted = self._convert_slim_shape(truncated)
        # Re-tag intent=answer → kind's true intent for prose-trained specialists (currently just chat_clarification).
        target_intent = SELORA_LOCAL_KIND_TO_INTENT.get(self._call_kind.get() or "")
        if target_intent in (None, "answer", SELORA_LOCAL_DEFAULT_INTENT):
            return converted
        try:
            body = json.loads(converted)
        except json.JSONDecodeError:
            return converted
        if isinstance(body, dict) and body.get("intent") == "answer":
            body["intent"] = target_intent
            return json.dumps(body, separators=(",", ":"))
        return converted

    def _is_visible_value_complete(self) -> bool:
        """Return True when the raw buffer contains the full first user-facing string value (i.e., we've already seen the unescaped closing ``"`` after the marker)."""
        raw = self._raw_response_buffer.get()
        earliest = -1
        marker_len = 0
        for marker in _SELORA_LOCAL_VISIBLE_VALUE_KEYS:
            idx = raw.find(marker)
            if idx >= 0 and (earliest < 0 or idx < earliest):
                earliest = idx
                marker_len = len(marker)
        if earliest < 0:
            return False
        i = earliest + marker_len
        n = len(raw)
        while i < n:
            c = raw[i]
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == '"':
                return True
            i += 1
        return False

    def _emit_visible_diff(self) -> str | None:
        """Recompute the user-facing text from the accumulated raw buffer and return whatever is new since the last emit."""
        prefix = ""
        if not self._spinner_sentinel_emitted.get() and self._call_kind.get() == "chat_automation":
            self._spinner_sentinel_emitted.set(True)
            prefix = "```automation\n"
        full_visible = _selora_local_extract_visible(self._raw_response_buffer.get())
        already = self._visible_emitted.get()
        new_chars = ""
        if full_visible and len(full_visible) > len(already):
            new_chars = full_visible[len(already) :]
            self._visible_emitted.set(full_visible)
        if not prefix and not new_chars:
            return None
        return prefix + new_chars

    def parse_stream_line(self, line: str) -> str | None:
        # Once we've seen a stop marker for this call, swallow every subsequent token — the model is hallucinating past EOS.
        if self._stop_seen.get():
            return None
        chunk = super().parse_stream_line(line)
        if not chunk:
            return chunk

        # Concatenate any held-back tail with this chunk so a marker split across SSE frames is still detected.
        combined = self._stream_carry.get() + chunk
        truncated, found = _selora_local_truncate_at_stop(combined)
        if found:
            self._stop_seen.set(True)
            self._stream_carry.set("")
            if truncated:
                # Append the safe portion (everything before the stop marker) to the raw buffer so convert_response_text sees the full slim JSON at end-of-stream.
                self._raw_response_buffer.set(self._raw_response_buffer.get() + truncated)
            return self._emit_visible_diff()

        # No marker yet.
        hold = _SELORA_LOCAL_MAX_MARKER_LEN - 1
        if len(combined) <= hold:
            self._stream_carry.set(combined)
            return None
        safe_raw = combined[:-hold]
        self._stream_carry.set(combined[-hold:])
        # Stash the safe portion into the raw buffer; emit only the new user-facing text (extracted from inside the slim JSON value).
        self._raw_response_buffer.set(self._raw_response_buffer.get() + safe_raw)
        return self._emit_visible_diff()

    async def send_request_stream(  # type: ignore[override]
        self,
        system: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 1024,  # noqa: ARG002 - low-context path sizes its own payload
    ) -> AsyncIterator[str]:
        # Branch by call_kind: * Prose intents (chat_answer, session_title) emit plain text — no JSON envelope to repair.

        # Deterministic short-circuit: inventory / state-filter questions are answered from hass.states without involving the LoRA.
        deterministic = self._maybe_calendar_question_envelope()
        if deterministic is None:
            deterministic = self._maybe_state_filter_envelope()
        if deterministic is None:
            deterministic = self._maybe_category_inventory_envelope()
        if deterministic is None:
            deterministic = self._maybe_single_state_envelope()
        if deterministic is None:
            deterministic = self._maybe_polar_valve_state_envelope()
        if deterministic is None:
            deterministic = self._maybe_weather_question_envelope()
        if deterministic is not None:
            try:
                visible = json.loads(deterministic).get("response") or ""
            except (
                json.JSONDecodeError,
                TypeError,
                AttributeError,
            ):
                visible = ""
            if visible:
                yield str(visible)
            # Stash the full envelope so convert_response_text returns it verbatim on stream completion.
            self._raw_response_buffer.set(deterministic)
            self._user_message_raw.set("")
            return

        await self._ensure_specialist_prompts_loaded()
        await self._settle_discovery()
        async with self._request_lock:
            # If activation fails, let _SeloraLocalActivationError (ConnectionError) propagate out of the generator before any chunks are yielded; LLMClient's streaming path already treats ConnectionError as a transport failure.
            await self._activate_lora_for_kind(self._call_kind.get())

            kind = self._call_kind.get() or ""

            # Prose path: stream natively, plus the carry-over flush
            if kind in _PROSE_KINDS:
                async for piece in super().send_request_stream(system, messages):
                    yield piece
                if not self._stop_seen.get():
                    tail = self._stream_carry.get()
                    if tail:
                        self._stream_carry.set("")
                        self._raw_response_buffer.set(self._raw_response_buffer.get() + tail)
                        final_diff = self._emit_visible_diff()
                        if final_diff:
                            yield final_diff
                return

            # JSON path: spinner sentinel for chat_automation, then a
            if not self._spinner_sentinel_emitted.get() and kind == "chat_automation":
                self._spinner_sentinel_emitted.set(True)
                yield "```automation\n"

            # CALL super().send_request, NOT self.send_request.
            _LOGGER.debug(
                "Selora Local JSON-path send: kind=%s endpoint=%s user_msg=%r",
                kind,
                self._endpoint,
                (self._user_message_raw.get() or "")[:80],
            )
            result, error = await super().send_request(system, messages)
            if error:
                # Stream consumers (architect_chat_stream → websocket handler) only surface errors when the generator raises ConnectionError; silently returning would persist an empty assistant message and report a successful "done" event.
                raise ConnectionError(f"{self.provider_name}: {error}")
            if result:
                yield result

    def convert_response_text(self, text: str, *, turn_token: str | None = None) -> str:
        """Apply the v0.4.2 slim → enveloped conversion to the complete response (used by LLMClient.parse_streamed_response).

        Binds ``turn_token`` for the duration so the deterministic overrides
        below resolve THIS turn's context rather than whichever turn wrote
        last. Safe as a plain attribute because this method is synchronous:
        no other turn can interleave on the event loop while it is held.
        """
        previous = self._active_turn_token
        self._active_turn_token = turn_token
        try:
            source = self._raw_response_buffer.get() or text
            converted = self._convert_slim_shape(source)
            return self._ensure_utilities_citations(converted)
        finally:
            self._active_turn_token = previous
            # The turn is answered; drop its snapshot rather than leave it to be
            # evicted by whichever seven turns happen to follow.
            if turn_token is not None:
                self._turn_snapshots.pop(turn_token, None)
