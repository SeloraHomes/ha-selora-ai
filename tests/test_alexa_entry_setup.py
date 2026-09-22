"""Voice is set up above the LLM gate, from what the entry was given.

Two things meet here. Selora OS injects the Alexa credential block into the
config entry, and voice is provisioned independently of Selora AI — so a
customer can be paying for one and not the other, and the integration has to
answer directives on an entry that carries no LLM provider at all.

The credential is read as delivered rather than assembled from constants,
because the audience, scope and issuer decide what the validator accepts and a
hub holding last release's values against this release's key refuses every
directive while looking perfectly configured.
"""

from __future__ import annotations

import base64
import hmac
from typing import Any

from homeassistant.setup import async_setup_component
import jwt
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai import (
    _alexa_credentials,
    _async_sync_alexa_runtime,
    async_setup_entry,
)
from custom_components.selora_ai.const import (
    CONF_ENTRY_TYPE,
    CONF_LLM_PROVIDER,
    CONF_SELORA_ALEXA_AUDIENCE,
    CONF_SELORA_ALEXA_ISSUER,
    CONF_SELORA_ALEXA_JWT_KEY,
    CONF_SELORA_ALEXA_SCOPE,
    CONF_SELORA_CONNECT_URL,
    CONF_SELORA_INSTALLATION_ID,
    DOMAIN,
    ENTRY_TYPE_DEVICE,
    ENTRY_TYPE_LLM,
)

INSTALLATION_ID = "install-001"
# The issuer Connect mints `iss` with. Deliberately NOT the Connect URL below:
# a hub whose config arrived over a different hostname would reject every
# directive while holding a perfectly correct key.
ALEXA_ISSUER = "https://alexa-issuer.example.test"
CONNECT_URL = "https://connect.example.test"
ALEXA_KEY = hmac.new(b"alexa-epoch", b"alexa-auth:install-001", "sha256").digest()
ALEXA_KEY_B64 = base64.b64encode(ALEXA_KEY).decode()


def _alexa_data(**overrides: Any) -> dict[str, Any]:
    data = {
        CONF_ENTRY_TYPE: ENTRY_TYPE_LLM,
        CONF_SELORA_INSTALLATION_ID: INSTALLATION_ID,
        CONF_SELORA_CONNECT_URL: CONNECT_URL,
        CONF_SELORA_ALEXA_JWT_KEY: ALEXA_KEY_B64,
        CONF_SELORA_ALEXA_AUDIENCE: "selora-alexa",
        CONF_SELORA_ALEXA_SCOPE: "alexa:directive",
        CONF_SELORA_ALEXA_ISSUER: ALEXA_ISSUER,
    }
    data.update(overrides)
    return data


def _directive_token(
    *,
    key: bytes = ALEXA_KEY,
    issuer: str = ALEXA_ISSUER,
    audience: str = "selora-alexa",
    scope: str = "alexa:directive",
) -> str:
    """A token shaped the way Connect mints one — 60 seconds, per directive."""
    import time

    now = int(time.time())
    return jwt.encode(
        {
            "sub": "user-1",
            "iss": issuer,
            "aud": audience,
            "scope": scope,
            "iat": now,
            "exp": now + 60,
            "role": "owner",
        },
        key,
        algorithm="HS256",
    )


def _add(hass: Any, data: dict[str, Any]) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    return entry


# ── The setup gate ────────────────────────────────────────────────────────────


async def test_an_entry_with_only_voice_still_sets_up(hass: Any) -> None:
    """The regression. Voice is provisioned independently of Selora AI, so an
    entry with the Alexa credential and no LLM provider used to return at the
    gate — no view, no validator, every directive refused, whatever Connect and
    the OS did."""
    assert await async_setup_component(hass, "http", {})
    entry = _add(hass, _alexa_data())

    assert await async_setup_entry(hass, entry) is True

    routed = {getattr(r, "canonical", None) for r in hass.http.app.router.resources()}
    assert "/api/selora_ai/alexa" in routed
    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is not None


async def test_a_voice_only_entry_gets_no_llm_runtime(hass: Any) -> None:
    """The gate still means what it meant: this entry has no provider, so it
    creates none of the per-entry runtime `hass.data` that unload tears down."""
    assert await async_setup_component(hass, "http", {})
    entry = _add(hass, _alexa_data())

    await async_setup_entry(hass, entry)

    assert entry.entry_id not in hass.data[DOMAIN]


async def test_an_entry_with_neither_still_returns_early(hass: Any) -> None:
    assert await async_setup_component(hass, "http", {})
    entry = _add(hass, {CONF_ENTRY_TYPE: ENTRY_TYPE_LLM})

    assert await async_setup_entry(hass, entry) is True
    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is None


async def test_a_device_onboarding_entry_is_still_records_only(hass: Any) -> None:
    """It returns above the hoist, so it must not reach the view either."""
    assert await async_setup_component(hass, "http", {})
    entry = _add(hass, {CONF_ENTRY_TYPE: ENTRY_TYPE_DEVICE})

    assert await async_setup_entry(hass, entry) is True
    routed = {getattr(r, "canonical", None) for r in hass.http.app.router.resources()}
    assert "/api/selora_ai/alexa" not in routed


async def test_a_stray_entry_does_not_clear_a_working_validator(hass: Any) -> None:
    """The hoist runs for every non-device entry, and the validator it builds
    is shared. Resolved per-entry, a stray unconfigured entry set up after the
    real one would find no credential and clear a working validator on its way
    past — voice dead, with the entry that owns the credential still loaded."""
    assert await async_setup_component(hass, "http", {})
    real = _add(hass, _alexa_data())
    stray = _add(hass, {CONF_ENTRY_TYPE: ENTRY_TYPE_LLM})

    await async_setup_entry(hass, real)
    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is not None

    await async_setup_entry(hass, stray)
    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is not None


async def test_an_llm_entry_without_voice_builds_no_alexa_validator(hass: Any) -> None:
    assert await async_setup_component(hass, "http", {})
    _add(hass, {CONF_ENTRY_TYPE: ENTRY_TYPE_LLM, CONF_LLM_PROVIDER: "anthropic"})

    await _async_sync_alexa_runtime(hass)
    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is None


# ── The credential is read as delivered ───────────────────────────────────────


async def test_iss_is_checked_against_the_delivered_issuer(hass: Any) -> None:
    """Connect mints `iss` with a value only it knows. Defaulting to the
    Connect URL would refuse every directive on a hub whose config arrived over
    a different hostname, while the key was perfectly correct."""
    _add(hass, _alexa_data())
    await _async_sync_alexa_runtime(hass)
    validator = hass.data[DOMAIN]["selora_alexa_jwt_validator"]

    assert validator.validate(_directive_token()).auth_type == "selora_jwt"

    from custom_components.selora_ai.selora_auth import AuthenticationError

    with pytest.raises(AuthenticationError):
        validator.validate(_directive_token(issuer=CONNECT_URL))


async def test_the_delivered_audience_and_scope_are_what_is_enforced(hass: Any) -> None:
    """They travel with the key so the set cannot drift apart across a fleet
    that updates at its own pace."""
    from custom_components.selora_ai.selora_auth import AuthenticationError

    _add(
        hass,
        _alexa_data(
            **{
                CONF_SELORA_ALEXA_AUDIENCE: "selora-alexa-next",
                CONF_SELORA_ALEXA_SCOPE: "alexa:next",
            }
        ),
    )
    await _async_sync_alexa_runtime(hass)
    validator = hass.data[DOMAIN]["selora_alexa_jwt_validator"]

    assert validator.validate(_directive_token(audience="selora-alexa-next", scope="alexa:next"))
    # The compiled-in values are a fallback, not an override.
    with pytest.raises(AuthenticationError):
        validator.validate(_directive_token())


async def test_an_entry_predating_these_keys_falls_back(hass: Any) -> None:
    """The keys are ABSENT on an entry provisioned before they existed, and it
    has to keep working. That is the only thing the fallback is for."""
    legacy = _alexa_data()
    del legacy[CONF_SELORA_ALEXA_AUDIENCE]
    del legacy[CONF_SELORA_ALEXA_SCOPE]
    _add(hass, legacy)

    resolved = _alexa_credentials(hass)
    assert resolved is not None
    assert resolved["audience"] == "selora-alexa"
    assert resolved["scope"] == "alexa:"


@pytest.mark.parametrize("blank", [CONF_SELORA_ALEXA_AUDIENCE, CONF_SELORA_ALEXA_SCOPE])
async def test_a_blank_member_disables_voice_rather_than_defaulting(
    hass: Any, caplog: Any, blank: str
) -> None:
    """Present-and-blank is a block that arrived malformed, and it is NOT the
    same answer as absent. Substituting a constant there runs voice on values
    Connect did not send — the drift the delivered values exist to prevent,
    reached by another door."""
    _add(hass, _alexa_data(**{blank: "   "}))

    with caplog.at_level("ERROR"):
        await _async_sync_alexa_runtime(hass)

    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is None
    assert any("not be separable" in r.getMessage() for r in caplog.records)


async def test_an_issuerless_credential_disables_voice(hass: Any, caplog: Any) -> None:
    _add(hass, _alexa_data(**{CONF_SELORA_ALEXA_ISSUER: ""}))

    with caplog.at_level("WARNING"):
        await _async_sync_alexa_runtime(hass)

    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is None
    assert any("missing its issuer" in r.getMessage() for r in caplog.records)


# ── The key spaces stay disjoint ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("audience", "scope"),
    [
        ("selora-mcp", "alexa:directive"),
        ("selora-alexa", "mcp:"),
        ("selora-alexa", "mcp:write"),
        ("selora-alexa", "m"),
    ],
)
async def test_a_credential_that_overlaps_mcp_disables_voice(
    hass: Any, caplog: Any, audience: str, scope: str
) -> None:
    """Entry data now decides what the voice path accepts, and the one thing it
    must not be able to say is "accept MCP tokens". Refused rather than fallen
    back on: running voice on values Connect did not send hides the failure,
    and an Alexa path that accepts MCP tokens looks exactly like one that
    works."""
    _add(
        hass,
        _alexa_data(**{CONF_SELORA_ALEXA_AUDIENCE: audience, CONF_SELORA_ALEXA_SCOPE: scope}),
    )

    with caplog.at_level("ERROR"):
        await _async_sync_alexa_runtime(hass)

    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is None
    assert any("not be separable" in r.getMessage() for r in caplog.records)


# ── Revocation ────────────────────────────────────────────────────────────────


async def test_a_credential_that_disappears_stops_the_validator(hass: Any) -> None:
    """How a revocation takes effect: the OS drops the block on
    ``enabled: false``, HA reloads, and the absence resolves to no validator.
    There is no separate revoke path to keep in step."""
    entry = _add(hass, _alexa_data())
    await _async_sync_alexa_runtime(hass)
    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is not None

    hass.config_entries.async_update_entry(
        entry, data={CONF_ENTRY_TYPE: ENTRY_TYPE_LLM, CONF_LLM_PROVIDER: "anthropic"}
    )
    await _async_sync_alexa_runtime(hass)

    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is None


async def test_a_rotated_credential_drops_the_cached_config(hass: Any) -> None:
    """The cached config holds a Connect client minted from the old key, so a
    change has to take it with it."""
    from unittest.mock import AsyncMock, MagicMock

    entry = _add(hass, _alexa_data())
    await _async_sync_alexa_runtime(hass)

    alexa_config = MagicMock()
    alexa_config.async_disable_proactive_mode = AsyncMock()
    alexa_config.async_deinitialize = MagicMock()
    hass.data[DOMAIN]["alexa_config"] = alexa_config

    # An unrelated reload leaves it alone — rebuilding every time would stop
    # proactive reporting until a directive happened to arrive.
    await _async_sync_alexa_runtime(hass)
    assert hass.data[DOMAIN]["alexa_config"] is alexa_config

    rotated = base64.b64encode(
        hmac.new(b"next-epoch", b"alexa-auth:install-001", "sha256").digest()
    ).decode()
    hass.config_entries.async_update_entry(
        entry, data=_alexa_data(**{CONF_SELORA_ALEXA_JWT_KEY: rotated})
    )
    await _async_sync_alexa_runtime(hass)

    assert "alexa_config" not in hass.data[DOMAIN]
    alexa_config.async_disable_proactive_mode.assert_awaited_once()


async def test_an_mcp_token_is_refused_by_the_delivered_alexa_validator(hass: Any) -> None:
    """The mutual rejection, against the values the OS actually delivers rather
    than the compiled-in ones. It must fail for more than one reason, so that
    it does not quietly pass on the signature alone if Connect ever derived
    both features from one secret."""
    from custom_components.selora_ai.selora_auth import (
        AuthenticationError,
        SeloraJWTValidator,
    )

    _add(hass, _alexa_data())
    await _async_sync_alexa_runtime(hass)
    alexa = hass.data[DOMAIN]["selora_alexa_jwt_validator"]

    mcp_key = hmac.new(b"mcp-epoch", b"mcp-auth:install-001", "sha256").digest()
    mcp_token = _directive_token(
        key=mcp_key, issuer=CONNECT_URL, audience="selora-mcp", scope="mcp:write"
    )
    with pytest.raises(AuthenticationError):
        alexa.validate(mcp_token)

    # Signed with Alexa's own key and issuer, so only the audience and scope
    # are left standing — and they still refuse it.
    with pytest.raises(AuthenticationError):
        alexa.validate(_directive_token(audience="selora-mcp", scope="mcp:write"))

    # And the other direction: Alexa's token on the MCP validator.
    mcp = SeloraJWTValidator(
        derived_key=mcp_key, installation_id=INSTALLATION_ID, issuer=CONNECT_URL
    )
    with pytest.raises(AuthenticationError):
        mcp.validate(_directive_token())


async def test_a_voice_only_hub_answers_a_real_directive(
    hass: Any, hass_client_no_auth: Any
) -> None:
    """End to end on the case the gate used to kill: no LLM provider, a
    Connect-minted token in the shape Connect actually mints, through the
    registered view, to a Discover.Response."""
    pytest.importorskip("turbojpeg", reason="alexa.entities imports the camera integration")

    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "http", {})
    entry = _add(hass, _alexa_data())
    assert await async_setup_entry(hass, entry) is True
    await hass.async_block_till_done()

    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen"})
    client = await hass_client_no_auth()

    resp = await client.post(
        "/api/selora_ai/alexa",
        headers={"Authorization": f"Bearer {_directive_token()}"},
        json={
            "directive": {
                "header": {
                    "namespace": "Alexa.Discovery",
                    "name": "Discover",
                    "payloadVersion": "3",
                    "messageId": "msg-1",
                },
                "payload": {"scope": {"type": "BearerToken", "token": "alexa-access-token"}},
            }
        },
    )

    assert resp.status == 200
    body = await resp.json()
    assert body["event"]["header"]["name"] == "Discover.Response"
    assert [e["endpointId"] for e in body["event"]["payload"]["endpoints"]] == ["light#kitchen"]


async def test_the_same_directive_is_refused_once_the_credential_is_withdrawn(
    hass: Any, hass_client_no_auth: Any
) -> None:
    """Revocation, from the outside. The OS drops the block, HA reloads, and
    the token that worked a moment ago stops being accepted."""
    assert await async_setup_component(hass, "http", {})
    entry = _add(hass, _alexa_data())
    await async_setup_entry(hass, entry)

    hass.config_entries.async_update_entry(entry, data={CONF_ENTRY_TYPE: ENTRY_TYPE_LLM})
    await _async_sync_alexa_runtime(hass)

    client = await hass_client_no_auth()
    resp = await client.post(
        "/api/selora_ai/alexa",
        headers={"Authorization": f"Bearer {_directive_token()}"},
        json={
            "directive": {
                "header": {
                    "namespace": "Alexa.Discovery",
                    "name": "Discover",
                    "payloadVersion": "3",
                    "messageId": "msg-1",
                },
                "payload": {"scope": {"type": "BearerToken", "token": "t"}},
            }
        },
    )
    assert resp.status == 401


# ── Teardown is symmetric with the hoist ──────────────────────────────────────


async def test_unloading_a_voice_only_entry_stops_accepting_directives(hass: Any) -> None:
    """A voice-only entry creates no per-entry runtime state, so unload's
    records-only guard returns before any of it runs. Without a teardown above
    that guard the credential keeps accepting directives until Home Assistant
    restarts."""
    from custom_components.selora_ai import async_unload_entry

    assert await async_setup_component(hass, "http", {})
    entry = _add(hass, _alexa_data())
    await async_setup_entry(hass, entry)
    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is not None

    assert await async_unload_entry(hass, entry) is True
    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is None


async def test_unloading_a_stray_entry_leaves_voice_alone(hass: Any) -> None:
    """The other half of the same question. Resolved per-entry, tearing down a
    stray duplicate would take voice down with it while the entry that owns the
    credential is still loaded."""
    from custom_components.selora_ai import async_unload_entry

    assert await async_setup_component(hass, "http", {})
    real = _add(hass, _alexa_data())
    stray = _add(hass, {CONF_ENTRY_TYPE: ENTRY_TYPE_LLM})
    await async_setup_entry(hass, real)

    assert await async_unload_entry(hass, stray) is True
    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is not None


async def test_a_disabled_entry_does_not_re_enable_voice(hass: Any) -> None:
    """``async_entries`` lists disabled entries. Reading one would turn voice
    back on from a credential the user switched off — at the next restart, on
    another entry's setup, with nothing tying the two together."""
    from homeassistant.config_entries import ConfigEntryDisabler

    assert await async_setup_component(hass, "http", {})
    disabled = MockConfigEntry(
        domain=DOMAIN, data=_alexa_data(), disabled_by=ConfigEntryDisabler.USER
    )
    disabled.add_to_hass(hass)
    assert disabled.disabled_by is ConfigEntryDisabler.USER
    _add(hass, {CONF_ENTRY_TYPE: ENTRY_TYPE_LLM, CONF_LLM_PROVIDER: "anthropic"})

    await _async_sync_alexa_runtime(hass)
    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is None
    assert _alexa_credentials(hass) is None


async def test_a_padded_credential_still_validates_a_normal_token(hass: Any) -> None:
    """The separation check trims before comparing, so a padded value passes
    it. Used untrimmed it would then match no token's audience at all — voice
    refusing everything while every value on the entry looks right."""
    _add(
        hass,
        _alexa_data(
            **{
                CONF_SELORA_ALEXA_AUDIENCE: "  selora-alexa  ",
                CONF_SELORA_ALEXA_SCOPE: "  alexa:directive  ",
                CONF_SELORA_ALEXA_ISSUER: f"  {ALEXA_ISSUER}  ",
            }
        ),
    )
    await _async_sync_alexa_runtime(hass)
    validator = hass.data[DOMAIN]["selora_alexa_jwt_validator"]

    assert validator is not None
    assert validator.validate(_directive_token()).auth_type == "selora_jwt"


async def test_unloading_one_entry_leaves_anothers_validator_standing(hass: Any) -> None:
    """The fleet sync runs at the top of unload; the shared cleanup below must
    not then throw its answer away. Popping there took voice down for an entry
    that was still loaded and owned a perfectly good credential."""
    from custom_components.selora_ai import async_unload_entry

    assert await async_setup_component(hass, "http", {})
    voice = _add(hass, _alexa_data())
    other = _add(hass, {CONF_ENTRY_TYPE: ENTRY_TYPE_LLM, CONF_LLM_PROVIDER: "anthropic"})
    await async_setup_entry(hass, voice)
    # The LLM entry owns per-entry runtime state, so its unload runs the whole
    # shared-cleanup block rather than returning at the records-only guard.
    hass.data[DOMAIN][other.entry_id] = {"_background_tasks": [], "unsub_discovery": None}

    assert await async_unload_entry(hass, other) is True
    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is not None
