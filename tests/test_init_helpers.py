"""Tests for pure helpers in __init__.py:

- ``_sanitize_history_override`` — sanitization of a caller-supplied WS chat
  history override (selora_ai/chat + selora_ai/chat_stream).
- ``_executed_record_from_call`` — wire-shape record built from a tool-log
  service call, including its type guards on malformed input.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.selora_ai import (
    _entry_is_configurable_llm,
    _executed_record_from_call,
    _find_llm,
    _resolve_llm_entry,
    _sanitize_history_override,
)
from custom_components.selora_ai.const import (
    CONF_AIGATEWAY_REFRESH_TOKEN,
    CONF_ENTRY_TYPE,
    CONF_LLM_PROVIDER,
    DOMAIN,
    ENTRY_TYPE_DEVICE,
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


# --- _entry_is_configurable_llm -------------------------------------------


def test_entry_configurable_with_explicit_provider() -> None:
    assert _entry_is_configurable_llm({CONF_LLM_PROVIDER: "anthropic"}) is True


def test_entry_configurable_with_aigateway_refresh_token() -> None:
    assert _entry_is_configurable_llm({CONF_AIGATEWAY_REFRESH_TOKEN: "aigw_x"}) is True


def test_entry_not_configurable_when_empty() -> None:
    # The stray second entry: no provider, no tokens. Must not be treated
    # as a real LLM entry (would default to empty-cred Selora Cloud).
    assert _entry_is_configurable_llm({}) is False
    assert _entry_is_configurable_llm({CONF_LLM_PROVIDER: ""}) is False
    assert _entry_is_configurable_llm({CONF_AIGATEWAY_REFRESH_TOKEN: ""}) is False


# --- _find_llm ------------------------------------------------------------


def _hass_with_data(data: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(data={DOMAIN: data})


def test_find_llm_prefers_configured_over_unconfigured() -> None:
    unconfigured = SimpleNamespace(is_configured=False)
    configured = SimpleNamespace(is_configured=True)
    # Unconfigured inserted first — picking insertion order would return it.
    hass = _hass_with_data(
        {"e_unconfigured": {"llm": unconfigured}, "e_configured": {"llm": configured}}
    )
    assert _find_llm(hass) is configured


def test_find_llm_falls_back_to_first_when_none_configured() -> None:
    first = SimpleNamespace(is_configured=False)
    second = SimpleNamespace(is_configured=False)
    hass = _hass_with_data({"a": {"llm": first}, "b": {"llm": second}})
    assert _find_llm(hass) is first


def test_find_llm_skips_non_dict_and_missing_llm_entries() -> None:
    configured = SimpleNamespace(is_configured=True)
    hass = _hass_with_data(
        {"_approval_store": "not-a-dict", "no_llm": {}, "real": {"llm": configured}}
    )
    assert _find_llm(hass) is configured


def test_find_llm_returns_none_when_no_clients() -> None:
    assert _find_llm(SimpleNamespace(data={})) is None


# --- _resolve_llm_entry ---------------------------------------------------


def _hass_with_entries(entries: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda _domain: entries)
    )


def test_resolve_llm_entry_prefers_configured_over_stray() -> None:
    stray = SimpleNamespace(data={})  # no provider, no tokens
    real = SimpleNamespace(data={CONF_LLM_PROVIDER: "selora_cloud"})
    # Stray first — insertion order alone would return it.
    hass = _hass_with_entries([stray, real])
    assert _resolve_llm_entry(hass) is real


def test_resolve_llm_entry_skips_device_entries() -> None:
    device = SimpleNamespace(data={CONF_ENTRY_TYPE: ENTRY_TYPE_DEVICE})
    real = SimpleNamespace(data={CONF_LLM_PROVIDER: "anthropic"})
    hass = _hass_with_entries([device, real])
    assert _resolve_llm_entry(hass) is real


def test_resolve_llm_entry_falls_back_to_first_non_device() -> None:
    stray = SimpleNamespace(data={})
    hass = _hass_with_entries([stray])
    assert _resolve_llm_entry(hass) is stray


def test_resolve_llm_entry_none_when_only_device_entries() -> None:
    device = SimpleNamespace(data={CONF_ENTRY_TYPE: ENTRY_TYPE_DEVICE})
    hass = _hass_with_entries([device])
    assert _resolve_llm_entry(hass) is None
