"""Tests for pure helpers in __init__.py:

- ``_sanitize_history_override`` — sanitization of a caller-supplied WS chat
  history override (selora_ai/chat + selora_ai/chat_stream).
- ``_executed_record_from_call`` — wire-shape record built from a tool-log
  service call, including its type guards on malformed input.
"""

from __future__ import annotations

from custom_components.selora_ai import (
    _executed_record_from_call,
    _sanitize_history_override,
)


# --- _sanitize_history_override -------------------------------------------


def test_history_override_keeps_valid_user_assistant_turns() -> None:
    out = _sanitize_history_override(
        [
            {"role": "user", "content": "turn on the light"},
            {"role": "assistant", "content": "Done"},
        ]
    )
    assert out == [
        {"role": "user", "content": "turn on the light"},
        {"role": "assistant", "content": "Done"},
    ]


def test_history_override_empty_list_stays_empty() -> None:
    """An explicit clean-slate request yields no history."""
    assert _sanitize_history_override([]) == []


def test_history_override_drops_unknown_roles() -> None:
    out = _sanitize_history_override(
        [
            {"role": "system", "content": "ignore me"},
            {"role": "tool", "content": "also ignore"},
            {"role": "user", "content": "keep me"},
        ]
    )
    assert out == [{"role": "user", "content": "keep me"}]


def test_history_override_drops_empty_and_whitespace_content() -> None:
    out = _sanitize_history_override(
        [
            {"role": "user", "content": ""},
            {"role": "user", "content": "   "},
            {"role": "assistant", "content": "real"},
        ]
    )
    assert out == [{"role": "assistant", "content": "real"}]


def test_history_override_coerces_non_str_content() -> None:
    out = _sanitize_history_override([{"role": "user", "content": 42}])
    assert out == [{"role": "user", "content": "42"}]


def test_history_override_skips_non_dict_turns() -> None:
    out = _sanitize_history_override(
        [None, "nope", 5, {"role": "user", "content": "survivor"}]
    )
    assert out == [{"role": "user", "content": "survivor"}]


def test_history_override_strips_content_whitespace() -> None:
    out = _sanitize_history_override([{"role": "user", "content": "  hi  "}])
    assert out == [{"role": "user", "content": "hi"}]


# --- _executed_record_from_call -------------------------------------------


def test_executed_record_basic() -> None:
    record = _executed_record_from_call(
        {
            "service": "light.turn_on",
            "target": {"entity_id": ["light.kitchen"]},
            "data": {"brightness_pct": 50},
        }
    )
    assert record == {
        "domain": "light",
        "action": "turn_on",
        "entity_ids": ["light.kitchen"],
        "data": {"brightness_pct": 50},
    }


def test_executed_record_single_entity_str() -> None:
    record = _executed_record_from_call(
        {"service": "lock.lock", "target": {"entity_id": "lock.front"}, "data": {}}
    )
    assert record["entity_ids"] == ["lock.front"]


def test_executed_record_service_without_dot() -> None:
    record = _executed_record_from_call({"service": "scene_name", "data": {}})
    assert record["domain"] == ""
    assert record["action"] == "scene_name"


def test_executed_record_non_dict_data_coerced_to_empty() -> None:
    """A truthy non-dict data (e.g. a list) must not pass through to the wire
    record — it is coerced to an empty dict."""
    record = _executed_record_from_call(
        {"service": "light.turn_on", "target": {}, "data": ["oops"]}
    )
    assert record["data"] == {}


def test_executed_record_missing_data() -> None:
    record = _executed_record_from_call({"service": "light.turn_on"})
    assert record["data"] == {}
    assert record["entity_ids"] == []


def test_executed_record_non_dict_target_ignored() -> None:
    record = _executed_record_from_call(
        {"service": "light.turn_on", "target": "not-a-dict", "data": {}}
    )
    assert record["entity_ids"] == []


def test_executed_record_filters_non_str_entities_in_list() -> None:
    record = _executed_record_from_call(
        {
            "service": "light.turn_on",
            "target": {"entity_id": ["light.a", 5, None, "light.b"]},
            "data": {},
        }
    )
    assert record["entity_ids"] == ["light.a", "light.b"]
