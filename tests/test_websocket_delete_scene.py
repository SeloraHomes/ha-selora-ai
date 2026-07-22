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
