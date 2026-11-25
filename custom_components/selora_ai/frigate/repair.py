"""Fixing how Frigate analyses a camera, behind a card.

The Health audit's camera check (``analysis``) finds what about a camera costs
the hub. Two of its issues are settings in the camera's own Frigate section,
which this changes once the user confirms a card:

* **Detection decodes the main stream** (``detect_stream_heavy`` on an input
  that records too): detection moves to a substream. Home Assistant's stream
  entities for the camera come first; without one, the brand's substream path
  beside the main one (``_SUB_PATHS``), offered only once it answers over RTSP
  with a smaller picture.
* **Detection runs above 720p** (``detect_above_720p``): the detect size is
  scaled down to fit 1280×720, shape kept.

What only the camera or its surroundings can change (a substream set too
large, constant motion, a camera off the network) is said, never written.

The substream's login is the one Frigate holds for the main stream, read from
its saved configuration, or Home Assistant's: it is never shown, logged or
returned. Each camera with issues is also a home suggestion
(``async_fix_camera_suggestions``), the highest the chat home shows.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlsplit, urlunsplit

from ..const import DOMAIN
from .analysis import (
    DETECT_STREAM_UNREADABLE,
    HIGH_CPU,
    NO_VIDEO,
    SKIPPING_FRAMES,
)
from .cameras import (
    HEAVY_DETECT_PIXELS,
    RESTREAM,
    async_device_substreams,
    choose_streams,
    detect_size,
    fits_detect_box,
    go2rtc_source,
    source_key,
)
from .client import NOT_SET_UP, FrigateClient, FrigateError
from .moving import _camera_streams, _friendly, _saved_config
from .removal import _camera_name
from .rtsp import async_stream_size

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..types import CameraReport, HomeSuggestion

_LOGGER = logging.getLogger(__name__)

_WRITE_LOCK: Final = "_frigate_fix_lock"
SUGGESTION_PREFIX: Final = "fix_camera:"

# A brand's main stream → its substream, in the stream's path or query. The
# match must end the path or a query parameter, so "/stream1" is not "/stream10".
_SUB_PATHS: Final = (
    ("_main", "_sub"),  # Reolink: h264Preview_01_main, h265Preview_01_main
    ("/streaming/channels/101", "/streaming/channels/102"),  # Hikvision
    ("subtype=0", "subtype=1"),  # Dahua, Amcrest
    ("/media/video1", "/media/video2"),  # Uniview
    ("/stream1", "/stream2"),  # TP-Link Tapo
    ("/stream0", "/stream1"),  # Wyze's own RTSPS (rtsps://…:322/stream0)
)
_RESTREAM_HOST: Final = "127.0.0.1"
_RESTREAM_PORT: Final = 8554

type Size = tuple[int, int]


@dataclass(frozen=True)
class _Plan:
    """What the card changes in one camera, and what it leaves to the user."""

    name: str
    friendly: str
    # The input detection reads now, by its index in the camera's inputs.
    detect_index: int | None = None
    # The substream to detect on, login included: written, never shown.
    sub_url: str | None = None
    sub_size: Size | None = None
    main_size: Size | None = None
    # The detect section to write: a size, or nulls to let Frigate measure.
    detect: dict[str, int | None] | None = None
    analysed: Size | None = None
    advice: tuple[str, ...] = ()

    @property
    def changes(self) -> bool:
        return self.sub_url is not None or self.detect is not None


def _size(width: Any, height: Any) -> Size | None:
    try:
        size = (int(width), int(height))
    except (TypeError, ValueError):
        return None
    return size if size[0] > 0 and size[1] > 0 else None


def _area(size: Size | None) -> int:
    return size[0] * size[1] if size else 0


def _shown(size: Size) -> str:
    return f"{size[0]}×{size[1]}"


def _plain(url: str) -> str:
    """A go2rtc source as a stream address: without ``ffmpeg:`` or its options."""
    return url.strip().removeprefix("ffmpeg:").split("#", 1)[0]


def _restream_key(path: str) -> str | None:
    """The go2rtc stream an input restreams, or None for a camera read directly."""
    try:
        parts = urlsplit(path.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.hostname != _RESTREAM_HOST or port != _RESTREAM_PORT:
        return None
    return parts.path.lstrip("/") or None


def _main_source(saved: dict[str, Any], path: str) -> str | None:
    """The address the input reads, login included: its go2rtc source when restreamed."""
    key = _restream_key(path)
    if key is None:
        return path or None
    value = ((saved.get("go2rtc") or {}).get("streams") or {}).get(key)
    sources = value if isinstance(value, list) else [value]
    return next((s for s in sources if isinstance(s, str) and s.strip()), None)


def _swapped(url: str) -> list[str]:
    """``url`` with a known brand's main-stream path turned into its substream's."""
    parts = urlsplit(url)
    tail = parts.path + (f"?{parts.query}" if parts.query else "")
    found: list[str] = []
    for main, sub in _SUB_PATHS:
        at = tail.lower().rfind(main)
        end = at + len(main)
        if at < 0 or (end < len(tail) and tail[end] not in "?&/"):
            continue
        path, _, query = (tail[:at] + sub + tail[end:]).partition("?")
        found.append(urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment)))
    return found


async def _find_substream(
    hass: HomeAssistant, main_url: str, main_size: Size
) -> tuple[str, Size | None] | None:
    """A substream smaller than the main stream, and its size when measured."""
    found: list[tuple[str, Size | None]] = []
    for url in await async_device_substreams(hass, _plain(main_url)):
        # Home Assistant says it is a substream; only a measured size can say
        # otherwise (one that does not answer, or whose SDP has no size, is
        # taken at its word).
        size = await async_stream_size(url)
        if size is None or _area(size) < _area(main_size):
            found.append((url, size))
    if not found:
        for url in _swapped(_plain(main_url)):
            # A guessed path is offered only once the camera answers it.
            size = await async_stream_size(url)
            if size is not None and _area(size) < _area(main_size):
                found.append((url, size))
                break
    if not found:
        return None
    _main, pick = choose_streams([main_size, *(size for _url, size in found)])
    return found[(pick or 1) - 1]


def _inputs(camera: dict[str, Any]) -> list[dict[str, Any]]:
    inputs = (camera.get("ffmpeg") or {}).get("inputs") or []
    return [i for i in inputs if isinstance(i, dict)]


def _roles(source: dict[str, Any]) -> list[str]:
    return [str(r) for r in source.get("roles") or []]


async def _plan(
    hass: HomeAssistant,
    config: dict[str, Any],
    saved: dict[str, Any],
    name: str,
    report: CameraReport | None,
) -> _Plan:
    """What Selora AI can change in how Frigate analyses ``name``, and what not."""
    friendly = _friendly(config, name)
    camera = (config.get("cameras") or {}).get(name) or {}
    detect_shown = camera.get("detect") or {}
    analysed = _size(detect_shown.get("width"), detect_shown.get("height"))
    inputs = _inputs(((saved.get("cameras") or {}).get(name)) or {})
    issues = set(report["issues"]) if report else set()
    advice: list[str] = []
    if issues & {NO_VIDEO, DETECT_STREAM_UNREADABLE}:
        advice.append(
            "Selora Vision gets no picture from it: check the camera is powered, "
            "on the network, and its stream is turned on."
        )
    if HIGH_CPU in issues:
        advice.append(
            "Constant motion in view (trees, a road, a screen) keeps the hub busy "
            "analysing it: draw a motion mask over those areas in the camera's "
            "Selora Vision settings."
        )
    index = next((i for i, s in enumerate(inputs) if "detect" in _roles(s)), None)
    if index is None:
        return _Plan(name, friendly, analysed=analysed, advice=tuple(advice))
    streams = report["streams"] if report else []
    probed = streams[index] if index < len(streams) else None
    main_url = _main_source(saved, str(inputs[index].get("path") or ""))
    source_size = _size(probed["width"], probed["height"]) if probed else None
    if source_size is None and main_url:
        source_size = await async_stream_size(_plain(main_url))
    heavy = _area(source_size) > HEAVY_DETECT_PIXELS
    records_too = "record" in _roles(inputs[index])
    sub = (
        await _find_substream(hass, main_url, source_size)
        if heavy and records_too and main_url and source_size
        else None
    )
    detect: dict[str, int | None] | None = None
    if sub is not None:
        if sub[1] is not None:
            detect = dict(detect_size(sub[1]))
        elif {"width", "height"} & set(_saved_detect(saved, name)):
            # A size written for the main stream would scale the substream up.
            detect = {"width": None, "height": None}
    elif analysed is not None and not fits_detect_box(analysed):
        detect = dict(detect_size(analysed))
    if heavy and not records_too:
        advice.append(
            f"Its substream is {_shown(source_size or (0, 0))}, so the hub decodes every "
            "frame of it in full: lower it to 720p in the camera's own settings (its "
            "app or web page). Nothing in Selora Vision needs changing after."
        )
    elif heavy and sub is None:
        advice.append(
            "Selora Vision reads only its main stream, and Selora AI found no "
            "substream for it: turn the camera's substream on, in its app or as its "
            "substream entity in Home Assistant, then ask again."
        )
    if SKIPPING_FRAMES in issues and sub is None and detect is None:
        advice.append(
            "The hub cannot keep up with every camera: fix the other cameras' issues "
            "first, or have Selora Vision watch fewer of them."
        )
    return _Plan(
        name,
        friendly,
        detect_index=index,
        sub_url=sub[0] if sub else None,
        sub_size=sub[1] if sub else None,
        main_size=source_size,
        detect=detect,
        analysed=analysed,
        advice=tuple(advice),
    )


def _saved_detect(saved: dict[str, Any], name: str) -> dict[str, Any]:
    camera = (saved.get("cameras") or {}).get(name) or {}
    detect = camera.get("detect")
    return detect if isinstance(detect, dict) else {}


def _sub_stream_name(saved: dict[str, Any], name: str) -> str | None:
    """The go2rtc stream to put the substream in: ``<name>_sub`` unless another
    camera reads that one."""
    stream = f"{name}_sub"
    for other, camera in (saved.get("cameras") or {}).items():
        if other == name or not isinstance(camera, dict):
            continue
        if any(_restream_key(str(i.get("path") or "")) == stream for i in _inputs(camera)):
            return None
    return stream


def _changes(saved: dict[str, Any], plan: _Plan) -> dict[str, Any] | None:
    """The config_data that makes the change: only the camera's own sections."""
    camera: dict[str, Any] = {}
    changes: dict[str, Any] = {}
    if plan.sub_url is not None and plan.detect_index is not None:
        inputs = [dict(i) for i in _inputs(((saved.get("cameras") or {}).get(plan.name)) or {})]
        if plan.detect_index >= len(inputs):
            return None
        main = inputs[plan.detect_index]
        main["roles"] = [r for r in _roles(main) if r != "detect"]
        if _restream_key(str(main.get("path") or "")) is not None:
            stream = _sub_stream_name(saved, plan.name)
            if stream is None:
                return None
            changes["go2rtc"] = {"streams": {stream: [go2rtc_source(plan.sub_url)]}}
            added: dict[str, Any] = {
                "path": RESTREAM.format(stream),
                "input_args": "preset-rtsp-restream",
                "roles": ["detect"],
            }
        else:
            added = {"path": plan.sub_url, "roles": ["detect"]}
            if main.get("input_args"):
                added["input_args"] = main["input_args"]
        camera["ffmpeg"] = {"inputs": [*inputs, added]}
    if plan.detect is not None:
        camera["detect"] = plan.detect
    if not camera:
        return None
    changes["cameras"] = {plan.name: camera}
    return changes


def _fingerprint(config: dict[str, Any], plan: _Plan) -> str:
    """The camera as the card showed it, and the change it named. Frigate
    masks every login in what it reports, so none is hashed."""
    camera = (config.get("cameras") or {}).get(plan.name) or {}
    streams = (config.get("go2rtc") or {}).get("streams") or {}
    shown = {
        "inputs": [[str(i.get("path") or ""), sorted(_roles(i))] for i in _inputs(camera)],
        "streams": {s: streams.get(s) for s in _camera_streams(config, plan.name)},
        "sub": source_key(plan.sub_url) if plan.sub_url else None,
        "sub_size": plan.sub_size,
        "detect": plan.detect,
    }
    return hashlib.sha256(json.dumps(shown, sort_keys=True, default=str).encode()).hexdigest()


def _change(plan: _Plan) -> str:
    """The change in words, after "so"."""
    if plan.sub_url is not None:
        sub = f"its {_shown(plan.sub_size)} substream" if plan.sub_size else "its substream"
        main = f"its {_shown(plan.main_size)} main stream" if plan.main_size else "its main stream"
        return f"{plan.friendly} is analysed from {sub} instead of {main}"
    detect = plan.detect or {}
    was = f" instead of {_shown(plan.analysed)}" if plan.analysed else ""
    return f"{plan.friendly} is analysed at {detect.get('width')}×{detect.get('height')}{was}"


async def _report(hass: HomeAssistant, name: str) -> CameraReport | None:
    """The last Health audit's report on camera ``name``."""
    return next((r for r in await last_camera_reports(hass) if r["frigate_name"] == name), None)


async def last_camera_reports(hass: HomeAssistant) -> list[CameraReport]:
    """How Frigate analysed each camera at the last Health audit."""
    from ..health_store import get_health_store  # noqa: PLC0415

    store = get_health_store(hass)
    audit = await store.get_last_audit() if store is not None else None
    reports = audit.get("cameras") if isinstance(audit, dict) else None
    return [
        r for r in reports or [] if isinstance(r, dict) and isinstance(r.get("frigate_name"), str)
    ]


async def _read(
    hass: HomeAssistant, client: FrigateClient, target: str
) -> tuple[dict[str, Any], dict[str, Any], _Plan] | dict[str, Any]:
    """Frigate's config, its saved file and the plan; or the error to answer."""
    try:
        config = await client.get_config()
        cameras = config.get("cameras") or {}
        name = _camera_name(hass, cameras, target)
        if name is None:
            return {"error": "Frigate has no such camera.", "frigate_cameras": sorted(cameras)}
        saved = await _saved_config(client)
    except FrigateError as exc:
        return {"error": str(exc)}
    return config, saved, await _plan(hass, config, saved, name, await _report(hass, name))


async def async_preview_fix_camera(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """A card fixing how Frigate analyses a camera, or what only the user can do."""
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": NOT_SET_UP}
    read = await _read(hass, client, str(arguments.get("camera") or ""))
    if isinstance(read, dict):
        return read
    config, _saved, plan = read
    result: dict[str, Any] = {"camera": plan.name, "name": plan.friendly}
    if plan.advice:
        result["how_to_fix"] = list(plan.advice)
    if not plan.changes:
        return {
            **result,
            "unchanged": True,
            "note": f"Selora AI has nothing to change in how Selora Vision analyses "
            f"{plan.friendly}" + (": tell the user how_to_fix." if plan.advice else "."),
        }
    return {
        **result,
        "requires_approval": True,
        "destructive": {
            "kind": "frigate_fix",
            "verb": "fix",
            "target_id": plan.name,
            "entity_id": "",
            "name": plan.friendly,
            "label": f"Restart Selora Vision so {_change(plan)}",
            "fingerprint": _fingerprint(config, plan),
        },
    }


async def async_fix_camera_confirmed(
    hass: HomeAssistant, name: str, fingerprint: str, _arguments: dict[str, Any]
) -> dict[str, Any]:
    """Make the change the card showed, if the camera is still as shown."""
    from .setup import async_reload_when_back  # noqa: PLC0415

    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": NOT_SET_UP}
    report = await _report(hass, name)
    lock: asyncio.Lock = hass.data.setdefault(DOMAIN, {}).setdefault(_WRITE_LOCK, asyncio.Lock())
    async with lock:
        read = await _read(hass, client, name)
        if isinstance(read, dict):
            return read
        config, saved, plan = read
        if plan.name != name:
            return {"error": "Frigate no longer has that camera."}
        if not plan.changes or _fingerprint(config, plan) != fingerprint:
            return {"error": "The camera changed since it was shown; ask again."}
        changes = _changes(saved, plan)
        if changes is None:
            return {"error": "Another camera already reads that camera's substream stream."}
        try:
            await client.set_config(changes)
        except FrigateError as exc:
            return {"error": str(exc)}
    if report is not None:
        from ..home_suggestions import async_dismiss_home_suggestion  # noqa: PLC0415

        await async_dismiss_home_suggestion(hass, suggestion_id(report))
    try:
        await client.restart()
    except FrigateError as exc:
        _LOGGER.warning("Frigate saved the camera's analysis but did not restart: %s", exc)
        return {
            "camera": name,
            "name": plan.friendly,
            "restart_failed": True,
            "done": f"Saved: once Selora Vision restarts, {_change(plan)}. "
            "It could not restart itself.",
        }
    hass.async_create_background_task(
        async_reload_when_back(hass, client), f"{DOMAIN}_frigate_reload"
    )
    return {
        "camera": name,
        "name": plan.friendly,
        "done": f"Done: {_change(plan)}. Selora Vision is restarting, about a minute.",
    }


def suggestion_id(report: CameraReport) -> str:
    """A camera's fix row, named by its issues and inputs: the same issues
    after its inputs changed are a new row, not one dismissed before."""
    shown = [
        report["issues"],
        [[sorted(s["roles"]), s["width"], s["height"]] for s in report["streams"]],
    ]
    digest = hashlib.sha256(json.dumps(shown, sort_keys=True).encode()).hexdigest()[:12]
    return f"{SUGGESTION_PREFIX}{report['frigate_name']}:{digest}"


def _row(report: CameraReport) -> HomeSuggestion:
    detect = report["detect"]
    source = next((s for s in report["streams"] if "detect" in s["roles"]), None)
    return {
        "suggestion_id": suggestion_id(report),
        "type": "fix_camera",
        "entities": [{"entity_id": report["entity_id"] or "", "name": report["name"]}],
        "collection": {"source": "frigate", "name": report["name"], "ref": report["frigate_name"]},
        "camera_report": {
            "issues": list(report["issues"]),
            "analysed": [detect["width"], detect["height"]]
            if detect["width"] and detect["height"]
            else None,
            "stream": [source["width"], source["height"]]
            if source and source["width"] and source["height"]
            else None,
            "records_too": bool(source and "record" in source["roles"]),
            "skipped_fps": report["stats"].get("skipped_fps"),
        },
    }


async def async_fix_camera_suggestions(hass: HomeAssistant) -> list[HomeSuggestion]:
    """One row per camera the last Health audit found costing the hub, or
    getting no picture.

    A camera that only falls behind (``skipping_frames``) is a victim when
    another camera has issues of its own: it is named on that camera's row,
    since fixing that one is what helps it.
    """
    if FrigateClient.from_hass(hass) is None:
        return []
    from ..entity_filter import resolve_ignored_entity_ids  # noqa: PLC0415

    ignored = resolve_ignored_entity_ids(hass)
    reports = [
        r
        for r in await last_camera_reports(hass)
        if r.get("enabled", True)
        and r.get("issues")
        and not (r.get("entity_id") and r["entity_id"] in ignored)
    ]
    causes = [r for r in reports if set(r["issues"]) - {SKIPPING_FRAMES}]
    rows = [_row(r) for r in causes or reports]
    if causes and (victims := [r for r in reports if r not in causes]):
        rows[0]["members"] = [
            {"entity_id": r["entity_id"] or "", "name": r["name"]} for r in victims
        ]
    return rows
