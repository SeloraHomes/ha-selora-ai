"""Tests for the rename_scene websocket handler and its yaml writer.

A rename touches one key in ``scenes.yaml``, and the interesting cases are all
about what it must NOT touch: the entities, the scene's identity, or the file
when the new name is unusable. The rebuild-through-``async_create_scene``
shortcut fails the third test here — it re-validates every member against the
state machine — which is why the writer is its own thing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.helpers import get_scene_store
from custom_components.selora_ai.scene_utils import SceneRenameError, async_rename_scene_yaml
from custom_components.selora_ai.websocket.scenes import _handle_websocket_rename_scene

# @async_response wraps the coroutine in a sync scheduler; drive the original.
_rename = _handle_websocket_rename_scene.__wrapped__

SCENE_ID = "selora_ai_scene_abcd1234"


def _scenes_path(hass: Any) -> Path:
    return Path(hass.config.config_dir) / "scenes.yaml"


async def _write_scenes(hass: Any, entries: list[dict[str, Any]]) -> None:
    from custom_components.selora_ai.scene_utils import _write_scenes_yaml

    await hass.async_add_executor_job(_write_scenes_yaml, _scenes_path(hass), entries)


async def _read_scenes(hass: Any) -> list[dict[str, Any]]:
    from custom_components.selora_ai.scene_utils import _read_scenes_yaml

    return await hass.async_add_executor_job(_read_scenes_yaml, _scenes_path(hass))


def _entry(name: str = "[Selora AI] Movie Night", entity: str = "light.lounge") -> dict[str, Any]:
    return {"id": SCENE_ID, "name": name, "entities": {entity: {"state": "on"}}}


async def _seed_store(hass: Any, name: str = "Movie Night") -> None:
    hass.data.setdefault(DOMAIN, {})
    await get_scene_store(hass).async_add_scene(SCENE_ID, name, 1, entity_id="scene.movie_night")


def _register_reload(hass: Any, calls: list[str] | None = None) -> None:
    async def _reload(call: Any) -> None:
        if calls is not None:
            calls.append(call.service)

    hass.services.async_register("scene", "reload", _reload)


async def _invoke(hass: Any, msg: dict[str, Any]) -> MagicMock:
    connection = MagicMock()
    with patch(
        "custom_components.selora_ai.websocket.scenes._require_admin",
        return_value=True,
    ):
        await _rename(hass, connection, msg)
    return connection


@pytest.mark.asyncio
async def test_renames_the_yaml_entry_and_keeps_everything_else(hass: Any) -> None:
    """The name changes, the id and entities do not."""
    await _write_scenes(hass, [_entry()])
    await _seed_store(hass)
    reloaded: list[str] = []
    _register_reload(hass, reloaded)

    connection = await _invoke(
        hass,
        {"id": 1, "type": "selora_ai/rename_scene", "scene_id": SCENE_ID, "name": "Film Night"},
    )

    connection.send_error.assert_not_called()
    assert connection.send_result.call_args.args[1]["name"] == "Film Night"
    assert reloaded == ["reload"]
    assert await _read_scenes(hass) == [
        {
            "id": SCENE_ID,
            "name": "[Selora AI] Film Night",
            "entities": {"light.lounge": {"state": "on"}},
        }
    ]


@pytest.mark.asyncio
async def test_the_store_serves_the_new_name(hass: Any) -> None:
    """The panel reads its list from the store, so a stale record is a stale UI."""
    await _write_scenes(hass, [_entry()])
    await _seed_store(hass)
    _register_reload(hass)

    await _invoke(
        hass,
        {"id": 1, "type": "selora_ai/rename_scene", "scene_id": SCENE_ID, "name": "Film Night"},
    )

    record = await get_scene_store(hass).async_get_scene(SCENE_ID)
    assert record is not None
    assert record["name"] == "Film Night"


@pytest.mark.asyncio
async def test_renames_a_scene_whose_entity_is_gone(hass: Any) -> None:
    """A member that has since been unpaired must not block a rename.

    Nothing about renaming needs the entities to resolve, and refusing here
    would leave the scene unrenameable until the device came back.
    """
    await _write_scenes(hass, [_entry(entity="light.unpaired")])
    await _seed_store(hass)
    _register_reload(hass)
    assert hass.states.get("light.unpaired") is None

    connection = await _invoke(
        hass,
        {"id": 1, "type": "selora_ai/rename_scene", "scene_id": SCENE_ID, "name": "Film Night"},
    )

    connection.send_error.assert_not_called()
    assert (await _read_scenes(hass))[0]["name"] == "[Selora AI] Film Night"


@pytest.mark.asyncio
async def test_a_prefixed_name_is_not_doubled(hass: Any) -> None:
    """Retyping the whole field can hand back the prefix the writer adds."""
    await _write_scenes(hass, [_entry()])
    await _seed_store(hass)
    _register_reload(hass)

    await _invoke(
        hass,
        {
            "id": 1,
            "type": "selora_ai/rename_scene",
            "scene_id": SCENE_ID,
            "name": "[Selora AI] Film Night",
        },
    )

    assert (await _read_scenes(hass))[0]["name"] == "[Selora AI] Film Night"


@pytest.mark.asyncio
async def test_an_empty_name_is_refused_and_the_file_is_untouched(hass: Any) -> None:
    """Whitespace sanitizes to nothing; the scene keeps the name it had."""
    await _write_scenes(hass, [_entry()])
    await _seed_store(hass)
    reloaded: list[str] = []
    _register_reload(hass, reloaded)

    connection = await _invoke(
        hass,
        {"id": 1, "type": "selora_ai/rename_scene", "scene_id": SCENE_ID, "name": "   "},
    )

    connection.send_result.assert_not_called()
    assert connection.send_error.call_args.args[1] == "rename_failed"
    assert reloaded == []
    assert (await _read_scenes(hass))[0]["name"] == "[Selora AI] Movie Night"


@pytest.mark.asyncio
async def test_a_scene_the_store_does_not_know_is_not_found(hass: Any) -> None:
    """The store record is the gate; the file is never searched without one."""
    await _write_scenes(hass, [_entry()])
    hass.data.setdefault(DOMAIN, {})
    _register_reload(hass)

    connection = await _invoke(
        hass,
        {"id": 1, "type": "selora_ai/rename_scene", "scene_id": SCENE_ID, "name": "Film Night"},
    )

    assert connection.send_error.call_args.args[1] == "not_found"
    assert (await _read_scenes(hass))[0]["name"] == "[Selora AI] Movie Night"


@pytest.mark.asyncio
async def test_a_failed_reload_rolls_the_name_back(hass: Any) -> None:
    """A name HA refuses must not be left on disk to load at the next restart."""
    await _write_scenes(hass, [_entry()])

    async def _reload(call: Any) -> None:
        raise RuntimeError("scenes.yaml rejected")

    hass.services.async_register("scene", "reload", _reload)

    with pytest.raises(SceneRenameError):
        await async_rename_scene_yaml(hass, SCENE_ID, "Film Night")

    assert (await _read_scenes(hass))[0]["name"] == "[Selora AI] Movie Night"


# A scene Home Assistant's own editor wrote: a timestamp id, an icon, a
# metadata block, and per-entity extras the integration captured.
HA_SCENE_ID = "1761606947175"


def _ha_entry() -> dict[str, Any]:
    return {
        "id": HA_SCENE_ID,
        "name": "External lights",
        "entities": {
            "switch.front_porch_overhead_light": {
                "device_id": "4",
                "zone_id": "2",
                "friendly_name": "Front Porch Overhead Light",
                "state": "on",
            }
        },
        "icon": "mdi:light-recessed",
        "metadata": {},
    }


@pytest.mark.asyncio
async def test_renames_a_home_assistant_scene_without_claiming_it(hass: Any) -> None:
    """The display prefix marks ownership, and the id already decides that."""
    await _write_scenes(hass, [_ha_entry()])
    _register_reload(hass)

    await async_rename_scene_yaml(hass, HA_SCENE_ID, "Exterior lights")

    entry = (await _read_scenes(hass))[0]
    assert entry["name"] == "Exterior lights"


@pytest.mark.asyncio
async def test_a_rename_keeps_everything_home_assistant_stored(hass: Any) -> None:
    """Only ``name`` is touched.

    Rebuilding the entry — which is what ``async_create_scene`` does — keeps
    id/name/entities and drops the rest, so the icon, the metadata block and
    every per-entity extra would vanish on a rename.
    """
    await _write_scenes(hass, [_ha_entry()])
    _register_reload(hass)

    await async_rename_scene_yaml(hass, HA_SCENE_ID, "Exterior lights")

    entry = (await _read_scenes(hass))[0]
    assert entry["icon"] == "mdi:light-recessed"
    assert entry["metadata"] == {}
    assert entry["entities"]["switch.front_porch_overhead_light"] == {
        "device_id": "4",
        "zone_id": "2",
        "friendly_name": "Front Porch Overhead Light",
        "state": "on",
    }
    # And the id stays a STRING. A timestamp id re-emitted unquoted reads back
    # as an int, which no longer matches the unique_id HA registered.
    assert entry["id"] == HA_SCENE_ID
    assert isinstance(entry["id"], str)


@pytest.mark.asyncio
async def test_an_unknown_home_assistant_scene_is_refused(hass: Any) -> None:
    """The file bounds the write: no entry with that id, no rename."""
    await _write_scenes(hass, [_ha_entry()])
    _register_reload(hass)

    with pytest.raises(SceneRenameError):
        await async_rename_scene_yaml(hass, "9999999999999", "Exterior lights")

    assert (await _read_scenes(hass))[0]["name"] == "External lights"


@pytest.mark.asyncio
async def test_refuses_a_scene_missing_from_the_file(hass: Any) -> None:
    """A store record whose yaml entry is gone is not a rename target."""
    await _write_scenes(hass, [])
    _register_reload(hass)

    with pytest.raises(SceneRenameError):
        await async_rename_scene_yaml(hass, SCENE_ID, "Film Night")


async def _setup_scene_component(hass: Any) -> str:
    """Load the real scene platform from a scenes.yaml include, as a hub does."""
    from homeassistant.helpers import entity_registry as er
    from homeassistant.setup import async_setup_component

    hass.states.async_set("light.lounge", "off")
    await _write_scenes(hass, [_entry()])
    Path(hass.config.config_dir, "configuration.yaml").write_text(
        "scene: !include scenes.yaml\n", encoding="utf-8"
    )
    assert await async_setup_component(
        hass,
        "scene",
        {
            "scene": [
                {
                    "id": SCENE_ID,
                    "name": "[Selora AI] Movie Night",
                    "entities": {"light.lounge": "on"},
                }
            ]
        },
    )
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id("scene", "homeassistant", SCENE_ID)
    assert entity_id is not None
    return entity_id


@pytest.mark.asyncio
async def test_home_assistant_serves_the_new_name_after_a_real_reload(hass: Any) -> None:
    """End to end against the real platform: the reloaded scene carries it."""
    from homeassistant.helpers import entity_registry as er

    entity_id = await _setup_scene_component(hass)

    await async_rename_scene_yaml(hass, SCENE_ID, "Film Night")
    await hass.async_block_till_done()

    assert er.async_get(hass).async_get(entity_id).original_name == "[Selora AI] Film Night"


@pytest.mark.asyncio
async def test_a_reload_that_silently_did_nothing_is_refused(hass: Any) -> None:
    """``scene.reload`` logs and returns on a bad configuration.yaml.

    Nothing raises, so without the post-reload check the rename would be
    reported — and recorded in the store and every session — while Home
    Assistant kept serving the old name.
    """
    await _setup_scene_component(hass)
    Path(hass.config.config_dir, "configuration.yaml").write_text(
        "scene: !include scenes.yaml\nbroken: [unclosed\n", encoding="utf-8"
    )

    with pytest.raises(SceneRenameError):
        await async_rename_scene_yaml(hass, SCENE_ID, "Film Night")

    assert (await _read_scenes(hass))[0]["name"] == "[Selora AI] Movie Night"


@pytest.mark.asyncio
async def test_assist_is_told_the_scene_was_renamed(hass: Any) -> None:
    """Assist caches each conversation's scenes in memory.

    The store now holds the new content hash, so a later reconcile sees no
    drift and never fires this signal itself — without the dispatch here an
    open conversation names the scene as it was until the process restarts.
    """
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    from custom_components.selora_ai.const import SIGNAL_SCENE_REFRESHED

    await _write_scenes(hass, [_entry()])
    await _seed_store(hass)
    _register_reload(hass)

    refreshed: list[tuple[str, str, str]] = []
    async_dispatcher_connect(
        hass,
        SIGNAL_SCENE_REFRESHED,
        lambda sid, name, yaml_repr: refreshed.append((sid, name, yaml_repr)),
    )

    await _invoke(
        hass,
        {"id": 1, "type": "selora_ai/rename_scene", "scene_id": SCENE_ID, "name": "Film Night"},
    )
    await hass.async_block_till_done()

    assert [(sid, name) for sid, name, _ in refreshed] == [(SCENE_ID, "Film Night")]
    assert "Film Night" in refreshed[0][2]


@pytest.mark.asyncio
async def test_the_handler_renames_a_home_assistant_scene(hass: Any) -> None:
    """No store record is required — and none is written.

    A record would list the scene as Selora-managed in the panel, which is the
    ownership claim the withheld display prefix exists to avoid.
    """
    await _write_scenes(hass, [_ha_entry()])
    hass.data.setdefault(DOMAIN, {})
    _register_reload(hass)

    connection = await _invoke(
        hass,
        {
            "id": 1,
            "type": "selora_ai/rename_scene",
            "scene_id": HA_SCENE_ID,
            "name": "Exterior lights",
        },
    )

    connection.send_error.assert_not_called()
    assert connection.send_result.call_args.args[1]["name"] == "Exterior lights"
    assert (await _read_scenes(hass))[0]["name"] == "Exterior lights"
    assert await get_scene_store(hass).async_get_scene(HA_SCENE_ID) is None


@pytest.mark.asyncio
async def test_a_selora_id_the_store_forgot_is_still_refused(hass: Any) -> None:
    """Widening to HA scenes must not widen to Selora ids nobody tracks.

    Selora addresses its scenes by an id it mints, so one resolving to no live
    record is a stale or crafted target rather than a rename.
    """
    await _write_scenes(hass, [_entry()])
    hass.data.setdefault(DOMAIN, {})
    _register_reload(hass)

    connection = await _invoke(
        hass,
        {"id": 1, "type": "selora_ai/rename_scene", "scene_id": SCENE_ID, "name": "Film Night"},
    )

    assert connection.send_error.call_args.args[1] == "not_found"
    assert (await _read_scenes(hass))[0]["name"] == "[Selora AI] Movie Night"
