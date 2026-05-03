"""Selora Cloud LLM provider.

Hosted, OAuth-authenticated LLM backend served by Connect's AI Gateway
proxy at ``/api/v1/ai-gateway/v1/chat/completions``. The wire format is
OpenAI-compatible — see ``../../../connect/docs/api/ha-integration-openapi.yaml``.

Tokens come from Connect's AI Gateway OAuth flow (PKCE) — the access token
is a short-lived RS256 JWT carried as a Bearer credential. When the JWT
nears expiry the provider refreshes it in-line via Connect's token
endpoint and persists the new pair back to the config entry.

The gateway picks the model from server-side admin config and silently
overwrites any ``model`` the client sends, so we omit the field
entirely from chat-completion payloads.

The target installation is bound into the JWT at OAuth issuance time
(Connect picks it from the signed-in user — auto when there's a single
hub, picker when 2+, free plan when none). The proxy reads
``installation_id`` from the verified JWT, so clients don't send an
installation header or query parameter.
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
    AIGATEWAY_CHAT_COMPLETIONS_PATH,
    AIGATEWAY_REFRESH_LEEWAY_SECONDS,
    AIGATEWAY_TOKEN_PATH,
    CONF_AIGATEWAY_ACCESS_TOKEN,
    CONF_AIGATEWAY_EXPIRES_AT,
    CONF_AIGATEWAY_REFRESH_TOKEN,
    DEFAULT_SELORA_CONNECT_URL,
)
from .openai_compat import OpenAICompatibleProvider

_LOGGER = logging.getLogger(__name__)


class SeloraCloudProvider(OpenAICompatibleProvider):
    """Selora-hosted LLM provider authenticated via the AI Gateway OAuth flow."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        access_token: str = "",
        refresh_token: str = "",
        expires_at: float = 0.0,
        connect_url: str = "",
        client_id: str = "",
        entry_id: str = "",
        **_kwargs: Any,
    ) -> None:
        # No api_key on this provider — the bearer JWT is the credential.
        # Model is left blank because the gateway always overwrites it; we
        # also strip it from the payload (build_payload below).
        super().__init__(
            hass,
            api_key="",
            model="",
            host=(connect_url or DEFAULT_SELORA_CONNECT_URL).rstrip("/"),
        )
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
        return "Selora Cloud"

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def has_api_key(self) -> bool:
        # "Configured" for this provider means we have a refresh token.
        return bool(self._refresh_token)

    # -- HTTP plumbing -----------------------------------------------------

    def _get_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    @property
    def _endpoint(self) -> str:
        return f"{self._host}{AIGATEWAY_CHAT_COMPLETIONS_PATH}"

    # -- Payload -----------------------------------------------------------

    def build_payload(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        # Connect's gateway picks the model server-side and overwrites any
        # client-supplied value; omit the field rather than send a misleading
        # one. Everything else stays OpenAI-compatible.
        payload = super().build_payload(
            system, messages, tools=tools, stream=stream, max_tokens=max_tokens
        )
        payload.pop("model", None)
        return payload

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
                    f"{self._host}{AIGATEWAY_TOKEN_PATH}",
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
        if not self._needs_refresh():
            return
        ok = await self._refresh_access_token()
        if ok:
            return
        # Refresh failed (network blip, refresh token revoked, Connect
        # upgrade in progress). Sending the request anyway with an
        # expired access token would return 401 — same outcome as
        # raising here, but the error message is mystifying. Raise
        # ConnectionError now so the chat handler's existing arm can
        # tell the user to retry / relink in plain English instead.
        raise ConnectionError(
            "Selora Cloud session expired and could not refresh — try again or relink in Settings."
        )

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
