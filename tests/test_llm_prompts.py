"""Tests for LLM system prompt verbosity constraints.

Verifies that both the JSON-mode and streaming system prompts include
explicit brevity directives so the LLM produces concise chat responses,
and that those directives do not conflict with tool-policy formatting rules.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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


class TestActionOriented:
    """Both prompts must instruct the LLM to act rather than ask for clarification."""

    def test_json_prompt_action_oriented(self, hass) -> None:
        prompt = _make_client(hass)._build_architect_system_prompt()
        assert "ACTION-ORIENTED" in prompt
        assert "resolve ambiguity" in prompt.lower()

    def test_stream_prompt_action_oriented(self, hass) -> None:
        prompt = _make_client(hass)._build_architect_stream_system_prompt()
        assert "ACTION-ORIENTED" in prompt
        assert "resolve ambiguity" in prompt.lower()

    def test_clarification_requires_genuine_ambiguity(self, hass) -> None:
        """Clarification intent should only fire when truly ambiguous."""
        prompt = _make_client(hass)._build_architect_system_prompt()
        assert "genuinely ambiguous" in prompt.lower() or "cannot resolve" in prompt.lower()


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


class TestCommandPolicyEnforcement:
    """Verify command intent handling and execution enforcement (#90)."""

    def test_command_without_calls_downgraded_to_answer(self, hass) -> None:
        """intent=command with no calls should be downgraded to answer."""
        client = _make_client(hass)
        result = client._apply_command_policy(
            {"intent": "command", "response": "Turning on the lights."},
            [{"entity_id": "light.kitchen"}],
        )
        assert result["intent"] == "answer"

    def test_command_with_empty_calls_downgraded(self, hass) -> None:
        """intent=command with calls=[] should also be downgraded."""
        client = _make_client(hass)
        result = client._apply_command_policy(
            {"intent": "command", "response": "Turning on the lights.", "calls": []},
            [{"entity_id": "light.kitchen"}],
        )
        assert result["intent"] == "answer"

    def test_answer_without_calls_unchanged(self, hass) -> None:
        """intent=answer with no calls should pass through unchanged."""
        client = _make_client(hass)
        result = client._apply_command_policy(
            {"intent": "answer", "response": "Here is some info."},
            [{"entity_id": "light.kitchen"}],
        )
        assert result["intent"] == "answer"

    def test_command_prompt_requires_calls(self, hass) -> None:
        """Both system prompts must instruct the LLM to always include calls for commands."""
        json_prompt = _make_client(hass)._build_architect_system_prompt()
        stream_prompt = _make_client(hass)._build_architect_stream_system_prompt()
        for prompt in (json_prompt, stream_prompt):
            assert "non-empty" in prompt.lower() and "calls" in prompt.lower(), (
                "System prompt must instruct LLM to include non-empty calls for commands"
            )

    def test_parse_streamed_response_applies_policy_even_without_calls(self, hass) -> None:
        """parse_streamed_response must run _apply_command_policy even when calls is empty."""
        client = _make_client(hass)
        # Simulate LLM returning command intent with no calls
        text = '{"intent": "command", "response": "I would turn on the lights."}'
        result = client.parse_streamed_response(text, entities=[{"entity_id": "light.kitchen"}])
        # Should be downgraded since there are no calls
        assert result["intent"] == "answer"

    def test_command_no_response_no_calls_not_false_confirmation(self, hass) -> None:
        """Command with no calls and no response must NOT say 'Done' (#94 P2)."""
        client = _make_client(hass)
        text = '{"intent": "command"}'
        result = client.parse_streamed_response(text, entities=[{"entity_id": "light.kitchen"}])
        # Policy downgrades to "answer" — must not say "Done"
        assert result["intent"] == "answer"
        assert "Done" not in result.get("response", "")

    def test_command_without_response_field_gets_confirmation(self, hass) -> None:
        """Command intent missing 'response' must get a human-readable fallback (#94)."""
        client = _make_client(hass)
        text = '{"intent":"command","calls":[{"service":"light.turn_on","target":{"entity_id":"light.living_room_kitchen"}}]}'
        result = client.parse_streamed_response(
            text, entities=[{"entity_id": "light.living_room_kitchen"}]
        )
        assert result["intent"] == "command"
        assert "response" in result
        # Must be human-readable, not raw JSON
        assert "{" not in result["response"]
        assert "living room kitchen" in result["response"]

    def test_command_without_response_field_turn_off(self, hass) -> None:
        """Turn-off command without 'response' also gets a readable fallback (#94)."""
        client = _make_client(hass)
        text = '{"intent":"command","calls":[{"service":"light.turn_off","target":{"entity_id":"light.living_room_table_ronde"}}]}'
        result = client.parse_streamed_response(
            text, entities=[{"entity_id": "light.living_room_table_ronde"}]
        )
        assert result["intent"] == "command"
        assert "{" not in result["response"]
        assert "living room table ronde" in result["response"]

    def test_delayed_command_example_not_executed(self, hass) -> None:
        """An informational delayed_command example mid-text must NOT be parsed as intent."""
        client = _make_client(hass)
        text = (
            'Here is an example of how to schedule a delayed command:\n\n'
            '```delayed_command\n'
            '{"calls": [{"service": "light.turn_on", "target": {"entity_id": "light.porch"}}], '
            '"delay_seconds": 600}\n'
            '```\n\n'
            'You can use this format in your requests.'
        )
        result = client.parse_streamed_response(text, entities=[{"entity_id": "light.porch"}])
        # Must NOT be parsed as a delayed_command — it's an example followed by more text
        assert result["intent"] != "delayed_command"

    def test_delayed_command_terminal_block_is_executed(self, hass) -> None:
        """A terminal delayed_command block must be parsed as an executable intent."""
        client = _make_client(hass)
        text = (
            'Scheduling the porch light.\n\n'
            '```delayed_command\n'
            '{"calls": [{"service": "light.turn_on", "target": {"entity_id": "light.porch"}}], '
            '"delay_seconds": 600}\n'
            '```'
        )
        result = client.parse_streamed_response(text, entities=[{"entity_id": "light.porch"}])
        assert result["intent"] == "delayed_command"
        assert result["delay_seconds"] == 600

    def test_light_turn_on_accepts_brightness_raw_form(self, hass) -> None:
        """`light.turn_on` must accept `brightness` (0-255), not just `brightness_pct`.

        Scenes store brightness as the raw form, so when the LLM expands a
        scene activation into device-level calls every light command would
        otherwise fail the per-domain parameter whitelist.
        """
        client = _make_client(hass)
        text = (
            '{"intent":"command","response":"Activating the scene.",'
            '"calls":[{"service":"light.turn_on",'
            '"target":{"entity_id":"light.living_room"},'
            '"data":{"brightness":180}}]}'
        )
        result = client.parse_streamed_response(
            text, entities=[{"entity_id": "light.living_room"}]
        )
        assert result["intent"] == "command"
        # Call must survive the policy with `brightness` intact.
        assert result["calls"][0]["data"]["brightness"] == 180

    def test_scene_turn_on_is_allowed_directly(self, hass) -> None:
        """`scene.turn_on` is in the policy so the LLM doesn't expand a
        scene into per-light calls (which would then trip light.turn_on's
        parameter whitelist for any non-default brightness/colour)."""
        client = _make_client(hass)
        text = (
            '{"intent":"command","response":"Activating fin film.",'
            '"calls":[{"service":"scene.turn_on",'
            '"target":{"entity_id":"scene.fin_film"}}]}'
        )
        result = client.parse_streamed_response(
            text, entities=[{"entity_id": "scene.fin_film"}]
        )
        assert result["intent"] == "command"
        assert result["calls"][0]["service"] == "scene.turn_on"


class TestUnbackedActionConfirmation:
    """Catch the 'Turning off …' confirmation with no service calls bug."""

    def test_command_with_confirmation_prose_but_no_calls_replaces_response(
        self, hass
    ) -> None:
        """Command intent with a 'Turning off …' response but empty calls
        must be downgraded AND the misleading prose replaced — otherwise the
        user sees a fake success while nothing executed."""
        client = _make_client(hass)
        result = client._apply_command_policy(
            {
                "intent": "command",
                "response": "Turning off Ceiling lights",
                "calls": [],
            },
            [{"entity_id": "light.kitchen"}],
        )
        assert result["intent"] == "answer"
        assert "Turning off" not in result["response"]
        assert "rephrase" in result["response"].lower() or "match" in result["response"].lower()
        assert result.get("validation_error") == "no_matching_entity_for_command"

    def test_streamed_prose_only_action_confirmation_is_replaced(self, hass) -> None:
        """When the LLM streams just 'Turning off' as prose with no command
        block, the parser must surface it as a clarification, not a fake
        confirmation."""
        client = _make_client(hass)
        result = client.parse_streamed_response(
            "Turning off",
            entities=[{"entity_id": "light.ceiling"}],
        )
        assert result["intent"] == "answer"
        assert "Turning off" not in result["response"]

    def test_long_educational_answer_starting_with_turning_is_kept(self, hass) -> None:
        """A long answer that happens to start with 'Turning off' must NOT
        be treated as a fake confirmation — only short responses are flagged."""
        client = _make_client(hass)
        long_response = (
            "Turning off lights at sunset is configured by the automation "
            "you created last week. The trigger fires when the sun.sun entity "
            "transitions below the horizon, and the action calls light.turn_off "
            "on every entity in the living-room area. You can review or edit "
            "the automation under Settings → Automations."
        )
        result = client._apply_command_policy(
            {"intent": "answer", "response": long_response},
            [{"entity_id": "light.living_room"}],
        )
        assert result["response"] == long_response

    def test_legitimate_command_with_calls_keeps_confirmation(self, hass) -> None:
        """A command with valid calls must keep its 'Turning off …' confirmation."""
        client = _make_client(hass)
        result = client._apply_command_policy(
            {
                "intent": "command",
                "response": "Turning off Ceiling lights",
                "calls": [
                    {
                        "service": "light.turn_off",
                        "target": {"entity_id": "light.ceiling"},
                    }
                ],
            },
            [{"entity_id": "light.ceiling"}],
        )
        assert result["intent"] == "command"
        assert result["response"] == "Turning off Ceiling lights"

    def test_short_explanatory_answer_starting_with_action_verb_is_kept(
        self, hass
    ) -> None:
        """A short HELP answer that happens to start with an action verb must
        not be replaced — only confirmations without backing calls should be."""
        client = _make_client(hass)
        explanatory = (
            "Opening a garage door in Home Assistant requires a cover entity "
            "and an integration that supports it."
        )
        result = client._apply_command_policy(
            {"intent": "answer", "response": explanatory},
            [{"entity_id": "cover.garage"}],
        )
        assert result["response"] == explanatory

    def test_command_with_explanatory_response_still_replaced(self, hass) -> None:
        """When intent was 'command' but calls is empty, explanatory markers
        must NOT save the response — the LLM clearly tried to issue a command
        and produced nothing, so the user must be told no action ran."""
        client = _make_client(hass)
        result = client._apply_command_policy(
            {
                "intent": "command",
                "response": "Turning off the lights requires a moment.",
                "calls": [],
            },
            [{"entity_id": "light.kitchen"}],
        )
        assert result["intent"] == "answer"
        assert "Turning off" not in result["response"]
        assert result.get("validation_error") == "no_matching_entity_for_command"

    def test_cover_open_close_are_safe_commands(self, hass) -> None:
        """Garage doors and blinds — `cover.open_cover` / `close_cover`
        / `stop_cover` / `toggle` / `set_cover_position` must pass the
        allowlist. The user types "Open the garage door" and the LLM
        should be able to execute it without hitting "outside the
        current safe command allowlist". Locks and alarms still need
        to be excluded (handled by their absence from the policy)."""
        client = _make_client(hass)
        for service in (
            "cover.open_cover",
            "cover.close_cover",
            "cover.stop_cover",
            "cover.toggle",
        ):
            result = client._apply_command_policy(
                {
                    "intent": "command",
                    "response": "Opening the garage door.",
                    "calls": [
                        {"service": service, "target": {"entity_id": "cover.garage_door"}}
                    ],
                },
                [{"entity_id": "cover.garage_door"}],
            )
            assert result["intent"] == "command", f"{service} got downgraded"
            assert result.get("calls"), f"{service} produced no calls"
            assert result.get("validation_error") is None

    def test_bogus_cover_service_is_repaired_from_prose(self, hass) -> None:
        """The model keeps inventing wrong cover service names
        (`cover.cover`, `cover.garage_door`). When the domain is
        allowed and the LLM's own confirmation prose names the verb
        ("Opening the garage door"), repair the service field instead
        of rejecting the call outright. The hard-coded cheat sheet
        helps but doesn't reach 100% compliance; this is the
        deterministic fallback so the user's request still executes."""
        client = _make_client(hass)
        for bogus_service, prose, expected in [
            ("cover.cover", "Opening the garage door.", "cover.open_cover"),
            ("cover.garage_door", "Closing the garage door.", "cover.close_cover"),
            ("cover.blinds", "Stopping the blinds.", "cover.stop_cover"),
            ("cover.gate", "Toggling the gate.", "cover.toggle"),
            ("light.kitchen", "Turning on the kitchen lights.", "light.turn_on"),
        ]:
            entity = {
                "cover.cover": "cover.garage_door",
                "cover.garage_door": "cover.garage_door",
                "cover.blinds": "cover.blinds",
                "cover.gate": "cover.gate",
                "light.kitchen": "light.kitchen",
            }[bogus_service]
            result = client._apply_command_policy(
                {
                    "intent": "command",
                    "response": prose,
                    "calls": [
                        {"service": bogus_service, "target": {"entity_id": entity}}
                    ],
                },
                [{"entity_id": entity}],
            )
            assert result["intent"] == "command", f"{bogus_service} got rejected"
            calls = result.get("calls") or []
            assert calls and calls[0]["service"] == expected, (
                f"{bogus_service} → expected {expected}, got {calls[0]['service']}"
            )

    def test_unrepairable_bogus_service_still_rejected(self, hass) -> None:
        """If the prose gives no verb hint, fall back to rejection so
        we don't silently execute a randomly-chosen verb."""
        client = _make_client(hass)
        result = client._apply_command_policy(
            {
                "intent": "command",
                "response": "Doing something with the garage door.",  # no verb
                "calls": [
                    {"service": "cover.cover", "target": {"entity_id": "cover.garage_door"}}
                ],
            },
            [{"entity_id": "cover.garage_door"}],
        )
        assert result["intent"] == "answer"
        assert "not a valid cover service" in result["response"].lower()

    def test_bad_service_name_gets_actionable_error(self, hass) -> None:
        """When the LLM emits a bogus service name AND the prose
        gives no verb hint (so the repair pass can't recover), the
        error must say "this is not a valid cover service" and
        enumerate the real verbs, not the generic "outside the
        current safe command allowlist" — the LLM can read the
        suggestion and self-correct next turn, and the user sees
        what actually went wrong."""
        client = _make_client(hass)
        result = client._apply_command_policy(
            {
                "intent": "command",
                "response": "Garage door request received.",  # no verb
                "calls": [
                    {
                        "service": "cover.garage_door",
                        "target": {"entity_id": "cover.garage_door"},
                    }
                ],
            },
            [{"entity_id": "cover.garage_door"}],
        )
        assert result["intent"] == "answer"
        msg = result["response"].lower()
        assert "not a valid cover service" in msg
        assert "open_cover" in msg
        assert "close_cover" in msg

    def test_lock_and_alarm_remain_blocked(self, hass) -> None:
        """Locks and alarms intentionally stay outside the allowlist —
        they're higher-risk and warrant a separate config gate."""
        client = _make_client(hass)
        for service in ("lock.unlock", "alarm_control_panel.alarm_disarm"):
            result = client._apply_command_policy(
                {
                    "intent": "command",
                    "response": "Unlocking the door.",
                    "calls": [
                        {"service": service, "target": {"entity_id": f"{service.split('.')[0]}.front"}}
                    ],
                },
                [{"entity_id": f"{service.split('.')[0]}.front"}],
            )
            assert result["intent"] == "answer", f"{service} unexpectedly allowed"

    def test_standalone_done_is_replaced(self, hass) -> None:
        """A bare 'Done' confirmation with no trailing punctuation must still
        be caught — the original `done[.,!\\s]` matcher missed this case."""
        client = _make_client(hass)
        result = client._apply_command_policy(
            {"intent": "command", "response": "Done", "calls": []},
            [{"entity_id": "light.kitchen"}],
        )
        assert result["intent"] == "answer"
        assert result["response"] != "Done"
        assert result.get("validation_error") == "no_matching_entity_for_command"

    def test_prompt_forbids_confirmation_without_calls_json(self, hass) -> None:
        """JSON-mode prompt explicitly bans confirmations without calls."""
        prompt = _make_client(hass)._build_architect_system_prompt()
        # Look for the new rule about typos and unmatched entities.
        assert "typo" in prompt.lower()
        assert "fabricate" in prompt.lower() or "clarification" in prompt.lower()

    def test_prompt_forbids_confirmation_without_calls_stream(self, hass) -> None:
        """Streaming prompt explicitly bans confirmations without command block."""
        prompt = _make_client(hass)._build_architect_stream_system_prompt()
        assert "typo" in prompt.lower()
        # Must mention that prose without a command block is a bug.
        assert "without a corresponding command block" in prompt.lower() or (
            "command block" in prompt.lower() and "bug" in prompt.lower()
        )


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

        history = [
            {"role": "user", "content": f"msg-{i}"} for i in range(_MAX_HISTORY_TURNS + 20)
        ]
        result = LLMClient._build_history_messages(history)
        assert len(result) == _MAX_HISTORY_TURNS
        assert result[0]["content"] == f"msg-20"  # oldest 20 dropped

    def test_trim_history_drops_oldest_first(self, hass) -> None:
        """When messages exceed the token budget, oldest turns are dropped."""
        client = _make_client(hass)
        # Force the smallest budget by using a real OllamaProvider instance
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


class TestChatCommandExecution:
    """Verify that the websocket chat handler actually calls hass.services.async_call (#90)."""

    @staticmethod
    def _get_inner_handler(decorated_fn):
        """Unwrap websocket decorators to get the raw async handler."""
        fn = decorated_fn
        while hasattr(fn, "__wrapped__"):
            fn = fn.__wrapped__
        return fn

    @pytest.mark.asyncio
    async def test_chat_handler_executes_command_calls(self, hass) -> None:
        """_handle_websocket_chat calls hass.services.async_call for command intents."""
        from custom_components.selora_ai import _handle_websocket_chat
        from custom_components.selora_ai.const import DOMAIN

        # Track calls made to the light.turn_on service
        service_calls: list[dict] = []

        async def _track_call(call) -> None:
            service_calls.append(
                {"domain": "light", "service": "turn_on", "data": dict(call.data)}
            )

        hass.services.async_register("light", "turn_on", _track_call)

        mock_llm = AsyncMock()
        mock_llm.architect_chat = AsyncMock(
            return_value={
                "intent": "command",
                "response": "Turning on the kitchen light.",
                "calls": [
                    {
                        "service": "light.turn_on",
                        "target": {"entity_id": "light.kitchen"},
                        "data": {},
                    }
                ],
            }
        )

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN]["test_entry"] = {"llm": mock_llm}

        connection = MagicMock()
        connection.user = MagicMock(is_admin=True)

        msg = {"id": 1, "type": "selora_ai/chat", "message": "Turn on the kitchen light"}

        handler = self._get_inner_handler(_handle_websocket_chat)
        await handler(hass, connection, msg)

        assert len(service_calls) == 1
        assert service_calls[0]["data"]["entity_id"] == "light.kitchen"

        result = connection.send_result.call_args[0][1]
        assert "light.turn_on" in result["executed"]

    @pytest.mark.asyncio
    async def test_chat_handler_reports_service_not_found(self, hass) -> None:
        """Failed service calls are reported in the response, not swallowed."""
        from custom_components.selora_ai import _handle_websocket_chat
        from custom_components.selora_ai.const import DOMAIN

        mock_llm = AsyncMock()
        mock_llm.architect_chat = AsyncMock(
            return_value={
                "intent": "command",
                "response": "Running bogus service.",
                "calls": [
                    {"service": "bogus.nonexistent", "target": {}, "data": {}},
                ],
            }
        )

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN]["test_entry"] = {"llm": mock_llm}

        connection = MagicMock()
        connection.user = MagicMock(is_admin=True)

        msg = {"id": 1, "type": "selora_ai/chat", "message": "Do something bogus"}

        handler = self._get_inner_handler(_handle_websocket_chat)
        await handler(hass, connection, msg)

        result = connection.send_result.call_args[0][1]
        assert result["executed"] == []
        assert "Failed" in result["response"]


class TestBuildCommandConfirmation:
    """Unit tests for _build_command_confirmation (#94)."""

    def test_single_call(self) -> None:
        from custom_components.selora_ai.llm_client import _build_command_confirmation

        calls = [{"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}}]
        result = _build_command_confirmation(calls)
        assert "kitchen" in result
        assert "light turn on" in result
        assert result.startswith("Done")

    def test_multiple_calls(self) -> None:
        from custom_components.selora_ai.llm_client import _build_command_confirmation

        calls = [
            {"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}},
            {"service": "light.turn_off", "target": {"entity_id": "light.bedroom"}},
        ]
        result = _build_command_confirmation(calls)
        assert "kitchen" in result
        assert "bedroom" in result

    def test_empty_calls(self) -> None:
        from custom_components.selora_ai.llm_client import _build_command_confirmation

        assert _build_command_confirmation([]) == "Done."

    def test_multiple_entities_in_one_call(self) -> None:
        from custom_components.selora_ai.llm_client import _build_command_confirmation

        calls = [
            {
                "service": "light.turn_on",
                "target": {"entity_id": ["light.kitchen", "light.bedroom"]},
            }
        ]
        result = _build_command_confirmation(calls)
        assert "kitchen" in result
        assert "bedroom" in result
