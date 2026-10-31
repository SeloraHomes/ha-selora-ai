"""The home the chat evals ask about, built to make names collide.

Every name here is something a real home had more than one of: a speaker,
a camera and an area are all "Living Room"; a lock and a camera are both
"Front Door"; Sam is a person, a face Frigate recognises and a last-camera
sensor. A model that answers from a name instead of from the kind of thing
it names gets these wrong, and that is what the cases check.

Frigate is a fake at the client: the config and events below are served
through the real `find_camera_events`, so its filtering, formatting and
the cards built from it are the production ones.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.selora_ai.frigate.client import FrigateClient

FRIGATE = "http://frigate.local:5000"

AREAS = ("Living Room", "Kitchen", "Entrance")

# entity_id, friendly name, area, state, extra attributes
ENTITIES: tuple[tuple[str, str, str | None, str, dict[str, Any]], ...] = (
    ("media_player.living_room", "Living Room", "Living Room", "playing", {}),
    ("camera.living_room", "Living Room", "Living Room", "recording", {}),
    ("camera.e1_zoom", "E1 Zoom", "Kitchen", "recording", {}),
    ("camera.front_door", "Front Door", "Entrance", "recording", {}),
    ("lock.front_door", "Front Door", "Entrance", "locked", {}),
    ("light.kitchen", "Kitchen", "Kitchen", "off", {}),
    ("light.living_room_lamp", "Living Room Lamp", "Living Room", "on", {}),
    ("person.sam", "Sam", None, "home", {}),
)

LAST_CAMERA = "sensor.frigate_sam_last_camera"

FRIGATE_CONFIG: dict[str, Any] = {
    "cameras": {
        "living_room": {"friendly_name": "Living Room"},
        "e1_zoom": {"friendly_name": "E1 Zoom"},
        "front_door": {"friendly_name": "Front Door"},
    },
    "semantic_search": {"enabled": False},
    "genai": {"enabled": False},
    "face_recognition": {"enabled": True},
}
# Frigate's Face Library; "train" holds the faces it could not match.
FACES: dict[str, list[str]] = {"Sam": ["sam-1.webp", "sam-2.webp"], "train": ["x.webp"]}


def _event(minutes_ago: float, camera: str, label: str, name: str | None = None) -> dict[str, Any]:
    start = dt_util.utcnow().timestamp() - minutes_ago * 60
    return {
        # Frigate's id shape; the tool drops anything else.
        "id": f"{start:.6f}-{camera.replace('_', '')}{int(minutes_ago):04d}",
        "camera": camera,
        "label": label,
        "start_time": start,
        "end_time": start + 20,
        "zones": [],
        "sub_label": [name, 0.92] if name else None,
        "has_clip": True,
        "has_snapshot": True,
        "data": {"top_score": 0.88},
    }


def frigate_events() -> list[dict[str, Any]]:
    """Newest first, as Frigate returns them.

    Sam was last on the E1 Zoom in the kitchen; the last-camera sensor still
    says Living Room, from hours earlier — it records a change of camera, not
    each sighting, which is the trap.
    """
    return [
        _event(25, "e1_zoom", "person", "Sam"),
        _event(40, "e1_zoom", "person", "Sam"),
        _event(130, "front_door", "person"),
        _event(300, "living_room", "person", "Sam"),
    ]


def _matches(event: dict[str, Any], params: dict[str, Any]) -> bool:
    def listed(key: str) -> list[str]:
        return [v for v in str(params.get(key) or "").split(",") if v]

    sub_label = event["sub_label"][0] if event["sub_label"] else None
    return (
        float(params.get("after") or 0)
        <= event["start_time"]
        <= float(params.get("before") or 1e12)
        and (not listed("cameras") or event["camera"] in listed("cameras"))
        and (not listed("labels") or event["label"] in listed("labels"))
        and (not listed("sub_labels") or sub_label in listed("sub_labels"))
    )


@contextmanager
def fake_frigate() -> Iterator[None]:
    async def _config(_self: FrigateClient) -> dict[str, Any]:
        return FRIGATE_CONFIG

    async def _events(
        _self: FrigateClient, params: dict[str, Any], *, search: bool = False
    ) -> list[Any]:
        found = [e for e in frigate_events() if _matches(e, params)]
        return found[: int(params.get("limit") or len(found))]

    async def _faces(_self: FrigateClient) -> dict[str, Any]:
        return FACES

    with (
        patch.object(FrigateClient, "get_config", _config),
        patch.object(FrigateClient, "get_events", _events),
        patch.object(FrigateClient, "get_faces", _faces),
    ):
        yield


async def build_home(hass: HomeAssistant) -> None:
    """Areas, entities in them, Frigate, and the last-camera sensor's past."""
    areas = ar.async_get(hass)
    area_ids = {name: areas.async_get_or_create(name).id for name in AREAS}
    entities = er.async_get(hass)
    for entity_id, name, area, state, attrs in ENTITIES:
        domain, object_id = entity_id.split(".", 1)
        entry = entities.async_get_or_create(
            domain, "eval", entity_id, suggested_object_id=object_id
        )
        if area:
            entities.async_update_entity(entry.entity_id, area_id=area_ids[area])
        hass.states.async_set(entity_id, state, {"friendly_name": name, **attrs})

    frigate = MockConfigEntry(domain="frigate", data={"url": FRIGATE})
    frigate.add_to_hass(hass)
    frigate.mock_state(hass, ConfigEntryState.LOADED)

    now = dt_util.utcnow().timestamp()
    attrs = {"friendly_name": "Frigate Sam last camera"}
    hass.states.async_set(LAST_CAMERA, "Living Room", attrs, timestamp=now - 300 * 60)
    hass.states.async_set(LAST_CAMERA, "unknown", attrs, timestamp=now - 280 * 60)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)
