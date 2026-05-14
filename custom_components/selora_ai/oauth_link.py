"""HA-mediated OAuth linking for Selora Connect & AI Gateway.

Why this exists
---------------
The original PKCE flow used a popup window opened from the panel.
Inside the Home Assistant Companion app the panel runs in a WebView
that blocks ``window.open()`` outright — and a same-window redirect
breaks the app's WebSocket to HA core. Neither pattern works on
mobile/desktop apps.

This module ships a third option: the panel asks HA to start a link
session, gets back an ``authorize_url`` it opens in the system
browser via a plain ``<a target="_blank">`` (which the Companion
app correctly delegates), and HA hosts a stable callback view that
finishes the exchange on the user's behalf. The panel listens for
a dispatcher signal to refresh once linking completes.

Connect's OAuth endpoints accept any ``redirect_uri`` as long as
``client_id == redirect_uri`` (the legacy public-client convention),
so we can use HA's external URL here without any Connect-side
registration step.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import logging
import secrets
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from aiohttp import ClientError, ClientSession, ClientTimeout, web
from homeassistant.components import websocket_api
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.websocket_api import decorators
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.network import NoURLAvailableError, get_url
import voluptuous as vol

from .const import (
    CONF_AIGATEWAY_ACCESS_TOKEN,
    CONF_AIGATEWAY_CLIENT_ID,
    CONF_AIGATEWAY_EXPIRES_AT,
    CONF_AIGATEWAY_REFRESH_TOKEN,
    CONF_AIGATEWAY_USER_EMAIL,
    CONF_AIGATEWAY_USER_ID,
    CONF_LLM_PROVIDER,
    CONF_SELORA_CONNECT_ENABLED,
    CONF_SELORA_CONNECT_URL,
    CONF_SELORA_INSTALLATION_ID,
    CONF_SELORA_JWT_KEY,
    DEFAULT_SELORA_CONNECT_URL,
    DOMAIN,
    LLM_PROVIDER_SELORA_CLOUD,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

CALLBACK_PATH = f"/api/{DOMAIN}/oauth_callback"
SIGNAL_OAUTH_LINKED = f"{DOMAIN}_oauth_linked"
EVENT_OAUTH_LINKED = f"{DOMAIN}_oauth_linked"

# Pending sessions live for 10 minutes max — gives the user time to
# walk through the auth flow but limits how long an unredeemed state
# token sits in memory.
PENDING_TTL_SECONDS = 600

FLOW_CONNECT = "connect"
FLOW_AIGATEWAY = "aigateway"


@dataclass
class _Pending:
    """One in-flight OAuth link session."""

    flow: str
    code_verifier: str
    state: str
    connect_url: str
    redirect_uri: str
    client_id: str
    entry_id: str
    started_at: float = field(default_factory=time.time)


def _store(hass: HomeAssistant) -> dict[str, _Pending]:
    return hass.data.setdefault(DOMAIN, {}).setdefault("oauth_pending", {})


def _prune(hass: HomeAssistant) -> None:
    cutoff = time.time() - PENDING_TTL_SECONDS
    bucket = _store(hass)
    for key in [k for k, p in bucket.items() if p.started_at < cutoff]:
        bucket.pop(key, None)


def _resolve_entry(hass: HomeAssistant, entry_id: str | None = None) -> ConfigEntry | None:
    """Find the LLM config entry — entry_id given or first match."""
    from .const import CONF_ENTRY_TYPE, ENTRY_TYPE_LLM

    if entry_id:
        return hass.config_entries.async_get_entry(entry_id)
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(CONF_ENTRY_TYPE, ENTRY_TYPE_LLM) == ENTRY_TYPE_LLM:
            return entry
    return None


def _generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    """Read the unverified JWT payload (display fields only)."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except ValueError, UnicodeDecodeError:
        return {}


def _resolve_external_url(hass: HomeAssistant) -> str:
    """Best URL for HA's callback — external if available, else local."""
    try:
        return get_url(hass, allow_external=True, prefer_external=True).rstrip("/")
    except NoURLAvailableError:
        try:
            return get_url(hass, allow_internal=True, allow_external=True).rstrip("/")
        except NoURLAvailableError as err:
            raise RuntimeError(
                "Home Assistant has no reachable URL configured — "
                "set an internal/external URL under Settings → System → Network."
            ) from err


# ── Exchange helpers (refactored from __init__.py WS handlers) ──────────────


async def exchange_connect_code(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    connect_url: str,
) -> dict[str, Any]:
    """Exchange a Connect auth code for installation creds + register device.

    Returns ``{"status": "linked", "device_id": ...}`` on success.
    Raises :class:`RuntimeError` with a human-readable message otherwise.
    """
    timeout = ClientTimeout(total=15)
    async with ClientSession() as session:
        try:
            async with session.post(
                f"{connect_url}/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": code_verifier,
                    "client_id": redirect_uri,
                    "redirect_uri": redirect_uri,
                },
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    _LOGGER.warning("Connect token exchange failed (%s): %s", resp.status, body)
                    raise RuntimeError(f"Connect returned HTTP {resp.status}")
                token_data = await resp.json()
        except (ClientError, TimeoutError) as err:
            raise RuntimeError(f"Cannot reach Connect: {err}") from err

        access_token = token_data.get("access_token")
        if not access_token:
            raise RuntimeError("No access_token in Connect response")

        try:
            async with session.post(
                f"{connect_url}/api/v1/mcp/devices/register",
                json={"device_name": hass.config.location_name or "Home Assistant"},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    _LOGGER.warning(
                        "Connect device registration failed (%s): %s", resp.status, body
                    )
                    raise RuntimeError(f"Device registration returned HTTP {resp.status}")
                device_data = await resp.json()
        except (ClientError, TimeoutError) as err:
            raise RuntimeError(f"Cannot reach Connect for device registration: {err}") from err

        device_id = device_data.get("device_id")
        installation_id = device_data.get("installation_id")
        scope_id_from_device = device_data.get("scope_id")
        if not device_id:
            raise RuntimeError("Connect response missing device_id")

        # Installation-scoped JWT key — falls back to per-device key.
        jwt_key = None
        scope_id = None
        if installation_id:
            try:
                async with session.get(
                    f"{connect_url}/api/v1/installations/{installation_id}/mcp-auth-config",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=timeout,
                ) as resp:
                    if resp.status == 200:
                        auth_config = await resp.json()
                        jwt_key = auth_config.get("jwt_key")
                        scope_id = auth_config.get("scope_id")
                    else:
                        _LOGGER.warning(
                            "Failed to fetch MCP auth config (%s), using device key",
                            resp.status,
                        )
            except (ClientError, TimeoutError) as err:
                _LOGGER.warning("Could not reach Connect for MCP auth config: %s", err)

    if not jwt_key:
        jwt_key = device_data.get("jwt_key")
    if not jwt_key:
        raise RuntimeError("Connect response missing jwt_key")

    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_SELORA_CONNECT_ENABLED: True,
            CONF_SELORA_CONNECT_URL: connect_url,
            CONF_SELORA_INSTALLATION_ID: scope_id
            or scope_id_from_device
            or installation_id
            or device_id,
            CONF_SELORA_JWT_KEY: jwt_key,
        },
    )

    async def _reload() -> None:
        try:
            await hass.config_entries.async_reload(entry.entry_id)
        except Exception:  # noqa: BLE001 — reload errors are best-effort
            _LOGGER.exception("Failed to reload entry after Connect linking")

    hass.async_create_task(_reload())
    return {"status": "linked", "device_id": device_id}


async def exchange_aigateway_code(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: str,
    connect_url: str,
) -> dict[str, Any]:
    """Exchange an AI Gateway auth code for access + refresh tokens."""
    timeout = ClientTimeout(total=15)
    async with ClientSession() as session:
        try:
            async with session.post(
                f"{connect_url}/oauth/aigw/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": code_verifier,
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                },
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    _LOGGER.warning("AI Gateway token exchange failed (%s): %s", resp.status, body)
                    raise RuntimeError(f"AI Gateway returned HTTP {resp.status}")
                token_data = await resp.json()
        except (ClientError, TimeoutError) as err:
            raise RuntimeError(f"Cannot reach Connect: {err}") from err

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = int(token_data.get("expires_in") or 0)
    if not access_token or not refresh_token:
        raise RuntimeError("Missing access_token or refresh_token")

    claims = _decode_jwt_claims(access_token)
    user_email = claims.get("email") or ""
    user_id = str(claims.get("sub") or "")
    expires_at = time.time() + expires_in if expires_in > 0 else 0.0

    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_LLM_PROVIDER: LLM_PROVIDER_SELORA_CLOUD,
            CONF_AIGATEWAY_ACCESS_TOKEN: access_token,
            CONF_AIGATEWAY_REFRESH_TOKEN: refresh_token,
            CONF_AIGATEWAY_EXPIRES_AT: expires_at,
            CONF_AIGATEWAY_USER_EMAIL: user_email,
            CONF_AIGATEWAY_USER_ID: user_id,
            CONF_AIGATEWAY_CLIENT_ID: client_id,
            CONF_SELORA_CONNECT_URL: connect_url,
        },
    )

    async def _reload() -> None:
        try:
            await hass.config_entries.async_reload(entry.entry_id)
        except Exception:  # noqa: BLE001 — reload errors are best-effort
            _LOGGER.exception("Failed to reload entry after AI Gateway linking")

    hass.async_create_task(_reload())
    return {"status": "linked", "user_email": user_email}


# ── Callback HTTP view ──────────────────────────────────────────────────────


class SeloraOAuthCallbackView(HomeAssistantView):
    """Public OAuth callback — no HA login required.

    Authentication is provided by the ``state`` token: only callbacks
    matching a server-generated pending state are honoured. Anything
    else gets a 400 and is ignored.
    """

    url = CALLBACK_PATH
    name = f"api:{DOMAIN}:oauth_callback"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        params = request.query
        state = params.get("state", "")
        code = params.get("code", "")
        error = params.get("error", "")
        error_description = params.get("error_description", "")

        _prune(self._hass)
        bucket = _store(self._hass)
        pending = bucket.pop(state, None) if state else None

        if pending is None:
            return _render_result(
                "Link expired or invalid",
                "This sign-in link is no longer valid. Please return to "
                "Home Assistant and start the link again.",
                ok=False,
            )

        if error:
            async_dispatcher_send(
                self._hass,
                SIGNAL_OAUTH_LINKED,
                {"flow": pending.flow, "ok": False, "error": error_description or error},
            )
            self._hass.bus.async_fire(
                EVENT_OAUTH_LINKED,
                {"flow": pending.flow, "ok": False, "error": error_description or error},
            )
            return _render_result(
                "Sign-in failed",
                error_description or error,
                ok=False,
            )

        if not code:
            return _render_result(
                "Sign-in failed",
                "Missing authorization code in the response from Connect.",
                ok=False,
            )

        entry = _resolve_entry(self._hass, pending.entry_id)
        if entry is None:
            return _render_result(
                "Selora AI not configured",
                "The Selora AI integration was removed before linking finished.",
                ok=False,
            )

        try:
            if pending.flow == FLOW_AIGATEWAY:
                result = await exchange_aigateway_code(
                    self._hass,
                    entry,
                    code=code,
                    code_verifier=pending.code_verifier,
                    redirect_uri=pending.redirect_uri,
                    client_id=pending.client_id,
                    connect_url=pending.connect_url,
                )
                title = "Selora Cloud linked"
                body = (
                    "You're signed in"
                    + (f" as {result.get('user_email')}" if result.get("user_email") else "")
                    + ". You can close this tab and return to Home Assistant."
                )
            else:
                await exchange_connect_code(
                    self._hass,
                    entry,
                    code=code,
                    code_verifier=pending.code_verifier,
                    redirect_uri=pending.redirect_uri,
                    connect_url=pending.connect_url,
                )
                title = "Selora Connect linked"
                body = (
                    "Your Home Assistant is now linked to Selora Connect. "
                    "You can close this tab and return to Home Assistant."
                )
        except RuntimeError as err:
            _LOGGER.warning("OAuth exchange failed (%s): %s", pending.flow, err)
            async_dispatcher_send(
                self._hass,
                SIGNAL_OAUTH_LINKED,
                {"flow": pending.flow, "ok": False, "error": str(err)},
            )
            self._hass.bus.async_fire(
                EVENT_OAUTH_LINKED,
                {"flow": pending.flow, "ok": False, "error": str(err)},
            )
            return _render_result("Linking failed", str(err), ok=False)

        async_dispatcher_send(self._hass, SIGNAL_OAUTH_LINKED, {"flow": pending.flow, "ok": True})
        self._hass.bus.async_fire(EVENT_OAUTH_LINKED, {"flow": pending.flow, "ok": True})
        return _render_result(title, body, ok=True)


def _render_result(title: str, message: str, *, ok: bool) -> web.Response:
    """Tiny self-contained HTML page — no HA assets required."""
    color = "#16a34a" if ok else "#dc2626"
    icon = "✓" if ok else "✕"
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Selora AI</title>
<style>
  html, body {{ margin:0; padding:0; height:100%; background:#0b0d10; color:#e6e8eb;
    font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
  .wrap {{ display:flex; min-height:100%; align-items:center; justify-content:center; padding:24px; }}
  .card {{ max-width:420px; text-align:center; padding:32px;
    background:#15181c; border:1px solid #23272d; border-radius:14px; }}
  .icon {{ width:56px; height:56px; border-radius:50%; margin:0 auto 16px;
    background:{color}; color:#fff; font-size:30px; line-height:56px; font-weight:600; }}
  h1 {{ font-size:20px; margin:0 0 8px; font-weight:600; }}
  p  {{ margin:0; color:#9aa3ad; }}
</style>
</head>
<body>
  <div class="wrap"><div class="card">
    <div class="icon">{icon}</div>
    <h1>{title}</h1>
    <p>{message}</p>
  </div></div>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html", charset="utf-8")


# ── WS handlers: start_link ────────────────────────────────────────────────


def _require_admin(connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> bool:
    user = connection.user
    if user is None or not user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Admin required")
        return False
    return True


async def _start_link(
    hass: HomeAssistant,
    flow: str,
    *,
    connect_url_override: str,
    authorize_path: str,
    scope: str,
    extra_params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Generate PKCE, store pending state, return authorize URL."""
    entry = _resolve_entry(hass)
    if entry is None:
        raise RuntimeError("Selora AI not configured")

    base_url = _resolve_external_url(hass)
    redirect_uri = f"{base_url}{CALLBACK_PATH}"
    client_id = redirect_uri  # Public-client convention.

    connect_url = (
        connect_url_override
        or entry.data.get(CONF_SELORA_CONNECT_URL)
        or DEFAULT_SELORA_CONNECT_URL
    ).rstrip("/")

    code_verifier, code_challenge = _generate_pkce()
    state = secrets.token_urlsafe(32)

    _prune(hass)
    _store(hass)[state] = _Pending(
        flow=flow,
        code_verifier=code_verifier,
        state=state,
        connect_url=connect_url,
        redirect_uri=redirect_uri,
        client_id=client_id,
        entry_id=entry.entry_id,
    )

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": scope,
    }
    if extra_params:
        params.update(extra_params)

    return {
        "authorize_url": f"{connect_url}{authorize_path}?{urlencode(params)}",
        "state": state,
        "redirect_uri": redirect_uri,
    }


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/start_aigw_link",
        vol.Optional("connect_url", default=""): str,
    }
)
async def _ws_start_aigw_link(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Begin a Selora Cloud (AI Gateway) link session."""
    if not _require_admin(connection, msg):
        return
    try:
        result = await _start_link(
            hass,
            FLOW_AIGATEWAY,
            connect_url_override=msg.get("connect_url", ""),
            authorize_path="/oauth/aigw/authorize",
            scope="ai-gateway",
            extra_params={
                "client_name": hass.config.location_name or "Home Assistant",
            },
        )
    except RuntimeError as err:
        connection.send_error(msg["id"], "start_failed", str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/start_connect_link",
        vol.Optional("connect_url", default=""): str,
    }
)
async def _ws_start_connect_link(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Begin a Selora Connect (MCP provision) link session."""
    if not _require_admin(connection, msg):
        return
    try:
        result = await _start_link(
            hass,
            FLOW_CONNECT,
            connect_url_override=msg.get("connect_url", ""),
            authorize_path="/oauth/authorize",
            scope="mcp:provision",
            extra_params={
                "device_name": hass.config.location_name or "Home Assistant",
            },
        )
    except RuntimeError as err:
        connection.send_error(msg["id"], "start_failed", str(err))
        return
    connection.send_result(msg["id"], result)


# ── Setup hook ─────────────────────────────────────────────────────────────


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the callback view + WS handlers. Idempotent."""
    flag = hass.data.setdefault(DOMAIN, {})
    if flag.get("oauth_link_registered"):
        return
    hass.http.register_view(SeloraOAuthCallbackView(hass))
    websocket_api.async_register_command(hass, _ws_start_aigw_link)
    websocket_api.async_register_command(hass, _ws_start_connect_link)
    flag["oauth_link_registered"] = True
