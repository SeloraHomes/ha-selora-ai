"""Tests for v3-added intent-classifier helpers.

Covers helpers in `llm_client/intent.py` that route low-context
chat traffic to the right LoRA specialist BEFORE the model runs.

- `_is_vague_automation` — bare "make me an automation" routes to clarification
- `_VAGUE_BARE_REQUEST` — opener pattern for the above
- `_DESTRUCTIVE_SYSTEM_REQUEST` — "shut down home assistant" / "factory reset"
- `_META_HELP` — bare "help" / "what can you do"
- `_AUTOMATION_PATTERNS` — temporal / sun-event / conditional anchors
- `_classify_chat_intent` — the routing function with v3 changes
  (sun events → automation, polite imperatives → command, destructive → clarification)
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN202

import pytest

from custom_components.selora_ai.llm_client.intent import (
    _AUTOMATION_PATTERNS,
    _DESTRUCTIVE_SYSTEM_REQUEST,
    _META_HELP,
    _VAGUE_BARE_REQUEST,
    _classify_chat_intent,
    _is_vague_automation,
)


class TestIsVagueAutomation:
    """Detect bare/anchorless 'make me an automation' requests."""

    @pytest.mark.parametrize(
        "msg",
        [
            "create an automation for me",
            "automate my house",
            "make something useful",
            "suggest an automation",
        ],
    )
    def test_recognises_vague_bare_requests(self, msg: str) -> None:
        assert _is_vague_automation(msg) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "create an automation that turns on the kitchen light at 7am",
            "automate the coffee maker every weekday at 6:30 AM",
            "turn on the bedroom light when motion is detected",
            "every morning at 6am turn on the porch light",
        ],
    )
    def test_concrete_automation_requests_pass_through(self, msg: str) -> None:
        # Anchors present (time, named device, etc.) — not vague.
        assert _is_vague_automation(msg) is False


class TestDestructiveSystemRequest:
    """The destructive-request blocklist (no command-LoRA fake confirmations)."""

    @pytest.mark.parametrize(
        "msg",
        [
            "shut down home assistant",
            "shutdown the system",
            "reboot home assistant",
            "restart the system",
            "factory reset",
            "delete all my automations",
            "remove all scenes",
            "wipe everything",
        ],
    )
    def test_destructive_phrases_match(self, msg: str) -> None:
        assert _DESTRUCTIVE_SYSTEM_REQUEST.search(msg) is not None

    @pytest.mark.parametrize(
        "msg",
        [
            "turn off the kitchen light",
            "dim the bedroom light",
            "close the garage door",
            "stop the music",
        ],
    )
    def test_normal_off_phrasing_does_not_match(self, msg: str) -> None:
        assert _DESTRUCTIVE_SYSTEM_REQUEST.search(msg) is None


class TestMetaHelp:
    """Bare 'help' / 'what can you do' route to answer specialist."""

    @pytest.mark.parametrize(
        "msg",
        [
            "help",
            "help me",
            "what can you do?",
            "what do you do",
        ],
    )
    def test_recognises_help_phrasings(self, msg: str) -> None:
        assert _META_HELP.match(msg.lower()) is not None


class TestVagueBareRequest:
    """The opener pattern used by ``_is_vague_automation``."""

    @pytest.mark.parametrize(
        "msg",
        [
            "suggest an automation",
            "automate my house",
            "make something useful",
        ],
    )
    def test_matches_known_openers(self, msg: str) -> None:
        assert _VAGUE_BARE_REQUEST.search(msg.lower()) is not None


class TestAutomationPatterns:
    """The anchor list — what counts as 'automation' for the classifier."""

    @pytest.mark.parametrize(
        "msg",
        [
            "every morning turn on the lights",
            "every weekday at 6am start the coffee maker",
            "remind me to take out the trash",
            "when the temperature drops below 18 turn up the thermostat",
            "if it gets warmer than 26 turn on the bedroom fan",
            "turn on the kitchen light at sunset",
            "close the garage at sundown",
            "wake me up at sunrise",
        ],
    )
    def test_matches_automation_phrasings(self, msg: str) -> None:
        msg_l = msg.lower()
        assert any(p.search(msg_l) for p in _AUTOMATION_PATTERNS)


class TestClassifyChatIntentRouting:
    """End-to-end pre-classifier routing — v3 behaviours plus main's pre-existing ones."""

    @pytest.mark.parametrize(
        ("msg", "expected"),
        [
            # Vague automation → clarification (v3)
            ("make me an automation", "clarification"),
            ("automate my house", "clarification"),
            ("suggest an automation", "clarification"),
            ("create an automation for me", "clarification"),
            # Concrete automation with time anchor → automation
            ("turn on the kitchen light every morning at 7am", "automation"),
            # Sun events → automation (v3 — was misrouting to command)
            ("turn on the porch light at sunset", "automation"),
            ("close the garage door at sundown", "automation"),
            # Numeric-state conditions → automation (v3)
            ("when the temperature drops below 18 turn on the heater", "automation"),
            # Destructive → clarification (v3 safety route)
            ("shut down home assistant", "clarification"),
            ("delete all my automations", "clarification"),
            ("factory reset the system", "clarification"),
            # Polite imperatives → command
            ("can you turn off the kitchen light", "command"),
            # Bare help → answer
            ("help", "answer"),
            # Bare command → command (unchanged)
            ("turn off the bedroom light", "command"),
        ],
    )
    def test_routes_to_expected_specialist(self, msg: str, expected: str) -> None:
        assert _classify_chat_intent(msg) == expected
