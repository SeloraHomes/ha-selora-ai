"""Fixing how Frigate analyses a camera: the card, and the home suggestion."""

from __future__ import annotations

import copy
import inspect
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import yaml

from custom_components.selora_ai import _apply_destructive_actions
from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.conversation_store import ConversationStore
from custom_components.selora_ai.frigate import repair
from custom_components.selora_ai.frigate import setup as frigate_setup
from custom_components.selora_ai.home_suggestions import async_home_suggestions
from custom_components.selora_ai.llm_client.command_policy import (
    _pending_destructive_from_log,
)
from custom_components.selora_ai.mcp_server.access import FRIGATE_TOOLS
from custom_components.selora_ai.mcp_server.names import TOOL_FIX_FRIGATE_CAMERA
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import (
    COMMAND_TOOL_NAMES,
    CONFIG_TOOL_NAMES,
    FRIGATE_TOOL_NAMES,
)
from custom_components.selora_ai.websocket import frigate as frigate_ws

FRIGATE = "http://frigate.local:5000"
LOGIN = "camuser:s3cret-pass"
RESTREAM = "rtsp://127.0.0.1:8554/{}"
MAIN_PATH = "192.168.2.50:554/h264Preview_01_main"
SUB_PATH = "192.168.2.50:554/h264Preview_01_sub"


def _camera(inputs: list[dict[str, Any]], detect: tuple[int, int]) -> dict[str, Any]:
    return {
        "friendly_name": "Living Room",
        "ffmpeg": {"inputs": inputs},
        "detect": {"width": detect[0], "height": detect[1]},
    }


def _single(detect: tuple[int, int] = (1280, 720)) -> dict[str, Any]:
    """One input, recording and detecting: the main stream, through go2rtc."""
    return _camera(
        [
            {
                "path": RESTREAM.format("living_room"),
                "input_args": "preset-rtsp-restream",
                "roles": ["record", "detect"],
            }
        ],
        detect,
    )


def _configs(camera: dict[str, Any], streams: dict[str, str]) -> tuple[dict, dict]:
    """What Frigate reports (logins masked) and what it saved (logins kept)."""

    def config(login: str) -> dict[str, Any]:
        return {
            "cameras": {"living_room": copy.deepcopy(camera)},
            "go2rtc": {"streams": {k: [f"rtsp://{login}@{path}"] for k, path in streams.items()}},
        }

    return config("*:*"), config(LOGIN)


def _api(aioclient_mock: Any, shown: dict, saved: dict) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", json=shown)
    # Frigate answers the raw file as a JSON string.
    aioclient_mock.get(f"{FRIGATE}/api/config/raw", text=json.dumps(yaml.safe_dump(saved)))
    aioclient_mock.put(f"{FRIGATE}/api/config/set", json={"success": True})
    aioclient_mock.post(f"{FRIGATE}/api/restart", json={"success": True})


def _report(
    name: str = "living_room",
    issues: tuple[str, ...] = ("detect_stream_heavy",),
    streams: list[tuple[list[str], int, int]] | None = None,
    **stats: float,
) -> dict[str, Any]:
    """The last Health audit's report on a camera."""
    return {
        "frigate_name": name,
        "name": name.replace("_", " ").title(),
        "device_id": None,
        "entity_id": f"camera.{name}",
        "enabled": True,
        "detect": {"width": 1280, "height": 720, "fps": 5},
        "streams": [
            {"roles": roles, "width": w, "height": h, "codec": "h264", "readable": True}
            for roles, w, h in streams or [(["record", "detect"], 2560, 1440)]
        ],
        "streams_measured_at": "2026-11-24T18:00:00+00:00",
        "stats": stats,
        "issues": list(issues),
    }


@pytest.fixture
def frigate(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain="frigate", data={"url": FRIGATE})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry


@pytest.fixture
def reports() -> list[dict[str, Any]]:
    """What the last Health audit reported; tests change it in place."""
    found = [_report()]
    with patch.object(repair, "last_camera_reports", AsyncMock(return_value=found)):
        yield found


@pytest.fixture
def sizes() -> dict[str, tuple[int, int]]:
    """The picture each stream answers over RTSP, by URL; none when absent."""
    found: dict[str, tuple[int, int]] = {}

    async def size(url: str) -> tuple[int, int] | None:
        return found.get(url)

    with patch.object(repair, "async_stream_size", side_effect=size):
        yield found


def _reolink(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, streams: dict[str, str]) -> None:
    """A Reolink camera in Home Assistant, one entity per stream."""
    entry = MockConfigEntry(domain="reolink")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("reolink", "lumus")}, name="Lumus"
    )
    sources: dict[str, str] = {}
    for kind, url in streams.items():
        entity = er.async_get(hass).async_get_or_create(
            "camera", "reolink", f"lumus_{kind}", device_id=device.id, translation_key=kind
        )
        hass.states.async_set(entity.entity_id, "idle")
        sources[entity.entity_id] = url

    async def source(_hass: HomeAssistant, entity_id: str) -> str | None:
        return sources.get(entity_id)

    monkeypatch.setattr("custom_components.selora_ai.frigate.cameras._async_stream_source", source)


async def _preview(hass: HomeAssistant) -> tuple[dict, list[dict]]:
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    result = await executor.execute("fix_frigate_camera", {"camera": "Living Room"})
    return result, _pending_destructive_from_log(executor.call_log)


async def _confirm(hass: HomeAssistant, action: dict) -> tuple[list[str], list[str]]:
    with patch.object(frigate_setup, "async_reload_when_back", AsyncMock()):
        return await _apply_destructive_actions(hass, [action])


def _written(aioclient_mock: Any) -> list[dict[str, Any]]:
    return [
        (c[2] if isinstance(c[2], dict) else json.loads(c[2]))["config_data"]
        for c in aioclient_mock.mock_calls
        if str(c[1]).endswith("/api/config/set")
    ]


async def test_detection_moves_to_the_substream_home_assistant_has(
    hass: HomeAssistant,
    frigate: MockConfigEntry,
    aioclient_mock: Any,
    reports: list,
    sizes: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _api(aioclient_mock, *_configs(_single(), {"living_room": MAIN_PATH}))
    _reolink(
        hass,
        monkeypatch,
        {"main": f"rtsp://admin:pw@{MAIN_PATH}", "sub": f"rtsp://admin:pw@{SUB_PATH}"},
    )
    sizes[f"rtsp://admin:pw@{SUB_PATH}"] = (640, 360)

    result, [action] = await _preview(hass)

    assert result["destructive"]["label"] == (
        "Restart Selora Vision so Living Room is analysed from its 640×360 substream "
        "instead of its 2560×1440 main stream"
    )
    assert _written(aioclient_mock) == []

    applied, errors = await _confirm(hass, action)

    assert errors == []
    assert applied == [
        "Done: Living Room is analysed from its 640×360 substream instead of its "
        "2560×1440 main stream. Selora Vision is restarting, about a minute."
    ]
    assert _written(aioclient_mock) == [
        {
            "go2rtc": {"streams": {"living_room_sub": [f"rtsp://admin:pw@{SUB_PATH}"]}},
            "cameras": {
                "living_room": {
                    "ffmpeg": {
                        "inputs": [
                            {
                                "path": RESTREAM.format("living_room"),
                                "input_args": "preset-rtsp-restream",
                                "roles": ["record"],
                            },
                            {
                                "path": RESTREAM.format("living_room_sub"),
                                "input_args": "preset-rtsp-restream",
                                "roles": ["detect"],
                            },
                        ]
                    },
                    "detect": {"width": 640, "height": 360},
                }
            },
        }
    ]
    assert any(str(c[1]).endswith("/api/restart") for c in aioclient_mock.mock_calls)


async def test_without_home_assistant_the_brands_substream_keeps_frigates_login(
    hass: HomeAssistant,
    frigate: MockConfigEntry,
    aioclient_mock: Any,
    reports: list,
    sizes: dict,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    _api(aioclient_mock, *_configs(_single(), {"living_room": MAIN_PATH}))
    sizes[f"rtsp://{LOGIN}@{SUB_PATH}"] = (896, 512)

    result, [action] = await _preview(hass)
    applied, errors = await _confirm(hass, action)

    assert errors == []
    assert "from its 896×512 substream" in result["destructive"]["label"]
    [sent] = _written(aioclient_mock)
    assert sent["go2rtc"]["streams"]["living_room_sub"] == [f"rtsp://{LOGIN}@{SUB_PATH}"]
    assert sent["cameras"]["living_room"]["detect"] == {"width": 896, "height": 512}
    assert "s3cret" not in json.dumps([result, applied]) + caplog.text


async def test_a_guessed_substream_that_does_not_answer_is_not_offered(
    hass: HomeAssistant,
    frigate: MockConfigEntry,
    aioclient_mock: Any,
    reports: list,
    sizes: dict,
) -> None:
    _api(aioclient_mock, *_configs(_single(), {"living_room": MAIN_PATH}))

    result, actions = await _preview(hass)

    assert actions == []
    assert result["unchanged"] is True
    assert "found no substream" in result["how_to_fix"][0]


async def test_a_substream_set_too_large_is_the_cameras_to_lower(
    hass: HomeAssistant,
    frigate: MockConfigEntry,
    aioclient_mock: Any,
    reports: list,
    sizes: dict,
) -> None:
    camera = _camera(
        [
            {"path": RESTREAM.format("living_room"), "roles": ["record"]},
            {"path": RESTREAM.format("living_room_sub"), "roles": ["detect"]},
        ],
        (1280, 720),
    )
    _api(
        aioclient_mock,
        *_configs(camera, {"living_room": MAIN_PATH, "living_room_sub": SUB_PATH}),
    )
    reports[0] = _report(streams=[(["record"], 3840, 2160), (["detect"], 2560, 1440)])

    result, actions = await _preview(hass)

    assert actions == []
    assert result["how_to_fix"] == [
        "Its substream is 2560×1440, so the hub decodes every frame of it in full: "
        "lower it to 720p in the camera's own settings (its app or web page). "
        "Nothing in Selora Vision needs changing after."
    ]


async def test_detection_above_720p_is_scaled_down(
    hass: HomeAssistant,
    frigate: MockConfigEntry,
    aioclient_mock: Any,
    reports: list,
    sizes: dict,
) -> None:
    _api(aioclient_mock, *_configs(_single((1920, 1080)), {"living_room": MAIN_PATH}))
    reports[0] = _report(
        issues=("detect_above_720p",), streams=[(["record", "detect"], 1920, 1080)]
    )

    result, [action] = await _preview(hass)
    await _confirm(hass, action)

    assert result["destructive"]["label"] == (
        "Restart Selora Vision so Living Room is analysed at 1280×720 instead of 1920×1080"
    )
    assert _written(aioclient_mock) == [
        {"cameras": {"living_room": {"detect": {"width": 1280, "height": 720}}}}
    ]


async def test_a_camera_changed_since_the_card_is_not_changed(
    hass: HomeAssistant,
    frigate: MockConfigEntry,
    aioclient_mock: Any,
    reports: list,
    sizes: dict,
) -> None:
    _api(aioclient_mock, *_configs(_single(), {"living_room": MAIN_PATH}))
    sizes[f"rtsp://{LOGIN}@{SUB_PATH}"] = (640, 360)
    _result, [action] = await _preview(hass)
    # Someone gave it a substream in Frigate meanwhile.
    sizes[f"rtsp://{LOGIN}@{SUB_PATH}"] = (1280, 720)

    applied, errors = await _confirm(hass, action)

    assert applied == []
    assert errors == [f"{action['label']}: The camera changed since it was shown; ask again."]
    assert _written(aioclient_mock) == []


async def test_a_failing_camera_is_the_first_suggestion_and_names_its_victims(
    hass: HomeAssistant,
    frigate: MockConfigEntry,
    aioclient_mock: Any,
    reports: list,
    sizes: dict,
) -> None:
    _api(aioclient_mock, *_configs(_single(), {"living_room": MAIN_PATH}))
    sizes[f"rtsp://{LOGIN}@{SUB_PATH}"] = (640, 360)
    reports.append(
        _report(
            "reolink_kitchen",
            ("skipping_frames",),
            [(["detect"], 640, 360)],
            skipped_fps=0.8,
        )
    )
    quiet = AsyncMock(return_value=[])
    with (
        patch("custom_components.selora_ai.frigate.descriptions.async_describe_suggestions", quiet),
        patch("custom_components.selora_ai.frigate.faces.async_face_suggestions", quiet),
        patch("custom_components.selora_ai.frigate.setup.async_frigate_suggestions", quiet),
        patch("custom_components.selora_ai.frigate.ideas.async_camera_ideas", quiet),
    ):
        first, *_rest = await async_home_suggestions(hass)

        assert first["type"] == "fix_camera"
        assert first["score"] == 1.0
        assert first["collection"]["ref"] == "living_room"
        assert first["camera_report"] == {
            "issues": ["detect_stream_heavy"],
            "analysed": [1280, 720],
            "stream": [2560, 1440],
            "records_too": True,
            "skipped_fps": None,
        }
        # Fixing the living room is what helps the kitchen: one row, naming it.
        assert first["members"] == [
            {"entity_id": "camera.reolink_kitchen", "name": "Reolink Kitchen"}
        ]
        assert [s["type"] for s in _rest].count("fix_camera") == 0

        _result, [action] = await _preview(hass)
        await _confirm(hass, action)

        # Fixed: the row is gone until an audit finds the camera failing again.
        assert all(s["type"] != "fix_camera" for s in await async_home_suggestions(hass))


async def test_a_camera_falling_behind_alone_is_its_own_row(
    hass: HomeAssistant, frigate: MockConfigEntry, reports: list
) -> None:
    reports[0] = _report("reolink_kitchen", ("skipping_frames",), skipped_fps=0.8)

    [row] = await repair.async_fix_camera_suggestions(hass)

    assert row["collection"]["ref"] == "reolink_kitchen"
    assert "members" not in row


async def test_no_row_without_frigate(hass: HomeAssistant, reports: list) -> None:
    assert await repair.async_fix_camera_suggestions(hass) == []


async def test_the_suggestion_opens_the_card_without_the_model(
    hass: HomeAssistant,
    frigate: MockConfigEntry,
    aioclient_mock: Any,
    reports: list,
    sizes: dict,
) -> None:
    _api(aioclient_mock, *_configs(_single(), {"living_room": MAIN_PATH}))
    sizes[f"rtsp://{LOGIN}@{SUB_PATH}"] = (640, 360)
    reports[0] = _report(issues=("detect_stream_heavy", "high_cpu"))
    store = ConversationStore(hass)
    hass.data.setdefault(DOMAIN, {})["_conv_store"] = store
    handler = inspect.unwrap(frigate_ws._handle_websocket_frigate_fix_start)
    connection = MagicMock()
    connection.user.is_admin = True
    connection.user.id = "test-user"

    title = "Fix how Selora Vision watches Living Room"
    await handler(
        hass, connection, {"id": 1, "camera": "living_room", "title": title, "language": "en"}
    )

    session_id = connection.send_result.call_args[0][1]["session_id"]
    asked, card = (await store.get_session(session_id))["messages"]
    assert (asked["role"], asked["content"]) == ("user", title)
    approval = card["command_approval"]
    assert approval["approval_kind"] == "destructive"
    [action] = approval["actions"]
    assert (action["kind"], action["target_id"]) == ("frigate_fix", "living_room")
    # What only the user can do rides on the card.
    assert "motion mask" in approval["why"]
    assert card["approval_status"] == "pending"


async def test_the_tool_is_offered_with_frigate_in_chat_and_over_mcp() -> None:
    assert "fix_frigate_camera" in FRIGATE_TOOL_NAMES
    assert "fix_frigate_camera" in COMMAND_TOOL_NAMES
    assert "fix_frigate_camera" in CONFIG_TOOL_NAMES
    assert TOOL_FIX_FRIGATE_CAMERA in FRIGATE_TOOLS


async def test_a_wyze_detects_on_its_stream1(
    hass: HomeAssistant,
    frigate: MockConfigEntry,
    aioclient_mock: Any,
    reports: list,
    sizes: dict,
) -> None:
    """Living Room on a real hub: Wyze's own RTSPS, one stream recording and
    detecting at 2560×1440, with a 640×360 picture on stream1."""
    main = "192.168.2.134:322/stream0"

    def config(login: str) -> dict[str, Any]:
        return {
            "cameras": {"living_room": _single()},
            "go2rtc": {
                "streams": {"living_room": [f"ffmpeg:rtsps://{login}@{main}#video=copy#audio=aac"]}
            },
        }

    _api(aioclient_mock, config("*:*"), config(LOGIN))
    sizes[f"rtsps://{LOGIN}@192.168.2.134:322/stream1"] = (640, 360)

    result, [action] = await _preview(hass)
    await _confirm(hass, action)

    assert "from its 640×360 substream" in result["destructive"]["label"]
    [sent] = _written(aioclient_mock)
    # Read through FFmpeg like its main stream, so Safari's live view keeps working.
    assert sent["go2rtc"]["streams"]["living_room_sub"] == [
        f"ffmpeg:rtsps://{LOGIN}@192.168.2.134:322/stream1#video=copy#audio=aac"
    ]
    assert sent["cameras"]["living_room"]["detect"] == {"width": 640, "height": 360}
