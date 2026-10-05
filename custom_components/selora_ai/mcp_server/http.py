"""The MCP HTTP endpoint: registration, the view, CORS, rate limiting, OAuth proxy."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
import logging
import time
import weakref

import aiohttp
from aiohttp import web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant

from ..const import (
    DOMAIN,
    SELORA_JWT_ISSUER,
)
from ..selora_auth import AuthenticationError, authenticate_request
from .dispatch import _jsonrpc_dispatch
from .protocol import (
    _CONTENT_TYPE_JSON,
    _MCP_URL,
    _OAUTH_TOKEN_PROXY_URL,
    _PROTECTED_RESOURCE_URL,
    _TIMEOUT_SECS,
)

_LOGGER = logging.getLogger(__name__)


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
    """Simple per-key sliding-window rate limiter (in-memory, no persistence).

    Buckets are evicted from ``_hits`` once their timestamps all age
    past the window, so the dict stays bounded by the count of
    CURRENTLY ACTIVE keys, not the cumulative count of all keys that
    have ever queried us. Without eviction the dict grows one entry
    per unique client IP forever: bots scanning from rotating IPs,
    CGNAT customers, even normal cycling-DHCP clients each add a
    permanent key, and the limiter becomes a slow but unbounded
    memory leak across the lifetime of the HA process.
    """

    # Sweep stale keys at most once per ``_SWEEP_INTERVAL_S`` seconds
    # of wall time. Cheap when called frequently (we early-return) and
    # bounded when traffic spikes — the sweep walks every key but only
    # runs at most once per minute regardless of request rate.
    _SWEEP_INTERVAL_S: float = 60.0

    def __init__(self, window: int, max_hits: int) -> None:
        self._window = window
        self._max_hits = max_hits
        # Plain dict (not defaultdict) — defaultdict.__getitem__ inserts
        # an empty list on EVERY read, which is the dict-growth path
        # we're trying to close. Explicit .get() with absent-key handling
        # below keeps the dict honest.
        self._hits: dict[str, list[float]] = {}
        self._last_sweep: float = 0.0

    def is_allowed(self, key: str) -> bool:
        """Return True if the key is within the rate limit.

        Side effects:
        * On allow: append the current timestamp to ``key``'s bucket
          (creating the bucket on first hit).
        * On deny: leave the bucket intact (so the next call still
          sees it full and rejects) — empty-bucket eviction can't
          fire on the denied path because the bucket is, by
          construction, non-empty.
        * Opportunistically sweeps stale keys whose timestamps have
          all aged out, at most every ``_SWEEP_INTERVAL_S`` seconds.
          Without this, a one-shot spike from many unique keys leaves
          their entries in the dict until each one is re-queried (or
          forever, if the spike was unique-per-IP).
        """
        now = time.monotonic()
        cutoff = now - self._window
        bucket = [t for t in self._hits.get(key, ()) if t > cutoff]
        if len(bucket) >= self._max_hits:
            self._hits[key] = bucket
            return False
        bucket.append(now)
        self._hits[key] = bucket
        if now - self._last_sweep >= self._SWEEP_INTERVAL_S:
            self._evict_empty(now=now, cutoff=cutoff)
            self._last_sweep = now
        return True

    def _evict_empty(self, *, now: float | None = None, cutoff: float | None = None) -> int:
        """Drop keys whose buckets have aged out entirely.

        Called opportunistically from ``is_allowed`` at most once per
        ``_SWEEP_INTERVAL_S``. Returns the number of evicted entries.
        ``now`` / ``cutoff`` are passed in by ``is_allowed`` to skip
        re-reading the monotonic clock; tests can omit them.
        """
        if now is None:
            now = time.monotonic()
        if cutoff is None:
            cutoff = now - self._window
        stale = [k for k, ts in self._hits.items() if not any(t > cutoff for t in ts)]
        for k in stale:
            del self._hits[k]
        return len(stale)


_request_limiter = _RateLimiter(_RATE_LIMIT_WINDOW, _RATE_LIMIT_MAX_REQUESTS)
_auth_fail_limiter = _RateLimiter(_RATE_LIMIT_WINDOW, _RATE_LIMIT_AUTH_FAILURES)


def _get_client_ip(request: web.Request) -> str:
    """Return the client IP using HA's trusted-proxy-aware remote address.

    ``request.remote`` is set by HA's ``async_setup_forwarded`` middleware,
    which only trusts ``X-Forwarded-For`` from configured trusted proxies.
    This prevents callers from spoofing the header to bypass rate limits.
    """
    return request.remote or "unknown"


# ── Registration ───────────────────────────────────────────────────────────────


# Registration is process-lifetime: aiohttp's UrlDispatcher has no unregister,
# so anything added here stays for as long as HA runs.
#
# Tracked per STEP rather than per instance. A step that fails (HTTP not up,
# frozen router) stays absent so the next config-entry reload retries just that
# one — marking the whole instance done would leave a failed view dead until HA
# restarts, while retrying everything would re-add router resources for the
# steps that already succeeded.
_REGISTERED_STEPS: weakref.WeakKeyDictionary[HomeAssistant, set[str]] = weakref.WeakKeyDictionary()


def _route_exists(app: web.Application, url: str) -> bool:
    """Whether *url* is already served by this app.

    Asked when registration raised: the router is where the truth is, and a
    view whose routes landed is registered whatever else went wrong.
    """
    return any(getattr(resource, "canonical", None) == url for resource in app.router.resources())


def register_mcp_server(hass: HomeAssistant) -> None:
    """Register the Selora AI MCP HTTP views with HA's HTTP server.

    Idempotent per HomeAssistant instance, step by step. This runs from
    ``async_setup_entry``, which also fires on every reload, and aiohttp offers no
    way to remove a route — so re-registering appended a fresh resource plus routes
    each time, every one retaining a closure over the superseded view instance.
    Requests kept being served by the FIRST registration, so the later copies were
    pure growth for the lifetime of the process.

    Registration is therefore skipped per completed step rather than per instance:
    a step that fails is left unmarked and retried on the next reload, so a
    transient failure can't leave an endpoint dead until HA restarts, while the
    steps that already succeeded are never re-added.

    CORS: each view has an options() handler for preflight, and
    on_response_prepare adds CORS headers to all responses on MCP paths.
    """
    done = _REGISTERED_STEPS.get(hass)
    if done is None:
        done = set()
        _REGISTERED_STEPS[hass] = done
    expected = 0

    app: web.Application = hass.http.app

    # Views under /api/ — registered via HA's standard mechanism.
    for view in (
        SeloraAIMCPView(),
        OAuthTokenProxyView(),
    ):
        expected += 1
        if view.name in done:
            continue
        # The except is not a duplicate guard: HomeAssistantView.register adds its
        # route with no name, so aiohttp never raises for a repeat. The `done`
        # check above is what makes this idempotent. A genuine failure is logged
        # and left unmarked so the next reload retries it.
        try:
            hass.http.register_view(view)
        except (ValueError, RuntimeError) as err:
            # `register` adds the routes and THEN hands them to aiohttp_cors,
            # which insists on owning OPTIONS. These views serve MCP clients
            # from origins HA's configured CORS list will never contain, so
            # they answer preflight themselves — and that collision raises
            # AFTER the routes are in the router. The endpoint is live; only
            # the decoration failed. Reported as a failure it was a warning on
            # every start about something that works, and the step stayed
            # unmarked, so every reload retried it for the life of the process.
            if _route_exists(app, view.url):
                _LOGGER.debug(
                    "MCP view %s is registered; CORS decoration was refused (%s)",
                    view.name,
                    err,
                )
                done.add(view.name)
            else:
                _LOGGER.warning(
                    "Failed to register MCP view %s (%s) — retrying on the next reload",
                    view.name,
                    err,
                )
        else:
            done.add(view.name)

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
        step = f"{_PROTECTED_RESOURCE_URL}:{method}"
        expected += 1
        if step in done:
            continue
        try:
            app.router.add_route(method, _PROTECTED_RESOURCE_URL, handler)
        except Exception as err:  # noqa: BLE001 — one method must not block the other
            _LOGGER.warning(
                "Failed to register %s %s (%s) — retrying on the next reload",
                method,
                _PROTECTED_RESOURCE_URL,
                err,
            )
        else:
            done.add(step)

    if len(done) >= expected:
        _LOGGER.info("Selora AI MCP server registered at %s", _MCP_URL)
    else:
        _LOGGER.warning(
            "Selora AI MCP server only partially registered (%d/%d steps) — "
            "the rest will be retried on the next reload",
            len(done),
            expected,
        )


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
        # Rate-limit before doing any outbound work — this view is unauthenticated
        # by design (OAuth dance) and would otherwise be a DoS amplifier:
        # each request opens a connection to Connect with a 10s timeout.
        client_ip = _get_client_ip(request)
        if not _request_limiter.is_allowed(client_ip):
            return _add_cors(
                request,
                web.Response(
                    status=HTTPStatus.TOO_MANY_REQUESTS,
                    text="Rate limit exceeded",
                    headers={"Retry-After": str(_RATE_LIMIT_WINDOW)},
                ),
            )
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
                timeout=aiohttp.ClientTimeout(total=10),
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
        except (aiohttp.ClientError, TimeoutError) as err:
            # Avoid `_LOGGER.exception` here — the traceback could include
            # request body fragments via aiohttp internals.
            _LOGGER.error("Failed to proxy token request to Connect: %s", type(err).__name__)
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

        # A valid JSON-RPC message is an object. A bare list/string/number/
        # null parses fine but has no .get — guard before we touch it.
        if not isinstance(json_data, dict):
            return web.Response(
                status=HTTPStatus.BAD_REQUEST,
                text="Request must be a valid JSON-RPC 2.0 message",
            )

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
