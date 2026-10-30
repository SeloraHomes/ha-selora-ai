"""Frigate's face recognition: switched by Selora AI, trained in Frigate."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import yaml

from custom_components.selora_ai import _apply_destructive_actions
from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.frigate import descriptions, faces, setup
from custom_components.selora_ai.home_suggestions import (
    async_dismiss_home_suggestion,
    async_home_suggestions,
)
from custom_components.selora_ai.llm_client.command_policy import (
    _DESTRUCTIVE_TOOLS,
    _pending_destructive_from_log,
)
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import COMMAND_TOOL_NAMES, CONFIG_TOOL_NAMES

FRIGATE = "http://frigate.local:5000"


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
) -> None:
    saved: dict[str, Any] = {"cameras": {"front_door": {}, "street": {}}}
    if saved_on is not None:
        saved["face_recognition"] = {"enabled": saved_on}
    aioclient_mock.get(f"{FRIGATE}/api/config", json=running)
    aioclient_mock.get(f"{FRIGATE}/api/config/raw", text=json.dumps(yaml.safe_dump(saved)))
    aioclient_mock.get(f"{FRIGATE}/api/faces", json=library or {})
    aioclient_mock.put(f"{FRIGATE}/api/config/set", json={"success": True})
    aioclient_mock.post(f"{FRIGATE}/api/restart", json={"success": True})


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
    assert "add each person in Frigate's Face Library" in applied[0]
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
