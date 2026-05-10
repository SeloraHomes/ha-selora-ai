"""Tests for the Selora AI Local Qwen 1.5B output-repair pipeline.

Mirrors the drift modes the management-host LoRA router used to defend
against; covers them now that the repair runs inline in the provider.
"""

from __future__ import annotations

import json

import pytest

from custom_components.selora_ai.providers._qwen_repair import (
    coerce_to_answer,
    extract_first_balanced_json_object,
    extract_time_from_prose,
    normalize_automation_block,
    normalize_response_content,
    repair_json_string_controls,
)

# ── extract_first_balanced_json_object ──────────────────────────────


def test_balanced_extract_clean() -> None:
    assert extract_first_balanced_json_object('{"a":1}') == '{"a":1}'


def test_balanced_extract_strips_trailing_brace() -> None:
    # Naive rfind('}') would have included the extra brace.
    assert extract_first_balanced_json_object('{"a":1}}') == '{"a":1}'


def test_balanced_extract_strips_trailing_prose() -> None:
    assert extract_first_balanced_json_object('{"a":1} and more text') == '{"a":1}'


def test_balanced_extract_braces_inside_strings_dont_count() -> None:
    payload = '{"alias":"if {x} then {y}","b":2}'
    assert extract_first_balanced_json_object(payload) == payload


def test_balanced_extract_no_open_brace() -> None:
    assert extract_first_balanced_json_object("not json at all") is None


# ── repair_json_string_controls ─────────────────────────────────────


def test_repair_unquoted_keys() -> None:
    repaired = repair_json_string_controls('{alias: "x", action: "y"}')
    assert json.loads(repaired) == {"alias": "x", "action": "y"}


def test_repair_single_quoted_strings() -> None:
    repaired = repair_json_string_controls("{'trigger': 'time'}")
    assert json.loads(repaired) == {"trigger": "time"}


def test_repair_trailing_commas_object_and_array() -> None:
    repaired = repair_json_string_controls('{"a": [1, 2,], "b": 3,}')
    assert json.loads(repaired) == {"a": [1, 2], "b": 3}


def test_repair_control_chars_in_string_value() -> None:
    # Real newline char inside a string value — invalid JSON until escaped.
    raw = '{"target":"line1\nline2"}'
    repaired = repair_json_string_controls(raw)
    assert json.loads(repaired) == {"target": "line1\nline2"}


def test_repair_preserves_reserved_literals() -> None:
    # `true`, `false`, `null` must NOT get quoted as keys.
    repaired = repair_json_string_controls('{"a": true, "b": false, "c": null}')
    assert json.loads(repaired) == {"a": True, "b": False, "c": None}


# ── extract_time_from_prose ─────────────────────────────────────────


@pytest.mark.parametrize(
    "prose,expected",
    [
        ("send a notification at 6:30 AM", "06:30:00"),
        ("at 14:00", "14:00:00"),
        ("at 12:00 PM", "12:00:00"),  # noon
        ("at 12:00 AM", "00:00:00"),  # midnight
        ("at 1:05 pm", "13:05:00"),
        ("no time here", None),
        ("", None),
        ("at 25:00", None),  # invalid hour
        ("at 12:99", None),  # invalid minute
    ],
)
def test_extract_time(prose: str, expected: str | None) -> None:
    assert extract_time_from_prose(prose) == expected


# ── coerce_to_answer ─────────────────────────────────────────────────


def test_coerce_extracts_response_field() -> None:
    out = coerce_to_answer('{"intent":"suggestion","response":"hello there"}')
    assert out == {"intent": "answer", "response": "hello there"}


def test_coerce_handles_unparseable() -> None:
    out = coerce_to_answer("nonsense")
    assert out["intent"] == "answer"
    assert isinstance(out["response"], str) and out["response"]


def test_coerce_handles_empty() -> None:
    out = coerce_to_answer("")
    assert out["intent"] == "answer"
    assert out["response"]  # non-empty fallback


# ── normalize_automation_block ──────────────────────────────────────


def test_automation_singular_keys_to_plural() -> None:
    body: dict = {
        "intent": "automation",
        "automation": {
            "trigger": {"trigger": "time", "at": "06:30:00"},
            "action": {"service": "light.turn_on"},
        },
    }
    normalize_automation_block(body)
    auto = body["automation"]
    assert auto["triggers"] == [{"trigger": "time", "at": "06:30:00"}]
    assert auto["actions"] == [{"service": "light.turn_on"}]
    assert auto["conditions"] == []


def test_automation_top_level_keys_migrated_into_block() -> None:
    body: dict = {
        "intent": "automation",
        "triggers": [{"trigger": "time", "at": "06:30:00"}],
    }
    normalize_automation_block(body)
    assert body["automation"]["triggers"] == [{"trigger": "time", "at": "06:30:00"}]
    assert "triggers" not in body  # moved into automation block


def test_automation_alias_synthesized_from_description() -> None:
    body: dict = {
        "intent": "automation",
        "description": "Wake-up lights at 6:30 AM",
        "automation": {"triggers": []},
    }
    normalize_automation_block(body)
    assert body["automation"]["alias"] == "Wake-up lights at 6:30"


def test_automation_alias_default_when_no_description() -> None:
    body: dict = {"intent": "automation", "automation": {"triggers": []}}
    normalize_automation_block(body)
    assert body["automation"]["alias"] == "Automation"


def test_automation_platform_to_trigger_migration() -> None:
    body: dict = {
        "intent": "automation",
        "automation": {"triggers": [{"platform": "time", "at": "06:30:00"}]},
    }
    normalize_automation_block(body)
    trig = body["automation"]["triggers"][0]
    assert trig.get("trigger") == "time"
    assert "platform" not in trig


def test_automation_time_extracted_from_description_prose() -> None:
    body: dict = {
        "intent": "automation",
        "description": "Notify me at 6:30 AM",
        "automation": {"triggers": [{"trigger": "time"}]},
    }
    normalize_automation_block(body)
    assert body["automation"]["triggers"][0]["at"] == "06:30:00"


def test_automation_time_at_padded_to_seconds() -> None:
    body: dict = {
        "intent": "automation",
        "automation": {"triggers": [{"trigger": "time", "at": "06:30"}]},
    }
    normalize_automation_block(body)
    assert body["automation"]["triggers"][0]["at"] == "06:30:00"


def test_automation_target_string_normalized_to_dict_with_entity_id_list() -> None:
    body: dict = {
        "intent": "automation",
        "automation": {
            "triggers": [{"trigger": "time", "at": "06:30:00"}],
            "actions": [{"service": "light.turn_on", "target": "light.kitchen"}],
        },
    }
    normalize_automation_block(body)
    action = body["automation"]["actions"][0]
    assert action["target"] == {"entity_id": ["light.kitchen"]}


def test_automation_target_placeholder_brackets_dropped() -> None:
    body: dict = {
        "intent": "automation",
        "automation": {
            "triggers": [{"trigger": "time", "at": "06:30:00"}],
            "actions": [
                {
                    "service": "light.turn_on",
                    "target": {"entity_id": "<light.placeholder>"},
                }
            ],
        },
    }
    normalize_automation_block(body)
    action = body["automation"]["actions"][0]
    assert action["target"]["entity_id"] == []


def test_automation_spillover_action_in_triggers_array_recovered() -> None:
    # Model jams an action item into the triggers array.
    body: dict = {
        "intent": "automation",
        "automation": {
            "triggers": [
                {"trigger": "time", "at": "06:30:00"},
                {"action": "light.turn_on", "service": "light.turn_on"},
            ],
        },
    }
    normalize_automation_block(body)
    auto = body["automation"]
    assert len(auto["triggers"]) == 1
    assert auto["triggers"][0].get("trigger") == "time"
    assert len(auto["actions"]) == 1


# ── normalize_response_content (end-to-end) ──────────────────────────


def test_pipeline_clean_command_passes_through() -> None:
    raw = '{"intent":"command","response":"OK"}'
    out = json.loads(normalize_response_content(raw))
    assert out == {"intent": "command", "response": "OK"}


def test_pipeline_strips_markdown_fences() -> None:
    raw = '```json\n{"intent":"answer","response":"hi"}\n```'
    out = json.loads(normalize_response_content(raw))
    assert out == {"intent": "answer", "response": "hi"}


def test_pipeline_unknown_intent_coerced_to_answer() -> None:
    raw = '{"intent":"suggestion","response":"maybe try X"}'
    out = json.loads(normalize_response_content(raw))
    assert out["intent"] == "answer"
    assert out["response"] == "maybe try X"


def test_pipeline_unparseable_garbage_yields_answer() -> None:
    raw = "this is not json"
    out = json.loads(normalize_response_content(raw))
    assert out["intent"] == "answer"
    assert isinstance(out["response"], str)


def test_pipeline_repairs_single_quoted_automation() -> None:
    raw = "{'intent': 'automation', 'automation': {'trigger': {'trigger': 'time', 'at': '06:30:00'}, 'action': {'service': 'light.turn_on'}}}"
    out = json.loads(normalize_response_content(raw))
    assert out["intent"] == "automation"
    assert out["automation"]["triggers"] == [{"trigger": "time", "at": "06:30:00"}]


def test_pipeline_capitalized_intent_lowercased() -> None:
    raw = '{"intent":"Command","response":"OK"}'
    out = json.loads(normalize_response_content(raw))
    assert out["intent"] == "command"


def test_pipeline_extra_trailing_brace_recovered() -> None:
    raw = '{"intent":"answer","response":"hi"}}'
    out = json.loads(normalize_response_content(raw))
    assert out == {"intent": "answer", "response": "hi"}


def test_pipeline_idempotent_on_clean_output() -> None:
    raw = '{"intent":"answer","response":"hello"}'
    once = normalize_response_content(raw)
    twice = normalize_response_content(once)
    assert json.loads(once) == json.loads(twice)
