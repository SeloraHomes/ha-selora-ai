"""Selora AI Local — talks to the SeloraHub's llama-server."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextvars import ContextVar
import logging
from pathlib import Path
from typing import Any, NamedTuple

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
    _SELORA_LOCAL_UNIFIED_PROMPT_KEY,
    _RequestBuildMixin,
)
from .runtime.serving import (
    _SELORA_LOCAL_DISCOVERY_BACKOFF_MAX_S,
    _SELORA_LOCAL_DISCOVERY_BACKOFF_MIN_S,
    _SELORA_LOCAL_DISCOVERY_WAIT_S,
    _SELORA_LOCAL_PREWARM_KINDS,
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
# How many turns may hold a snapshot at once. A turn releases its own entry at
# its conversion pass; one that errors or is cancelled never gets there, so the
# store is bounded and evicts oldest-first rather than growing for the life of
# the process.
_MAX_TURN_SNAPSHOTS = 8

# Key for a caller that passed no token -- the pre-token behaviour, usable only
# while it is the one turn outstanding.
_UNTOKENED_TURN = ""


class _TurnSnapshot(NamedTuple):
    """What ``set_chat_context`` was told, for one turn."""

    user_message: str
    chat_kind: str | None
    entities: list[Any]


_SELORA_LOCAL_PROMPT_FILENAMES: dict[str, str] = {
    "command": "command_system_prompt.txt",
    "automation": "automation_system_prompt.txt",
    "answer": "answer_system_prompt.txt",
    "clarification": "clarification_system_prompt.txt",
    _SELORA_LOCAL_UNIFIED_PROMPT_KEY: "unified_system_prompt.txt",
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
        selora_local_ollama_model: str | None = None,
        **_kwargs: Any,
    ) -> None:
        super().__init__(
            hass,
            model=SELORA_LOCAL_DEFAULT_INTENT,
            host=host or DEFAULT_SELORA_LOCAL_HOST,
            api_key="",
        )
        # Which runtime is serving the model. Everything below that speaks
        # llama-server's slot API is conditional on this.
        self._backend: str = selora_local_backend or DEFAULT_SELORA_LOCAL_BACKEND
        # Explicit model/tag for the Ollama backend, straight from the config entry.
        # A benchmark run sets it to drive a candidate model without republishing
        # over the shipped tag; left empty, the tag is discovered from the host.
        self._ollama_model_override: str | None = (selora_local_ollama_model or "").strip() or None
        # The Ollama model tag actually in use. Discovered, never written down:
        # "settled" stays False while the only value we have is the unreachable-host
        # fallback, so a host that finishes booting later still gets resolved instead
        # of being pinned to :latest for the rest of the process.
        self._unified_model: str | None = None
        self._unified_model_settled: bool = False
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
        # Per-turn snapshots of what set_chat_context was told, keyed by the
        # caller's turn token.
        #
        # These exist because the ContextVars above frequently read back EMPTY
        # where they are needed most: set_chat_context runs inside the request
        # task, and a write there does not propagate up to the caller's context,
        # which is where the conversion pass runs. Without a fallback the
        # deterministic overrides silently skip.
        #
        # Keyed rather than a single mirror because a single one is
        # last-writer-wins: a background analysis cycle overlapping a panel chat
        # replaced the panel turn's message before its conversion pass read it,
        # and that turn was then answered from the other one's request.
        # ``_MAX_TURN_SNAPSHOTS`` bounds the store, since a turn that never
        # reaches its conversion pass never releases its own entry.
        self._turn_snapshots: OrderedDict[str, _TurnSnapshot] = OrderedDict()
        # The turn whose conversion pass is running right now. Set only for the
        # duration of ``convert_response_text``, which is SYNCHRONOUS -- nothing
        # else can interleave on the event loop while it is held, which is what
        # makes a plain attribute safe here where the mirror was not.
        self._active_turn_token: str | None = None
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

    def _store_turn_snapshot(self, turn_token: str | None, snapshot: _TurnSnapshot) -> None:
        """Record what this turn was told, evicting the oldest when full."""
        key = turn_token or _UNTOKENED_TURN
        self._turn_snapshots.pop(key, None)
        self._turn_snapshots[key] = snapshot
        while len(self._turn_snapshots) > _MAX_TURN_SNAPSHOTS:
            self._turn_snapshots.popitem(last=False)

    def _turn_snapshot(self) -> _TurnSnapshot | None:
        """This turn's snapshot, or None when it cannot be identified.

        A token names the turn outright. Without one there is a single
        candidate only when a single turn is outstanding — with several, the
        newest is not knowably this one, and answering a turn from another
        turn's request is worse than declining to answer deterministically at
        all, so this reports nothing and the caller falls back to the model's
        own output.
        """
        if self._active_turn_token is not None:
            return self._turn_snapshots.get(self._active_turn_token)
        if len(self._turn_snapshots) == 1:
            return next(iter(self._turn_snapshots.values()))
        return None

    def _current_user_message(self) -> str:
        """This turn's raw user message: the ContextVar, else its snapshot."""
        ctx = self._user_message_raw.get()
        if ctx:
            return ctx
        snapshot = self._turn_snapshot()
        return snapshot.user_message if snapshot else ""

    def _current_chat_kind(self) -> str | None:
        """This turn's chat kind, resolved like ``_current_user_message``."""
        ctx = self._chat_kind.get()
        if ctx:
            return ctx
        snapshot = self._turn_snapshot()
        return snapshot.chat_kind if snapshot else None

    def _current_entities(self) -> list[Any]:
        """This turn's injected entity snapshot, resolved the same way."""
        ctx = self._entities_for_lora.get()
        if ctx:
            return ctx
        snapshot = self._turn_snapshot()
        return list(snapshot.entities) if snapshot else []

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
        turn_token: str | None = None,
    ) -> None:
        """Capture the raw chat context from LLMClient so build_payload can reconstruct the v0.4.2 training-format request body."""
        # Snapshot baseline entity states before this turn's command (if any) mutates them.
        self._capture_baseline_states()
        self._user_message_raw.set(user_message or "")
        # Capture the kind for this turn while ``_call_kind`` still holds the live value.
        self._chat_kind.set(self._call_kind.get())
        # Snapshot for the conversion pass, which runs in a context that never saw
        # the ContextVar writes above (see __init__). Keyed by the caller's token
        # so the pass selects THIS turn's rather than whichever ran last.
        self._store_turn_snapshot(
            turn_token,
            _TurnSnapshot(
                user_message=user_message or "",
                chat_kind=self._call_kind.get(),
                entities=list(entities or []),
            ),
        )
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
    "_SELORA_LOCAL_PREWARM_KINDS",
    "_SeloraLocalActivationError",
]
