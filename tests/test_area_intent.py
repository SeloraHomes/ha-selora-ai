"""The area tools must be reachable from the wording people actually use.

Reported from the panel: "Create a new office area" got "I can't create areas
directly with the available controls" — `create_area` has existed since !472,
but the turn never saw it. Two independent failures had to line up: the config
detector missed the phrasing, and `create_area` was in the config lane only.
"""

from __future__ import annotations

import pytest

from custom_components.selora_ai.llm_client.intent import _is_config_request
from custom_components.selora_ai.tool_registry import COMMAND_TOOL_NAMES, CONFIG_TOOL_NAMES


@pytest.mark.parametrize(
    "message",
    [
        # The reported phrasing: the noun does not sit against the article.
        "Create a new office area",
        "create a new upstairs floor",
        "delete the old office area",
        "rename the downstairs guest room",
        "make a new kids room",
        "add a second floor",
        # The forms that already worked, which must keep working.
        "create an area called Office",
        "add an area for the office",
        "list the areas",
    ],
)
def test_area_requests_are_config_requests(message: str) -> None:
    assert _is_config_request(message) is True


@pytest.mark.parametrize(
    "message",
    [
        # A scene request that happens to end in a config noun. Widening the
        # gap too far swallows these, and a false positive here strips
        # execute_command from a turn that meant to act.
        "create a new scene for the living room",
        "make a scene for the bedroom",
        "create an automation for the living room",
        "add a light to the kitchen scene",
        "turn on the lights in the living room",
        "set the living room to 50%",
    ],
)
def test_ordinary_requests_are_not_config_requests(message: str) -> None:
    assert _is_config_request(message) is False


def test_create_area_is_reachable_from_either_lane() -> None:
    """The detector is one regex and it has been wrong here before. Being in
    both lanes means a phrasing it misses costs a slightly larger schema rather
    than the feature — the same reasoning delete_area already carries."""
    assert "create_area" in CONFIG_TOOL_NAMES
    assert "create_area" in COMMAND_TOOL_NAMES
