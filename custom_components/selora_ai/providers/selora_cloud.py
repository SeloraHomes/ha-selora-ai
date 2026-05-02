"""Selora Cloud LLM provider.

Hosted, OAuth-authenticated LLM backend. The wire format is Anthropic's
``/v1/messages`` API, so this provider extends ``AnthropicProvider`` and
only overrides authentication and endpoint resolution.

Tokens come from Connect's AI Gateway OAuth flow (PKCE) — the access token
is a short-lived RS256 JWT carried as a Bearer credential. When the JWT
nears expiry the provider refreshes it in-line via Connect's token
endpoint and persists the new pair back to the config entry.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import logging
import time
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import (
    AIGATEWAY_REFRESH_LEEWAY_SECONDS,
    AIGATEWAY_TOKEN_PATH,
    ANTHROPIC_MESSAGES_ENDPOINT,
    CONF_AIGATEWAY_ACCESS_TOKEN,
    CONF_AIGATEWAY_EXPIRES_AT,
    CONF_AIGATEWAY_REFRESH_TOKEN,
    DEFAULT_AIGATEWAY_API_URL,
    DEFAULT_SELORA_CLOUD_MODEL,
    DEFAULT_SELORA_CONNECT_URL,
)
from .anthropic import AnthropicProvider

_LOGGER = logging.getLogger(__name__)


class SeloraCloudProvider(AnthropicProvider):
    """Selora-hosted LLM provider authenticated via the AI Gateway OAuth flow."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        access_token: str = "",
        refresh_token: str = "",
        expires_at: float = 0.0,
        api_url: str = "",
        connect_url: str = "",
        client_id: str = "",
        entry_id: str = "",
        model: str = "",
        **_kwargs: Any,
    ) -> None:
        # We never use api_key on this provider — bearer token is the credential.
        super().__init__(
            hass,
            api_key="",
            model=model or DEFAULT_SELORA_CLOUD_MODEL,
        )
        # Override the host inherited from AnthropicProvider with the Gateway URL.
        self._host = (api_url or DEFAULT_AIGATEWAY_API_URL).rstrip("/")
        self._connect_url = (connect_url or DEFAULT_SELORA_CONNECT_URL).rstrip("/")
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._expires_at = float(expires_at or 0.0)
        self._client_id = client_id
        self._entry_id = entry_id
        self._refresh_lock = asyncio.Lock()

    # -- Identity ----------------------------------------------------------

    @property
    def provider_type(self) -> str:
        return "selora_cloud"

    @property
    def provider_name(self) -> str:
        return f"Selora Cloud ({self._model})"

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def has_api_key(self) -> bool:
        # "Configured" for this provider means we have a refresh token.
        return bool(self._refresh_token)

    # -- HTTP plumbing -----------------------------------------------------

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    @property
    def _endpoint(self) -> str:
        return f"{self._host}{ANTHROPIC_MESSAGES_ENDPOINT}"

    # -- Token refresh -----------------------------------------------------

    def _needs_refresh(self) -> bool:
        if not self._refresh_token:
            return False
        if not self._access_token:
            return True
        if not self._expires_at:
            return False
        return time.time() + AIGATEWAY_REFRESH_LEEWAY_SECONDS >= self._expires_at

    async def _refresh_access_token(self) -> bool:
        """Refresh the access token using the stored refresh token.

        Returns True on success. Persists the new tokens to the config
        entry. Single-flight via ``self._refresh_lock``.
        """
        async with self._refresh_lock:
            # Re-check under the lock — another caller may have refreshed.
            if not self._needs_refresh():
                return True
            if not self._refresh_token:
                return False

            session = async_get_clientsession(self._hass)
            try:
                async with session.post(
                    f"{self._connect_url}{AIGATEWAY_TOKEN_PATH}",
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": self._refresh_token,
                        "client_id": self._client_id,
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        body = (await resp.text())[:200]
                        _LOGGER.warning(
                            "AI Gateway token refresh failed (%s): %s",
                            resp.status,
                            body,
                        )
                        return False
                    data = await resp.json()
            except (aiohttp.ClientError, TimeoutError) as exc:
                _LOGGER.warning("AI Gateway token refresh error: %s", exc)
                return False

            access = data.get("access_token")
            expires_in = int(data.get("expires_in") or 0)
            new_refresh = data.get("refresh_token")  # only present on rotation
            if not access:
                _LOGGER.warning("AI Gateway refresh response missing access_token")
                return False

            self._access_token = access
            self._expires_at = time.time() + expires_in if expires_in > 0 else 0.0
            if new_refresh:
                self._refresh_token = new_refresh

            self._persist_tokens()
            return True

    def _persist_tokens(self) -> None:
        """Write the current tokens back to the config entry."""
        if not self._entry_id:
            return
        entry: ConfigEntry | None = self._hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return
        new_data = {
            **entry.data,
            CONF_AIGATEWAY_ACCESS_TOKEN: self._access_token,
            CONF_AIGATEWAY_REFRESH_TOKEN: self._refresh_token,
            CONF_AIGATEWAY_EXPIRES_AT: self._expires_at,
        }
        self._hass.config_entries.async_update_entry(entry, data=new_data)

    # -- Request hooks -----------------------------------------------------

    async def _ensure_token(self) -> None:
        if self._needs_refresh():
            await self._refresh_access_token()

    async def send_request(
        self,
        system: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 1024,
    ) -> tuple[str | None, str | None]:
        await self._ensure_token()
        return await super().send_request(system, messages, max_tokens=max_tokens)

    async def raw_request(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        await self._ensure_token()
        return await super().raw_request(system, messages, tools=tools)

    async def raw_request_stream(  # type: ignore[override]
        self,
        *args: Any,
        **kwargs: Any,
    ) -> AsyncIterator[aiohttp.ClientResponse]:
        await self._ensure_token()
        async for item in super().raw_request_stream(*args, **kwargs):
            yield item

    async def send_request_stream(  # type: ignore[override]
        self,
        *args: Any,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        await self._ensure_token()
        async for chunk in super().send_request_stream(*args, **kwargs):
            yield chunk

    # -- Health check ------------------------------------------------------

    async def health_check(self) -> bool:
        if not self._refresh_token and not self._access_token:
            return False
        await self._ensure_token()
        if not self._access_token:
            return False
        result, _err = await super().send_request(
            system="Respond with 'ok'",
            messages=[{"role": "user", "content": "Hi"}],
        )
        return result is not None
