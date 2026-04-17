"""Tests for LLM system prompt verbosity constraints.

Verifies that both the JSON-mode and streaming system prompts include
explicit brevity directives so the LLM produces concise chat responses,
and that those directives do not conflict with tool-policy formatting rules.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.selora_ai.llm_client import LLMClient, _read_prompt_files
from custom_components.selora_ai.providers import create_provider

import custom_components.selora_ai.llm_client as _llm_mod


@pytest.fixture(autouse=True)
def _enable_custom_component(enable_custom_integrations):
    """Auto-enable custom integrations so our domain is discoverable."""


@pytest.fixture(autouse=True)
def _preload_prompts():
    """Load prompt files so system prompts include tool-policy text."""
    _llm_mod._TOOL_POLICY_TEXT, _llm_mod._DEVICE_KNOWLEDGE_TEXT = _read_prompt_files()


def _make_client(hass) -> LLMClient:
    """Create an LLMClient with dummy config for prompt inspection."""
    provider = create_provider("anthropic", hass, api_key="test-key")
    return LLMClient(hass, provider)


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


class TestActionBased:
    """Both prompts must instruct the LLM to always act on device commands."""

    def test_json_prompt_action_based(self, hass) -> None:
        prompt = _make_client(hass)._build_architect_system_prompt()
        assert "ACTION-BASED" in prompt
        assert "never ask" in prompt.lower()

    def test_stream_prompt_action_based(self, hass) -> None:
        prompt = _make_client(hass)._build_architect_stream_system_prompt()
        assert "ACTION-BASED" in prompt
        assert "never ask" in prompt.lower()

    def test_command_intent_always_executes(self, hass) -> None:
        """Command intent description must say to always execute immediately."""
        prompt = _make_client(hass)._build_architect_system_prompt()
        assert "ALWAYS execute immediately" in prompt

    def test_clarification_never_for_devices(self, hass) -> None:
        """Clarification intent must explicitly exclude device commands."""
        prompt = _make_client(hass)._build_architect_system_prompt()
        assert "NEVER use this for device commands" in prompt


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


class TestConversationHistoryManagement:
    """Verify conversation history is handled correctly (#89)."""

    def test_history_window_is_at_least_40(self) -> None:
        """The history window must be large enough for multi-turn conversations."""
        from custom_components.selora_ai.llm_client import _MAX_HISTORY_TURNS

        assert _MAX_HISTORY_TURNS >= 40, (
            f"_MAX_HISTORY_TURNS={_MAX_HISTORY_TURNS} is too small; "
            "users lose context in long conversations"
        )

    def test_build_history_messages_filters_and_strips(self) -> None:
        """_build_history_messages strips whitespace, rejects non-user/assistant roles, coerces types."""
        from custom_components.selora_ai.llm_client import LLMClient

        history = [
            {"role": "user", "content": "  hello  "},
            {"role": "system", "content": "ignored"},
            {"role": "assistant", "content": "hi there"},
            {"role": "user", "content": "   "},  # whitespace-only -> dropped
            {"role": "assistant", "content": 42},  # non-str -> coerced to '42'
        ]
        result = LLMClient._build_history_messages(history)
        assert len(result) == 3
        assert result[0] == {"role": "user", "content": "hello"}
        assert result[1] == {"role": "assistant", "content": "hi there"}
        assert result[2] == {"role": "assistant", "content": "42"}

    def test_build_history_messages_respects_max_turns(self) -> None:
        """Only the most recent _MAX_HISTORY_TURNS turns are kept."""
        from custom_components.selora_ai.llm_client import LLMClient, _MAX_HISTORY_TURNS

        history = [{"role": "user", "content": f"msg-{i}"} for i in range(_MAX_HISTORY_TURNS + 20)]
        result = LLMClient._build_history_messages(history)
        assert len(result) == _MAX_HISTORY_TURNS
        assert result[0]["content"] == f"msg-20"  # oldest 20 dropped

    def test_trim_history_drops_oldest_first(self, hass) -> None:
        """When messages exceed the token budget, oldest turns are dropped."""
        client = _make_client(hass)
        # Force a very small budget by using the Ollama provider budget
        client._provider = create_provider("ollama", hass)
        messages = [
            {"role": "user", "content": "x" * 10_000}  # ~2857 tokens
            for _ in range(30)
        ]
        trimmed = client._trim_history_to_budget(
            messages, system_prompt="sys", context_prompt="ctx"
        )
        assert len(trimmed) < len(messages), "Some messages should have been dropped"
        # The last message in the original should be the last in trimmed (minus summary)
        assert trimmed[-1]["content"] == "x" * 10_000

    def test_trim_history_adds_summary_when_dropping(self, hass) -> None:
        """A condensed summary is folded into the first kept user message."""
        client = _make_client(hass)
        client._provider = create_provider("ollama", hass)  # smallest budget
        messages = [{"role": "user", "content": "x" * 10_000} for _ in range(30)]
        trimmed = client._trim_history_to_budget(
            messages, system_prompt="sys", context_prompt="ctx"
        )
        if len(trimmed) < len(messages):
            # Summary is prepended to the first user message, not a separate turn
            first_user = next(m for m in trimmed if m["role"] == "user")
            assert "condensed" in first_user["content"].lower()
            # Must preserve user-first ordering (required by Gemini)
            assert trimmed[0]["role"] == "user"

    def test_trim_history_no_drop_when_within_budget(self, hass) -> None:
        """Small history within budget is returned as-is."""
        client = _make_client(hass)
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        trimmed = client._trim_history_to_budget(
            messages, system_prompt="sys", context_prompt="ctx"
        )
        assert trimmed == messages

    def test_trim_history_drops_leading_assistant(self, hass) -> None:
        """Leading assistant messages are stripped to preserve user-first ordering."""
        client = _make_client(hass)
        client._provider = create_provider("ollama", hass)
        # Simulate a trim that keeps an assistant reply but drops its user message:
        # large user msg (won't fit) followed by small assistant + small user + small assistant
        messages = [
            {"role": "user", "content": "x" * 100_000},  # too large to keep
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "follow-up"},
            {"role": "assistant", "content": "answer"},
        ]
        trimmed = client._trim_history_to_budget(
            messages, system_prompt="sys", context_prompt="ctx"
        )
        assert trimmed, "Should keep at least some messages"
        assert trimmed[0]["role"] == "user", "Must start with a user message"

    def test_build_history_messages_handles_none(self) -> None:
        """None history returns empty list."""
        from custom_components.selora_ai.llm_client import LLMClient

        assert LLMClient._build_history_messages(None) == []
