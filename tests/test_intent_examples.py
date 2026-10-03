"""The word "example" only makes a message a meta-question when it asks for one.

Blueprint tasks are markdown with an "## Example use cases" heading, and
requests carry asides like "for example, at sunset". Matching the bare
word routed every one of them to the answer specialist, which describes
an automation instead of writing it.

``fixtures/blueprint_tasks/`` holds the four blueprint task descriptions
of the automations dataset in allenporter/home-assistant-datasets
(``datasets/automations/<task>/DESCRIPTION.md`` at
c422946344b31406f5336e63a865e2d587918e33), verbatim.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.selora_ai.llm_client.intent import _classify_chat_intent

_TASKS = Path(__file__).parent / "fixtures" / "blueprint_tasks"


@pytest.mark.parametrize(
    "task", ["door_left_open", "humidity_fan", "light_on_door", "vacuum_pause"]
)
def test_blueprint_task_routes_to_automation(task: str) -> None:
    text = (_TASKS / f"{task}.md").read_text(encoding="utf-8")
    assert "## Example use cases" in text
    assert _classify_chat_intent(text) == "automation"


def test_example_heading_does_not_flip_a_task() -> None:
    task = (
        "# Light on door\n\n## Problem statement\n\n"
        "Create an blueprint automation to turn on a light when the door opens.\n"
    )
    assert _classify_chat_intent(task) == "automation"
    assert _classify_chat_intent(task + "\n## Example use cases\n") == "automation"


@pytest.mark.parametrize(
    "message",
    [
        "Create an automation, for example turn on the porch light at sunset",
        "turn on the porch light at sunset, for example",
        "Create a blueprint automation to pause the vacuum when I get a call. "
        "Here are some examples of how I'd use it.",
        "here's an example of what I want: turn on the porch light at sunset",
        "## Example automation\nTurn on the porch light at sunset",
        "Create something like an example automation that turns on the porch light at sunset",
        "Here are some examples of use cases. "
        "Create an automation to turn on the porch light at sunset.",
    ],
)
def test_example_aside_does_not_make_a_meta_question(message: str) -> None:
    assert _classify_chat_intent(message) == "automation"


@pytest.mark.parametrize(
    "message",
    [
        "give me some examples of automations",
        "show me examples of what you can do",
        "what examples do you have",
        "any examples of scenes?",
        "examples of automations for a bathroom",
        "do you have examples",
        "examples?",
        "I'd like an example of an automation",
        "an example of a scene",
        "can I see an example automation",
        "example automations for my kitchen",
        "- examples of automations",
        "“Examples of automations”",
        # The create request is the subject of the ask, not a task.
        "I need examples of how to create an automation",
        "What are examples of how to create an automation",
    ],
)
def test_asking_for_examples_stays_answer(message: str) -> None:
    assert _classify_chat_intent(message) == "answer"
