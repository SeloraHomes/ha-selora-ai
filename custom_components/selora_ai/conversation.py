"""Conversation platform for Selora AI."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from homeassistant.components import conversation
from homeassistant.components.conversation import (
    AssistantContent,
    ChatLog,
    ConversationEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import intent
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_SCENE_DELETED, SIGNAL_SCENE_REFRESHED, SIGNAL_SCENE_RESTORED

if TYPE_CHECKING:
    from .llm_client import LLMClient

_LOGGER = logging.getLogger(__name__)

# Matches the scene metadata marker appended to Assist chat log entries.
_SCENE_MARKER_RE = re.compile(r"\[Selora scene: id=([^,]+), name=[^\]]+\]")
# Captures scene_id, name, and trailing YAML from an Assist marker.
_SCENE_CONTEXT_RE = re.compile(r"\[Selora scene: id=([^,]+), name=([^\]]+)\](?:\n([\s\S*]*))?$")
# Strips a scene marker and its trailing YAML from the end of a message.
_SCENE_BLOCK_RE = re.compile(r"\n\n\[Selora scene: id=[^,]+, name=[^\]]+\](?:\n[\s\S]*)?$")

# Entity tile markers as embedded by the architect prompt. Assist
# renders the response as plain text — the panel-only hydration that
# turns these markers into HA tile cards is not available — so we
# unwrap them back to friendly names before speech / chat-log output.
_ENTITY_SINGLE_RE = re.compile(
    r"\[\[entity:(?P<id>[a-z_]+\.[a-z0-9_\-]+)(?:\|(?P<label>[^\]]+))?\]\]"
)
_ENTITIES_LIST_RE = re.compile(
    r"\[\[entities:(?P<ids>[a-z_]+\.[a-z0-9_\-]+(?:,\s*[a-z_]+\.[a-z0-9_\-]+)*)\]\]"
)


def _unwrap_entity_markers(text: str, entities: list[dict[str, Any]]) -> str:
    """Convert `[[entity:…]]` / `[[entities:…]]` markers to friendly names.

    Assist surfaces the assistant text verbatim (no panel hydration), so
    leaving markers in place shows the user raw `[[entity:cover.garage_door|…]]`
    syntax. Replace each marker with the entity's friendly_name (falling
    back to entity_id when unknown), then collapse stray blank-line runs
    the rewrite may leave behind.
    """
    if not text:
        return text
    friendly: dict[str, str] = {}
    for ent in entities:
        eid = ent.get("entity_id")
        if not eid:
            continue
        name = ((ent.get("attributes") or {}).get("friendly_name") or "").strip()
        friendly[eid] = name or eid

    def _single(m: re.Match[str]) -> str:
        eid = m.group("id")
        label = (m.group("label") or "").strip()
        return label or friendly.get(eid, eid)

    def _multi(m: re.Match[str]) -> str:
        ids = [s.strip() for s in m.group("ids").split(",") if s.strip()]
        return ", ".join(friendly.get(i, i) for i in ids)

    text = _ENTITY_SINGLE_RE.sub(_single, text)
    text = _ENTITIES_LIST_RE.sub(_multi, text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ── Smart-rewrite for follow-up affirmations ─────────────────────────
# When a user replies "yes please" / "do it" / "sounds good" to an
# automation suggestion ("Want me to also turn off the porch light?"),
# the LoRA's multi-turn echo bias often regenerates the prior automation
# instead of producing the suggested one. We bypass that by parsing the
# prior turn's suggestion + trigger time into a self-contained single-
# turn request and sending THAT as the user message (with history
# cleared). v0.4.2 was retrained on multi-turn pairs but the rewrite
# still helps on edge cases — and it costs nothing on cloud providers.

# Gratitude-only closings ("thanks", "thank you", "thx", "ty") are
# intentionally NOT in the leading alternation: in the common case a
# user says "thanks" as a sign-off after an automation is created, not
# as consent to a follow-up suggestion. They remain as a permitted
# trailing modifier ("yes please thanks") so genuine affirmations
# aren't rejected for ending with a polite tail.
_AFFIRMATION_RE = re.compile(
    r"^(yes|yeah|yep|yup|y|ya|sure|ok|okay|alright|fine|"
    r"great|perfect|awesome|cool|nice|excellent|wonderful|"
    r"sounds\s+good|sounds\s+great|that(?:'s| is)\s+(fine|good|great|perfect|nice)|"
    r"please\s+do\s+(it|that)|do\s+(it|that)|go\s+ahead)"
    r"(\s+(please|now|do\s+(it|that)|go\s+ahead|thanks?|thank\s+you|thx|ty|sure))*"
    r"[\s!.,?]*$",
    re.IGNORECASE,
)
# Bare affirmations are short. Reject anything past this word count so
# "yes turn off the lights" isn't treated as a follow-up confirmation.
_AFFIRMATION_MAX_WORDS = 5

# Suggestion shapes the bot uses when proposing follow-ups (per the
# v0.4.x automation system prompt + gen_multiturn.py training data).
# Captures the action clause inside.
_SUGGESTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Want me to (?:also )?(.+?)\?", re.IGNORECASE),
    re.compile(r"Should I (?:also )?(.+?)\?", re.IGNORECASE),
    re.compile(r"Would you like me to (?:also )?(.+?)\?", re.IGNORECASE),
    re.compile(r"Want me to add a similar one to (.+?)\?", re.IGNORECASE),
)

_MARKDOWN_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ENTITY_LINK_RE = re.compile(r"\[\[entity:[^|\]]+\|([^\]]+)\]\]")
_TIME_CLAUSE_RE = re.compile(
    r"\b(at\s+\d|at\s+sunset|at\s+sunrise|every\s+(day|morning|night))\b",
    re.IGNORECASE,
)

# Cap on the per-conversation last-result cache. Conversations are
# typically <50 active per user; this prevents unbounded growth across
# long-lived HA processes.
_LAST_RESULT_CACHE_MAX = 50


def _is_affirmation(message: str) -> bool:
    """Return True when ``message`` is a bare affirmation with no other content."""
    text = message.strip()
    if not text or len(text.split()) > _AFFIRMATION_MAX_WORDS:
        return False
    return bool(_AFFIRMATION_RE.match(text))


def _strip_response_markdown(text: str) -> str:
    """Drop **bold** and [[entity:id|name]] markers so suggestion-extraction
    sees plain text the LoRA can act on."""
    text = _MARKDOWN_BOLD_RE.sub(r"\1", text)
    text = _ENTITY_LINK_RE.sub(r"\1", text)
    return text


def _extract_suggestion(prior_response: str) -> str | None:
    """Pull the action clause from a 'Want me to also X?' style suggestion.
    Returns the clause with trailing punctuation trimmed, or None when no
    suggestion is found."""
    if not prior_response:
        return None
    clean = _strip_response_markdown(prior_response)
    for pat in _SUGGESTION_PATTERNS:
        m = pat.search(clean)
        if m:
            return m.group(1).strip().rstrip(".,;:")
    return None


def _format_time_at(at: str) -> str:
    """Render a HA ``time`` trigger value (``HH:MM`` or ``HH:MM:SS``) as a
    12-hour natural-language phrase.

    Minutes and seconds are preserved when non-zero — dropping them as
    earlier versions of this helper did would silently shift the
    synthesized follow-up automation to the wrong moment when the prior
    trigger was, say, ``07:30:00``. When the input can't be parsed
    fall back to the literal value so the LoRA at least sees the
    original spec.
    """
    parts = at.split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        s = int(parts[2]) if len(parts) > 2 else 0
    except (
        ValueError,
        IndexError,
    ):
        return at
    if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59):
        return at
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    if s:
        return f"{h12}:{m:02d}:{s:02d}{suffix}"
    if m:
        return f"{h12}:{m:02d}{suffix}"
    return f"{h12}{suffix}"


def _time_clause_from_automation(automation_obj: dict[str, Any] | None) -> str:
    """Translate the prior automation's first time/sun trigger into a
    natural-language time clause we can append to the suggestion when
    the suggestion didn't already mention a time."""
    if not isinstance(automation_obj, dict):
        return ""
    raw = automation_obj.get("triggers") or automation_obj.get("trigger") or []
    triggers = [raw] if isinstance(raw, dict) else raw if isinstance(raw, list) else []
    for t in triggers:
        if not isinstance(t, dict):
            continue
        kind = t.get("trigger") or t.get("platform")
        if kind == "time":
            at = t.get("at")
            if isinstance(at, str) and at:
                return f" at {_format_time_at(at)} every day"
        elif kind == "sun":
            return f" at {t.get('event', 'sunset')} every day"
    return ""


def _synthesize_followup_request(
    prior_response: str,
    prior_automation: dict[str, Any] | None,
) -> str | None:
    """Convert an affirmation + prior automation suggestion into a
    self-contained single-turn user request. Returns None when the
    prior turn didn't propose a follow-up the LoRA can act on (so
    the caller falls back to normal multi-turn classification)."""
    if not isinstance(prior_automation, dict):
        return None
    suggestion = _extract_suggestion(prior_response)
    if not suggestion:
        return None
    if not _TIME_CLAUSE_RE.search(suggestion):
        suggestion = suggestion + _time_clause_from_automation(prior_automation)
    return suggestion


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up conversation platform."""
    async_add_entities([SeloraConversationEntity(hass, config_entry)])


class SeloraConversationEntity(conversation.ConversationEntity):
    """Selora AI conversation entity."""

    _attr_supported_features = ConversationEntityFeature.CONTROL

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the agent."""
        self.hass = hass
        self.entry = entry
        self._attr_name = "Selora AI"
        self._attr_unique_id = f"{entry.entry_id}-conversation"
        # Per-conversation scene index that survives chat_log truncation.
        # Keyed by conversation_id → {scene_id: (name, yaml)}.
        self._scene_index: dict[str, dict[str, tuple[str, str]]] = {}
        # Scene IDs that have been deleted during this process lifetime.
        # Prevents chat_log markers from reseeding deleted scenes.
        self._deleted_scene_ids: set[str] = set()
        # Reverse index: scene_id → set of conversation_ids that had it.
        # Populated on delete so restore can precisely re-inject.
        self._deleted_scene_convs: dict[str, set[str]] = {}
        # Per-conversation last-result cache used by the smart-rewrite
        # path: when the user replies "yes please" to an automation
        # suggestion, we look up the prior turn's response + automation
        # here and synthesize a self-contained single-turn request.
        # Bounded by _LAST_RESULT_CACHE_MAX (oldest entry evicted when
        # full — Python 3.7+ dicts preserve insertion order).
        self._last_result_by_conv: dict[str, dict[str, Any]] = {}

    async def async_added_to_hass(self) -> None:
        """Subscribe to scene lifecycle signals when added to HA."""
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_SCENE_DELETED, self._handle_scene_deleted)
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_SCENE_REFRESHED, self._handle_scene_refreshed
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_SCENE_RESTORED, self._handle_scene_restored)
        )

    @callback
    def _handle_scene_deleted(self, scene_id: str) -> None:
        """Remove a deleted scene from every conversation's in-memory index."""
        self._deleted_scene_ids.add(scene_id)
        affected: set[str] = set()
        for conv_id, conv_scenes in self._scene_index.items():
            if scene_id in conv_scenes:
                conv_scenes.pop(scene_id)
                affected.add(conv_id)
        if affected:
            self._deleted_scene_convs[scene_id] = affected

    @callback
    def _handle_scene_refreshed(self, scene_id: str, name: str, yaml_repr: str) -> None:
        """Update a scene's cached YAML in every conversation that has it."""
        for conv_scenes in self._scene_index.values():
            if scene_id in conv_scenes:
                conv_scenes[scene_id] = (name, yaml_repr)

    @callback
    def _handle_scene_restored(self, scene_id: str, name: str, yaml_repr: str) -> None:
        """Clear the tombstone and re-inject into originally affected conversations."""
        self._deleted_scene_ids.discard(scene_id)
        affected = self._deleted_scene_convs.pop(scene_id, set())
        for conv_id in affected:
            conv_scenes = self._scene_index.get(conv_id)
            if conv_scenes is not None:
                conv_scenes[scene_id] = (name, yaml_repr)
            else:
                # Conversation was emptied on delete — recreate its index
                self._scene_index[conv_id] = {scene_id: (name, yaml_repr)}

    @property
    def supported_languages(self) -> list[str]:
        """Return a list of supported languages."""
        return ["en"]

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: ChatLog,
    ) -> conversation.ConversationResult:
        """Handle a sentence via HA Assist pipeline."""
        llm_data: dict[str, Any] | None = self.hass.data[DOMAIN].get(self.entry.entry_id)
        if not llm_data or "llm" not in llm_data:
            _LOGGER.warning("Selora AI LLM not initialized for entry %s", self.entry.entry_id)
            response = intent.IntentResponse(language=user_input.language)
            response.async_set_speech("I'm sorry, my brain isn't initialized yet.")
            return conversation.ConversationResult(
                response=response,
                conversation_id=user_input.conversation_id,
            )

        llm: LLMClient | None = llm_data["llm"]
        if llm is None:
            response = intent.IntentResponse(language=user_input.language)
            response.async_set_speech("Selora AI is currently in unconfigured mode.")
            return conversation.ConversationResult(
                response=response,
                conversation_id=user_input.conversation_id,
            )

        # Reconcile scene store so context reflects external edits
        from .helpers import get_scene_store  # noqa: PLC0415

        await get_scene_store(self.hass).async_reconcile_yaml()

        # Get current entity states for context
        from . import _collect_entity_states

        entities: list[dict[str, Any]] = _collect_entity_states(self.hass)

        # Get existing automations for context
        automations: list[dict[str, Any]] = []
        for state in self.hass.states.async_all("automation"):
            automations.append(
                {
                    "entity_id": state.entity_id,
                    "alias": state.attributes.get("friendly_name", state.entity_id),
                    "state": state.state,
                }
            )

        # Convert ChatLog into the history format architect_chat expects
        # so that confirmation follow-ups ("yes", "do it") work in Assist.
        #
        # Scene markers are deduplicated by scene_id: for each id, only the
        # latest revision's YAML is kept so the LLM doesn't see conflicting
        # snapshots after repeated refinements or renames.
        conv_id = chat_log.conversation_id

        latest_scene_entry: dict[str, int] = {}
        for idx, entry in enumerate(chat_log.content):
            if entry.role == "assistant" and entry.content:
                m = _SCENE_MARKER_RE.search(entry.content)
                if m and m.group(1) not in self._deleted_scene_ids:
                    latest_scene_entry[m.group(1)] = idx

        # Identify scene_ids already in _scene_index from a signal refresh
        # (these have fresher YAML than what the chat-log marker carries).
        signal_refreshed_ids: set[str] = set()
        if conv_id is not None:
            existing_index = self._scene_index.get(conv_id, {})
            signal_refreshed_ids = set(existing_index.keys())

        history: list[dict[str, str]] = []
        for idx, entry in enumerate(chat_log.content):
            if entry.role in ("user", "assistant") and entry.content:
                content = entry.content
                # Strip scene YAML blocks when superseded by a later
                # revision OR when the scene was refreshed via signal
                # (the fresh YAML is provided in scene_context instead).
                if entry.role == "assistant":
                    m = _SCENE_MARKER_RE.search(content)
                    if m and (
                        latest_scene_entry.get(m.group(1)) != idx
                        or m.group(1) in signal_refreshed_ids
                    ):
                        content = _SCENE_BLOCK_RE.sub("", content)
                history.append({"role": entry.role, "content": content})
        # Drop the last user entry — architect_chat receives it as user_message
        if history and history[-1]["role"] == "user":
            history.pop()

        # Build scene context from the entity-level index (survives
        # chat_log truncation).  Seed from chat_log markers only for
        # scene_ids not already in the index — existing entries may have
        # been updated by SIGNAL_SCENE_REFRESHED or SIGNAL_SCENE_RESTORED
        # and must not be overwritten by stale chat-log snapshots.
        # For stateless flows (conv_id is None), use a local dict that is
        # not stored in _scene_index to avoid unbounded memory growth.
        conv_scenes: dict[str, tuple[str, str]] = (
            self._scene_index.setdefault(conv_id, {}) if conv_id is not None else {}
        )
        for idx in latest_scene_entry.values():
            entry = chat_log.content[idx]
            if entry.content:
                cm = _SCENE_CONTEXT_RE.search(entry.content)
                if cm and cm.group(1) not in conv_scenes:
                    conv_scenes[cm.group(1)] = (cm.group(2), cm.group(3) or "")

        assist_scenes: list[tuple[str, str, str]] = [
            (sid, name, yaml) for sid, (name, yaml) in conv_scenes.items() if yaml
        ]

        # Smart-rewrite: when the user replies with a bare affirmation
        # ("yes please", "do it", etc.) and the prior turn produced an
        # automation with a "Want me to also X?" suggestion, synthesize
        # a self-contained single-turn request from the suggestion + the
        # prior automation's trigger time. Bypasses the LoRA's multi-
        # turn echo bias by sending the rewritten message with no
        # history. Falls back to the normal multi-turn path when the
        # prior turn didn't propose anything we can parse.
        effective_text: str = user_input.text
        effective_history: list[dict[str, str]] | None = history or None
        if conv_id is not None and _is_affirmation(user_input.text):
            cached = self._last_result_by_conv.get(conv_id)
            if cached:
                synth = _synthesize_followup_request(
                    cached.get("response", ""),
                    cached.get("automation"),
                )
                if synth:
                    _LOGGER.debug(
                        "Selora AI smart-rewrite: %r → %r",
                        user_input.text,
                        synth,
                    )
                    effective_text = synth
                    effective_history = None

        # Use architect_chat for rich responses and automation generation.
        # `for_assist=True` swaps the marker-emission rules in the prompt
        # for plain-prose rules — Assist renders the assistant text
        # verbatim, so `[[entity:…]]` markers would leak through as raw
        # syntax. The panel chat path keeps markers enabled.
        _LOGGER.debug("Selora AI Assist processing: %s", effective_text)
        result: dict[str, Any] = await llm.architect_chat(
            effective_text,
            entities,
            existing_automations=automations,
            history=effective_history,
            scene_context=assist_scenes or None,
            for_assist=True,
            language=user_input.language,
        )

        response = intent.IntentResponse(language=user_input.language)

        if "error" in result:
            _LOGGER.error("Selora AI LLM error: %s", result["error"])
            response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                result["error"],
            )
            return conversation.ConversationResult(
                response=response,
                conversation_id=chat_log.conversation_id,
            )

        response_text: str = result.get("response", "I'm not sure how to help with that.")
        intent_type: str = result.get("intent", "answer")
        scene_result: dict[str, Any] | None = None

        # Invalidate the smart-rewrite cache when this turn isn't an
        # automation. Otherwise an answer/command/scene reply followed
        # by a later "yes" would synthesize the stale prior automation
        # suggestion instead of responding to the actual preceding turn.
        # The automation branch below repopulates the cache with the
        # fresh result, so successive automation suggestions still
        # support follow-up affirmations.
        if conv_id is not None and intent_type != "automation":
            self._last_result_by_conv.pop(conv_id, None)

        if intent_type == "command":
            # Execute immediate commands via HA Assist context
            calls: list[dict[str, Any]] = result.get("calls", [])
            for call in calls:
                service: str = call.get("service", "")
                if not service or "." not in service:
                    continue
                domain_part: str
                service_name: str
                domain_part, service_name = service.split(".", 1)
                target: dict[str, Any] = call.get("target", {})
                data: dict[str, Any] = call.get("data", {})
                try:
                    await self.hass.services.async_call(
                        domain_part,
                        service_name,
                        {**data, **target},
                        blocking=True,
                    )
                except Exception as exc:
                    _LOGGER.error("Failed to execute %s via Assist: %s", service, exc)

        elif intent_type == "automation" and result.get("automation"):
            desc: str = result.get("description", "")
            if desc:
                response_text += f"\n\nAutomation summary: {desc}"
            response_text += "\n\n(Draft automation created — review and enable it in the Selora AI sidebar panel.)"
            # Cache for next-turn smart-rewrite. Only automations carry
            # the "Want me to also X?" suggestion that the rewrite path
            # parses, so other intents are skipped. We cache the raw
            # LLM ``response`` field rather than ``response_text`` so
            # the suggestion-regex isn't tripped up by the appended
            # "Automation summary:" / "(Draft automation created…)"
            # suffixes added above.
            if conv_id is not None:
                if len(self._last_result_by_conv) >= _LAST_RESULT_CACHE_MAX:
                    oldest = next(iter(self._last_result_by_conv))
                    self._last_result_by_conv.pop(oldest, None)
                self._last_result_by_conv[conv_id] = {
                    "response": result.get("response", "") or "",
                    "automation": result["automation"],
                }

        elif intent_type == "scene" and result.get("scene"):
            # Scene creation writes to scenes.yaml — require admin.
            # When user_id is absent (e.g. voice satellite flows), allow the
            # request through since blocking it would make scenes unreachable
            # on those Assist paths.
            is_admin = True
            user_id: str | None = user_input.context.user_id
            if user_id:
                user = await self.hass.auth.async_get_user(user_id)
                is_admin = user is not None and user.is_admin
            if not is_admin:
                response_text = "Scene creation requires an administrator account."
            else:
                # The LLM includes refine_scene_id when modifying an
                # existing scene — the id comes from the history context.
                # Known scene IDs come from the entity-level index
                # (survives chat_log truncation).
                try:
                    from .scene_utils import async_create_scene  # noqa: PLC0415

                    scene_result = await async_create_scene(
                        self.hass,
                        result["scene"],
                        existing_scene_id=result.get("refine_scene_id"),
                        session_scene_ids=set(conv_scenes.keys()),
                    )
                    scene_name: str = scene_result.get("name", "scene")
                    response_text += f"\n\n(Scene '{scene_name}' saved — activate it from Scenes in the HA sidebar.)"

                    # Update the entity-level scene index so future
                    # turns see this scene even after chat_log trims.
                    # Use the sanitized scene from scene_result so the
                    # refinement context matches what was written.
                    conv_scenes[scene_result["scene_id"]] = (
                        scene_name,
                        scene_result.get("scene_yaml", ""),
                    )
                except Exception as exc:  # noqa: BLE001 — HA service handlers may raise beyond HA's hierarchy
                    _LOGGER.error("Failed to create scene via Assist: %s", exc)
                    response_text += f"\n\n(Scene creation failed: {exc})"
                    scene_result = None

                if scene_result is not None:
                    try:
                        from .helpers import get_scene_store  # noqa: PLC0415

                        scene_store = get_scene_store(self.hass)
                        await scene_store.async_add_scene(
                            scene_result["scene_id"],
                            scene_result["name"],
                            scene_result["entity_count"],
                            entity_id=scene_result.get("entity_id"),
                            content_hash=scene_result.get("content_hash"),
                        )
                    except Exception:  # noqa: BLE001 — store failure doesn't invalidate the created scene
                        _LOGGER.warning(
                            "Failed to record scene %s in store", scene_result["scene_id"]
                        )

        elif intent_type in ("delayed_command", "cancel"):
            # Persisted schedules (scheduled_time → automation) and cancel
            # of automation-backed tasks modify automations.yaml — require
            # admin, same gate as scene creation above.  Relative delays
            # and cancelling in-memory timers are safe for all users.
            needs_admin = False
            if result.get("scheduled_time") is not None:
                needs_admin = True
            elif intent_type == "cancel":
                # Check whether the task being cancelled is automation-backed
                from .scheduled_actions import ScheduledTaskTracker  # noqa: PLC0415

                _tracker = self.hass.data.get(DOMAIN, {}).get("_scheduled_tasks")
                if isinstance(_tracker, ScheduledTaskTracker):
                    _latest = _tracker.get_latest_pending(chat_log.conversation_id)
                    needs_admin = _latest is not None and _latest.automation_id is not None

            if needs_admin:
                is_sched_admin = True
                sched_user_id: str | None = user_input.context.user_id
                if sched_user_id:
                    sched_user = await self.hass.auth.async_get_user(sched_user_id)
                    is_sched_admin = sched_user is not None and sched_user.is_admin
                if not is_sched_admin:
                    response_text = (
                        "Scheduling actions at a specific time requires an administrator account."
                    )
                    intent_type = "answer"

            if intent_type in ("delayed_command", "cancel"):
                from . import _handle_scheduled_intent  # noqa: PLC0415

                intent_type, response_text, _schedule_id = await _handle_scheduled_intent(
                    self.hass, intent_type, result, chat_log.conversation_id
                )

        # Defensive: the prompt instructs the LLM not to emit entity
        # markers in Assist mode, but compliance is probabilistic, so
        # unwrap any markers it does emit back to friendly names.
        response_text = _unwrap_entity_markers(response_text, entities)
        response.async_set_speech(response_text)

        # The chat log gets extra scene context (YAML + scene_id) so the
        # LLM can reference it and the refinement lookup can find the
        # prior scene_id.  This is never spoken or shown to the user.
        log_content = response_text
        if intent_type == "scene" and scene_result:
            sid: str = scene_result["scene_id"]
            # Escape brackets so the name can't break the marker delimiters
            # that _SCENE_MARKER_RE / _SCENE_BLOCK_RE rely on.
            s_name: str = scene_result.get("name", "").replace("[", "(").replace("]", ")")
            log_content += f"\n\n[Selora scene: id={sid}, name={s_name}]"
            scene_yaml: str = scene_result.get("scene_yaml", "")
            if scene_yaml:
                log_content += f"\n{scene_yaml}"

        # Persist the assistant turn so subsequent messages in the same
        # conversation see the full history (enables confirmation follow-ups).
        chat_log.async_add_assistant_content_without_tools(
            AssistantContent(agent_id=self.entity_id, content=log_content)
        )

        return conversation.ConversationResult(
            response=response,
            conversation_id=chat_log.conversation_id,
            continue_conversation=chat_log.continue_conversation,
        )
