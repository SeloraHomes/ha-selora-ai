"""Strict automation predicate used to gate the cloud spinner sentinel.

The broad ``_classify_chat_intent`` routes one-shot delayed commands
to the automation LoRA too, so it cannot drive the
"```automation" sentinel emitted from the cloud chat path —
delayed commands return a ``delayed_command`` block and would leave
the panel stuck on "Building automation...".
"""

from __future__ import annotations

import pytest

from custom_components.selora_ai.llm_client.intent import _is_definite_automation


@pytest.mark.parametrize(
    "message",
    [
        "Create an automation that turns on the lights at sunset",
        "Schedule the heater every weekday at 7am",
        "Every morning turn on the kitchen lights",
        "When the door opens then turn on the hallway light",
        "Whenever motion is detected, send me a notification",
        "If the temperature drops below 18 turn on the heater",
        "create an automation for the porch light",
        "Automate the bedroom blinds at sunrise",
    ],
)
def test_definite_automation_recurring(message: str) -> None:
    assert _is_definite_automation(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "turn off the light after 5 minutes",
        "turn off the lights in 10 minutes",
        "at 11 PM turn off the porch light",
        "remind me in 10 minutes",
        "remind me to take the trash out",
        "turn on the kitchen light",
        "is the front door locked?",
        "hello",
        "thanks",
        "",
    ],
)
def test_definite_automation_excludes_one_shot(message: str) -> None:
    assert _is_definite_automation(message) is False


@pytest.mark.parametrize(
    "message",
    [
        "What time is sunset?",
        "what time is sunrise today?",
        "Is the temperature higher than 25?",
        "is it warmer than 26 outside?",
        "How much higher than 20 is the bedroom?",
    ],
)
def test_definite_automation_excludes_informational_questions(message: str) -> None:
    """P2 — sun-event / numeric-comparator words inside an informational
    question must NOT fire the cloud automation spinner."""
    assert _is_definite_automation(message) is False


@pytest.mark.parametrize(
    "message",
    [
        "when is sunset?",
        "when is sunrise tomorrow?",
        "while it is still dark?",
    ],
)
def test_definite_automation_excludes_interrogative_when(message: str) -> None:
    """P2 — "when is sunset?" is an interrogative status question, not a
    "when X then Y" automation rule. Must not fire the spinner."""
    assert _is_definite_automation(message) is False


@pytest.mark.parametrize(
    "message",
    [
        "Can you remind me at sunset?",
        "Could you notify me every morning?",
    ],
)
def test_definite_automation_keeps_polite_notification_requests(message: str) -> None:
    """P2 — polite scheduled notify/remind requests keep the spinner so
    the cloud UI matches the classifier routing them to automation."""
    assert _is_definite_automation(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "Turn off the porch light when nobody is on the porch for 10 minutes",
        "turn off the kitchen lights when nobody is home for 15 minutes",
        "turn on the light if someone is in the office for 5 minutes",
    ],
)
def test_definite_automation_fires_for_presence_duration(message: str) -> None:
    """Presence+duration prompts route to the automation specialist in
    the classifier, so the cloud spinner sentinel must agree and fire."""
    assert _is_definite_automation(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "When nobody is home, turn off the lights",
        "if the temperature drops below 18 turn on the heater",
    ],
)
def test_definite_automation_keeps_conditional_rules(message: str) -> None:
    """Conditional connectors (when/if) still fire the sentinel — they
    open a genuine automation rule, not a question."""
    assert _is_definite_automation(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "Can you turn off the porch light every morning?",
        "Could you turn on the lights every night?",
        "Will you turn off the heater at sunset?",
    ],
)
def test_definite_automation_keeps_polite_scheduled_requests(message: str) -> None:
    """P2 — a question opener with a command verb + schedule is an
    automation REQUEST, not an informational query. Keep the sentinel so
    cloud streams show the automation-building UI (matches the
    classifier routing it to automation)."""
    assert _is_definite_automation(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "How do I stop an automation?",
        "Why does this automation keep running?",
        "How do I turn off an automation that I created?",
        "What happens when I disable an automation?",
    ],
)
def test_definite_automation_excludes_instructional_questions(message: str) -> None:
    """P2 — WH-interrogative documentation questions must NOT fire the
    spinner, even though they contain action verbs + the automation
    anchor. The user wants docs, not a proposal."""
    assert _is_definite_automation(message) is False
