"""Tests for what accept_scene reports back about the write it performed.

Accepting a refinement rewrites the scene the session already saved — a rename
asked for in chat is exactly that — so the panel needs to know which of create
and update happened before it tells the user.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.conversation_store import ConversationStore
from custom_components.selora_ai.websocket.scenes import _handle_websocket_accept_scene

_accept = _handle_websocket_accept_scene.__wrapped__

SESSION_ID = "session-1"
SCENE_ID = "selora_ai_scene_abcd1234"
SCENE_YAML = "name: Movie Night\nentities:\n  light.lounge:\n    state: 'on'\n"


async def _read_scenes(hass: Any) -> list[dict[str, Any]]:
    from custom_components.selora_ai.scene_utils import _read_scenes_yaml

    return await hass.async_add_executor_job(
        _read_scenes_yaml, Path(hass.config.config_dir) / "scenes.yaml"
    )


async def _write_scenes(hass: Any, entries: list[dict[str, Any]]) -> None:
    from custom_components.selora_ai.scene_utils import _write_scenes_yaml

    await hass.async_add_executor_job(
        _write_scenes_yaml, Path(hass.config.config_dir) / "scenes.yaml", entries
    )


def _proposal(name: str) -> dict[str, Any]:
    return {"name": name, "entities": {"light.lounge": {"state": "on"}}}


async def _setup(hass: Any, *, refine: bool) -> ConversationStore:
    """A session holding a saved scene and a fresh proposal after it."""
    hass.states.async_set("light.lounge", "on")
    await _write_scenes(
        hass,
        [
            {
                "id": SCENE_ID,
                "name": "[Selora AI] Movie Night",
                "entities": {"light.lounge": {"state": "on"}},
            }
        ],
    )

    async def _reload(call: Any) -> None:
        # Stand in for HA loading the file: every entry in it gets an entity,
        # which is what async_create_scene verifies before reporting success.
        for entry in await _read_scenes(hass):
            hass.states.async_set(f"scene.{entry['id']}", "scening")

    hass.services.async_register("scene", "reload", _reload)

    store = ConversationStore(hass)
    hass.data.setdefault(DOMAIN, {})["_conv_store"] = store
    await store.append_message(
        SESSION_ID,
        "assistant",
        "Saved.",
        scene=_proposal("Movie Night"),
        scene_yaml=SCENE_YAML,
        scene_id=SCENE_ID,
        scene_status="saved",
    )
    await store.append_message(
        SESSION_ID,
        "assistant",
        "Here it is.",
        scene=_proposal("Film Night"),
        scene_yaml=SCENE_YAML,
        scene_status="pending",
        refine_scene_id=SCENE_ID if refine else None,
    )
    return store


async def _invoke(hass: Any) -> MagicMock:
    connection = MagicMock()
    with patch(
        "custom_components.selora_ai.websocket.scenes._require_admin",
        return_value=True,
    ):
        await _accept(
            hass,
            connection,
            {
                "id": 1,
                "type": "selora_ai/accept_scene",
                "session_id": SESSION_ID,
                "message_index": 1,
            },
        )
    return connection


@pytest.mark.asyncio
async def test_accepting_a_refinement_reports_a_replacement(hass: Any) -> None:
    """The commonest refinement is a rename, and it is not a new scene."""
    await _setup(hass, refine=True)

    connection = await _invoke(hass)

    connection.send_error.assert_not_called()
    assert connection.send_result.call_args.args[1]["replaced"] is True
    # One scene, under the new name — not a second one beside it.
    scenes = await _read_scenes(hass)
    assert [s["name"] for s in scenes] == ["[Selora AI] Film Night"]


@pytest.mark.asyncio
async def test_accepting_a_fresh_proposal_reports_a_creation(hass: Any) -> None:
    """Nothing was refined, so the panel keeps the created wording."""
    await _setup(hass, refine=False)

    connection = await _invoke(hass)

    connection.send_error.assert_not_called()
    assert connection.send_result.call_args.args[1]["replaced"] is False
    assert len(await _read_scenes(hass)) == 2
