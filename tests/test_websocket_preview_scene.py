"""Tests for the scene proposal's "what changed" preview.

A refinement card shows what the scene WILL look like; the diff answers what
accepting overwrites, and the target it answers about has to be the one the
accept path resolves — a preview of a different write would be worse than no
preview at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml as pyyaml

from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.conversation_store import ConversationStore
from custom_components.selora_ai.websocket.scenes import _handle_websocket_preview_scene_write

_preview = _handle_websocket_preview_scene_write.__wrapped__

SESSION_ID = "session-1"
SCENE_ID = "selora_ai_scene_abcd1234"
SCENE_YAML = "name: Movie Night\nentities:\n  light.lounge:\n    state: 'on'\n"


def _proposal(name: str, brightness: int) -> dict[str, Any]:
    return {
        "name": name,
        "entities": {"light.lounge": {"state": "on", "brightness": brightness}},
    }


async def _write_scenes(hass: Any, entries: list[dict[str, Any]]) -> None:
    from custom_components.selora_ai.scene_utils import _write_scenes_yaml

    await hass.async_add_executor_job(
        _write_scenes_yaml, Path(hass.config.config_dir) / "scenes.yaml", entries
    )


async def _setup(
    hass: Any,
    *,
    refine_id: str | None,
    on_disk: bool = True,
) -> None:
    """A session holding a saved scene and a proposal refining it."""
    hass.states.async_set("light.lounge", "on")
    await _write_scenes(
        hass,
        [
            {
                "id": SCENE_ID,
                "name": "[Selora AI] Movie Night",
                "entities": {"light.lounge": {"state": "on", "brightness": 128}},
            }
        ]
        if on_disk
        else [],
    )

    store = ConversationStore(hass)
    hass.data.setdefault(DOMAIN, {})["_conv_store"] = store
    await store.append_message(
        SESSION_ID,
        "assistant",
        "Saved.",
        scene=_proposal("Movie Night", 128),
        scene_yaml=SCENE_YAML,
        scene_id=SCENE_ID,
        scene_status="saved",
    )
    await store.append_message(
        SESSION_ID,
        "assistant",
        "Here it is.",
        scene=_proposal("Movie Night", 66),
        scene_yaml=SCENE_YAML,
        scene_status="pending",
        refine_scene_id=refine_id,
    )


async def _invoke(hass: Any, message_index: int = 1) -> MagicMock:
    connection = MagicMock()
    with patch(
        "custom_components.selora_ai.websocket.scenes._require_admin",
        return_value=True,
    ):
        await _preview(
            hass,
            connection,
            {
                "id": 1,
                "type": "selora_ai/preview_scene_write",
                "session_id": SESSION_ID,
                "message_index": message_index,
            },
        )
    return connection


def _result(connection: MagicMock) -> dict[str, str]:
    connection.send_error.assert_not_called()
    return connection.send_result.call_args.args[1]


@pytest.mark.asyncio
async def test_refinement_compares_disk_against_what_would_be_written(hass: Any) -> None:
    """Both sides come back, and only the refined field differs."""
    await _setup(hass, refine_id=SCENE_ID)

    result = _result(await _invoke(hass))

    current = pyyaml.safe_load(result["current_yaml"])
    proposed = pyyaml.safe_load(result["proposed_yaml"])
    assert current["entities"]["light.lounge"]["brightness"] == 128
    assert proposed["entities"]["light.lounge"]["brightness"] == 66
    # The id line and the managed prefix are the writer's, on both sides, so
    # neither reads as a change.
    assert current["id"] == proposed["id"] == SCENE_ID
    assert current["name"] == proposed["name"] == "[Selora AI] Movie Night"


@pytest.mark.asyncio
async def test_a_rename_shows_as_the_only_change(hass: Any) -> None:
    """The commonest refinement, and the prefix must not double up."""
    await _setup(hass, refine_id=SCENE_ID)
    store: ConversationStore = hass.data[DOMAIN]["_conv_store"]
    session = await store.get_session(SESSION_ID)
    session["messages"][1]["scene"] = _proposal("Film Night", 128)

    result = _result(await _invoke(hass))

    assert pyyaml.safe_load(result["proposed_yaml"])["name"] == "[Selora AI] Film Night"
    assert pyyaml.safe_load(result["current_yaml"])["name"] == "[Selora AI] Movie Night"


@pytest.mark.asyncio
async def test_a_create_has_nothing_to_compare_against(hass: Any) -> None:
    """Accepting appends a scene, so there is no earlier document."""
    await _setup(hass, refine_id=None)

    assert _result(await _invoke(hass)) == {"current_yaml": "", "proposed_yaml": ""}


@pytest.mark.asyncio
async def test_a_target_outside_the_session_is_not_a_target(hass: Any) -> None:
    """Same refusal the accept path makes, reported as nothing to diff."""
    await _setup(hass, refine_id="selora_ai_scene_ffffffff")

    assert _result(await _invoke(hass)) == {"current_yaml": "", "proposed_yaml": ""}


@pytest.mark.asyncio
async def test_a_scene_deleted_since_the_proposal_is_not_a_target(hass: Any) -> None:
    """Accepting appends a fresh scene, so nothing is overwritten."""
    await _setup(hass, refine_id=SCENE_ID, on_disk=False)

    assert _result(await _invoke(hass)) == {"current_yaml": "", "proposed_yaml": ""}


@pytest.mark.asyncio
async def test_an_index_past_the_session_is_an_error(hass: Any) -> None:
    await _setup(hass, refine_id=SCENE_ID)

    connection = await _invoke(hass, message_index=9)

    connection.send_result.assert_not_called()
    assert connection.send_error.call_args.args[1] == "not_found"


@pytest.mark.asyncio
async def test_a_non_admin_is_refused(hass: Any) -> None:
    await _setup(hass, refine_id=SCENE_ID)

    connection = MagicMock()
    with patch(
        "custom_components.selora_ai.websocket.scenes._require_admin",
        return_value=False,
    ):
        await _preview(
            hass,
            connection,
            {
                "id": 1,
                "type": "selora_ai/preview_scene_write",
                "session_id": SESSION_ID,
                "message_index": 1,
            },
        )
    connection.send_result.assert_not_called()
