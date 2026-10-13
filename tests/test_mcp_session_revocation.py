"""Installer support sessions: tokens carrying ``sid``, and revoking them.

An installer's token is a homeowner token plus ``sid``, expiring within five
minutes. Connect pushes a revoke through Selora OS, which writes the list this
reads, so a homeowner ending a session cuts the agent off on its next request
rather than at the token's expiry — and keeps it cut off across a restart.

The full-setup tests also pin what the well-known document advertises: MCP
clients follow ``authorization_servers``, so a hub synced with a dev Connect
that advertised prod sent every agent to a Connect that did not know it.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
from pathlib import Path
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import jwt
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai import mcp_session_revocation
from custom_components.selora_ai.const import (
    CONF_COLLECTOR_ENABLED,
    CONF_DISCOVERY_ENABLED,
    CONF_ENTRY_TYPE,
    CONF_INSIGHTS_ENABLED,
    CONF_SELORA_CONNECT_URL,
    CONF_SELORA_INSTALLATION_ID,
    CONF_SELORA_JWT_KEY,
    DEFAULT_SELORA_CONNECT_URL,
    DOMAIN,
    ENTRY_TYPE_LLM,
)
from custom_components.selora_ai.mcp_session_revocation import (
    ACK_FILE_NAME,
    REVOCATION_DIR_NAME,
    REVOCATION_FILE_NAME,
    MCPSessionRevocations,
    ensure_ack,
    parse_revocations,
)
from custom_components.selora_ai.selora_auth import (
    AuthenticationError,
    SeloraJWTValidator,
    authenticate_request,
)

INSTALLATION_ID = "4242"
DEV_CONNECT = "https://dev-rapid-pangolin.selorabox-dev.com"
DERIVED_KEY = hmac.new(b"secret", f"mcp-auth:{INSTALLATION_ID}".encode(), "sha256").digest()
SID = 9_000_000_001
_PROTECTED_RESOURCE = "/.well-known/oauth-protected-resource/api/selora_ai/mcp"
_MCP = "/api/selora_ai/mcp"


def _token(*, issuer: str = DEV_CONNECT, **claims: Any) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": "installer-user",
        "iss": issuer,
        "aud": "selora-mcp",
        "exp": now + 300,
        "iat": now,
        "inst_id": int(INSTALLATION_ID),
        "subdomain": "mcp-abc-def",
        "scope": "mcp:mcp-abc-def",
        "role": "owner",
    }
    payload.update(claims)
    return jwt.encode(payload, DERIVED_KEY, algorithm="HS256")


def _validator() -> SeloraJWTValidator:
    return SeloraJWTValidator(DERIVED_KEY, INSTALLATION_ID, issuer=DEV_CONNECT)


def _write(directory: Path, sessions: list[Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / REVOCATION_FILE_NAME).write_text(
        json.dumps({"version": 1, "sessions": sessions}) + "\n"
    )


def _request(token: str) -> MagicMock:
    request = MagicMock()
    request.get = lambda key, default=None: {"ha_authenticated": False}.get(key, default)
    request.headers = {"Authorization": f"Bearer {token}"}
    return request


# ── The token ─────────────────────────────────────────────────────────────────


def test_a_token_with_sid_is_accepted_and_carries_it() -> None:
    ctx = _validator().validate(_token(sid=SID))
    assert ctx.session_id == SID
    assert ctx.user_id == "installer-user"


def test_a_homeowner_token_has_no_session() -> None:
    assert _validator().validate(_token()).session_id is None


@pytest.mark.parametrize("sid", ["123", 1.5, True, [1], None])
def test_a_sid_no_revoke_could_match_is_refused(sid: Any) -> None:
    with pytest.raises(AuthenticationError):
        _validator().validate(_token(sid=sid))


# ── The file ──────────────────────────────────────────────────────────────────


def test_parse_skips_bad_entries_without_lifting_the_rest() -> None:
    raw = json.dumps(
        {
            "sessions": [
                {"sid": 1, "until": 100},
                {"sid": "2", "until": 100},
                {"sid": True, "until": 100},
                {"sid": 3},
                "junk",
                {"sid": 1, "until": 50},
            ]
        }
    ).encode()
    assert parse_revocations(raw) == {1: 100.0}


@pytest.mark.parametrize("raw", [b"", b"not json", b"[]", b'{"sessions": {}}'])
def test_parse_refuses_what_is_not_a_list(raw: bytes) -> None:
    assert parse_revocations(raw) is None


def test_a_listed_sid_is_revoked(tmp_path: Path) -> None:
    _write(tmp_path, [{"sid": SID, "until": time.time() + 300}])
    revocations = MCPSessionRevocations(tmp_path)
    revocations.refresh()
    assert revocations.is_revoked(SID)
    assert not revocations.is_revoked(SID + 1)


def test_an_entry_is_dropped_at_until(tmp_path: Path) -> None:
    until = time.time() + 300
    _write(tmp_path, [{"sid": SID, "until": until}, {"sid": 7, "until": time.time() - 1}])
    revocations = MCPSessionRevocations(tmp_path)
    revocations.refresh()
    assert not revocations.is_revoked(7)

    with patch.object(mcp_session_revocation.time, "time", return_value=until + 1):
        assert not revocations.is_revoked(SID)
    assert SID not in revocations._state.revoked


def test_the_list_survives_a_restart(tmp_path: Path) -> None:
    """A fresh instance is a restarted Core: it reads the revoke back."""
    _write(tmp_path, [{"sid": SID, "until": time.time() + 300}])
    MCPSessionRevocations(tmp_path).refresh()

    after_restart = MCPSessionRevocations(tmp_path)
    after_restart.refresh()
    assert after_restart.is_revoked(SID)


def test_a_garbled_file_keeps_the_last_list(tmp_path: Path) -> None:
    """Corruption must not reinstate an agent the homeowner cut off."""
    _write(tmp_path, [{"sid": SID, "until": time.time() + 300}])
    revocations = MCPSessionRevocations(tmp_path)
    revocations.refresh()

    (tmp_path / REVOCATION_FILE_NAME).write_text("{truncated")
    revocations.refresh()
    assert revocations.is_revoked(SID)


def test_a_removed_file_revokes_nothing(tmp_path: Path) -> None:
    _write(tmp_path, [{"sid": SID, "until": time.time() + 300}])
    revocations = MCPSessionRevocations(tmp_path)
    revocations.refresh()

    (tmp_path / REVOCATION_FILE_NAME).unlink()
    revocations.refresh()
    assert not revocations.is_revoked(SID)


def test_the_ack_declares_the_channel(tmp_path: Path) -> None:
    directory = tmp_path / REVOCATION_DIR_NAME
    assert ensure_ack(directory) == ""
    assert json.loads((directory / ACK_FILE_NAME).read_text()) == {"channel": 1}
    assert ensure_ack(directory) == ""


# ── Authenticating ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_revoked_session_is_refused(hass: HomeAssistant, tmp_path: Path) -> None:
    revocations = MCPSessionRevocations(tmp_path)
    ctx = await authenticate_request(
        hass, _request(_token(sid=SID)), _validator(), None, revocations
    )
    assert ctx.session_id == SID

    # Revoked mid-session: the next request, once the recheck is due, is refused.
    _write(tmp_path, [{"sid": SID, "until": time.time() + 300}])
    revocations._checked_at = float("-inf")
    with pytest.raises(AuthenticationError):
        await authenticate_request(hass, _request(_token(sid=SID)), _validator(), None, revocations)

    # The homeowner, and another installer session, are untouched.
    await authenticate_request(hass, _request(_token()), _validator(), None, revocations)
    await authenticate_request(hass, _request(_token(sid=SID + 1)), _validator(), None, revocations)


@pytest.mark.asyncio
async def test_parallel_requests_wait_for_the_reread(hass: HomeAssistant, tmp_path: Path) -> None:
    """Requests sent together must not be answered from the cache being replaced."""
    _write(tmp_path, [{"sid": SID, "until": time.time() + 300}])
    revocations = MCPSessionRevocations(tmp_path)

    results = await asyncio.gather(*(revocations.async_is_revoked(hass, SID) for _ in range(5)))
    assert all(results)


@pytest.mark.asyncio
async def test_the_file_is_not_reread_on_every_request(hass: HomeAssistant, tmp_path: Path) -> None:
    revocations = MCPSessionRevocations(tmp_path)
    with patch.object(revocations, "refresh") as refresh:
        for _ in range(5):
            await revocations.async_is_revoked(hass, SID)
    assert refresh.call_count == 1


# ── Full setup ────────────────────────────────────────────────────────────────


def _entry(**data: Any) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_LLM,
            "llm_provider": "ollama",
            "ollama_host": "http://127.0.0.1:1",
            "ollama_model": "test-model",
            CONF_SELORA_INSTALLATION_ID: INSTALLATION_ID,
            CONF_SELORA_JWT_KEY: base64.b64encode(DERIVED_KEY).decode(),
            **data,
        },
        options={
            CONF_DISCOVERY_ENABLED: False,
            CONF_COLLECTOR_ENABLED: False,
            CONF_INSIGHTS_ENABLED: False,
            "pattern_detection_enabled": False,
        },
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await async_setup_component(hass, "homeassistant", {})
    await async_setup_component(hass, "http", {})
    entry.add_to_hass(hass)
    with patch(
        "custom_components.selora_ai.llm_client.LLMClient.health_check",
        AsyncMock(return_value=True),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await asyncio.wait_for(hass.async_block_till_done(), timeout=10)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "advertised"),
    [
        ({CONF_SELORA_CONNECT_URL: DEV_CONNECT}, DEV_CONNECT),
        ({}, DEFAULT_SELORA_CONNECT_URL),
        ({CONF_SELORA_CONNECT_URL: ""}, DEFAULT_SELORA_CONNECT_URL),
    ],
)
async def test_authorization_servers_follow_connect_url(
    hass: HomeAssistant, hass_client_no_auth: Any, data: dict[str, Any], advertised: str
) -> None:
    entry = _entry(**data)
    await _setup(hass, entry)
    client = await hass_client_no_auth()

    resp = await client.get(_PROTECTED_RESOURCE)
    assert resp.status == 200
    assert (await resp.json())["authorization_servers"] == [advertised]

    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_a_revoke_made_before_setup_holds_over_mcp(
    hass: HomeAssistant, hass_client_no_auth: Any
) -> None:
    """Setup loads the list, so a revoke survives Core restarting; the refusal
    is the plain 401 an expired token gets, pointing at the metadata."""
    _write(Path(hass.config.path(REVOCATION_DIR_NAME)), [{"sid": SID, "until": time.time() + 300}])
    entry = _entry(**{CONF_SELORA_CONNECT_URL: DEV_CONNECT})
    await _setup(hass, entry)
    client = await hass_client_no_auth()

    resp = await client.get(_MCP, headers={"Authorization": f"Bearer {_token(sid=SID)}"})
    assert resp.status == 401
    assert "resource_metadata=" in resp.headers["WWW-Authenticate"]
    expired = await client.get(
        _MCP, headers={"Authorization": f"Bearer {_token(sid=SID + 1, exp=1)}"}
    )
    assert expired.status == 401
    assert await resp.text() == await expired.text()

    ok = await client.get(_MCP, headers={"Authorization": f"Bearer {_token(sid=SID + 1)}"})
    assert ok.status == 200

    ack = Path(hass.config.path(REVOCATION_DIR_NAME, ACK_FILE_NAME))
    assert json.loads(ack.read_text()) == {"channel": 1}

    await hass.config_entries.async_unload(entry.entry_id)
    assert "mcp_session_revocations" not in hass.data[DOMAIN]
