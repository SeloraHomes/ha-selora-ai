"""Tests for adding the home's cameras to Frigate, behind a confirmation card."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.frigate import setup as frigate_setup
from custom_components.selora_ai.frigate.cameras import (
    async_camera_candidates,
    build_config,
    clean_objects,
)
from custom_components.selora_ai.frigate.client import FrigateClient, FrigateError
from custom_components.selora_ai.frigate.discovery import NetworkCamera
from custom_components.selora_ai.frigate.onvif import OnvifAuthError, OnvifError, Profile
from custom_components.selora_ai.home_suggestions import (
    async_dismiss_home_suggestion,
    async_home_suggestions,
)
from custom_components.selora_ai.llm_client.command_policy import _pending_client_actions_from_log
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import COMMAND_TOOL_NAMES, CONFIG_TOOL_NAMES

FRIGATE = "http://frigate.local:5000"
PASSWORD = "s3cret-pass"

_EMPTY_CONFIG: dict[str, Any] = {"cameras": {}, "go2rtc": {"streams": {}}}


@pytest.fixture
def frigate(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain="frigate", data={"url": FRIGATE})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry


class _Home:
    """Camera entities on devices, with the stream source each one reports."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.sources: dict[str, str | None] = {}
        self._entry = MockConfigEntry(domain="test")
        self._entry.add_to_hass(hass)

    def camera(
        self,
        device: str,
        key: str,
        source: str | None,
        *,
        platform: str = "reolink",
        area: str | None = None,
    ) -> str:
        dev_reg = dr.async_get(self.hass)
        device_entry = dev_reg.async_get_or_create(
            config_entry_id=self._entry.entry_id,
            identifiers={("test", device)},
            name=device,
        )
        if area:
            area_id = ar.async_get(self.hass).async_get_or_create(area).id
            dev_reg.async_update_device(device_entry.id, area_id=area_id)
        entry = er.async_get(self.hass).async_get_or_create(
            "camera",
            platform,
            f"{device}_{key}",
            device_id=device_entry.id,
            translation_key=key,
            suggested_object_id=f"{device}_{key}",
        )
        self.hass.states.async_set(entry.entity_id, "idle", {"friendly_name": f"{device} {key}"})
        self.sources[entry.entity_id] = source
        return entry.entity_id

    def patch(self) -> Any:
        async def _source(_hass: HomeAssistant, entity_id: str) -> str | None:
            return self.sources[entity_id]

        return patch("custom_components.selora_ai.frigate.cameras._async_stream_source", _source)


@pytest.fixture
def home(hass: HomeAssistant) -> Any:
    h = _Home(hass)
    with h.patch():
        yield h


async def test_main_and_substream_are_told_apart(hass: HomeAssistant, home: _Home) -> None:
    main = home.camera("Porch", "main", f"rtsp://admin:{PASSWORD}@192.168.1.20:554/main")
    sub = home.camera("Porch", "sub", f"rtsp://admin:{PASSWORD}@192.168.1.20:554/sub")
    home.camera("Porch", "snapshots_sub", "http://192.168.1.20/snap.jpg")

    (camera,), skipped = await async_camera_candidates(hass, _EMPTY_CONFIG)

    assert skipped == []
    assert camera.camera == "porch"
    assert (camera.main.entity_id, camera.sub and camera.sub.entity_id) == (main, sub)


async def test_unifi_rtsps_is_read_unverified_without_srtp(
    hass: HomeAssistant, home: _Home
) -> None:
    home.camera("Garage", "high", "rtsps://10.0.0.2:7441/abc?enableSrtp", platform="unifiprotect")

    (camera,), _ = await async_camera_candidates(hass, _EMPTY_CONFIG)

    assert camera.main.url == "rtspx://10.0.0.2:7441/abc"
    assert camera.sub is None


async def test_cameras_frigate_cannot_use_are_skipped_with_a_reason(
    hass: HomeAssistant, home: _Home
) -> None:
    home.camera("Doorbell", "main", "rtsps://stream.googleapis.com:443/x", platform="nest")
    home.camera("Blink", "main", None, platform="blink")
    home.camera("Shed", "main", f"rtsp://u:{PASSWORD}@192.168.1.30/live")
    frigate_config = {
        "cameras": {"shed": {"ffmpeg": {"inputs": [{"path": "rtsp://*:*@192.168.1.30/live"}]}}},
        "go2rtc": {"streams": {}},
    }

    candidates, skipped = await async_camera_candidates(hass, frigate_config)

    assert candidates == []
    assert {s.name: s.reason for s in skipped} == {
        "Doorbell": "it streams from the internet, not your network",
        "Blink": "it has no stream Frigate can read",
        "Shed": "it is already in Frigate",
    }


async def test_a_source_go2rtc_already_wraps_counts_as_in_frigate(
    hass: HomeAssistant, home: _Home
) -> None:
    home.camera("Yard", "main", "rtsp://192.168.1.40:554/h264")
    frigate_config = {
        "cameras": {},
        "go2rtc": {"streams": {"yard": ["ffmpeg:rtsp://*:*@192.168.1.40/h264#video=copy"]}},
    }

    candidates, skipped = await async_camera_candidates(hass, frigate_config)

    assert candidates == [] and skipped[0].reason == "it is already in Frigate"


async def test_frigate_and_ignored_cameras_are_not_candidates(
    hass: HomeAssistant, home: _Home
) -> None:
    home.camera("Porch", "main", "rtsp://192.168.1.20/main", platform="frigate")

    assert await async_camera_candidates(hass, _EMPTY_CONFIG) == ([], [])


async def test_names_do_not_collide_with_frigate_cameras(hass: HomeAssistant, home: _Home) -> None:
    home.camera("Porch", "main", "rtsp://192.168.1.20/main")

    (camera,), _ = await async_camera_candidates(
        hass, {"cameras": {"porch": {}}, "go2rtc": {"streams": {}}}
    )

    assert camera.camera == "porch_2"


async def test_config_restreams_records_main_and_detects_on_sub(
    hass: HomeAssistant, home: _Home
) -> None:
    home.camera("Porch", "main", f"rtsp://admin:{PASSWORD}@192.168.1.20/main")
    home.camera("Porch", "sub", f"rtsp://admin:{PASSWORD}@192.168.1.20/sub")
    candidates, _ = await async_camera_candidates(hass, _EMPTY_CONFIG)

    config = build_config(candidates, ["person"], True, 7)

    assert config["go2rtc"]["streams"] == {
        "porch": [f"rtsp://admin:{PASSWORD}@192.168.1.20/main"],
        "porch_sub": [f"rtsp://admin:{PASSWORD}@192.168.1.20/sub"],
    }
    porch = config["cameras"]["porch"]
    assert [(i["path"], i["roles"]) for i in porch["ffmpeg"]["inputs"]] == [
        ("rtsp://127.0.0.1:8554/porch", ["record"]),
        ("rtsp://127.0.0.1:8554/porch_sub", ["detect"]),
    ]
    assert porch["detect"] == {"enabled": True}
    assert porch["record"]["alerts"] == {"retain": {"days": 7}}


async def test_a_single_stream_detects_scaled_down(hass: HomeAssistant, home: _Home) -> None:
    home.camera("Yard", "main", "rtsp://192.168.1.40/live")
    candidates, _ = await async_camera_candidates(hass, _EMPTY_CONFIG)

    yard = build_config(candidates, ["person"], False, None)["cameras"]["yard"]

    assert yard["ffmpeg"]["inputs"][0]["roles"] == ["detect"]
    assert yard["detect"] == {"enabled": True, "width": 1280, "height": 720}
    assert yard["record"] == {"enabled": False}


def test_objects_are_labels_or_the_default() -> None:
    assert clean_objects(None) == ["person"]
    assert clean_objects(["Car", "person", "rm -rf", "car"]) == ["car", "person"]


# ── Chat card and confirm ─────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def no_probe() -> Any:
    """No test sends a WS-Discovery probe; the ones about network cameras
    say what it finds."""
    with patch(
        "custom_components.selora_ai.frigate.discovery.async_probe", AsyncMock(return_value=[])
    ) as probe:
        yield probe


async def _card(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    result = await executor.execute("add_cameras_to_frigate", arguments)
    assert result.get("requires_approval"), result
    (action,) = _pending_client_actions_from_log(executor.call_log)
    assert action["kind"] == "add_frigate_cameras"
    return action


async def _confirm(
    hass: HomeAssistant, action: dict[str, Any], credentials: dict[str, Any] | None = None
) -> dict[str, Any]:
    with patch.object(frigate_setup, "async_reload_when_back", AsyncMock()):
        return await frigate_setup.async_add_cameras_confirmed(
            hass, action["plan_id"], credentials or {}
        )


def _sent_config(aioclient_mock: Any) -> dict[str, Any] | None:
    sent = [c for c in aioclient_mock.mock_calls if str(c[1]).endswith("/api/config/set")]
    return sent[0][2]["config_data"] if sent else None


def _frigate_api(aioclient_mock: Any, config: dict[str, Any] | None = None) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", json=config or _EMPTY_CONFIG)
    aioclient_mock.put(f"{FRIGATE}/api/config/set", json={"success": True})
    aioclient_mock.post(f"{FRIGATE}/api/restart", json={"success": True})


async def test_ha_cameras_are_carded_without_credentials_then_written(
    hass: HomeAssistant, home: _Home, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    home.camera("Porch", "main", f"rtsp://admin:{PASSWORD}@192.168.1.20/main", area="Front")
    home.camera("Doorbell", "main", "rtsps://stream.googleapis.com/x", platform="nest")
    _frigate_api(aioclient_mock)

    action = await _card(hass, {"objects": ["person", "car"], "record_days": 10})

    assert PASSWORD not in json.dumps(action)
    (row,) = action["cameras"]
    assert (row["name"], row["area"], row["source"]) == ("Porch", "Front", "home_assistant")
    assert "needs_login" not in row
    assert action["skipped"] == [
        {"name": "Doorbell", "reason": "it streams from the internet, not your network"}
    ]
    assert _sent_config(aioclient_mock) is None

    result = await _confirm(hass, action)

    assert result == {"status": "added", "cameras": ["Porch"]}
    config = _sent_config(aioclient_mock)
    assert config["go2rtc"]["streams"]["porch"] == [f"rtsp://admin:{PASSWORD}@192.168.1.20/main"]
    assert config["cameras"]["porch"]["objects"]["track"] == ["car", "person"]
    assert any(str(c[1]).endswith("/api/restart") for c in aioclient_mock.mock_calls)
    assert "Added Porch to Frigate" in frigate_setup.added_line(hass, action["plan_id"], "en")


async def test_a_camera_changed_since_the_card_is_not_written(
    hass: HomeAssistant, home: _Home, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    porch = home.camera("Porch", "main", "rtsp://192.168.1.20/main")
    _frigate_api(aioclient_mock)
    action = await _card(hass, {})
    home.sources[porch] = "rtsp://192.168.1.99/main"

    result = await _confirm(hass, action)

    assert "not the ones that were shown" in result["error"]
    assert _sent_config(aioclient_mock) is None


async def test_a_config_frigate_refuses_is_reported(
    hass: HomeAssistant, home: _Home, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    home.camera("Porch", "main", "rtsp://192.168.1.20/main")
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_EMPTY_CONFIG)
    aioclient_mock.put(
        f"{FRIGATE}/api/config/set", status=400, json={"message": "Invalid config: detect"}
    )
    action = await _card(hass, {})

    result = await _confirm(hass, action)

    assert "Frigate refused it: Invalid config: detect" in result["error"]
    assert not any(str(c[1]).endswith("/api/restart") for c in aioclient_mock.mock_calls)


async def test_an_expired_card_is_refused(hass: HomeAssistant, frigate: MockConfigEntry) -> None:
    result = await frigate_setup.async_add_cameras_confirmed(hass, "no-such-plan", {})

    assert "expired" in result["error"]


async def test_without_the_frigate_integration_the_tool_says_so(
    hass: HomeAssistant, home: _Home
) -> None:
    result = await ToolExecutor(hass, MagicMock(), is_admin=True).execute(
        "add_cameras_to_frigate", {}
    )

    assert "Frigate is not set up" in result["error"]


async def test_named_cameras_only(
    hass: HomeAssistant, home: _Home, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    porch = home.camera("Porch", "main", "rtsp://192.168.1.20/main")
    home.camera("Yard", "main", "rtsp://192.168.1.40/live")
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_EMPTY_CONFIG)

    action = await _card(hass, {"entity_ids": [porch, "camera.nope"]})

    assert [c["name"] for c in action["cameras"]] == ["Porch"]
    assert {"name": "camera.nope", "reason": "it is not a camera Home Assistant has"} in action[
        "skipped"
    ]


# ── Network cameras: straight to Frigate ──────────────────────────────────

_GARAGE = NetworkCamera("192.168.1.60", 80, "Garage Cam", "reolink", "onvif")


def _profiles() -> list[Profile]:
    return [
        Profile("main", "MainStream", 2560, 1440, "rtsp://192.168.1.60:554/main"),
        Profile("sub", "SubStream", 640, 360, "rtsp://192.168.1.60:554/sub"),
    ]


async def test_a_network_camera_asks_for_its_login_then_is_added(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any, no_probe: AsyncMock
) -> None:
    no_probe.return_value = [_GARAGE]
    _frigate_api(aioclient_mock)

    action = await _card(hass, {})
    (row,) = action["cameras"]
    assert row == {
        "key": "192.168.1.60",
        "source": "network",
        "name": "Garage Cam",
        "address": "192.168.1.60",
        "brand": "reolink",
        "needs_login": True,
    }

    with patch.object(
        frigate_setup.OnvifCamera, "async_profiles", AsyncMock(return_value=_profiles())
    ) as profiles:
        result = await _confirm(
            hass, action, {"192.168.1.60": {"username": "admin", "password": PASSWORD}}
        )

    profiles.assert_awaited_once_with("admin", PASSWORD)
    assert result == {"status": "added", "cameras": ["Garage Cam"]}
    config = _sent_config(aioclient_mock)
    assert config["go2rtc"]["streams"] == {
        "garage_cam": [f"rtsp://admin:{PASSWORD}@192.168.1.60:554/main"],
        "garage_cam_sub": [f"rtsp://admin:{PASSWORD}@192.168.1.60:554/sub"],
    }


async def test_a_refused_login_writes_nothing_and_names_the_camera(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any, no_probe: AsyncMock
) -> None:
    no_probe.return_value = [_GARAGE]
    _frigate_api(aioclient_mock)
    action = await _card(hass, {})

    with patch.object(
        frigate_setup.OnvifCamera, "async_profiles", AsyncMock(side_effect=OnvifAuthError("no"))
    ):
        result = await _confirm(
            hass, action, {"192.168.1.60": {"username": "admin", "password": "wrong"}}
        )

    assert result["camera_errors"] == {
        "192.168.1.60": "The camera refused this username or password."
    }
    assert _sent_config(aioclient_mock) is None
    assert "wrong" not in json.dumps(result)


async def test_a_reolink_without_onvif_uses_its_known_streams(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any, no_probe: AsyncMock
) -> None:
    no_probe.return_value = [_GARAGE]
    _frigate_api(aioclient_mock)
    action = await _card(hass, {})

    with patch.object(
        frigate_setup.OnvifCamera, "async_profiles", AsyncMock(side_effect=OnvifError("off"))
    ):
        await _confirm(hass, action, {"192.168.1.60": {"username": "admin", "password": "p"}})

    streams = _sent_config(aioclient_mock)["go2rtc"]["streams"]
    assert streams["garage_cam"] == ["rtsp://admin:p@192.168.1.60:554/h264Preview_01_main"]


async def test_an_address_named_in_chat_becomes_a_network_camera(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_EMPTY_CONFIG)

    action = await _card(hass, {"hosts": ["192.168.1.70", "8.8.8.8"]})

    assert [(c["address"], c["needs_login"]) for c in action["cameras"]] == [("192.168.1.70", True)]
    assert {"name": "8.8.8.8", "reason": "it is not an address on your network"} in action[
        "skipped"
    ]


async def test_a_network_camera_already_in_frigate_is_skipped(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any, no_probe: AsyncMock
) -> None:
    no_probe.return_value = [_GARAGE]
    aioclient_mock.get(
        f"{FRIGATE}/api/config",
        json={"cameras": {}, "go2rtc": {"streams": {"garage": ["rtsp://*:*@192.168.1.60/x"]}}},
    )

    result = await ToolExecutor(hass, MagicMock(), is_admin=True).execute(
        "add_cameras_to_frigate", {}
    )

    assert result["skipped"] == [
        {"camera": "192.168.1.60", "name": "Garage Cam", "reason": "it is already in Frigate"}
    ]


def test_the_tool_is_in_both_lanes() -> None:
    assert "add_cameras_to_frigate" in COMMAND_TOOL_NAMES
    assert "add_cameras_to_frigate" in CONFIG_TOOL_NAMES


async def test_the_client_logs_in_and_reads_the_plain_text_version(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    aioclient_mock.post(f"{FRIGATE}/api/login", cookies={"frigate_token": "jwt"})
    aioclient_mock.get(f"{FRIGATE}/api/version", text="0.16.0")

    version = await FrigateClient(hass, FRIGATE, "admin", PASSWORD).version()

    assert version == "0.16.0"
    assert aioclient_mock.mock_calls[1][3]["Authorization"] == "Bearer jwt"


async def test_an_unreachable_frigate_raises(hass: HomeAssistant, aioclient_mock: Any) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", exc=TimeoutError())

    with pytest.raises(FrigateError, match="could not be reached"):
        await FrigateClient(hass, FRIGATE).get_config()


# ── Home suggestion ───────────────────────────────────────────────────────


async def test_cameras_not_in_frigate_are_suggested_until_dismissed(
    hass: HomeAssistant, home: _Home, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    porch = home.camera("Porch", "main", "rtsp://192.168.1.20/main")
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_EMPTY_CONFIG)

    (suggestion,) = [s for s in await async_home_suggestions(hass) if s["type"] == "add_to_frigate"]
    assert suggestion["entities"] == [{"entity_id": porch, "name": "Porch"}]

    await async_dismiss_home_suggestion(hass, suggestion["suggestion_id"])
    assert not [s for s in await async_home_suggestions(hass) if s["type"] == "add_to_frigate"]


async def test_no_suggestion_without_frigate(hass: HomeAssistant, home: _Home) -> None:
    home.camera("Porch", "main", "rtsp://192.168.1.20/main")

    assert await async_home_suggestions(hass) == []


async def test_a_camera_already_in_frigate_answers_with_its_switches(
    hass: HomeAssistant, home: _Home, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    """ "Enable recording on the Front Door cam" finds Frigate's own camera
    entity; the tool must point at its recording switch, not call it unknown."""
    camera = home.camera("Front Door", "front_door", None, platform="frigate")
    device_id = er.async_get(hass).async_get(camera).device_id
    switch = er.async_get(hass).async_get_or_create(
        "switch",
        "frigate",
        "front_door_recordings",
        device_id=device_id,
        suggested_object_id="front_door_recordings",
    )
    hass.states.async_set(switch.entity_id, "off", {"friendly_name": "Front Door Recordings"})
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_EMPTY_CONFIG)

    result = await ToolExecutor(hass, MagicMock(), is_admin=True).execute(
        "add_cameras_to_frigate", {"entity_ids": [camera]}
    )

    assert result["skipped"][0]["reason"] == "it is already in Frigate"
    assert result["already_in_frigate"][camera] == [
        {"entity_id": switch.entity_id, "name": "Front Door Recordings", "state": "off"}
    ]
    assert "execute_command" in result["hint"]


# ── A switch the camera's config disables ─────────────────────────────────


def _frigate_switch(hass: HomeAssistant, camera: str, name: str) -> str:
    from homeassistant.const import EntityCategory

    entry = er.async_get(hass).async_get_or_create(
        "switch",
        "frigate",
        f"entry1:switch:{camera}_{name}",
        suggested_object_id=f"{camera}_{name}",
        entity_category=EntityCategory.CONFIG,
    )
    hass.states.async_set(entry.entity_id, "off", {"friendly_name": f"{camera} {name}"})
    return entry.entity_id


async def test_a_recording_switch_the_config_disables_is_explained(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    from custom_components.selora_ai.mcp_server.commands import _tool_execute_command

    recordings = _frigate_switch(hass, "front_door", "recordings")
    aioclient_mock.get(
        f"{FRIGATE}/api/config",
        json={
            "cameras": {"front_door": {"record": {"enabled": False, "enabled_in_config": False}}}
        },
    )

    async def _ignored(call: Any) -> None:
        hass.states.async_set(recordings, "off", {"friendly_name": "Front Door Recordings"})

    hass.services.async_register("switch", "turn_on", _ignored)
    with patch("custom_components.selora_ai.mcp_server.commands._STATE_SETTLE_TIMEOUT", 0.05):
        result = await _tool_execute_command(
            hass, {"service": "switch.turn_on", "entity_id": recordings}
        )

    assert result["unconfirmed"] == [recordings]
    assert result["unconfirmed_reasons"] == {recordings: "disabled_in_frigate_config"}


async def test_a_switch_the_config_allows_has_no_reason(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    from custom_components.selora_ai.frigate.controls import async_disabled_in_config

    recordings = _frigate_switch(hass, "front_door", "recordings")
    detect = _frigate_switch(hass, "front_door", "detect")
    aioclient_mock.get(
        f"{FRIGATE}/api/config",
        json={
            "cameras": {
                "front_door": {
                    "record": {"enabled_in_config": True},
                    "detect": {"enabled_in_config": False},
                }
            }
        },
    )

    assert await async_disabled_in_config(hass, [recordings, detect, "switch.other"]) == [detect]


# ── The panel's side: logins in, outcome recorded ─────────────────────────


async def test_the_websocket_command_takes_logins_from_admins_only(
    hass: HomeAssistant,
) -> None:
    import inspect

    from custom_components.selora_ai.websocket import frigate as frigate_ws

    handler = inspect.unwrap(frigate_ws._handle_websocket_frigate_add_cameras)
    msg = {
        "id": 1,
        "plan_id": "plan-1",
        "credentials": {"192.168.1.60": {"username": "admin", "password": PASSWORD}},
    }
    confirm = AsyncMock(return_value={"status": "added", "cameras": ["Garage Cam"]})
    with patch.object(frigate_ws, "async_add_cameras_confirmed", confirm):
        user = MagicMock()
        user.user.is_admin = False
        await handler(hass, user, msg)
        confirm.assert_not_called()

        admin = MagicMock()
        admin.user.is_admin = True
        await handler(hass, admin, msg)

    confirm.assert_awaited_once_with(hass, "plan-1", msg["credentials"])
    admin.send_result.assert_called_once_with(1, {"status": "added", "cameras": ["Garage Cam"]})


async def _frigate_card_session(hass: HomeAssistant, plan_id: str) -> tuple[Any, str]:
    from custom_components.selora_ai.const import DOMAIN
    from custom_components.selora_ai.conversation_store import ConversationStore

    store = ConversationStore(hass)
    hass.data.setdefault(DOMAIN, {})["_conv_store"] = store
    await store.append_message("sess-1", "user", "add my cameras to Frigate")
    await store.append_message(
        "sess-1",
        "assistant",
        "",
        intent="command_approval",
        command_approval={
            "proposal_id": "prop-1",
            "approval_kind": "client_action",
            "calls": [],
            "deletes": [],
            "actions": [],
            "client_actions": [{"kind": "add_frigate_cameras", "plan_id": plan_id}],
        },
    )
    return store, "sess-1"


async def _report(hass: HomeAssistant, kind: str = "add_frigate_cameras") -> MagicMock:
    import inspect

    from custom_components.selora_ai.websocket import tokens

    connection = MagicMock()
    connection.user.is_admin = True
    await inspect.unwrap(tokens._handle_websocket_client_action_result)(
        hass,
        connection,
        {
            "id": 1,
            "session_id": "sess-1",
            "proposal_id": "prop-1",
            # The panel's own claim about what it added is not what is recorded.
            "results": [{"ok": True, "kind": kind, "detail": {"cameras": ["Anything"]}}],
        },
    )
    return connection


async def test_the_conversation_records_the_cameras_the_plan_added(
    hass: HomeAssistant, home: _Home, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    home.camera("Porch", "main", "rtsp://192.168.1.20/main")
    _frigate_api(aioclient_mock)
    action = await _card(hass, {})
    await _confirm(hass, action)
    store, session_id = await _frigate_card_session(hass, action["plan_id"])

    connection = await _report(hass)

    assert connection.send_result.call_args[0][1] == {"ok": True}
    messages = (await store.get_session(session_id))["messages"]
    assert messages[-1]["content"].startswith("Added Porch to Frigate.")
    assert "Anything" not in messages[-1]["content"]


async def test_a_success_the_plan_does_not_record_is_not_recorded(
    hass: HomeAssistant, frigate: MockConfigEntry
) -> None:
    store, session_id = await _frigate_card_session(hass, "never-confirmed")

    connection = await _report(hass)

    assert connection.send_result.call_args[0][1] == {"ok": False}
    session = await store.get_session(session_id)
    assert session["messages"][1]["approval_status"] == "denied"


async def test_a_camera_given_by_stream_address_skips_onvif(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    """A Wyze Cam has no ONVIF; the address the user gave is the stream."""
    _frigate_api(aioclient_mock)

    action = await _card(
        hass,
        {
            "stream_urls": ["rtsps://olduser:leaked@192.168.2.113:322/stream0"],
            "name": "Wyze Cam3",
        },
    )
    (row,) = action["cameras"]
    assert (row["name"], row["stream"]) == ("Wyze Cam3", "rtsps://192.168.2.113:322/stream0")
    assert "leaked" not in json.dumps(action)

    onvif = AsyncMock()
    with patch.object(frigate_setup.OnvifCamera, "async_profiles", onvif):
        result = await _confirm(
            hass, action, {"192.168.2.113": {"username": "Lafou", "password": PASSWORD}}
        )

    onvif.assert_not_called()
    assert result["status"] == "added"
    streams = _sent_config(aioclient_mock)["go2rtc"]["streams"]
    # RTSPS read unverified (self-signed), with the login typed into the card.
    assert streams["wyze_cam3"] == [f"rtspx://Lafou:{PASSWORD}@192.168.2.113:322/stream0"]


async def test_a_stream_without_a_login_is_added_as_given(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _frigate_api(aioclient_mock)
    action = await _card(
        hass,
        {
            "stream_urls": [
                "rtsp://192.168.2.120:8554/garage",
                "rtsp://192.168.2.120:8554/garage_sub",
            ]
        },
    )

    await _confirm(hass, action, {"192.168.2.120": {"username": "", "password": ""}})

    streams = _sent_config(aioclient_mock)["go2rtc"]["streams"]
    assert streams["192_168_2_120"] == ["rtsp://192.168.2.120:8554/garage"]
    assert streams["192_168_2_120_sub"] == ["rtsp://192.168.2.120:8554/garage_sub"]


async def test_a_frigate_that_cannot_restart_still_keeps_the_cameras(
    hass: HomeAssistant, home: _Home, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    """Frigate run by the host answers its restart with "Unable to restart
    Frigate". The cameras are saved; they are not a failure to add."""
    home.camera("Porch", "main", "rtsp://192.168.1.20/main")
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_EMPTY_CONFIG)
    aioclient_mock.put(f"{FRIGATE}/api/config/set", json={"success": True})
    aioclient_mock.post(
        f"{FRIGATE}/api/restart", status=500, json={"message": "Unable to restart Frigate."}
    )
    action = await _card(hass, {})

    result = await _confirm(hass, action)

    assert result == {"status": "saved", "cameras": ["Porch"], "restart_failed": True}
    assert _sent_config(aioclient_mock) is not None
    assert frigate_setup.added_line(hass, action["plan_id"], "en").startswith(
        "Saved Porch in Frigate, but Frigate could not restart itself"
    )


# ── Removing a camera from Frigate ────────────────────────────────────────

_WITH_LUMUS: dict[str, Any] = {
    "cameras": {
        "front_door": {"ffmpeg": {"inputs": [{"path": "rtsp://*:*@192.168.2.98/main"}]}},
        "reolink_lumus_pro": {
            "ffmpeg": {
                "inputs": [
                    {"path": "rtsp://127.0.0.1:8554/reolink_lumus_pro"},
                    {"path": "rtsp://127.0.0.1:8554/reolink_lumus_pro_sub"},
                ]
            }
        },
    },
    "go2rtc": {
        "streams": {
            "reolink_lumus_pro": ["rtsp://*:*@192.168.2.178/h264Preview_01_main"],
            "reolink_lumus_pro_sub": ["rtsp://*:*@192.168.2.178/h264Preview_01_sub"],
        }
    },
}


async def _removal_card(hass: HomeAssistant, target: str) -> dict[str, Any]:
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    result = await executor.execute("remove_from_home", {"kind": "frigate_camera", "id": target})
    assert result.get("requires_approval"), result
    return result["delete"]


async def test_a_camera_is_removed_from_frigate_with_its_streams(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    from custom_components.selora_ai.removal_cards import async_confirm_removal

    _frigate_api(aioclient_mock, _WITH_LUMUS)

    card = await _removal_card(hass, "Reolink Lumus Pro")
    assert card["kind"] == "frigate_camera"
    assert card["target_id"] == "reolink_lumus_pro"
    assert "Remove reolink_lumus_pro from Frigate" in card["label"]

    with patch.object(frigate_setup, "async_reload_when_back", AsyncMock()):
        result = await async_confirm_removal(hass, card)

    assert result == {"removed": "reolink_lumus_pro"}
    assert _sent_config(aioclient_mock) == {
        "cameras": {"reolink_lumus_pro": None},
        "go2rtc": {"streams": {"reolink_lumus_pro": None, "reolink_lumus_pro_sub": None}},
    }


async def test_the_frigate_integrations_camera_entity_names_it(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    entry = er.async_get(hass).async_get_or_create(
        "camera", "frigate", "entry1:camera:reolink_lumus_pro", suggested_object_id="lumus"
    )
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_WITH_LUMUS)

    card = await _removal_card(hass, entry.entity_id)

    assert card["target_id"] == "reolink_lumus_pro"


async def test_a_camera_changed_since_the_removal_card_stays(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    from custom_components.selora_ai.removal_cards import async_confirm_removal

    _frigate_api(aioclient_mock, _WITH_LUMUS)
    card = await _removal_card(hass, "reolink_lumus_pro")

    result = await async_confirm_removal(hass, {**card, "fingerprint": "something-else"})

    assert "changed since" in result["error"]
    assert _sent_config(aioclient_mock) is None


async def test_an_unknown_camera_lists_frigates(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", json=_WITH_LUMUS)

    result = await ToolExecutor(hass, MagicMock(), is_admin=True).execute(
        "remove_from_home", {"kind": "frigate_camera", "id": "garage"}
    )

    assert result["frigate_cameras"] == ["front_door", "reolink_lumus_pro"]


async def test_a_removal_frigate_cannot_restart_for_still_counts(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    from custom_components.selora_ai.removal_cards import async_confirm_removal

    aioclient_mock.get(f"{FRIGATE}/api/config", json=_WITH_LUMUS)
    aioclient_mock.put(f"{FRIGATE}/api/config/set", json={"success": True})
    aioclient_mock.post(
        f"{FRIGATE}/api/restart", status=500, json={"message": "Unable to restart Frigate."}
    )
    card = await _removal_card(hass, "reolink_lumus_pro")

    result = await async_confirm_removal(hass, card)

    assert result["removed"] == "reolink_lumus_pro" and result["restart_failed"]


async def test_an_unknown_onvif_port_is_found_by_trying(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    """A camera named by address: Reolink serves ONVIF on 8000, not 80."""
    _frigate_api(aioclient_mock)
    action = await _card(hass, {"hosts": ["192.168.2.192"], "name": "E1 Zoom"})
    tried: list[int] = []

    async def _profiles(self: Any, username: str, password: str) -> list[Profile]:
        tried.append(self._device_url.split(":")[2].split("/")[0])
        if self._device_url.startswith("http://192.168.2.192:8000/"):
            return _profiles_for("192.168.2.192")
        raise OnvifError("refused")

    with patch.object(frigate_setup.OnvifCamera, "async_profiles", _profiles):
        result = await _confirm(
            hass, action, {"192.168.2.192": {"username": "admin", "password": "p"}}
        )

    assert tried == ["80", "8000"]
    assert result["status"] == "added"


async def test_a_room_taken_for_an_address_gets_the_scanned_cameras(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any, no_probe: AsyncMock
) -> None:
    no_probe.return_value = [NetworkCamera("192.168.2.192", 8000, "E1 Zoom", "reolink", "onvif")]
    aioclient_mock.get(
        f"{FRIGATE}/api/config",
        json={"cameras": {}, "go2rtc": {"streams": {"e1": ["rtsp://*:*@192.168.2.192/x"]}}},
    )
    await frigate_setup.async_scan(hass)

    with patch.object(hass.loop, "getaddrinfo", AsyncMock(side_effect=OSError)):
        result = await ToolExecutor(hass, MagicMock(), is_admin=True).execute(
            "add_cameras_to_frigate", {"hosts": ["kitchen"], "name": "E1 Zoom"}
        )

    assert {
        "camera": "kitchen",
        "name": "kitchen",
        "reason": "it is not an address on your network",
    } in result["skipped"]
    assert result["found_on_network"] == [{"name": "E1 Zoom", "address": "192.168.2.192"}]


def _profiles_for(host: str) -> list[Profile]:
    return [
        Profile("main", "MainStream", 2560, 1440, f"rtsp://{host}:554/main"),
        Profile("sub", "SubStream", 640, 360, f"rtsp://{host}:554/sub"),
    ]


async def test_a_stopped_frigate_is_not_reported_as_refusing(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Frigate's nginx answers 500 with an HTML page while Frigate is down."""
    aioclient_mock.get(
        f"{FRIGATE}/api/config", status=500, text="<html><h1>500 Internal Server Error</h1></html>"
    )

    with pytest.raises(FrigateError, match="not responding"):
        await FrigateClient(hass, FRIGATE).get_config()
