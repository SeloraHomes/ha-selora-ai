"""Conversation platform for Selora AI."""

from __future__ import annotations

import logging
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
        history: list[dict[str, str]] = []
        for entry in chat_log.content:
            if entry.role in ("user", "assistant") and entry.content:
                history.append({"role": entry.role, "content": entry.content})
        # Drop the last user entry — architect_chat receives it as user_message
        if history and history[-1]["role"] == "user":
            history.pop()

        # Use architect_chat for rich responses and automation generation
        _LOGGER.debug("Selora AI Assist processing: %s", user_input.text)
        result: dict[str, Any] = await llm.architect_chat(
            user_input.text,
            entities,
            existing_automations=automations,
            history=history or None,
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

        response.async_set_speech(response_text)

        # Persist the assistant turn so subsequent messages in the same
        # conversation see the full history (enables confirmation follow-ups).
        chat_log.async_add_assistant_content_without_tools(
            AssistantContent(agent_id=self.entity_id, content=response_text)
        )

        return conversation.ConversationResult(
            response=response,
            conversation_id=chat_log.conversation_id,
            continue_conversation=chat_log.continue_conversation,
        )
