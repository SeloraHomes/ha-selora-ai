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
