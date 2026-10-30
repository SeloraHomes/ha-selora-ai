"""Frigate's HTTP API, at the URL and login of the Frigate integration's entry,
or of Selora AI's own Frigate settings when no such entry is loaded."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
from typing import TYPE_CHECKING, Any, Final, Literal, NamedTuple
from urllib.parse import urlencode, urlsplit

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import CONF_FRIGATE_PASSWORD, CONF_FRIGATE_URL, CONF_FRIGATE_USERNAME, DOMAIN
from ..helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

FRIGATE_DOMAIN: Final = "frigate"
_TIMEOUT: Final = aiohttp.ClientTimeout(total=15)
# A clip streams for as long as it plays: no total, only a stall limit.
_STREAM_TIMEOUT: Final = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=60)
_TOKEN_COOKIE: Final = "frigate_token"
# The hub host's address on the Home Assistant VM's internal network, which
# only ever means "this hub". Selora OS's ``seloraSlirpHost`` (modules/lib.nix):
# if it changes there, it changes here.
SELORA_HUB_HOST: Final = "10.42.42.2"
# Snapshots are a few hundred KB; a response past this is not one.
_MAX_IMAGE_BYTES: Final = 8 * 1024 * 1024

NOT_SET_UP: Final = (
    "Frigate is not set up: install the Frigate integration, or enter Frigate's "
    "address in Selora AI's settings."
)


class FrigateError(Exception):
    """Frigate could not be reached or refused a request; the message is for the user."""


def frigate_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """The Frigate integration's loaded entry, if any."""
    for entry in hass.config_entries.async_entries(FRIGATE_DOMAIN):
        if entry.state is ConfigEntryState.LOADED and entry.data.get("url"):
            return entry
    return None


class FrigateConnection(NamedTuple):
    """Where Frigate is and how to log in; ``source`` says who configured it."""

    url: str
    username: str | None
    password: str | None
    source: Literal["integration", "settings"]


def frigate_connection(hass: HomeAssistant) -> FrigateConnection | None:
    """The Frigate server Selora AI talks to, or None without one.

    The Frigate integration's entry comes first: on a Selora hub it points at
    the host's proxy, and its camera entities are what the rest of Selora reads.
    Selora AI's own settings serve a home whose Frigate has no such entry.
    """
    if (entry := frigate_entry(hass)) is not None:
        return FrigateConnection(
            str(entry.data["url"]),
            entry.data.get("username") or None,
            entry.data.get("password") or None,
            "integration",
        )
    for selora in hass.config_entries.async_entries(DOMAIN):
        if url := str(selora.data.get(CONF_FRIGATE_URL) or "").strip():
            return FrigateConnection(
                url,
                selora.data.get(CONF_FRIGATE_USERNAME) or None,
                selora.data.get(CONF_FRIGATE_PASSWORD) or None,
                "settings",
            )
    return None


def managed_by_hub(entry: ConfigEntry) -> bool:
    """Whether this entry points at the Frigate Selora OS runs on the hub.

    Selora OS adds it through the integration's own config flow (source
    "user"), always at the VM's address for the host. Matched on the host
    alone, so a port change keeps it.
    """
    return urlsplit(str(entry.data.get("url") or "")).hostname == SELORA_HUB_HOST


def normalize_frigate_url(url: str) -> str | None:
    """``url`` as Frigate's base address (``http(s)://host[:port][/path]``),
    or None when it is not one. A login belongs in its own fields."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if parts.query or parts.fragment or parts.username or parts.password:
        return None
    try:
        parts.port  # noqa: B018 - raises on a port that is not one
    except ValueError:
        return None
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


def frigate_available(hass: HomeAssistant) -> bool:
    """Whether Frigate is set up, so its tools are offered at all."""
    return frigate_connection(hass) is not None


class FrigateClient:
    """One Frigate server. Logs in first when the entry holds a login."""

    def __init__(
        self,
        hass: HomeAssistant,
        url: str,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._session = async_get_clientsession(hass)
        self._url = url.rstrip("/")
        self._username = username
        self._password = password
        self._token: str | None = None

    @classmethod
    def from_hass(cls, hass: HomeAssistant) -> FrigateClient | None:
        """A client for the server ``frigate_connection`` names, or None without one."""
        connection = frigate_connection(hass)
        if connection is None:
            return None
        return cls(hass, connection.url, connection.username, connection.password)

    async def _login(self) -> None:
        try:
            async with self._session.post(
                f"{self._url}/api/login",
                json={"user": self._username, "password": self._password},
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status >= 400:
                    raise FrigateError("Frigate refused the login Selora AI uses for it.")
                cookie = resp.cookies.get(_TOKEN_COOKIE)
                self._token = cookie.value if cookie is not None else None
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise FrigateError(f"Frigate could not be reached: {exc}") from exc

    async def _request(self, method: str, path: str, payload: Any = None) -> Any:
        if self._username and self._token is None:
            await self._login()
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        try:
            async with self._session.request(
                method, f"{self._url}{path}", json=payload, headers=headers, timeout=_TIMEOUT
            ) as resp:
                text = await resp.text()
                # /api/version answers in plain text, everything else in JSON.
                try:
                    body: Any = json.loads(text) if text else None
                except ValueError:
                    body = text
                if resp.status >= 400:
                    detail = body.get("message") if isinstance(body, dict) else None
                    if resp.status >= 500 and not detail:
                        # Frigate's own front end answering for a Frigate that
                        # is not running: nothing was refused.
                        raise FrigateError(
                            "Frigate is not responding: it is stopped or still restarting."
                        )
                    raise FrigateError(
                        ("Frigate failed" if resp.status >= 500 else "Frigate refused it")
                        + (f": {sanitize_untrusted_text(str(detail), 300)}" if detail else ".")
                    )
                return body
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise FrigateError(f"Frigate could not be reached: {exc}") from exc

    async def get_config(self) -> dict[str, Any]:
        """Frigate's running configuration, with credentials redacted by Frigate."""
        config = await self._request("GET", "/api/config")
        if not isinstance(config, dict):
            raise FrigateError("Frigate returned no configuration.")
        return config

    async def get_raw_config(self) -> str:
        """Frigate's saved configuration file as text, logins included.

        For a write that has to keep them; never log or return it.
        """
        raw = await self._request("GET", "/api/config/raw")
        if not isinstance(raw, str) or not raw.strip():
            raise FrigateError("Frigate returned no saved configuration.")
        return raw

    async def set_config(self, config_data: dict[str, Any]) -> None:
        """Merge ``config_data`` into Frigate's saved configuration.

        Frigate validates the result and refuses an invalid one; the change
        applies on its next restart.
        """
        await self._request(
            "PUT", "/api/config/set", {"config_data": config_data, "requires_restart": 1}
        )

    async def restart(self) -> None:
        """Restart Frigate, which takes up to a minute."""
        await self._request("POST", "/api/restart")

    async def get_events(self, params: dict[str, Any], *, search: bool = False) -> list[Any]:
        """Tracked-object events, newest first; ``search`` ranks them against
        ``params["query"]`` with Frigate's semantic search instead."""
        path = "/api/events/search" if search else "/api/events"
        events = await self._request("GET", f"{path}?{urlencode(params)}")
        if not isinstance(events, list):
            raise FrigateError("Frigate returned no events.")
        return events

    async def get_stats(self) -> dict[str, Any]:
        """Frigate's live statistics: per-camera and detector rates, storage."""
        stats = await self._request("GET", "/api/stats")
        if not isinstance(stats, dict):
            raise FrigateError("Frigate returned no statistics.")
        return stats

    async def get_image(self, path: str) -> tuple[bytes, str]:
        """An image Frigate serves at ``path`` (a camera's latest frame, an
        event's snapshot), with its content type."""
        if self._username and self._token is None:
            await self._login()
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        try:
            async with self._session.get(
                f"{self._url}{path}", headers=headers, timeout=_TIMEOUT
            ) as resp:
                if resp.status == 404:
                    raise FrigateError("Frigate has no such image.")
                if resp.status >= 400:
                    raise FrigateError(f"Frigate refused the image ({resp.status}).")
                if (resp.content_length or 0) > _MAX_IMAGE_BYTES:
                    raise FrigateError("Frigate's image is too large to send.")
                # read(n) returns what has arrived so far, not the whole body:
                # read to the end, stopping once past the cap.
                data = bytearray()
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    data += chunk
                    if len(data) > _MAX_IMAGE_BYTES:
                        raise FrigateError("Frigate's image is too large to send.")
                return bytes(data), resp.content_type or "application/octet-stream"
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise FrigateError(f"Frigate could not be reached: {exc}") from exc

    @asynccontextmanager
    async def media(
        self, path: str, headers: dict[str, str] | None = None
    ) -> AsyncIterator[aiohttp.ClientResponse]:
        """Frigate's open response for ``path``, to be streamed (a clip runs to
        hundreds of MB, so nothing caps or buffers it). ``headers`` adds a
        browser's ``Range``, so a video can seek."""
        if self._username and self._token is None:
            await self._login()
        sent = dict(headers or {})
        if self._token:
            sent["Authorization"] = f"Bearer {self._token}"
        try:
            async with self._session.get(
                f"{self._url}{path}", headers=sent, timeout=_STREAM_TIMEOUT
            ) as resp:
                yield resp
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise FrigateError(f"Frigate could not be reached: {exc}") from exc

    async def version(self) -> str:
        """Frigate's version; raises while it is down."""
        return str(await self._request("GET", "/api/version") or "")
