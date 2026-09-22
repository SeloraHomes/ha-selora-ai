"""The Alexa Smart Home directive endpoint.

One directive is one synchronous HTTP round trip (ADR-0022): Alexa invokes the
skill Lambda, the Lambda calls Connect, Connect resolves the installation to
its Pangolin subdomain and POSTs the directive body here unchanged, and the
response returns up the same call stack. There is no queue, no correlation
table and no held connection, so this view is the entire hub-side surface.

The directive body is opaque to everything between Alexa and this module.
Mapping it to devices is ``homeassistant.components.alexa``'s job, and the
only thing added here is the three concerns that are genuinely ours:
authentication, a throttle, and the timing instrumentation ADR-0022's
performance gate turns on.
"""

from __future__ import annotations

import asyncio
import hashlib
from http import HTTPStatus
import json
import logging
import time
from typing import TYPE_CHECKING, Any
import uuid
import weakref

from aiohttp import web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import Context, CoreState, HomeAssistant

from .const import CONF_SELORA_INSTALLATION_ID, DOMAIN
from .selora_auth import AuthenticationError, authenticate_request

if TYPE_CHECKING:
    from .alexa_config import SeloraAlexaConfig

_LOGGER = logging.getLogger(__name__)

ALEXA_URL = "/api/selora_ai/alexa"

# ── Throttle policy ───────────────────────────────────────────────────────────
#
# This view's policy is its own. Nothing is inherited from the MCP view, whose
# limiter answers a different question for a different caller.
#
# **The key is the grant, never the source address.** Every cloud call arrives
# through the same newt tunnel, so HA resolves one source address for all of
# them: a per-IP bucket is one bucket per home, shared by all of Connect and —
# since a household can link several Amazon accounts to one house — by every
# linked account at once. Two adults would compete for a single budget, and the
# limit would tighten as the product works better.
#
# **The ceiling is sized for a full ``Alexa.ReportState`` sweep.** ReportState
# is per endpoint, so a state refresh is one directive per endpoint arriving at
# once — hundreds on exactly the homes worth having. 400 per minute clears a
# sweep of a large registry with control traffic on top, while still catching
# the thing a limiter is actually here for on a Connect-only reachable path: a
# runaway loop, which arrives at thousands per minute rather than hundreds.
_RATE_LIMIT_WINDOW_S = 60
_RATE_LIMIT_MAX_DIRECTIVES = 400

# Below this a gzip frame costs more than it saves. Every control directive is
# a few hundred bytes; only Discover is ever near it.
_COMPRESS_MIN_BYTES = 1024


class _GrantRateLimiter:
    """Sliding-window limiter keyed on whatever identifies the caller's grant.

    Buckets are evicted once every timestamp in them has aged past the window,
    so the dict stays bounded by currently-active grants rather than by every
    grant that has ever called.
    """

    _SWEEP_INTERVAL_S: float = 60.0

    def __init__(self, window: int, max_hits: int) -> None:
        self._window = window
        self._max_hits = max_hits
        self._hits: dict[str, list[float]] = {}
        self._last_sweep: float = 0.0

    def is_allowed(self, key: str) -> bool:
        """Record a hit for *key* and return whether it is within the limit."""
        now = time.monotonic()
        cutoff = now - self._window
        bucket = [t for t in self._hits.get(key, ()) if t > cutoff]
        if len(bucket) >= self._max_hits:
            self._hits[key] = bucket
            return False
        bucket.append(now)
        self._hits[key] = bucket
        if now - self._last_sweep >= self._SWEEP_INTERVAL_S:
            stale = [k for k, ts in self._hits.items() if not any(t > cutoff for t in ts)]
            for k in stale:
                del self._hits[k]
            self._last_sweep = now
        return True


_directive_limiter = _GrantRateLimiter(_RATE_LIMIT_WINDOW_S, _RATE_LIMIT_MAX_DIRECTIVES)


# ── Grant identification ──────────────────────────────────────────────────────


def grant_token(message: dict[str, Any]) -> str | None:
    """Return the Alexa bearer token carried by *message*, wherever it sits.

    The token is not in a fixed place, and an endpoint written against one
    location passes local control tests and then fails certification, because
    discovery is among the first things Amazon's review exercises:

    * endpoint-targeted directives (``TurnOn``, ``ReportState``) carry it at
      ``directive.endpoint.scope.token``;
    * ``Alexa.Discovery.Discover`` carries it at ``directive.payload.scope.token``
      — there is no endpoint to hang a scope on;
    * ``Alexa.Authorization.AcceptGrant`` carries ``payload.grantee.token``
      alongside the ``payload.grant.code`` that is exchanged for the pair of
      long-lived tokens.

    Returns None when the directive carries no token at all, which is a
    malformed directive rather than a shape worth guessing at.
    """
    directive = message.get("directive")
    if not isinstance(directive, dict):
        return None

    payload = directive.get("payload")
    payload = payload if isinstance(payload, dict) else {}

    for holder in (directive.get("endpoint"), payload, payload.get("grantee")):
        if not isinstance(holder, dict):
            continue
        # AcceptGrant's grantee IS the scope object; the other two hold one.
        scope = holder.get("scope", holder)
        if isinstance(scope, dict) and isinstance(token := scope.get("token"), str) and token:
            return token

    return None


def _limiter_key(message: dict[str, Any], transport_user_id: str) -> str:
    """Return the bucket a directive is counted against.

    The grant token when there is one, hashed — the raw token is a live
    credential and has no business sitting in an in-memory dict or a log line.
    When a directive carries none it is malformed, and the transport
    credential Connect authenticated with is the next-narrowest honest key:
    still per-caller, still never the source address, and never a bucket
    shared across the whole home.
    """
    if (token := grant_token(message)) is not None:
        return "grant:" + hashlib.sha256(token.encode()).hexdigest()[:32]
    return f"auth:{transport_user_id}"


# ── Config lifecycle ──────────────────────────────────────────────────────────


async def _async_get_config(hass: HomeAssistant) -> SeloraAlexaConfig:
    """Return the shared Alexa config, building it on first use.

    Built lazily rather than at setup because importing
    ``homeassistant.components.alexa`` pulls in every domain it can map —
    camera, climate, media_player and two dozen more. On a hub running
    ``default_config`` those are already imported and the marginal cost is
    ~11 ms, but on a minimal install it is ~600 ms, and an install with no
    Alexa account linked should not pay that at all. ``async_register_view``
    warms it off the event loop so the first real directive does not.

    ``async_initialize`` is what gives the config its store, which
    ``async_handle_message`` writes the authorization flag to on every
    directive, so it is not optional.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    if (config := domain_data.get("alexa_config")) is not None:
        return config

    # Built under a lock. A config is not a stateless value object — it can own
    # an event-bus listener for proactive reporting — so a concurrent first
    # directive must not be allowed to build a second one and have it silently
    # dropped: the listener it started would outlive the reference, emitting a
    # duplicate ChangeReport per state change with nothing left to cancel it.
    # And concurrent first directives are the ordinary case rather than a
    # corner: a ReportState sweep is one directive per endpoint, tens of them
    # arriving together, and a refresh right after a restart finds no config.
    #
    # The lock is created without awaiting, so the `setdefault` cannot
    # interleave with another caller's; only the build inside it can.
    lock: asyncio.Lock = domain_data.setdefault("_alexa_config_lock", asyncio.Lock())
    async with lock:
        if (config := domain_data.get("alexa_config")) is not None:
            return config

        from .alexa_config import SeloraAlexaConfig
        from .alexa_connect import build_client

        config = SeloraAlexaConfig(hass, _installation_id(hass), build_client(hass, domain_data))
        await config.async_initialize()
        domain_data["alexa_config"] = config
        return config


def _installation_id(hass: HomeAssistant) -> str | None:
    """Return the Selora installation id from whichever entry carries one."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if installation_id := entry.data.get(CONF_SELORA_INSTALLATION_ID):
            return str(installation_id)
    return None


# ── The view ──────────────────────────────────────────────────────────────────


class SeloraAlexaView(HomeAssistantView):
    """Accept one Alexa Smart Home directive and answer it synchronously."""

    name = "selora_ai:alexa"
    url = ALEXA_URL
    # Manual auth, for the reason the MCP view has it: HA's dispatch layer
    # would reject a Selora Connect JWT before we ever see it. The middleware
    # still runs and still populates the HA-token path.
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        """Answer Connect's health probe.

        ``AlexaHealthCheck`` authenticates by *expecting a 401*, and the
        Pangolin rule set accepts this path without regard to method — so a
        bare 405 here would read as an unhealthy target. Answering the method
        is what makes the probe's contract true.
        """
        hass: HomeAssistant = request.app[KEY_HASS]
        try:
            await _authenticate(hass, request)
        except AuthenticationError:
            return _unauthorized()
        return web.Response(status=HTTPStatus.OK, text="Selora AI Alexa endpoint")

    async def post(self, request: web.Request) -> web.Response:
        """Handle one directive."""
        hass: HomeAssistant = request.app[KEY_HASS]

        # Authentication runs before the limiter, and nothing counts a failed
        # attempt. That is not an oversight: the health check is by
        # construction a stream of deliberate auth failures against this exact
        # path, so a limiter that counted them would eventually answer the
        # probe with 429 instead of the 401 it is waiting for — marking the
        # target unhealthy and serving 503 on the voice path until the window
        # rolled. A self-inflicted outage, avoided by construction rather than
        # by tuning the numbers.
        try:
            auth_ctx = await _authenticate(hass, request)
        except AuthenticationError:
            _note_missing_alexa_credential(hass)
            return _unauthorized()

        try:
            message = await request.json()
        except (ValueError, UnicodeDecodeError):
            return web.Response(status=HTTPStatus.BAD_REQUEST, text="Invalid JSON")

        if (shape_error := _directive_shape_error(message)) is not None:
            # ``async_handle_message`` asserts the payload version, so a
            # malformed body would be a 500 here — or, under ``python -O``,
            # a KeyError deeper in. Refuse it at the door instead.
            return web.Response(status=HTTPStatus.BAD_REQUEST, text=shape_error)

        if not _directive_limiter.is_allowed(_limiter_key(message, auth_ctx.user_id)):
            _LOGGER.warning(
                "Alexa directive throttled (%d per %ds per grant)",
                _RATE_LIMIT_MAX_DIRECTIVES,
                _RATE_LIMIT_WINDOW_S,
            )
            return web.Response(
                status=HTTPStatus.TOO_MANY_REQUESTS,
                text="Rate limit exceeded",
                headers={"Retry-After": str(_RATE_LIMIT_WINDOW_S)},
            )

        config = await _async_get_config(hass)

        from homeassistant.components.alexa.smart_home import async_handle_message

        header = message["directive"]["header"]

        # A hub that is still starting has a sparse state machine, so a
        # Discover answered now reports a fraction of the home — or none of
        # it — as a 200 that reads as success. Amazon only ever removes
        # endpoints through an explicit DeleteReport, so an empty success is
        # not corrected by the next discovery: the customer is simply told
        # they have no devices. Upstream's own `enabled=False` path is the
        # right answer and produces a proper BRIDGE_UNREACHABLE through the
        # same code that echoes correlationToken and endpoint.
        ready = hass.state is CoreState.running

        # The gate in ADR-0022 turns on how long HA's capability mapping takes
        # over a full entity registry, and an end-to-end number cannot answer
        # that — it carries the tunnel, Connect and the Lambda with it. So the
        # handler is timed here, on its own, and the figure is reported on the
        # response where a probe can read it without a second channel.
        started = time.perf_counter()
        try:
            response = await async_handle_message(
                hass,
                config,
                message,
                context=_directive_context(auth_ctx),
                enabled=ready,
            )
        except Exception:
            _LOGGER.exception(
                "Alexa directive %s/%s failed",
                header.get("namespace"),
                header.get("name"),
            )
            # Never a bare 5xx. Connect relays the body, and Alexa reports an
            # unparseable answer as a generic failure with nothing in it to
            # say which directive broke or why.
            response = _bridge_unreachable(message, "Home Assistant could not answer")
        handler_ms = (time.perf_counter() - started) * 1000

        endpoints = response.get("event", {}).get("payload", {}).get("endpoints")
        if isinstance(endpoints, list) and not endpoints:
            response = _answer_empty_discovery(hass, config, message, response)

        body = json.dumps(response).encode()
        _LOGGER.debug(
            "Alexa %s/%s handled in %.1f ms (%d bytes)",
            header.get("namespace"),
            header.get("name"),
            handler_ms,
            len(body),
        )

        alexa_response = web.Response(
            body=body,
            content_type="application/json",
            headers={"X-Selora-Handler-Ms": f"{handler_ms:.3f}"},
        )
        if len(body) >= _COMPRESS_MIN_BYTES:
            # The Discover body is ~211 KB of highly repetitive JSON and gzips
            # about 20:1, which over the tunnel's measured ~800 KB/s is the
            # difference between ~250 ms and ~13 ms — and on that directive
            # transfer is ~97% of the total, against ~9 ms of compute.
            #
            # It belongs here rather than as Traefik middleware on the Pangolin
            # resource: PangolinRuleReconciler owns the rule list and deletes
            # what it did not put there, so proxy-level compression is
            # out-of-band config that can drift or vanish on a reconcile, while
            # this travels with the code whose payload depends on it.
            #
            # No `force`, so aiohttp negotiates against Accept-Encoding and a
            # caller that cannot decompress still gets a readable answer.
            alexa_response.enable_compression()
        return alexa_response

    async def _authenticate(self, hass: HomeAssistant, request: web.Request) -> web.Response | None:
        """Return a 401 response, or None when the caller is authenticated."""
        domain_data = hass.data.get(DOMAIN, {})
        try:
            await authenticate_request(
                hass,
                request,
                domain_data.get("selora_jwt_validator"),
                domain_data.get("mcp_token_store"),
            )
        except AuthenticationError:
            return _unauthorized()
        return None


async def _authenticate(hass: HomeAssistant, request: web.Request) -> Any:
    """Authenticate a caller on the Alexa path, and only on Alexa's terms.

    The Alexa validator is passed rather than the MCP one, and no MCP token
    store at all. Both omissions are the point: a token minted for another
    feature must not open the voice path, or the separate resource and its
    separate ``key_epoch`` buy nothing — and a locally-created MCP API key,
    which exists to scope tool access, is not an Alexa credential in any
    sense. A Home Assistant token still works, because that is Home
    Assistant's own auth and the only way to exercise this endpoint by hand.

    An unlinked hub has no Alexa validator, and passing None is what makes it
    reject every Selora token instead of quietly falling back to the MCP key.
    """
    domain_data = hass.data.get(DOMAIN, {})
    return await authenticate_request(
        hass, request, domain_data.get("selora_alexa_jwt_validator"), None
    )


def _note_missing_alexa_credential(hass: HomeAssistant) -> None:
    """Say why a refused directive was refused, once.

    A hub linked to Connect BEFORE its Alexa resource existed holds no Alexa
    key, and the only thing that fetches one is the linking exchange — so every
    directive is refused and the customer sees a skill that will not enable,
    with a bare 401 and nothing to read. Naming it turns that into something a
    support ticket can start from.

    Once per setup, because this sits on the unauthenticated path and a
    per-request warning is a log-flood lever for anything that can reach the
    endpoint. The health check reaches it by design, several times a minute.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("selora_alexa_jwt_validator") is not None:
        return
    if domain_data.get("_alexa_credential_warned"):
        return
    domain_data["_alexa_credential_warned"] = True
    _LOGGER.warning(
        "An Alexa directive was refused because this hub holds no Alexa "
        "credential. It is issued when Selora Connect is linked and the Alexa "
        "resource already exists; re-link Selora Connect after enabling the "
        "Alexa skill"
    )


def _directive_context(auth_ctx: Any) -> Context:
    """The context a directive's service calls run under.

    A Home Assistant token names a real HA user, and HA's own Alexa view
    passes that user through — so this must too, or a restricted account gets
    a system context for the asking and every permission policy downstream
    stops seeing who is driving. Which matters because this endpoint accepts
    HA tokens at all: that is how it is exercised by hand.

    A Selora Connect token names Connect's subject, which is not an HA user.
    Putting it on a context makes ``hass.services.async_call`` raise
    ``UnknownUser`` and fail every directive, so the cloud acting for the
    household runs as the system does — the same as HA's own cloud path.
    """
    if auth_ctx.auth_type == "ha_token" and auth_ctx.user_id != "unknown":
        return Context(user_id=auth_ctx.user_id)
    return Context(user_id=None)


def _unauthorized() -> web.Response:
    """Return the 401 Connect's health check is waiting for."""
    return web.Response(
        status=HTTPStatus.UNAUTHORIZED,
        text="Authentication required",
        headers={"WWW-Authenticate": 'Bearer realm="selora-ai"'},
    )


def _answer_empty_discovery(
    hass: HomeAssistant,
    config: Any,
    message: dict[str, Any],
    response: dict[str, Any],
) -> dict[str, Any]:
    """Decide whether an empty Discover is an answer or a symptom.

    Two very different things arrive here looking identical, and Amazon treats
    an empty success as authoritative — it removes endpoints only through an
    explicit ``DeleteReport``, so nothing later corrects it:

    * the home presents nothing Alexa could map at all, which on a hub with
      any devices at all means it is not in a fit state to be answering. That
      is ``BRIDGE_UNREACHABLE``.
    * the home presents plenty and the customer has exposed none of it. That
      is a real answer, and reporting a bridge failure would send them
      chasing a network problem instead of the Expose page.

    ``async_get_entities`` is upstream's own pre-exposure list, so the split
    costs one extra walk on a path that is rare by construction.
    """
    from homeassistant.components.alexa.entities import async_get_entities

    if async_get_entities(hass, config):
        _LOGGER.warning(
            "Alexa discovery matched no exposed entities — check Settings → "
            "Voice assistants → Expose"
        )
        return response
    _LOGGER.warning("Alexa discovery found nothing to map; reporting the bridge unreachable")
    return _bridge_unreachable(message, "Home Assistant has no entities to expose")


def _bridge_unreachable(message: dict[str, Any], reason: str) -> dict[str, Any]:
    """Build the ErrorResponse for a hub that cannot answer.

    Built through ``AlexaDirective`` rather than by hand so the reply carries
    the directive's ``correlationToken`` and its ``endpoint`` — without both,
    Alexa cannot match the error to the request that caused it and reports it
    as malformed, which loses the one piece of information the error existed
    to carry.
    """
    from homeassistant.components.alexa.state_report import AlexaDirective

    try:
        return (
            AlexaDirective(message)
            .error(error_type="BRIDGE_UNREACHABLE", error_message=reason)
            .serialize()
        )
    except (KeyError, TypeError, AttributeError):
        # Reached only if a body got past the shape check and then defeated
        # upstream's own parsing. This runs on the error path, so raising here
        # is the one thing that turns a reportable failure into a 5xx.
        header = message.get("directive", {}).get("header", {})
        event_header: dict[str, Any] = {
            "namespace": "Alexa",
            "name": "ErrorResponse",
            "messageId": str(uuid.uuid4()),
            "payloadVersion": "3",
        }
        if isinstance(token := header.get("correlationToken"), str):
            event_header["correlationToken"] = token
        return {
            "event": {
                "header": event_header,
                "payload": {"type": "BRIDGE_UNREACHABLE", "message": reason},
            }
        }


def _directive_shape_error(message: Any) -> str | None:
    """Return why *message* is not a v3 directive, or None when it is."""
    if not isinstance(message, dict):
        return "Body must be a JSON object"
    directive = message.get("directive")
    if not isinstance(directive, dict):
        return "Body must carry a 'directive' object"
    header = directive.get("header")
    if not isinstance(header, dict):
        return "Directive must carry a 'header' object"
    if header.get("payloadVersion") != "3":
        return "Only payloadVersion 3 is supported"
    if not isinstance(header.get("namespace"), str) or not isinstance(header.get("name"), str):
        return "Directive header must carry 'namespace' and 'name'"
    if not isinstance(directive.get("payload"), dict):
        # ``AlexaDirective.__init__`` reads the payload unconditionally, so a
        # header-only body raises before any handler runs — and the error path
        # builds an AlexaDirective from the same body, raising a second time
        # and escaping as the bare 5xx this validation exists to prevent.
        return "Directive must carry a 'payload' object"
    return None


# ── Registration ──────────────────────────────────────────────────────────────

# aiohttp's UrlDispatcher has no unregister, so a route added here lives for
# the life of the process. Tracked per HomeAssistant instance so a config-entry
# reload — which re-runs setup — does not append a second resource holding a
# closure over the superseded view.
_REGISTERED: weakref.WeakKeyDictionary[HomeAssistant, bool] = weakref.WeakKeyDictionary()


def async_register_view(hass: HomeAssistant) -> None:
    """Register the Alexa directive endpoint. Idempotent per HA instance."""
    if _REGISTERED.get(hass):
        return

    hass.http.register_view(SeloraAlexaView())
    _REGISTERED[hass] = True

    # Warm the import off the event loop so the first directive is not the one
    # that pays for it. Fire-and-forget: a failure here costs a slow first
    # directive, not a broken endpoint, and blocking setup on it would make
    # every install wait for a component most of them never use.
    hass.async_create_background_task(
        _async_warm_alexa_import(hass),
        "selora_ai_alexa_warm_import",
        eager_start=False,
    )
    _LOGGER.info("Selora AI Alexa endpoint registered at %s", ALEXA_URL)


async def _async_warm_alexa_import(hass: HomeAssistant) -> None:
    """Import the Alexa machinery in the import executor."""
    import importlib

    try:
        await hass.async_add_import_executor_job(
            importlib.import_module, "homeassistant.components.alexa.smart_home"
        )
    except ImportError as err:
        # ``alexa.entities`` reaches the camera integration, whose own
        # requirements are installed by HA rather than by us. A hub without
        # them answers directives with a 500 rather than silently; surfacing it
        # at startup is what makes that diagnosable.
        _LOGGER.warning("Alexa support is unavailable on this install: %s", err)
