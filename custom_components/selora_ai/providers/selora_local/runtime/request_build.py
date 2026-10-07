"""Selora AI Local — request assembly: the trained system prompt, the user turn
in the corpus layout (``user_turn``), and recent history."""

from __future__ import annotations

import re
from typing import Any

from ....const import (
    SCHEDULE_ENTITIES_OMITTED,
    SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED,
    SELORA_LOCAL_DEFAULT_INTENT,
    SELORA_LOCAL_DEFAULT_MAX_TOKENS,
    SELORA_LOCAL_KIND_TO_INTENT,
    SELORA_LOCAL_MAX_TOKENS_BY_KIND,
    SELORA_LOCAL_OLLAMA_UNIFIED_MODEL_FAMILY,
)
from ..utilities.rag import _selora_local_retrieve_doc_chunks
from .streaming import _SELORA_LOCAL_STOP_MARKERS
from .user_turn import (
    ENTITY_INDENT,
    build_user_turn,
    format_entity_line,
    format_existing_automations_block,
    format_relevant_docs_block,
)

# Selora AI Local — how many prior turns to feed back to the LoRA (matches model-tester backends.py:591 cap).
_SELORA_LOCAL_HISTORY_TURNS = 3

# Cap: hub ctx is 4096 tokens; overflow -> HTTP 500, so bound entities/history.
_SELORA_LOCAL_NO_HISTORY_KINDS: frozenset[str] = frozenset({"chat_automation", "suggestions"})

# Cap: hub ctx is 4096 tokens; overflow -> HTTP 500, so bound entities/history.
_SELORA_LOCAL_MAX_ENTITY_LINES = 60

# Cap: hub ctx is 4096 tokens; overflow -> HTTP 500, so bound entities/history.
_SELORA_LOCAL_MAX_ENTITY_LINES_AUTOMATION = 25

# Every kind gets EXISTING AUTOMATIONS, so it is capped like the entity block.
_SELORA_LOCAL_MAX_AUTOMATION_LINES = 20
_SELORA_LOCAL_MAX_AUTOMATION_ALIAS_CHARS = 60

# Not an intent. The Ollama backend serves ONE self-routing model that
# was trained on a single router prompt covering every intent, so it
# keys the same prompt for all of them instead of a per-specialist one.
_SELORA_LOCAL_UNIFIED_PROMPT_KEY = "unified"

# Qwen3 token count of each bundled system prompt (stripped, as sent).
# tests/test_selora_local_published_prompts.py ties every count to the
# sha256 of the file it was measured on, so a prompt release fails there
# until the counts are re-measured.
_SELORA_LOCAL_PROMPT_TOKENS: dict[str, int] = {
    "command": 263,
    "automation": 659,
    "answer": 195,
    "clarification": 154,
    "utilities": 348,
    _SELORA_LOCAL_UNIFIED_PROMPT_KEY: 694,
}

# Everything a request carries besides its system prompt and entity block:
# language directive, history, the user turn's labels and untrusted-data
# notice, EXISTING AUTOMATIONS (20 aliases of 60 chars, ~400 tokens) and the
# reply. The automation figure includes chat_automation's 400-token output cap.
_SELORA_LOCAL_REQUEST_TOKENS = 754
_SELORA_LOCAL_AUTOMATION_REQUEST_TOKENS = 1121

# Non-entity tokens a request reserves before the entity block gets what is
# left, on the llama backend, where each intent sends its own specialist prompt.
_SELORA_LOCAL_RESERVED_TOKENS = (
    max(
        _SELORA_LOCAL_PROMPT_TOKENS[intent]
        for intent in ("command", "answer", "clarification", "utilities")
    )
    + _SELORA_LOCAL_REQUEST_TOKENS
)
_SELORA_LOCAL_AUTOMATION_RESERVED_TOKENS = (
    _SELORA_LOCAL_PROMPT_TOKENS["automation"] + _SELORA_LOCAL_AUTOMATION_REQUEST_TOKENS
)

# Same, on ollama-unified, which sends the unified prompt for every intent.
# It is larger than every specialist prompt but the automation one's, so the
# specialist figures would hand the entity block room the prompt already took.
_SELORA_LOCAL_UNIFIED_RESERVED_TOKENS = (
    _SELORA_LOCAL_PROMPT_TOKENS[_SELORA_LOCAL_UNIFIED_PROMPT_KEY] + _SELORA_LOCAL_REQUEST_TOKENS
)
_SELORA_LOCAL_UNIFIED_AUTOMATION_RESERVED_TOKENS = (
    _SELORA_LOCAL_PROMPT_TOKENS[_SELORA_LOCAL_UNIFIED_PROMPT_KEY]
    + _SELORA_LOCAL_AUTOMATION_REQUEST_TOKENS
)


class _RequestBuildMixin:
    """Training-format request assembly for the Selora AI Local provider."""

    def _resolve_intent(self) -> str:
        """Return the specialist intent for the current call (command, automation, answer, clarification)."""
        return SELORA_LOCAL_KIND_TO_INTENT.get(
            self._call_kind.get() or "", SELORA_LOCAL_DEFAULT_INTENT
        )

    def _filter_known_entities(self, q: list[str]) -> list[str]:
        """Drop entity_ids the utilities LoRA invented that aren't in the user's real entity set, so docs-grounded advice never references a fabricated device (``no_hallucinated_entities``)."""
        real = {
            e.get("entity_id")
            for e in (self._entities_for_lora.get() or [])
            if isinstance(e, dict) and e.get("entity_id")
        }
        return [x for x in q if x in real]

    def _resolve_max_tokens(self, requested: int) -> int:
        """Cap ``requested`` at the per-intent ceiling for this call."""
        cap = SELORA_LOCAL_MAX_TOKENS_BY_KIND.get(
            self._call_kind.get() or "", SELORA_LOCAL_DEFAULT_MAX_TOKENS
        )
        return max(1, min(int(requested), cap))

    def _entity_line_cap(self) -> int:
        """Entity-block line cap for the call kind currently in flight.

        The stricter cap applies to ``chat_automation``: its trained system
        prompt is by far the largest, leaving least headroom for entities.

        When the hub has told us its context window the cap is derived from
        a token budget, so a hub configured with a smaller window than the
        one these constants were tuned against stops overflowing instead of
        returning HTTP 500. The derived value can only tighten the constant
        — never raise it — because the LoRAs were trained against entity
        blocks of that size and grow unreliable outside it.

        ``context_window`` is populated by ``async_refresh_capabilities``
        from ``GET /v1/models`` (``data[0].meta.n_ctx``) — the window
        llama-server was actually launched with, which is what makes the
        derived path live rather than theoretical. It stays ``None`` until
        a probe succeeds and whenever one fails, and that case returns the
        hand-tuned constants unchanged: an unknown window must not be read
        as a large one.
        """
        # Deferred: ``context_budget`` itself is dependency-free, but
        # reaching it imports the ``llm_client`` package __init__, which
        # pulls in ``client`` and its Home Assistant imports. A provider
        # module should not drag the facade in at import time.
        from ....llm_client.context_budget import LOCAL_ENTITY_LINE_TOKENS, entity_budget

        automation = self._call_kind.get() == "chat_automation"
        fallback = (
            _SELORA_LOCAL_MAX_ENTITY_LINES_AUTOMATION
            if automation
            else _SELORA_LOCAL_MAX_ENTITY_LINES
        )
        window = getattr(self, "context_window", None)
        if window is None:
            return fallback
        derived = entity_budget(
            window,
            reserved=self._reserved_tokens(automation=automation),
            tokens_per_line=LOCAL_ENTITY_LINE_TOKENS,
        )
        return min(fallback, derived)

    def _reserved_tokens(self, *, automation: bool) -> int:
        """Non-entity tokens for the call in flight, sized for the system prompt this backend sends."""
        if self._backend == SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED:
            return (
                _SELORA_LOCAL_UNIFIED_AUTOMATION_RESERVED_TOKENS
                if automation
                else _SELORA_LOCAL_UNIFIED_RESERVED_TOKENS
            )
        return (
            _SELORA_LOCAL_AUTOMATION_RESERVED_TOKENS
            if automation
            else _SELORA_LOCAL_RESERVED_TOKENS
        )

    def _format_entities_block(self, entities: list[Any]) -> str:
        """AVAILABLE ENTITIES, one ``format_entity_line`` per entity up to the line cap.

        Calendar events and to-do items are lines too, so they count against it.
        """
        cap = self._entity_line_cap()
        lines: list[str] = ["AVAILABLE ENTITIES:"]
        rendered = 0
        skipped = 0
        for e in entities:
            if not isinstance(e, dict):
                continue
            # Calendars or lists schedule_context left out.
            skipped += int((e.get("attributes") or {}).get(SCHEDULE_ENTITIES_OMITTED) or 0)
            if rendered >= cap:
                skipped += 1
                continue
            eid = e.get("entity_id", "")
            # Events and open items are attached by llm_client.schedule_context; HA exposes neither as state.
            if isinstance(eid, str) and eid.startswith(("calendar.", "todo.")):
                entry = self._format_schedule_entity_lines(e, cap - rendered)
            else:
                entry = format_entity_line(e)
            entry_lines = entry.split("\n")
            lines.extend(entry_lines)
            rendered += len(entry_lines)
        if skipped:
            lines.append(f"{ENTITY_INDENT}... ({skipped} more entities not listed)")
        return "\n".join(lines)

    def _format_schedule_entity_lines(self, entity: dict[str, Any], room: int) -> str:
        """A calendar or list in at most ``room`` lines, its header counting the rows cut."""
        calendar = str(entity.get("entity_id", "")).startswith("calendar.")
        render = self._format_calendar_entity_lines if calendar else self._format_todo_entity_lines
        entry = render(entity)
        if entry.count("\n") < room:
            return entry
        # No room for a row: the plain line, rather than a header claiming none.
        if room <= 1:
            return format_entity_line(entity)
        attrs = entity.get("attributes") or {}
        rows_key, total_key = (
            ("events", "events_total") if calendar else ("todo_items", "todo_items_total")
        )
        rows = attrs.get(rows_key) or []
        cut = {
            **attrs,
            rows_key: rows[: room - 1],
            total_key: attrs.get(total_key) or len(rows),
        }
        return render({**entity, "attributes": cut})

    def _format_existing_automations_block(
        self, automations: list[dict[str, Any]], request: str = ""
    ) -> str:
        """EXISTING AUTOMATIONS, one alias per line up to the cap.

        Aliases sharing words with the request go first, so the cap never
        hides the automation the user is talking about.
        """
        from ....helpers import sanitize_untrusted_text

        def words(text: str) -> set[str]:
            return {w for w in re.findall(r"\w+", text.casefold()) if len(w) > 2}

        named = [str(a.get("alias") or a.get("entity_id") or "(unnamed)") for a in automations]
        wanted = words(request)
        named.sort(key=lambda alias: -len(wanted & words(alias)))
        aliases = [
            sanitize_untrusted_text(alias, limit=_SELORA_LOCAL_MAX_AUTOMATION_ALIAS_CHARS)
            for alias in named[:_SELORA_LOCAL_MAX_AUTOMATION_LINES]
        ]
        if skipped := len(automations) - len(aliases):
            aliases.append(f"... ({skipped} more automations not listed)")
        return format_existing_automations_block(aliases)

    def _build_training_user_content(self) -> str:
        """The current user turn in the corpus layout, for every kind and backend."""
        raw = self._user_message_raw.get()
        docs_block = ""
        if (self._call_kind.get() or "") == "chat_utilities":
            # No externally-supplied docs (the hub does no server-side retrieval) → retrieve from the bundled corpus using the user's question, so the specialist grounds its advice in the real docs instead of answering from parametric memory.
            docs = self._docs_for_lora.get() or _selora_local_retrieve_doc_chunks(raw)
            docs_block = format_relevant_docs_block(docs)
        return build_user_turn(
            raw,
            self._format_existing_automations_block(self._automations_for_lora.get() or [], raw),
            self._format_entities_block(self._entities_for_lora.get() or []),
            docs_block,
        )

    def _build_training_messages(
        self, fallback_messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Build the messages list the LoRA expects: optional last-3 prior turns (alternating user/assistant), then the current training-format user message."""
        if not self._user_message_raw.get():
            return fallback_messages
        out: list[dict[str, Any]] = []
        kind = self._call_kind.get() or ""
        if kind not in _SELORA_LOCAL_NO_HISTORY_KINDS:
            for h in (self._history_for_lora.get() or [])[-_SELORA_LOCAL_HISTORY_TURNS:]:
                role = h.get("role")
                content = h.get("content") or ""
                if role in ("user", "assistant") and content:
                    out.append({"role": role, "content": content})
        out.append({"role": "user", "content": self._build_training_user_content()})
        return out

    def build_payload(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        # Reflect the resolved intent in self._model so the usage callback (which reports against self._model) tags telemetry per specialist.
        intent = self._resolve_intent()
        if self._backend == SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED:
            # ONE self-routing model for every intent; the model infers the intent
            # from the request. _ensure_unified_model settled the tag before the
            # request went out, so this only reads it.
            self._model = self._unified_model or SELORA_LOCAL_OLLAMA_UNIFIED_MODEL_FAMILY
        else:
            self._model = self._base_model_id or intent
        # The system prompt must be the trained one, byte for byte.
        # The self-routing model was trained against ONE router prompt for every
        # intent, so handing it a per-specialist prompt is exactly that mismatch --
        # it would be asked for a shape it never saw in training.
        prompt_key = (
            _SELORA_LOCAL_UNIFIED_PROMPT_KEY
            if self._backend == SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED
            else intent
        )
        trained_system = self._specialist_prompts.get(prompt_key, system)
        # Re-attach the request-language directive.
        from ....llm_client.prompts import _language_directive

        lang_directive = _language_directive(self._language_for_lora.get())
        if lang_directive and trained_system:
            trained_system = f"{lang_directive}{trained_system}"
        training_messages = self._build_training_messages(messages)
        payload = super().build_payload(
            trained_system,
            training_messages,
            tools=tools,
            stream=stream,
            max_tokens=max_tokens,
        )
        # Cap: hub ctx is 4096 tokens; overflow -> HTTP 500, so bound entities/history.
        payload["max_tokens"] = self._resolve_max_tokens(payload.get("max_tokens", max_tokens))
        # The hub's OpenAI-compat surface accepts the basic chat fields only.
        payload.pop("tools", None)
        payload.pop("stream_options", None)
        # Stop on ChatML markers + Qwen3's specific tokens.
        payload["stop"] = list(_SELORA_LOCAL_STOP_MARKERS)
        # Lets llama-server reuse the cached prefix. USER REQUEST opens the user turn, so across different sentences that is the system prompt only.
        payload["cache_prompt"] = True
        if (lora := self._lora_vector(intent)) is not None:
            payload["lora"] = lora
        # Greedy decoding.
        payload.setdefault("temperature", 0.0)
        payload.setdefault("repeat_penalty", 1.0)
        # Qwen3 ChatML defaults to thinking mode, which emits <think>…</think> tokens that llama-server strips before returning ``content``.
        kwargs = payload.setdefault("chat_template_kwargs", {})
        kwargs.setdefault("enable_thinking", False)
        # Ollama ignores ``chat_template_kwargs``; its OpenAI-compatible
        # endpoint turns thinking off through ``reasoning_effort`` instead. A
        # GGUF whose template forces thinking off needs neither, but an older
        # or third-party build would otherwise think on every turn.
        if self._backend == SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED:
            payload.setdefault("reasoning_effort", "none")

        return payload
