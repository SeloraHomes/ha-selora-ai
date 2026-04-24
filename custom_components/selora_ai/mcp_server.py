"""Selora AI MCP Server — exposes the Selora intelligence layer over MCP.

Endpoint: POST /api/selora_ai/mcp
Protocol: Model Context Protocol 1.26.0, Streamable HTTP (stateless)
Auth:     HA Bearer token or Selora Connect JWT (dual-auth)

Phase 1 tools
─────────────
  selora_list_automations     List Selora-managed automations with status + risk
  selora_get_automation       Full automation detail with YAML and version history
  selora_validate_automation  Validate + risk-assess YAML without writing anything
  selora_create_automation    Create automation from externally-generated YAML
  selora_accept_automation    Enable/commit a pending automation
  selora_delete_automation    Delete a Selora-managed automation
  selora_get_home_snapshot    Current entity states grouped by area
  selora_chat                 Natural-language chat with Selora's LLM
  selora_list_sessions        Recent conversation sessions

Phase 2 tools (device data)
───────────────────────────
  selora_list_devices         List HA devices with area/domain filters
  selora_get_device           Full device detail with entities and current states

Security
────────
  - Dual auth: HA Bearer token (middleware) OR Selora Connect JWT (HS256)
  - HA auth is checked first (set by HA middleware); Selora JWT is fallback
  - Write tools enforce admin-level authorization
  - All user-controlled string fields pass through _sanitize_untrusted_text()
    before inclusion in responses (prompt-injection boundary)
  - selora_create_automation runs server-side validation + risk assessment
    regardless of the YAML's origin
  - Automations are created disabled (initial_state: false) by default

See docs/selora-mcp-server.md and docs/adr/ADR-001-selora-mcp-server.md.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from http import HTTPStatus
import json
import logging
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any

import aiohttp
from aiohttp import web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import Unauthorized

from .const import (
    COLLECTOR_DOMAINS,
    DOMAIN,
    LIGHT_ENTITY_EXCLUDE_PATTERNS,
    SELORA_JWT_ISSUER,
)
from .selora_auth import AuthenticationError, SeloraAuthContext, authenticate_request

if TYPE_CHECKING:
    from . import ConversationStore
    from .automation_store import AutomationStore
    from .collector import DataCollector
    from .llm_client import LLMClient
    from .types import (
        ArchitectResponse,
        AutomationDict,
        AutomationMetadata,
        AutomationRecord,
        RiskAssessment,
    )

_LOGGER = logging.getLogger(__name__)


# ── Vendored MCP types (no pydantic dependency) ──────────────────────────────


@dataclass
class MCPTool:
    """MCP tool definition (replaces mcp.MCPTool)."""

    name: str
    description: str
    inputSchema: dict[str, Any]


@dataclass
class MCPTextContent:
    """MCP text content block (replaces mcp.MCPTextContent)."""

    type: str = "text"
    text: str = ""


_MCP_URL = "/api/selora_ai/mcp"
_PROTECTED_RESOURCE_URL = "/.well-known/oauth-protected-resource/api/selora_ai/mcp"
_OAUTH_AS_METADATA_URL = "/.well-known/oauth-authorization-server/api/selora_ai/mcp"
_OAUTH_TOKEN_PROXY_URL = "/api/selora_ai/oauth/token"
_TIMEOUT_SECS = 60
_CONTENT_TYPE_JSON = "application/json"

# ── CORS for browser-based MCP clients ───────────────────────────────────────
# MCP Streamable HTTP requires CORS so browser-based clients (mcp-inspector,
# web-hosted agents) can reach the endpoint.  HA's built-in aiohttp_cors only
# allows a fixed set of headers and we cannot inject middleware after startup,
# so each view has an options() handler and on_response_prepare adds CORS
# headers to all responses.

_MCP_CORS_HEADERS = "Origin, Accept, Content-Type, Authorization, Mcp-Protocol-Version"
_MCP_CORS_METHODS = "GET, POST, OPTIONS"
_MCP_CORS_MAX_AGE = "86400"


def _cors_headers(origin: str) -> dict[str, str]:
    """Build CORS response headers for the given origin."""
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": _MCP_CORS_METHODS,
        "Access-Control-Allow-Headers": _MCP_CORS_HEADERS,
        "Access-Control-Max-Age": _MCP_CORS_MAX_AGE,
    }


def _validate_cors_origin(request: web.Request) -> str:
    """Return the Origin header only if it matches a trusted pattern.

    Allows same-host origins (HA frontend), localhost development,
    and mDNS .local addresses.  Returns empty string for untrusted origins
    so the Access-Control-Allow-Origin header is omitted.
    """
    origin = request.headers.get("Origin", "")
    if not origin:
        return ""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(origin)
        host = parsed.hostname or ""
    except Exception:
        return ""
    # Allow localhost / loopback and .local (mDNS) origins unconditionally
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local"):
        return origin
    # Allow when the Origin matches the Host header (same-origin)
    request_host = request.host.split(":")[0] if request.host else ""
    if host == request_host:
        return origin
    # Allow private-network IPs (RFC 1918)
    try:
        import ipaddress

        addr = ipaddress.ip_address(host)
        if addr.is_private:
            return origin
    except ValueError:
        pass
    return ""


async def _cors_preflight(request: web.Request) -> web.Response:
    """Return a 204 CORS preflight response."""
    origin = _validate_cors_origin(request)
    if not origin:
        return web.Response(status=204)
    return web.Response(status=204, headers=_cors_headers(origin))


def _add_cors(request: web.Request, response: web.Response) -> web.Response:
    """Add CORS headers to a response and return it."""
    origin = _validate_cors_origin(request)
    if origin:
        response.headers.update(_cors_headers(origin))
    return response


# ── Rate limiting ──────────────────────────────────────────────────────────────

# Per-IP sliding window: max requests in the window before returning 429.
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX_REQUESTS = 30  # max requests per IP per window
_RATE_LIMIT_AUTH_FAILURES = 5  # max auth failures per IP per window


class _RateLimiter:
    """Simple per-key sliding-window rate limiter (in-memory, no persistence)."""

    def __init__(self, window: int, max_hits: int) -> None:
        self._window = window
        self._max_hits = max_hits
        self._hits: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        """Return True if the key is within the rate limit."""
        now = time.monotonic()
        bucket = self._hits[key]
        # Prune expired entries
        cutoff = now - self._window
        self._hits[key] = bucket = [t for t in bucket if t > cutoff]
        if len(bucket) >= self._max_hits:
            return False
        bucket.append(now)
        return True


_request_limiter = _RateLimiter(_RATE_LIMIT_WINDOW, _RATE_LIMIT_MAX_REQUESTS)
_auth_fail_limiter = _RateLimiter(_RATE_LIMIT_WINDOW, _RATE_LIMIT_AUTH_FAILURES)


def _get_client_ip(request: web.Request) -> str:
    """Return the client IP using HA's trusted-proxy-aware remote address.

    ``request.remote`` is set by HA's ``async_setup_forwarded`` middleware,
    which only trusts ``X-Forwarded-For`` from configured trusted proxies.
    This prevents callers from spoofing the header to bypass rate limits.
    """
    return request.remote or "unknown"


# ── Tool name constants ────────────────────────────────────────────────────────

TOOL_LIST_AUTOMATIONS = "selora_list_automations"
TOOL_GET_AUTOMATION = "selora_get_automation"
TOOL_VALIDATE_AUTOMATION = "selora_validate_automation"
TOOL_CREATE_AUTOMATION = "selora_create_automation"
TOOL_ACCEPT_AUTOMATION = "selora_accept_automation"
TOOL_DELETE_AUTOMATION = "selora_delete_automation"
TOOL_GET_HOME_SNAPSHOT = "selora_get_home_snapshot"
TOOL_CHAT = "selora_chat"
TOOL_LIST_SESSIONS = "selora_list_sessions"
TOOL_LIST_PATTERNS = "selora_list_patterns"
TOOL_GET_PATTERN = "selora_get_pattern"
TOOL_LIST_SUGGESTIONS = "selora_list_suggestions"
TOOL_ACCEPT_SUGGESTION = "selora_accept_suggestion"
TOOL_DISMISS_SUGGESTION = "selora_dismiss_suggestion"
TOOL_TRIGGER_SCAN = "selora_trigger_scan"
TOOL_LIST_DEVICES = "selora_list_devices"
TOOL_GET_DEVICE = "selora_get_device"
TOOL_HOME_ANALYTICS = "selora_home_analytics"

# Tools that require admin privileges (write/mutating operations)
_ADMIN_TOOLS = frozenset(
    {
        TOOL_CREATE_AUTOMATION,
        TOOL_ACCEPT_AUTOMATION,
        TOOL_DELETE_AUTOMATION,
        TOOL_CHAT,
        TOOL_ACCEPT_SUGGESTION,
        TOOL_DISMISS_SUGGESTION,
        TOOL_TRIGGER_SCAN,
    }
)

# All read-only tools (complement of _ADMIN_TOOLS)
_READ_ONLY_TOOLS = frozenset(
    {
        TOOL_LIST_AUTOMATIONS,
        TOOL_GET_AUTOMATION,
        TOOL_VALIDATE_AUTOMATION,
        TOOL_GET_HOME_SNAPSHOT,
        TOOL_LIST_SESSIONS,
        TOOL_LIST_PATTERNS,
        TOOL_GET_PATTERN,
        TOOL_LIST_SUGGESTIONS,
        TOOL_LIST_DEVICES,
        TOOL_GET_DEVICE,
        TOOL_HOME_ANALYTICS,
    }
)


def _check_tool_access(auth_ctx: SeloraAuthContext, tool_name: str) -> None:
    """Raise Unauthorized if *auth_ctx* does not grant access to *tool_name*.

    For MCP tokens with ``allowed_tools`` set, only those tools are accessible.
    For MCP tokens with ``read_only`` permission, only read-only tools are accessible.
    For all other auth types, the existing is_admin check applies.
    """
    if auth_ctx.auth_type == "mcp_token":
        if auth_ctx.allowed_tools is not None:
            # Custom permission: explicit tool allowlist
            if tool_name not in auth_ctx.allowed_tools:
                raise Unauthorized(f"Token does not grant access to {tool_name}")
            return
        if not auth_ctx.is_admin and tool_name in _ADMIN_TOOLS:
            raise Unauthorized(f"Read-only token cannot access {tool_name}")
        return

    # HA token / Selora JWT: binary admin check
    if tool_name in _ADMIN_TOOLS and not auth_ctx.is_admin:
        raise Unauthorized("Admin access is required for this Selora MCP tool")


def _can_access_tool(auth_ctx: SeloraAuthContext, tool_name: str) -> bool:
    """Return True if *auth_ctx* grants access to *tool_name*."""
    if auth_ctx.auth_type == "mcp_token":
        if auth_ctx.allowed_tools is not None:
            return tool_name in auth_ctx.allowed_tools
        return auth_ctx.is_admin or tool_name not in _ADMIN_TOOLS
    return auth_ctx.is_admin or tool_name not in _ADMIN_TOOLS


# ── Registration ───────────────────────────────────────────────────────────────


def register_mcp_server(hass: HomeAssistant) -> None:
    """Register the Selora AI MCP HTTP views with HA's HTTP server.

    Each view is registered independently so a reload/upgrade that already
    has one view registered doesn't prevent the other from being added.

    CORS: each view has an options() handler for preflight, and
    on_response_prepare adds CORS headers to all responses on MCP paths.
    """
    app: web.Application = hass.http.app

    # Views under /api/ — registered via HA's standard mechanism.
    for view in (
        SeloraAIMCPView(),
        OAuthTokenProxyView(),
    ):
        try:
            hass.http.register_view(view)
        except ValueError:
            _LOGGER.debug("View %s already registered, skipping", view.name)

    # RFC 9728 protected resource metadata at the domain root — HA doesn't
    # allow HomeAssistantView outside /api/, so register as raw aiohttp route.
    # Use a closure to capture `hass` since raw routes don't have KEY_HASS on
    # request.app (HA only sets that for its own view system).
    async def _protected_resource_get(request: web.Request) -> web.Response:
        base_url = _get_external_base_url(hass, request)
        connect_url = hass.data.get(DOMAIN, {}).get("selora_connect_url", SELORA_JWT_ISSUER)
        return _add_cors(
            request,
            web.json_response(
                {
                    "resource": f"{base_url}{_MCP_URL}",
                    "authorization_servers": [connect_url],
                    "bearer_methods_supported": ["header"],
                    "scopes_supported": [],
                }
            ),
        )

    for method, handler in [("GET", _protected_resource_get), ("OPTIONS", _cors_preflight)]:
        with contextlib.suppress(Exception):
            app.router.add_route(method, _PROTECTED_RESOURCE_URL, handler)

    _LOGGER.info("Selora AI MCP server registered at %s", _MCP_URL)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_external_base_url(hass: HomeAssistant, request: web.Request) -> str:
    """Return the external base URL, respecting reverse-proxy path prefixes.

    Prefers the request's X-Forwarded headers (set by reverse proxies like
    Cloudflare/ngrok) so that URLs work for the actual caller. Falls back
    to HA's configured external URL, then internal URL.
    """
    # If behind a reverse proxy, trust the forwarded headers
    fwd_host = request.headers.get("X-Forwarded-Host")
    if fwd_host:
        scheme = request.headers.get("X-Forwarded-Proto", "https")
        prefix = request.headers.get("X-Forwarded-Prefix", "").rstrip("/")
        return f"{scheme}://{fwd_host}{prefix}"

    # Otherwise use HA's configured URL
    try:
        from homeassistant.helpers.network import get_url

        return get_url(hass, allow_internal=True).rstrip("/")
    except Exception:
        return f"{request.scheme}://{request.host}"


class OAuthTokenProxyView(HomeAssistantView):
    """Proxy token requests to Selora Connect.

    POST /api/selora_ai/oauth/token

    Some clients (mcp-remote) re-fetch the root
    /.well-known/oauth-authorization-server for the token exchange step,
    which returns HA's built-in /auth/token. To work around this, the
    AS metadata points token_endpoint here and we forward to Connect.
    """

    name = "selora_ai:oauth_token_proxy"
    url = _OAUTH_TOKEN_PROXY_URL
    requires_auth = False

    async def options(self, request: web.Request) -> web.Response:
        """CORS preflight."""
        return await _cors_preflight(request)

    async def post(self, request: web.Request) -> web.Response:
        """Forward the token request to Connect."""
        hass: HomeAssistant = request.app[KEY_HASS]
        connect_url = hass.data.get(DOMAIN, {}).get("selora_connect_url", SELORA_JWT_ISSUER)
        body = await request.read()
        headers = {
            "Content-Type": request.content_type,
        }
        try:
            from homeassistant.helpers.aiohttp_client import async_get_clientsession

            session = async_get_clientsession(hass)
            async with session.post(
                f"{connect_url}/oauth/token",
                data=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp_body = await resp.read()
                return _add_cors(
                    request,
                    web.Response(
                        status=resp.status,
                        body=resp_body,
                        content_type=resp.content_type,
                    ),
                )
        except (aiohttp.ClientError, TimeoutError):
            _LOGGER.exception("Failed to proxy token request to Connect")
            return _add_cors(
                request,
                web.json_response(
                    {"error": "server_error", "error_description": "Connect unreachable"},
                    status=HTTPStatus.BAD_GATEWAY,
                ),
            )


# ── HTTP view ──────────────────────────────────────────────────────────────────


class SeloraAIMCPView(HomeAssistantView):
    """Selora AI MCP endpoint — Streamable HTTP, stateless mode."""

    name = "selora_ai:mcp"
    url = _MCP_URL
    requires_auth = False  # Dual-auth: handled manually in post()

    async def options(self, request: web.Request) -> web.Response:
        """CORS preflight."""
        return await _cors_preflight(request)

    def _build_unauthorized_response(
        self, hass: HomeAssistant, request: web.Request
    ) -> web.Response:
        """Return a 401 with OAuth metadata if Connect is linked."""
        jwt_validator = hass.data.get(DOMAIN, {}).get("selora_jwt_validator")
        www_auth = 'Bearer realm="selora-ai"'
        if jwt_validator is not None:
            base_url = _get_external_base_url(hass, request)
            resource_meta = f"{base_url}{_PROTECTED_RESOURCE_URL}"
            www_auth += f', resource_metadata="{resource_meta}"'
        return _add_cors(
            request,
            web.Response(
                status=HTTPStatus.UNAUTHORIZED,
                text="Authentication required",
                headers={"WWW-Authenticate": www_auth},
            ),
        )

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET — used by MCP clients to probe auth requirements."""
        client_ip = _get_client_ip(request)
        if not _request_limiter.is_allowed(client_ip):
            return web.Response(
                status=HTTPStatus.TOO_MANY_REQUESTS,
                text="Rate limit exceeded",
                headers={"Retry-After": str(_RATE_LIMIT_WINDOW)},
            )
        hass: HomeAssistant = request.app[KEY_HASS]
        domain_data = hass.data.get(DOMAIN, {})
        jwt_validator = domain_data.get("selora_jwt_validator")
        mcp_token_store = domain_data.get("mcp_token_store")
        try:
            await authenticate_request(hass, request, jwt_validator, mcp_token_store)
        except AuthenticationError:
            return self._build_unauthorized_response(hass, request)
        return _add_cors(request, web.Response(status=HTTPStatus.OK, text="Selora AI MCP endpoint"))

    async def post(self, request: web.Request) -> web.Response:
        """Handle a single MCP JSON-RPC request."""
        response = await self._handle_post(request)
        return _add_cors(request, response)

    async def _handle_post(self, request: web.Request) -> web.Response:
        """Internal post handler — CORS headers added by the wrapper."""
        hass: HomeAssistant = request.app[KEY_HASS]
        client_ip = _get_client_ip(request)

        # ── Rate limiting ──
        if not _request_limiter.is_allowed(client_ip):
            return web.Response(
                status=HTTPStatus.TOO_MANY_REQUESTS,
                text="Rate limit exceeded",
                headers={"Retry-After": str(_RATE_LIMIT_WINDOW)},
            )

        # ── Authentication (HA token, Selora MCP token, or Selora Connect JWT) ──
        domain_data = hass.data.get(DOMAIN, {})
        jwt_validator = domain_data.get("selora_jwt_validator")
        mcp_token_store = domain_data.get("mcp_token_store")
        try:
            auth_ctx = await authenticate_request(hass, request, jwt_validator, mcp_token_store)
        except AuthenticationError:
            if not _auth_fail_limiter.is_allowed(client_ip):
                return web.Response(
                    status=HTTPStatus.TOO_MANY_REQUESTS,
                    text="Too many authentication failures",
                    headers={"Retry-After": str(_RATE_LIMIT_WINDOW)},
                )
            return self._build_unauthorized_response(hass, request)

        # Content-type negotiation
        if _CONTENT_TYPE_JSON not in request.headers.get("accept", ""):
            return web.Response(
                status=HTTPStatus.BAD_REQUEST,
                text=f"Client must accept {_CONTENT_TYPE_JSON}",
            )
        if request.content_type != _CONTENT_TYPE_JSON:
            return web.Response(
                status=HTTPStatus.BAD_REQUEST,
                text=f"Content-Type must be {_CONTENT_TYPE_JSON}",
            )

        # Parse JSON-RPC message
        try:
            json_data = await request.json()
        except Exception:
            return web.Response(status=HTTPStatus.BAD_REQUEST, text="Invalid JSON")

        method = json_data.get("method")
        req_id = json_data.get("id")
        params = json_data.get("params")

        if json_data.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return web.Response(
                status=HTTPStatus.BAD_REQUEST,
                text="Request must be a valid JSON-RPC 2.0 message",
            )

        # Notifications (no id) get 202 Accepted
        if req_id is None:
            _LOGGER.debug("MCP notification received (%s), returning 202", method)
            return web.Response(status=HTTPStatus.ACCEPTED)

        # Dispatch
        try:
            async with asyncio.timeout(_TIMEOUT_SECS):
                result = await _jsonrpc_dispatch(hass, method, params, auth_ctx)
        except TimeoutError:
            _LOGGER.warning("MCP request timed out after %ss", _TIMEOUT_SECS)
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": "Request timed out"},
                },
                status=HTTPStatus.GATEWAY_TIMEOUT,
            )
        except ValueError as exc:
            return web.json_response(
                {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": str(exc)}},
            )
        except Exception:
            _LOGGER.exception("MCP request failed")
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": "Internal error"},
                },
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

        return web.json_response({"jsonrpc": "2.0", "id": req_id, "result": result})


# ── JSON-RPC dispatch (stateless, no mcp dependency) ────────────────────────


_MCP_PROTOCOL_VERSION = "2025-03-26"
_MCP_SERVER_NAME = "selora-ai"
_MCP_SERVER_VERSION = "0.3.2"


async def _jsonrpc_dispatch(
    hass: HomeAssistant,
    method: str,
    params: dict[str, Any] | None,
    auth_ctx: SeloraAuthContext,
) -> dict[str, Any]:
    """Dispatch a JSON-RPC method and return the result payload."""
    if method == "initialize":
        return {
            "protocolVersion": _MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": _MCP_SERVER_NAME, "version": _MCP_SERVER_VERSION},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {
            "tools": [
                {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
                for t in _TOOL_DEFINITIONS
                if _can_access_tool(auth_ctx, t.name)
            ]
        }
    if method == "tools/call":
        tool_name = (params or {}).get("name", "")
        arguments = (params or {}).get("arguments", {})
        content = await _dispatch(hass, tool_name, arguments, auth_ctx=auth_ctx)
        return {"content": [{"type": c.type, "text": c.text} for c in content]}
    raise ValueError(f"Unknown method: {method}")


# ── Tool dispatch ──────────────────────────────────────────────────────────────


# Tools that take only (hass) — no arguments parameter
_NO_ARGS_TOOLS = frozenset({TOOL_GET_HOME_SNAPSHOT, TOOL_LIST_SESSIONS, TOOL_TRIGGER_SCAN})


def _get_tool_handlers() -> dict[str, Any]:
    """Return the tool handler registry (lazy to avoid forward-reference issues)."""
    return {
        TOOL_LIST_AUTOMATIONS: _tool_list_automations,
        TOOL_GET_AUTOMATION: _tool_get_automation,
        TOOL_VALIDATE_AUTOMATION: _tool_validate_automation,
        TOOL_CREATE_AUTOMATION: _tool_create_automation,
        TOOL_ACCEPT_AUTOMATION: _tool_accept_automation,
        TOOL_DELETE_AUTOMATION: _tool_delete_automation,
        TOOL_GET_HOME_SNAPSHOT: _tool_get_home_snapshot,
        TOOL_CHAT: _tool_chat,
        TOOL_LIST_SESSIONS: _tool_list_sessions,
        TOOL_LIST_PATTERNS: _tool_list_patterns,
        TOOL_GET_PATTERN: _tool_get_pattern,
        TOOL_LIST_SUGGESTIONS: _tool_list_suggestions,
        TOOL_ACCEPT_SUGGESTION: _tool_accept_suggestion,
        TOOL_DISMISS_SUGGESTION: _tool_dismiss_suggestion,
        TOOL_TRIGGER_SCAN: _tool_trigger_scan,
        TOOL_LIST_DEVICES: _tool_list_devices,
        TOOL_GET_DEVICE: _tool_get_device,
        TOOL_HOME_ANALYTICS: _tool_home_analytics,
    }


async def _dispatch(
    hass: HomeAssistant,
    name: str,
    arguments: dict[str, Any],
    *,
    auth_ctx: SeloraAuthContext,
) -> list[MCPTextContent]:
    """Route a tool call to its handler and return MCP TextContent."""
    result: dict[str, Any] | list[dict[str, Any]]
    try:
        _check_tool_access(auth_ctx, name)

        handler = _get_tool_handlers().get(name)
        if handler is None:
            result = {"error": f"Unknown tool: {name}"}
        elif name in _NO_ARGS_TOOLS:
            result = await handler(hass)
        else:
            result = await handler(hass, arguments)
    except Unauthorized as exc:
        result = {"error": str(exc)}
    except Exception:
        _LOGGER.exception("Tool %s raised an exception", name)
        result = {"error": "Tool execution failed"}

    return [MCPTextContent(type="text", text=json.dumps(result, ensure_ascii=False))]


# ── Helpers ────────────────────────────────────────────────────────────────────


def _sanitize(value: Any, limit: int = 200) -> str:
    """Normalize and truncate untrusted string fields before including in responses."""
    from .helpers import sanitize_untrusted_text

    return sanitize_untrusted_text(value, limit=limit)


def _get_automation_store(hass: HomeAssistant) -> AutomationStore:
    """Return (or lazily create) the AutomationStore singleton."""
    from .helpers import get_automation_store

    return get_automation_store(hass)


def _get_conv_store(hass: HomeAssistant) -> ConversationStore:
    """Return (or lazily create) the ConversationStore singleton."""
    from . import ConversationStore

    domain_data = hass.data.setdefault(DOMAIN, {})
    return domain_data.setdefault("_conv_store", ConversationStore(hass))


def _get_llm(hass: HomeAssistant) -> LLMClient | None:
    """Return the LLMClient from the first active LLM config entry, or None."""
    domain_data = hass.data.get(DOMAIN, {})
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        entry_data = domain_data.get(entry.entry_id, {})
        llm = entry_data.get("llm")
        if llm is not None:
            return llm
    return None


async def _read_yaml_automations(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Read automations.yaml in an executor thread."""
    from .automation_utils import _read_automations_yaml

    path = Path(hass.config.config_dir) / "automations.yaml"
    return await hass.async_add_executor_job(_read_automations_yaml, path)


def _is_selora(automation: dict[str, Any]) -> bool:
    """Return True if this automation was created by Selora AI."""
    from .helpers import is_selora_automation

    return is_selora_automation(automation)


def _is_pending_automation(auto: dict[str, Any], record: dict[str, Any] | None) -> bool:
    """Return True if a Selora automation should be shown as pending."""
    if auto.get("initial_state", True) is not False:
        return False
    if not record:
        return True
    versions = record.get("versions", [])
    if not versions:
        return True
    latest_message = str(versions[-1].get("message", "")).strip().lower()
    return latest_message != "accepted via mcp"


# ── Tool: selora_list_automations ─────────────────────────────────────────────


async def _tool_list_automations(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return Selora-managed automations with status and risk metadata."""
    from .automation_utils import assess_automation_risk

    status_filter: str | None = arguments.get("status")
    yaml_automations: list[dict[str, Any]] = await _read_yaml_automations(hass)
    store: AutomationStore = _get_automation_store(hass)

    # Build a live state lookup (enabled/disabled) from HA state machine
    live_states: dict[str, str] = {}
    for state in hass.states.async_all("automation"):
        friendly = state.attributes.get("friendly_name", "")
        # Map by alias — will correlate below
        live_states[friendly] = state.state  # "on" or "off"

    result: list[dict[str, Any]] = []
    for auto in yaml_automations:
        if not _is_selora(auto):
            continue

        automation_id = str(auto.get("id", ""))
        alias = _sanitize(auto.get("alias", ""))

        meta: AutomationMetadata | None = await store.get_metadata(automation_id)
        record: AutomationRecord | None = await store.get_record(automation_id)

        # Determine display status
        if _is_pending_automation(auto, record):
            status = "pending"
        else:
            # Cross-reference live HA state
            live = live_states.get(alias, "")
            status = "enabled" if live == "on" else "disabled"

        if status_filter and status != status_filter:
            continue

        risk: RiskAssessment = assess_automation_risk(auto)

        result.append(
            {
                "automation_id": automation_id,
                "alias": alias,
                "status": status,
                "version_count": meta["version_count"] if meta else 1,
                "current_version_id": meta["current_version_id"] if meta else None,
                "risk_assessment": _sanitize_risk(risk),
            }
        )

    return result


# ── Tool: selora_get_automation ───────────────────────────────────────────────


async def _tool_get_automation(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return full detail for a single automation including YAML and version history."""
    import yaml as _yaml

    from .automation_utils import assess_automation_risk

    automation_id: str = str(arguments.get("automation_id", ""))
    if not automation_id:
        return {"error": "automation_id is required"}

    yaml_automations: list[dict[str, Any]] = await _read_yaml_automations(hass)
    auto: dict[str, Any] | None = next(
        (a for a in yaml_automations if str(a.get("id")) == automation_id), None
    )
    if auto is None:
        return {"error": f"Automation {automation_id} not found"}

    store: AutomationStore = _get_automation_store(hass)
    record: AutomationRecord | None = await store.get_record(automation_id)
    versions: list[dict[str, Any]] = []
    lineage: list[dict[str, Any]] = []
    if record:
        for v in record.get("versions", []):
            versions.append(
                {
                    "version_id": v["version_id"],
                    "created_at": v.get("created_at", ""),
                    "message": _sanitize(v.get("message", "")),
                    "session_id": v.get("session_id"),
                }
            )
        lineage = [
            {
                "version_id": le.get("version_id"),
                "session_id": le.get("session_id"),
                "action": le.get("action"),
                "timestamp": le.get("timestamp"),
            }
            for le in record.get("lineage", [])
        ]

    yaml_text: str = _yaml.dump(auto, allow_unicode=True, default_flow_style=False)
    risk: RiskAssessment = assess_automation_risk(auto)

    return {
        "automation_id": automation_id,
        "alias": _sanitize(auto.get("alias", "")),
        "yaml": yaml_text,
        "status": "pending"
        if _is_pending_automation(auto, record)
        else ("disabled" if not auto.get("initial_state", True) else "enabled"),
        "version_count": len(versions),
        "versions": versions,
        "lineage": lineage,
        "risk_assessment": _sanitize_risk(risk),
    }


# ── Tool: selora_validate_automation ──────────────────────────────────────────


async def _tool_validate_automation(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Validate + risk-assess YAML without creating anything. Pure read."""
    import yaml as _yaml

    from .automation_utils import assess_automation_risk, validate_automation_payload

    yaml_text: str = str(arguments.get("yaml", ""))
    if not yaml_text.strip():
        return {
            "valid": False,
            "errors": ["yaml field is required"],
            "normalized_yaml": None,
            "risk_assessment": None,
        }

    # Parse
    try:
        parsed: Any = await hass.async_add_executor_job(lambda: _yaml.safe_load(yaml_text))
    except _yaml.YAMLError as exc:
        return {
            "valid": False,
            "errors": [f"YAML parse error: {exc}"],
            "normalized_yaml": None,
            "risk_assessment": None,
        }

    if not isinstance(parsed, dict):
        return {
            "valid": False,
            "errors": ["YAML must be a mapping"],
            "normalized_yaml": None,
            "risk_assessment": None,
        }

    # Validate
    is_valid: bool
    reason: str
    normalized: AutomationDict | None
    is_valid, reason, normalized = validate_automation_payload(parsed, hass)
    if not is_valid or normalized is None:
        return {
            "valid": False,
            "errors": [reason] if reason else ["Validation failed"],
            "normalized_yaml": None,
            "risk_assessment": None,
        }

    normalized_yaml: str = _yaml.dump(normalized, allow_unicode=True, default_flow_style=False)
    risk: RiskAssessment = assess_automation_risk(normalized)

    return {
        "valid": True,
        "errors": [],
        "normalized_yaml": normalized_yaml,
        "risk_assessment": _sanitize_risk(risk),
    }


# ── Tool: selora_create_automation ────────────────────────────────────────────


async def _tool_create_automation(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create an automation from externally-provided YAML.

    Server-side validation and risk assessment run unconditionally.
    Automations are created disabled by default.
    """
    import yaml as _yaml

    from .automation_utils import (
        assess_automation_risk,
        async_create_automation,
        validate_automation_payload,
    )

    yaml_text: str = str(arguments.get("yaml", ""))
    enabled: bool = bool(arguments.get("enabled", False))
    version_message: str = _sanitize(arguments.get("version_message", "Created via MCP"))

    if not yaml_text.strip():
        return {"error": "yaml field is required"}

    try:
        parsed: Any = await hass.async_add_executor_job(lambda: _yaml.safe_load(yaml_text))
    except _yaml.YAMLError as exc:
        return {"error": f"YAML parse error: {exc}"}

    if not isinstance(parsed, dict):
        return {"error": "YAML must be a mapping"}

    is_valid: bool
    reason: str
    normalized: AutomationDict | None
    is_valid, reason, normalized = validate_automation_payload(parsed, hass)
    if not is_valid or normalized is None:
        return {"error": f"Invalid automation: {reason}"}

    risk: RiskAssessment = assess_automation_risk(normalized)

    # Enforce disabled-by-default
    normalized["initial_state"] = enabled

    success: bool = await async_create_automation(hass, normalized, version_message=version_message)
    if not success:
        return {"error": "Failed to write automation to automations.yaml"}

    # Retrieve the automation_id that was assigned during creation
    yaml_automations: list[dict[str, Any]] = await _read_yaml_automations(hass)
    alias: str = normalized.get("alias", "")
    created: dict[str, Any] | None = next(
        (a for a in yaml_automations if a.get("alias") == alias and _is_selora(a)),
        None,
    )
    automation_id: str = str(created.get("id", "")) if created else ""

    return {
        "automation_id": automation_id,
        "status": "created",
        "risk_assessment": _sanitize_risk(risk),
    }


# ── Tool: selora_accept_automation ────────────────────────────────────────────


async def _tool_accept_automation(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Enable/commit a pending Selora automation."""
    from .automation_utils import async_update_automation

    automation_id: str = str(arguments.get("automation_id", ""))
    enabled: bool = bool(arguments.get("enabled", False))

    if not automation_id:
        return {"error": "automation_id is required"}

    yaml_automations: list[dict[str, Any]] = await _read_yaml_automations(hass)
    auto: dict[str, Any] | None = next(
        (a for a in yaml_automations if str(a.get("id")) == automation_id), None
    )
    if auto is None or not _is_selora(auto):
        return {"error": f"Selora automation {automation_id} not found"}

    updated: dict[str, Any] = dict(auto)
    updated["initial_state"] = enabled

    success: bool = await async_update_automation(
        hass,
        automation_id,
        updated,
        version_message="Accepted via MCP",
    )
    if not success:
        return {"error": "Failed to update automation"}

    return {"automation_id": automation_id, "status": "enabled" if enabled else "disabled"}


# ── Tool: selora_delete_automation ────────────────────────────────────────────


async def _tool_delete_automation(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a Selora-managed automation."""
    from .automation_utils import async_delete_automation

    automation_id: str = str(arguments.get("automation_id", ""))
    if not automation_id:
        return {"error": "automation_id is required"}

    yaml_automations: list[dict[str, Any]] = await _read_yaml_automations(hass)
    auto: dict[str, Any] | None = next(
        (a for a in yaml_automations if str(a.get("id")) == automation_id), None
    )
    if auto is None or not _is_selora(auto):
        return {"error": f"Selora automation {automation_id} not found"}

    success: bool = await async_delete_automation(hass, automation_id)
    if not success:
        return {"error": "Failed to delete automation"}

    return {"automation_id": automation_id, "status": "deleted"}


# ── Tool: selora_get_home_snapshot ────────────────────────────────────────────


def _format_state_value(value: str) -> str:
    """Format entity state values for human-readable display.

    Converts ISO 8601 timestamps to 12-hour HH:MM AM/PM format.
    Other values are sanitized normally.
    """
    from .helpers import format_entity_state

    raw = value.strip()
    result = format_entity_state(value)
    # format_entity_state returns the stripped input unchanged for
    # non-timestamps.  In that case, apply MCP sanitization to enforce
    # the 64-char limit on user-controlled state strings.
    if result == raw:
        return _sanitize(raw, limit=64)
    return result


async def _tool_get_home_snapshot(hass: HomeAssistant) -> dict[str, Any]:
    """Return current entity states grouped by HA area."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import entity_registry as er

    area_reg = ar.async_get(hass)
    entity_reg = er.async_get(hass)

    # Build area_id → area_name map
    area_names: dict[str, str] = {
        area.id: _sanitize(area.name) for area in area_reg.async_list_areas()
    }

    areas: dict[str, list[dict[str, Any]]] = {name: [] for name in area_names.values()}
    unassigned: list[dict[str, Any]] = []

    _ALLOWED_DOMAINS: set[str] = COLLECTOR_DOMAINS | {"automation"}

    for state in hass.states.async_all():
        domain = state.entity_id.split(".")[0]
        if domain not in _ALLOWED_DOMAINS:
            continue
        if domain == "light" and any(
            pat in state.entity_id for pat in LIGHT_ENTITY_EXCLUDE_PATTERNS
        ):
            continue

        entry = entity_reg.async_get(state.entity_id)
        area_id = entry.area_id if entry else None

        entity_entry = {
            "entity_id": state.entity_id,
            "domain": domain,
            "state": _format_state_value(state.state),
            "friendly_name": _sanitize(state.attributes.get("friendly_name", state.entity_id)),
        }

        if area_id and area_id in area_names:
            areas[area_names[area_id]].append(entity_entry)
        else:
            unassigned.append(entity_entry)

    # Drop empty areas
    areas = {k: v for k, v in areas.items() if v}

    return {"areas": areas, "unassigned": unassigned}


# ── Tool: selora_list_devices ──────────────────────────────────────────────────


async def _tool_list_devices(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List HA devices with optional area and domain filters."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)

    area_filter: str = (arguments.get("area") or "").strip().lower()
    domain_filter: str = (arguments.get("domain") or "").strip().lower()

    # Build area_id → area_name map
    area_names: dict[str, str] = {area.id: area.name for area in area_reg.async_list_areas()}

    # Build config_entry_id → domain map for O(1) integration lookup
    entry_domains: dict[str, str] = {
        ce.entry_id: ce.domain for ce in hass.config_entries.async_entries()
    }

    devices: list[dict[str, Any]] = []
    for device in dev_reg.devices.values():
        # Resolve area name
        area_name = area_names.get(device.area_id or "") or ""

        # Apply area filter (case-insensitive substring match)
        if area_filter and area_filter not in area_name.lower():
            continue

        # Collect entities for this device in collector domains
        entities: list[dict[str, str]] = []
        device_domains: set[str] = set()
        for entity in er.async_entries_for_device(ent_reg, device.id):
            domain = entity.entity_id.split(".")[0]
            if domain in COLLECTOR_DOMAINS:
                state_obj = hass.states.get(entity.entity_id)
                entities.append(
                    {
                        "entity_id": entity.entity_id,
                        "state": _format_state_value(state_obj.state) if state_obj else "unknown",
                    }
                )
                device_domains.add(domain)

        # Skip devices with no entities in collector domains
        if not entities:
            continue

        # Apply domain filter
        if domain_filter and domain_filter not in device_domains:
            continue

        integration = _sanitize(entry_domains.get(device.primary_config_entry or "", ""))

        devices.append(
            {
                "device_id": device.id,
                "name": _sanitize(device.name or device.name_by_user or "Unknown"),
                "area": _sanitize(area_name) if area_name else None,
                "manufacturer": _sanitize(device.manufacturer or ""),
                "model": _sanitize(device.model or ""),
                "integration": integration,
                "domains": sorted(device_domains),
                "entities": entities,
            }
        )

    return {"devices": devices, "count": len(devices)}


# ── Tool: selora_get_device ────────────────────────────────────────────────────


async def _tool_get_device(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return full detail for a single device with entity states."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    device_id = str(arguments.get("device_id", "")).strip()
    if not device_id:
        return {"error": "device_id is required"}

    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if device is None:
        return {"error": f"Device {_sanitize(device_id)} not found"}

    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)

    # Resolve area
    area_name = ""
    if device.area_id:
        area = area_reg.async_get_area(device.area_id)
        area_name = area.name if area else ""

    # Resolve integration domain
    integration = ""
    if device.primary_config_entry:
        ce = hass.config_entries.async_get_entry(device.primary_config_entry)
        if ce is not None:
            integration = ce.domain

    # Collect entities with current states
    entities: list[dict[str, Any]] = []
    for entity in er.async_entries_for_device(ent_reg, device.id):
        domain = entity.entity_id.split(".")[0]
        if domain not in COLLECTOR_DOMAINS:
            continue

        state = hass.states.get(entity.entity_id)
        entity_entry: dict[str, Any] = {
            "entity_id": entity.entity_id,
            "domain": domain,
            "name": _sanitize(entity.name or entity.original_name or entity.entity_id),
            "state": _format_state_value(state.state) if state else "unavailable",
        }

        # Include key attributes based on domain
        if state and state.attributes:
            attrs = state.attributes
            filtered: dict[str, Any] = {}
            if "friendly_name" in attrs:
                filtered["friendly_name"] = _sanitize(attrs["friendly_name"])
            if "device_class" in attrs:
                filtered["device_class"] = str(attrs["device_class"])
            if "unit_of_measurement" in attrs:
                filtered["unit_of_measurement"] = str(attrs["unit_of_measurement"])
            # Domain-specific attributes
            if domain == "climate":
                for key in ("temperature", "current_temperature", "hvac_action"):
                    if key in attrs:
                        filtered[key] = attrs[key]
            elif domain == "light":
                for key in ("brightness", "color_temp", "color_mode"):
                    if key in attrs:
                        filtered[key] = attrs[key]
            elif domain == "cover":
                for key in ("current_position",):
                    if key in attrs:
                        filtered[key] = attrs[key]
            elif domain == "fan":
                for key in ("percentage", "preset_mode"):
                    if key in attrs:
                        filtered[key] = attrs[key]
            if filtered:
                entity_entry["attributes"] = filtered

        entities.append(entity_entry)

    return {
        "device_id": device.id,
        "name": _sanitize(device.name or device.name_by_user or "Unknown"),
        "area": _sanitize(area_name) if area_name else None,
        "manufacturer": _sanitize(device.manufacturer or ""),
        "model": _sanitize(device.model or ""),
        "sw_version": _sanitize(device.sw_version or ""),
        "hw_version": _sanitize(device.hw_version or ""),
        "integration": _sanitize(integration),
        "via_device_id": device.via_device_id,
        "entities": entities,
    }


# ── Tool: selora_chat ─────────────────────────────────────────────────────────


async def _tool_chat(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Send a message to Selora's LLM and return the response.

    This is the primary suspension point for the external agent in the
    Coroutine Synthesis pattern: the external agent yields here and Selora's
    LLM advances the automation artifact using home-grounded generation.
    """

    message: str = str(arguments.get("message", "")).strip()
    if not message:
        return {"error": "message is required"}

    session_id: str | None = arguments.get("session_id")
    refine_automation_id: str | None = arguments.get("refine_automation_id")

    llm: LLMClient | None = _get_llm(hass)
    if llm is None:
        return {"error": "Selora AI LLM is not configured"}

    conv_store: ConversationStore = _get_conv_store(hass)

    # Get or create session
    session: dict[str, Any]
    if session_id:
        session_or_none: dict[str, Any] | None = await conv_store.get_session(session_id)
        if session_or_none is None:
            return {"error": f"Session {session_id} not found"}
        session = session_or_none
    else:
        session = await conv_store.create_session()
        session_id = session["id"]

    # Reconcile scene store so session context reflects external edits
    from .helpers import get_scene_store  # noqa: PLC0415

    await get_scene_store(hass).async_reconcile_yaml()

    # Build history for the LLM.
    # For each unique scene_id, keep the latest YAML so multi-scene
    # sessions retain context for every scene (including renames).
    messages = session.get("messages", [])
    latest_scene_by_id: dict[str, int] = {}
    for i, m in enumerate(messages):
        if m.get("scene_yaml") and m.get("scene_id"):
            latest_scene_by_id[m["scene_id"]] = i
    latest_scene_indices: set[int] = set(latest_scene_by_id.values())

    history: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
        role = m.get("role", "user")
        content = str(m.get("content", ""))
        # Re-attach pending automation YAML as context (sanitized)
        if m.get("automation_yaml") and m.get("automation_status") in ("pending", "refining"):
            alias = _sanitize((m.get("automation") or {}).get("alias", ""))
            header = f"[Untrusted automation reference data for context only: {alias}]\n"
            quoted_yaml = json.dumps(str(m["automation_yaml"]), ensure_ascii=True)
            content = f"{header}{quoted_yaml}\n{content}"
        # Re-attach latest scene YAML for each unique scene name
        elif i in latest_scene_indices:
            scene_name = _sanitize((m.get("scene") or {}).get("name", ""))
            sid = m.get("scene_id", "")
            header = f"[Untrusted scene reference data for context only: {scene_name} (scene_id: {sid})]\n"
            quoted_yaml = json.dumps(str(m["scene_yaml"]), ensure_ascii=True)
            content = f"{header}{quoted_yaml}\n{content}"
        history.append({"role": role, "content": content})

    # Collect existing automation aliases for dedup
    existing_aliases: list[str] = [
        str(s.attributes.get("friendly_name", "")) for s in hass.states.async_all("automation")
    ]

    # Build scene context from the session-level index (survives message
    # pruning) so the LLM always has scene_id + YAML on the current turn.
    scene_index: dict[str, dict[str, str]] = session.get("scenes", {})
    mcp_scene_context: list[tuple[str, str, str]] | None = None
    if scene_index:
        mcp_scene_context = [
            (sid, _sanitize(data.get("name", "")), data.get("yaml", ""))
            for sid, data in scene_index.items()
            if data.get("yaml")
        ] or None

    # Call Selora's LLM
    llm_result: ArchitectResponse = await llm.architect_chat(
        message=message,
        history=history,
        existing_automations=existing_aliases,
        refining_automation_id=refine_automation_id,
        scene_context=mcp_scene_context,
    )

    intent: str = llm_result.get("intent", "answer")
    response_text: str = _sanitize(llm_result.get("response", ""), limit=2000)
    automation: AutomationDict | None = llm_result.get("automation")
    automation_yaml: str | None = llm_result.get("automation_yaml")
    risk_assessment: RiskAssessment | None = llm_result.get("risk_assessment")

    # The LLM includes refine_scene_id when modifying an existing scene.
    # Collect scene IDs known to this session so the validator can reject
    # hallucinated IDs that don't belong to the conversation.  Include
    # the session-level index (survives pruning) and message-level IDs.
    mcp_session_scene_ids: set[str] = set(scene_index.keys()) | {
        m["scene_id"] for m in messages if m.get("scene_id")
    }
    scene_result: dict[str, Any] | None = None
    if intent == "scene" and llm_result.get("scene"):
        try:
            from .scene_utils import async_create_scene  # noqa: PLC0415

            scene_result = await async_create_scene(
                hass,
                llm_result["scene"],
                existing_scene_id=llm_result.get("refine_scene_id"),
                session_scene_ids=mcp_session_scene_ids,
            )
        except Exception as exc:  # noqa: BLE001 — HA service handlers may raise beyond HA's hierarchy
            _LOGGER.error("Failed to create scene via MCP: %s", exc)
            response_text += f" (Scene creation failed: {exc})"
            # Clear scene metadata so the caller doesn't think a scene was created
            llm_result.pop("scene", None)
            llm_result.pop("scene_yaml", None)
            intent = "answer"

        if scene_result is not None:
            try:
                from .helpers import get_scene_store  # noqa: PLC0415

                scene_store = get_scene_store(hass)
                await scene_store.async_add_scene(
                    scene_result["scene_id"],
                    scene_result["name"],
                    scene_result["entity_count"],
                    session_id=session_id,
                    entity_id=scene_result.get("entity_id"),
                    content_hash=scene_result.get("content_hash"),
                )
            except Exception:  # noqa: BLE001 — store failure doesn't invalidate the created scene
                _LOGGER.warning("Failed to record scene %s in store", scene_result["scene_id"])

    # Persist messages
    await conv_store.append_message(session_id, "user", message)
    await conv_store.append_message(
        session_id,
        "assistant",
        response_text,
        automation=automation,
        automation_yaml=automation_yaml,
        automation_status="pending" if automation else None,
        risk_assessment=risk_assessment,
        scene=llm_result.get("scene"),
        scene_yaml=llm_result.get("scene_yaml"),
        scene_id=scene_result["scene_id"] if scene_result else None,
    )

    # Generate session title if this is the first exchange
    if len(session.get("messages", [])) == 0 and llm:
        try:
            title = await llm.generate_session_title(message, response_text)
            if title:
                await conv_store.update_session_title(session_id, _sanitize(title))
        except Exception as exc:
            _LOGGER.debug("Session title generation failed for %s: %s", session_id, exc)

    # If automation was generated, retrieve its id from the pending message
    automation_id: str | None = None
    if automation and automation_yaml:
        # The automation is stored as pending in the conversation store.
        # The caller uses selora_accept_automation to commit it, passing the
        # automation data payload directly. Return the normalized payload so
        # the caller can act on it.
        automation_id = automation.get("id") if isinstance(automation, dict) else None

    response: dict[str, Any] = {
        "response": response_text,
        "intent": intent,
        "session_id": session_id,
    }
    if automation_yaml:
        response["automation_yaml"] = automation_yaml
    if automation_id:
        response["automation_id"] = automation_id
    if risk_assessment:
        response["risk_assessment"] = _sanitize_risk(risk_assessment)
    if scene_result:
        response["scene_id"] = scene_result["scene_id"]
        response["scene_name"] = scene_result["name"]

    return response


# ── Tool: selora_list_sessions ────────────────────────────────────────────────


async def _tool_list_sessions(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Return recent conversation sessions (title + id, no messages)."""
    conv_store: ConversationStore = _get_conv_store(hass)
    sessions: list[dict[str, Any]] = await conv_store.list_sessions()
    return [
        {
            "session_id": s["id"],
            "title": _sanitize(s.get("title", "Untitled")),
            "updated_at": s.get("updated_at", ""),
            "message_count": s.get("message_count", 0),
        }
        for s in sessions
    ]


def _find_collector(hass: HomeAssistant) -> DataCollector | None:
    domain_data = hass.data.get(DOMAIN, {})
    for key, value in domain_data.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and "collector" in value:
            return value["collector"]
    return None


def _get_suggestion_status_store(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    domain_data = hass.data.setdefault(DOMAIN, {})
    return domain_data.setdefault("_mcp_suggestion_status", {})


def _collect_entity_ids(value: Any) -> list[str]:
    from .helpers import collect_entity_ids

    return sorted(collect_entity_ids(value))


def _suggestion_identity(raw: dict[str, Any], index: int) -> tuple[str, str]:
    automation_yaml: str = str(raw.get("automation_yaml", ""))
    alias: str = str(raw.get("alias", ""))
    digest_source: str = automation_yaml or json.dumps(
        {
            "alias": alias,
            "trigger": raw.get("trigger"),
            "triggers": raw.get("triggers"),
            "action": raw.get("action"),
            "actions": raw.get("actions"),
            "index": index,
        },
        sort_keys=True,
        default=str,
    )
    digest: str = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:16]
    return f"sugg_{digest}", f"pattern_{digest}"


def _normalize_suggestion(
    raw: dict[str, Any], *, index: int, status_store: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    suggestion_id: str
    fallback_pattern_id: str
    suggestion_id, fallback_pattern_id = _suggestion_identity(raw, index)
    persisted: dict[str, Any] = status_store.get(suggestion_id, {})
    status: str = persisted.get("status", "pending")
    created_at: str = (
        persisted.get("created_at") or raw.get("created_at") or datetime.now(UTC).isoformat()
    )
    automation_yaml: str = str(raw.get("automation_yaml", ""))
    description: str = _sanitize(raw.get("description", raw.get("alias", "")), limit=400)
    confidence_raw: Any = raw.get("confidence", 0.7)
    try:
        confidence: float = max(0.0, min(1.0, float(confidence_raw)))
    except (TypeError, ValueError):
        confidence = 0.7

    entity_ids: list[str] = _collect_entity_ids(raw.get("automation_data") or raw)
    evidence_summary: str = _sanitize(
        raw.get("evidence_summary") or raw.get("evidence") or description,
        limit=500,
    )

    risk_assessment: dict[str, Any] | None = raw.get("risk_assessment")
    risk: RiskAssessment
    if isinstance(risk_assessment, dict):
        risk = _sanitize_risk(risk_assessment)
    else:
        risk = {
            "level": "normal",
            "flags": [],
            "reasons": [],
            "scrutiny_tags": [],
            "summary": "No risk assessment available.",
        }

    suggestion: dict[str, Any] = {
        "suggestion_id": suggestion_id,
        "pattern_id": str(raw.get("pattern_id") or fallback_pattern_id),
        "description": description,
        "confidence": confidence,
        "automation_yaml": automation_yaml,
        "evidence_summary": evidence_summary,
        "risk_assessment": risk,
        "status": status,
        "created_at": created_at,
        "entity_ids": entity_ids,
    }

    if suggestion_id not in status_store:
        status_store[suggestion_id] = {
            "status": status,
            "created_at": created_at,
        }

    return suggestion


async def _phase2_suggestions(hass: HomeAssistant) -> list[dict[str, Any]]:
    raw_items: list[Any] = hass.data.get(DOMAIN, {}).get("latest_suggestions", [])
    status_store: dict[str, dict[str, Any]] = _get_suggestion_status_store(hass)
    results: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        results.append(_normalize_suggestion(raw, index=index, status_store=status_store))
    return results


async def _tool_list_suggestions(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> list[dict[str, Any]]:
    status_filter: str = str(arguments.get("status", "")).strip()
    suggestions: list[dict[str, Any]] = await _phase2_suggestions(hass)
    if status_filter:
        suggestions = [s for s in suggestions if s.get("status") == status_filter]
    return [
        {
            "suggestion_id": s["suggestion_id"],
            "pattern_id": s["pattern_id"],
            "description": s["description"],
            "confidence": s["confidence"],
            "automation_yaml": s["automation_yaml"],
            "evidence_summary": s["evidence_summary"],
            "risk_assessment": s["risk_assessment"],
            "status": s["status"],
            "created_at": s["created_at"],
        }
        for s in suggestions
    ]


async def _tool_list_patterns(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> list[dict[str, Any]]:
    type_filter: str = str(arguments.get("type", "")).strip()
    status_filter: str = str(arguments.get("status", "")).strip()
    min_confidence_raw: Any = arguments.get("min_confidence")
    min_confidence: float | None = None
    if min_confidence_raw is not None:
        try:
            min_confidence = float(min_confidence_raw)
        except (TypeError, ValueError):
            min_confidence = None

    suggestions: list[dict[str, Any]] = await _phase2_suggestions(hass)
    patterns: dict[str, dict[str, Any]] = {}
    status_rank: dict[str, int] = {
        "pending": 4,
        "active": 4,
        "accepted": 3,
        "snoozed": 2,
        "dismissed": 1,
    }

    for suggestion in suggestions:
        pattern_id = suggestion["pattern_id"]
        pattern_type = "correlation"
        suggestion_status = str(suggestion.get("status", "pending"))
        if suggestion_status == "pending":
            suggestion_status = "active"
        if pattern_id not in patterns:
            patterns[pattern_id] = {
                "pattern_id": pattern_id,
                "type": pattern_type,
                "description": suggestion["description"],
                "confidence": suggestion["confidence"],
                "entity_ids": list(suggestion.get("entity_ids", [])),
                "evidence": {
                    "evidence_summary": suggestion["evidence_summary"],
                    "suggestion_ids": [suggestion["suggestion_id"]],
                },
                "status": suggestion_status,
                "detected_at": suggestion["created_at"],
                "last_seen": suggestion["created_at"],
                "occurrence_count": 1,
            }
            continue

        current = patterns[pattern_id]
        current["occurrence_count"] += 1
        current["confidence"] = max(float(current["confidence"]), float(suggestion["confidence"]))
        current["entity_ids"] = sorted(
            set(current["entity_ids"]) | set(suggestion.get("entity_ids", []))
        )
        current["last_seen"] = max(str(current["last_seen"]), str(suggestion["created_at"]))
        current["evidence"]["suggestion_ids"].append(suggestion["suggestion_id"])

        current_rank = status_rank.get(str(current["status"]), 0)
        candidate_rank = status_rank.get(suggestion_status, 0)
        if candidate_rank > current_rank:
            current["status"] = suggestion_status

    result: list[dict[str, Any]] = list(patterns.values())

    if type_filter:
        result = [p for p in result if p.get("type") == type_filter]
    if status_filter:
        result = [p for p in result if p.get("status") == status_filter]
    if min_confidence is not None:
        result = [p for p in result if float(p.get("confidence", 0.0)) >= min_confidence]

    return result


async def _tool_get_pattern(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    pattern_id: str = str(arguments.get("pattern_id", "")).strip()
    if not pattern_id:
        return {"error": "pattern_id is required"}

    patterns: list[dict[str, Any]] = await _tool_list_patterns(hass, {})
    pattern: dict[str, Any] | None = next(
        (p for p in patterns if p.get("pattern_id") == pattern_id), None
    )
    if pattern is None:
        return {"error": f"Pattern {pattern_id} not found"}

    suggestions: list[dict[str, Any]] = await _phase2_suggestions(hass)
    linked: list[dict[str, Any]] = [
        {
            "suggestion_id": s["suggestion_id"],
            "description": s["description"],
            "status": s["status"],
            "confidence": s["confidence"],
            "created_at": s["created_at"],
        }
        for s in suggestions
        if s.get("pattern_id") == pattern_id
    ]

    return {
        **pattern,
        "suggestions": linked,
    }


async def _tool_accept_suggestion(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    suggestion_id: str = str(arguments.get("suggestion_id", "")).strip()
    enabled: bool = bool(arguments.get("enabled", False))
    if not suggestion_id:
        return {"error": "suggestion_id is required"}

    suggestions: list[dict[str, Any]] = await _phase2_suggestions(hass)
    target: dict[str, Any] | None = next(
        (s for s in suggestions if s.get("suggestion_id") == suggestion_id), None
    )
    if target is None:
        return {"error": f"Suggestion {suggestion_id} not found"}

    if not target.get("automation_yaml"):
        return {"error": "Suggestion does not include automation_yaml"}

    created: dict[str, Any] = await _tool_create_automation(
        hass,
        {
            "yaml": target["automation_yaml"],
            "enabled": enabled,
            "version_message": f"Created from suggestion {suggestion_id}",
        },
    )
    if "error" in created:
        return created

    status_store = _get_suggestion_status_store(hass)
    status_store[suggestion_id] = {
        "status": "accepted",
        "created_at": status_store.get(suggestion_id, {}).get("created_at", target["created_at"]),
        "updated_at": datetime.now(UTC).isoformat(),
    }

    return {
        "suggestion_id": suggestion_id,
        "status": "accepted",
        "automation_id": created.get("automation_id", ""),
        "risk_assessment": target.get("risk_assessment"),
    }


async def _tool_dismiss_suggestion(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    suggestion_id: str = str(arguments.get("suggestion_id", "")).strip()
    reason: str = _sanitize(arguments.get("reason", ""), limit=300)
    if not suggestion_id:
        return {"error": "suggestion_id is required"}

    suggestions: list[dict[str, Any]] = await _phase2_suggestions(hass)
    target: dict[str, Any] | None = next(
        (s for s in suggestions if s.get("suggestion_id") == suggestion_id), None
    )
    if target is None:
        return {"error": f"Suggestion {suggestion_id} not found"}

    now_iso: str = datetime.now(UTC).isoformat()
    dismissal_reason: str = reason if reason else "user-declined"

    # Update in-memory status overlay (used by phase-2 suggestion rendering)
    status_store: dict[str, dict[str, Any]] = _get_suggestion_status_store(hass)
    status_store[suggestion_id] = {
        "status": "dismissed",
        "reason": dismissal_reason,
        "created_at": status_store.get(suggestion_id, {}).get("created_at", target["created_at"]),
        "updated_at": now_iso,
    }

    # Persist to PatternStore so dismissal survives HA restarts (#43)
    pattern_store = hass.data.get(DOMAIN, {}).get("pattern_store")
    if pattern_store is not None:
        await pattern_store.update_suggestion_status(
            suggestion_id,
            status="dismissed",
            dismissed_at=now_iso,
            dismissal_reason=dismissal_reason,
        )
    else:
        _LOGGER.warning(
            "pattern_store not available — dismissal for %s not persisted to storage",
            suggestion_id,
        )

    return {
        "suggestion_id": suggestion_id,
        "status": "dismissed",
        "reason": dismissal_reason,
    }


async def _tool_trigger_scan(hass: HomeAssistant) -> dict[str, Any]:
    domain_data: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    now: datetime = datetime.now(UTC)
    last_scan_iso: str | None = domain_data.get("_mcp_last_scan_at")

    if isinstance(last_scan_iso, str):
        try:
            last_scan: datetime = datetime.fromisoformat(last_scan_iso)
            delta: float = (now - last_scan).total_seconds()
            if delta < 60:
                suggestions: list[dict[str, Any]] = await _phase2_suggestions(hass)
                return {
                    "patterns_detected": len({s["pattern_id"] for s in suggestions}),
                    "suggestions_generated": len(suggestions),
                    "scan_duration_ms": 0,
                    "cached": True,
                }
        except ValueError:
            pass

    collector: DataCollector | None = _find_collector(hass)
    if collector is None:
        return {"error": "No collector available — check LLM configuration"}

    started: datetime = datetime.now(UTC)
    await collector._collect_analyze_log()
    finished: datetime = datetime.now(UTC)

    domain_data["_mcp_last_scan_at"] = finished.isoformat()
    suggestions: list[dict[str, Any]] = await _phase2_suggestions(hass)

    return {
        "patterns_detected": len({s["pattern_id"] for s in suggestions}),
        "suggestions_generated": len(suggestions),
        "scan_duration_ms": int((finished - started).total_seconds() * 1000),
        "cached": False,
    }


# ── Risk assessment sanitizer ─────────────────────────────────────────────────


def _sanitize_risk(risk: RiskAssessment | dict[str, Any]) -> RiskAssessment:
    """Return a copy of a risk_assessment dict with all strings sanitized."""
    return {
        "level": risk.get("level", "normal"),
        "flags": list(risk.get("flags", [])),
        "reasons": [_sanitize(r, limit=300) for r in risk.get("reasons", [])],
        "scrutiny_tags": list(risk.get("scrutiny_tags", [])),
        "summary": _sanitize(risk.get("summary", ""), limit=300),
    }


# ── Tool: selora_home_analytics ──────────────────────────────────────────────


async def _tool_home_analytics(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get analytics about device usage patterns and state changes."""
    from .pattern_store import get_pattern_store  # noqa: PLC0415

    pattern_store = get_pattern_store(hass)
    if pattern_store is None:
        return {"error": "Pattern store not available"}

    entity_id = str(arguments.get("entity_id", "")).strip() or None

    if entity_id:
        usage_windows = await pattern_store.get_usage_windows(entity_id)
        state_transitions = await pattern_store.get_state_transition_counts(entity_id)
        return {
            "entity_id": entity_id,
            "usage_windows": [
                {**w, "primary_state": _sanitize(w["primary_state"], limit=64)}
                for w in usage_windows
            ],
            "state_transitions": [
                {
                    "from": _sanitize(t["from"], limit=64),
                    "to": _sanitize(t["to"], limit=64),
                    "count": t["count"],
                }
                for t in state_transitions
            ],
        }

    return await pattern_store.get_analytics_summary()


# ── Tool definitions (MCP schema) ─────────────────────────────────────────────


_TOOL_DEFINITIONS: list[MCPTool] = [
    MCPTool(
        name=TOOL_LIST_AUTOMATIONS,
        description=(
            "List Selora AI-managed automations with their status and risk assessment. "
            "Filter by status: pending, enabled, or disabled."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "enabled", "disabled"],
                    "description": "Filter by automation status. Omit to return all.",
                }
            },
        },
    ),
    MCPTool(
        name=TOOL_GET_AUTOMATION,
        description=(
            "Return full detail for a single Selora automation: YAML, version history, "
            "lineage, and risk assessment."
        ),
        inputSchema={
            "type": "object",
            "required": ["automation_id"],
            "properties": {
                "automation_id": {"type": "string", "description": "The Selora automation ID."}
            },
        },
    ),
    MCPTool(
        name=TOOL_VALIDATE_AUTOMATION,
        description=(
            "Validate and risk-assess a YAML string representing a Home Assistant automation "
            "WITHOUT creating or modifying anything. Use this to check externally-generated "
            "YAML before committing. Returns validation errors and a risk assessment."
        ),
        inputSchema={
            "type": "object",
            "required": ["yaml"],
            "properties": {
                "yaml": {
                    "type": "string",
                    "description": "Raw YAML string for a Home Assistant automation.",
                }
            },
        },
    ),
    MCPTool(
        name=TOOL_CREATE_AUTOMATION,
        description=(
            "Create a new Home Assistant automation from a YAML string. "
            "Server-side validation and risk assessment run unconditionally. "
            "Automations are created DISABLED by default — set enabled=true to override. "
            "Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "required": ["yaml"],
            "properties": {
                "yaml": {"type": "string", "description": "Raw YAML for the automation."},
                "enabled": {
                    "type": "boolean",
                    "default": False,
                    "description": "Whether to enable the automation immediately. Defaults to false.",
                },
                "version_message": {
                    "type": "string",
                    "description": "Optional note recorded in the version history.",
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_ACCEPT_AUTOMATION,
        description=(
            "Enable or update a Selora automation that is currently disabled. "
            "Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "required": ["automation_id"],
            "properties": {
                "automation_id": {"type": "string"},
                "enabled": {
                    "type": "boolean",
                    "default": False,
                    "description": "Set true to enable immediately, false to keep disabled.",
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_DELETE_AUTOMATION,
        description=("Delete a Selora-managed automation permanently. Requires admin access."),
        inputSchema={
            "type": "object",
            "required": ["automation_id"],
            "properties": {"automation_id": {"type": "string"}},
        },
    ),
    MCPTool(
        name=TOOL_GET_HOME_SNAPSHOT,
        description=(
            "Return current Home Assistant entity states grouped by area. "
            "Call this first to understand what entities and areas exist before "
            "generating or requesting any automation."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    MCPTool(
        name=TOOL_CHAT,
        description=(
            "Send a natural-language message to Selora's internal LLM in the context of "
            "the current home state. Returns a response and, where applicable, a proposed "
            "automation with YAML and risk assessment. "
            "Pass session_id to continue an existing conversation. "
            "Pass refine_automation_id to refine a specific pending automation. "
            "This is the primary Coroutine Synthesis suspension point: the external agent "
            "yields here and Selora advances the automation artifact using home-grounded "
            "generation. Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "required": ["message"],
            "properties": {
                "message": {"type": "string"},
                "session_id": {
                    "type": "string",
                    "description": "Continue an existing session. Omit to start a new one.",
                },
                "refine_automation_id": {
                    "type": "string",
                    "description": "Refine a specific pending automation.",
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_LIST_SESSIONS,
        description="Return recent Selora chat sessions (title, id, timestamp). No messages included.",
        inputSchema={"type": "object", "properties": {}},
    ),
    MCPTool(
        name=TOOL_LIST_PATTERNS,
        description=(
            "List detected behavior patterns derived from Selora suggestions. "
            "Supports filtering by type, confidence, and status."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["time_based", "correlation", "sequence"],
                },
                "min_confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "dismissed", "snoozed", "accepted"],
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_GET_PATTERN,
        description="Return full detail for one pattern, including linked suggestions.",
        inputSchema={
            "type": "object",
            "required": ["pattern_id"],
            "properties": {"pattern_id": {"type": "string"}},
        },
    ),
    MCPTool(
        name=TOOL_LIST_SUGGESTIONS,
        description=(
            "List proactive automation suggestions with YAML previews and risk assessment. "
            "Supports status filtering."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "accepted", "dismissed", "snoozed"],
                }
            },
        },
    ),
    MCPTool(
        name=TOOL_ACCEPT_SUGGESTION,
        description=(
            "Create an automation from a pending suggestion and mark it accepted. "
            "Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "required": ["suggestion_id"],
            "properties": {
                "suggestion_id": {"type": "string"},
                "enabled": {
                    "type": "boolean",
                    "default": False,
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_DISMISS_SUGGESTION,
        description=(
            "Mark a suggestion as dismissed. Optionally include a reason. Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "required": ["suggestion_id"],
            "properties": {
                "suggestion_id": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name=TOOL_TRIGGER_SCAN,
        description=(
            "Trigger an immediate suggestion scan. Rate-limited to 60 seconds and returns "
            "cached metadata when called too frequently. Requires admin access."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    # ── Phase 2: Device data ──
    MCPTool(
        name=TOOL_LIST_DEVICES,
        description=(
            "List Home Assistant devices tracked by Selora AI with their area, manufacturer, "
            "model, integration, and entity IDs. Supports optional area and domain filters."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "area": {
                    "type": "string",
                    "description": "Filter by area name (case-insensitive substring match).",
                },
                "domain": {
                    "type": "string",
                    "description": ("Filter by entity domain (e.g. light, climate, lock)."),
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_GET_DEVICE,
        description=(
            "Return full detail for a single Home Assistant device: metadata, "
            "all associated entities, and their current states and key attributes."
        ),
        inputSchema={
            "type": "object",
            "required": ["device_id"],
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "The HA device registry ID.",
                }
            },
        },
    ),
    MCPTool(
        name=TOOL_HOME_ANALYTICS,
        description=(
            "Get analytics about device usage patterns and state changes. "
            "Without entity_id returns a home-wide summary (top entities, busiest hour, totals). "
            "With entity_id returns hourly usage windows and state transition counts for that entity."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": (
                        "Optional entity ID. If provided, returns per-entity analytics. "
                        "If omitted, returns a home-wide summary."
                    ),
                }
            },
        },
    ),
]
