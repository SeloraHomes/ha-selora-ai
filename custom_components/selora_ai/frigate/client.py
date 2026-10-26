"""Frigate's HTTP API, at the URL and login of the Frigate integration's entry."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlencode

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

FRIGATE_DOMAIN: Final = "frigate"
_TIMEOUT: Final = aiohttp.ClientTimeout(total=15)
_TOKEN_COOKIE: Final = "frigate_token"


class FrigateError(Exception):
    """Frigate could not be reached or refused a request; the message is for the user."""


def frigate_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """The Frigate integration's loaded entry, if any."""
    for entry in hass.config_entries.async_entries(FRIGATE_DOMAIN):
        if entry.state is ConfigEntryState.LOADED and entry.data.get("url"):
            return entry
    return None


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
        """A client for the Frigate integration's server, or None without one."""
        entry = frigate_entry(hass)
        if entry is None:
            return None
        return cls(
            hass,
            str(entry.data["url"]),
            entry.data.get("username") or None,
            entry.data.get("password") or None,
        )

    async def _login(self) -> None:
        try:
            async with self._session.post(
                f"{self._url}/api/login",
                json={"user": self._username, "password": self._password},
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status >= 400:
                    raise FrigateError("Frigate refused the login the Frigate integration uses.")
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

    async def version(self) -> str:
        """Frigate's version; raises while it is down."""
        return str(await self._request("GET", "/api/version") or "")
