"""What the cameras saw: Frigate's tracked-object events, for a question.

Frigate records an event per object it tracks (a person walking up the drive,
a car parking) with its camera, zones, times and, when face or plate
recognition is on, who or what it was (``sub_label``). These are read from
Frigate's API, not from Home Assistant, whose Frigate entities only say what is
there now.

The model gets facts only: ids, names and local times, never a media URL. The
panel draws each event's snapshot from the ids, through the Frigate
integration's own authenticated views.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
import re
from typing import TYPE_CHECKING, Any, Final

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from ..helpers import sanitize_untrusted_text
from ..history_reader import _BadRequest, _window
from .cameras import FOLDED_LABELS, OBJECT_LABEL, model_labels, unsupported_labels
from .client import FRIGATE_DOMAIN, FrigateClient, FrigateError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

DEFAULT_HOURS: Final = 24.0
MAX_DAYS: Final = 31
DEFAULT_LIMIT: Final = 10
MAX_LIMIT: Final = 25
# Events read to count a range: a busy camera makes hundreds a day, and
# "how many people came by?" needs all of them, not the listed page.
_COUNT_CAP: Final = 1000
_MAX_FILTERS: Final = 10
# "<start epoch>-<random>": the panel builds media paths from it.
_EVENT_ID: Final = re.compile(r"^\d+(\.\d+)?-[a-z0-9]+$")
# What Frigate allows in a camera name; the panel builds clip paths from it.
_CAMERA: Final = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# An MQTT client id, as the panel puts it in one path segment: nothing that
# would end or escape the segment, and no "." or "..", which the browser would
# resolve. The panel URL-encodes the rest.
_INSTANCE: Final = re.compile(r"^(?!\.{1,2}$)[^/\\?#%\s\x00-\x1f\x7f]{1,128}$")

_NOT_SET_UP: Final = (
    "Frigate is not set up in Home Assistant: the Frigate integration has to be "
    "installed and connected to Frigate first."
)
# A description search with semantic search off still answers with the other
# filters, and says what turning it on would take, for the user to decide:
# it is a global Frigate setting, which Selora AI does not write.
_SEMANTIC_OFF: Final = (
    "Frigate's semantic search is off, so these events match the other filters "
    "only: say they were not checked for the description. If the user wants to "
    "search by description, tell them what turning it on means."
)
_SEMANTIC_SEARCH: Final = {
    "where": (
        "In Frigate: Settings, Enrichments, Semantic search (or "
        "semantic_search.enabled in its config); Frigate restarts. Selora AI "
        "does not change this setting."
    ),
    "cost": (
        "Frigate runs an image-matching AI model on the hub: more CPU and memory "
        "(the large model wants a GPU), and a model download the first time."
    ),
    "history": (
        "Events are indexed from then on; older ones only after a reindex, "
        "which can take a while on a busy system."
    ),
    "privacy": (
        "It runs on the hub: no image leaves the house. Written descriptions of "
        "each event are a separate option (GenAI), which sends snapshots to an "
        "AI provider unless it is a local one."
    ),
}


def _local(timestamp: Any) -> str | None:
    if not isinstance(timestamp, int | float):
        return None
    moment = datetime.fromtimestamp(timestamp, UTC)
    return dt_util.as_local(moment).isoformat(timespec="seconds")


def _words(arguments: dict[str, Any], name: str) -> list[str]:
    raw = arguments.get(name)
    items = raw if isinstance(raw, list) else [raw] if isinstance(raw, str) else []
    words = [str(i).strip() for i in items if str(i).strip()]
    return list(dict.fromkeys(words))[:_MAX_FILTERS]


def _frigate_cameras(config: dict[str, Any]) -> dict[str, str]:
    """Frigate camera name → the name people know it by."""
    cameras = config.get("cameras")
    if not isinstance(cameras, dict):
        return {}
    named: dict[str, str] = {}
    for key, camera in cameras.items():
        friendly = camera.get("friendly_name") if isinstance(camera, dict) else None
        named[str(key)] = sanitize_untrusted_text(str(friendly or key), 60)
    return named


def _watching(config: dict[str, Any], cameras: list[str]) -> list[dict[str, Any]]:
    """Whether each camera detects at all, and the objects it looks for:
    Frigate records events only for those, so an empty search on a camera
    that is not looking for cars says nothing about cars."""
    configs = config.get("cameras") if isinstance(config.get("cameras"), dict) else {}
    watching = []
    for key in cameras:
        camera = configs.get(key) if isinstance(configs.get(key), dict) else {}
        detect = camera.get("detect") if isinstance(camera.get("detect"), dict) else {}
        objects = camera.get("objects") if isinstance(camera.get("objects"), dict) else {}
        track = objects.get("track") if isinstance(objects.get("track"), list) else []
        watching.append(
            {
                "camera": key,
                "detection": detect.get("enabled") is not False,
                "looks_for": [sanitize_untrusted_text(str(o), 40) for o in track if o],
            }
        )
    return watching


def _blind_spots(
    watching: list[dict[str, Any]], labels: list[str], supported: frozenset[str] | None = None
) -> list[str]:
    """What no searched camera could have recorded, said plainly."""
    notes = [
        f"Detection is off on {w['camera']}: Frigate records no events there."
        for w in watching
        if not w["detection"]
    ]
    looking = [w for w in watching if w["detection"]]
    unknown = unsupported_labels(labels, supported)
    if unknown:
        # Not a camera setting: no camera can be made to look for it.
        notes.append(
            f"Frigate's current model does not detect {', '.join(map(repr, unknown))}, "
            f"so no camera can look for it. {FOLDED_LABELS}"
        )
    for label in labels:
        if label in unknown:
            continue
        if looking and not any(label in w["looks_for"] for w in looking):
            notes.append(
                f"No searched camera looks for {label!r} now, so none records "
                "one, and a search, by description or not, finds only those "
                "recorded while one did."
            )
    return notes


def _offer(
    watching: list[dict[str, Any]], labels: list[str], supported: frozenset[str] | None = None
) -> str | None:
    """What would let the cameras record the objects none looks for.

    A field of its own, built from checked names only: the prompt has the
    model make this offer, so it must never carry text from elsewhere.
    """
    looking = [w for w in watching if w["detection"]]
    missing = [
        label
        for label in labels
        if OBJECT_LABEL.match(label)
        and label not in unsupported_labels([label], supported)
        and not any(label in w["looks_for"] for w in looking)
    ]
    cameras = [w["camera"] for w in looking if _CAMERA.match(w["camera"])]
    if not cameras or not missing:
        return None
    return (
        f"Make {', '.join(cameras)} look for {', '.join(missing)} from now on "
        "(set_camera_objects; Frigate restarts)."
    )


def _resolve_camera(hass: HomeAssistant, given: str, cameras: dict[str, str]) -> str | None:
    """A Frigate camera from its name, its friendly name, or a camera entity:
    Frigate's own, or the Home Assistant camera it was added from."""
    if given in cameras:
        return given
    if given.startswith("camera."):
        entry = er.async_get(hass).async_get(given)
        if entry is not None and entry.platform == FRIGATE_DOMAIN:
            # Frigate's camera entities are keyed "<entry id>:camera:<camera>".
            name = entry.unique_id.rpartition(":")[2]
            if name in cameras:
                return name
        given = given.removeprefix("camera.")
    wanted = slugify(given)
    for key, friendly in cameras.items():
        if wanted in (slugify(key), slugify(friendly)):
            return key
    return None


def _describes(config: dict[str, Any]) -> bool:
    """Whether Frigate writes event descriptions with GenAI.

    Frigate 0.18 names its providers (``genai: {<name>: {provider, roles…}}``),
    and one whose roles include ``descriptions`` writes them; ``roles`` left
    out means every role. Before it, ``genai`` was one provider with an
    ``enabled`` flag. Read as the old shape only, a home with descriptions on
    had every one dropped.
    """
    genai = config.get("genai")
    if not isinstance(genai, dict):
        return False
    # The old shape by its values: a name could be "provider" or "enabled".
    if isinstance(genai.get("provider"), str) or isinstance(genai.get("enabled"), bool):
        return genai.get("enabled") is True
    return any(
        isinstance(provider, dict) and "descriptions" in provider.get("roles", ["descriptions"])
        for provider in genai.values()
    )


def _event(raw: dict[str, Any], cameras: dict[str, str], descriptions: bool) -> dict[str, Any]:
    camera = str(raw.get("camera") or "")
    start, end = raw.get("start_time"), raw.get("end_time")
    event: dict[str, Any] = {
        "id": str(raw["id"]),
        "camera": camera if _CAMERA.match(camera) else "",
        "camera_name": cameras.get(camera) or sanitize_untrusted_text(camera, 60),
        "label": sanitize_untrusted_text(str(raw.get("label") or ""), 40),
        "start": _local(start),
    }
    if isinstance(start, int | float) and isinstance(end, int | float):
        event["seconds"] = max(0, round(end - start))
    else:
        event["in_progress"] = True
    if sub_label := raw.get("sub_label"):
        # A face or plate Frigate recognised: [name, score] in some versions.
        name = sub_label[0] if isinstance(sub_label, list) and sub_label else sub_label
        event["sub_label"] = sanitize_untrusted_text(str(name), 60)
    if zones := [sanitize_untrusted_text(str(z), 40) for z in raw.get("zones") or [] if z]:
        event["zones"] = zones
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    if isinstance(score := data.get("top_score") or raw.get("top_score"), int | float):
        event["score"] = round(score, 2)
    if descriptions and (description := data.get("description")):
        event["description"] = sanitize_untrusted_text(str(description), 300)
    event["has_snapshot"] = bool(raw.get("has_snapshot"))
    event["has_clip"] = bool(raw.get("has_clip")) and bool(event["camera"])
    return event


async def async_find_events(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """The events matching the arguments, newest first, with counts for the range."""
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": _NOT_SET_UP}
    try:
        start, end = _window({"hours": DEFAULT_HOURS, **arguments}, MAX_DAYS)
    except _BadRequest as exc:
        return {"error": str(exc)}
    try:
        limit = max(1, min(int(arguments.get("limit") or DEFAULT_LIMIT), MAX_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT

    try:
        config = await client.get_config()
        cameras = _frigate_cameras(config)
        chosen: list[str] = []
        for given in _words(arguments, "cameras"):
            camera = _resolve_camera(hass, given, cameras)
            if camera is None:
                return {
                    "error": f"Frigate has no camera {sanitize_untrusted_text(given, 60)!r}.",
                    "cameras": [{"camera": k, "name": v} for k, v in cameras.items()],
                }
            chosen.append(camera)
        query = sanitize_untrusted_text(str(arguments.get("query") or ""), 200).strip()
        unfiltered = ""
        if query and not (config.get("semantic_search") or {}).get("enabled"):
            unfiltered, query = query, ""
        params: dict[str, Any] = {
            "after": start.timestamp(),
            "before": end.timestamp(),
            "include_thumbnails": 0,
        }
        filters = {
            "cameras": chosen,
            "labels": [w.lower() for w in _words(arguments, "objects")],
            "zones": _words(arguments, "zones"),
            "sub_labels": _words(arguments, "names"),
        }
        params.update({k: ",".join(v) for k, v in filters.items() if v})
        if query:
            params.update({"query": query, "limit": limit})
            if _describes(config):
                # Frigate searches thumbnails only unless asked; a colour or a
                # make is in the description, not always in the picture's match.
                params["search_type"] = "thumbnail,description"
            raw = await client.get_events(params, search=True)
        else:
            params["limit"] = _COUNT_CAP
            raw = await client.get_events(params)
    except FrigateError as exc:
        return {"error": str(exc)}

    found = [e for e in raw if isinstance(e, dict) and _EVENT_ID.match(str(e.get("id") or ""))]
    if not query:
        found.sort(key=lambda e: e.get("start_time") or 0, reverse=True)
    events = [_event(e, cameras, _describes(config)) for e in found[:limit]]
    watching = _watching(config, chosen or list(cameras))
    result: dict[str, Any] = {
        "range": {"start": _local(start.timestamp()), "end": _local(end.timestamp())},
        "events": events,
        "watching": watching,
    }
    # The Frigate integration's always-on media routes are keyed by Frigate's
    # MQTT client id; the panel draws an event without a snapshot from one.
    instance = (config.get("mqtt") or {}).get("client_id")
    if isinstance(instance, str) and _INSTANCE.match(instance):
        result["instance"] = instance
    supported = model_labels(config)
    if notes := _blind_spots(watching, filters["labels"], supported):
        result["not_recorded"] = notes
    if offer := _offer(watching, filters["labels"], supported):
        result["offer"] = offer
    if unfiltered:
        result["not_filtered_by"] = unfiltered
        result["note"] = _SEMANTIC_OFF
        result["semantic_search"] = _SEMANTIC_SEARCH
    if query:
        result["ranked_by"] = "how well each matches the query"
        return result
    result["total"] = len(found)
    if len(found) >= _COUNT_CAP:
        result["total_note"] = f"at least {_COUNT_CAP}; narrow the range for an exact count"
    if len(found) > limit:
        # By Frigate's camera key: two cameras can share a friendly name.
        result["by_object"] = dict(
            Counter(sanitize_untrusted_text(str(e.get("label") or ""), 40) for e in found)
        )
        result["by_camera"] = dict(
            Counter(sanitize_untrusted_text(str(e.get("camera") or ""), 64) for e in found)
        )
    return result
