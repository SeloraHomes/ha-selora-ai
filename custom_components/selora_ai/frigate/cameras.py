"""Which of the home's cameras Frigate could watch, and their Frigate config.

A camera is a device: one Home Assistant entity per stream it serves (Reolink's
Clear and Fluent, UniFi Protect's High/Medium/Low, an ONVIF camera's profiles).
The main stream is recorded and the smallest substream is what Frigate runs
detection on. A camera with a single stream uses it for both, with detection
scaled down.

Stream URLs carry the camera's credentials. They are resolved here, written to
Frigate, and nowhere else: what leaves this module for a tool result, a card or
a log is ``shown()`` — scheme, host and path only.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from ..entity_filter import resolve_ignored_entity_ids
from .client import FRIGATE_DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_SCHEMES: Final = frozenset({"rtsp", "rtsps", "rtspx", "rtmp", "rtmps", "http", "https"})
_DEFAULT_PORTS: Final = {"rtsp": 554, "rtsps": 322, "rtspx": 322, "rtmp": 1935, "rtmps": 443}
_LOCAL_SUFFIXES: Final = (".local", ".lan", ".home", ".internal", ".home.arpa", ".localdomain")

# Words in an entity's translation key, unique id or name that say which
# stream it serves. Substreams are ranked: the first found is detected on.
_MAIN_WORDS: Final = frozenset({"main", "mainstream", "clear", "high", "major"})
_SUB_RANK: Final = {
    "sub": 0,
    "substream": 0,
    "fluent": 0,
    "medium": 1,
    "ext": 1,
    "balanced": 1,
    "low": 2,
    "minor": 2,
}
# A snapshot entity is a still image, not a stream.
_NOT_A_STREAM: Final = frozenset({"snapshot", "snapshots"})

# go2rtc restreams every camera to Frigate on this address, so each camera is
# connected to once however many roles read it.
_RESTREAM: Final = "rtsp://127.0.0.1:8554/{}"
_SINGLE_STREAM_DETECT: Final = {"width": 1280, "height": 720}

DEFAULT_OBJECTS: Final = ("person",)
OBJECT_LABEL: Final = re.compile(r"^[a-z_]{2,30}$")
MAX_OBJECTS: Final = 10


@dataclass(frozen=True)
class Stream:
    entity_id: str
    url: str  # carries credentials: written to Frigate, never shown


@dataclass(frozen=True)
class Candidate:
    """A camera Frigate does not have yet."""

    device_id: str | None
    name: str
    camera: str  # its name in Frigate
    area: str | None
    main: Stream
    sub: Stream | None

    @property
    def entity_ids(self) -> list[str]:
        """The Home Assistant entities it streams from; none for a camera
        found on the network."""
        streams = [self.main] + ([self.sub] if self.sub else [])
        return [s.entity_id for s in streams if s.entity_id]


@dataclass(frozen=True)
class Skipped:
    entity_id: str
    name: str
    reason: str


def shown(url: str) -> str:
    """``url`` without its credentials or query: safe for a card or the model."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}{parts.path}"


# Query parameters that say how to reach a stream, not which one: logins
# (Frigate redacts them) and UniFi's SRTP flag (dropped when written to go2rtc).
_NOT_IDENTITY: Final = frozenset(
    {"user", "username", "password", "pass", "pwd", "token", "auth", "enablesrtp"}
)


def source_key(url: str) -> tuple[str, int, str, str] | None:
    """Host, port, path and query: what a stream is, whatever its scheme or
    login.

    Frigate redacts credentials and go2rtc wraps sources (``ffmpeg:…#video=copy``),
    so only these survive in both what Home Assistant and Frigate report. The
    query stays, minus logins and transport flags: Reolink's FLV and RTMP
    streams name the NVR channel there, on one shared host and path.
    """
    url = url.strip()
    if url.startswith("ffmpeg:"):
        url = url[len("ffmpeg:") :]
    try:
        parts = urlsplit(url.split("#", 1)[0])
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname:
        return None
    scheme = parts.scheme.lower()
    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in _NOT_IDENTITY
        )
    )
    return (
        parts.hostname.lower(),
        port or _DEFAULT_PORTS.get(scheme, 0),
        parts.path.rstrip("/"),
        query,
    )


def is_local_host(host: str) -> bool:
    """A private, link-local or loopback address, or a LAN-only hostname."""
    host = host.strip().lower()
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host or host.endswith(_LOCAL_SUFFIXES)
    return address.is_private or address.is_link_local or address.is_loopback


def _is_local(url: str) -> bool:
    return is_local_host(urlsplit(url).hostname or "")


def with_credentials(uri: str, username: str, password: str) -> str:
    """``uri`` with this login in it, replacing any it had."""
    parts = urlsplit(uri)
    host = parts.hostname or ""
    netloc = f"{quote(username, safe='')}:{quote(password, safe='')}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


# Stream paths by brand, for a camera that does not answer ONVIF.
_BRAND_STREAMS: Final = {
    "reolink": ("h264Preview_01_main", "h264Preview_01_sub"),
    "hikvision": ("Streaming/Channels/101", "Streaming/Channels/102"),
    "dahua": (
        "cam/realmonitor?channel=1&subtype=0",
        "cam/realmonitor?channel=1&subtype=1",
    ),
}


def brand_streams(brand: str | None, host: str) -> tuple[str, str] | None:
    """The main and sub RTSP URLs a brand serves, without credentials."""
    paths = _BRAND_STREAMS.get(brand or "")
    if paths is None:
        return None
    main, sub = paths
    return f"rtsp://{host}:554/{main}", f"rtsp://{host}:554/{sub}"


def existing_hosts(frigate_config: dict[str, Any]) -> set[str]:
    """Every camera host Frigate already reads."""
    return {key[0] for key in existing_sources(frigate_config)}


def for_go2rtc(url: str) -> str:
    """The URL as go2rtc should open it.

    Cameras serve RTSPS with self-signed certificates, which go2rtc reads
    unverified as ``rtspx``; UniFi Protect's ``enableSrtp`` asks for SRTP,
    which go2rtc does not speak.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() != "rtsps":
        return url
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k != "enableSrtp"])
    return urlunsplit(("rtspx", parts.netloc, parts.path, query, parts.fragment))


def _words(entry: er.RegistryEntry) -> set[str]:
    text = " ".join(
        str(part or "")
        for part in (
            entry.translation_key,
            entry.unique_id,
            entry.original_name,
            entry.name,
            entry.entity_id,
        )
    ).lower()
    return set(re.split(r"[^a-z0-9]+", text))


def _sub_rank(words: set[str]) -> int | None:
    ranks = [rank for word, rank in _SUB_RANK.items() if word in words]
    return min(ranks) if ranks else None


def existing_sources(frigate_config: dict[str, Any]) -> set[tuple[str, int, str, str]]:
    """Every source Frigate already reads, directly or through go2rtc."""
    urls: list[str] = []
    for value in ((frigate_config.get("go2rtc") or {}).get("streams") or {}).values():
        urls.extend(value if isinstance(value, list) else [value])
    for camera in (frigate_config.get("cameras") or {}).values():
        for stream in ((camera or {}).get("ffmpeg") or {}).get("inputs") or []:
            urls.append(str((stream or {}).get("path") or ""))
    return {key for url in urls if isinstance(url, str) and (key := source_key(url))}


def existing_names(frigate_config: dict[str, Any]) -> set[str]:
    streams = (frigate_config.get("go2rtc") or {}).get("streams") or {}
    return set(frigate_config.get("cameras") or {}) | set(streams)


def unique_name(name: str, taken: set[str]) -> str:
    base = slugify(name) or "camera"
    candidate, n = base, 2
    while candidate in taken or f"{candidate}_sub" in taken:
        candidate, n = f"{base}_{n}", n + 1
    taken.update({candidate, f"{candidate}_sub"})
    return candidate


async def _async_stream_source(hass: HomeAssistant, entity_id: str) -> str | None:
    """The camera's stream URL, credentials included.

    Imported here: the camera component pulls in its image libraries, which
    only an install with the camera integration loaded is sure to have.
    """
    from homeassistant.components.camera import async_get_stream_source  # noqa: PLC0415

    try:
        return await async_get_stream_source(hass, entity_id)
    except HomeAssistantError:
        return None


async def async_camera_candidates(
    hass: HomeAssistant, frigate_config: dict[str, Any]
) -> tuple[list[Candidate], list[Skipped]]:
    """The cameras Frigate could add, and the ones it could not, with why."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    area_reg = ar.async_get(hass)
    ignored = resolve_ignored_entity_ids(hass)
    by_device: dict[str, list[er.RegistryEntry]] = {}
    for entry in ent_reg.entities.values():
        if (
            entry.domain != "camera"
            or entry.platform == FRIGATE_DOMAIN
            or entry.disabled_by is not None
            or entry.entity_id in ignored
            or hass.states.get(entry.entity_id) is None
            or _NOT_A_STREAM & _words(entry)
        ):
            continue
        by_device.setdefault(entry.device_id or entry.entity_id, []).append(entry)

    in_frigate = existing_sources(frigate_config)
    taken = existing_names(frigate_config)
    candidates: list[Candidate] = []
    skipped: list[Skipped] = []
    for key, entries in sorted(by_device.items()):
        device = dev_reg.async_get(key) if entries[0].device_id else None
        state = hass.states.get(entries[0].entity_id)
        name = str(
            (device.name_by_user or device.name if device else None)
            or (state.attributes.get("friendly_name") if state else None)
            or entries[0].entity_id
        )
        streams: list[tuple[er.RegistryEntry, str]] = []
        reasons: list[str] = []
        for entry in entries:
            url = await _async_stream_source(hass, entry.entity_id)
            if not url:
                reasons.append("it has no stream Frigate can read")
            elif urlsplit(url).scheme.lower() not in _SCHEMES:
                reasons.append("its stream is in a format Frigate cannot read")
            elif not _is_local(url):
                reasons.append("it streams from the internet, not your network")
            elif source_key(url) in in_frigate:
                reasons.append("it is already in Frigate")
            else:
                streams.append((entry, url))
        if not streams:
            # The most telling reason: one stream already in Frigate means the
            # camera is, even if another entity of it has no stream.
            order = (
                "it is already in Frigate",
                "it streams from the internet, not your network",
                "its stream is in a format Frigate cannot read",
            )
            reason = next((r for r in order if r in reasons), reasons[0])
            skipped.append(Skipped(entries[0].entity_id, name, reason))
            continue
        ranked = [(e, url, _words(e)) for e, url in streams]
        main = next((s for s in ranked if _MAIN_WORDS & s[2]), None) or next(
            (s for s in ranked if _sub_rank(s[2]) is None), ranked[0]
        )
        subs = sorted(
            (s for s in ranked if s is not main and _sub_rank(s[2]) is not None),
            key=lambda s: _sub_rank(s[2]) or 0,
        )
        area_id = entries[0].area_id or (device.area_id if device else None)
        area = area_reg.async_get_area(area_id) if area_id else None
        candidates.append(
            Candidate(
                device_id=entries[0].device_id,
                name=name,
                camera=unique_name(name, taken),
                area=area.name if area else None,
                main=Stream(main[0].entity_id, for_go2rtc(main[1])),
                sub=Stream(subs[0][0].entity_id, for_go2rtc(subs[0][1])) if subs else None,
            )
        )
    return candidates, skipped


def clean_objects(value: Any) -> list[str]:
    """The object labels to track: lowercase words, the default when none."""
    items = value if isinstance(value, list) else []
    objects = sorted({str(o).strip().lower() for o in items} - {""})
    objects = [o for o in objects if OBJECT_LABEL.match(o)][:MAX_OBJECTS]
    return objects or list(DEFAULT_OBJECTS)


def build_config(
    candidates: list[Candidate], objects: list[str], record: bool, record_days: int | None
) -> dict[str, Any]:
    """The part of Frigate's configuration these cameras need, nothing global."""
    streams: dict[str, list[str]] = {}
    cameras: dict[str, Any] = {}
    for c in candidates:
        streams[c.camera] = [c.main.url]
        inputs: list[dict[str, Any]] = []
        main_roles = (["record"] if record else []) + ([] if c.sub else ["detect"])
        if main_roles:
            inputs.append(
                {
                    "path": _RESTREAM.format(c.camera),
                    "input_args": "preset-rtsp-restream",
                    "roles": main_roles,
                }
            )
        if c.sub:
            streams[f"{c.camera}_sub"] = [c.sub.url]
            inputs.append(
                {
                    "path": _RESTREAM.format(f"{c.camera}_sub"),
                    "input_args": "preset-rtsp-restream",
                    "roles": ["detect"],
                }
            )
        camera: dict[str, Any] = {
            # What Frigate and the Frigate integration show, instead of the
            # slug the camera is keyed by ("E1 Zoom", not "e1_zoom").
            "friendly_name": c.name,
            "ffmpeg": {"inputs": inputs},
            "detect": {"enabled": True, **({} if c.sub else _SINGLE_STREAM_DETECT)},
            "objects": {"track": objects},
            "record": {"enabled": record},
        }
        if record and record_days:
            retain = {"retain": {"days": record_days}}
            camera["record"].update({"alerts": retain, "detections": retain})
        cameras[c.camera] = camera
    return {"go2rtc": {"streams": streams}, "cameras": cameras}
