"""How Frigate analyses each camera, and what about it costs the hub.

Built once per Health audit, for the Health page's camera check and for the
insights export, where Selora alerts on ``issues``. Each issue is a stable code:

* ``detect_above_720p`` — Frigate analyses frames larger than 1280×720. Left
  without a detect size, Frigate probes the stream and analyses it whole: two
  4K cameras starved every other camera on a hub.
* ``detect_stream_heavy`` — the stream decoded for detection is larger than
  1080p, so the hub decodes it in full at every frame whatever size it then
  analyses (a "substream" set to the camera's main profile).
* ``detect_stream_unreadable`` — Frigate's ffprobe got no answer from the
  detect stream on two probes in a row.
* ``no_video``, ``skipping_frames``, ``high_cpu`` — from Frigate's live rates,
  which move every second: raised only once present for
  ``HEALTH_EVENT_CONFIRM_SECS`` with no audit in between missing them.
* ``stream_uneven`` — ``skipping_frames`` on a hub with power to spare, from a
  camera that is not itself costly: its frames arrive in bursts (weak Wi-Fi, a
  high bitrate), which nothing on the hub fixes.

Stream sizes come from Frigate's own ffprobe of each input
(``/api/ffprobe?paths=camera:<name>``), which uses the logins Frigate holds, so
none reach this side. It opens a connection to the camera, so it runs in the
background, once a day per camera (an hour after a failure, or as soon as the
camera's inputs change), and an audit reads the last answer.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import logging
import math
import time
from typing import TYPE_CHECKING, Any, Final

from homeassistant.helpers import entity_registry as er

from ..const import DOMAIN, HEALTH_EVENT_CONFIRM_SECS
from ..helpers import sanitize_untrusted_text
from .cameras import HEAVY_DETECT_PIXELS, fits_detect_box
from .client import FRIGATE_DOMAIN, FrigateClient, FrigateError, frigate_entry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..types import CameraDetect, CameraReport, CameraStats, CameraStream

_LOGGER = logging.getLogger(__name__)

DETECT_ABOVE_720P: Final = "detect_above_720p"
DETECT_STREAM_HEAVY: Final = "detect_stream_heavy"
DETECT_STREAM_UNREADABLE: Final = "detect_stream_unreadable"
NO_VIDEO: Final = "no_video"
SKIPPING_FRAMES: Final = "skipping_frames"
STREAM_UNEVEN: Final = "stream_uneven"
HIGH_CPU: Final = "high_cpu"
# Most telling first: a camera Frigate cannot read explains its other symptoms.
ISSUES: Final = (
    DETECT_STREAM_UNREADABLE,
    NO_VIDEO,
    SKIPPING_FRAMES,
    STREAM_UNEVEN,
    DETECT_ABOVE_720P,
    DETECT_STREAM_HEAVY,
    HIGH_CPU,
)
_SUSTAINED: Final = (NO_VIDEO, SKIPPING_FRAMES, HIGH_CPU)

# Frigate drops frames it cannot process in time; a few a second is a camera
# that has fallen behind, not a hiccup.
_SKIPPING_FPS: Final = 0.5
# The camera's detection process (motion, tracking) using more than a core.
_HIGH_CPU: Final = 100.0
# Below both, the hub has power to spare: a camera dropping frames is sent
# them unevenly, not refused them. % of the whole system; share of the frames
# a second the detectors can analyse.
_ROOM_SYSTEM_CPU: Final = 85.0
_ROOM_DETECTOR_LOAD: Final = 0.8
# A probe that failed this many times in a row is a stream that does not answer.
_UNREADABLE_AFTER: Final = 2

_PROBE_EVERY: Final = 24 * 3600
_PROBE_RETRY: Final = 3600
_STATE: Final = "_frigate_camera_analysis"


@dataclass
class _Probe:
    key: str  # the inputs probed (paths and roles): new inputs, new probe
    next_at: float  # time.time() the inputs are probed again
    measured_at: str | None  # the last probe Frigate answered; None before one
    streams: list[CameraStream]
    failures: int  # consecutive probes the detect input did not answer


@dataclass
class _State:
    # The Frigate server all of it is about.
    server: str | None = None
    probes: dict[str, _Probe] = field(default_factory=dict)
    # (camera, issue) -> time.time() it was first seen, for the live symptoms.
    since: dict[tuple[str, str], float] = field(default_factory=dict)
    # (camera, issue) pairs that held long enough to be issues.
    confirmed: set[tuple[str, str]] = field(default_factory=set)
    task: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class CameraReports:
    """The reports, or why there are none: ``reports`` is None without Frigate,
    and ``error`` says Frigate could not be read (the check did not run)."""

    reports: list[CameraReport] | None
    error: str | None = None


def _state(hass: HomeAssistant) -> _State:
    data = hass.data.setdefault(DOMAIN, {})
    state = data.get(_STATE)
    if not isinstance(state, _State):
        state = data[_STATE] = _State()
    return state


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None  # NaN or inf is no reading


def _inputs(camera: dict[str, Any]) -> list[dict[str, Any]]:
    inputs = (camera.get("ffmpeg") or {}).get("inputs") or []
    return [i for i in inputs if isinstance(i, dict)]


def _inputs_key(camera: dict[str, Any]) -> str:
    """The inputs' paths (as Frigate shows them, logins redacted) and roles."""
    shown = [
        [str(i.get("path") or ""), sorted(map(str, i.get("roles") or []))] for i in _inputs(camera)
    ]
    return hashlib.sha256(json.dumps(shown).encode()).hexdigest()


def _codec(name: Any) -> str | None:
    text = str(name or "").strip().lower()
    if not text:
        return None
    if text.startswith(("h.265", "hevc")):
        return "h265"
    if text.startswith(("h.264", "avc")):
        return "h264"
    return sanitize_untrusted_text(text.split(" ", 1)[0], 20)


def _measured(camera: dict[str, Any], probes: list[Any]) -> list[CameraStream]:
    """Each input with the video Frigate's ffprobe found in it.

    ``readable`` is ffprobe's own verdict (its return code). A stream that
    answered can still come back 0×0, when ffprobe gave up on the size before
    the picture said it: the size is then unknown, not the stream unreadable.
    """
    streams: list[CameraStream] = []
    for index, source in enumerate(_inputs(camera)):
        probe = probes[index] if index < len(probes) and isinstance(probes[index], dict) else {}
        stdout = probe.get("stdout")
        found = stdout.get("streams") if isinstance(stdout, dict) else None
        # Audio streams have no width; a video one has one, 0 when unknown.
        video = next(
            (s for s in found or [] if isinstance(s, dict) and s.get("width") is not None), None
        )
        width = _number(video.get("width")) if video else None
        height = _number(video.get("height")) if video else None
        sized = bool(width and height)
        streams.append(
            {
                "roles": [str(r) for r in source.get("roles") or []],
                "width": int(width) if sized and width else None,
                "height": int(height) if sized and height else None,
                "codec": _codec(video.get("codec_long_name") or video.get("codec_name"))
                if video
                else None,
                "readable": probe.get("return_code") == 0,
            }
        )
    return streams


def _unprobed(camera: dict[str, Any]) -> list[CameraStream]:
    return [
        {
            "roles": [str(r) for r in source.get("roles") or []],
            "width": None,
            "height": None,
            "codec": None,
            "readable": None,
        }
        for source in _inputs(camera)
    ]


def _detect_input(streams: list[CameraStream]) -> CameraStream | None:
    return next((s for s in streams if "detect" in s["roles"]), None)


def _stale(probe: _Probe | None, key: str, now: float) -> bool:
    return probe is None or probe.key != key or now >= probe.next_at


async def _async_probe(
    hass: HomeAssistant, client: FrigateClient, cameras: dict[str, dict[str, Any]]
) -> None:
    """Measure each camera's inputs, one camera at a time."""
    state = _state(hass)
    for name, camera in cameras.items():
        key = _inputs_key(camera)
        previous = state.probes.get(name)
        if previous is not None and previous.key != key:
            previous = None
        now = time.time()
        try:
            probes = await client.ffprobe(name, len(_inputs(camera)))
        except FrigateError as exc:
            # Frigate failing to probe says nothing about the camera: keep
            # what was measured, and ask again in an hour.
            _LOGGER.debug("Frigate could not probe %s: %s", name, exc)
            state.probes[name] = _Probe(
                key,
                now + _PROBE_RETRY,
                previous.measured_at if previous else None,
                previous.streams if previous else _unprobed(camera),
                previous.failures if previous else 0,
            )
            continue
        streams = _measured(camera, probes)
        detect = _detect_input(streams)
        answered = detect is None or detect["readable"] is True
        failures = 0 if answered else (previous.failures if previous else 0) + 1
        # A stream that did not answer, or answered without its size, is asked
        # again in an hour; a measured one the next day.
        unsized = any(s["readable"] and not s["width"] for s in streams)
        retry = failures or unsized
        state.probes[name] = _Probe(
            key,
            now + (_PROBE_RETRY if retry else _PROBE_EVERY),
            datetime.now(UTC).isoformat(),
            streams,
            failures,
        )


def _start_probes(hass: HomeAssistant, client: FrigateClient, cameras: dict[str, Any]) -> None:
    state = _state(hass)
    if state.task is not None and not state.task.done():
        return
    now = time.time()
    stale = {
        name: camera
        for name, camera in cameras.items()
        if isinstance(camera, dict) and _stale(state.probes.get(name), _inputs_key(camera), now)
    }
    if stale:
        state.task = hass.async_create_background_task(
            _async_probe(hass, client, stale), f"{DOMAIN}_frigate_camera_probe"
        )


async def async_stop_probes(hass: HomeAssistant) -> None:
    """Cancel a probe still running when the entry unloads."""
    state = hass.data.get(DOMAIN, {}).get(_STATE)
    task = state.task if isinstance(state, _State) else None
    if task is not None and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def _camera_entities(hass: HomeAssistant) -> dict[str, er.RegistryEntry]:
    """The camera entity per Frigate camera, from the Frigate integration's
    entry the reports read (unique id ``<entry id>:camera:<camera>``). None
    when Selora AI's own settings name the server: another entry's camera of
    the same name is a different camera."""
    frigate = frigate_entry(hass)
    if frigate is None:
        return {}
    prefix = f"{frigate.entry_id}:camera:"
    return {
        str(entry.unique_id).removeprefix(prefix): entry
        for entry in er.async_entries_for_config_entry(er.async_get(hass), frigate.entry_id)
        if entry.platform == FRIGATE_DOMAIN
        and entry.domain == "camera"
        and str(entry.unique_id).startswith(prefix)
    }


def _stats(name: str, stats: dict[str, Any]) -> CameraStats:
    rates = (stats.get("cameras") or {}).get(name)
    if not isinstance(rates, dict):
        return {}
    out: CameraStats = {}
    for key in ("camera_fps", "process_fps", "skipped_fps", "detection_fps"):
        if (value := _number(rates.get(key))) is not None:
            out[key] = value  # type: ignore[literal-required]
    usages = stats.get("cpu_usages") or {}
    for key, pid in (
        ("detect_cpu", rates.get("pid")),
        ("decode_cpu", rates.get("ffmpeg_pid")),
        ("system_cpu", "frigate.full_system"),
    ):
        usage = usages.get(str(pid)) if pid is not None else None
        if isinstance(usage, dict) and (value := _number(usage.get("cpu"))) is not None:
            out[key] = value  # type: ignore[literal-required]
    if (load := _detector_load(stats)) is not None:
        out["detector_load"] = load
    return out


def _detector_load(stats: dict[str, Any]) -> float | None:
    """The share of the frames a second its detectors can analyse that Frigate
    asks of them: they share one queue, each taking a frame per inference."""
    detectors = stats.get("detectors")
    asked = _number(stats.get("detection_fps"))
    if not isinstance(detectors, dict) or asked is None:
        return None
    speeds = [
        speed
        for d in detectors.values()
        if isinstance(d, dict) and (speed := _number(d.get("inference_speed"))) and speed > 0
    ]
    if not speeds:
        return None
    return round(asked / sum(1000 / speed for speed in speeds), 3)


def hub_has_room(stats: CameraStats) -> bool:
    """Whether Frigate's last statistics show the hub with power to spare.
    False when they do not say: a camera falls behind on a busy hub unless
    shown otherwise."""
    cpu, load = stats.get("system_cpu"), stats.get("detector_load")
    return (
        cpu is not None
        and load is not None
        and cpu < _ROOM_SYSTEM_CPU
        and load < _ROOM_DETECTOR_LOAD
    )


def _symptoms(stats: CameraStats) -> dict[str, bool]:
    """Whether each live symptom shows, for those whose rate Frigate gave: a
    missing rate proves nothing either way."""
    found: dict[str, bool] = {}
    if (fps := stats.get("camera_fps")) is not None:
        found[NO_VIDEO] = fps == 0
    if (skipped := stats.get("skipped_fps")) is not None:
        found[SKIPPING_FRAMES] = skipped > _SKIPPING_FPS
    if (cpu := stats.get("detect_cpu")) is not None:
        found[HIGH_CPU] = cpu > _HIGH_CPU
    return found


def _issues(
    state: _State,
    name: str,
    detect: CameraDetect,
    streams: list[CameraStream],
    probe: _Probe | None,
    symptoms: dict[str, bool],
    now: float,
    room: bool,
) -> list[str]:
    found: set[str] = set()
    width, height = detect["width"], detect["height"]
    if width and height and not fits_detect_box((width, height)):
        found.add(DETECT_ABOVE_720P)
    source = _detect_input(streams)
    if (
        source
        and source["width"]
        and source["height"]
        and source["width"] * source["height"] > HEAVY_DETECT_PIXELS
    ):
        found.add(DETECT_STREAM_HEAVY)
    if probe is not None and probe.failures >= _UNREADABLE_AFTER:
        found.add(DETECT_STREAM_UNREADABLE)
    # Live rates move every second: an issue only once it has held across
    # readings. Without its rate, a confirmed issue stands and a pending one
    # starts over, since nothing shows it held in between.
    for issue in _SUSTAINED:
        key = (name, issue)
        shows = symptoms.get(issue)
        if shows:
            first = state.since.setdefault(key, now)
            if now - first >= HEALTH_EVENT_CONFIRM_SECS:
                state.confirmed.add(key)
        elif shows is False or key not in state.confirmed:
            state.since.pop(key, None)
            state.confirmed.discard(key)
        if key in state.confirmed:
            found.add(issue)
    # Dropping frames on a hub with power to spare, without costing it: the
    # frames arrive unevenly. Relabelled, not timed apart, so a hub whose load
    # moves does not restart the clock.
    if room and SKIPPING_FRAMES in found and HIGH_CPU not in found:
        found = found - {SKIPPING_FRAMES} | {STREAM_UNEVEN}
    return [issue for issue in ISSUES if issue in found]


def _forget_pending(state: _State) -> None:
    """An audit without rates: a pending symptom starts over."""
    state.since = {k: v for k, v in state.since.items() if k in state.confirmed}


async def async_camera_reports(hass: HomeAssistant) -> CameraReports:
    """Every Frigate camera's report; none without Frigate.

    Frigate's answers are another program's JSON: a shape this reading does not
    expect fails the camera check alone, never the audit it runs before.
    """
    try:
        return await _async_camera_reports(hass)
    except (KeyError, TypeError, AttributeError, ValueError, OverflowError) as exc:
        _LOGGER.warning("Frigate answered in a shape the camera check can't read: %r", exc)
        return CameraReports(None, "Frigate answered in an unexpected shape")


async def _async_camera_reports(hass: HomeAssistant) -> CameraReports:
    client = FrigateClient.from_hass(hass)
    if client is None:
        _forget_pending(_state(hass))
        return CameraReports(None)
    state = _state(hass)
    if state.server != client.url:
        # Another server's cameras can share names and paths with these.
        await async_stop_probes(hass)
        state = hass.data[DOMAIN][_STATE] = _State(server=client.url)
    try:
        config = await client.get_config()
    except FrigateError as exc:
        _forget_pending(state)
        return CameraReports(None, str(exc))
    try:
        stats: dict[str, Any] | None = await client.get_stats()
    except FrigateError as exc:
        # The configuration still answers; the live symptoms keep their clocks.
        _LOGGER.debug("No Frigate statistics for the camera check: %s", exc)
        stats = None
    cameras = {
        str(name): camera
        for name, camera in (config.get("cameras") or {}).items()
        if isinstance(camera, dict)
    }
    for gone in set(state.probes) - set(cameras):
        state.probes.pop(gone)
    # A camera turned off starts its clocks again once back on.
    live = {n for n, c in cameras.items() if c.get("enabled") is not False}
    state.since = {k: v for k, v in state.since.items() if k[0] in live}
    state.confirmed = {k for k in state.confirmed if k[0] in live}
    entities = _camera_entities(hass)
    now = time.time()
    reports: list[CameraReport] = []
    for name, camera in sorted(cameras.items()):
        enabled = camera.get("enabled") is not False
        raw = camera.get("detect") or {}
        detect: CameraDetect = {
            "width": int(w) if (w := _number(raw.get("width"))) else None,
            "height": int(h) if (h := _number(raw.get("height"))) else None,
            "fps": _number(raw.get("fps")),
        }
        probe = state.probes.get(name)
        current = probe if probe is not None and probe.key == _inputs_key(camera) else None
        streams = current.streams if current else _unprobed(camera)
        camera_stats = _stats(name, stats) if stats is not None else {}
        entity = entities.get(name)
        reports.append(
            {
                "frigate_name": name,
                "name": sanitize_untrusted_text(
                    str(camera.get("friendly_name") or name.replace("_", " ").title()), 60
                ),
                "device_id": entity.device_id if entity else None,
                "entity_id": entity.entity_id if entity else None,
                "enabled": enabled,
                "detect": detect,
                "streams": streams,
                "streams_measured_at": current.measured_at if current else None,
                "stats": camera_stats,
                # A camera turned off in Frigate costs nothing and sends nothing.
                "issues": _issues(
                    state,
                    name,
                    detect,
                    streams,
                    current,
                    _symptoms(camera_stats),
                    now,
                    hub_has_room(camera_stats),
                )
                if enabled
                else [],
            }
        )
    _start_probes(hass, client, {n: cameras[n] for n in live})
    return CameraReports(reports)
