"""Conversation platform for Selora AI."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

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

    async def async_handle_message(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        """Handle a sentence."""
        llm_data = self.hass.data[DOMAIN].get(self.entry.entry_id)
        if not llm_data or "llm" not in llm_data:
            _LOGGER.warning("Selora AI LLM not initialized for entry %s", self.entry.entry_id)
            response = intent.IntentResponse(language=user_input.language)
            response.async_set_speech("I'm sorry, my brain isn't initialized yet.")
            return conversation.ConversationResult(
                response=response,
                conversation_id=user_input.conversation_id,
            )

        llm = llm_data["llm"]
        if llm is None:
            response = intent.IntentResponse(language=user_input.language)
            response.async_set_speech("Selora AI is currently in unconfigured mode.")
            return conversation.ConversationResult(
                response=response,
                conversation_id=user_input.conversation_id,
            )

        # Get current entity states for context
        from . import _collect_entity_states
        entities = _collect_entity_states(self.hass)

        # Get existing automations for context
        automations = []
        for state in self.hass.states.async_all("automation"):
            automations.append({
                "entity_id": state.entity_id,
                "alias": state.attributes.get("friendly_name", state.entity_id),
                "state": state.state,
            })

        # Use architect_chat for rich responses and automation generation
        _LOGGER.debug("Selora AI Assist processing: %s", user_input.text)
        result = await llm.architect_chat(
            user_input.text, 
            entities,
            existing_automations=automations
        )
        
        response = intent.IntentResponse(language=user_input.language)
        
        if "error" in result:
            _LOGGER.error("Selora AI LLM error: %s", result["error"])
            response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                result["error"]
            )
            return conversation.ConversationResult(
                response=response,
                conversation_id=user_input.conversation_id,
            )

        response_text = result.get("response", "I'm not sure how to help with that.")
        
        # If there's an automation, we inform the user it's in the panel
        if result.get("automation"):
            response_text += "\n\n(I've generated a draft automation. You can review and enable it in the Selora AI sidebar panel.)"

        response.async_set_speech(response_text)
        
        return conversation.ConversationResult(
            response=response,
            conversation_id=user_input.conversation_id,
        )
