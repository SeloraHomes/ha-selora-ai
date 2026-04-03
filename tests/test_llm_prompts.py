"""Tests for LLM system prompt verbosity constraints.

Verifies that both the JSON-mode and streaming system prompts include
explicit brevity directives so the LLM produces concise chat responses,
and that those directives do not conflict with tool-policy formatting rules.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.selora_ai.llm_client import LLMClient


@pytest.fixture(autouse=True)
def _enable_custom_component(enable_custom_integrations):
    """Auto-enable custom integrations so our domain is discoverable."""


def _make_client(hass) -> LLMClient:
    """Create an LLMClient with dummy config for prompt inspection."""
    return LLMClient(hass, provider="anthropic", api_key="test-key")


class TestArchitectPromptVerbosity:
    """JSON-mode prompt (_build_architect_system_prompt) conciseness."""

    def test_contains_sentence_limits(self, hass) -> None:
        """Prompt specifies sentence counts per intent type."""
        prompt = _make_client(hass)._build_architect_system_prompt()
        assert "1 sentence" in prompt.lower() or "1-sentence" in prompt.lower()
        assert "1-2 sentence" in prompt.lower()
        assert "1-3 sentence" in prompt.lower()

    def test_forbids_filler_phrases(self, hass) -> None:
        """Prompt explicitly bans common LLM filler openings."""
        prompt = _make_client(hass)._build_architect_system_prompt()
        assert "Sure!" in prompt
        assert "Great question!" in prompt
        assert "I can help with that" in prompt

    def test_forbids_echoing_user(self, hass) -> None:
        """Prompt tells the LLM not to echo the user's full request back."""
        prompt = _make_client(hass)._build_architect_system_prompt()
        assert "Do NOT echo" in prompt

    def test_keeps_entity_names_in_commands(self, hass) -> None:
        """Prompt requires naming targeted entities in command confirmations."""
        prompt = _make_client(hass)._build_architect_system_prompt()
        assert "name the targeted entities" in prompt

    def test_automation_response_includes_entities(self, hass) -> None:
        """Automation response must mention targeted entities for MCP callers."""
        prompt = _make_client(hass)._build_architect_system_prompt()
        assert "mention all targeted entities" in prompt

    def test_preserves_description_field_completeness(self, hass) -> None:
        """Prompt protects the structured description field from the brevity ban."""
        prompt = _make_client(hass)._build_architect_system_prompt()
        assert '"description" field MUST remain' in prompt

    def test_allows_setup_steps(self, hass) -> None:
        """Prompt allows numbered steps for setup/integration guidance."""
        prompt = _make_client(hass)._build_architect_system_prompt()
        assert "numbered steps" in prompt

    def test_brevity_scoped_to_conversational(self, hass) -> None:
        """Brevity rules are scoped to conversational responses, not tool-backed answers."""
        prompt = _make_client(hass)._build_architect_system_prompt()
        assert "NOT tool-backed answers" in prompt

    def test_tool_policy_formatting_preserved(self, hass) -> None:
        """Tool policy output formatting rules are still present."""
        prompt = _make_client(hass)._build_architect_system_prompt()
        assert "List EVERY entity" in prompt
        assert "bullet-pointed lists" in prompt.lower() or "bullet" in prompt.lower()

    def test_brevity_after_tool_policy(self, hass) -> None:
        """Brevity rules appear after the tool policy so tool formatting takes precedence."""
        prompt = _make_client(hass)._build_architect_system_prompt()
        tool_policy_pos = prompt.find("List EVERY entity")
        brevity_pos = prompt.find("NOT tool-backed answers")
        assert tool_policy_pos < brevity_pos


class TestStreamPromptVerbosity:
    """Streaming prompt (_build_architect_stream_system_prompt) conciseness."""

    def test_contains_sentence_limits(self, hass) -> None:
        """Prompt specifies sentence counts per response type."""
        prompt = _make_client(hass)._build_architect_stream_system_prompt()
        assert "1 sentence" in prompt.lower() or "1-sentence" in prompt.lower()
        assert "1-2 sentence" in prompt.lower()
        assert "1-3 sentence" in prompt.lower()

    def test_forbids_filler_phrases(self, hass) -> None:
        """Prompt explicitly bans common LLM filler openings."""
        prompt = _make_client(hass)._build_architect_stream_system_prompt()
        assert "Sure!" in prompt
        assert "Great question!" in prompt
        assert "Absolutely!" in prompt

    def test_forbids_echoing_user(self, hass) -> None:
        """Prompt tells the LLM not to echo the user's full request back."""
        prompt = _make_client(hass)._build_architect_stream_system_prompt()
        assert "Do NOT echo" in prompt

    def test_keeps_entity_names_in_commands(self, hass) -> None:
        """Prompt requires naming targeted entities in command confirmations."""
        prompt = _make_client(hass)._build_architect_stream_system_prompt()
        assert "name the targeted entities" in prompt

    def test_forbids_entity_listing_in_automations(self, hass) -> None:
        """Prompt tells the LLM not to enumerate entities in automation responses."""
        prompt = _make_client(hass)._build_architect_stream_system_prompt()
        assert "do not list every entity" in prompt.lower()

    def test_preserves_description_field_completeness(self, hass) -> None:
        """Prompt protects the structured description field from the brevity ban."""
        prompt = _make_client(hass)._build_architect_stream_system_prompt()
        assert '"description" field MUST remain' in prompt

    def test_discourages_bullet_lists(self, hass) -> None:
        """Prompt discourages bullet lists in favor of flowing text."""
        prompt = _make_client(hass)._build_architect_stream_system_prompt()
        assert "bullet list" in prompt.lower()

    def test_brevity_scoped_to_conversational(self, hass) -> None:
        """Brevity rules are scoped to conversational responses, not tool-backed answers."""
        prompt = _make_client(hass)._build_architect_stream_system_prompt()
        assert "NOT tool-backed answers" in prompt

    def test_tool_policy_formatting_preserved(self, hass) -> None:
        """Tool policy output formatting rules are still present."""
        prompt = _make_client(hass)._build_architect_stream_system_prompt()
        assert "List EVERY entity" in prompt
        assert "bullet-pointed lists" in prompt.lower() or "bullet" in prompt.lower()

    def test_brevity_after_tool_policy(self, hass) -> None:
        """Brevity rules appear after the tool policy so tool formatting takes precedence."""
        prompt = _make_client(hass)._build_architect_stream_system_prompt()
        tool_policy_pos = prompt.find("List EVERY entity")
        brevity_pos = prompt.find("NOT tool-backed answers")
        assert tool_policy_pos < brevity_pos

    def test_response_format_defers_to_tool_policy(self, hass) -> None:
        """RESPONSE FORMAT section references tool policy for tool-backed answers."""
        prompt = _make_client(hass)._build_architect_stream_system_prompt()
        assert "tool-backed answers" in prompt.lower()
        assert "Output Formatting" in prompt

    def test_still_contains_automation_block_instructions(self, hass) -> None:
        """Ensure verbosity changes did not break the automation fenced block format."""
        prompt = _make_client(hass)._build_architect_stream_system_prompt()
        assert "```automation" in prompt
        assert '"alias"' in prompt
        assert '"triggers"' in prompt
