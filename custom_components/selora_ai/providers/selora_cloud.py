"""Selora Cloud LLM provider.

Hosted, OAuth-authenticated LLM backend served by Connect's AI Gateway
proxy at ``/api/v1/ai-gateway/v1/chat/completions``. The wire format is
OpenAI-compatible — see ``../../../connect/docs/api/ha-integration-openapi.yaml``.

Tokens come from Connect's AI Gateway OAuth flow (PKCE) — the access token
is a short-lived RS256 JWT carried as a Bearer credential. When the JWT
nears expiry the provider refreshes it in-line via Connect's token
endpoint and persists the new pair back to the config entry.

Expiry is read from the JWT's own ``exp`` claim (at construction and after
each refresh), not from the token endpoint's ``expires_in`` — the latter
has been seen omitted, which left the expiry unknown (0.0), defeated the
proactive refresh, and never wrote a durable expiry to ``.storage``. As a
backstop, a request rejected with 401 despite a "valid" token (server-side
revocation, clock skew) forces a token refresh and retries once, so a stale
bearer self-heals instead of failing forever.

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
import base64
import binascii
from collections.abc import AsyncIterator
from enum import Enum
import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import (
    AIGATEWAY_CAPABILITIES_PATH,
    AIGATEWAY_CAPABILITIES_TTL_S,
    AIGATEWAY_CHAT_COMPLETIONS_PATH,
    AIGATEWAY_REFRESH_LEEWAY_SECONDS,
    AIGATEWAY_TOKEN_PATH,
    CLOUD_LLM_TEMPERATURE,
    CONF_AIGATEWAY_ACCESS_TOKEN,
    CONF_AIGATEWAY_EXPIRES_AT,
    CONF_AIGATEWAY_REFRESH_TOKEN,
    DEFAULT_SELORA_CONNECT_URL,
)
from .openai_compat import OpenAICompatibleProvider

if TYPE_CHECKING:
    from ..types import LLMUsageInfo, OpenAIChatPayload

_LOGGER = logging.getLogger(__name__)

# An intermediary in front of the AI Gateway has been observed returning
# transient 5xx (a 500 with "unable to reach app" and generic 502/503/504)
# at HA boot — likely the dev framework's runtime proxy, not the AI
# Gateway itself. We retry on these delays so the next attempt lands on
# a healthy upstream. Keep the total budget short so a genuinely-down
# upstream still surfaces quickly.
_UPSTREAM_RETRY_DELAYS: tuple[float, ...] = (2.0, 4.0, 6.0)

# Shown to the user when the OAuth refresh fails. The wording is chosen per
# failure class so the advice matches the cause: a rejected refresh token
# can only be fixed by re-linking, while a network/5xx blip is worth a retry.
_REFRESH_TERMINAL_MESSAGE = "Selora Cloud session expired — relink in Settings to continue."
_REFRESH_TRANSIENT_MESSAGE = (
    "Couldn't reach Selora Cloud to refresh the session — try again in a moment."
)


class CloudSessionExpiredError(ConnectionError):
    """The OAuth refresh token was rejected — the user must re-link.

    Subclasses ``ConnectionError`` so every existing ``except
    ConnectionError`` arm still catches it; the WS chat handler
    additionally uses the type to pick a localized, cause-specific
    message. Carries the English text as its default (log/fallback) value.
    """


class CloudUnreachableError(ConnectionError):
    """A token refresh failed transiently (network / 5xx / timeout).

    Distinct from :class:`CloudSessionExpiredError` so the user is told to
    retry rather than re-link. Also a ``ConnectionError`` subclass.
    """


class _RefreshResult(Enum):
    """Outcome of a token-refresh attempt.

    Distinguishing these lets the caller pick the right user message and
    behaviour: ``TERMINAL`` fails fast (relink), ``TRANSIENT`` is retried.
    """

    OK = "ok"
    # Credential rejected (4xx from the token endpoint) or missing refresh
    # token — retrying is pointless, the user must re-link.
    TERMINAL = "terminal"
    # Network error, timeout, 429, or 5xx from the token endpoint — the
    # refresh may succeed on a retry.
    TRANSIENT = "transient"


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


def _is_auth_error(err: str | None) -> bool:
    """Return True when an upstream error means the access token was rejected.

    The AI Gateway rejects a stale/revoked bearer with a 401 whose body is
    ``authentication_error`` / "Invalid or expired token". This is distinct
    from a transient 5xx: the request will keep failing until we mint a
    fresh token, so the caller reacts by force-refreshing and retrying once.

    Matches across the two error shapes the base provider produces: the
    non-streaming / tool paths preserve the ``HTTP 401`` status prefix,
    while ``send_request_stream`` unwraps the JSON body into just its
    ``message`` (no status), so we also match the gateway's message text.
    """
    if not err:
        return False
    lowered = err.lower()
    return (
        "http 401" in lowered
        or "authentication_error" in lowered
        or "invalid or expired token" in lowered
        or "expired token" in lowered
    )


def _jwt_expiry(token: str) -> float:
    """Best-effort read of a JWT's ``exp`` claim as a unix timestamp.

    The access token is an RS256 JWT; its own ``exp`` is the ground-truth
    expiry and is more reliable than the token endpoint's ``expires_in``,
    which Connect has been seen to omit — leaving no expiry to schedule a
    proactive refresh against and letting a dead token be sent until it
    401s. We only READ the claim to time the refresh; the gateway still
    verifies the signature on every call, so decoding without verification
    here is safe.

    Returns 0.0 when the token isn't a well-formed JWT or carries no
    positive numeric ``exp`` — callers treat 0.0 as "expiry unknown".
    """
    try:
        payload_b64 = token.split(".")[1]
    except (AttributeError, IndexError):
        return 0.0
    # JWT uses unpadded base64url; restore padding before decoding.
    payload_b64 += "=" * (-len(payload_b64) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except (ValueError, binascii.Error):
        return 0.0
    exp = payload.get("exp") if isinstance(payload, dict) else None
    if isinstance(exp, (int, float)) and not isinstance(exp, bool) and exp > 0:
        return float(exp)
    return 0.0


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
        expires_at = float(expires_at or 0.0)
        # A provisioned/persisted token may arrive with an unknown expiry
        # (0.0) — the hub auto-provisioner's nested blob carries no
        # expires_at. Recover it from the token's own ``exp`` claim so the
        # proactive refresh path works instead of sending the token until
        # it 401s.
        if not expires_at and access_token:
            expires_at = _jwt_expiry(access_token)
        self._expires_at = expires_at
        self._client_id = client_id
        self._entry_id = entry_id
        self._refresh_lock = asyncio.Lock()
        # Vision capability is a property of the server-side-routed model,
        # discovered via the gateway's capabilities endpoint. None = never
        # fetched (treated as no-vision); refreshed with a TTL by
        # async_refresh_capabilities. The fetch timestamp also uses None as
        # its never-fetched sentinel — time.monotonic() counts from boot,
        # so on a freshly started host a 0.0 sentinel would read as
        # "fetched recently" and suppress the first fetch entirely.
        self._vision_capable: bool | None = None
        self._capabilities_fetched_at: float | None = None

    # -- Identity ----------------------------------------------------------

    @property
    def provider_type(self) -> str:
        return "selora_cloud"

    @property
    def provider_name(self) -> str:
        return "Selora Cloud"

    @property
    def supports_vision(self) -> bool:
        # The gateway picks the chat model server-side, so vision support
        # is whatever the admin-configured model can do — asked via
        # async_refresh_capabilities, never assumed. Unknown (endpoint not
        # deployed yet, fetch failed) means False: offering an upload the
        # backend will 404 ("No endpoints found that support image input")
        # is worse than hiding it.
        return self._vision_capable is True

    async def async_refresh_capabilities(self) -> None:
        """Ask the gateway whether its active chat model supports vision.

        TTL-cached; any failure (endpoint missing on an older gateway,
        auth trouble, network) leaves the cached value in place and still
        stamps the TTL so a broken gateway isn't polled on every panel
        load. Never raises.
        """
        now = time.monotonic()
        if (
            self._capabilities_fetched_at is not None
            and now - self._capabilities_fetched_at < AIGATEWAY_CAPABILITIES_TTL_S
        ):
            return
        self._capabilities_fetched_at = now
        try:
            await self._ensure_token()
            session = self._get_session()
            async with session.get(
                f"{self._host}{AIGATEWAY_CAPABILITIES_PATH}",
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    # A gateway that predates the endpoint 404s — that's a
                    # definitive "can't do vision", not an error.
                    self._vision_capable = False
                    return
                data = await resp.json()
            self._vision_capable = bool(data.get("supports_vision"))
        except (aiohttp.ClientError, TimeoutError, ValueError, ConnectionError):
            _LOGGER.debug("AI Gateway capabilities fetch failed; keeping cached value")

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
    ) -> OpenAIChatPayload:
        # Connect's gateway picks the model server-side and overwrites any
        # client-supplied value; omit the field rather than send a misleading
        # one. Everything else stays OpenAI-compatible.
        payload = super().build_payload(
            system, messages, tools=tools, stream=stream, max_tokens=max_tokens
        )
        payload.pop("model", None)
        # Disable reasoning for the same reason as OpenRouter: for
        # chat-action turns the reasoning trace burns the output
        # budget and the model sometimes runs out of tokens before
        # emitting the tool_calls JSON, falling back to plain prose
        # that the safety policy then stomps. Connect's gateway
        # forwards this hint to whichever model it routes to.
        payload["reasoning"] = {"enabled": False}
        # Pin a low sampling temperature. The gateway may route to a cheap,
        # weak model that drifts on structured output at the default ~1.0;
        # 0.2 keeps tool-call/suggestion JSON near-deterministic without
        # making prose replies robotic.
        payload["temperature"] = CLOUD_LLM_TEMPERATURE
        return payload

    # -- Usage -------------------------------------------------------------

    def extract_usage(self, response_data: dict[str, Any]) -> LLMUsageInfo | None:
        # The gateway routes to a model server-side, so this provider has no
        # fixed model of its own. Capture the backing model the response
        # reports so usage events carry it (enables cost estimation).
        info = super().extract_usage(response_data)
        if info is not None:
            model = response_data.get("model")
            if isinstance(model, str) and model:
                info["model"] = model
        return info

    def parse_stream_usage(self, line: str) -> LLMUsageInfo | None:
        info = super().parse_stream_usage(line)
        if info is not None and line.startswith("data: "):
            try:
                obj = json.loads(line[6:])
            except json.JSONDecodeError:
                obj = None
            model = obj.get("model") if isinstance(obj, dict) else None
            if isinstance(model, str) and model:
                info["model"] = model
        return info

    # -- Token refresh -----------------------------------------------------

    def _needs_refresh(self) -> bool:
        if not self._refresh_token:
            return False
        if not self._access_token:
            return True
        if not self._expires_at:
            return False
        return time.time() + AIGATEWAY_REFRESH_LEEWAY_SECONDS >= self._expires_at

    async def _refresh_access_token(self, *, stale_token: str | None = None) -> _RefreshResult:
        """Refresh the access token using the stored refresh token.

        Returns ``_RefreshResult.OK`` on success, ``TERMINAL`` when the
        credential is rejected (4xx) or absent — retrying can't help and
        the user must re-link — and ``TRANSIENT`` for a network error,
        timeout, 429, or 5xx that a retry may clear. Persists the new
        tokens on success. Single-flight via ``self._refresh_lock``.

        ``stale_token`` drives reactive 401 recovery: refresh
        unconditionally — bypassing the not-yet-expired check, since the
        token is dead despite what our expiry math believes — but only if
        ``stale_token`` is still the live access token. A concurrent
        request that shared the same bearer may have already refreshed it,
        in which case we return OK so the caller reuses the fresh token
        rather than minting a redundant one (and, worse, discarding the
        just-issued token). The old bearer is left in place until the new
        one lands, so a parallel request never builds an empty
        Authorization header.
        """
        async with self._refresh_lock:
            if stale_token is not None:
                # Reactive path: skip the refresh entirely if someone else
                # already replaced the rejected token under the lock.
                if self._access_token != stale_token:
                    return _RefreshResult.OK
            # Proactive path: re-check under the lock — another caller may
            # have refreshed while we waited for it.
            elif not self._needs_refresh():
                return _RefreshResult.OK
            if not self._refresh_token:
                return _RefreshResult.TERMINAL

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
                        # 5xx / 429 are server-side or throttling — worth a
                        # retry. Any other 4xx means the refresh token or
                        # client is rejected: re-linking is the only fix.
                        if resp.status >= 500 or resp.status == 429:
                            return _RefreshResult.TRANSIENT
                        return _RefreshResult.TERMINAL
                    data = await resp.json()
            except (aiohttp.ClientError, TimeoutError) as exc:
                _LOGGER.warning("AI Gateway token refresh error: %s", _mask_tokens(str(exc)))
                return _RefreshResult.TRANSIENT

            access = data.get("access_token")
            expires_in = int(data.get("expires_in") or 0)
            new_refresh = data.get("refresh_token")  # only present on rotation
            if not access:
                # 200 with no token is a server contract violation, not a
                # bad credential — treat as transient so a retry can recover.
                _LOGGER.warning("AI Gateway refresh response missing access_token")
                return _RefreshResult.TRANSIENT

            self._access_token = access
            # Prefer the JWT's own ``exp`` — it's authoritative and survives
            # a server that omits ``expires_in`` (which previously left
            # _expires_at=0.0, i.e. "unknown", defeating proactive refresh
            # and never getting a durable expiry into .storage).
            jwt_exp = _jwt_expiry(access)
            if jwt_exp > 0:
                self._expires_at = jwt_exp
            elif expires_in > 0:
                self._expires_at = time.time() + expires_in
            else:
                self._expires_at = 0.0
            if new_refresh:
                self._refresh_token = new_refresh

            self._persist_tokens()
            return _RefreshResult.OK

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

    async def _refresh_with_retry(self, *, stale_token: str | None = None) -> None:
        """Refresh the token, retrying transient failures over the retry budget.

        On ``TERMINAL`` (rejected credential) fail fast — retrying can't help
        and the user must re-link; sending the request with the dead token
        would just return a mystifying 401. On ``TRANSIENT`` (network blip /
        5xx / 429 / Connect upgrade in progress) retry on the same short
        budget the upstream path uses, so a momentary hiccup self-heals
        instead of surfacing. ``stale_token`` is forwarded on every attempt
        so the reactive path keeps forcing (and deduplicating) the refresh
        across retries.
        """
        result = await self._refresh_access_token(stale_token=stale_token)
        if result is _RefreshResult.OK:
            return
        if result is _RefreshResult.TERMINAL:
            raise CloudSessionExpiredError(_REFRESH_TERMINAL_MESSAGE)
        for delay in _UPSTREAM_RETRY_DELAYS:
            await asyncio.sleep(delay)
            result = await self._refresh_access_token(stale_token=stale_token)
            if result is _RefreshResult.OK:
                return
            if result is _RefreshResult.TERMINAL:
                raise CloudSessionExpiredError(_REFRESH_TERMINAL_MESSAGE)
        raise CloudUnreachableError(_REFRESH_TRANSIENT_MESSAGE)

    async def _ensure_token(self) -> None:
        if not self._needs_refresh():
            return
        await self._refresh_with_retry()

    async def _force_refresh_after_auth_error(self, rejected_token: str) -> None:
        """Mint a fresh token after the gateway 401'd ``rejected_token``.

        Called reactively when the gateway rejects a request even though
        ``_ensure_token`` had judged the token valid — it was invalid for a
        reason our expiry math can't see (server-side revocation, clock
        skew, or a token whose ``exp`` we never learned). Delegates to
        ``_refresh_with_retry(stale_token=…)`` so the refresh is forced past
        the expiry check, deduplicated against a concurrent request that
        already replaced the same rejected token, AND retried over the
        transient budget exactly like the proactive path — a one-off refresh
        outage shouldn't turn a recoverable 401 into a hard failure.

        Raises the same errors as ``_ensure_token`` so callers surface the
        right relink/retry message; on success the caller retries the
        request once with the new bearer.
        """
        await self._refresh_with_retry(stale_token=rejected_token)

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
        auth_retried = False
        for delay in _UPSTREAM_RETRY_DELAYS:
            used_token = self._access_token
            result, err = await super().send_request(
                system, messages, max_tokens=max_tokens, log_errors=False, timeout=timeout
            )
            if result is not None:
                return result, None
            # A 401 slipped past the proactive expiry check (unknown/stale
            # expiry, server-side revocation). Force a fresh token and retry
            # once — never loop, so a token the gateway keeps rejecting still
            # surfaces instead of hammering the refresh endpoint.
            if _is_auth_error(err) and not auth_retried:
                auth_retried = True
                await self._force_refresh_after_auth_error(used_token)
                continue
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
        # Final attempt after the retry budget. Keep the caller's timeout —
        # the hourly analysis cycle asks for a longer one, and dropping it
        # here (or on the auth retry below) would time out only on this path.
        used_token = self._access_token
        result, err = await super().send_request(
            system, messages, max_tokens=max_tokens, log_errors=False, timeout=timeout
        )
        if result is not None:
            return result, None
        if _is_auth_error(err) and not auth_retried:
            await self._force_refresh_after_auth_error(used_token)
            result, err = await super().send_request(
                system, messages, max_tokens=max_tokens, log_errors=False, timeout=timeout
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
        used_token = self._access_token
        try:
            return await super().raw_request(system, messages, tools=tools)
        except ConnectionError as exc:
            # A 401 despite a "valid" token — force-refresh and retry once.
            if not _is_auth_error(str(exc)):
                raise
            await self._force_refresh_after_auth_error(used_token)
            return await super().raw_request(system, messages, tools=tools)

    async def raw_request_stream(  # type: ignore[override]
        self,
        *args: Any,
        **kwargs: Any,
    ) -> AsyncIterator[aiohttp.ClientResponse]:
        # The parent generator raises ConnectionError before yielding
        # anything when the initial POST returns non-200 — so it is safe
        # to retry the whole generator on a transient cold-start error (or
        # a 401 that a fresh token clears). A successful first attempt
        # yields once and we return immediately.
        await self._ensure_token()
        auth_retried = False
        for delay in _UPSTREAM_RETRY_DELAYS:
            used_token = self._access_token
            try:
                async for item in super().raw_request_stream(*args, **kwargs):
                    yield item
                return
            except ConnectionError as exc:
                if _is_auth_error(str(exc)) and not auth_retried:
                    auth_retried = True
                    await self._force_refresh_after_auth_error(used_token)
                    continue
                if not _is_transient_upstream_error(str(exc)):
                    raise
                _LOGGER.info(
                    "Selora Cloud transient upstream error on stream (%s) — retrying after %.1fs",
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                await self._ensure_token()
        # Final attempt after the retry budget. If it is the first to hit a
        # 401 (all prior attempts were transient), still refresh-and-retry
        # once so a token that expired mid-retry doesn't propagate unhandled.
        used_token = self._access_token
        try:
            async for item in super().raw_request_stream(*args, **kwargs):
                yield item
        except ConnectionError as exc:
            if not (_is_auth_error(str(exc)) and not auth_retried):
                raise
            await self._force_refresh_after_auth_error(used_token)
            async for item in super().raw_request_stream(*args, **kwargs):
                yield item

    async def send_request_stream(  # type: ignore[override]
        self,
        *args: Any,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        # See raw_request_stream above for why retrying the parent
        # generator is safe: the cold-start (or 401) failure happens at
        # the initial status check, before any chunk is yielded.
        await self._ensure_token()
        auth_retried = False
        for delay in _UPSTREAM_RETRY_DELAYS:
            used_token = self._access_token
            try:
                async for chunk in super().send_request_stream(*args, **kwargs):
                    yield chunk
                return
            except ConnectionError as exc:
                if _is_auth_error(str(exc)) and not auth_retried:
                    auth_retried = True
                    await self._force_refresh_after_auth_error(used_token)
                    continue
                if not _is_transient_upstream_error(str(exc)):
                    raise
                _LOGGER.info(
                    "Selora Cloud transient upstream error on stream (%s) — retrying after %.1fs",
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                await self._ensure_token()
        # Final attempt after the retry budget. If it is the first to hit a
        # 401 (all prior attempts were transient), still refresh-and-retry
        # once so a token that expired mid-retry doesn't propagate unhandled.
        used_token = self._access_token
        try:
            async for chunk in super().send_request_stream(*args, **kwargs):
                yield chunk
        except ConnectionError as exc:
            if not (_is_auth_error(str(exc)) and not auth_retried):
                raise
            await self._force_refresh_after_auth_error(used_token)
            async for chunk in super().send_request_stream(*args, **kwargs):
                yield chunk

    # -- Health check ------------------------------------------------------

    async def health_check(self) -> bool:
        # Earlier versions probed reachability by posting a one-shot
        # ``{"role": "user", "content": "Hi"}`` to /chat/completions. The
        # AI Gateway materializes every chat call as a session, so each
        # health check surfaced as a "Greeting"/"Chat initiation" entry
        # in the user's conversation list — fired on every config-entry
        # setup and reload, which in production showed up as a phantom
        # session roughly every 10 minutes despite zero user input.
        #
        # Validate via the token endpoint instead: a fresh access token
        # is itself proof of reachability + valid credentials, and the
        # token endpoint (/oauth/aigw/token) is not session-bearing.
        if not self._refresh_token and not self._access_token:
            return False
        # Already-valid access token: trust the most recent prior call.
        # Reloading the entry should not re-prove the link by burning
        # an LLM call — the previous successful chat or refresh already
        # did that.
        if self._access_token and not self._needs_refresh():
            return True
        # Refresh leeway expired (or we never had an access token):
        # exercise the token endpoint as the auth + reachability probe.
        # Success → healthy. Failure (terminal or transient) → unhealthy,
        # same outcome the old chat-based probe produced when the gateway
        # was unreachable.
        return await self._refresh_access_token() is _RefreshResult.OK
