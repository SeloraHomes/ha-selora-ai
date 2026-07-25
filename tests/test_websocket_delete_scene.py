"""Tests for the delete_scene websocket handler.

Covers the id-less yaml scene path: a hand-authored scenes.yaml entry that
omits the optional `id` field has no usable scene_id, so the panel deletes
it by entity_id — the handler must fall back to the shared entity/name
resolver instead of reporting "not found".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from custom_components.selora_ai.websocket.scenes import _handle_websocket_delete_scene

# @async_response wraps the coroutine in a sync scheduler; drive the original.
_delete = _handle_websocket_delete_scene.__wrapped__


def _scenes_path(hass: Any) -> Path:
    return Path(hass.config.config_dir) / "scenes.yaml"


async def _write_scenes(hass: Any, entries: list[dict[str, Any]]) -> None:
    from custom_components.selora_ai.scene_utils import _write_scenes_yaml

    await hass.async_add_executor_job(_write_scenes_yaml, _scenes_path(hass), entries)


async def _read_scenes(hass: Any) -> list[dict[str, Any]]:
    from custom_components.selora_ai.scene_utils import _read_scenes_yaml

    return await hass.async_add_executor_job(_read_scenes_yaml, _scenes_path(hass))


async def _invoke(hass: Any, msg: dict[str, Any]) -> MagicMock:
    connection = MagicMock()
    with patch(
        "custom_components.selora_ai.websocket.scenes._require_admin",
        return_value=True,
    ):
        await _delete(hass, connection, msg)
    return connection


@pytest.mark.asyncio
async def test_deletes_idless_yaml_scene_by_entity_id(hass: Any) -> None:
    """An id-less yaml scene deletes via the entity_id fallback."""
    await _write_scenes(
        hass,
        [{"name": "External Lights", "entities": {"light.x": {"state": "on"}}}],
    )
    hass.states.async_set("scene.external_lights", "scening")

    reloaded: list[tuple[str, str]] = []

    async def _reload(call):
        reloaded.append((call.domain, call.service))

    hass.services.async_register("scene", "reload", _reload)

    connection = await _invoke(
        hass,
        {
            "id": 1,
            "type": "selora_ai/delete_scene",
            # id-less scenes surface their slug as scene_id; it is not a
            # scenes.yaml id, so the handler must fall back to entity_id.
            "scene_id": "external_lights",
            "entity_id": "scene.external_lights",
        },
    )

    connection.send_result.assert_called_once()
    assert connection.send_result.call_args.args[1] == {"success": True}
    connection.send_error.assert_not_called()
    assert reloaded == [("scene", "reload")]
    assert await _read_scenes(hass) == []


@pytest.mark.asyncio
async def test_idless_scene_id_colliding_with_another_yaml_id_deletes_the_right_scene(
    hass: Any,
) -> None:
    """An id-less row's scene_id is its entity object_id, not a yaml id.

    When an *unrelated* entry happens to carry that same string as its ``id``,
    deleting by id would remove that other scene and report success. The handler
    must notice the id doesn't belong to the entity the panel pointed at and
    route to the entity resolver instead.
    """
    from homeassistant.helpers import entity_registry as er

    await _write_scenes(
        hass,
        [
            # Its yaml `id` collides with the *other* scene's object_id.
            {"id": "movie_night", "name": "Cinema Mode", "entities": {"light.a": {"state": "on"}}},
            {"name": "Movie Night", "entities": {"light.b": {"state": "on"}}},
        ],
    )
    # HA registers yaml scenes under unique_id = the yaml id, which is what makes
    # the two entries distinguishable.
    er.async_get(hass).async_get_or_create(
        "scene", "homeassistant", "movie_night", suggested_object_id="cinema_mode"
    )
    hass.states.async_set("scene.cinema_mode", "scening")
    hass.states.async_set("scene.movie_night", "scening")

    async def _reload(call):
        pass

    hass.services.async_register("scene", "reload", _reload)

    connection = await _invoke(
        hass,
        {
            "id": 1,
            "type": "selora_ai/delete_scene",
            "scene_id": "movie_night",
            "entity_id": "scene.movie_night",
        },
    )

    connection.send_result.assert_called_once()
    connection.send_error.assert_not_called()
    # The id-less "Movie Night" is gone; the id-bearing "Cinema Mode" survives.
    assert await _read_scenes(hass) == [
        {"id": "movie_night", "name": "Cinema Mode", "entities": {"light.a": {"state": "on"}}}
    ]


@pytest.mark.asyncio
async def test_store_tracked_id_still_validated_against_the_supplied_entity(
    hass: Any,
) -> None:
    """A store record for scene_id must not skip the ownership check.

    SceneStore tracks every ``selora_ai_``-prefixed yaml id, so if the caller's
    scene_id matches one of those while entity_id points at a different scene,
    taking the id path on the strength of the store record alone would delete the
    Selora scene instead of the one the caller identified.
    """
    from homeassistant.helpers import entity_registry as er

    await _write_scenes(
        hass,
        [
            # Selora-managed -> imported into SceneStore by reconcile.
            {
                "id": "selora_ai_abcd1234",
                "name": "Cinema Mode",
                "entities": {"light.a": {"state": "on"}},
            },
            # The id-less scene the caller actually picked.
            {"name": "Movie Night", "entities": {"light.b": {"state": "on"}}},
        ],
    )
    er.async_get(hass).async_get_or_create(
        "scene", "homeassistant", "selora_ai_abcd1234", suggested_object_id="cinema_mode"
    )
    hass.states.async_set("scene.cinema_mode", "scening")
    hass.states.async_set("scene.movie_night", "scening")

    async def _reload(call):
        pass

    hass.services.async_register("scene", "reload", _reload)

    connection = await _invoke(
        hass,
        {
            "id": 1,
            "type": "selora_ai/delete_scene",
            "scene_id": "selora_ai_abcd1234",
            "entity_id": "scene.movie_night",
        },
    )

    connection.send_result.assert_called_once()
    connection.send_error.assert_not_called()
    # The id-less "Movie Night" is gone; the Selora scene survives untouched.
    remaining = await _read_scenes(hass)
    assert remaining == [
        {
            "id": "selora_ai_abcd1234",
            "name": "Cinema Mode",
            "entities": {"light.a": {"state": "on"}},
        }
    ]


@pytest.mark.asyncio
async def test_unknown_scene_without_entity_id_is_not_found(hass: Any) -> None:
    """No store/yaml match and no entity_id → a genuine not_found error."""
    await _write_scenes(hass, [])

    async def _reload(call):
        pass

    hass.services.async_register("scene", "reload", _reload)

    connection = await _invoke(
        hass,
        {"id": 1, "type": "selora_ai/delete_scene", "scene_id": "ghost"},
    )

    connection.send_error.assert_called_once()
    assert connection.send_error.call_args.args[1] == "not_found"
    connection.send_result.assert_not_called()
