"""Tests for the Selora AI conversation platform.

Uses the real ``hass`` fixture and HA conversation imports — no sys.modules
stubbing.  ``pytest-homeassistant-custom-component`` brings all transitive
deps (hassil, home-assistant-intents, etc.) so the real import surface is
exercised.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.conversation import SeloraConversationEntity


@pytest.fixture(autouse=True)
def _enable_custom_component(enable_custom_integrations):
    """Auto-enable custom integrations so our domain is discoverable."""


def _make_entity(hass: HomeAssistant) -> SeloraConversationEntity:
    """Create a conversation entity with a mock config entry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    return SeloraConversationEntity(hass, entry)


def _make_user_input(text: str = "Turn on the lights") -> MagicMock:
    user_input = MagicMock()
    user_input.text = text
    user_input.language = "en"
    user_input.conversation_id = "conv_001"
    return user_input


class TestSeloraConversationEntity:
    """Tests for SeloraConversationEntity."""

    async def test_returns_fallback_when_llm_not_in_data(self, hass) -> None:
        """When the entry has no llm key, return a fallback response."""
        hass.data[DOMAIN] = {}
        entity = _make_entity(hass)
        user_input = _make_user_input()
        chat_log = MagicMock()

        result = await entity._async_handle_message(user_input, chat_log)
        speech = result.response.speech["plain"]["speech"]
        assert "not initialized" in speech.lower() or "brain" in speech.lower()

    async def test_returns_fallback_when_llm_is_none(self, hass) -> None:
        hass.data[DOMAIN] = {"test_entry_id": {"llm": None}}
        entity = _make_entity(hass)
        user_input = _make_user_input()
        chat_log = MagicMock()

        result = await entity._async_handle_message(user_input, chat_log)
        speech = result.response.speech["plain"]["speech"]
        assert "unconfigured" in speech.lower()

    async def test_supported_languages(self, hass) -> None:
        entity = _make_entity(hass)
        assert entity.supported_languages == ["en"]

    async def test_handles_llm_answer_response(self, hass) -> None:
        mock_llm = MagicMock()
        mock_llm.architect_chat = AsyncMock(
            return_value={
                "response": "The living room light is currently on.",
                "intent": "answer",
            }
        )
        hass.data[DOMAIN] = {"test_entry_id": {"llm": mock_llm}}

        entity = _make_entity(hass)
        user_input = _make_user_input("What's the status of my lights?")
        chat_log = MagicMock()

        with patch(
            "custom_components.selora_ai._collect_entity_states",
            return_value=[],
        ):
            result = await entity._async_handle_message(user_input, chat_log)

        assert "living room light" in result.response.speech["plain"]["speech"].lower()

    async def test_handles_command_intent_calls_services(self, hass) -> None:
        service_calls: list[tuple[str, str, dict]] = []

        async def _track(call):
            service_calls.append((call.domain, call.service, dict(call.data)))

        hass.services.async_register("light", "turn_on", _track)

        mock_llm = MagicMock()
        mock_llm.architect_chat = AsyncMock(
            return_value={
                "response": "Done! I turned on the lights.",
                "intent": "command",
                "calls": [
                    {
                        "service": "light.turn_on",
                        "target": {"entity_id": "light.living_room"},
                        "data": {},
                    }
                ],
            }
        )
        hass.data[DOMAIN] = {"test_entry_id": {"llm": mock_llm}}

        entity = _make_entity(hass)
        user_input = _make_user_input("Turn on the living room lights")
        chat_log = MagicMock()

        with patch(
            "custom_components.selora_ai._collect_entity_states",
            return_value=[],
        ):
            await entity._async_handle_message(user_input, chat_log)

        assert ("light", "turn_on", {"entity_id": "light.living_room"}) in service_calls

    async def test_handles_llm_error(self, hass) -> None:
        mock_llm = MagicMock()
        mock_llm.architect_chat = AsyncMock(return_value={"error": "API rate limit exceeded"})
        hass.data[DOMAIN] = {"test_entry_id": {"llm": mock_llm}}

        entity = _make_entity(hass)
        user_input = _make_user_input()
        chat_log = MagicMock()

        with patch(
            "custom_components.selora_ai._collect_entity_states",
            return_value=[],
        ):
            result = await entity._async_handle_message(user_input, chat_log)

        assert result.response.error_code is not None
