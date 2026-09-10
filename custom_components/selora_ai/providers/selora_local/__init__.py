"""Selora AI Local — talks to the SeloraHub's llama-server."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
import logging
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from ...const import (
    DEFAULT_SELORA_LOCAL_BACKEND,
    DEFAULT_SELORA_LOCAL_HOST,
    SELORA_LOCAL_DEFAULT_INTENT,
)
from ..openai_compat import OpenAICompatibleProvider
from .answers.domain import _AnswersDomainMixin
from .answers.filter import _AnswersFilterMixin
from .answers.inventory import _AnswersInventoryMixin
from .answers.state import (
    _AnswersStateMixin,
)
from .automations.overrides import _AutomationsMixin
from .automations.presence import _AutomationsPresenceMixin
from .automations.time import _AutomationsTimeMixin
from .calendar.events import _CalendarMixin
from .calendar.todo import _CalendarTodoMixin
from .commands.cover_climate import _CommandsCoverClimateMixin
from .commands.devices import _CommandsDevicesMixin
from .commands.light import _CommandsLightMixin
from .commands.media import _CommandsMediaMixin
from .commands.vacuum_fan import (
    _CommandsVacuumFanMixin,
)
from .runtime.request_build import (
    _SELORA_LOCAL_MAX_ENTITY_LINES,
    _SELORA_LOCAL_MAX_ENTITY_LINES_AUTOMATION,
    _RequestBuildMixin,
)
from .runtime.serving import (
    _SELORA_LOCAL_DISCOVERY_BACKOFF_MAX_S,
    _SELORA_LOCAL_DISCOVERY_BACKOFF_MIN_S,
    _SELORA_LOCAL_DISCOVERY_WAIT_S,
    _SeloraLocalActivationError,
    _ServingMixin,
)
from .runtime.slim_parser import (
    _SlimParserMixin,
)
from .runtime.streaming import _StreamingMixin
from .utilities.rag import (
    _UtilitiesRagMixin,
)

_LOGGER = logging.getLogger(__name__)

# Must match the LoRA's trained prompt format byte-for-byte or it goes OOD.
_SELORA_LOCAL_PROMPTS_DIR = (
    Path(__file__).resolve().parent.parent.parent / "local_model" / "prompts"
)
_SELORA_LOCAL_PROMPT_FILENAMES: dict[str, str] = {
    "command": "command_system_prompt.txt",
    "automation": "automation_system_prompt.txt",
    "answer": "answer_system_prompt.txt",
    "clarification": "clarification_system_prompt.txt",
    # Must match the LoRA's trained prompt format byte-for-byte or it goes OOD.
    "utilities": "utilities_system_prompt.txt",
}

# State word → the domain to synthesize a ``{domain.slug}`` placeholder for when the named device can't be resolved to a real entity.
_STATE_WORD_TO_PLACEHOLDER_DOMAIN: dict[str, str] = {
    "open": "cover",
    "closed": "cover",
    "locked": "lock",
    "unlocked": "lock",
    "running": "switch",
    "playing": "media_player",
    "home": "device_tracker",
    "away": "device_tracker",
    "on": "light",
    "off": "light",
    "active": "binary_sensor",
}


class SeloraLocalProvider(
    _ServingMixin,
    _StreamingMixin,
    _RequestBuildMixin,
    OpenAICompatibleProvider,
    _UtilitiesRagMixin,
    _AutomationsMixin,
    _AutomationsPresenceMixin,
    _AutomationsTimeMixin,
    _CommandsDevicesMixin,
    _CommandsLightMixin,
    _CommandsCoverClimateMixin,
    _CommandsMediaMixin,
    _CommandsVacuumFanMixin,
    _AnswersStateMixin,
    _AnswersFilterMixin,
    _AnswersDomainMixin,
    _AnswersInventoryMixin,
    _CalendarMixin,
    _CalendarTodoMixin,
    _SlimParserMixin,
):
    """Selora AI Local provider (SeloraHub llama-server, OpenAI-compatible)."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        host: str = "",
        selora_local_backend: str | None = None,
        **_kwargs: Any,
    ) -> None:
        super().__init__(
            hass,
            model=SELORA_LOCAL_DEFAULT_INTENT,
            host=host or DEFAULT_SELORA_LOCAL_HOST,
            api_key="",
        )
        # Backend runtime.
        self._backend: str = selora_local_backend or DEFAULT_SELORA_LOCAL_BACKEND
        # Per-task LoRA selection.
        self._call_kind: ContextVar[str | None] = ContextVar(
            "selora_ai_local_call_kind", default=None
        )
        # Per-call latch: once we've seen a stop marker in the SSE stream, suppress every following chunk.
        self._stop_seen: ContextVar[bool] = ContextVar("selora_ai_local_stop_seen", default=False)
        # Trailing carry-over for cross-chunk stop-marker detection.
        self._stream_carry: ContextVar[str] = ContextVar("selora_ai_local_stream_carry", default="")
        # Per-call accumulator of the raw streamed JSON.
        self._raw_response_buffer: ContextVar[str] = ContextVar(
            "selora_ai_local_raw_response", default=""
        )
        # Snapshot of the LLMClient call kind at the moment ``set_chat_context`` ran.
        self._chat_kind: ContextVar[str | None] = ContextVar(
            "selora_ai_local_chat_kind", default=None
        )
        # How many user-facing chars we've already emitted to the WS handler.
        self._visible_emitted: ContextVar[str] = ContextVar(
            "selora_ai_local_visible_emitted", default=""
        )
        # Whether we've already prepended the `````automation`` spinner sentinel to the visible stream this call.
        self._spinner_sentinel_emitted: ContextVar[bool] = ContextVar(
            "selora_ai_local_spinner_sentinel_emitted", default=False
        )
        # v0.4.2 hub: LoRA slot routing state
        self._lora_slots: dict[str, int] | None = None
        self._n_slots: int = 0
        # The last slot we POSTed an activation for.
        self._active_slot: int | None = None
        # The model id reported by GET /v1/models.
        self._base_model_id: str | None = None
        # Context window llama-server is actually serving, read from the same GET /v1/models
        # body (``data[0].meta.n_ctx``). None = never asked or the probe failed — see
        # LLMProvider.context_window; it does NOT mean "unlimited". The fetch timestamp uses
        # None as its never-fetched sentinel because time.monotonic() counts from boot: a 0.0
        # sentinel reads as "just fetched" on a host with less uptime than the TTL and would
        # suppress the first probe.
        self._context_window: int | None = None
        self._context_probe_at: float | None = None
        # Serializes discovery so concurrent first-use requests don't all race the GET /lora-adapters endpoint.
        self._slot_lock: asyncio.Lock = asyncio.Lock()
        # Current step of the escalating retry schedule; reset to the minimum once
        # discovery succeeds.
        self._discovery_backoff_s: float = _SELORA_LOCAL_DISCOVERY_BACKOFF_MIN_S
        # The step the last _schedule_discovery_retry actually armed. Distinct from the
        # deadline: the deadline shrinks as the window elapses, the step does not. Whether
        # a request may wait a window out is a question about the step ("is this hub
        # seconds from ready?"), not about how much of it happens to be left.
        self._discovery_armed_delay: float = 0.0
        # Set for the duration of the pre-warm task so its requests do not spend the retry
        # schedule's short steps before anyone has typed anything — see prewarm() and
        # _settle_discovery(). A ContextVar rather than a plain attribute because pre-warm
        # runs as its own background task and must not change what an overlapping panel
        # chat does.
        self._prewarming: ContextVar[bool] = ContextVar("selora_ai_local_prewarming", default=False)
        # Monotonic deadline before we'll retry discovery after a transient failure.
        self._discovery_retry_after: float = 0.0
        # Single-flight gate around (activate slot, run completion).
        self._request_lock: asyncio.Lock = asyncio.Lock()
        # Must match the LoRA's trained prompt format byte-for-byte or it goes OOD.
        self._user_message_raw: ContextVar[str] = ContextVar(
            "selora_ai_local_user_message", default=""
        )
        # Plain-attribute mirror of the current turn's user message + call kind.
        self._user_message_instance: str = ""
        self._chat_kind_instance: str | None = None
        # Same ContextVar → instance-attribute fallback for the injected entity snapshot.
        self._entities_for_lora_instance: list[Any] = []
        # Default=None (not []) so the same list isn't shared across async contexts — ruff's B039 / flake8-bugbear flags ContextVar mutable defaults as a real footgun.
        self._entities_for_lora: ContextVar[list[Any] | None] = ContextVar(
            "selora_ai_local_entities", default=None
        )
        self._automations_for_lora: ContextVar[list[dict[str, Any]] | None] = ContextVar(
            "selora_ai_local_automations", default=None
        )
        self._history_for_lora: ContextVar[list[dict[str, str]] | None] = ContextVar(
            "selora_ai_local_history", default=None
        )
        # Request locale forwarded by LLMClient.set_chat_context.
        self._language_for_lora: ContextVar[str | None] = ContextVar(
            "selora_ai_local_language", default=None
        )
        # v0.4.8 RAG/utilities specialist: retrieved documentation chunks for the current turn, each as ``{"id": <doc_chunk_id>, "text": <chunk body>}``.
        self._docs_for_lora: ContextVar[list[dict[str, str]] | None] = ContextVar(
            "selora_ai_local_docs", default=None
        )
        # Per-specialist trained system prompts.
        self._specialist_prompts: dict[str, str] = {}
        self._specialist_prompts_loaded: bool = False
        self._specialist_prompts_lock: asyncio.Lock = asyncio.Lock()
        # Baseline state snapshot for the answer.state_filter envelope.
        self._baseline_states: dict[str, str] = {}
        self._baseline_captured: bool = False

    def _capture_baseline_states(self) -> None:
        """Snapshot hass.states for answer.state_filter, freeze-once at first chat — including ``unknown``/``unavailable`` verbatim."""
        if self._baseline_captured:
            return
        try:
            for s in self._hass.states.async_all():
                # Record the verbatim state — including "unknown" / "unavailable" / "" — so the envelope can distinguish "this entity was concretely off at fixture time" from "this entity was not yet evaluated at fixture time".
                self._baseline_states[s.entity_id] = (s.state or "").lower()
        except Exception:  # noqa: BLE001 — defensive: keep prior baseline on error
            pass
        self._baseline_captured = True

    async def _ensure_specialist_prompts_loaded(self) -> None:
        """Lazily load trained prompts off the event loop on first use."""
        if self._specialist_prompts_loaded:
            return
        async with self._specialist_prompts_lock:
            if self._specialist_prompts_loaded:
                return
            self._specialist_prompts = await self._hass.async_add_executor_job(
                self._load_specialist_prompts
            )
            self._specialist_prompts_loaded = True

    @staticmethod
    def _load_specialist_prompts() -> dict[str, str]:
        """Read the bundled v0.4.2 trained prompts from disk."""
        loaded: dict[str, str] = {}
        for intent, filename in _SELORA_LOCAL_PROMPT_FILENAMES.items():
            path = _SELORA_LOCAL_PROMPTS_DIR / filename
            try:
                loaded[intent] = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                _LOGGER.warning(
                    "Selora Local trained prompt missing for %s (%s): %s",
                    intent,
                    path,
                    exc,
                )
        if loaded:
            _LOGGER.info(
                "Selora Local loaded %d trained system prompts from %s",
                len(loaded),
                _SELORA_LOCAL_PROMPTS_DIR.name,
            )
        return loaded

    @property
    def provider_type(self) -> str:
        return "selora_local"

    @property
    def provider_name(self) -> str:
        return "Selora AI Local"

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def context_window(self) -> int | None:
        # Whatever llama-server was launched with (`-c`), discovered from
        # GET /v1/models. Distinct from ``is_low_context``: that is a
        # static "assume a tight window" policy flag, this is the measured
        # number — and it can be much larger than the 1024 the policy
        # assumes when the hub operator raised it. None until a probe
        # succeeds; see LLMProvider.context_window.
        return self._context_window

    @property
    def is_low_context(self) -> bool:
        # Cap: hub ctx is 4096 tokens; overflow -> HTTP 500, so bound entities/history.
        return True

    @property
    def is_local(self) -> bool:
        return True

    @property
    def supports_streaming(self) -> bool:
        # Prose intents (answer/clarification) skip JSON-envelope repair and stream natively; JSON intents (command/automation) still wait for the full payload so normalize_response_content can run.
        return True

    def set_call_kind(self, kind: str | None) -> None:
        self._call_kind.set(kind)
        # Only reset streaming state at the START of a new call (kind is not None).
        if kind is not None:
            self._reset_streaming_state_inner()

    def _current_user_message(self) -> str:
        """Return the in-flight turn's raw user message."""
        return self._user_message_raw.get() or self._user_message_instance or ""

    def _current_chat_kind(self) -> str | None:
        """Return the in-flight turn's chat kind, with the same ContextVar → instance-attribute fallback as ``_current_user_message``."""
        return self._chat_kind.get() or self._chat_kind_instance

    def _current_entities(self) -> list[Any]:
        """Return this turn's injected entity snapshot, with the same ContextVar → instance-attribute fallback as ``_current_user_message``."""
        ctx = self._entities_for_lora.get()
        if ctx:
            return ctx
        return self._entities_for_lora_instance or []

    def _reset_streaming_state_inner(self) -> None:
        """Drop per-turn streaming buffers without touching ``_call_kind``."""
        self._stop_seen.set(False)
        self._stream_carry.set("")
        self._raw_response_buffer.set("")
        self._visible_emitted.set("")
        self._spinner_sentinel_emitted.set(False)

    def reset_streaming_state(self) -> None:
        """Clear stream buffers at the start of every architect_chat_stream turn (called by LLMClient before the greeting short-circuit)."""
        self._reset_streaming_state_inner()

    def set_chat_context(
        self,
        *,
        user_message: str = "",
        entities: list[Any] | None = None,
        existing_automations: list[dict[str, Any]] | None = None,
        history: list[dict[str, str]] | None = None,
        language: str | None = None,
        relevant_docs: list[dict[str, str]] | None = None,
    ) -> None:
        """Capture the raw chat context from LLMClient so build_payload can reconstruct the v0.4.2 training-format request body."""
        # Snapshot baseline entity states before this turn's command (if any) mutates them.
        self._capture_baseline_states()
        self._user_message_raw.set(user_message or "")
        # Capture the kind for this turn while ``_call_kind`` still holds the live value.
        self._chat_kind.set(self._call_kind.get())
        # Plain-attribute mirror that survives the ContextVar-propagation race (see __init__): the conversion pass falls back to these when the ContextVars read back empty.
        self._user_message_instance = user_message or ""
        self._chat_kind_instance = self._call_kind.get()
        self._entities_for_lora_instance = list(entities or [])
        self._entities_for_lora.set(list(entities or []))
        self._automations_for_lora.set(list(existing_automations or []))
        self._history_for_lora.set(list(history or []))
        self._language_for_lora.set(language)
        self._docs_for_lora.set(list(relevant_docs or []))


__all__ = [
    "SeloraLocalProvider",
    "_SELORA_LOCAL_DISCOVERY_BACKOFF_MAX_S",
    "_SELORA_LOCAL_DISCOVERY_BACKOFF_MIN_S",
    "_SELORA_LOCAL_DISCOVERY_WAIT_S",
    "_SELORA_LOCAL_MAX_ENTITY_LINES",
    "_SELORA_LOCAL_MAX_ENTITY_LINES_AUTOMATION",
    "_SeloraLocalActivationError",
]
