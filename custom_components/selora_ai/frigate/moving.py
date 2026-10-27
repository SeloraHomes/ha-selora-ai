"""Pointing a camera already in Frigate at its new address, behind a card.

A camera that gets a new address from the router keeps its name, history,
Home Assistant entities and automations in Frigate only if Frigate is told
where it went; adding it again makes a second camera.

Frigate masks the login in every stream it reports, so the new address
cannot be written from what ``/api/config`` says. The confirm reads the saved
configuration (``/api/config/raw``), swaps the host (and the port, when one is
given) in the camera's own sources, and writes them back: the login is never
shown, logged, sent to the model or typed again.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import TYPE_CHECKING, Any, Final

import yaml

from ..const import DOMAIN
from ..helpers import sanitize_untrusted_text
from .cameras import is_local_host, source_key
from .client import FrigateClient, FrigateError
from .discovery import SUGGESTION_CACHE
from .removal import _camera_name, _streams_of

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_WRITE_LOCK: Final = "_frigate_move_lock"
# An IPv4 address or a hostname: what a card may name and a URL may carry.
_HOST: Final = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$"
)
# scheme://[login@]host[:port]rest, with go2rtc's "ffmpeg:" wrapper kept.
_SOURCE: Final = re.compile(
    r"^(?P<head>(?:ffmpeg:)?[a-z][a-z0-9+.-]*://)(?P<login>[^@/?#]*@)?"
    r"(?P<host>[^:/?#@]+)(?::(?P<port>\d+))?(?P<rest>[/?#].*)?$",
    re.IGNORECASE | re.DOTALL,
)
# go2rtc's restream, which Frigate reads a restreamed camera from.
_RESTREAM_HOST: Final = "127.0.0.1"

_NOT_SET_UP: Final = (
    "Frigate is not set up in Home Assistant: the Frigate integration has to be "
    "installed and connected first."
)


class _Refused(ValueError):
    """Why the move cannot be offered, for the reply."""


def _target(arguments: dict[str, Any]) -> tuple[str, int | None]:
    host = str(arguments.get("host") or "").strip().lower()
    port = arguments.get("port")
    if "://" in host:
        # The model passed the whole stream address the user gave; a port
        # written in it counts unless one is given on its own.
        match = _SOURCE.match(host)
        host = match.group("host") if match else ""
        if port in (None, "", 0) and match:
            port = match.group("port")
    if not _HOST.match(host) or not is_local_host(host):
        raise _Refused("Give the camera's new address on your network: an IP or a hostname.")
    if port in (None, "", 0):
        return host, None
    try:
        number = int(port)
    except (TypeError, ValueError):
        number = 0
    if not 0 < number < 65536:
        raise _Refused("The port has to be a number from 1 to 65535.")
    return host, number


def _camera_streams(config: dict[str, Any], name: str) -> list[str]:
    """The go2rtc streams the camera is read from: the ones Selora names after
    it, and any its inputs restream, whatever they are called."""
    streams = (config.get("go2rtc") or {}).get("streams") or {}
    found = list(_streams_of(config, name))
    camera = (config.get("cameras") or {}).get(name) or {}
    for stream in (camera.get("ffmpeg") or {}).get("inputs") or []:
        match = _SOURCE.match(str((stream or {}).get("path") or "").strip())
        if match and match.group("host") == _RESTREAM_HOST:
            key = (match.group("rest") or "").lstrip("/").split("?", 1)[0].split("#", 1)[0]
            if key in streams and key not in found:
                found.append(key)
    return found


def _sources(config: dict[str, Any], name: str) -> list[str]:
    """Every source the camera is read from: its go2rtc streams and its inputs."""
    streams = (config.get("go2rtc") or {}).get("streams") or {}
    urls: list[str] = []
    for stream in _camera_streams(config, name):
        value = streams.get(stream)
        urls.extend(value if isinstance(value, list) else [value])
    camera = (config.get("cameras") or {}).get(name) or {}
    urls.extend(
        str((i or {}).get("path") or "") for i in (camera.get("ffmpeg") or {}).get("inputs") or []
    )
    return [u for u in urls if isinstance(u, str)]


def _current_host(config: dict[str, Any], name: str) -> str:
    """The one address the camera is read from now."""
    hosts = {
        m.group("host").lower()
        for url in _sources(config, name)
        if (m := _SOURCE.match(url.strip())) and m.group("host") != _RESTREAM_HOST
    }
    if not hosts:
        raise _Refused("Frigate reads that camera from no network address Selora AI can change.")
    if len(hosts) > 1:
        raise _Refused("That camera is read from more than one address; change it in Frigate.")
    return hosts.pop()


def _resolve(
    config: dict[str, Any], name: str, arguments: dict[str, Any]
) -> tuple[str, str, int | None]:
    """The address the camera is read from, the one it moves to, and a port
    only when it changes."""
    host, port = _target(arguments)
    old = _current_host(config, name)
    # A port left out is the scheme's own: RTSP's and HTTP's differ.
    ports = {
        str(key[1]) if (key := source_key(url)) and key[1] else m.group("head").lower()
        for url in _sources(config, name)
        if (m := _SOURCE.match(url.strip())) and m.group("host").lower() == old
    }
    if port is not None and ports == {str(port)}:
        port = None
    if port is not None and len(ports) > 1:
        # RTSP on one port and HTTP on another: which one moved is a guess.
        raise _Refused(
            "That camera is read on several ports; give only its new address, "
            "or change the port in Frigate."
        )
    return old, host, port


def _moved(url: str, old: str, new: str, port: int | None) -> str:
    """``url`` read from ``new`` instead of ``old``; login, path and options kept."""
    match = _SOURCE.match(url.strip())
    if match is None or match.group("host").lower() != old:
        return url
    kept_port = str(port) if port else match.group("port")
    return (
        f"{match.group('head')}{match.group('login') or ''}{new}"
        f"{':' + kept_port if kept_port else ''}{match.group('rest') or ''}"
    )


def _card_fingerprint(config: dict[str, Any], name: str, host: str, port: int | None) -> str:
    """The camera as shown, and where the card said it would go."""
    return hashlib.sha256(json.dumps([_sources(config, name), host, port]).encode()).hexdigest()


def _friendly(config: dict[str, Any], name: str) -> str:
    camera = (config.get("cameras") or {}).get(name) or {}
    friendly = str(camera.get("friendly_name") or name.replace("_", " ").title())
    return sanitize_untrusted_text(friendly, 60)


async def async_preview_move_camera(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """A card for reading a Frigate camera from a new address, or why not."""
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
    try:
        old, host, port = _resolve(config, name, arguments)
    except _Refused as exc:
        return {"error": str(exc)}
    friendly = _friendly(config, name)
    if host == old and port is None:
        return {
            "unchanged": True,
            "camera": name,
            "note": f"Frigate already reads {friendly} from {old}.",
        }
    where = f"{host}:{port}" if port else host
    return {
        "requires_approval": True,
        "destructive": {
            "kind": "frigate_move",
            "verb": "move",
            "target_id": name,
            "entity_id": "",
            "name": friendly,
            "label": sanitize_untrusted_text(
                f"Restart Frigate so {friendly} is read from {where} instead of {old}", 300
            ),
            "fingerprint": _card_fingerprint(config, name, host, port),
        },
    }


async def _saved_config(client: FrigateClient) -> dict[str, Any]:
    """Frigate's saved configuration, logins included: never logged or returned."""
    raw = await client.get_raw_config()
    try:
        saved = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise FrigateError("Frigate's saved configuration could not be read.") from exc
    if not isinstance(saved, dict):
        raise FrigateError("Frigate's saved configuration could not be read.")
    return saved


def _changes(
    saved: dict[str, Any], name: str, old: str, new: str, port: int | None
) -> dict[str, Any]:
    """The config_data that reads the camera from ``new``: only what changes."""
    changes: dict[str, Any] = {}
    streams = (saved.get("go2rtc") or {}).get("streams") or {}
    for stream in _camera_streams(saved, name):
        value = streams.get(stream)
        sources = value if isinstance(value, list) else [value]
        moved = [_moved(s, old, new, port) if isinstance(s, str) else s for s in sources]
        if moved != sources:
            changes.setdefault("go2rtc", {}).setdefault("streams", {})[stream] = moved
    camera = (saved.get("cameras") or {}).get(name) or {}
    inputs = (camera.get("ffmpeg") or {}).get("inputs") or []
    moved_inputs = [
        {**i, "path": _moved(str(i.get("path") or ""), old, new, port)}
        if isinstance(i, dict) and i.get("path")
        else i
        for i in inputs
    ]
    if moved_inputs != inputs:
        changes.setdefault("cameras", {}).setdefault(name, {})["ffmpeg"] = {"inputs": moved_inputs}
    onvif = camera.get("onvif") or {}
    if str(onvif.get("host") or "").lower() == old:
        changes.setdefault("cameras", {}).setdefault(name, {})["onvif"] = {"host": new}
    return changes


async def async_move_camera_confirmed(
    hass: HomeAssistant, name: str, fingerprint: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Read the camera from the address the card showed, if it is still as shown."""
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
            try:
                old, host, port = _resolve(config, name, arguments)
            except _Refused as exc:
                return {"error": str(exc)}
            if _card_fingerprint(config, name, host, port) != fingerprint:
                return {"error": "The camera changed since it was shown; ask again."}
            changes = _changes(await _saved_config(client), name, old, host, port)
            if not changes:
                return {
                    "error": "Frigate's saved configuration no longer reads that camera from there."
                }
            await client.set_config(changes)
        except FrigateError as exc:
            return {"error": str(exc)}
    hass.data[DOMAIN].pop(SUGGESTION_CACHE, None)
    where = f"{host}:{port}" if port else host
    friendly = _friendly(config, name)
    try:
        await client.restart()
    except FrigateError as exc:
        _LOGGER.warning("Frigate saved the camera's address but did not restart: %s", exc)
        return {
            "camera": name,
            "name": friendly,
            "restart_failed": True,
            "done": f"Saved: Frigate reads {friendly} from {where} once it restarts. "
            "It could not restart itself.",
        }
    hass.async_create_background_task(
        async_reload_when_back(hass, client), f"{DOMAIN}_frigate_reload"
    )
    return {
        "camera": name,
        "name": friendly,
        "done": f"Frigate now reads {friendly} from {where}; it is restarting, about a minute.",
    }


async def _answers(host: str, port: int) -> bool:
    """Whether anything listens at ``host:port`` within a couple of seconds."""
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 2)
    except (OSError, TimeoutError):
        return False
    writer.close()
    return True


async def async_moved_camera(config: dict[str, Any], streams: tuple[str, ...]) -> str | None:
    """The Frigate camera these stream addresses look like, moved: one read
    from the same port and path on another address that no longer answers.

    Two cameras of one brand share a path, so a match alone is not a move.
    """
    keys = {key[1:] for url in streams if (key := source_key(url))}
    hosts = {key[0] for url in streams if (key := source_key(url))}
    for name in config.get("cameras") or {}:
        for url in _sources(config, name):
            key = source_key(url)
            if key is None or key[0] in hosts | {_RESTREAM_HOST} or key[1:] not in keys:
                continue
            # No known port (an HTTP stream without one): nothing to probe.
            if not key[1]:
                continue
            if not await _answers(key[0], key[1]):
                return name
    return None
