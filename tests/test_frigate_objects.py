"""Changing what a Frigate camera looks for, behind a confirmation card."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.selora_ai import _apply_destructive_actions
from custom_components.selora_ai.frigate import setup as frigate_setup
from custom_components.selora_ai.llm_client.command_policy import _pending_destructive_from_log
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import COMMAND_TOOL_NAMES, CONFIG_TOOL_NAMES

FRIGATE = "http://frigate.local:5000"


def _config(track: list[str], *, detect: bool = True) -> dict[str, Any]:
    return {
        "cameras": {
            "wyze_cam_v3": {
                "friendly_name": "Wyze Cam V3",
                "detect": {"enabled": detect},
                "objects": {"track": track},
            }
        }
    }


@pytest.fixture
def frigate(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain="frigate", data={"url": FRIGATE})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry


async def _card(hass: HomeAssistant, arguments: dict[str, Any]) -> tuple[dict, dict]:
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    result = await executor.execute("set_camera_objects", arguments)
    assert result.get("requires_approval"), result
    (action,) = _pending_destructive_from_log(executor.call_log)
    return result, {**action, "fingerprint": result["destructive"]["fingerprint"]}


def _written(aioclient_mock: Any) -> list[Any]:
    return [c[2] for c in aioclient_mock.mock_calls if str(c[1]).endswith("/api/config/set")]


async def test_a_car_is_added_on_a_card_then_written_and_frigate_restarts(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_config(["person"]))
    aioclient_mock.put(f"{FRIGATE}/api/config/set", json={"success": True})
    aioclient_mock.post(f"{FRIGATE}/api/restart", json={"success": True})

    result, action = await _card(hass, {"camera": "Wyze Cam V3", "add": ["Car"]})

    label = result["destructive"]["label"]
    assert label == "Wyze Cam V3 starts looking for car (Frigate restarts)"
    assert _written(aioclient_mock) == []

    with patch.object(frigate_setup, "async_reload_when_back", AsyncMock()):
        applied, errors = await _apply_destructive_actions(hass, [action])

    assert errors == []
    assert applied == [
        "Wyze Cam V3 now looks for car, person. Frigate is restarting, about a minute."
    ]
    (sent,) = _written(aioclient_mock)
    body = sent if isinstance(sent, dict) else json.loads(sent)
    assert body["config_data"] == {
        "cameras": {"wyze_cam_v3": {"objects": {"track": ["car", "person"]}}}
    }
    assert any(str(c[1]).endswith("/api/restart") for c in aioclient_mock.mock_calls)


async def test_a_camera_that_changed_since_the_card_is_not_written(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_config(["person"]))
    _result, action = await _card(hass, {"camera": "wyze_cam_v3", "add": ["car"]})
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_config(["person", "dog"]))

    _applied, errors = await _apply_destructive_actions(hass, [action])

    assert "changed since it was shown" in errors[0]
    assert _written(aioclient_mock) == []


async def test_what_it_already_looks_for_needs_no_card(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_config(["car", "person"]))

    result = await ToolExecutor(hass, MagicMock(), is_admin=True).execute(
        "set_camera_objects", {"camera": "wyze_cam_v3", "add": ["car"]}
    )

    assert result["unchanged"] is True
    assert "already looks for car, person" in result["note"]


async def test_a_camera_with_detection_off_says_so_on_the_card(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_config(["person"], detect=False))

    result, _action = await _card(hass, {"camera": "wyze_cam_v3", "objects": ["car"]})

    assert "detection is off" in result["destructive"]["label"]


async def test_nothing_left_to_look_for_is_refused(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_config(["person"]))

    result = await ToolExecutor(hass, MagicMock(), is_admin=True).execute(
        "set_camera_objects", {"camera": "wyze_cam_v3", "remove": ["person"]}
    )

    assert "at least one object" in result["error"]


async def test_an_unknown_camera_lists_the_ones_there_are(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_config(["person"]))

    result = await ToolExecutor(hass, MagicMock(), is_admin=True).execute(
        "set_camera_objects", {"camera": "garage", "add": ["car"]}
    )

    assert result["frigate_cameras"] == ["wyze_cam_v3"]


async def test_only_an_admin_may_change_it(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    result = await ToolExecutor(hass, MagicMock(), is_admin=False).execute(
        "set_camera_objects", {"camera": "wyze_cam_v3", "add": ["car"]}
    )

    assert "error" in result
    assert aioclient_mock.call_count == 0


def test_set_camera_objects_is_in_both_lanes() -> None:
    assert "set_camera_objects" in COMMAND_TOOL_NAMES
    assert "set_camera_objects" in CONFIG_TOOL_NAMES


@pytest.mark.parametrize(
    "arguments",
    [{"remove": ["car-1"]}, {"add": ["car!"]}, {"objects": ["Car Park"]}],
)
async def test_a_label_frigate_would_not_take_is_refused_not_dropped(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any, arguments: dict
) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_config(["person"]))

    result = await ToolExecutor(hass, MagicMock(), is_admin=True).execute(
        "set_camera_objects", {"camera": "wyze_cam_v3", **arguments}
    )

    assert result["error"].startswith("Not Frigate object labels")


async def test_a_frigate_that_cannot_restart_says_the_change_waits(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_config(["person"]))
    aioclient_mock.put(f"{FRIGATE}/api/config/set", json={"success": True})
    aioclient_mock.post(f"{FRIGATE}/api/restart", status=500, json={"message": "Unable"})
    _result, action = await _card(hass, {"camera": "wyze_cam_v3", "add": ["car"]})

    (done,), errors = await _apply_destructive_actions(hass, [action])

    assert errors == []
    assert done.startswith("Saved: Wyze Cam V3 now looks for car, person once Frigate restarts")


async def test_two_cards_confirmed_at_once_write_only_the_first(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    async def config(*_args: Any) -> Any:
        # Frigate answers later: the other confirm runs meanwhile.
        await asyncio.sleep(0)
        return AiohttpClientMockResponse("GET", "", json=_config(["person"]))

    aioclient_mock.get(f"{FRIGATE}/api/config", side_effect=config)
    aioclient_mock.put(f"{FRIGATE}/api/config/set", json={"success": True})
    aioclient_mock.post(f"{FRIGATE}/api/restart", json={"success": True})
    _r1, car = await _card(hass, {"camera": "wyze_cam_v3", "add": ["car"]})
    _r2, dog = await _card(hass, {"camera": "wyze_cam_v3", "add": ["dog"]})

    with patch.object(frigate_setup, "async_reload_when_back", AsyncMock()):
        results = await asyncio.gather(
            _apply_destructive_actions(hass, [car]), _apply_destructive_actions(hass, [dog])
        )

    assert sorted(len(applied) for applied, _errors in results) == [0, 1]
    assert len(_written(aioclient_mock)) == 1


async def test_a_change_saved_before_frigate_restarts_is_what_the_next_card_starts_from(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    # Frigate reports what it runs; the saved list runs only after a restart.
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_config(["person"]))
    aioclient_mock.put(f"{FRIGATE}/api/config/set", json={"success": True})
    aioclient_mock.post(f"{FRIGATE}/api/restart", status=500, json={"message": "Unable"})
    _result, car = await _card(hass, {"camera": "wyze_cam_v3", "add": ["car"]})
    _r2, stale = await _card(hass, {"camera": "wyze_cam_v3", "add": ["dog"]})
    await _apply_destructive_actions(hass, [car])

    _applied, errors = await _apply_destructive_actions(hass, [stale])
    after, _action = await _card(hass, {"camera": "wyze_cam_v3", "add": ["dog"]})

    assert "changed since it was shown" in errors[0]
    assert after["destructive"]["label"] == "Wyze Cam V3 starts looking for dog (Frigate restarts)"


async def test_a_list_changed_in_frigate_since_beats_the_one_saved(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_config(["person"]))
    aioclient_mock.put(f"{FRIGATE}/api/config/set", json={"success": True})
    aioclient_mock.post(f"{FRIGATE}/api/restart", status=500, json={"message": "Unable"})
    _result, car = await _card(hass, {"camera": "wyze_cam_v3", "add": ["car"]})
    await _apply_destructive_actions(hass, [car])
    # Someone set the list in Frigate and restarted it.
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_config(["cat", "person"]))

    after, _action = await _card(hass, {"camera": "wyze_cam_v3", "add": ["dog"]})

    assert after["destructive"]["label"] == "Wyze Cam V3 starts looking for dog (Frigate restarts)"


async def test_a_replacement_says_what_it_stops_looking_for(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    # "Look for cars" sent as a whole list drops person; the card must say so,
    # not bury it in a before-and-after list.
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_config(["person"]))

    result, _action = await _card(hass, {"camera": "wyze_cam_v3", "objects": ["car"]})

    assert result["destructive"]["label"] == (
        "Wyze Cam V3 starts looking for car and stops looking for person; "
        "it then looks for car only (Frigate restarts)"
    )


def test_looking_for_something_is_an_add_not_a_replacement() -> None:
    from custom_components.selora_ai.tool_registry import TOOL_SET_CAMERA_OBJECTS  # noqa: PLC0415

    assert "'Look for cars' adds (add)" in TOOL_SET_CAMERA_OBJECTS.description


async def _confirm(hass: HomeAssistant, action: dict[str, Any]) -> MagicMock:
    """Confirm *action* on its card through the resolver; returns the store."""
    from custom_components.selora_ai import _resolve_approval  # noqa: PLC0415

    approval = {"proposal_id": "p1", "approval_kind": "destructive", "actions": [action]}
    store = MagicMock()
    store.get_session = AsyncMock(
        return_value={
            "messages": [
                {
                    "intent": "command_approval",
                    "command_approval": approval,
                    "approval_status": "pending",
                }
            ]
        }
    )
    store.set_approval_status = AsyncMock()
    store.append_message = AsyncMock(return_value={"role": "assistant"})
    with patch.object(frigate_setup, "async_reload_when_back", AsyncMock()):
        await _resolve_approval(
            hass, MagicMock(), {"id": 1}, store, MagicMock(), "sess", "p1", "apply"
        )
    return store


async def test_a_confirmed_change_shows_the_camera_and_what_it_looks_for(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_config(["person"]))
    aioclient_mock.put(f"{FRIGATE}/api/config/set", json={"success": True})
    aioclient_mock.post(f"{FRIGATE}/api/restart", json={"success": True})
    _result, action = await _card(hass, {"camera": "wyze_cam_v3", "add": ["bus"]})

    store = await _confirm(hass, action)

    assert store.append_message.call_args.kwargs["frigate_result"] == {
        "action": "updated",
        "cameras": [
            {
                "name": "Wyze Cam V3",
                "frigate_name": "wyze_cam_v3",
                "camera_entity": "camera.wyze_cam_v3",
                "recordings_entity": "switch.wyze_cam_v3_recordings",
                "objects": ["bus", "person"],
            }
        ],
    }


async def test_a_change_frigate_could_not_restart_for_says_it_waits(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_config(["person"]))
    aioclient_mock.put(f"{FRIGATE}/api/config/set", json={"success": True})
    aioclient_mock.post(f"{FRIGATE}/api/restart", status=500, json={"message": "Unable"})
    _result, action = await _card(hass, {"camera": "wyze_cam_v3", "add": ["bus"]})

    store = await _confirm(hass, action)

    assert store.append_message.call_args.kwargs["frigate_result"]["action"] == "update_saved"


def test_a_card_with_more_than_frigate_changes_keeps_its_text() -> None:
    # The result card replaces the message, which would hide the rest.
    from custom_components.selora_ai import _frigate_changed_result  # noqa: PLC0415

    changed = {"camera": "porch", "name": "Porch", "looks_for": ["person"], "done": "x"}
    assert _frigate_changed_result([changed], ["x"], [])
    assert _frigate_changed_result([changed], ["x", "Renamed lamp"], []) is None
    assert _frigate_changed_result([changed], ["x"], ["Lamp: gone"]) is None
    assert _frigate_changed_result([], ["Renamed lamp"], []) is None
