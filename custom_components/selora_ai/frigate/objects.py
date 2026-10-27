"""Changing what a camera already in Frigate looks for, behind a card.

Frigate records events only for the objects in a camera's ``objects.track``
(``person`` by default). That list is configuration, not a switch: the
Frigate integration's "Detect" switch turns detection on or off for the whole
camera and chooses nothing. So a change is written to ``cameras.<name>`` —
the per-camera section Selora may write — and Frigate restarts to apply it.

The card names the camera and the full list it will look for. Confirming
re-reads the camera and writes only if what it looks for is still what the
card was built from.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any, Final

from ..const import DOMAIN
from ..helpers import sanitize_untrusted_text
from .cameras import (
    MAX_OBJECTS,
    OBJECT_LABEL,
    model_labels,
    unsupported_error,
    unsupported_labels,
)
from .client import FrigateClient, FrigateError
from .removal import _camera_name

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Confirms are serialized: two cards built from one list would both pass the
# check, and the second write would silently undo the first.
_WRITE_LOCK: Final = "_frigate_objects_lock"
# Lists saved but not running yet, with the list that ran when each was
# saved: Frigate reports its running configuration, and a write takes effect
# only once it restarts (or never, when it cannot).
_SAVED: Final = "_frigate_objects_saved"

_NOT_SET_UP: Final = (
    "Frigate is not set up in Home Assistant: the Frigate integration has to be "
    "installed and connected first."
)


def _tracked(config: dict[str, Any], name: str) -> list[str]:
    camera = (config.get("cameras") or {}).get(name) or {}
    track = (camera.get("objects") or {}).get("track")
    return sorted(str(o) for o in track or [] if o)


def _looks_for(hass: HomeAssistant, config: dict[str, Any], name: str) -> list[str]:
    """What the camera will look for: the list saved last, until it runs.

    A running list that is neither the saved one nor the one it replaced was
    set by someone else since, and is what counts.
    """
    saved: dict[str, tuple[list[str], list[str]]] = hass.data.setdefault(DOMAIN, {}).setdefault(
        _SAVED, {}
    )
    running = _tracked(config, name)
    if name in saved and running != saved[name][0]:
        del saved[name]
    return saved[name][1] if name in saved else running


def _fingerprint(name: str, tracked: list[str]) -> str:
    """The camera and what it looked for when the card was shown."""
    return hashlib.sha256(json.dumps([name, tracked]).encode()).hexdigest()


def _friendly(config: dict[str, Any], name: str) -> str:
    camera = (config.get("cameras") or {}).get(name) or {}
    friendly = str(camera.get("friendly_name") or name.replace("_", " ").title())
    return sanitize_untrusted_text(friendly, 60)


class _BadLabels(ValueError):
    """Labels Frigate would not take, named for the reply."""

    def reply(self) -> dict[str, Any]:
        return {"error": str(self)}


class _NotInModel(_BadLabels):
    """Labels the running model cannot report: Frigate would save them and
    drop them at startup, so the card would promise what never happens."""

    def __init__(self, labels: list[str], supported: frozenset[str]) -> None:
        super().__init__(", ".join(labels))
        self.labels = labels
        self.supported = supported

    def reply(self) -> dict[str, Any]:
        return unsupported_error(self.labels, self.supported)


def _labels(value: Any) -> set[str]:
    items = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    labels = {str(i).strip().lower() for i in items} - {""}
    if bad := sorted(o for o in labels if not OBJECT_LABEL.match(o)):
        shown = ", ".join(sanitize_untrusted_text(o, 40) for o in bad)
        raise _BadLabels(
            f"Not Frigate object labels: {shown}. Use labels such as person, car, dog."
        )
    return labels


def _change(friendly: str, tracked: list[str], wanted: list[str]) -> str:
    """What the card changes, in words: what it starts and stops looking for.

    A whole list hides a removal: asked to look for cars, a replacement of
    "person" by "car" read "looks for car (now car, person)", and the person
    it would stop looking for was easy to miss.
    """
    starts = [label for label in wanted if label not in tracked]
    stops = [label for label in tracked if label not in wanted]
    parts = [f"starts looking for {', '.join(starts)}"] if starts else []
    if stops:
        parts.append(f"stops looking for {', '.join(stops)}")
    change = f"{friendly} {' and '.join(parts)}"
    return f"{change}; it then looks for {', '.join(wanted)} only" if stops else change


def _wanted(
    arguments: dict[str, Any], tracked: list[str], supported: frozenset[str] | None = None
) -> list[str]:
    """The full list to look for: ``objects`` replaces, ``add``/``remove`` edit.

    An empty ``objects`` counts as not given: models send ``[]`` for an
    optional list, and reading it as "look for nothing" would be refused anyway.

    Raises _BadLabels rather than dropping a label: a dropped removal or a
    replacement left empty would change the camera in a way nobody asked for.
    Only a label being added is checked against the model (*supported*): one
    it already tracks, or one being removed, is the user's to keep or drop.
    """
    if replacement := _labels(arguments.get("objects")):
        wanted = replacement
    else:
        wanted = (set(tracked) | _labels(arguments.get("add"))) - _labels(arguments.get("remove"))
    if len(wanted) > MAX_OBJECTS:
        raise _BadLabels(f"A camera can look for at most {MAX_OBJECTS} objects.")
    if supported is not None and (missing := unsupported_labels(wanted - set(tracked), supported)):
        raise _NotInModel(missing, supported)
    return sorted(wanted)


async def async_preview_set_objects(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """A card for changing what a Frigate camera looks for, or why not."""
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    try:
        config = await client.get_config()
    except FrigateError as exc:
        return {"error": str(exc)}
    cameras = config.get("cameras") or {}
    name = _camera_name(hass, cameras, str(arguments.get("camera") or ""))
    if name is None:
        return {"error": "Frigate has no such camera.", "frigate_cameras": sorted(cameras)}
    tracked = _looks_for(hass, config, name)
    try:
        wanted = _wanted(arguments, tracked, model_labels(config))
    except _BadLabels as exc:
        return exc.reply()
    friendly = _friendly(config, name)
    if not wanted:
        return {"error": f"{friendly} has to look for at least one object."}
    if wanted == tracked:
        return {
            "unchanged": True,
            "camera": name,
            "looks_for": tracked,
            "note": f"{friendly} already looks for {', '.join(tracked)}; nothing to change.",
        }
    # What it costs comes first: a long list is what gets cut, never the restart.
    detect = (cameras.get(name) or {}).get("detect") or {}
    off = (
        "; detection is off on it, so turn on its Detect switch too"
        if detect.get("enabled") is False
        else ""
    )
    label = f"{_change(friendly, tracked, wanted)}{off} (Frigate restarts)"
    return {
        "requires_approval": True,
        "destructive": {
            "kind": "frigate_objects",
            "verb": "set_objects",
            "target_id": name,
            "entity_id": "",
            "name": friendly,
            "label": sanitize_untrusted_text(label, 300),
            "fingerprint": _fingerprint(name, tracked),
        },
    }


async def async_set_objects_confirmed(
    hass: HomeAssistant, name: str, fingerprint: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Write what the card showed, if the camera still looks for what it did."""
    from .setup import async_reload_when_back  # noqa: PLC0415

    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    lock: asyncio.Lock = hass.data.setdefault(DOMAIN, {}).setdefault(_WRITE_LOCK, asyncio.Lock())
    async with lock:
        try:
            config = await client.get_config()
            if name not in (config.get("cameras") or {}):
                return {"error": "Frigate no longer has that camera."}
            tracked = _looks_for(hass, config, name)
            if _fingerprint(name, tracked) != fingerprint:
                return {"error": "What the camera looks for changed since it was shown; ask again."}
            try:
                wanted = _wanted(arguments, tracked, model_labels(config))
            except _BadLabels as exc:
                return exc.reply()
            if not wanted:
                return {"error": "A camera has to look for at least one object."}
            await client.set_config({"cameras": {name: {"objects": {"track": wanted}}}})
            running = _tracked(config, name)
            if wanted != running:
                hass.data[DOMAIN][_SAVED][name] = (running, wanted)
            else:
                hass.data[DOMAIN][_SAVED].pop(name, None)
            friendly = _friendly(config, name)
            looks_for = f"{friendly} now looks for {', '.join(wanted)}"
        except FrigateError as exc:
            return {"error": str(exc)}
    try:
        await client.restart()
    except FrigateError as exc:
        _LOGGER.warning("Frigate saved the camera's objects but did not restart: %s", exc)
        return {
            "camera": name,
            "name": friendly,
            "looks_for": wanted,
            "restart_failed": True,
            "done": f"Saved: {looks_for} once Frigate restarts. It could not restart itself.",
        }
    hass.async_create_background_task(
        async_reload_when_back(hass, client), f"{DOMAIN}_frigate_reload"
    )
    return {
        "camera": name,
        "name": friendly,
        "looks_for": wanted,
        "done": f"{looks_for}. Frigate is restarting, about a minute.",
    }
