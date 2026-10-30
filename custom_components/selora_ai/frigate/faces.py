"""Recognizing the people the household knows, with Frigate's face recognition.

Frigate looks for a face on each person it detects and matches it against its
Face Library, so it recognizes nobody until someone adds photos of each person
there. Adding them is Frigate's own UI; Selora AI says what to add and takes
the user to the page.

``face_recognition.enabled`` is global. It is the one global Frigate setting
Selora AI switches, on the user's yes: it needs no hardware choice, since the
default small model runs on the hub's CPU. The toggle is read from the saved
file, the one Frigate runs after its restart.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Final

from ..const import DOMAIN
from ..helpers import sanitize_untrusted_text
from .client import FrigateClient, FrigateError
from .moving import _saved_config

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..types import HomeSuggestion

_LOGGER = logging.getLogger(__name__)

SUGGESTION_CACHE: Final = "_frigate_faces_suggestion"
_SUGGESTION_TTL: Final = 300
_WRITE_LOCK: Final = "_frigate_faces_lock"
# Frigate keeps the faces it saw but could not match in this folder of the
# library; it is not a person.
_TRAIN: Final = "train"
_MAX_PEOPLE: Final = 50

_NOT_SET_UP: Final = (
    "Frigate is not set up in Home Assistant: the Frigate integration has to be "
    "installed and connected first."
)
_GUIDE: Final = (
    "It runs on the hub: no face leaves the house. Frigate downloads its model "
    "once, and the hub's CPU has to support AVX2.",
    "Frigate recognizes only the people added to its Face Library, in Frigate's "
    "own UI (the panel shows a button to open it under this reply): add each "
    "person with a few clear, front-facing photos.",
    "5 to 10 photos per person to begin with, 20 to 30 for good results: well "
    "lit, no hat or sunglasses, not black-and-white night shots. Varied photos "
    "matter more than many.",
    "The library's Recent Recognitions then lists faces Frigate saw: assigning "
    "each to the right person trains it further.",
    "A face is only looked for on a person Frigate detects, so a camera has to "
    "look for people, and close enough to see a face (a door, not the street).",
)
_HOW_TO_OFFER: Final = (
    "Explain what it does and what it needs (guide), then ask before turning it "
    "on: only the user's yes calls set_face_recognition. Once on, tell them to "
    "add each person in the Face Library."
)


def _enabled(config: dict[str, Any]) -> bool:
    section = config.get("face_recognition")
    return isinstance(section, dict) and section.get("enabled") is True


def _people(faces: dict[str, Any]) -> list[dict[str, Any]]:
    """Each person in the Face Library, with how many photos they have."""
    people = [
        {"name": sanitize_untrusted_text(str(name), 60), "photos": len(files)}
        for name, files in sorted(faces.items())
        if name != _TRAIN and isinstance(files, list)
    ]
    return people[:_MAX_PEOPLE]


def _without_people(config: dict[str, Any]) -> list[str]:
    """Cameras that do not look for people, so recognize no face."""
    cameras = config.get("cameras") if isinstance(config.get("cameras"), dict) else {}
    blind = []
    for name, camera in sorted(cameras.items()):
        objects = camera.get("objects") if isinstance(camera, dict) else None
        track = objects.get("track") if isinstance(objects, dict) else None
        if isinstance(track, list) and "person" not in track:
            blind.append(sanitize_untrusted_text(str(name), 64))
    return blind


def _fingerprint(enabled: bool) -> str:
    return hashlib.sha256(json.dumps(["face_recognition", enabled]).encode()).hexdigest()


async def async_face_recognition(hass: HomeAssistant) -> dict[str, Any]:
    """Whether Frigate recognizes faces, whom it knows, and what it takes."""
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    try:
        config = await client.get_config()
        saved = await _saved_config(client)
    except FrigateError as exc:
        return {"error": str(exc)}
    on = _enabled(saved)
    result: dict[str, Any] = {"face_recognition": "on" if on else "off", "guide": list(_GUIDE)}
    if on and not _enabled(config):
        result["note"] = "It is saved on and starts with Frigate's next restart."
    if on:
        try:
            people = _people(await client.get_faces())
        except FrigateError as exc:
            _LOGGER.debug("No Face Library: %s", exc)
            people = []
        result["people"] = people
        if not people:
            result["next"] = (
                "Nobody is in the Face Library yet: Frigate recognizes no one until they are added."
            )
        result["face_library"] = True
    else:
        result["how_to_offer"] = _HOW_TO_OFFER
    if blind := _without_people(config):
        result["cameras_not_looking_for_people"] = blind
    return result


async def async_preview_set_face_recognition(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """A card for turning face recognition on or off, or why not."""
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    enabled = arguments.get("enabled")
    if not isinstance(enabled, bool):
        return {"error": "Say whether to recognize faces: enabled true or false."}
    try:
        saved = await _saved_config(client)
    except FrigateError as exc:
        return {"error": str(exc)}
    current = _enabled(saved)
    if enabled == current:
        state = "on" if current else "off"
        return {"unchanged": True, "note": f"Face recognition is already {state}."}
    label = (
        "Restart Frigate to recognize the faces of people added to its Face Library. "
        "It runs on the hub: no face leaves the house"
        if enabled
        else "Restart Frigate with face recognition off. The Face Library is kept"
    )
    return {
        "requires_approval": True,
        "destructive": {
            "kind": "frigate_faces",
            "verb": "set_faces",
            "target_id": "face_recognition",
            "entity_id": "",
            "name": "Face recognition",
            "label": label,
            "fingerprint": _fingerprint(current),
        },
    }


async def async_set_face_recognition_confirmed(
    hass: HomeAssistant, fingerprint: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Write the card's toggle, if face recognition is still as it was shown."""
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    enabled = arguments.get("enabled")
    if not isinstance(enabled, bool):
        return {"error": "The card did not say whether to recognize faces."}
    lock: asyncio.Lock = hass.data.setdefault(DOMAIN, {}).setdefault(_WRITE_LOCK, asyncio.Lock())
    async with lock:
        try:
            if _fingerprint(_enabled(await _saved_config(client))) != fingerprint:
                return {"error": "Face recognition changed since it was shown; ask again."}
            await client.set_config({"face_recognition": {"enabled": enabled}})
        except FrigateError as exc:
            return {"error": str(exc)}
    hass.data[DOMAIN].pop(SUGGESTION_CACHE, None)
    done = (
        "Face recognition is on; add each person in Frigate's Face Library"
        if enabled
        else "Face recognition is off"
    )
    try:
        await client.restart()
    except FrigateError as exc:
        _LOGGER.warning("Frigate saved face recognition but did not restart: %s", exc)
        return {
            "restart_failed": True,
            "done": f"Saved: {done} once Frigate restarts. It could not restart itself.",
        }
    return {"done": f"{done}. Frigate is restarting, about a minute."}


async def async_face_suggestions(hass: HomeAssistant) -> list[HomeSuggestion]:
    """A suggestion to start recognizing faces, then to add the first person.

    Each state has its own id, so dismissing "turn it on" does not hide "add
    someone" once it is on.
    """
    client = FrigateClient.from_hass(hass)
    if client is None:
        return []
    domain_data = hass.data.setdefault(DOMAIN, {})
    cached = domain_data.get(SUGGESTION_CACHE)
    if cached is not None and time.monotonic() - cached[0] < _SUGGESTION_TTL:
        return list(cached[1])
    suggestions: list[HomeSuggestion] = []
    try:
        config = await client.get_config()
        if config.get("cameras"):
            saved = await _saved_config(client)
            state = None
            if not _enabled(saved):
                state = "off"
            elif not _people(await client.get_faces()):
                state = "empty"
            if state:
                suggestions.append(
                    {
                        "suggestion_id": f"faces:{state}",
                        "type": "recognize_faces",
                        "entities": [],
                        "collection": {"source": "frigate", "name": "Frigate", "ref": "frigate"},
                        "state": state,
                    }
                )
    except FrigateError as exc:
        _LOGGER.debug("No face recognition suggestion: %s", exc)
    domain_data[SUGGESTION_CACHE] = (time.monotonic(), suggestions)
    return suggestions
