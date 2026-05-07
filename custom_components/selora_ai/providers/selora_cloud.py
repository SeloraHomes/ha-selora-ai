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
import re
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

# An intermediary in front of the AI Gateway has been observed returning
# transient 5xx (a 500 with "unable to reach app" and generic 502/503/504)
# at HA boot — likely the dev framework's runtime proxy, not the AI
# Gateway itself. We retry on these delays so the next attempt lands on
# a healthy upstream. Keep the total budget short so a genuinely-down
# upstream still surfaces quickly.
_UPSTREAM_RETRY_DELAYS: tuple[float, ...] = (2.0, 4.0, 6.0)


_TOKEN_FIELD_RE = re.compile(r'("(?:access_token|refresh_token|id_token|token)"\s*:\s*")[^"]+(")')
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b")


def _mask_tokens(text: str) -> str:
    """Redact bearer/refresh tokens from text destined for logs.

    Covers: ``"access_token":"…"``, ``"refresh_token":"…"`` JSON values
    (as Connect's OAuth proxy returns them), bare JWTs (3 dot-separated
    base64url segments), and the ``Bearer …`` HTTP header form.
    """
    text = _TOKEN_FIELD_RE.sub(r"\1***\2", text)
    text = _BEARER_RE.sub(r"\1***", text)
    text = _JWT_RE.sub("***", text)
    return text


def _is_transient_upstream_error(err: str | None) -> bool:
    """Return True for Connect proxy 5xx that indicates upstream cold-start.

    Match conservatively: only the proxy-specific 500 message and the
    generic 502/503/504 status codes, so a real 500 from the AI Gateway
    app itself (a bug we should see in logs) isn't silently retried.

    Also catches the streaming-path error format, which raises without
    the "HTTP <code>:" prefix — we match the proxy's distinctive
    cold-start hint directly so the same retry covers both paths.
    """
    if not err:
        return False
    if "HTTP 502" in err or "HTTP 503" in err or "HTTP 504" in err:
        return True
    if "HTTP 500" in err and "unable to reach app" in err:
        return True
    return "unable to reach app" in err or "app_start_timeout" in err


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

    @property
    def is_configured(self) -> bool:
        # OAuth: the refresh token is the credential. Without it, every
        # request would 401 — surface unlinked state to skip analysis
        # cycles cleanly instead of letting the request fail downstream.
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
                        # Mask BEFORE truncating: slicing first could cut a
                        # token's closing quote, which would prevent
                        # _TOKEN_FIELD_RE from matching and leak the prefix.
                        body = _mask_tokens(await resp.text())[:200]
                        _LOGGER.warning(
                            "AI Gateway token refresh failed (%s): %s",
                            resp.status,
                            body,
                        )
                        return False
                    data = await resp.json()
            except (aiohttp.ClientError, TimeoutError) as exc:
                _LOGGER.warning("AI Gateway token refresh error: %s", _mask_tokens(str(exc)))
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
        log_errors: bool = True,
        timeout: float | None = None,
    ) -> tuple[str | None, str | None]:
        await self._ensure_token()
        # An intermediary in front of the AI Gateway has been seen
        # returning transient 5xx at HA boot — observed in the dev stack
        # when the integration fires a health-check / first collector
        # cycle. Keep base.send_request silent throughout so we can pick
        # the right log level locally: success → no log, non-transient
        # failure → ERROR (real outage), transient retries exhausted →
        # WARNING (self-healing condition that doesn't deserve HA's
        # "error reported by integration" notification on every restart).
        for delay in _UPSTREAM_RETRY_DELAYS:
            result, err = await super().send_request(
                system, messages, max_tokens=max_tokens, log_errors=False, timeout=timeout
            )
            if result is not None:
                return result, None
            if not _is_transient_upstream_error(err):
                if log_errors:
                    _LOGGER.error("Selora Cloud request failed: %s", err)
                return None, err
            _LOGGER.info(
                "Selora Cloud transient upstream error (%s) — retrying after %.1fs",
                err,
                delay,
            )
            await asyncio.sleep(delay)
            await self._ensure_token()
        # Final attempt after the retry budget.
        result, err = await super().send_request(
            system, messages, max_tokens=max_tokens, log_errors=False
        )
        if result is not None:
            return result, None
        if log_errors:
            if _is_transient_upstream_error(err):
                _LOGGER.warning(
                    "Selora Cloud upstream still unreachable after %d attempts: %s",
                    len(_UPSTREAM_RETRY_DELAYS) + 1,
                    err,
                )
            else:
                _LOGGER.error("Selora Cloud request failed: %s", err)
        return None, err

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
        # The parent generator raises ConnectionError before yielding
        # anything when the initial POST returns non-200 — so it is safe
        # to retry the whole generator on a transient cold-start error.
        # A successful first attempt yields once and we return immediately.
        await self._ensure_token()
        for delay in _UPSTREAM_RETRY_DELAYS:
            try:
                async for item in super().raw_request_stream(*args, **kwargs):
                    yield item
                return
            except ConnectionError as exc:
                if not _is_transient_upstream_error(str(exc)):
                    raise
                _LOGGER.info(
                    "Selora Cloud transient upstream error on stream (%s) — retrying after %.1fs",
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                await self._ensure_token()
        # Final attempt after the retry budget — let any error propagate.
        async for item in super().raw_request_stream(*args, **kwargs):
            yield item

    async def send_request_stream(  # type: ignore[override]
        self,
        *args: Any,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        # See raw_request_stream above for why retrying the parent
        # generator is safe: the cold-start failure happens at the
        # initial status check, before any chunk is yielded.
        await self._ensure_token()
        for delay in _UPSTREAM_RETRY_DELAYS:
            try:
                async for chunk in super().send_request_stream(*args, **kwargs):
                    yield chunk
                return
            except ConnectionError as exc:
                if not _is_transient_upstream_error(str(exc)):
                    raise
                _LOGGER.info(
                    "Selora Cloud transient upstream error on stream (%s) — retrying after %.1fs",
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                await self._ensure_token()
        # Final attempt after the retry budget.
        async for chunk in super().send_request_stream(*args, **kwargs):
            yield chunk

    # -- Health check ------------------------------------------------------

    async def health_check(self) -> bool:
        if not self._refresh_token and not self._access_token:
            return False
        # Use ``self.send_request`` rather than ``super()`` so the cold-start
        # retry logic above also covers boot-time health checks — those are
        # the most likely callers to race the proxy's upstream warmup.
        result, _err = await self.send_request(
            system="Respond with 'ok'",
            messages=[{"role": "user", "content": "Hi"}],
        )
        return result is not None
