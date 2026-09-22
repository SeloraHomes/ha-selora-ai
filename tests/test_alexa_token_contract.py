"""The Alexa path and the MCP path must reject each other's tokens.

Alexa rides its own Pangolin resource with its own ``key_epoch``, and the only
property that buys is independence: rotating or disabling one feature must not
touch the other. That property lives or dies here. A validator that accepted
either shape would restore the coupling silently — nothing would fail, voice
would simply start depending on a key epoch that belongs to MCP, and the day
somebody rotated it the symptom would be "Alexa stopped working" with nothing
to connect it to the cause.

Three things separate the two, and each is tested on its own, because in
production they will usually all differ at once and a test that changes
everything proves only that *something* was checked:

* the derived key, which is the signature;
* the audience, which is what still refuses a cross-path token if Connect ever
  derives both features from one secret;
* the scope prefix, which is what refuses one minted under the right key and
  audience but granted for the other feature.
"""

from __future__ import annotations

import hmac
import time
from typing import Any
from unittest.mock import MagicMock

import jwt
import pytest

from custom_components.selora_ai.const import (
    CONF_SELORA_ALEXA_AUDIENCE,
    CONF_SELORA_ALEXA_ISSUER,
    CONF_SELORA_ALEXA_JWT_KEY,
    CONF_SELORA_ALEXA_SCOPE,
    SELORA_JWT_AUDIENCE_ALEXA,
    SELORA_JWT_AUDIENCE_MCP,
    SELORA_JWT_ISSUER,
    SELORA_JWT_SCOPE_PREFIX_ALEXA,
    SELORA_JWT_SCOPE_PREFIX_MCP,
)
from custom_components.selora_ai.selora_auth import (
    AuthenticationError,
    SeloraJWTValidator,
    authenticate_request,
)

INSTALLATION_ID = "test-installation-001"

# Two epochs, two secrets, two keys — which is the whole point. Connect derives
# each from the resource that owns it, so these are never equal in production
# and a test that reused one would be testing the wrong system.
_MCP_KEY = hmac.new(b"mcp-epoch-secret", f"mcp-auth:{INSTALLATION_ID}".encode(), "sha256").digest()
_ALEXA_KEY = hmac.new(
    b"alexa-epoch-secret", f"alexa-auth:{INSTALLATION_ID}".encode(), "sha256"
).digest()


def _token(
    *,
    key: bytes,
    audience: str,
    scope: str,
) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": "user-uuid-123",
            "iss": SELORA_JWT_ISSUER,
            "aud": audience,
            "exp": now + 3600,
            "iat": now,
            "scope": scope,
            "role": "owner",
        },
        key,
        algorithm="HS256",
    )


def _mcp_validator() -> SeloraJWTValidator:
    return SeloraJWTValidator(
        derived_key=_MCP_KEY,
        installation_id=INSTALLATION_ID,
        audience=SELORA_JWT_AUDIENCE_MCP,
        scope_prefix=SELORA_JWT_SCOPE_PREFIX_MCP,
    )


def _alexa_validator() -> SeloraJWTValidator:
    return SeloraJWTValidator(
        derived_key=_ALEXA_KEY,
        installation_id=INSTALLATION_ID,
        audience=SELORA_JWT_AUDIENCE_ALEXA,
        scope_prefix=SELORA_JWT_SCOPE_PREFIX_ALEXA,
    )


def _mcp_token(key: bytes = _MCP_KEY) -> str:
    return _token(
        key=key, audience=SELORA_JWT_AUDIENCE_MCP, scope=f"mcp:{INSTALLATION_ID} mcp:write"
    )


def _alexa_token(key: bytes = _ALEXA_KEY) -> str:
    return _token(key=key, audience=SELORA_JWT_AUDIENCE_ALEXA, scope=f"alexa:{INSTALLATION_ID}")


# ── Each path accepts its own ─────────────────────────────────────────────────


def test_each_validator_accepts_its_own_token() -> None:
    assert _mcp_validator().validate(_mcp_token()).auth_type == "selora_jwt"
    assert _alexa_validator().validate(_alexa_token()).auth_type == "selora_jwt"


def test_the_alexa_scope_is_carried_through() -> None:
    ctx = _alexa_validator().validate(_alexa_token())
    assert f"alexa:{INSTALLATION_ID}" in ctx.scopes


# ── And refuses the other's, in both directions ───────────────────────────────


def test_an_alexa_token_is_rejected_on_the_mcp_path() -> None:
    with pytest.raises(AuthenticationError):
        _mcp_validator().validate(_alexa_token())


def test_an_mcp_token_is_rejected_on_the_alexa_path() -> None:
    with pytest.raises(AuthenticationError):
        _alexa_validator().validate(_mcp_token())


# ── Each separator refuses on its own ─────────────────────────────────────────


def test_the_audience_alone_refuses_a_cross_path_token() -> None:
    """If Connect ever derived both features from one secret, the signature
    would pass and this is all that is left standing between them."""
    signed_with_alexas_key = _token(
        key=_ALEXA_KEY, audience=SELORA_JWT_AUDIENCE_MCP, scope=f"alexa:{INSTALLATION_ID}"
    )
    with pytest.raises(AuthenticationError):
        _alexa_validator().validate(signed_with_alexas_key)


def test_the_scope_alone_refuses_a_token_granted_for_the_other_feature() -> None:
    """Right key, right audience, wrong grant — a token minted for MCP under
    Alexa's epoch is still not a licence to drive the house by voice."""
    right_key_wrong_grant = _token(
        key=_ALEXA_KEY,
        audience=SELORA_JWT_AUDIENCE_ALEXA,
        scope=f"mcp:{INSTALLATION_ID}",
    )
    with pytest.raises(AuthenticationError):
        _alexa_validator().validate(right_key_wrong_grant)


def test_the_key_alone_refuses_a_token_of_the_right_shape() -> None:
    """Same audience and scope, minted at the other feature's epoch."""
    with pytest.raises(AuthenticationError):
        _alexa_validator().validate(_alexa_token(key=_MCP_KEY))


# ── The default shape is still MCP's ──────────────────────────────────────────


def test_an_unqualified_validator_still_answers_for_mcp() -> None:
    """Every existing caller constructs the validator positionally, so the
    defaults are load-bearing rather than decorative."""
    validator = SeloraJWTValidator(derived_key=_MCP_KEY, installation_id=INSTALLATION_ID)
    assert validator.validate(_mcp_token()).user_id == "user-uuid-123"
    with pytest.raises(AuthenticationError):
        validator.validate(_alexa_token())


# ── And the view is wired to the Alexa one ────────────────────────────────────


def _request(token: str) -> Any:
    request = MagicMock()
    request.headers = {"Authorization": f"Bearer {token}"}
    request.get.return_value = False
    return request


async def test_the_view_authenticates_against_the_alexa_validator_only(hass: Any) -> None:
    """An unlinked-for-Alexa hub holds an MCP validator and must not fall back
    to it: that fallback is the coupling wearing a helpful face."""
    from custom_components.selora_ai.alexa_view import _authenticate
    from custom_components.selora_ai.const import DOMAIN

    hass.data[DOMAIN] = {
        "selora_jwt_validator": _mcp_validator(),
        "selora_alexa_jwt_validator": None,
        "mcp_token_store": MagicMock(),
    }
    with pytest.raises(AuthenticationError):
        await _authenticate(hass, _request(_mcp_token()))

    hass.data[DOMAIN]["selora_alexa_jwt_validator"] = _alexa_validator()
    ctx = await _authenticate(hass, _request(_alexa_token()))
    assert ctx.auth_type == "selora_jwt"

    # Still no, even now that an Alexa validator exists.
    with pytest.raises(AuthenticationError):
        await _authenticate(hass, _request(_mcp_token()))


async def test_an_mcp_api_key_is_not_an_alexa_credential(hass: Any) -> None:
    """``smt_`` tokens scope tool access. The Alexa path passes no store, so
    the prefix never routes anywhere and the token store is never consulted."""
    from custom_components.selora_ai.alexa_view import _authenticate
    from custom_components.selora_ai.const import DOMAIN

    store = MagicMock()
    hass.data[DOMAIN] = {
        "selora_alexa_jwt_validator": _alexa_validator(),
        "mcp_token_store": store,
    }
    with pytest.raises(AuthenticationError):
        await _authenticate(hass, _request("smt_some_local_api_key"))
    store.async_validate_token.assert_not_called()


async def test_authenticate_request_rejects_an_alexa_token_with_no_validator(hass: Any) -> None:
    """The MCP orchestrator given None must refuse rather than pass through."""
    with pytest.raises(AuthenticationError):
        await authenticate_request(hass, _request(_alexa_token()), None, None)


# ── Relinking moves the whole credential, or none of it ───────────────────────


def _block(key: str = "bmV3LWtleQ==") -> dict[str, str]:
    return {
        CONF_SELORA_ALEXA_JWT_KEY: key,
        CONF_SELORA_ALEXA_AUDIENCE: SELORA_JWT_AUDIENCE_ALEXA,
        CONF_SELORA_ALEXA_SCOPE: "alexa:directive",
        CONF_SELORA_ALEXA_ISSUER: "https://connect.example.test",
    }


def test_a_fresh_alexa_credential_is_written_whole() -> None:
    from custom_components.selora_ai.oauth_link import _apply_alexa_block

    data: dict[str, Any] = {}
    _apply_alexa_block(data, _block(), answered=True)
    assert data == _block()


def test_a_withdrawn_credential_is_removed_when_connect_answered() -> None:
    """The resource is gone or its key_epoch rolled. Keeping the old key would
    have the view accepting directives signed with a credential the cloud has
    stopped issuing — a revocable per-feature epoch undone."""
    from custom_components.selora_ai.oauth_link import _apply_alexa_block

    data: dict[str, Any] = {**_block("b2xkLWtleQ=="), "other": "kept"}
    _apply_alexa_block(data, None, answered=True)
    assert data == {"other": "kept"}


def test_a_credential_survives_connect_being_unreachable() -> None:
    """Silence says nothing about the key, and deleting one on a timeout would
    break voice on any relink that raced a deploy."""
    from custom_components.selora_ai.oauth_link import _apply_alexa_block

    data: dict[str, Any] = dict(_block("b2xkLWtleQ=="))
    _apply_alexa_block(data, None, answered=False)
    assert data == _block("b2xkLWtleQ==")


def test_a_rotation_replaces_every_member() -> None:
    """A key that moved while the audience did not is a hub that refuses every
    directive while looking perfectly configured."""
    from custom_components.selora_ai.oauth_link import _apply_alexa_block

    data: dict[str, Any] = dict(_block("b2xk"))
    data[CONF_SELORA_ALEXA_ISSUER] = "https://old-issuer.example.test"
    rotated = {**_block("bmV3"), CONF_SELORA_ALEXA_ISSUER: "https://new-issuer.example.test"}

    _apply_alexa_block(data, rotated, answered=True)
    assert data == rotated


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"jwt_key": "a2V5"},
        {"jwt_key": "a2V5", "audience": "selora-alexa", "scope": "alexa:directive"},
        {"jwt_key": "a2V5", "audience": "", "scope": "alexa:directive", "issuer": "x"},
    ],
)
def test_an_incomplete_auth_config_yields_no_block(payload: Any) -> None:
    """Whole or nothing, the same rule the OS applies. A hub that took the key
    and defaulted the rest would hold a correct credential and refuse every
    directive — a failure that reads as a bad key and is nothing of the sort."""
    from custom_components.selora_ai.oauth_link import _alexa_block

    assert _alexa_block(payload) is None


def test_a_complete_auth_config_maps_onto_the_entry_keys() -> None:
    from custom_components.selora_ai.oauth_link import _alexa_block

    assert _alexa_block(
        {
            "jwt_key": "a2V5",
            "audience": "selora-alexa",
            "scope": "alexa:directive",
            "issuer": "https://connect.example.test",
        }
    ) == {
        CONF_SELORA_ALEXA_JWT_KEY: "a2V5",
        CONF_SELORA_ALEXA_AUDIENCE: "selora-alexa",
        CONF_SELORA_ALEXA_SCOPE: "alexa:directive",
        CONF_SELORA_ALEXA_ISSUER: "https://connect.example.test",
    }


# ── Unlinking Connect revokes voice with it ───────────────────────────────────


async def test_unlinking_connect_revokes_the_whole_alexa_credential(hass: Any) -> None:
    """The credential is independently revocable from the OTHER features, not
    from Connect itself. Left behind it is a live voice credential on a hub the
    user has just unlinked, and the next relink picks it back up."""
    from unittest.mock import AsyncMock

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.selora_ai.const import (
        CONF_ENTRY_TYPE,
        CONF_SELORA_CONNECT_ENABLED,
        CONF_SELORA_JWT_KEY,
        DOMAIN,
        ENTRY_TYPE_LLM,
    )
    from custom_components.selora_ai.websocket import linking

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_LLM,
            CONF_SELORA_CONNECT_ENABLED: True,
            CONF_SELORA_JWT_KEY: "bWNw",
            **_block("YWxleGE="),
        },
    )
    entry.add_to_hass(hass)

    alexa_config = MagicMock()
    alexa_config.async_disable_proactive_mode = AsyncMock()
    alexa_config.async_deinitialize = MagicMock()
    hass.data.setdefault(DOMAIN, {}).update(
        {
            "selora_jwt_validator": MagicMock(),
            "selora_alexa_jwt_validator": MagicMock(),
            "alexa_config": alexa_config,
            "_alexa_credential_fingerprint": "stale",
        }
    )

    connection = MagicMock()
    connection.user = MagicMock(is_admin=True)
    # `async_response` wraps the handler in a sync scheduler, so the coroutine
    # is reached through __wrapped__ — the same way `chat_harness` drives the
    # chat handlers.
    await linking._handle_websocket_unlink_connect.__wrapped__(hass, connection, {"id": 1})

    # Every member, not only the key: a stray audience or issuer beside a
    # cleared key is what a later partial relink builds a mismatched validator
    # from.
    for key in (
        CONF_SELORA_ALEXA_JWT_KEY,
        CONF_SELORA_ALEXA_AUDIENCE,
        CONF_SELORA_ALEXA_SCOPE,
        CONF_SELORA_ALEXA_ISSUER,
    ):
        assert key not in entry.data

    # Re-resolved to nothing rather than popped: `None` is how every consumer
    # reads "no validator", and it is what setup writes for an unlinked hub.
    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is None
    assert "alexa_config" not in hass.data[DOMAIN]
    # Not just the reference: the listener reports state to Amazon through a
    # Connect client built from credentials the hub no longer has.
    alexa_config.async_disable_proactive_mode.assert_awaited_once()


# ── The delivered audience and scope may not collapse the separation ──────────


def test_a_usable_alexa_credential_reports_no_conflict() -> None:
    from custom_components.selora_ai.selora_auth import alexa_credential_conflict

    assert alexa_credential_conflict("selora-alexa", "alexa:directive") is None
    assert alexa_credential_conflict(" selora-alexa ", " alexa: ") is None


@pytest.mark.parametrize(
    ("audience", "scope", "because"),
    [
        ("selora-mcp", "alexa:directive", "audience"),
        ("selora-alexa", "mcp:", "scope"),
        ("selora-alexa", "mcp:write", "scope"),
        # A prefix OF the MCP prefix matches every MCP scope there is.
        ("selora-alexa", "m", "scope"),
        # The empty prefix matches everything, MCP included.
        ("selora-alexa", "", "scope"),
        ("selora-alexa", "   ", "scope"),
        ("", "alexa:directive", "audience"),
    ],
)
def test_values_that_would_let_an_mcp_token_in_are_refused(
    audience: str, scope: str, because: str
) -> None:
    """Entry data now decides what the voice path accepts, and the one thing it
    must not be able to say is "accept MCP tokens". Today a cross-path token
    also fails on the signature — that is exactly the reassurance not to lean
    on, since the audience and scope checks exist so the property survives
    Connect ever deriving both features from one secret."""
    from custom_components.selora_ai.selora_auth import alexa_credential_conflict

    conflict = alexa_credential_conflict(audience, scope)
    assert conflict is not None
    assert because in conflict
