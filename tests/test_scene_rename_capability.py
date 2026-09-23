"""What the scenes list says about renaming each row.

The panel cannot work this out for itself: whether a rename is possible turns
on how the scene is stored — a yaml entry with an id, one without, or nothing
in the file at all — and only the backend reads the file. A row that simply
lacked the menu item was the complaint that started this.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.websocket.scenes import _handle_websocket_get_scenes

_get_scenes = _handle_websocket_get_scenes.__wrapped__

SELORA_ID = "selora_ai_scene_abcd1234"
HA_ID = "1761606947175"


async def _write_scenes(hass: Any, entries: list[dict[str, Any]]) -> None:
    from custom_components.selora_ai.scene_utils import _write_scenes_yaml

    await hass.async_add_executor_job(
        _write_scenes_yaml, Path(hass.config.config_dir) / "scenes.yaml", entries
    )


async def _rows(hass: Any) -> dict[str, dict[str, Any]]:
    connection = MagicMock()
    with patch(
        "custom_components.selora_ai.websocket.scenes._require_admin",
        return_value=True,
    ):
        await _get_scenes(hass, connection, {"id": 1, "type": "selora_ai/get_scenes"})
    connection.send_error.assert_not_called()
    scenes = connection.send_result.call_args.args[1]["scenes"]
    return {s["scene_id"]: s for s in scenes}


@pytest.fixture
async def listed(hass: Any) -> dict[str, dict[str, Any]]:
    """One row of each kind the list can hold."""
    hass.states.async_set("switch.porch", "off")
    await _write_scenes(
        hass,
        [
            {
                "id": SELORA_ID,
                "name": "[Selora AI] Movie Night",
                "entities": {"switch.porch": {"state": "on"}},
            },
            # Home Assistant's own scene editor: timestamp id, icon, metadata.
            {
                "id": HA_ID,
                "name": "External lights",
                "entities": {"switch.porch": {"state": "on"}},
                "icon": "mdi:light-recessed",
                "metadata": {},
            },
            # Hand-authored, no id at all.
            {"name": "Hallway", "entities": {"switch.porch": {"state": "on"}}},
        ],
    )
    hass.states.async_set("scene.selora_ai_movie_night", "scening")
    hass.states.async_set("scene.external_lights", "scening")
    hass.states.async_set("scene.hallway", "scening")
    # A scene no yaml entry backs — what a Hue or Lutron bridge contributes.
    hass.states.async_set("scene.hue_relax", "scening", {"friendly_name": "Relax"})
    hass.data.setdefault(DOMAIN, {})
    return await _rows(hass)


@pytest.mark.asyncio
async def test_a_selora_scene_is_renamable(listed: dict[str, dict[str, Any]]) -> None:
    assert listed[SELORA_ID]["renamable"] is True
    assert listed[SELORA_ID]["rename_blocked"] == ""


@pytest.mark.asyncio
async def test_a_home_assistant_scene_with_an_id_is_renamable(
    listed: dict[str, dict[str, Any]],
) -> None:
    """The same one-key rewrite, and HA keeps the entity: the id is its
    unique_id, so the entity_id does not move with the name."""
    row = listed[HA_ID]
    assert row["source"] == "home_assistant"
    assert row["renamable"] is True
    assert row["rename_blocked"] == ""


@pytest.mark.asyncio
async def test_an_idless_scene_is_not_renamable_though_it_is_deletable(
    listed: dict[str, dict[str, Any]],
) -> None:
    """With no id there is no unique_id, so HA derives the entity_id from the
    name — a rename moves the entity and breaks every reference to it. Delete
    stays available, which is why the two flags are separate."""
    row = listed["hallway"]
    assert row["deletable"] is True
    assert row["renamable"] is False
    assert row["rename_blocked"] == "no_yaml_id"


@pytest.mark.asyncio
async def test_an_integration_scene_says_so(listed: dict[str, dict[str, Any]]) -> None:
    """Nothing in scenes.yaml backs it, so the name lives in the integration."""
    row = listed["hue_relax"]
    assert row["deletable"] is False
    assert row["renamable"] is False
    assert row["rename_blocked"] == "integration"
