"""Valid Home Assistant automations come through our validator unchanged.

Each case is a config Home Assistant itself accepts, and each was silently
changed or wrongly refused on the way to automations.yaml: fields dropped by a
payload rebuilt from a fixed list, lists turned into text, a newer trigger
style refused, a refresh of a sensor refused. The assertion is on the VALUE
that comes out, not just on "valid" — a validator that accepts a config and
writes something else is the failure being guarded.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from custom_components.selora_ai.automation_utils import (
    prepare_write_payload,
    validate_automation_payload,
)


def _automation(**overrides: Any) -> dict[str, Any]:
    return {
        "alias": "Porch light",
        "triggers": [{"trigger": "state", "entity_id": "binary_sensor.door", "to": "on"}],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}],
        **overrides,
    }


def _home(hass: HomeAssistant) -> HomeAssistant:
    for entity_id in ("binary_sensor.door", "light.porch", "sensor.temp", "input_boolean.away"):
        hass.states.async_set(entity_id, "off")

    async def _noop(_call: Any) -> None:
        return None

    for domain, service in (
        ("light", "turn_on"),
        ("homeassistant", "update_entity"),
        ("homeassistant", "turn_on"),
    ):
        hass.services.async_register(domain, service, _noop)
    return hass


def _accepted(hass: HomeAssistant, payload: dict[str, Any]) -> dict[str, Any]:
    ok, reason, normalized = validate_automation_payload(payload, hass)
    assert ok, reason
    assert normalized is not None
    return dict(normalized)


async def test_top_level_fields_survive(hass: HomeAssistant) -> None:
    fields = {
        "mode": "queued",
        "max": 3,
        "max_exceeded": "silent",
        "variables": {"brightness": 80},
        "trigger_variables": {"room": "porch"},
        "trace": {"stored_traces": 20},
    }
    normalized = _accepted(_home(hass), _automation(**fields))

    for key, value in fields.items():
        assert normalized[key] == value, key


async def test_the_write_path_keeps_them_too(hass: HomeAssistant) -> None:
    payload = _automation(mode="parallel", max=5, variables={"x": 1})

    ok, reason, _ = await prepare_write_payload(_home(hass), payload)

    assert ok, reason
    assert payload["max"] == 5
    assert payload["variables"] == {"x": 1}


async def test_a_malformed_extra_field_is_refused_not_dropped(hass: HomeAssistant) -> None:
    """Refused as Home Assistant's own schema would: `max` starts at 2."""
    for bad in ({"max": 0}, {"max": 1, "mode": "queued"}, {"max_exceeded": "loud"}):
        ok, reason, _ = validate_automation_payload(_automation(**bad), _home(hass))
        assert not ok, bad
        assert "max" in reason, bad


async def test_lists_of_states_stay_lists(hass: HomeAssistant) -> None:
    payload = _automation(
        triggers=[
            {
                "trigger": "state",
                "entity_id": "binary_sensor.door",
                "from": ["off", "unavailable"],
                "to": ["on", True],
            }
        ],
        conditions=[
            {"condition": "state", "entity_id": "input_boolean.away", "state": ["on", "off"]}
        ],
    )

    normalized = _accepted(_home(hass), payload)

    (trigger,) = normalized["triggers"]
    assert trigger["from"] == ["off", "unavailable"]
    assert trigger["to"] == ["on", "on"]
    assert normalized["conditions"][0]["state"] == ["on", "off"]


async def test_several_times_and_an_entity_time_stay_as_written(hass: HomeAssistant) -> None:
    payload = _automation(
        triggers=[
            {"trigger": "time", "at": ["07:00:00", "19:30:00"]},
            {"trigger": "time", "at": {"entity_id": "sensor.temp", "offset": "-00:10:00"}},
        ]
    )

    normalized = _accepted(_home(hass), payload)

    assert normalized["triggers"][0]["at"] == ["07:00:00", "19:30:00"]
    assert normalized["triggers"][1]["at"] == {"entity_id": "sensor.temp", "offset": "-00:10:00"}


async def test_a_trigger_home_assistant_registered_is_accepted(hass: HomeAssistant) -> None:
    """`trigger: light.turned_on` is what HA 2026.7+'s editor writes."""
    hass.data["triggers"] = {"light.turned_on": "light", "zwave_js.value_updated": "zwave_js"}

    for platform in ("light.turned_on", "zwave_js.value_updated"):
        normalized = _accepted(
            _home(hass),
            _automation(triggers=[{"trigger": platform, "target": {"entity_id": "light.porch"}}]),
        )
        assert normalized["triggers"][0]["trigger"] == platform


async def test_an_unregistered_dotted_trigger_is_still_refused(hass: HomeAssistant) -> None:
    """An event written as a trigger, and an invented sub-type of an
    old-style platform that is registered under its bare name."""
    hass.data["triggers"] = {"light.turned_on": "light", "mqtt": "mqtt"}

    for platform in ("timer.finished", "mqtt.foo"):
        ok, reason, _ = validate_automation_payload(
            _automation(triggers=[{"trigger": platform}]), _home(hass)
        )
        assert not ok, platform
        assert "event" in reason


async def test_refreshing_a_sensor_is_accepted(hass: HomeAssistant) -> None:
    normalized = _accepted(
        _home(hass),
        _automation(
            actions=[
                {"action": "homeassistant.update_entity", "target": {"entity_id": "sensor.temp"}}
            ]
        ),
    )

    assert normalized["actions"][0]["action"] == "homeassistant.update_entity"
