"""AcceptGrant and proactive state — the two legs that face the cloud.

Neither travels the tunnel. ``AcceptGrant`` arrives *on* the directive stream
but is answered by handing the code to Connect, and ``ChangeReport`` goes
home-to-cloud as an ordinary authenticated POST. What the hub must never hold
is the skill's messaging credentials or any customer's Amazon tokens, so both
legs are deliberately thin: the hub proves which installation it is, and the
cloud does the rest.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
from typing import Any
from unittest.mock import MagicMock

from homeassistant.setup import async_setup_component
import jwt
import pytest

pytest.importorskip(
    "turbojpeg",
    reason="alexa.entities imports the camera integration, which needs PyTurboJPEG",
)

from custom_components.selora_ai import alexa_connect, alexa_view  # noqa: E402
from custom_components.selora_ai.alexa_config import SeloraAlexaConfig  # noqa: E402
from custom_components.selora_ai.alexa_connect import (  # noqa: E402
    ALEXA_GRANT_PATH,
    ALEXA_TOKEN_PATH,
    AlexaConnectClient,
    AlexaConnectError,
)
from custom_components.selora_ai.const import (  # noqa: E402
    CONF_SELORA_ALEXA_JWT_KEY,
    CONF_SELORA_CONNECT_URL,
    CONF_SELORA_INSTALLATION_ID,
    SELORA_JWT_AUDIENCE_ALEXA,
    SELORA_JWT_SCOPE_PREFIX_ALEXA,
)
from custom_components.selora_ai.selora_auth import (  # noqa: E402
    AuthenticationError,
    SeloraJWTValidator,
)

CONNECT_URL = "https://connect.example.test"
INSTALLATION_ID = "install-001"
ALEXA_KEY = hmac.new(b"alexa-epoch", b"alexa-auth:install-001", "sha256").digest()
EVENT_RELAY = f"{CONNECT_URL}/api/v1/alexa/events"


def _client(hass: Any) -> AlexaConnectClient:
    return AlexaConnectClient(
        hass,
        connect_url=CONNECT_URL,
        installation_id=INSTALLATION_ID,
        derived_key=ALEXA_KEY,
    )


async def _settle_reporting(config: SeloraAlexaConfig) -> None:
    """Wait for the deferred proactive-mode start.

    It runs as a background task, which ``async_block_till_done`` deliberately
    does not wait on — that is the whole point of it not being on the directive
    path — so a test that cares about the outcome awaits the task itself.
    """
    if (pending := config._report_start) is not None:
        await pending


def _accept_grant(code: str = "auth-code-1") -> dict[str, Any]:
    return {
        "directive": {
            "header": {
                "namespace": "Alexa.Authorization",
                "name": "AcceptGrant",
                "payloadVersion": "3",
                "messageId": "msg-grant",
            },
            "payload": {
                "grant": {"type": "OAuth2.AuthorizationCode", "code": code},
                "grantee": {"type": "BearerToken", "token": "grantee-token"},
            },
        }
    }


# ── The outbound credential ───────────────────────────────────────────────────


def test_the_hub_token_is_not_replayable_against_the_hub(hass: Any) -> None:
    """The key is symmetric, so a token the hub mints under the audience the
    view accepts would be a working directive credential for anything that
    captured it in flight or out of a log."""
    token = _client(hass)._mint_hub_token()

    inbound = SeloraJWTValidator(
        derived_key=ALEXA_KEY,
        installation_id=INSTALLATION_ID,
        issuer=CONNECT_URL,
        audience=SELORA_JWT_AUDIENCE_ALEXA,
        scope_prefix=SELORA_JWT_SCOPE_PREFIX_ALEXA,
    )
    with pytest.raises(AuthenticationError):
        inbound.validate(token)


def test_the_hub_token_names_the_installation_it_speaks_for(hass: Any) -> None:
    claims = jwt.decode(
        _client(hass)._mint_hub_token(),
        ALEXA_KEY,
        algorithms=["HS256"],
        audience="selora-connect-alexa",
    )
    assert claims["sub"] == INSTALLATION_ID
    assert claims["scope"].startswith(SELORA_JWT_SCOPE_PREFIX_ALEXA)


# ── AcceptGrant ───────────────────────────────────────────────────────────────


async def test_the_grant_code_reaches_connect(hass: Any, aioclient_mock: Any) -> None:
    aioclient_mock.post(f"{CONNECT_URL}{ALEXA_GRANT_PATH}", json={"status": "linked"})

    await _client(hass).async_accept_grant("auth-code-1")

    method, url, data, headers = aioclient_mock.mock_calls[0]
    assert data == {"installation_id": INSTALLATION_ID, "grant_code": "auth-code-1"}
    assert headers["Authorization"].startswith("Bearer ")


async def test_a_refused_grant_raises_rather_than_reporting_success(
    hass: Any, aioclient_mock: Any
) -> None:
    """``async_api_accept_grant`` would otherwise answer ``AcceptGrant.Response``
    to Amazon, recording a linkage that does not exist — and the customer
    discovers it as a skill that simply never works."""
    aioclient_mock.post(f"{CONNECT_URL}{ALEXA_GRANT_PATH}", status=502)

    with pytest.raises(AlexaConnectError):
        await _client(hass).async_accept_grant("auth-code-1")


async def test_accepting_a_grant_drops_the_cached_token(hass: Any, aioclient_mock: Any) -> None:
    """A new grant is a new fan-out target, and the cached token was fetched
    before it existed."""
    aioclient_mock.post(
        f"{CONNECT_URL}{ALEXA_TOKEN_PATH}",
        json={"access_token": "tok-1", "event_endpoint": EVENT_RELAY, "expires_in": 3600},
    )
    aioclient_mock.post(f"{CONNECT_URL}{ALEXA_GRANT_PATH}", json={})

    client = _client(hass)
    assert await client.async_get_access_token() == "tok-1"
    await client.async_accept_grant("auth-code-1")
    assert client._access_token is None


# ── The token and the event endpoint arrive together ──────────────────────────


async def test_the_token_and_endpoint_are_fetched_as_a_pair(hass: Any, aioclient_mock: Any) -> None:
    """Amazon runs three regional gateways and which one a customer belongs to
    is not something a home can know, so the URL is the cloud's to decide."""
    aioclient_mock.post(
        f"{CONNECT_URL}{ALEXA_TOKEN_PATH}",
        json={"access_token": "tok-1", "event_endpoint": EVENT_RELAY, "expires_in": 3600},
    )
    client = _client(hass)

    assert client.event_endpoint is None
    assert await client.async_get_access_token() == "tok-1"
    assert client.event_endpoint == EVENT_RELAY

    # Cached: a second call does not go back to Connect.
    assert await client.async_get_access_token() == "tok-1"
    assert len(aioclient_mock.mock_calls) == 1


async def test_a_response_without_an_event_endpoint_is_refused(
    hass: Any, aioclient_mock: Any
) -> None:
    """HA's sender asserts on a None endpoint rather than reporting it, so a
    half-answer here surfaces as an AssertionError inside a background task."""
    aioclient_mock.post(f"{CONNECT_URL}{ALEXA_TOKEN_PATH}", json={"access_token": "tok-1"})

    with pytest.raises(AlexaConnectError):
        await _client(hass).async_get_access_token()


async def test_invalidating_the_token_keeps_the_endpoint(hass: Any, aioclient_mock: Any) -> None:
    """HA invalidates mid-retry on INVALID_ACCESS_TOKEN_EXCEPTION. Clearing the
    endpoint there turns a recoverable failure into an assertion on the retry."""
    aioclient_mock.post(
        f"{CONNECT_URL}{ALEXA_TOKEN_PATH}",
        json={"access_token": "tok-1", "event_endpoint": EVENT_RELAY, "expires_in": 3600},
    )
    client = _client(hass)
    await client.async_get_access_token()

    client.async_invalidate_access_token()
    assert client._access_token is None
    assert client.event_endpoint == EVENT_RELAY


# ── What the config exposes to HA's own machinery ─────────────────────────────


async def test_an_unlinked_hub_does_not_claim_to_support_auth(hass: Any) -> None:
    """``supports_auth`` is what makes ``handlers.py`` call
    ``async_accept_grant`` at all, so claiming it without a cloud behind it
    turns a missing link into a raised NotImplementedError mid-directive."""
    assert SeloraAlexaConfig(hass, INSTALLATION_ID, None).supports_auth is False
    assert SeloraAlexaConfig(hass, INSTALLATION_ID, _client(hass)).supports_auth is True


async def test_proactive_reporting_waits_for_amazon_to_have_spoken(hass: Any) -> None:
    """``authorized`` is set by the first directive, so reporting turns on when
    Amazon has actually reached this hub rather than on configuration alone."""
    assert await async_setup_component(hass, "homeassistant", {})
    config = SeloraAlexaConfig(hass, INSTALLATION_ID, _client(hass))
    await config.async_initialize()

    assert config.should_report_state is False
    config._store.set_authorized(True)
    assert config.should_report_state is True


async def test_an_unreachable_cloud_becomes_no_token_available(
    hass: Any, aioclient_mock: Any
) -> None:
    """``state_report`` catches NoTokenAvailable to unset the authorized flag
    and stop reporting; anything else escapes ``async_enable_proactive_mode``."""
    from homeassistant.components.alexa.errors import NoTokenAvailable

    aioclient_mock.post(f"{CONNECT_URL}{ALEXA_TOKEN_PATH}", status=503)
    config = SeloraAlexaConfig(hass, INSTALLATION_ID, _client(hass))

    with pytest.raises(NoTokenAvailable):
        await config.async_get_access_token()


async def test_an_unlinked_config_reports_no_endpoint(hass: Any) -> None:
    assert SeloraAlexaConfig(hass, INSTALLATION_ID, None).endpoint is None


# ── The directive path, end to end ────────────────────────────────────────────


async def test_an_accept_grant_directive_hands_the_code_over(
    hass: Any, hass_client: Any, aioclient_mock: Any, monkeypatch: Any
) -> None:
    aioclient_mock.post(f"{CONNECT_URL}{ALEXA_GRANT_PATH}", json={"status": "linked"})
    # A successful grant makes `should_report_state` true, so `handlers.py`
    # turns proactive mode on in the same directive — which validates that a
    # token can be fetched before it returns. The second mock is not scenery.
    aioclient_mock.post(
        f"{CONNECT_URL}{ALEXA_TOKEN_PATH}",
        json={"access_token": "tok-1", "event_endpoint": EVENT_RELAY, "expires_in": 3600},
    )
    monkeypatch.setattr(alexa_view, "_installation_id", lambda _hass: INSTALLATION_ID)
    monkeypatch.setattr(alexa_connect, "build_client", lambda _hass, _data: _client(_hass))

    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "http", {})
    alexa_view.async_register_view(hass)
    await hass.async_block_till_done()
    client = await hass_client()

    resp = await client.post(alexa_view.ALEXA_URL, json=_accept_grant())
    assert resp.status == 200
    body = await resp.json()
    assert body["event"]["header"]["name"] == "AcceptGrant.Response"

    grant_calls = [c for c in aioclient_mock.mock_calls if ALEXA_GRANT_PATH in str(c[1])]
    assert grant_calls, "the grant code never reached Connect"
    assert grant_calls[0][2]["grant_code"] == "auth-code-1"


# ── Building the client from the config entry ─────────────────────────────────


def test_no_client_without_alexas_own_key(hass: Any) -> None:
    """Without it Alexa is simply not linked. It must never fall back to the
    MCP key, which would tie voice to another feature's key epoch."""
    entry = MagicMock()
    # `build_client` resolves through `_alexa_credentials`, which skips a
    # disabled entry — and a bare MagicMock's `disabled_by` is a MagicMock.
    entry.disabled_by = None
    entry.data = {
        CONF_SELORA_INSTALLATION_ID: INSTALLATION_ID,
        CONF_SELORA_CONNECT_URL: CONNECT_URL,
    }
    hass.config_entries.async_entries = lambda *_a, **_k: [entry]
    assert alexa_connect.build_client(hass, {}) is None

    entry.data = {
        **entry.data,
        CONF_SELORA_ALEXA_JWT_KEY: base64.b64encode(ALEXA_KEY).decode(),
    }
    built = alexa_connect.build_client(hass, {})
    assert built is not None
    assert built._connect_url == CONNECT_URL


# ── Proactive reporting is registered exactly once ────────────────────────────


async def test_accept_grant_registers_one_state_listener(hass: Any, aioclient_mock: Any) -> None:
    """Two things try to start reporting on the same directive: HA's
    ``set_authorized`` at the top of ``async_handle_message``, through the
    locked method that keeps its unsubscribe, and then ``async_api_accept_grant``
    through the module-level function, which throws its return value away. The
    second is a listener nothing can cancel and a second ChangeReport for every
    state change."""
    from homeassistant.components.alexa.smart_home import async_handle_message
    from homeassistant.const import EVENT_STATE_CHANGED

    aioclient_mock.post(f"{CONNECT_URL}{ALEXA_GRANT_PATH}", json={})
    aioclient_mock.post(
        f"{CONNECT_URL}{ALEXA_TOKEN_PATH}",
        json={"access_token": "tok-1", "event_endpoint": EVENT_RELAY, "expires_in": 3600},
    )
    assert await async_setup_component(hass, "homeassistant", {})
    config = SeloraAlexaConfig(hass, INSTALLATION_ID, _client(hass))
    await config.async_initialize()

    before_count = hass.bus.async_listeners().get(EVENT_STATE_CHANGED, 0)

    body = await async_handle_message(hass, config, _accept_grant())
    assert body["event"]["header"]["name"] == "AcceptGrant.Response"

    # The directive answered without waiting on the cloud round trip that
    # starting reporting needs: on the first AcceptGrant, awaiting it put a
    # token fetch in front of the grant hand-over — two serial calls at up to
    # four seconds each against Connect's five-second hub deadline.
    assert config.is_reporting_states is False
    grant_calls = [c for c in aioclient_mock.mock_calls if ALEXA_GRANT_PATH in str(c[1])]
    assert grant_calls, "the grant code never reached Connect"

    await _settle_reporting(config)
    after_count = hass.bus.async_listeners().get(EVENT_STATE_CHANGED, 0)
    assert after_count - before_count == 1, "proactive mode registered more than once"
    assert config.is_reporting_states is True


async def test_reporting_stops_when_authorization_is_withdrawn(
    hass: Any, aioclient_mock: Any
) -> None:
    """``state_report`` calls ``set_authorized(False)`` when the cloud refuses a
    token, and that has to actually tear the listener down."""
    from homeassistant.const import EVENT_STATE_CHANGED

    aioclient_mock.post(
        f"{CONNECT_URL}{ALEXA_TOKEN_PATH}",
        json={"access_token": "tok-1", "event_endpoint": EVENT_RELAY, "expires_in": 3600},
    )
    assert await async_setup_component(hass, "homeassistant", {})
    config = SeloraAlexaConfig(hass, INSTALLATION_ID, _client(hass))
    await config.async_initialize()

    baseline = hass.bus.async_listeners().get(EVENT_STATE_CHANGED, 0)
    await config.set_authorized(True)
    await _settle_reporting(config)
    assert hass.bus.async_listeners().get(EVENT_STATE_CHANGED, 0) == baseline + 1

    await config.set_authorized(False)
    assert hass.bus.async_listeners().get(EVENT_STATE_CHANGED, 0) == baseline
    assert config.is_reporting_states is False


async def test_a_repeated_directive_does_not_add_a_second_listener(
    hass: Any, aioclient_mock: Any
) -> None:
    """``set_authorized(True)`` runs on every directive, not only the first."""
    from homeassistant.const import EVENT_STATE_CHANGED

    aioclient_mock.post(
        f"{CONNECT_URL}{ALEXA_TOKEN_PATH}",
        json={"access_token": "tok-1", "event_endpoint": EVENT_RELAY, "expires_in": 3600},
    )
    assert await async_setup_component(hass, "homeassistant", {})
    config = SeloraAlexaConfig(hass, INSTALLATION_ID, _client(hass))
    await config.async_initialize()

    baseline = hass.bus.async_listeners().get(EVENT_STATE_CHANGED, 0)
    for _ in range(3):
        await config.set_authorized(True)
        await _settle_reporting(config)
    assert hass.bus.async_listeners().get(EVENT_STATE_CHANGED, 0) == baseline + 1


async def test_a_pending_start_is_cancelled_on_teardown(hass: Any, aioclient_mock: Any) -> None:
    """``async_create_background_task`` outlives a config-entry unload, so a
    start still in flight would register its listener after teardown — on a
    config nothing holds a reference to and nothing can cancel."""
    aioclient_mock.post(
        f"{CONNECT_URL}{ALEXA_TOKEN_PATH}",
        json={"access_token": "tok-1", "event_endpoint": EVENT_RELAY, "expires_in": 3600},
    )
    assert await async_setup_component(hass, "homeassistant", {})
    config = SeloraAlexaConfig(hass, INSTALLATION_ID, _client(hass))
    await config.async_initialize()

    await config.set_authorized(True)
    pending = config._report_start
    assert pending is not None

    await config.async_disable_proactive_mode()
    with contextlib.suppress(asyncio.CancelledError):
        await pending
    assert pending.cancelled()
    assert config.is_reporting_states is False


async def test_concurrent_first_directives_build_one_config(
    hass: Any, aioclient_mock: Any, monkeypatch: Any
) -> None:
    """A ReportState sweep is one directive per endpoint, tens arriving
    together, and a refresh right after a restart finds no cached config. A
    config is not a stateless value object — it can own an event-bus listener —
    so a second one built and dropped leaks a reporter nothing can cancel."""
    from custom_components.selora_ai.const import DOMAIN

    monkeypatch.setattr(alexa_view, "_installation_id", lambda _hass: INSTALLATION_ID)
    monkeypatch.setattr(alexa_connect, "build_client", lambda _hass, _data: _client(_hass))
    assert await async_setup_component(hass, "homeassistant", {})
    hass.data.setdefault(DOMAIN, {})

    configs = await asyncio.gather(*(alexa_view._async_get_config(hass) for _ in range(12)))
    assert len({id(c) for c in configs}) == 1
    assert configs[0] is hass.data[DOMAIN]["alexa_config"]
