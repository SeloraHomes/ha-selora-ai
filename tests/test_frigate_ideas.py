"""Tests for automation and scene ideas drawn from the home's cameras."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.frigate.ideas import async_camera_ideas
from custom_components.selora_ai.home_suggestions import (
    async_dismiss_home_suggestion,
    async_home_suggestions,
)


class _Home:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.entry = MockConfigEntry(domain="test")
        self.entry.add_to_hass(hass)

    def device(self, name: str, area: str | None = None) -> str:
        device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.entry.entry_id, identifiers={("test", name)}, name=name
        )
        if area:
            area_id = ar.async_get(self.hass).async_get_or_create(area).id
            dr.async_get(self.hass).async_update_device(device.id, area_id=area_id)
        return device.id

    def entity(
        self,
        domain: str,
        platform: str,
        unique_id: str,
        object_id: str,
        device_id: str | None = None,
        state: str = "off",
        translation_key: str | None = None,
    ) -> str:
        entry = er.async_get(self.hass).async_get_or_create(
            domain,
            platform,
            unique_id,
            suggested_object_id=object_id,
            device_id=device_id,
            translation_key=translation_key,
        )
        self.hass.states.async_set(entry.entity_id, state, {"friendly_name": object_id})
        return entry.entity_id

    def frigate_camera(self, name: str, objects: tuple[str, ...] = ("person",)) -> str:
        device = self.device(name, area="Garden")
        camera = self.entity("camera", "frigate", f"e1:camera:{name}", name, device, "streaming")
        for obj in objects:
            self.entity(
                "binary_sensor",
                "frigate",
                f"e1:occupancy_sensor:{name}_{obj}",
                f"{name}_{obj}_occupancy",
                device,
            )
        self.entity(
            "binary_sensor", "frigate", f"e1:motion_sensor:{name}", f"{name}_motion", device
        )
        for switch in ("recordings", "detect"):
            self.entity(
                "switch", "frigate", f"e1:switch:{name}_{switch}", f"{name}_{switch}", device, "on"
            )
        return camera

    def people(self) -> None:
        self.hass.states.async_set("person.anne", "home")
        self.hass.states.async_set("zone.home", "1")

    def phone(self) -> None:
        async def _noop(call: ServiceCall) -> None:
            return None

        self.hass.services.async_register("notify", "mobile_app_pixel", _noop)


@pytest.fixture
def home(hass: HomeAssistant) -> _Home:
    return _Home(hass)


def _by_kind(ideas: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {i["kind"]: i for i in ideas}


async def test_a_person_sensor_with_people_and_a_phone_suggests_an_away_alert(
    hass: HomeAssistant, home: _Home
) -> None:
    camera = home.frigate_camera("e1_zoom")
    home.people()
    home.phone()

    ideas = _by_kind(await async_camera_ideas(hass, camera))

    away = ideas["person_away"]
    assert away["idea_id"] == "idea:person_away:camera.e1_zoom"
    assert away["facts"] == {
        "trigger": "binary_sensor.e1_zoom_person_occupancy",
        "presence": "zone.home",
        "presence_empty": "0",
        "notify": "notify.mobile_app_pixel",
        "image": "/api/camera_proxy/camera.e1_zoom",
    }


async def test_without_a_phone_the_alert_uses_home_assistants_notifications(
    hass: HomeAssistant, home: _Home
) -> None:
    camera = home.frigate_camera("e1_zoom")
    home.people()

    away = _by_kind(await async_camera_ideas(hass, camera))["person_away"]

    assert away["facts"]["notify"] == "persistent_notification.create"
    # HA's own notifications take a title and a message: no snapshot.
    assert "image" not in away["facts"]


async def test_a_group_of_some_people_is_not_the_household(
    hass: HomeAssistant, home: _Home
) -> None:
    """group.kids being away is not nobody being home."""
    camera = home.frigate_camera("e1_zoom")
    hass.states.async_set("person.anne", "home")
    hass.states.async_set("person.kid", "home")
    hass.states.async_set("group.kids", "home", {"entity_id": ["person.kid"]})

    assert "person_away" not in _by_kind(await async_camera_ideas(hass, camera))

    hass.states.async_set("group.family", "home", {"entity_id": ["person.anne", "person.kid"]})
    away = _by_kind(await async_camera_ideas(hass, camera))["person_away"]
    assert away["facts"]["presence"] == "group.family"

    hass.states.async_set("zone.home", "2")
    away = _by_kind(await async_camera_ideas(hass, camera))["person_away"]
    assert (away["facts"]["presence"], away["facts"]["presence_empty"]) == ("zone.home", "0")


async def test_no_one_to_track_means_no_away_alert(hass: HomeAssistant, home: _Home) -> None:
    camera = home.frigate_camera("e1_zoom")

    assert "person_away" not in _by_kind(await async_camera_ideas(hass, camera))


async def test_a_light_in_the_cameras_area_suggests_lighting_it(
    hass: HomeAssistant, home: _Home
) -> None:
    camera = home.frigate_camera("e1_zoom")
    garden = home.device("Garden lamp", area="Garden")
    home.entity("light", "test", "garden_lamp", "garden_lamp", garden)

    light = _by_kind(await async_camera_ideas(hass, camera))["person_night_light"]

    assert light["facts"] == {
        "trigger": "binary_sensor.e1_zoom_person_occupancy",
        "light": "light.garden_lamp",
    }


async def test_animal_and_car_sensors_suggest_their_alerts(
    hass: HomeAssistant, home: _Home
) -> None:
    camera = home.frigate_camera("driveway", objects=("car", "dog"))

    ideas = _by_kind(await async_camera_ideas(hass, camera))

    assert ideas["vehicle"]["facts"]["trigger"] == "binary_sensor.driveway_car_occupancy"
    assert ideas["animal"]["facts"]["trigger"] == "binary_sensor.driveway_dog_occupancy"
    assert "person_away" not in ideas


async def test_reolinks_own_ai_sensors_count(hass: HomeAssistant, home: _Home) -> None:
    device = home.device("Lumus")
    camera = home.entity("camera", "reolink", "lumus_main", "lumus", device, "idle")
    home.entity(
        "binary_sensor", "reolink", "lumus_animal", "lumus_animal", device, translation_key="animal"
    )

    ideas = _by_kind(await async_camera_ideas(hass, camera))

    assert ideas["animal"]["facts"]["trigger"] == "binary_sensor.lumus_animal"


async def test_a_trigger_an_automation_already_uses_is_not_offered(
    hass: HomeAssistant, home: _Home
) -> None:
    camera = home.frigate_camera("e1_zoom")
    home.people()
    Path(hass.config.config_dir, "automations.yaml").write_text(
        "- id: a1\n  alias: Person seen\n  triggers:\n"
        "    - trigger: state\n      entity_id: binary_sensor.e1_zoom_person_occupancy\n"
        "      to: 'on'\n  actions: []\n"
    )

    assert await async_camera_ideas(hass, camera) == []


async def test_a_watch_scene_needs_two_frigate_cameras(hass: HomeAssistant, home: _Home) -> None:
    home.frigate_camera("e1_zoom")
    assert "watch_scene" not in _by_kind(await async_camera_ideas(hass))

    home.frigate_camera("lumus")
    scene = _by_kind(await async_camera_ideas(hass))["watch_scene"]

    assert scene["facts"]["switches"] == (
        "switch.e1_zoom_detect, switch.e1_zoom_recordings, "
        "switch.lumus_detect, switch.lumus_recordings"
    )


async def test_ideas_are_home_suggestions_until_dismissed(hass: HomeAssistant, home: _Home) -> None:
    home.frigate_camera("driveway", objects=("car",))

    rows = [s for s in await async_home_suggestions(hass) if s["type"] == "camera_idea"]
    assert [r["suggestion_id"] for r in rows] == ["idea:vehicle:camera.driveway"]

    await async_dismiss_home_suggestion(hass, "idea:vehicle:camera.driveway")
    assert not [s for s in await async_home_suggestions(hass) if s["type"] == "camera_idea"]


async def test_the_websocket_command_answers_admins_with_undismissed_ideas(
    hass: HomeAssistant, home: _Home
) -> None:
    import inspect

    from custom_components.selora_ai.websocket import frigate as frigate_ws

    camera = home.frigate_camera("driveway", objects=("car", "dog"))
    await async_dismiss_home_suggestion(hass, "idea:animal:camera.driveway")
    handler = inspect.unwrap(frigate_ws._handle_websocket_frigate_ideas)

    user = MagicMock()
    user.user.is_admin = False
    await handler(hass, user, {"id": 1, "camera_entity": camera})
    user.send_result.assert_not_called()

    admin = MagicMock()
    admin.user.is_admin = True
    await handler(hass, admin, {"id": 2, "camera_entity": camera})
    (_, payload), _ = admin.send_result.call_args
    assert [i["kind"] for i in payload["ideas"]] == ["vehicle"]


async def test_an_excluded_light_is_not_offered(hass: HomeAssistant, home: _Home) -> None:
    camera = home.frigate_camera("e1_zoom")
    garden = home.device("Garden lamp", area="Garden")
    lamp = home.entity("light", "test", "garden_lamp", "garden_lamp", garden)

    with patch(
        "custom_components.selora_ai.frigate.ideas.resolve_ignored_entity_ids",
        return_value=frozenset({lamp}),
    ):
        ideas = _by_kind(await async_camera_ideas(hass, camera))

    assert "person_night_light" not in ideas


async def test_a_reolink_camera_frigate_also_watches_gets_ideas_once(
    hass: HomeAssistant, home: _Home, aioclient_mock: Any
) -> None:
    """The same physical camera is two cameras in HA: Reolink's and Frigate's.
    Matched by stream: an NVR's other channel, at the same address, stays."""
    frigate_entry = MockConfigEntry(domain="frigate", data={"url": "http://frigate.local:5000"})
    frigate_entry.add_to_hass(hass)
    frigate_entry.mock_state(hass, ConfigEntryState.LOADED)
    aioclient_mock.get(
        "http://frigate.local:5000/api/config",
        json={"cameras": {}, "go2rtc": {"streams": {"lumus": ["rtsp://*:*@192.168.2.10:554/ch1"]}}},
    )
    home.frigate_camera("lumus", objects=("car",))
    streams: dict[str, str] = {}
    for channel in ("ch1", "ch2"):
        device = home.device(f"NVR {channel}")
        camera = home.entity(
            "camera", "reolink", f"nvr_{channel}", f"nvr_{channel}", device, "idle"
        )
        home.entity(
            "binary_sensor",
            "reolink",
            f"nvr_{channel}_vehicle",
            f"nvr_{channel}_vehicle",
            device,
            translation_key="vehicle",
        )
        streams[camera] = f"rtsp://admin:pw@192.168.2.10:554/{channel}"

    async def _stream(_hass: HomeAssistant, entity_id: str) -> str | None:
        return streams.get(entity_id)

    with patch("custom_components.selora_ai.frigate.cameras._async_stream_source", _stream):
        vehicle = [i for i in await async_camera_ideas(hass) if i["kind"] == "vehicle"]

    assert [i["camera"]["entity_id"] for i in vehicle] == ["camera.lumus", "camera.nvr_ch2"]


async def test_a_sensor_used_only_as_a_condition_still_gets_ideas(
    hass: HomeAssistant, home: _Home
) -> None:
    camera = home.frigate_camera("driveway", objects=("car",))
    Path(hass.config.config_dir, "automations.yaml").write_text(
        "- id: a1\n  alias: Porch light\n  triggers:\n"
        "    - trigger: sun\n      event: sunset\n"
        "  conditions:\n    - condition: state\n"
        "      entity_id: binary_sensor.driveway_car_occupancy\n      state: 'on'\n"
        "  actions: []\n"
    )

    assert "vehicle" in _by_kind(await async_camera_ideas(hass, camera))


async def test_an_excluded_presence_entity_is_not_used(hass: HomeAssistant, home: _Home) -> None:
    camera = home.frigate_camera("e1_zoom")
    home.people()

    with patch(
        "custom_components.selora_ai.frigate.ideas.resolve_ignored_entity_ids",
        return_value=frozenset({"zone.home"}),
    ):
        ideas = _by_kind(await async_camera_ideas(hass, camera))

    assert "person_away" not in ideas


async def test_a_watch_scene_needs_two_cameras_with_both_switches(
    hass: HomeAssistant, home: _Home
) -> None:
    home.frigate_camera("e1_zoom")
    home.frigate_camera("lumus")
    er.async_get(hass).async_update_entity(
        "switch.lumus_recordings", disabled_by=er.RegistryEntryDisabler.USER
    )
    home.frigate_camera("porch")
    er.async_get(hass).async_update_entity(
        "switch.porch_detect", disabled_by=er.RegistryEntryDisabler.USER
    )

    assert "watch_scene" not in _by_kind(await async_camera_ideas(hass))


async def test_a_watch_scene_already_made_is_not_offered_again(
    hass: HomeAssistant, home: _Home
) -> None:
    home.frigate_camera("e1_zoom")
    home.frigate_camera("lumus")
    scenes = Path(hass.config.config_dir, "scenes.yaml")
    scenes.write_text(
        "- id: s1\n  name: Sleep\n  entities:\n"
        "    switch.e1_zoom_detect: 'off'\n    switch.e1_zoom_recordings: 'off'\n"
        "    switch.lumus_detect: 'off'\n    switch.lumus_recordings: 'off'\n"
    )
    # A scene turning them off is not a Watch scene.
    assert "watch_scene" in _by_kind(await async_camera_ideas(hass))

    scenes.write_text(
        "- id: s2\n  name: Watch\n  entities:\n"
        "    switch.e1_zoom_detect: 'on'\n"
        "    switch.e1_zoom_recordings:\n      state: 'on'\n"
        "    switch.lumus_detect: 'on'\n    switch.lumus_recordings: 'on'\n"
    )
    assert "watch_scene" not in _by_kind(await async_camera_ideas(hass))


async def test_a_reolink_device_frigate_reads_one_stream_of_is_dropped_whole(
    hass: HomeAssistant, home: _Home, aioclient_mock: Any
) -> None:
    """Reolink has a camera entity per stream; Frigate reads the main one."""
    frigate_entry = MockConfigEntry(domain="frigate", data={"url": "http://frigate.local:5000"})
    frigate_entry.add_to_hass(hass)
    frigate_entry.mock_state(hass, ConfigEntryState.LOADED)
    aioclient_mock.get(
        "http://frigate.local:5000/api/config",
        json={"cameras": {}, "go2rtc": {"streams": {"lumus": ["rtsp://*:*@192.168.2.178/main"]}}},
    )
    home.frigate_camera("lumus", objects=("car",))
    device = home.device("Lumus Reolink")
    streams = {
        home.entity("camera", "reolink", "lumus_fluent", "lumus_fluent", device, "idle"): (
            "rtsp://admin:pw@192.168.2.178:554/sub"
        ),
        home.entity("camera", "reolink", "lumus_clear", "lumus_clear", device, "idle"): (
            "rtsp://admin:pw@192.168.2.178:554/main"
        ),
    }
    home.entity(
        "binary_sensor",
        "reolink",
        "lumus_vehicle",
        "lumus_vehicle",
        device,
        translation_key="vehicle",
    )

    async def _stream(_hass: HomeAssistant, entity_id: str) -> str | None:
        return streams.get(entity_id)

    with patch("custom_components.selora_ai.frigate.cameras._async_stream_source", _stream):
        vehicle = [i for i in await async_camera_ideas(hass) if i["kind"] == "vehicle"]

    assert [i["camera"]["entity_id"] for i in vehicle] == ["camera.lumus"]


async def test_an_unreadable_scenes_file_only_drops_the_watch_idea(
    hass: HomeAssistant, home: _Home
) -> None:
    home.frigate_camera("e1_zoom", objects=("car",))
    home.frigate_camera("lumus")
    scenes = Path(hass.config.config_dir, "scenes.yaml")
    for broken in (b"not: [a, list\n", b"- name: \xff\xfe\n"):
        scenes.write_bytes(broken)

        kinds = {i["kind"] for i in await async_camera_ideas(hass)}

        assert "watch_scene" not in kinds and "vehicle" in kinds


async def test_any_stream_entity_of_a_camera_asks_for_its_ideas(
    hass: HomeAssistant, home: _Home
) -> None:
    device = home.device("Garage Reolink")
    clear = home.entity("camera", "reolink", "garage_clear", "garage_clear", device, "idle")
    fluent = home.entity("camera", "reolink", "garage_fluent", "garage_fluent", device, "idle")
    home.entity(
        "binary_sensor",
        "reolink",
        "garage_vehicle",
        "garage_vehicle",
        device,
        translation_key="vehicle",
    )

    for entity_id in (clear, fluent):
        assert [i["kind"] for i in await async_camera_ideas(hass, entity_id)] == ["vehicle"]
