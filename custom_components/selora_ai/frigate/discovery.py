"""Cameras on the network that Home Assistant does not have.

Three sources, merged by address:

* an ONVIF WS-Discovery probe of the LAN — most IP cameras answer it;
* Home Assistant's own discovery flows that nobody set up, when the flow
  names the camera's address;
* addresses the user names in chat.

The probe takes a few seconds, so its answer is cached and refreshed on a
timer: the chat home reads the cache and never waits on a scan. Cameras that
ignore the multicast probe are found by asking each local address directly,
at most once a day on the timer or on demand in developer mode.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import logging
import re
import socket
import time
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import unquote, urlsplit, urlunsplit
import uuid
from xml.etree import ElementTree as ET

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.event import async_call_later

from ..const import DOMAIN
from .cameras import is_local_host
from .client import frigate_entry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_MULTICAST: Final = ("239.255.255.250", 3702)
_PROBE_WAIT: Final = 3.0
_MAX_DATAGRAM: Final = 64 * 1024
_CACHE: Final = "_frigate_network_cameras"
SUGGESTION_CACHE: Final = "_frigate_suggestion_cache"
_SCAN_DELAY: Final = 60
_SCAN_INTERVAL: Final = 1800
# The direct sweep of every local address, at most this often on the timer.
_SWEEP_CACHE: Final = "_frigate_swept_cameras"
_SWEEP_INTERVAL: Final = 86400
_SWEEP_MIN_PREFIX: Final = 24
# Each network is /24 at most; a hub on several (its LAN, a link to the
# Frigate host) sweeps each, up to this many addresses in all.
_SWEEP_MAX_NETWORKS: Final = 4

# Home Assistant integrations whose discovery flows are cameras, and the brand
# each stands for when its streams have to be guessed.
_CAMERA_DOMAINS: Final = {
    "onvif": None,
    "reolink": "reolink",
    "amcrest": "dahua",
    "dahua": "dahua",
    "hikvision": "hikvision",
    "axis": None,
    "foscam": None,
    "doorbird": None,
}
_HOST_PLACEHOLDERS: Final = ("host", "ip_address", "ip", "address")
_STREAM_SCHEMES: Final = frozenset({"rtsp", "rtsps", "rtspx", "rtmp", "rtmps", "http", "https"})
_BRANDS: Final = ("reolink", "hikvision", "dahua", "amcrest")
_REOLINK_MODELS: Final = re.compile(
    r"\b(E1|RLC|RLN|Lumus|Argus|TrackMix|Duo|CX\d|Elite|Go ?Plus)", re.IGNORECASE
)
# Names cameras report out of the box, which say nothing about which camera.
_GENERIC_NAME: Final = re.compile(
    r"^(ipc|ip ?cam(era)?|camera|nvt|onvif|network ?camera)([-_ ].*)?$", re.IGNORECASE
)

_PROBE: Final = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
    'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
    'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
    'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
    "<e:Header><w:MessageID>uuid:{message_id}</w:MessageID>"
    "<w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>"
    "<w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>"
    "</e:Header><e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types>"
    "</d:Probe></e:Body></e:Envelope>"
)


@dataclass(frozen=True)
class NetworkCamera:
    host: str
    port: int
    name: str
    brand: str | None
    source: str  # "onvif" | "home_assistant" | "user"
    # Stream addresses the user gave, main first, without credentials. A
    # camera with these is read from them, not over ONVIF.
    streams: tuple[str, ...] = ()


def _brand(text: str) -> str | None:
    lowered = text.lower()
    named = next((b if b != "amcrest" else "dahua" for b in _BRANDS if b in lowered), None)
    if named is None and _REOLINK_MODELS.search(text):
        # Reolink names its hardware by model alone ("E1Zoom"), not by maker.
        return "reolink"
    return named


def _camera_name(fields: dict[str, str]) -> str | None:
    """What to call a probed camera: the name it reports, unless that is a
    factory placeholder ("IPC-BO", "IP Camera"), then its hardware model.

    Reolink reports "IPC-BO" for every model, which named a Lumus Pro on the
    chat home as something nobody recognised.
    """
    name = fields.get("name", "").strip()
    if name and not _GENERIC_NAME.match(name):
        return name
    hardware = fields.get("hardware", "").strip()
    if not hardware:
        return name or None
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z][a-z])", " ", hardware).replace("Wi Fi", "WiFi")
    return spaced


def parse_probe_match(data: bytes) -> NetworkCamera | None:
    """A camera from one WS-Discovery ProbeMatch, or None."""
    try:
        root = ET.fromstring(data)  # noqa: S314 — one datagram, size-capped
    except ET.ParseError:
        return None
    xaddrs = scopes = ""
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "XAddrs":
            xaddrs = element.text or ""
        elif tag == "Scopes":
            scopes = element.text or ""
    for xaddr in xaddrs.split():
        parts = urlsplit(xaddr)
        if parts.hostname and is_local_host(parts.hostname):
            break
    else:
        return None
    fields: dict[str, str] = {}
    for scope in scopes.split():
        path = urlsplit(scope).path.strip("/").split("/")
        if len(path) >= 2 and path[0] in ("name", "hardware", "mfr"):
            fields.setdefault(path[0], unquote(path[1]))
    return NetworkCamera(
        host=parts.hostname,
        port=parts.port or 80,
        name=_camera_name(fields) or parts.hostname,
        brand=_brand(" ".join(fields.values())),
        source="onvif",
    )


class _ProbeProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.found: dict[str, NetworkCamera] = {}

    def datagram_received(self, data: bytes, addr: tuple[str | Any, int]) -> None:
        if len(data) <= _MAX_DATAGRAM and (camera := parse_probe_match(data)) is not None:
            self.found.setdefault(camera.host, camera)


async def async_probe(
    wait: float = _PROBE_WAIT, targets: list[tuple[str, int]] | None = None
) -> list[NetworkCamera]:
    """ONVIF cameras answering a WS-Discovery probe within ``wait`` seconds:
    the multicast probe, or one sent to each of *targets*."""
    loop = asyncio.get_running_loop()
    try:
        transport, protocol = await loop.create_datagram_endpoint(
            _ProbeProtocol,
            local_addr=("0.0.0.0", 0),  # noqa: S104 — replies come back here
        )
    except OSError as exc:
        _LOGGER.debug("ONVIF probe could not open a socket: %s", exc)
        return []
    try:
        for target in targets or [_MULTICAST]:
            transport.sendto(_PROBE.format(message_id=uuid.uuid4()).encode(), target)
        await asyncio.sleep(wait)
    except OSError as exc:
        _LOGGER.debug("ONVIF probe could not be sent: %s", exc)
    finally:
        transport.close()
    return list(protocol.found.values())


async def _local_hosts(hass: HomeAssistant) -> list[str]:
    """Every address on the hub's own networks, /24 or smaller, but its own.

    Network by network, so a second network (the link to the Frigate host)
    never crowds out the LAN the cameras are on.
    """
    from homeassistant.components import network  # noqa: PLC0415

    hosts: list[str] = []
    networks: set[ipaddress.IPv4Network | ipaddress.IPv6Network] = set()
    for adapter in await network.async_get_adapters(hass):
        if not adapter["enabled"]:
            continue
        for configured in adapter["ipv4"]:
            if configured["network_prefix"] < _SWEEP_MIN_PREFIX:
                continue
            own = ipaddress.ip_address(configured["address"])
            subnet = ipaddress.ip_network(
                f"{configured['address']}/{configured['network_prefix']}", strict=False
            )
            if subnet.is_private and subnet not in networks:
                if len(networks) == _SWEEP_MAX_NETWORKS:
                    break
                networks.add(subnet)
                hosts.extend(str(h) for h in subnet.hosts() if h != own)
    return hosts


async def async_sweep(hass: HomeAssistant) -> list[NetworkCamera]:
    """Ask every address on the local network directly.

    Some cameras never answer the multicast probe (a Reolink E1 Zoom on
    Wi-Fi does not) but answer one sent to them. One small UDP packet per
    address, to the ONVIF discovery port only.
    """
    hosts = await _local_hosts(hass)
    return await async_probe(targets=[(h, _MULTICAST[1]) for h in hosts]) if hosts else []


def _flow_cameras(hass: HomeAssistant) -> list[NetworkCamera]:
    """Cameras Home Assistant discovered and nobody set up, where the flow
    names their address."""
    cameras: list[NetworkCamera] = []
    for flow in hass.config_entries.flow.async_progress():
        domain = flow.get("handler")
        if domain not in _CAMERA_DOMAINS:
            continue
        placeholders = (flow.get("context") or {}).get("title_placeholders") or {}
        host = next((str(placeholders[k]) for k in _HOST_PLACEHOLDERS if placeholders.get(k)), "")
        if not host or not is_local_host(host):
            continue
        cameras.append(
            NetworkCamera(
                host=host,
                port=80,
                name=str(placeholders.get("name") or host),
                brand=_CAMERA_DOMAINS[domain],
                source="home_assistant",
            )
        )
    return cameras


async def async_user_cameras(hass: HomeAssistant, hosts: list[str]) -> list[NetworkCamera]:
    """Addresses named in chat that are cameras' addresses on the LAN.

    A name must resolve to a local address: a model handed "the E1 Zoom from
    the Kitchen" passed "kitchen" as the address, and a dotless name looks
    like a LAN hostname. The port is unknown (0) until the camera is asked.
    """
    cameras: list[NetworkCamera] = []
    for given in dict.fromkeys(str(h).strip().lower() for h in hosts):
        if not given:
            continue
        try:
            ipaddress.ip_address(given)
            address = given
        except ValueError:
            try:
                infos = await hass.loop.getaddrinfo(given, None, family=socket.AF_INET)
            except OSError:
                continue
            address = str(infos[0][4][0]) if infos else ""
        if address and is_local_host(address):
            cameras.append(NetworkCamera(address, 0, given, None, "user"))
    return cameras


def user_streams(urls: list[str]) -> list[NetworkCamera]:
    """Stream addresses named in chat, one camera per address: the first URL
    for a host is its main stream, a second its substream.

    Credentials in a URL are dropped; the card asks for the login.
    """
    by_host: dict[str, list[str]] = {}
    for url in urls:
        try:
            parts = urlsplit(str(url).strip())
            host, port = (parts.hostname or "").lower(), parts.port
        except ValueError:
            continue
        if parts.scheme.lower() not in _STREAM_SCHEMES or not is_local_host(host):
            continue
        netloc = host + (f":{port}" if port else "")
        clean = urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, ""))
        streams = by_host.setdefault(host, [])
        if clean not in streams and len(streams) < 2:
            streams.append(clean)
    return [
        NetworkCamera(host, 80, host, None, "user", tuple(streams))
        for host, streams in by_host.items()
    ]


def merge(*groups: list[NetworkCamera]) -> list[NetworkCamera]:
    """One camera per address; the first source to name it wins, gaps filled from later."""
    merged: dict[str, NetworkCamera] = {}
    for group in groups:
        for camera in group:
            known = merged.get(camera.host)
            if known is None:
                merged[camera.host] = camera
                continue
            merged[camera.host] = NetworkCamera(
                known.host,
                known.port or camera.port,
                known.name if known.name != known.host else camera.name,
                known.brand or camera.brand,
                known.source,
                known.streams or camera.streams,
            )
    return list(merged.values())


async def async_scan(hass: HomeAssistant, *, sweep: bool = False) -> list[NetworkCamera]:
    """Probe now, and keep the answer for the chat home.

    ``sweep`` also asks every local address directly (``async_sweep``); its
    answer is kept until the next sweep, since sweeps are rare. Only probe
    answers are kept; discovery flows are read as they stand, so a camera set
    up or dismissed since is not offered again.
    """
    probed = await async_probe()
    domain_data = hass.data.setdefault(DOMAIN, {})
    if sweep:
        domain_data[_SWEEP_CACHE] = (time.monotonic(), await async_sweep(hass))
    domain_data[_CACHE] = (time.monotonic(), probed)
    # The chat home's list was built from the previous scan.
    domain_data.pop(SUGGESTION_CACHE, None)
    return cached_cameras(hass)


def cached_cameras(hass: HomeAssistant) -> list[NetworkCamera]:
    """The last scan and sweep, plus discovery flows as they stand now."""
    domain_data = hass.data.get(DOMAIN, {})
    cached, swept = domain_data.get(_CACHE), domain_data.get(_SWEEP_CACHE)
    return merge(cached[1] if cached else [], swept[1] if swept else [], _flow_cameras(hass))


def _sweep_due(hass: HomeAssistant) -> bool:
    swept = hass.data.get(DOMAIN, {}).get(_SWEEP_CACHE)
    return swept is None or time.monotonic() - swept[0] > _SWEEP_INTERVAL


@callback
def async_start_scanning(hass: HomeAssistant) -> CALLBACK_TYPE:
    """Scan shortly after start, then every half hour. Returns the canceller.

    A timer, never a task that sleeps: the scan must not hold up start-up,
    a reload, or a test waiting for Home Assistant to settle.
    """
    cancel: CALLBACK_TYPE | None = None

    @callback
    def _fire(_now: Any = None) -> None:
        nonlocal cancel
        # Without Frigate nothing reads the answer, so nothing probes the LAN.
        if frigate_entry(hass) is not None:
            hass.async_create_background_task(
                async_scan(hass, sweep=_sweep_due(hass)), f"{DOMAIN}_frigate_scan"
            )
        cancel = async_call_later(hass, _SCAN_INTERVAL, _fire)

    cancel = async_call_later(hass, _SCAN_DELAY, _fire)

    @callback
    def _stop() -> None:
        if cancel is not None:
            cancel()

    return _stop
