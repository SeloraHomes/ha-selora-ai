"""Pointing a Frigate camera at its new address, behind a confirmation card."""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import yaml

from custom_components.selora_ai import _apply_destructive_actions
from custom_components.selora_ai.frigate import moving
from custom_components.selora_ai.frigate import setup as frigate_setup
from custom_components.selora_ai.llm_client.command_policy import (
    _pending_destructive_from_log,
)
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import COMMAND_TOOL_NAMES, CONFIG_TOOL_NAMES

FRIGATE = "http://frigate.local:5000"
LOGIN = "camuser:s3cret-pass"
RESTREAM = "rtsp://127.0.0.1:8554/{}"


def _configs(host: str = "192.168.2.113") -> tuple[dict[str, Any], dict[str, Any]]:
    """What Frigate reports (logins masked) and what it saved (logins kept)."""

    def config(login: str) -> dict[str, Any]:
        return {
            "cameras": {
                "wyze_cam_v3": {
                    "friendly_name": "Wyze Cam v3",
                    "ffmpeg": {"inputs": [{"path": RESTREAM.format("wyze_cam_v3")}]},
                },
                "front_door": {
                    "ffmpeg": {
                        "inputs": [
                            {"path": f"rtsp://{login}@192.168.2.98:554/main", "roles": ["detect"]}
                        ]
                    },
                    "onvif": {"host": "192.168.2.98", "port": 8000},
                },
            },
            "go2rtc": {
                "streams": {
                    "wyze_cam_v3": [f"rtspx://{login}@{host}:322/stream0"],
                    "front_door": [f"rtsp://{login}@192.168.2.98:554/main"],
                }
            },
        }

    return config("*:*"), config(LOGIN)


@pytest.fixture
def frigate(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain="frigate", data={"url": FRIGATE})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry


def _api(aioclient_mock: Any, host: str = "192.168.2.113", *, restart: int = 200) -> None:
    shown, saved = _configs(host)
    aioclient_mock.get(f"{FRIGATE}/api/config", json=shown)
    # Frigate answers the raw file as a JSON string.
    aioclient_mock.get(f"{FRIGATE}/api/config/raw", text=json.dumps(yaml.safe_dump(saved)))
    aioclient_mock.put(f"{FRIGATE}/api/config/set", json={"success": True})
    aioclient_mock.post(f"{FRIGATE}/api/restart", status=restart, json={"message": "Unable"})


async def _card(hass: HomeAssistant, arguments: dict[str, Any]) -> tuple[dict, dict]:
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    result = await executor.execute("move_frigate_camera", arguments)
    assert result.get("requires_approval"), result
    (action,) = _pending_destructive_from_log(executor.call_log)
    return result, {**action, "fingerprint": result["destructive"]["fingerprint"]}


async def _confirm(hass: HomeAssistant, action: dict) -> tuple[list[str], list[str]]:
    with patch.object(frigate_setup, "async_reload_when_back", AsyncMock()):
        return await _apply_destructive_actions(hass, [action])


def _written(aioclient_mock: Any) -> list[dict[str, Any]]:
    return [
        (c[2] if isinstance(c[2], dict) else json.loads(c[2]))["config_data"]
        for c in aioclient_mock.mock_calls
        if str(c[1]).endswith("/api/config/set")
    ]


async def test_a_camera_is_moved_on_a_card_keeping_its_login(
    hass: HomeAssistant,
    frigate: MockConfigEntry,
    aioclient_mock: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    _api(aioclient_mock)

    result, action = await _card(hass, {"camera": "Wyze Cam v3", "host": "192.168.2.205"})

    assert result["destructive"]["label"] == (
        "Restart Frigate so Wyze Cam v3 is read from 192.168.2.205 instead of 192.168.2.113"
    )
    assert _written(aioclient_mock) == []

    applied, errors = await _confirm(hass, action)

    assert errors == []
    assert applied == [
        "Frigate now reads Wyze Cam v3 from 192.168.2.205; it is restarting, about a minute."
    ]
    assert _written(aioclient_mock) == [
        {"go2rtc": {"streams": {"wyze_cam_v3": [f"rtspx://{LOGIN}@192.168.2.205:322/stream0"]}}}
    ]
    assert any(str(c[1]).endswith("/api/restart") for c in aioclient_mock.mock_calls)
    assert "s3cret" not in json.dumps([result, applied]) + caplog.text


async def test_a_camera_read_directly_moves_its_inputs_and_onvif_too(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock)
    _result, action = await _card(
        hass, {"camera": "front_door", "host": "192.168.2.99", "port": 8554}
    )

    await _confirm(hass, action)

    (sent,) = _written(aioclient_mock)
    assert sent["go2rtc"]["streams"]["front_door"] == [f"rtsp://{LOGIN}@192.168.2.99:8554/main"]
    camera = sent["cameras"]["front_door"]
    assert camera["ffmpeg"]["inputs"] == [
        {"path": f"rtsp://{LOGIN}@192.168.2.99:8554/main", "roles": ["detect"]}
    ]
    assert camera["onvif"] == {"host": "192.168.2.99"}


async def test_the_whole_stream_address_can_be_given(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock)

    result, _action = await _card(
        hass, {"camera": "wyze_cam_v3", "host": "rtsps://192.168.2.205:322/stream0"}
    )

    assert "read from 192.168.2.205 instead" in result["destructive"]["label"]


async def test_the_address_it_already_has_needs_no_card(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock)

    result = await ToolExecutor(hass, MagicMock(), is_admin=True).execute(
        "move_frigate_camera", {"camera": "wyze_cam_v3", "host": "192.168.2.113"}
    )

    assert result["unchanged"] is True


@pytest.mark.parametrize(
    "arguments",
    [
        {"host": "8.8.8.8"},
        {"host": "cam.example.com"},
        {"host": "a b"},
        {"host": "10.0.0.2", "port": 70000},
    ],
)
async def test_an_address_off_the_network_or_malformed_is_refused(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any, arguments: dict
) -> None:
    _api(aioclient_mock)

    result = await ToolExecutor(hass, MagicMock(), is_admin=True).execute(
        "move_frigate_camera", {"camera": "wyze_cam_v3", **arguments}
    )

    assert "error" in result


async def test_a_camera_changed_since_the_card_is_not_written(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock)
    _result, action = await _card(hass, {"camera": "wyze_cam_v3", "host": "192.168.2.205"})
    aioclient_mock.clear_requests()
    _api(aioclient_mock, host="192.168.2.150")

    _applied, errors = await _confirm(hass, action)

    assert "changed since it was shown" in errors[0]
    assert _written(aioclient_mock) == []


async def test_a_frigate_that_cannot_restart_says_the_move_waits(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, restart=500)
    _result, action = await _card(hass, {"camera": "wyze_cam_v3", "host": "192.168.2.205"})

    (done,), errors = await _confirm(hass, action)

    assert errors == []
    assert done.startswith("Saved: Frigate reads Wyze Cam v3 from 192.168.2.205 once it restarts")


async def test_only_an_admin_may_move_a_camera(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    result = await ToolExecutor(hass, MagicMock(), is_admin=False).execute(
        "move_frigate_camera", {"camera": "wyze_cam_v3", "host": "192.168.2.205"}
    )

    assert "error" in result
    assert aioclient_mock.call_count == 0


def test_move_frigate_camera_is_in_both_lanes() -> None:
    assert "move_frigate_camera" in COMMAND_TOOL_NAMES
    assert "move_frigate_camera" in CONFIG_TOOL_NAMES


@pytest.mark.parametrize(("old_answers", "moved"), [(False, True), (True, False)])
async def test_adding_a_camera_that_may_have_moved_asks_about_moving_it(
    hass: HomeAssistant,
    frigate: MockConfigEntry,
    aioclient_mock: Any,
    old_answers: bool,
    moved: bool,
) -> None:
    # Same port and path on another address may be the same camera, moved,
    # only when the old address is gone; even then two cameras of one brand
    # share a path, so the add card stays.
    _api(aioclient_mock)
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)

    with (
        patch.object(moving, "_answers", AsyncMock(return_value=old_answers)),
        patch(
            "custom_components.selora_ai.frigate.discovery.async_probe",
            AsyncMock(return_value=[]),
        ),
    ):
        result = await executor.execute(
            "add_cameras_to_frigate",
            {"stream_urls": ["rtsps://192.168.2.205:322/stream0"], "name": "Wyze Front"},
        )

    assert result.get("requires_approval")
    assert ("possibly_moved" in result) is moved
    if moved:
        assert result["possibly_moved"] == {"192.168.2.205": "wyze_cam_v3"}
        assert "move_frigate_camera" in result["moved_hint"]


async def test_a_port_written_in_the_stream_address_is_used(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock)
    _result, action = await _card(
        hass, {"camera": "wyze_cam_v3", "host": "rtsp://192.168.2.205:8554/stream0"}
    )

    await _confirm(hass, action)

    (sent,) = _written(aioclient_mock)
    assert sent["go2rtc"]["streams"]["wyze_cam_v3"] == [
        f"rtspx://{LOGIN}@192.168.2.205:8554/stream0"
    ]


async def test_a_restream_named_otherwise_is_followed(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    # Set up by hand in Frigate: the go2rtc stream is not named after the camera.
    shown, saved = _configs()
    for config, login in ((shown, "*:*"), (saved, LOGIN)):
        config["go2rtc"]["streams"] = {"drive": [f"rtsp://{login}@192.168.2.60:554/main"]}
        config["cameras"]["wyze_cam_v3"]["ffmpeg"]["inputs"] = [
            {"path": RESTREAM.format("drive") + "?video=copy"}
        ]
    aioclient_mock.get(f"{FRIGATE}/api/config", json=shown)
    aioclient_mock.get(f"{FRIGATE}/api/config/raw", text=json.dumps(yaml.safe_dump(saved)))
    aioclient_mock.put(f"{FRIGATE}/api/config/set", json={"success": True})
    aioclient_mock.post(f"{FRIGATE}/api/restart", json={"success": True})
    _result, action = await _card(hass, {"camera": "wyze_cam_v3", "host": "192.168.2.61"})

    await _confirm(hass, action)

    (sent,) = _written(aioclient_mock)
    assert sent == {"go2rtc": {"streams": {"drive": [f"rtsp://{LOGIN}@192.168.2.61:554/main"]}}}


async def test_a_stream_without_a_known_port_is_never_taken_for_a_move(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    shown, _saved = _configs()
    shown["go2rtc"]["streams"]["wyze_cam_v3"] = ["http://192.168.2.113/video.mjpg"]

    with patch.object(moving, "_answers", AsyncMock(return_value=False)) as answers:
        moved = await moving.async_moved_camera(shown, ("http://192.168.2.205/video.mjpg",))

    assert moved is None
    answers.assert_not_called()


async def test_a_new_port_is_refused_for_a_camera_on_several_ports(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock)

    # front_door: RTSP on 554, and ONVIF is not a source; add an HTTP one.
    shown, saved = _configs()
    for config, login in ((shown, "*:*"), (saved, LOGIN)):
        config["go2rtc"]["streams"]["front_door"].append(f"http://{login}@192.168.2.98:80/snap")
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{FRIGATE}/api/config", json=shown)

    refused = await ToolExecutor(hass, MagicMock(), is_admin=True).execute(
        "move_frigate_camera", {"camera": "front_door", "host": "192.168.2.99", "port": 8554}
    )
    moved = await ToolExecutor(hass, MagicMock(), is_admin=True).execute(
        "move_frigate_camera", {"camera": "front_door", "host": "192.168.2.99"}
    )

    assert "several ports" in refused["error"]
    assert moved.get("requires_approval")


async def test_a_new_port_is_refused_when_the_ports_left_out_differ(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    shown, _saved = _configs()
    shown["go2rtc"]["streams"]["front_door"] = [
        "rtsp://*:*@192.168.2.98/main",
        "http://*:*@192.168.2.98/snap",
    ]
    shown["cameras"]["front_door"]["ffmpeg"]["inputs"] = [{"path": "rtsp://*:*@192.168.2.98/main"}]
    aioclient_mock.get(f"{FRIGATE}/api/config", json=shown)

    result = await ToolExecutor(hass, MagicMock(), is_admin=True).execute(
        "move_frigate_camera", {"camera": "front_door", "host": "192.168.2.99", "port": 8554}
    )

    assert "several ports" in result["error"]


async def test_a_default_port_written_or_left_out_is_one_port(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    shown, _saved = _configs()
    shown["go2rtc"]["streams"]["front_door"] = ["rtsp://*:*@192.168.2.98/main"]
    aioclient_mock.get(f"{FRIGATE}/api/config", json=shown)

    result = await ToolExecutor(hass, MagicMock(), is_admin=True).execute(
        "move_frigate_camera", {"camera": "front_door", "host": "192.168.2.99", "port": 8554}
    )

    assert "read from 192.168.2.99:8554" in result["destructive"]["label"]


def test_a_moved_camera_is_on_the_result_card_without_objects() -> None:
    from custom_components.selora_ai.frigate.setup import changed_card_camera  # noqa: PLC0415

    camera = changed_card_camera({"camera": "wyze_front", "name": "Wyze Front", "done": "x"})

    assert camera == {
        "name": "Wyze Front",
        "frigate_name": "wyze_front",
        "camera_entity": "camera.wyze_front",
        "recordings_entity": "switch.wyze_front_recordings",
    }
    assert changed_card_camera({"done": "x"}) is None
