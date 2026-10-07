"""Changing an existing scene over MCP, its icon, and fading into it.

MCP could create a scene and delete one, so an edit was a delete and a new
scene — a new entity_id, every automation that activated the old one broken,
and for a scene made in Home Assistant's editor, its icon and metadata gone.
An update now changes the entry in place, keeps what it was not asked to
change, and is checked as a new scene would be. Run against the real scene
platform reading scenes.yaml.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
import yaml

from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import (
    TOOL_ACTIVATE_SCENE,
    TOOL_CREATE_SCENE,
    TOOL_UPDATE_SCENE,
)

# As Home Assistant's own scene editor writes one.
_EDITOR_SCENE = {
    "id": "1712345678901",
    "name": "Evening",
    "icon": "mdi:weather-sunset",
    "entities": {
        "input_boolean.porch": {"state": "on"},
        "input_boolean.hall": {"state": "off"},
    },
    "metadata": {"input_boolean.porch": {"entity_only": True}, "input_boolean.hall": {}},
}


@pytest.fixture
async def scenes(hass: HomeAssistant) -> HomeAssistant:
    Path(hass.config.path("scenes.yaml")).write_text(yaml.safe_dump([_EDITOR_SCENE]))
    Path(hass.config.path("configuration.yaml")).write_text("scene: !include scenes.yaml\n")
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"porch": {}, "hall": {}, "garden": {}}}
    )
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "scene", {"scene": [_EDITOR_SCENE]})
    await hass.async_block_till_done()
    return hass


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[tool](hass, arguments)


def _stored(hass: HomeAssistant) -> list[dict[str, Any]]:
    return yaml.safe_load(Path(hass.config.path("scenes.yaml")).read_text())


def _entity_id(hass: HomeAssistant) -> str:
    entity_id = er.async_get(hass).async_get_entity_id("scene", "homeassistant", "1712345678901")
    assert entity_id is not None
    return entity_id


async def test_an_editor_scene_is_changed_in_place(scenes: HomeAssistant) -> None:
    before = _entity_id(scenes)

    result = await _mcp(
        scenes,
        TOOL_UPDATE_SCENE,
        entity_id=before,
        entities={"input_boolean.porch": {"state": "off"}, "input_boolean.garden": {"state": "on"}},
    )

    assert result["status"] == "updated", result
    (entry,) = _stored(scenes)
    assert entry["name"] == "Evening"
    assert entry["icon"] == "mdi:weather-sunset"
    assert entry["entities"] == {
        "input_boolean.porch": {"state": "off"},
        "input_boolean.garden": {"state": "on"},
    }
    # Metadata for an entity the scene no longer holds is dropped, the rest kept.
    assert entry["metadata"] == {"input_boolean.porch": {"entity_only": True}}
    assert _entity_id(scenes) == before
    assert set(scenes.states.get(before).attributes["entity_id"]) == {
        "input_boolean.porch",
        "input_boolean.garden",
    }


async def test_name_and_icon_alone_leave_the_states_alone(scenes: HomeAssistant) -> None:
    renamed = await _mcp(
        scenes, TOOL_UPDATE_SCENE, scene_id="1712345678901", name="Dusk", icon="mdi:moon"
    )
    cleared = await _mcp(scenes, TOOL_UPDATE_SCENE, scene_id="1712345678901", clear=["icon"])

    assert renamed["status"] == cleared["status"] == "updated"
    (entry,) = _stored(scenes)
    assert entry["name"] == "Dusk"
    assert "icon" not in entry
    assert entry["entities"] == _EDITOR_SCENE["entities"]


@pytest.mark.parametrize(
    ("entities", "says"),
    [
        ({"input_boolean.nope": {"state": "on"}}, "does not exist"),
        # A scene must not unlock: left out, nothing remains to save.
        ({"lock.front_door": {"state": "unlocked"}}, "no scene-capable entities"),
    ],
)
async def test_a_change_that_would_not_be_created_is_not_made(
    scenes: HomeAssistant, entities: dict[str, Any], says: str
) -> None:
    scenes.states.async_set("lock.front_door", "locked")

    result = await _mcp(scenes, TOOL_UPDATE_SCENE, scene_id="1712345678901", entities=entities)

    assert "not changed" in result["error"]
    assert says in result["error"].lower()
    assert _stored(scenes) == [_EDITOR_SCENE]


async def test_a_scene_from_an_integration_is_refused(scenes: HomeAssistant) -> None:
    entry = er.async_get(scenes).async_get_or_create("scene", "hue", "abc")

    result = await _mcp(scenes, TOOL_UPDATE_SCENE, entity_id=entry.entity_id, name="X")

    assert "not a scene from scenes.yaml" in result["error"]


async def test_a_new_scene_takes_an_icon(scenes: HomeAssistant) -> None:
    result = await _mcp(
        scenes,
        TOOL_CREATE_SCENE,
        name="Garden",
        entities={"input_boolean.garden": {"state": "on"}},
        icon="mdi:flower",
    )

    assert result["status"] == "created", result
    garden = next(s for s in _stored(scenes) if s["id"] == result["scene_id"])
    assert garden["icon"] == "mdi:flower"


async def test_activating_fades_in_with_a_transition(hass: HomeAssistant) -> None:
    calls: list[ServiceCall] = []

    async def _turn_on(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register("scene", "turn_on", _turn_on)
    hass.states.async_set("scene.evening", "scening")

    result = await _mcp(hass, TOOL_ACTIVATE_SCENE, entity_id="scene.evening", transition=4)
    too_long = await _mcp(hass, TOOL_ACTIVATE_SCENE, entity_id="scene.evening", transition=900)

    assert result["transition"] == 4
    assert calls[0].data == {"entity_id": "scene.evening", "transition": 4.0}
    assert "0 to 300" in too_long["error"]


async def test_a_change_home_assistant_never_loaded_is_rolled_back(
    scenes: HomeAssistant,
) -> None:
    """With no `scene:` section left in configuration.yaml, scene.reload loads
    no scenes and does not fail — the name in the registry still matches."""
    Path(scenes.config.path("configuration.yaml")).write_text("homeassistant:\n")

    result = await _mcp(scenes, TOOL_UPDATE_SCENE, scene_id="1712345678901", icon="mdi:moon")

    assert "did not reload" in result["error"]
    assert _stored(scenes) == [_EDITOR_SCENE]


async def test_a_disabled_scene_is_still_edited(scenes: HomeAssistant) -> None:
    """A disabled scene is never loaded, so there is nothing to compare against:
    unverifiable, not a failed reload to roll back."""
    er.async_get(scenes).async_update_entity(
        _entity_id(scenes), disabled_by=er.RegistryEntryDisabler.USER
    )
    await scenes.async_block_till_done()

    result = await _mcp(scenes, TOOL_UPDATE_SCENE, scene_id="1712345678901", name="Dusk")

    assert result["status"] == "updated", result
    assert _stored(scenes)[0]["name"] == "Dusk"
