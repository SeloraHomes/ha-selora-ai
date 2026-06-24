"""Tests for the parametric-call safety-net repairs run on raw LLM output
before policy validation: ``_normalize_parametric_calls`` (bogus light
brightness verbs + missing climate setpoint) and the brightness/temperature
prose extractors, plus the regex false-positive guards.

Also covers ``_executed_record_from_call``'s type guards on the
synthesized-from-tool-log path.
"""

from __future__ import annotations

import pytest

from custom_components.selora_ai.llm_client.command_policy import (
    _extract_brightness_pct,
    _normalize_parametric_calls,
    _repair_service_name,
)


# --- Repair 1: bogus light brightness verbs -------------------------------


@pytest.mark.parametrize(
    "verb",
    [
        "set_brightness",
        "set_brightness_pct",
        "set_percentage",
        "brightness_set",
        "brightness",
        "dim",
        "brighten",
    ],
)
def test_bogus_light_verb_rewritten_to_turn_on(verb: str) -> None:
    """Every invented light verb with an extractable level becomes
    light.turn_on carrying brightness_pct."""
    calls = [
        {
            "service": f"light.{verb}",
            "target": {"entity_id": ["light.kitchen"]},
            "data": {"brightness_pct": 40},
        }
    ]
    _normalize_parametric_calls(calls, "")
    assert calls[0]["service"] == "light.turn_on"
    assert calls[0]["data"]["brightness_pct"] == 40
    # Target untouched.
    assert calls[0]["target"] == {"entity_id": ["light.kitchen"]}


def test_bogus_verb_level_from_prose() -> None:
    """When data carries no level, a brightness-anchored number in the
    response prose is used."""
    calls = [{"service": "light.dim", "data": {}}]
    _normalize_parametric_calls(calls, "Setting the brightness to 30")
    assert calls[0]["service"] == "light.turn_on"
    assert calls[0]["data"]["brightness_pct"] == 30


def test_bogus_verb_no_level_left_untouched() -> None:
    """No extractable level → do NOT default to 100; leave the bogus verb
    so the validation loop rejects it and the user is asked to clarify."""
    calls = [{"service": "light.dim", "data": {}}]
    _normalize_parametric_calls(calls, "Dimming the light")
    assert calls[0]["service"] == "light.dim"


def test_bogus_verb_zero_pct_becomes_turn_off() -> None:
    """An explicit 0% is an off request, not turn_on at 1%."""
    calls = [{"service": "light.set_brightness", "data": {"brightness_pct": 0}}]
    _normalize_parametric_calls(calls, "")
    assert calls[0]["service"] == "light.turn_off"
    assert "brightness_pct" not in calls[0]["data"]


def test_bogus_verb_preserves_unrelated_data_keys() -> None:
    """Only the brightness-family keys are stripped; other valid params
    (e.g. transition) survive the rewrite."""
    calls = [
        {
            "service": "light.dim",
            "data": {"brightness": 128, "transition": 2},
        }
    ]
    _normalize_parametric_calls(calls, "")
    assert calls[0]["service"] == "light.turn_on"
    assert calls[0]["data"]["transition"] == 2
    assert "brightness" not in calls[0]["data"]
    # 128/255 ≈ 50%
    assert calls[0]["data"]["brightness_pct"] == 50


def test_turn_off_with_stray_brightness_not_rewritten() -> None:
    """A real turn_off carrying a stray brightness key is left alone — the
    verb is the user's explicit intent."""
    calls = [{"service": "light.turn_off", "data": {"brightness": 50}}]
    _normalize_parametric_calls(calls, "Brightness to 50")
    assert calls[0]["service"] == "light.turn_off"


# --- Brightness extractor + regex false-positive guards -------------------


@pytest.mark.parametrize(
    ("prose", "expected"),
    [
        ("Brightness 50%", 50),
        ("Set to 75 percent", 75),
        ("brightness to 30", 30),
        ("level to 80", 80),
        ("all the way", 100),
        ("set it to full", 100),
        ("maximum brightness", 100),
    ],
)
def test_extract_brightness_from_prose(prose: str, expected: int) -> None:
    assert _extract_brightness_pct({}, prose) == expected


@pytest.mark.parametrize(
    "prose",
    [
        "going to 50 stores today",
        "moved the file to 90 other folders",
        "added it to 12 lists",
    ],
)
def test_bare_to_n_in_prose_not_a_level(prose: str) -> None:
    """The 'to N' alternative is anchored on brightness/level context, so an
    unrelated 'to N' in prose must not be read as a brightness percentage."""
    assert _extract_brightness_pct({}, prose) is None


def test_explicit_pct_wins_over_earlier_level_phrase() -> None:
    """re.search returns the leftmost match, but an explicit "60%" must win
    over a non-brightness "level to 80" that appears earlier in the shared
    multi-action response text."""
    prose = "Setting the comfort level to 80 and dimming the light to 60%"
    assert _extract_brightness_pct({}, prose) == 60


def test_level_phrase_used_only_when_no_pct() -> None:
    """The 'brightness/level to N' fallback still applies when no %/percent
    value is present."""
    assert _extract_brightness_pct({}, "set the brightness to 35") == 35


def test_extract_brightness_data_precedence() -> None:
    """Explicit data level wins over prose."""
    assert _extract_brightness_pct({"brightness_pct": 10}, "brightness to 90") == 10


def test_extract_brightness_255_scale_converted() -> None:
    assert _extract_brightness_pct({"brightness": 255}, "") == 100
    assert _extract_brightness_pct({"brightness": 0}, "") == 0


def test_extract_brightness_bool_ignored() -> None:
    """A bool is not a numeric level (bool is an int subclass) — skip it."""
    assert _extract_brightness_pct({"brightness_pct": True}, "") is None


# --- _repair_service_name interplay with bogus light verbs ----------------


def test_repair_service_name_does_not_rescue_bogus_light_verb() -> None:
    """A bogus light brightness verb must NOT be rescued to light.turn_on by
    prose verb-inference — that would flip a dim request to full brightness.
    _normalize_parametric_calls owns these; if it left the verb (no level),
    validation should reject it for clarification."""
    assert _repair_service_name("light.set_brightness", "Turning on the light") is None
    assert _repair_service_name("light.dim", "Turning on the light") is None


def test_repair_service_name_still_fixes_real_bogus_light_onoff() -> None:
    """A non-brightness bogus light verb (e.g. light.on) is still repaired
    from prose — that path is not owned by _normalize_parametric_calls."""
    assert _repair_service_name("light.on", "Turning on the light") == "light.turn_on"


def test_repair_service_name_other_domains_unaffected() -> None:
    assert _repair_service_name("cover.cover", "Opening the garage door") == (
        "cover.open_cover"
    )


# --- Repair 2: missing climate setpoint -----------------------------------


def test_climate_setpoint_from_unit_qualified_prose() -> None:
    calls = [{"service": "climate.set_temperature", "data": {}}]
    _normalize_parametric_calls(calls, "Thermostat set to 21 degrees")
    assert calls[0]["data"]["temperature"] == 21


def test_climate_setpoint_unit_qualified_beats_clock_time() -> None:
    """A schedule hour in the same sentence must not win over the
    unit-qualified setpoint."""
    calls = [{"service": "climate.set_temperature", "data": {}}]
    _normalize_parametric_calls(calls, "Set the thermostat at 7 PM to 21 degrees")
    assert calls[0]["data"]["temperature"] == 21


def test_climate_setpoint_cue_excludes_clock_time() -> None:
    """'to 7 PM' is a time, not a setpoint — the time-marker guard drops it,
    leaving no temperature to add."""
    calls = [{"service": "climate.set_temperature", "data": {}}]
    _normalize_parametric_calls(calls, "Set the thermostat to 7 PM")
    assert "temperature" not in calls[0]["data"]


def test_climate_negative_and_decimal_setpoint() -> None:
    calls = [{"service": "climate.set_temperature", "data": {}}]
    _normalize_parametric_calls(calls, "Frost protection to -5 degrees")
    assert calls[0]["data"]["temperature"] == -5

    calls = [{"service": "climate.set_temperature", "data": {}}]
    _normalize_parametric_calls(calls, "Set to 21.5 degrees")
    assert calls[0]["data"]["temperature"] == 21.5


def test_climate_setpoint_ignores_entity_marker_digits() -> None:
    """Digits inside an [[entities:...]] marker must not shadow the real
    setpoint number."""
    calls = [{"service": "climate.set_temperature", "data": {}}]
    _normalize_parametric_calls(
        calls,
        "Setting [[entities:climate.thermostat_2]] to 22 degrees",
    )
    assert calls[0]["data"]["temperature"] == 22


def test_climate_out_of_range_dropped() -> None:
    calls = [{"service": "climate.set_temperature", "data": {}}]
    _normalize_parametric_calls(calls, "to 500 degrees")
    assert "temperature" not in calls[0]["data"]


def test_climate_existing_temperature_untouched() -> None:
    calls = [{"service": "climate.set_temperature", "data": {"temperature": 19}}]
    _normalize_parametric_calls(calls, "Thermostat set to 25 degrees")
    assert calls[0]["data"]["temperature"] == 19


# --- Defensive input shapes -----------------------------------------------


def test_non_list_calls_noop() -> None:
    _normalize_parametric_calls("not a list", "")  # type: ignore[arg-type]


def test_non_dict_call_skipped() -> None:
    calls = [None, "x", {"service": "light.dim", "data": {"brightness_pct": 5}}]
    _normalize_parametric_calls(calls, "")
    assert calls[0] is None
    assert calls[2]["service"] == "light.turn_on"


def test_non_dict_data_treated_as_empty() -> None:
    """A call whose data is a list (not a dict) must not crash; the bogus
    verb is left untouched because no level can be extracted."""
    calls = [{"service": "light.dim", "data": ["oops"]}]
    _normalize_parametric_calls(calls, "")
    assert calls[0]["service"] == "light.dim"
