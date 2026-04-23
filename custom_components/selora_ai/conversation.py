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
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

if TYPE_CHECKING:
    from .llm_client import LLMClient

_LOGGER = logging.getLogger(__name__)

# Matches the scene metadata marker appended to Assist chat log entries.
_SCENE_MARKER_RE = re.compile(r"\[Selora scene: id=([^,]+), name=[^\]]+\]")
# Captures scene_id, name, and trailing YAML from an Assist marker.
_SCENE_CONTEXT_RE = re.compile(r"\[Selora scene: id=([^,]+), name=([^\]]+)\](?:\n([\s\S*]*))?$")
# Strips a scene marker and its trailing YAML from the end of a message.
_SCENE_BLOCK_RE = re.compile(r"\n\n\[Selora scene: id=[^,]+, name=[^\]]+\](?:\n[\s\S]*)?$")


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
        latest_scene_entry: dict[str, int] = {}
        for idx, entry in enumerate(chat_log.content):
            if entry.role == "assistant" and entry.content:
                m = _SCENE_MARKER_RE.search(entry.content)
                if m:
                    latest_scene_entry[m.group(1)] = idx

        history: list[dict[str, str]] = []
        for idx, entry in enumerate(chat_log.content):
            if entry.role in ("user", "assistant") and entry.content:
                content = entry.content
                # Strip superseded scene markers so only the latest
                # revision of each scene_id appears in LLM history.
                if entry.role == "assistant":
                    m = _SCENE_MARKER_RE.search(content)
                    if m and latest_scene_entry.get(m.group(1)) != idx:
                        content = _SCENE_BLOCK_RE.sub("", content)
                history.append({"role": entry.role, "content": content})
        # Drop the last user entry — architect_chat receives it as user_message
        if history and history[-1]["role"] == "user":
            history.pop()

        # Build scene context from the entity-level index (survives
        # chat_log truncation).  Seed from chat_log markers for
        # conversations started before the index was populated.
        conv_id = chat_log.conversation_id
        conv_scenes = self._scene_index.setdefault(conv_id, {})
        for idx in latest_scene_entry.values():
            entry = chat_log.content[idx]
            if entry.content:
                cm = _SCENE_CONTEXT_RE.search(entry.content)
                if cm:
                    conv_scenes[cm.group(1)] = (cm.group(2), cm.group(3) or "")

        assist_scenes: list[tuple[str, str, str]] = [
            (sid, name, yaml) for sid, (name, yaml) in conv_scenes.items() if yaml
        ]

        # Use architect_chat for rich responses and automation generation
        _LOGGER.debug("Selora AI Assist processing: %s", user_input.text)
        result: dict[str, Any] = await llm.architect_chat(
            user_input.text,
            entities,
            existing_automations=automations,
            history=history or None,
            scene_context=assist_scenes or None,
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
                    from .scene_utils import async_create_scene

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
                    conv_scenes[scene_result["scene_id"]] = (
                        scene_name,
                        result.get("scene_yaml", ""),
                    )
                except Exception as exc:  # noqa: BLE001 — HA service handlers may raise beyond HA's hierarchy
                    _LOGGER.error("Failed to create scene via Assist: %s", exc)
                    response_text += f"\n\n(Scene creation failed: {exc})"

        # Speech gets clean text only — no internal markers or YAML.
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
            scene_yaml: str = result.get("scene_yaml", "")
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
