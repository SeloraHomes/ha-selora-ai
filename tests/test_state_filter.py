"""Tests for the deterministic state-filter ground-truth (cloud path)."""

from __future__ import annotations

import pytest

from custom_components.selora_ai.llm_client.state_filter import (
    detect_state_filter,
    ground_truth_block,
    matching_entity_ids,
)

_ENTITIES = [
    {"entity_id": "light.kitchen", "state": "on"},
    {"entity_id": "light.bed", "state": "off"},
    {"entity_id": "light.ceiling", "state": "on"},
    {"entity_id": "fan.living", "state": "off"},
    {"entity_id": "switch.deco", "state": "on"},
    {"entity_id": "light.broken", "state": "unavailable"},
]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Quelles lumières sont allumées?", ("light", "on")),
        ("What lights are on?", ("light", "on")),
        ("quelles lumières sont éteintes?", ("light", "off")),
        ("welche lichter sind an", ("light", "on")),
        ("quali luci sono accese", ("light", "on")),
        ("¿qué luces están encendidas?", ("light", "on")),
        ("what fans are on", ("fan", "on")),
        ("which covers are open", ("cover", "open")),
    ],
)
def test_detects_status_questions(message: str, expected: tuple[str, str]) -> None:
    assert detect_state_filter(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        "éteins les lumières",  # fr command — no interrogative
        "allume la lumière",  # fr command
        "turn off the lights",  # en command
        "what time is it",  # no category
        "quelle heure est-il",  # interrogative but no category
        "",
    ],
)
def test_ignores_commands_and_non_status(message: str) -> None:
    assert detect_state_filter(message) is None


def test_matching_excludes_other_domains_and_dead_states() -> None:
    # lights on → kitchen + ceiling only (not the fan, not the switch,
    # not the unavailable light).
    assert matching_entity_ids(_ENTITIES, "light", "on") == [
        "light.ceiling",
        "light.kitchen",
    ]
    # lights off → bed only (fan.off is a different domain).
    assert matching_entity_ids(_ENTITIES, "light", "off") == ["light.bed"]


def test_ground_truth_block_pins_set_and_count() -> None:
    block = ground_truth_block(_ENTITIES, "quelles lumières sont allumées ?")
    assert block is not None
    assert "Exactly 2 light entities are 'on'" in block
    assert "light.kitchen" in block and "light.ceiling" in block
    assert "fan.living" not in block and "switch.deco" not in block
    assert "MUST be 2" in block


def test_ground_truth_block_singular_grammar() -> None:
    block = ground_truth_block(_ENTITIES, "quelles lumières sont éteintes ?")
    assert block is not None
    assert "Exactly 1 light entity is 'off'" in block
    assert "this 1 entity_id" in block


def test_ground_truth_block_none_for_command() -> None:
    assert ground_truth_block(_ENTITIES, "turn off the lights") is None


@pytest.mark.parametrize(
    "message",
    [
        "¿qué persianas están cerrado?",  # es masc singular
        "¿qué persianas están cerrados?",  # es masc plural
        "¿qué persianas están cerradas?",  # es fem plural
        "quali tapparelle sono chiusi?",  # it masc plural
        "quali tapparelle sono chiuse?",  # it fem plural
    ],
)
def test_detect_state_filter_es_it_closed_forms(message: str) -> None:
    # Gender/number variants of "closed" must all engage the deterministic
    # path; missing forms silently hand filtering back to the LLM, which is
    # exactly the wrong-count bug this module exists to prevent.
    assert detect_state_filter(message) == ("cover", "closed")


def test_detect_state_filter_picks_first_mentioned_domain() -> None:
    # A two-category question must resolve deterministically to the
    # first-mentioned domain, not an arbitrary set-iteration order.
    assert detect_state_filter("which covers and lights are open?") == ("cover", "open")
    assert detect_state_filter("which lights and covers are on?") == ("light", "on")
