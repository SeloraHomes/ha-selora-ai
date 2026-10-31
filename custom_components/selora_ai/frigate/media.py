"""Links to an event's snapshot and clip, for an MCP client to hand the user.

An MCP client such as Claude cannot show the user an image a tool returns, and
cannot reach the home to fetch one. What it can do is give the user a link, which
the user's own browser opens: at the hub's remote address (a file Selora OS
delivers), past the login that guards it (the browser carries that login), or
at the home's local one.

Home Assistant's login is replaced by a signature: ``async_sign_path`` makes the
link valid for a few minutes, for this one path, with no token in it. The view
streams the media from Frigate, so Frigate's own address and login never leave
the hub, whichever way Frigate is connected.

A clip is not streamed straight through: Frigate builds it on the fly and sends
it chunked, with no length, ignoring ``Range``. Chrome plays that; Safari and
every WebKit view (the iOS and macOS apps) ask for ``bytes=0-1``, want a 206,
and show a struck-out play button otherwise. So the clip is fetched once into a
short-lived file and served from there, ranges and all.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from http import HTTPStatus
import json
import logging
import os
from pathlib import Path
import tempfile
import time
from typing import TYPE_CHECKING, BinaryIO, Final
from urllib.parse import urlsplit

from aiohttp import hdrs, web
from homeassistant.components.http import KEY_HASS, KEY_HASS_USER, HomeAssistantView
from homeassistant.components.http.auth import SIGN_QUERY_PARAM, async_sign_path
from homeassistant.helpers.network import NoURLAvailableError, get_url

from ..alexa_credential_file import CREDENTIAL_DIR_NAME
from .client import FrigateClient, FrigateError
from .events import _EVENT_ID

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

MEDIA_URL: Final = "/api/selora_ai/frigate/events/{event_id}/{kind}"
LINK_LIFETIME: Final = timedelta(minutes=15)
# The hub's remote address for Home Assistant (Connect's remote access, behind
# its login): ``{"remote_url": "https://..."}``, written atomically by Selora OS
# beside the Alexa credential, and removed when remote access is off. A file,
# not entry data: writing the entry means editing .storage with Core stopped.
# Not the MCP URL either: that resource takes only Connect's token.
REMOTE_URL_DIR: Final = CREDENTIAL_DIR_NAME
REMOTE_URL_FILE: Final = "remote_url.json"
# What the link asks for, and where Frigate keeps it. A snapshot falls back to
# the thumbnail, which every event has: snapshots can be off on a camera.
_SNAPSHOT: Final = "snapshot.jpg"
_CLIP: Final = "clip.mp4"
_FRIGATE_PATHS: Final = {
    _SNAPSHOT: ("/api/events/{id}/snapshot.jpg?bbox=1", "/api/events/{id}/thumbnail.jpg"),
    _CLIP: ("/api/events/{id}/clip.mp4",),
}
# What reaches the browser from Frigate's response: enough to play and seek a
# video, nothing that sets state (cookies) or names Frigate.
_PASSED_HEADERS: Final = (
    hdrs.CONTENT_TYPE,
    hdrs.CONTENT_LENGTH,
    hdrs.CONTENT_RANGE,
    hdrs.ACCEPT_RANGES,
    hdrs.LAST_MODIFIED,
    hdrs.ETAG,
)
# Fetched clips, in the system's temp dir: never the config dir, which backups
# carry. Reused for a few minutes (a browser asks for many ranges of one video,
# and a clip of an event still running grows). The disk they take is bounded:
# a few fetches at once, each at most _CLIP_MAX_BYTES, and finished clips kept
# up to a count and a total, newest first.
_CLIP_DIR: Final = "selora_ai_frigate_clips"
_CLIP_REUSE_SECONDS: Final = 5 * 60
_CLIPS_KEPT: Final = 8
_CLIPS_MAX_TOTAL: Final = 512 * 1024 * 1024
_FETCHES_AT_ONCE: Final = 2
# A clip larger than this is streamed through as it comes (Chrome still plays
# it). Frigate sends no length, so that is learned only by reaching it: the
# event is remembered, and its later requests stream at once.
_CLIP_MAX_BYTES: Final = 128 * 1024 * 1024
_TOO_LARGE_REMEMBERED: Final = 64
_CHUNK: Final = 1024 * 1024


async def async_link_base(hass: HomeAssistant) -> str | None:
    """Where the user's browser can open a link: the hub's remote address,
    else Home Assistant's own (its external URL when one is set, else the
    local one).

    Never the address the MCP client came in at: on a hub that is the MCP
    resource, which takes only Connect's token, so a browser is refused."""
    path = hass.config.path(REMOTE_URL_DIR, REMOTE_URL_FILE)
    if remote := await hass.async_add_executor_job(_read_remote_url, path):
        return remote
    try:
        return get_url(hass, prefer_external=True).rstrip("/")
    except NoURLAvailableError:
        return None


def _read_remote_url(path: str) -> str | None:
    """The base of the remote address Selora OS delivers, or None without a
    usable one. Read on every call: the file is tiny and links are rare, and
    the OS removes it when remote access is off. Blocking: use an executor."""
    try:
        with open(path, encoding="utf-8") as file:
            remote = json.load(file).get("remote_url")
    except FileNotFoundError:
        return None
    except (OSError, ValueError, AttributeError) as exc:
        # Never the file's content: only why it was not used.
        _LOGGER.debug("Remote URL file unusable (%s); using the local address", type(exc).__name__)
        return None
    if not isinstance(remote, str) or (base := _base(remote.strip())) is None:
        _LOGGER.debug("Remote URL file holds no valid http(s) address; using the local address")
        return None
    return base


def _base(url: str) -> str | None:
    """``scheme://host[:port]`` of an http(s) URL, or None for anything else."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    return f"{parts.scheme}://{parts.hostname}" + (f":{port}" if port else "")


def event_links(hass: HomeAssistant, base: str, event_id: str, has_clip: bool) -> dict[str, str]:
    """Signed links to an event's snapshot and, when Frigate kept one, its clip."""
    kinds = [_SNAPSHOT, _CLIP] if has_clip else [_SNAPSHOT]
    return {
        ("clip_url" if kind == _CLIP else "snapshot_url"): base
        + async_sign_path(
            hass,
            MEDIA_URL.format(event_id=event_id, kind=kind),
            LINK_LIFETIME,
            # Signed for Home Assistant's content user, not the MCP caller's
            # token: a Selora Connect login has none to sign with.
            use_content_user=True,
        )
        for kind in kinds
    }


class FrigateEventMediaView(HomeAssistantView):
    """Stream one event's snapshot or clip from Frigate."""

    name = "selora_ai:frigate_event_media"
    url = MEDIA_URL
    requires_auth = True

    def __init__(self) -> None:
        self._clips = _ClipCache()

    async def get(self, request: web.Request, event_id: str, kind: str) -> web.StreamResponse:
        """Answer a signed link, or an admin's own request."""
        if not _allowed(request):
            return web.Response(status=HTTPStatus.FORBIDDEN)
        if kind not in _FRIGATE_PATHS or not _EVENT_ID.match(event_id):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        hass: HomeAssistant = request.app[KEY_HASS]
        client = FrigateClient.from_hass(hass)
        if client is None:
            return web.Response(status=HTTPStatus.NOT_FOUND, text="Frigate is not set up.")
        if kind == _CLIP:
            try:
                file = await self._clips.file(hass, client, event_id)
            except _ClipTooLarge:
                return await self._stream(request, client, event_id, kind)
            except (FrigateError, OSError) as exc:
                _LOGGER.debug("Frigate clip for %s failed: %s", event_id, exc)
                return web.Response(status=HTTPStatus.BAD_GATEWAY)
            if file is None:
                return web.Response(status=HTTPStatus.NOT_FOUND)
            return web.FileResponse(
                file,
                headers={
                    hdrs.CONTENT_TYPE: "video/mp4",
                    hdrs.CACHE_CONTROL: "private, no-store",
                },
            )
        return await self._stream(request, client, event_id, kind)

    async def _stream(
        self, request: web.Request, client: FrigateClient, event_id: str, kind: str
    ) -> web.StreamResponse:
        """Pass Frigate's response through as it arrives."""
        forwarded = (
            {hdrs.RANGE: request.headers[hdrs.RANGE]} if hdrs.RANGE in request.headers else {}
        )
        try:
            for path in _FRIGATE_PATHS[kind]:
                async with client.media(path.format(id=event_id), forwarded) as upstream:
                    if upstream.status == HTTPStatus.NOT_FOUND:
                        continue
                    if upstream.status >= HTTPStatus.BAD_REQUEST:
                        return web.Response(status=HTTPStatus.BAD_GATEWAY)
                    response = web.StreamResponse(status=upstream.status)
                    for header in _PASSED_HEADERS:
                        if header in upstream.headers:
                            response.headers[header] = upstream.headers[header]
                    response.headers[hdrs.CACHE_CONTROL] = "private, no-store"
                    await response.prepare(request)
                    async for chunk in upstream.content.iter_chunked(64 * 1024):
                        await response.write(chunk)
                    await response.write_eof()
                    return response
        except FrigateError as exc:
            _LOGGER.debug("Frigate media for %s failed: %s", event_id, exc)
            return web.Response(status=HTTPStatus.BAD_GATEWAY)
        return web.Response(status=HTTPStatus.NOT_FOUND)


class _ClipTooLarge(Exception):
    """The clip outgrew ``_CLIP_MAX_BYTES``: stream it through instead."""


class _ClipCache:
    """Event clips fetched whole from Frigate, so a browser can ask for ranges."""

    def __init__(self) -> None:
        # One fetch per clip: a browser opens several ranges of a video at once.
        self._fetching: dict[str, asyncio.Task[Path | None]] = {}
        self._fetch_slots = asyncio.Semaphore(_FETCHES_AT_ONCE)
        # Insertion-ordered, so the oldest is forgotten first.
        self._too_large: dict[str, None] = {}

    async def file(self, hass: HomeAssistant, client: FrigateClient, event_id: str) -> Path | None:
        """The clip's file, fetched unless a recent one is on disk; None when
        Frigate has no clip for the event. Raises _ClipTooLarge for one past
        _CLIP_MAX_BYTES."""
        if event_id in self._too_large:
            raise _ClipTooLarge
        if (fetching := self._fetching.get(event_id)) is None:
            # Registered before any await, and in the background, so requests
            # arriving together share it and one giving up cancels nothing.
            fetching = hass.async_create_background_task(
                self._fetch(hass, client, event_id), f"selora_ai frigate clip {event_id}"
            )
            self._fetching[event_id] = fetching
            fetching.add_done_callback(lambda _: self._fetching.pop(event_id, None))
        return await asyncio.shield(fetching)

    async def _fetch(
        self, hass: HomeAssistant, client: FrigateClient, event_id: str
    ) -> Path | None:
        """The clip's recent file, else Frigate's clip written to it."""
        file = await hass.async_add_executor_job(_clip_file, event_id)
        if await hass.async_add_executor_job(_recent, file):
            return file
        async with self._fetch_slots:
            try:
                return await _download(hass, client, event_id, file)
            except _ClipTooLarge:
                self._too_large[event_id] = None
                if len(self._too_large) > _TOO_LARGE_REMEMBERED:
                    del self._too_large[next(iter(self._too_large))]
                raise


async def _download(
    hass: HomeAssistant, client: FrigateClient, event_id: str, file: Path
) -> Path | None:
    """Write Frigate's clip to ``file``; None when Frigate has none."""
    path = _FRIGATE_PATHS[_CLIP][0].format(id=event_id)
    async with client.media(path) as upstream:
        if upstream.status == HTTPStatus.NOT_FOUND:
            return None
        if upstream.status >= HTTPStatus.BAD_REQUEST:
            raise FrigateError(f"Frigate answered {upstream.status}")
        partial = file.with_suffix(".part")
        handle = await hass.async_add_executor_job(partial.open, "wb")
        kept = False
        try:
            size = 0
            async for chunk in upstream.content.iter_chunked(_CHUNK):
                size += len(chunk)
                if size > _CLIP_MAX_BYTES:
                    raise _ClipTooLarge
                await hass.async_add_executor_job(handle.write, chunk)
            await hass.async_add_executor_job(_keep, handle, partial, file)
            kept = True
        finally:
            if not kept:
                await hass.async_add_executor_job(_discard, handle, partial)
    return file


def _clip_file(event_id: str) -> Path:
    """Where the event's clip is kept. Blocking: use an executor."""
    directory = Path(tempfile.gettempdir()) / _CLIP_DIR
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory / f"{event_id}.mp4"


def _recent(file: Path) -> bool:
    """Whether ``file`` was fetched recently enough to reuse. Blocking."""
    try:
        return time.time() - file.stat().st_mtime < _CLIP_REUSE_SECONDS
    except FileNotFoundError:
        return False


def _discard(handle: BinaryIO, partial: Path) -> None:
    """Drop a fetch that did not finish. Blocking."""
    handle.close()
    partial.unlink(missing_ok=True)


def _keep(handle: BinaryIO, partial: Path, file: Path) -> None:
    """Put a finished fetch in place, and drop clips past their reuse or
    beyond the latest few. Blocking."""
    handle.close()
    os.replace(partial, file)
    now = time.time()
    ages: list[tuple[float, int, Path]] = []
    for entry in file.parent.iterdir():
        try:
            stat = entry.stat()
        except FileNotFoundError:
            continue
        ages.append((now - stat.st_mtime, stat.st_size, entry))
    ages.sort(key=lambda age: age[0])
    # A ``.part`` is another clip still arriving: dropped only once abandoned.
    # The clip just fetched is kept whatever the others take.
    kept = 0
    total = file.stat().st_size
    for age, size, entry in ages:
        if entry == file:
            continue
        if entry.suffix == ".mp4":
            if (
                age < _CLIP_REUSE_SECONDS
                and kept + 1 < _CLIPS_KEPT
                and total + size <= _CLIPS_MAX_TOTAL
            ):
                kept += 1
                total += size
                continue
        elif age < _CLIP_REUSE_SECONDS:
            continue
        entry.unlink(missing_ok=True)


def _allowed(request: web.Request) -> bool:
    """A signed link, or an admin. Home Assistant has already checked the
    signature: a request with no Authorization header can only be authenticated
    by it. A non-admin's own token is refused, as the MCP tools refuse it."""
    user = request.get(KEY_HASS_USER)
    if user is not None and user.is_admin:
        return True
    return hdrs.AUTHORIZATION not in request.headers and SIGN_QUERY_PARAM in request.query


def async_register_view(hass: HomeAssistant) -> None:
    """Register the view, once at setup."""
    hass.http.register_view(FrigateEventMediaView())
