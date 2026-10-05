"""Tests for the configuration reads: get_automation, get_scene, find_references.

They drive HA's real automation, scene and trace components, because the
failure they guard against was the model answering from names: a trace path it
could not resolve, a scene whose contents it could not see, and an automation
called "Goodnight Scene" taken for the Goodnight scene.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai.diagnostics_tools import _step_config
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import TOOL_MAP

_NIGHT_LIGHTS = {
    "id": "goodnight_scene_auto",
    "alias": "Goodnight Scene",
    "description": "Turn off all lights and set the thermostat to 69",
    "triggers": [{"trigger": "event", "event_type": "front_door_changed"}],
    "conditions": [
        {
            "condition": "template",
            "value_template": "{{ is_state('sun.sun', 'above_horizon') and false }}",
        }
    ],
    "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.foyer"}}],
}


def _executor(hass: HomeAssistant, *, is_admin: bool = True) -> ToolExecutor:
    return ToolExecutor(hass, MagicMock(), is_admin=is_admin)


@pytest.fixture
async def home(hass: HomeAssistant) -> HomeAssistant:
    """A Goodnight scene and an unrelated automation named "Goodnight Scene"."""
    hass.states.async_set("light.foyer", "off", {"friendly_name": "Foyer"})
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen"})
    hass.states.async_set("light.guest_bath", "on", {"friendly_name": "Guest Bath"})
    assert await async_setup_component(
        hass,
        "scene",
        {
            "scene": [
                {
                    "name": "Goodnight",
                    "entities": {"light.kitchen": "off", "light.foyer": "off"},
                }
            ]
        },
    )
    assert await async_setup_component(hass, "automation", {"automation": [_NIGHT_LIGHTS]})
    await hass.async_block_till_done()
    return hass


# ── get_scene ───────────────────────────────────────────────────────────────


async def test_get_scene_lists_what_the_scene_sets(home: HomeAssistant) -> None:
    """A light the scene doesn't list is one the scene leaves alone — say which."""
    result = await _executor(home).execute("get_scene", {"scene": "Goodnight"})

    assert result["entity_id"] == "scene.goodnight"
    assert result["entities"] == {"light.kitchen": "off", "light.foyer": "off"}
    assert "light.guest_bath" not in result["entities"]
    assert "ONLY" in result["note"]


async def test_get_scene_never_resolves_to_an_automation(home: HomeAssistant) -> None:
    """'Goodnight Scene' is an automation's name; asking for a scene must not find it."""
    result = await _executor(home).execute("get_scene", {"scene": "Goodnight Scene"})

    assert "error" in result
    assert "search_entities(domain='scene')" in result["error"]


# ── get_automation ──────────────────────────────────────────────────────────


async def test_get_automation_reads_config_not_in_automations_yaml(
    home: HomeAssistant,
) -> None:
    """Packages and includes are not automations.yaml; the loaded entity still knows."""
    result = await _executor(home).execute("get_automation", {"automation": "Goodnight Scene"})

    assert result["entity_id"] == "automation.goodnight_scene"
    assert "light.turn_on" in result["config"]
    assert "front_door_changed" in result["config"]
    assert "!!python" not in result["config"]


async def test_get_automation_requires_admin(home: HomeAssistant) -> None:
    """Home Assistant's own automation/config read is admin-only."""
    assert TOOL_MAP["get_automation"].requires_admin
    result = await _executor(home, is_admin=False).execute(
        "get_automation", {"automation": "automation.goodnight_scene"}
    )
    assert "admin" in result["error"]


async def test_a_shared_name_is_an_ambiguity(hass: HomeAssistant) -> None:
    hass.states.async_set("automation.one", "on", {"friendly_name": "Goodnight"})
    hass.states.async_set("automation.two", "on", {"friendly_name": "Goodnight"})

    result = await _executor(hass).execute("get_automation", {"automation": "Goodnight"})

    assert "automation.one" in result["error"]
    assert "automation.two" in result["error"]


# ── get_automation_traces ───────────────────────────────────────────────────


async def test_trace_names_the_condition_that_stopped_the_run(home: HomeAssistant) -> None:
    """``condition/0`` alone let the model say only that *a* condition failed."""
    home.bus.async_fire("front_door_changed")
    await home.async_block_till_done()

    result = await _executor(home).execute(
        "get_automation_traces", {"automation": "automation.goodnight_scene"}
    )

    run = result["traces"][0]
    assert run["last_step"] == "condition/0"
    stopped = run["stopped_at"]
    assert stopped["config"]["condition"] == "template"
    assert stopped["result"]["result"] is False


def test_step_config_maps_singular_trace_keys_to_either_config_form() -> None:
    plural = {"conditions": [{"condition": "sun", "after": "sunset"}]}
    singular_mapping = {"condition": {"condition": "sun", "after": "sunset"}}
    nested = {
        "actions": [
            {"choose": [{"conditions": [{"condition": "state"}], "sequence": [{"delay": 1}]}]}
        ]
    }

    assert _step_config(plural, "condition/0") == {"condition": "sun", "after": "sunset"}
    assert _step_config(singular_mapping, "condition/0") == {"condition": "sun", "after": "sunset"}
    assert _step_config(nested, "action/0/choose/0/conditions/0") == {"condition": "state"}
    assert _step_config(nested, "action/0/choose/0/sequence/0") == {"delay": 1}
    assert _step_config(plural, "condition/3") is None


# ── find_references ─────────────────────────────────────────────────────────


async def test_find_references_lists_automations_and_scenes(home: HomeAssistant) -> None:
    result = await _executor(home).execute("find_references", {"target": "light.foyer"})

    assert [a["entity_id"] for a in result["automations"]] == ["automation.goodnight_scene"]
    assert [s["entity_id"] for s in result["scenes"]] == ["scene.goodnight"]
    assert result["scripts"] == []


async def test_find_references_includes_the_entitys_device(hass: HomeAssistant) -> None:
    """Device triggers and targets name the device, not the entity the user sees."""
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("test", "button")}, name="Smart Button"
    )
    button = er.async_get(hass).async_get_or_create(
        "event", "test", "button", device_id=device.id, config_entry=entry
    )
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": [
                {
                    "id": "press",
                    "alias": "Button Goodnight",
                    "triggers": [{"trigger": "event", "event_type": "pressed"}],
                    "actions": [{"action": "light.turn_off", "target": {"device_id": device.id}}],
                }
            ]
        },
    )
    await hass.async_block_till_done()

    result = await _executor(hass).execute("find_references", {"target": button.entity_id})

    assert result["device_id"] == device.id
    assert [a["name"] for a in result["automations"]] == ["Button Goodnight"]


async def test_find_references_rejects_an_unknown_target(hass: HomeAssistant) -> None:
    result = await _executor(hass).execute("find_references", {"target": "Smart Button"})
    assert "search_entities" in result["error"]


async def test_mcp_get_automation_never_exposes_the_loaded_config(home: HomeAssistant) -> None:
    """The loaded config carries resolved secrets; MCP serves read-only tokens."""
    from custom_components.selora_ai.mcp_server.automations import _tool_get_automation

    result = await _tool_get_automation(home, {"entity_id": "automation.goodnight_scene"})
    assert result["yaml"] == ""


async def test_a_scene_that_sets_nothing_is_not_reported_hidden(hass: HomeAssistant) -> None:
    assert await async_setup_component(
        hass, "scene", {"scene": [{"name": "Empty", "entities": {}}]}
    )
    await hass.async_block_till_done()

    result = await _executor(hass).execute("get_scene", {"scene": "scene.empty"})

    assert result["entities"] == {}
    assert "ONLY" in result["note"]
