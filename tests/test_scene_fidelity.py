"""What a scene keeps, what it leaves out, and that it says so.

A scene kept six domains and dropped every other entity without a word, so a
scene with a dropdown or a number helper in it was saved without them. Which
domains a scene can set is now Home Assistant's answer (a `reproduce_state`
platform), minus locks and alarms, which a scene must not unlock or disarm;
whatever is left out is named, with the reason.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from custom_components.selora_ai.scene_utils import scene_left_out, validate_scene_payload


def _scene(**entities: dict[str, Any]) -> dict[str, Any]:
    return {"name": "Evening", "entities": {k.replace("__", "."): v for k, v in entities.items()}}


def test_helpers_and_settings_a_scene_can_restore_are_kept() -> None:
    scene = _scene(
        light__lounge={"state": "on", "brightness": 120},
        input_boolean__guest_mode={"state": "on"},
        input_select__house_mode={"state": "Evening"},
        input_number__target={"state": "21.5"},
        select__fan_speed={"state": "low"},
    )

    ok, reason, normalized = validate_scene_payload(scene)

    assert ok, reason
    assert set(normalized["entities"]) == {
        "light.lounge",
        "input_boolean.guest_mode",
        "input_select.house_mode",
        "input_number.target",
        "select.fan_speed",
    }
    assert normalized["entities"]["input_select.house_mode"] == {"state": "Evening"}
    assert scene_left_out(scene) == []


def test_what_is_left_out_is_named_with_why() -> None:
    scene = _scene(
        light__lounge={"state": "on"},
        lock__front_door={"state": "unlocked"},
        alarm_control_panel__home={"state": "disarmed"},
        sensor__temperature={"state": "21"},
        switch__camera_privacy_mode={"state": "on"},
    )

    ok, reason, normalized = validate_scene_payload(scene)
    left = {row["entity_id"]: row["reason"] for row in scene_left_out(scene)}

    assert ok, reason
    assert set(normalized["entities"]) == {"light.lounge"}
    assert "unlock or disarm" in left["lock.front_door"]
    assert "unlock or disarm" in left["alarm_control_panel.home"]
    assert "no state" in left["sensor.temperature"]
    assert "device setting" in left["switch.camera_privacy_mode"]


async def test_a_loaded_integration_decides_for_itself(hass: HomeAssistant) -> None:
    """With hass, the loaded integration's own reproduce_state platform is
    what counts — input_boolean has one, a domain that is not loaded falls
    back to the core list."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "input_boolean", {})
    hass.states.async_set("input_boolean.guest_mode", "off")
    hass.states.async_set("light.lounge", "off")

    ok, reason, normalized = validate_scene_payload(
        _scene(input_boolean__guest_mode={"state": "on"}, light__lounge={"state": "on"}), hass
    )

    assert ok, reason
    assert set(normalized["entities"]) == {"input_boolean.guest_mode", "light.lounge"}


async def test_mcp_names_what_it_left_out(hass: HomeAssistant) -> None:
    from custom_components.selora_ai.mcp_server.scenes import _tool_validate_scene

    for entity_id in ("light.lounge", "sensor.temperature"):
        hass.states.async_set(entity_id, "on")

    result = await _tool_validate_scene(
        hass,
        {
            "name": "Evening",
            "entities": {"light.lounge": {"state": "on"}, "sensor.temperature": {"state": "21"}},
        },
    )

    assert result["valid"] is True, result
    assert [row["entity_id"] for row in result["left_out"]] == ["sensor.temperature"]


async def test_a_loaded_integration_that_can_restore_is_accepted_end_to_end(
    hass: HomeAssistant,
) -> None:
    """Every validator asks the same question of the same hass: a custom
    integration with a reproduce_state platform is not accepted by the first
    check and refused by a later one that only knows the core list."""
    from unittest.mock import patch

    from custom_components.selora_ai import entity_capabilities
    from custom_components.selora_ai.scene_validation import validate_scene_security

    hass.states.async_set("blinds_plus.lounge", "open")
    with patch.object(
        entity_capabilities,
        "_restorable",
        side_effect=lambda h, domain: h is not None and domain == "blinds_plus",
    ):
        ok, reason, normalized = validate_scene_payload(
            _scene(blinds_plus__lounge={"state": "closed"}), hass
        )
        assert ok, reason
        safe, warnings = validate_scene_security(normalized, hass)

    assert normalized["entities"] == {"blinds_plus.lounge": {"state": "closed"}}
    assert safe, warnings
