"""Automation and scene ideas for the home's cameras.

A camera that detects people, animals or cars can drive automations, but a
new camera has no usage history for the pattern-based suggestions to learn
from. These ideas come from what the home has instead: a detection sensor on
the camera, a way to tell nobody is home, a phone to notify, a light in the
camera's area.

Each idea names the exact entities it would use, because cameras are not in
the model's entity list. The panel turns an idea into a chat request, and the
model proposes the automation or scene on the usual card; nothing is written
from here. An idea whose trigger an automation already uses is not offered.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..automation_utils import _collect_referenced_entity_ids, _read_automations_yaml
from ..entity_filter import resolve_ignored_entity_ids
from ..scene_utils import ScenesYamlError, _read_scenes_yaml
from . import cameras as camera_streams
from .cameras import existing_sources
from .client import FRIGATE_DOMAIN, FrigateClient, FrigateError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..types import CameraIdea

_LOGGER = logging.getLogger(__name__)

# What Frigate's occupancy sensors and Reolink's AI sensors detect, by the
# object word they carry.
_PERSON: Final = frozenset({"person"})
_ANIMAL: Final = frozenset({"dog", "cat", "bird", "animal", "pet"})
_VEHICLE: Final = frozenset({"car", "vehicle"})
_REOLINK_KEYS: Final = _PERSON | _ANIMAL | _VEHICLE

MAX_PER_CAMERA: Final = 3


@dataclass(frozen=True)
class _Camera:
    entity_id: str
    name: str
    device_id: str | None
    area_id: str | None
    # object kind ("person", "animal", "vehicle") → detection sensor
    sensors: dict[str, str]
    platform: str
    # Every camera entity of the device: one per stream (Reolink's Clear and
    # Fluent), any of which Frigate may read.
    streams: tuple[str, ...] = ()


def _detected_kind(word: str) -> str | None:
    if word in _PERSON:
        return "person"
    if word in _ANIMAL:
        return "animal"
    if word in _VEHICLE:
        return "vehicle"
    return None


def _sensor_kind(entry: er.RegistryEntry) -> str | None:
    """What a camera's detection sensor detects, or None for any other entity."""
    if entry.domain != "binary_sensor" or entry.disabled:
        return None
    if entry.platform == FRIGATE_DOMAIN:
        # unique id `<entry>:occupancy_sensor:<camera>_<object>`; the motion
        # sensor and the "all objects" one say nothing about what is seen.
        unique_id = str(entry.unique_id)
        if ":occupancy_sensor:" not in unique_id:
            return None
        return _detected_kind(unique_id.rsplit("_", 1)[-1])
    if entry.platform == "reolink" and entry.translation_key in _REOLINK_KEYS:
        return _detected_kind(str(entry.translation_key))
    return None


def _cameras(hass: HomeAssistant) -> list[_Camera]:
    """Every camera device with at least one detection sensor: one per
    device, whatever number of stream entities it has."""
    ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)
    ignored = resolve_ignored_entity_ids(hass)
    by_device: dict[str, list[er.RegistryEntry]] = {}
    for entry in sorted(ent_reg.entities.values(), key=lambda e: e.entity_id):
        if (
            entry.domain == "camera"
            and not entry.disabled
            and entry.entity_id not in ignored
            and entry.platform in (FRIGATE_DOMAIN, "reolink")
            and entry.device_id
        ):
            by_device.setdefault(entry.device_id, []).append(entry)
    cameras: list[_Camera] = []
    for entries in by_device.values():
        entry = entries[0]
        sensors: dict[str, str] = {}
        for sibling in er.async_entries_for_device(ent_reg, entry.device_id):
            kind = _sensor_kind(sibling)
            if kind and sibling.entity_id not in ignored and hass.states.get(sibling.entity_id):
                sensors.setdefault(kind, sibling.entity_id)
        if not sensors:
            continue
        device = dev_reg.async_get(entry.device_id)
        state = hass.states.get(entry.entity_id)
        cameras.append(
            _Camera(
                entity_id=entry.entity_id,
                name=str(
                    (state.name if state else None)
                    or (device.name_by_user or device.name if device else None)
                    or entry.entity_id
                ),
                device_id=entry.device_id,
                area_id=entry.area_id or (device.area_id if device else None),
                sensors=sensors,
                platform=entry.platform,
                streams=tuple(e.entity_id for e in entries),
            )
        )
    return sorted(cameras, key=lambda c: c.entity_id)


async def _frigate_sources(hass: HomeAssistant) -> set[tuple[str, int, str, str]]:
    """The streams Frigate reads (host, port, path, query); empty when it
    cannot say."""
    client = FrigateClient.from_hass(hass)
    if client is None:
        return set()
    try:
        return existing_sources(await client.get_config())
    except FrigateError:
        return set()


async def _one_per_camera(hass: HomeAssistant, cameras: list[_Camera]) -> list[_Camera]:
    """Drop a camera Frigate also watches, keeping Frigate's: a Reolink camera
    added to Frigate is two cameras in HA with their own sensors, and would
    otherwise get every idea twice. Matched by stream, not by address: every
    channel of a Reolink NVR has the NVR's address, and Frigate may watch one."""
    if all(c.platform == FRIGATE_DOMAIN for c in cameras):
        return cameras
    watched = await _frigate_sources(hass)
    if not watched:
        return cameras
    kept: list[_Camera] = []
    for camera in cameras:
        if camera.platform != FRIGATE_DOMAIN and await _any_stream_watched(hass, camera, watched):
            continue
        kept.append(camera)
    return kept


async def _any_stream_watched(
    hass: HomeAssistant, camera: _Camera, watched: set[tuple[str, int, str, str]]
) -> bool:
    """Whether Frigate reads any of the device's streams: it usually takes one
    (the main), which still makes the device a camera Frigate watches."""
    for entity_id in camera.streams or (camera.entity_id,):
        url = await camera_streams._async_stream_source(hass, entity_id)
        if url and camera_streams.source_key(url) in watched:
            return True
    return False


def _presence(hass: HomeAssistant, ignored: frozenset[str]) -> tuple[str, str] | None:
    """(entity, state when nobody is home): the home zone, whose state counts
    the people in it, else a group of people covering every person in the
    home. A group of some of them ("kids") would read their absence as nobody
    being home."""
    people = {s.entity_id for s in hass.states.async_all("person") if s.entity_id not in ignored}
    if people and "zone.home" not in ignored and hass.states.get("zone.home"):
        return "zone.home", "0"
    for state in hass.states.async_all("group"):
        members = state.attributes.get("entity_id")
        if (
            state.entity_id not in ignored
            and isinstance(members, (list, tuple))
            and members
            and all(str(m).startswith(("person.", "device_tracker.")) for m in members)
            and people <= set(members)
        ):
            return state.entity_id, "not_home"
    return None


def _notify_service(hass: HomeAssistant) -> tuple[str, bool]:
    """(service, whether it can carry an image): a phone, else Home
    Assistant's own notifications, which take only a title and a message."""
    phones = sorted(
        s for s in hass.services.async_services().get("notify", {}) if s.startswith("mobile_app_")
    )
    if phones:
        return f"notify.{phones[0]}", True
    return "persistent_notification.create", False


def _area_light(hass: HomeAssistant, area_id: str | None, ignored: frozenset[str]) -> str | None:
    if not area_id:
        return None
    ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)
    for entry in ent_reg.entities.values():
        if (
            entry.domain != "light"
            or entry.disabled
            or entry.entity_id in ignored
            or not hass.states.get(entry.entity_id)
        ):
            continue
        device = dev_reg.async_get(entry.device_id) if entry.device_id else None
        if (entry.area_id or (device.area_id if device else None)) == area_id:
            return entry.entity_id
    return None


async def _automated_triggers(hass: HomeAssistant) -> set[str]:
    path = Path(hass.config.config_dir) / "automations.yaml"
    automations = await hass.async_add_executor_job(_read_automations_yaml, path)
    used: set[str] = set()
    for automation in automations:
        if not isinstance(automation, dict):
            continue
        # Triggers only: a sensor used as a condition elsewhere does not mean
        # anything already fires when it detects something.
        triggers = automation.get("triggers") or automation.get("trigger")
        if triggers:
            used |= _collect_referenced_entity_ids({"triggers": triggers})
    return used


def _snapshot(camera: _Camera, takes_image: bool) -> str | None:
    """The camera's snapshot for the notification, when the notifier takes one."""
    return f"/api/camera_proxy/{camera.entity_id}" if takes_image else None


def _idea(kind: str, camera: _Camera, **facts: Any) -> CameraIdea:
    return {
        "idea_id": f"idea:{kind}:{camera.entity_id}",
        "kind": kind,
        "camera": {"entity_id": camera.entity_id, "name": camera.name},
        "facts": {k: v for k, v in facts.items() if v is not None},
    }


async def async_camera_ideas(
    hass: HomeAssistant, camera_entity: str | None = None
) -> list[CameraIdea]:
    """Ideas for every camera with detection, or for one camera."""
    cameras = _cameras(hass)
    if camera_entity is not None:
        # Any of the device's stream entities names it. Narrowed first, so one
        # camera's card does not resolve every other camera's streams.
        cameras = [c for c in cameras if camera_entity in (c.entity_id, *c.streams)]
    cameras = await _one_per_camera(hass, cameras)
    if not cameras:
        return []
    used = await _automated_triggers(hass)
    ignored = resolve_ignored_entity_ids(hass)
    presence = _presence(hass, ignored)
    notify, takes_image = _notify_service(hass)
    ideas: list[CameraIdea] = []
    seen_triggers: set[str] = set()
    for camera in cameras:
        found: list[CameraIdea] = []
        person = camera.sensors.get("person")
        if person and person not in used and person not in seen_triggers:
            if presence:
                found.append(
                    _idea(
                        "person_away",
                        camera,
                        trigger=person,
                        presence=presence[0],
                        presence_empty=presence[1],
                        notify=notify,
                        image=_snapshot(camera, takes_image),
                    )
                )
            if light := _area_light(hass, camera.area_id, ignored):
                found.append(_idea("person_night_light", camera, trigger=person, light=light))
        for kind in ("animal", "vehicle"):
            sensor = camera.sensors.get(kind)
            if sensor and sensor not in used and sensor not in seen_triggers:
                found.append(
                    _idea(
                        kind,
                        camera,
                        trigger=sensor,
                        notify=notify,
                        image=_snapshot(camera, takes_image),
                    )
                )
        seen_triggers |= set(camera.sensors.values())
        ideas.extend(found[:MAX_PER_CAMERA])
    if camera_entity is None and (scene := await _watch_scene(hass, ignored)) is not None:
        ideas.append(scene)
    return ideas


def _scene_turns_on(scene: dict[str, Any], switches: list[str]) -> bool:
    """Whether a scene's configuration sets every one of *switches* to on."""
    entities = scene.get("entities")
    if not isinstance(entities, dict):
        return False
    for switch in switches:
        target = entities.get(switch)
        state = target.get("state") if isinstance(target, dict) else target
        if str(state).lower() != "on":
            return False
    return True


async def _watch_scene(hass: HomeAssistant, ignored: frozenset[str]) -> CameraIdea | None:
    """Every Frigate camera's recording and detection on, as one scene: for a
    home with at least two cameras that have both switches, and no scene in
    scenes.yaml already setting them all to on."""
    pairs: dict[str, dict[str, str]] = {}
    for entry in er.async_get(hass).entities.values():
        if (
            entry.platform != FRIGATE_DOMAIN
            or entry.domain != "switch"
            or entry.disabled
            or entry.entity_id in ignored
            or not entry.device_id
            or not hass.states.get(entry.entity_id)
        ):
            continue
        for control in ("recordings", "detect"):
            if str(entry.unique_id).endswith(f"_{control}"):
                pairs.setdefault(entry.device_id, {})[control] = entry.entity_id
    complete = [p for p in pairs.values() if len(p) == 2]
    if len(complete) < 2:
        return None
    switches = sorted(sid for pair in complete for sid in pair.values())
    path = Path(hass.config.config_dir) / "scenes.yaml"
    try:
        scenes = await hass.async_add_executor_job(_read_scenes_yaml, path)
    except (ScenesYamlError, OSError, UnicodeError, ValueError, TypeError) as exc:
        # Unknown whether a Watch scene exists: leave the idea out, and only
        # it, rather than failing every suggestion beside it.
        _LOGGER.debug("Skipping the Watch scene idea: %s", exc)
        return None
    if any(_scene_turns_on(scene, switches) for scene in scenes if isinstance(scene, dict)):
        return None
    return {
        "idea_id": "idea:watch_scene",
        "kind": "watch_scene",
        "camera": {"entity_id": "", "name": ""},
        "facts": {"switches": ", ".join(switches)},
    }
