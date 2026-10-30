"""Frigate's face recognition: switched by Selora AI, trained in Frigate."""

from __future__ import annotations

import base64
import inspect
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import yaml

from custom_components.selora_ai import _apply_destructive_actions
from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.conversation_store import ConversationStore
from custom_components.selora_ai.frigate import descriptions, faces, setup
from custom_components.selora_ai.frigate.client import FrigateClient
from custom_components.selora_ai.home_suggestions import (
    async_dismiss_home_suggestion,
    async_home_suggestions,
    get_home_suggestion_store,
)
from custom_components.selora_ai.llm_client.command_policy import (
    _DESTRUCTIVE_TOOLS,
    _pending_destructive_from_log,
)
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import (
    TOOL_ADD_FACE_PHOTO,
    TOOL_GET_FACE_RECOGNITION,
    TOOL_SET_FACE_RECOGNITION,
)
from custom_components.selora_ai.selora_auth import SeloraAuthContext
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import COMMAND_TOOL_NAMES, CONFIG_TOOL_NAMES
from custom_components.selora_ai.websocket import frigate as frigate_ws

FRIGATE = "http://frigate.local:5000"
# Where Selora OS runs Frigate for the hub (frigate/client.py SELORA_HUB_HOST).
HUB = "http://10.42.42.2:5000"


def _running(*, on: bool = False, descriptions_on: bool = False) -> dict[str, Any]:
    config: dict[str, Any] = {
        "cameras": {
            "front_door": {"objects": {"track": ["person"]}},
            "street": {"objects": {"track": ["car"]}},
        },
        "face_recognition": {"enabled": on, "model_size": "small"},
    }
    if descriptions_on:
        config["genai"] = {"selora": {"provider": "openai", "roles": ["descriptions"]}}
    return config


@pytest.fixture
def frigate(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain="frigate", data={"url": FRIGATE})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry


def _api(
    aioclient_mock: Any,
    running: dict[str, Any],
    *,
    saved_on: bool | None = None,
    library: dict[str, Any] | None = None,
    base: str = FRIGATE,
) -> None:
    saved: dict[str, Any] = {"cameras": {"front_door": {}, "street": {}}}
    if saved_on is not None:
        saved["face_recognition"] = {"enabled": saved_on}
    aioclient_mock.get(f"{base}/api/config", json=running)
    aioclient_mock.get(f"{base}/api/config/raw", text=json.dumps(yaml.safe_dump(saved)))
    aioclient_mock.get(f"{base}/api/faces", json=library or {})
    aioclient_mock.put(f"{base}/api/config/set", json={"success": True})
    aioclient_mock.post(f"{base}/api/restart", json={"success": True})


def _written(aioclient_mock: Any) -> list[dict[str, Any]]:
    return [
        c[2] if isinstance(c[2], dict) else json.loads(c[2])
        for c in aioclient_mock.mock_calls
        if str(c[1]).endswith("/api/config/set")
    ]


async def _run(hass: HomeAssistant, tool: str, arguments: dict[str, Any]) -> tuple[dict, Any]:
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    return await executor.execute(tool, arguments), executor


async def test_off_it_says_what_it_takes_and_to_ask_first(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running())

    result, _ = await _run(hass, "get_face_recognition", {})

    assert result["face_recognition"] == "off"
    assert any("no face leaves the house" in line for line in result["guide"])
    assert "only the user's yes" in result["how_to_offer"]
    # A camera that does not look for people recognizes no face.
    assert result["cameras_not_looking_for_people"] == ["street"]
    assert "face_library" not in result


async def test_on_it_lists_the_people_without_the_training_folder(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(
        aioclient_mock,
        _running(on=True),
        saved_on=True,
        library={"Alice": ["a.webp", "b.webp"], "train": ["x.webp"]},
    )

    result, _ = await _run(hass, "get_face_recognition", {})

    assert result["face_recognition"] == "on"
    assert result["people"] == [{"name": "Alice", "photos": 2}]
    # The panel opens the Face Library from this.
    assert result["face_library"] is True


async def test_on_with_nobody_says_frigate_knows_no_one(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running(on=True), saved_on=True, library={"train": ["x.webp"]})

    result, _ = await _run(hass, "get_face_recognition", {})

    assert result["people"] == []
    assert "Nobody" in result["next"]


async def test_a_yes_goes_on_a_card_that_writes_only_the_toggle(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running())

    result, executor = await _run(hass, "set_face_recognition", {"enabled": True})

    assert result["requires_approval"]
    assert "no face leaves the house" in result["destructive"]["label"]
    assert _written(aioclient_mock) == []
    (action,) = _pending_destructive_from_log(executor.call_log)
    action = {**action, "fingerprint": result["destructive"]["fingerprint"]}

    applied, errors = await _apply_destructive_actions(hass, [action])

    assert errors == []
    assert "add each person on the card" in applied[0]
    (body,) = _written(aioclient_mock)
    assert body["config_data"] == {"face_recognition": {"enabled": True}}
    assert any(str(c[1]).endswith("/api/restart") for c in aioclient_mock.mock_calls)


async def test_already_on_needs_no_card(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running(on=True), saved_on=True)

    result, _ = await _run(hass, "set_face_recognition", {"enabled": True})

    assert result["unchanged"] is True


async def test_a_toggle_changed_since_the_card_is_not_written(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running())
    result, executor = await _run(hass, "set_face_recognition", {"enabled": True})
    (action,) = _pending_destructive_from_log(executor.call_log)
    action = {**action, "fingerprint": result["destructive"]["fingerprint"]}
    aioclient_mock.clear_requests()
    _api(aioclient_mock, _running(), saved_on=True)

    _applied, errors = await _apply_destructive_actions(hass, [action])

    assert "changed since it was shown" in errors[0]
    assert _written(aioclient_mock) == []


async def test_the_suggestion_moves_from_turning_it_on_to_adding_people(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running())
    (off,) = await faces.async_face_suggestions(hass)
    assert (off["suggestion_id"], off["state"]) == ("faces:off", "off")

    aioclient_mock.clear_requests()
    hass.data[DOMAIN].pop(faces.SUGGESTION_CACHE)
    _api(aioclient_mock, _running(on=True), saved_on=True)
    (empty,) = await faces.async_face_suggestions(hass)
    assert (empty["suggestion_id"], empty["state"]) == ("faces:empty", "empty")

    aioclient_mock.clear_requests()
    hass.data[DOMAIN].pop(faces.SUGGESTION_CACHE)
    _api(aioclient_mock, _running(on=True), saved_on=True, library={"Alice": ["a.webp"]})
    assert await faces.async_face_suggestions(hass) == []


async def test_frigate_steps_show_one_at_a_time_in_order(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any, monkeypatch: Any
) -> None:
    _api(aioclient_mock, _running(descriptions_on=True))
    aioclient_mock.get(f"{FRIGATE}/api/events", json=[])
    add = {
        "suggestion_id": "frigate:camera.porch",
        "type": "add_to_frigate",
        "entities": [{"entity_id": "camera.porch", "name": "Porch"}],
        "collection": {"source": "frigate", "name": "Frigate", "ref": "frigate"},
    }

    async def _add(_hass: HomeAssistant) -> list[dict[str, Any]]:
        return [add]

    monkeypatch.setattr(setup, "async_frigate_suggestions", _add)

    def types(found: list[dict[str, Any]]) -> list[str]:
        return [s["type"] for s in found if s["collection"]["source"] == "frigate"]

    assert types(await async_home_suggestions(hass)) == ["describe_cameras"]
    await async_dismiss_home_suggestion(hass, "describe:front_door,street")
    assert types(await async_home_suggestions(hass)) == ["recognize_faces"]
    await async_dismiss_home_suggestion(hass, "faces:off")
    assert types(await async_home_suggestions(hass)) == ["add_to_frigate"]
    assert descriptions.SUGGESTION_CACHE in hass.data[DOMAIN]


def test_both_tools_are_in_both_lanes_and_the_toggle_holds_for_a_card() -> None:
    for name in ("get_face_recognition", "set_face_recognition"):
        assert name in COMMAND_TOOL_NAMES
        assert name in CONFIG_TOOL_NAMES
    assert "set_face_recognition" in _DESTRUCTIVE_TOOLS


def _hub(hass: HomeAssistant) -> MockConfigEntry:
    """The Frigate entry Selora OS sets up, at its address for the hub."""
    entry = MockConfigEntry(domain="frigate", data={"url": HUB})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry


def _restarted(aioclient_mock: Any) -> bool:
    return any(str(c[1]).endswith("/api/restart") for c in aioclient_mock.mock_calls)


async def test_the_guide_opens_with_what_it_does_and_asks_without_the_model(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running())
    store = ConversationStore(hass)
    hass.data.setdefault(DOMAIN, {})["_conv_store"] = store
    handler = inspect.unwrap(frigate_ws._handle_websocket_frigate_faces_start)
    connection = MagicMock()
    connection.user.is_admin = True

    title = "Recognize the people you know on your cameras"
    await handler(hass, connection, {"id": 1, "title": title, "language": "en-US"})

    session_id = connection.send_result.call_args[0][1]["session_id"]
    asked, opening = (await store.get_session(session_id))["messages"]
    assert (asked["role"], asked["content"]) == ("user", title)
    card = opening["faces_card"]
    assert card["question"] == "Turn on face recognition?"
    assert "no face leaves the house" in opening["content"]
    # The card says the same as the text, which the model reads later.
    assert opening["content"].startswith(card["intro"]["lead"])
    assert [p["text"] for p in card["intro"]["points"]] == opening["content"].split("\n\n")[1:-1]
    assert [a["face_answer"] for a in opening["quick_actions"]] == [
        {"enable": True},
        {"enable": False},
    ]
    assert _written(aioclient_mock) == []


async def test_already_on_the_guide_opens_on_adding_people(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running(on=True), saved_on=True)

    opening = await faces.async_start(hass, "fr")

    assert opening["card"]["people"] is True
    assert "quick_actions" not in opening
    assert opening["reply"].startswith("Ajoutez les personnes")


async def test_a_yes_turns_it_on_restarts_and_shows_the_people_card(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running())
    store = ConversationStore(hass)
    hass.data.setdefault(DOMAIN, {})["_conv_store"] = store
    session = await store.create_session()
    handler = inspect.unwrap(frigate_ws._handle_websocket_frigate_faces_answer)
    connection = MagicMock()
    connection.user.is_admin = True

    await handler(hass, connection, {"id": 1, "session_id": session["id"], "enable": True})

    said, reply = connection.send_result.call_args[0][1]["messages"]
    assert said["content"] == "Turn on face recognition"
    assert reply["faces_card"]["people"] is True
    assert reply["faces_card"]["done"] == "restarting"
    # Not a hub: Frigate's default model is left alone.
    (body,) = _written(aioclient_mock)
    assert body["config_data"] == {"face_recognition": {"enabled": True}}
    assert _restarted(aioclient_mock)
    stored = (await store.get_session(session["id"]))["messages"]
    assert [m["content"] for m in stored] == [said["content"], reply["content"]]


async def test_on_a_hub_it_runs_the_large_model(hass: HomeAssistant, aioclient_mock: Any) -> None:
    _hub(hass)
    _api(aioclient_mock, _running(), base=HUB)

    await faces.async_answer(hass, True, "en")

    (body,) = _written(aioclient_mock)
    assert body["config_data"] == {"face_recognition": {"enabled": True, "model_size": "large"}}


async def test_on_a_hub_a_model_already_chosen_is_kept(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    _hub(hass)
    saved = {"cameras": {}, "face_recognition": {"enabled": False, "model_size": "small"}}
    aioclient_mock.get(f"{HUB}/api/config/raw", text=json.dumps(yaml.safe_dump(saved)))
    aioclient_mock.put(f"{HUB}/api/config/set", json={"success": True})
    aioclient_mock.post(f"{HUB}/api/restart", json={"success": True})

    await faces.async_answer(hass, True, "en")

    (body,) = _written(aioclient_mock)
    assert body["config_data"] == {"face_recognition": {"enabled": True}}


async def test_a_frigate_that_cannot_restart_still_has_it_saved(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    saved = {"cameras": {}}
    aioclient_mock.get(f"{FRIGATE}/api/config/raw", text=json.dumps(yaml.safe_dump(saved)))
    aioclient_mock.put(f"{FRIGATE}/api/config/set", json={"success": True})
    aioclient_mock.post(
        f"{FRIGATE}/api/restart", status=400, json={"message": "Unable to restart Frigate."}
    )

    result = await faces.async_answer(hass, True, "en")

    assert result["card"]["done"] == "not_restarted"
    assert "next restart" in result["reply"]


async def test_not_now_writes_nothing_and_hides_the_suggestion(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running())

    result = await faces.async_answer(hass, False, "en")

    assert result["card"]["done"] == "declined"
    assert _written(aioclient_mock) == []
    assert not _restarted(aioclient_mock)
    assert "faces:off" in await get_home_suggestion_store(hass).ids("dismissed")


async def test_a_yes_clicked_again_later_writes_nothing(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running(on=True), saved_on=True)

    result = await faces.async_answer(hass, True, "en")

    assert "already on" in result["error"]
    assert _written(aioclient_mock) == []


async def test_the_people_card_waits_for_frigate_to_run_it(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running(), saved_on=True, library={"Alice": ["a.webp"]})

    assert await faces.async_people(hass) == {"running": False, "people": []}

    aioclient_mock.clear_requests()
    _api(aioclient_mock, _running(on=True), saved_on=True, library={"Alice": ["a.webp"]})
    assert await faces.async_people(hass) == {
        "running": True,
        "people": [{"name": "Alice", "photos": 1}],
    }


async def test_a_photo_goes_straight_to_frigate_under_the_name_typed(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running(on=True), saved_on=True)
    url = f"{FRIGATE}/api/faces/Alice%20Martin/register"
    aioclient_mock.post(url, json={"success": True})
    handler = inspect.unwrap(frigate_ws._handle_websocket_frigate_faces_add_photo)
    connection = MagicMock()
    connection.user.is_admin = True
    photo = base64.b64encode(b"\xff\xd8jpeg").decode()

    msg = {"id": 1, "name": "  Alice   Martin ", "content_type": "image/jpeg", "image": photo}
    await handler(hass, connection, msg)

    connection.send_result.assert_called_once_with(1, {"name": "Alice Martin"})
    (call,) = [c for c in aioclient_mock.mock_calls if str(c[1]) == url]
    assert isinstance(call[2], aiohttp.FormData)


async def test_a_photo_without_a_face_says_so(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running(on=True), saved_on=True)
    aioclient_mock.post(
        f"{FRIGATE}/api/faces/Alice/register",
        status=400,
        json={"success": False, "message": "No face was detected."},
    )

    result = await faces.async_add_photo(hass, "Alice", b"jpeg", "image/jpeg")

    assert result["error"] == "Frigate refused it: No face was detected."


async def test_no_photo_before_frigate_runs_it(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running(), saved_on=True)

    result = await faces.async_add_photo(hass, "Alice", b"jpeg", "image/jpeg")

    assert "wait for its restart" in result["error"]


@pytest.mark.parametrize("name", ["", "   ", "../etc", "a/b", ".hidden", "train", "x" * 41])
def test_a_name_that_cannot_be_a_library_folder_is_refused(name: str) -> None:
    assert faces.face_name(name) is None


def test_a_name_keeps_its_accents_and_spaces() -> None:
    assert faces.face_name("Zoé  Dubois") == "Zoé Dubois"


# ── Over MCP ──────────────────────────────────────────────────────────────────

EVENT = "1730000000.123-abc123"


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await mcp_dispatch._jsonrpc_dispatch(
        hass,
        "tools/call",
        {"name": tool, "arguments": arguments},
        SeloraAuthContext("admin", None, True, "ha_token"),
    )
    return json.loads(result["content"][0]["text"])


async def test_over_mcp_people_are_added_from_events_not_a_card(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running())

    result = await _mcp(hass, TOOL_GET_FACE_RECOGNITION)

    assert result["face_recognition"] == "off"
    assert "selora_add_face_photo" in result["add_people"]
    assert "card under this reply" not in json.dumps(result)


async def test_over_mcp_turning_it_on_is_two_calls(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running())

    asked = await _mcp(hass, TOOL_SET_FACE_RECOGNITION, enabled=True)

    assert asked["requires_confirmation"] is True
    assert "no face leaves the house" in asked["change"]
    assert _written(aioclient_mock) == []

    without = await _mcp(hass, TOOL_SET_FACE_RECOGNITION, enabled=True, confirmed=True)
    done = await _mcp(
        hass,
        TOOL_SET_FACE_RECOGNITION,
        enabled=True,
        confirmed=True,
        confirm_token=asked["confirm_token"],
    )

    assert "confirm_token" in without["error"]
    assert "selora_add_face_photo" in done["done"]
    (body,) = _written(aioclient_mock)
    assert body["config_data"] == {"face_recognition": {"enabled": True}}
    assert _restarted(aioclient_mock)


async def test_over_mcp_a_stale_token_changes_nothing(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    """Shown while it was off, confirmed after someone turned it on."""
    _api(aioclient_mock, _running(on=True), saved_on=True)

    result = await _mcp(
        hass,
        TOOL_SET_FACE_RECOGNITION,
        enabled=False,
        confirmed=True,
        confirm_token=faces._fingerprint(False),
    )

    assert "changed since" in result["error"]
    assert _written(aioclient_mock) == []


async def test_over_mcp_a_face_comes_from_an_event_snapshot(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running(on=True), saved_on=True)
    url = f"{FRIGATE}/api/faces/Alice/register"
    aioclient_mock.post(url, json={"success": True})
    # HA's mock response has no content_length, which get_image checks.
    snapshot = AsyncMock(return_value=(b"\xff\xd8jpeg", "image/jpeg"))

    with patch.object(FrigateClient, "get_image", snapshot):
        result = await _mcp(hass, TOOL_ADD_FACE_PHOTO, name="Alice", event_id=EVENT)

    snapshot.assert_awaited_once_with(f"/api/events/{EVENT}/snapshot.jpg")
    assert result == {"name": "Alice", "event_id": EVENT}
    (call,) = [c for c in aioclient_mock.mock_calls if str(c[1]) == url]
    assert isinstance(call[2], aiohttp.FormData)


async def test_over_mcp_an_event_without_a_snapshot_is_refused(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running(on=True), saved_on=True)
    aioclient_mock.get(f"{FRIGATE}/api/events/{EVENT}/snapshot.jpg", status=404)

    result = await _mcp(hass, TOOL_ADD_FACE_PHOTO, name="Alice", event_id=EVENT)

    assert "No snapshot" in result["error"]
    assert not any("/register" in str(c[1]) for c in aioclient_mock.mock_calls)


@pytest.mark.parametrize("event_id", ["", "../config", "abc"])
async def test_over_mcp_only_an_event_id_reaches_frigate(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any, event_id: str
) -> None:
    result = await _mcp(hass, TOOL_ADD_FACE_PHOTO, name="Alice", event_id=event_id)

    assert "Not an event id" in result["error"]
    assert aioclient_mock.mock_calls == []
