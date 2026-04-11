"""Tests for Selora Connect JWT validation and dual-auth orchestration."""

from __future__ import annotations

import base64
import hmac
import time
from unittest.mock import MagicMock

import jwt
import pytest
from custom_components.selora_ai.const import (
    SELORA_JWT_ALGORITHM,
    SELORA_JWT_ISSUER,
    SELORA_JWT_MAX_SIZE,
)
from custom_components.selora_ai.selora_auth import (
    AuthenticationError,
    SeloraAuthContext,
    SeloraJWTValidator,
    authenticate_request,
    decode_jwt_key,
)

# ── Test fixtures ─────────────────────────────────────────────────────────────

INSTALLATION_ID = "test-installation-001"
JWT_SECRET = b"test-jwt-secret-key-for-unit-tests"

# Derive key the same way Connect does
DERIVED_KEY = hmac.new(
    JWT_SECRET, f"mcp-auth:{INSTALLATION_ID}".encode(), "sha256"
).digest()
DERIVED_KEY_B64 = base64.b64encode(DERIVED_KEY).decode()


def _make_jwt(
    payload: dict | None = None,
    key: bytes | None = None,
    algorithm: str = "HS256",
) -> str:
    """Create a signed JWT for testing."""
    now = int(time.time())
    defaults = {
        "sub": "user-uuid-123",
        "email": "test@selorahomes.com",
        "iss": SELORA_JWT_ISSUER,
        "aud": "selora-mcp",
        "exp": now + 3600,
        "iat": now,
        "scope": f"mcp:{INSTALLATION_ID}",
        "role": "owner",
    }
    if payload:
        defaults.update(payload)
    return jwt.encode(defaults, key or DERIVED_KEY, algorithm=algorithm)


def _make_validator(
    key: bytes | None = None,
    installation_id: str | None = None,
) -> SeloraJWTValidator:
    """Create a validator with test defaults."""
    return SeloraJWTValidator(
        derived_key=key or DERIVED_KEY,
        installation_id=installation_id or INSTALLATION_ID,
    )


# ── decode_jwt_key ────────────────────────────────────────────────────────────


class TestDecodeJwtKey:
    """Test base64 key decoding."""

    def test_roundtrip(self) -> None:
        assert decode_jwt_key(DERIVED_KEY_B64) == DERIVED_KEY


# ── SeloraJWTValidator.validate ───────────────────────────────────────────────


class TestSeloraJWTValidator:
    """Test JWT validation logic."""

    def test_valid_token(self) -> None:
        validator = _make_validator()
        ctx = validator.validate(_make_jwt())

        assert ctx.user_id == "user-uuid-123"
        assert ctx.email == "test@selorahomes.com"
        assert ctx.is_admin is True
        assert ctx.auth_type == "selora_jwt"

    def test_expired_token(self) -> None:
        token = _make_jwt({"exp": int(time.time()) - 3600})
        validator = _make_validator()

        with pytest.raises(AuthenticationError, match="expired"):
            validator.validate(token)

    def test_wrong_issuer(self) -> None:
        token = _make_jwt({"iss": "https://evil.example.com"})
        validator = _make_validator()

        with pytest.raises(AuthenticationError, match="issuer"):
            validator.validate(token)

    def test_wrong_scope(self) -> None:
        token = _make_jwt({"scope": "read:something write:other"})
        validator = _make_validator()

        with pytest.raises(AuthenticationError, match="scope"):
            validator.validate(token)

    def test_missing_scope(self) -> None:
        """Token without scope claim should fail."""
        now = int(time.time())
        payload = {
            "sub": "user-uuid-123",
            "iss": SELORA_JWT_ISSUER,
            "aud": "selora-mcp",
            "exp": now + 3600,
            "iat": now,
            "role": "owner",
            # no scope
        }
        token = jwt.encode(payload, DERIVED_KEY, algorithm="HS256")
        validator = _make_validator()

        with pytest.raises(AuthenticationError):
            validator.validate(token)

    def test_algorithm_confusion_rs256(self) -> None:
        """RS256 token should be rejected (algorithm confusion attack)."""
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.backends import default_backend

        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend(),
        )
        token = _make_jwt(key=private_key, algorithm="RS256")
        validator = _make_validator()

        with pytest.raises(AuthenticationError):
            validator.validate(token)

    def test_wrong_signing_key(self) -> None:
        """Token signed with a different key should fail."""
        wrong_key = b"wrong-key-not-the-derived-one-xxxxx"
        token = _make_jwt(key=wrong_key)
        validator = _make_validator()

        with pytest.raises(AuthenticationError, match="malformed"):
            validator.validate(token)

    def test_oversized_token(self) -> None:
        """Token exceeding max size should be rejected before decode."""
        # Create a token with a huge payload
        big_payload = {"data": "x" * SELORA_JWT_MAX_SIZE}
        token = _make_jwt(big_payload)
        validator = _make_validator()

        with pytest.raises(AuthenticationError, match="maximum size"):
            validator.validate(token)

    def test_role_owner_is_admin(self) -> None:
        ctx = _make_validator().validate(_make_jwt({"role": "owner"}))
        assert ctx.is_admin is True

    def test_role_member_is_admin(self) -> None:
        ctx = _make_validator().validate(_make_jwt({"role": "member"}))
        assert ctx.is_admin is True

    def test_role_viewer_is_not_admin(self) -> None:
        ctx = _make_validator().validate(_make_jwt({"role": "viewer"}))
        assert ctx.is_admin is False

    def test_role_missing_defaults_to_viewer(self) -> None:
        """Missing role claim should default to non-admin."""
        now = int(time.time())
        payload = {
            "sub": "user-uuid-123",
            "iss": SELORA_JWT_ISSUER,
            "aud": "selora-mcp",
            "exp": now + 3600,
            "iat": now,
            "scope": f"mcp:{INSTALLATION_ID}",
            # no role
        }
        token = jwt.encode(payload, DERIVED_KEY, algorithm="HS256")
        ctx = _make_validator().validate(token)
        assert ctx.is_admin is False

    def test_multiple_scopes(self) -> None:
        """Token with multiple space-separated scopes should work."""
        token = _make_jwt({"scope": f"profile mcp:{INSTALLATION_ID} email"})
        ctx = _make_validator().validate(token)
        assert ctx.user_id == "user-uuid-123"

    def test_malformed_token(self) -> None:
        validator = _make_validator()
        with pytest.raises(AuthenticationError, match="malformed"):
            validator.validate("not-a-jwt-at-all")


# ── authenticate_request ──────────────────────────────────────────────────────


class TestAuthenticateRequest:
    """Test the dual-auth orchestrator."""

    @pytest.mark.asyncio
    async def test_ha_token_takes_priority(self) -> None:
        """When HA middleware authenticated, use HA auth context."""
        hass = MagicMock()
        user = MagicMock()
        user.id = "ha-user-123"
        user.is_admin = True

        request = MagicMock()
        request.get = lambda key, default=None: {
            "ha_authenticated": True,
            "hass_user": user,
        }.get(key, default)

        ctx = await authenticate_request(hass, request, _make_validator())

        assert ctx.auth_type == "ha_token"
        assert ctx.user_id == "ha-user-123"
        assert ctx.is_admin is True

    @pytest.mark.asyncio
    async def test_selora_jwt_fallback(self) -> None:
        """When HA auth fails, fall back to Selora JWT."""
        hass = MagicMock()
        hass.auth.async_validate_access_token.return_value = None
        token = _make_jwt()

        request = MagicMock()
        request.get = lambda key, default=None: {
            "ha_authenticated": False,
        }.get(key, default)
        request.headers = {"Authorization": f"Bearer {token}"}

        ctx = await authenticate_request(hass, request, _make_validator())

        assert ctx.auth_type == "selora_jwt"
        assert ctx.user_id == "user-uuid-123"
        assert ctx.is_admin is True

    @pytest.mark.asyncio
    async def test_no_auth_raises_unauthorized(self) -> None:
        """No HA token and no JWT should raise Unauthorized."""
        hass = MagicMock()

        request = MagicMock()
        request.get = lambda key, default=None: {
            "ha_authenticated": False,
        }.get(key, default)
        request.headers = {}

        with pytest.raises(AuthenticationError, match="Authentication required"):
            await authenticate_request(hass, request, _make_validator())

    @pytest.mark.asyncio
    async def test_no_validator_ha_only_mode(self) -> None:
        """When validator is None, only HA auth works."""
        hass = MagicMock()
        hass.auth.async_validate_access_token.return_value = None

        request = MagicMock()
        request.get = lambda key, default=None: {
            "ha_authenticated": False,
        }.get(key, default)
        request.headers = {"Authorization": f"Bearer {_make_jwt()}"}

        with pytest.raises(AuthenticationError):
            await authenticate_request(hass, request, None)

    @pytest.mark.asyncio
    async def test_invalid_jwt_raises_unauthorized(self) -> None:
        """Invalid JWT should raise Unauthorized, not crash."""
        hass = MagicMock()
        hass.auth.async_validate_access_token.return_value = None

        request = MagicMock()
        request.get = lambda key, default=None: {
            "ha_authenticated": False,
        }.get(key, default)
        request.headers = {"Authorization": "Bearer invalid.jwt.token"}

        with pytest.raises(AuthenticationError):
            await authenticate_request(hass, request, _make_validator())

    @pytest.mark.asyncio
    async def test_ha_non_admin_user(self) -> None:
        """HA user without admin should have is_admin=False."""
        hass = MagicMock()
        user = MagicMock()
        user.id = "ha-user-456"
        user.is_admin = False

        request = MagicMock()
        request.get = lambda key, default=None: {
            "ha_authenticated": True,
            "hass_user": user,
        }.get(key, default)

        ctx = await authenticate_request(hass, request, None)

        assert ctx.auth_type == "ha_token"
        assert ctx.is_admin is False

    @pytest.mark.asyncio
    async def test_mcp_token_auth(self) -> None:
        """MCP token (smt_ prefix) should be validated via the token store."""
        from unittest.mock import AsyncMock

        hass = MagicMock()
        hass.auth.async_validate_access_token.return_value = None

        token_meta = {
            "id": "tok-1",
            "name": "Test",
            "permission_level": "admin",
            "allowed_tools": None,
            "created_by_user_id": "user-1",
        }
        mock_store = MagicMock()
        mock_store.async_validate_token = AsyncMock(return_value=token_meta)

        request = MagicMock()
        request.get = lambda key, default=None: {
            "ha_authenticated": False,
        }.get(key, default)
        request.headers = {"Authorization": "Bearer smt_test_token_value"}

        ctx = await authenticate_request(hass, request, None, mock_store)

        assert ctx.auth_type == "mcp_token"
        assert ctx.is_admin is True
        assert ctx.token_id == "tok-1"
        mock_store.async_validate_token.assert_called_once_with("smt_test_token_value")

    @pytest.mark.asyncio
    async def test_mcp_token_read_only(self) -> None:
        """Read-only MCP token should have is_admin=False."""
        from unittest.mock import AsyncMock

        hass = MagicMock()
        hass.auth.async_validate_access_token.return_value = None

        token_meta = {
            "id": "tok-2",
            "name": "ReadOnly",
            "permission_level": "read_only",
            "allowed_tools": None,
            "created_by_user_id": "user-1",
        }
        mock_store = MagicMock()
        mock_store.async_validate_token = AsyncMock(return_value=token_meta)

        request = MagicMock()
        request.get = lambda key, default=None: {
            "ha_authenticated": False,
        }.get(key, default)
        request.headers = {"Authorization": "Bearer smt_readonly_token"}

        ctx = await authenticate_request(hass, request, None, mock_store)

        assert ctx.auth_type == "mcp_token"
        assert ctx.is_admin is False

    @pytest.mark.asyncio
    async def test_mcp_token_custom_tools(self) -> None:
        """Custom MCP token should carry allowed_tools."""
        from unittest.mock import AsyncMock

        hass = MagicMock()
        hass.auth.async_validate_access_token.return_value = None

        token_meta = {
            "id": "tok-3",
            "name": "Custom",
            "permission_level": "custom",
            "allowed_tools": ["selora_list_automations", "selora_get_automation"],
            "created_by_user_id": "user-1",
        }
        mock_store = MagicMock()
        mock_store.async_validate_token = AsyncMock(return_value=token_meta)

        request = MagicMock()
        request.get = lambda key, default=None: {
            "ha_authenticated": False,
        }.get(key, default)
        request.headers = {"Authorization": "Bearer smt_custom_token"}

        ctx = await authenticate_request(hass, request, None, mock_store)

        assert ctx.auth_type == "mcp_token"
        assert ctx.allowed_tools == frozenset({"selora_list_automations", "selora_get_automation"})

    @pytest.mark.asyncio
    async def test_mcp_token_invalid_raises(self) -> None:
        """Invalid MCP token should raise AuthenticationError."""
        from unittest.mock import AsyncMock

        hass = MagicMock()
        hass.auth.async_validate_access_token.return_value = None

        mock_store = MagicMock()
        mock_store.async_validate_token = AsyncMock(return_value=None)

        request = MagicMock()
        request.get = lambda key, default=None: {
            "ha_authenticated": False,
        }.get(key, default)
        request.headers = {"Authorization": "Bearer smt_bad_token"}

        with pytest.raises(AuthenticationError, match="Invalid or expired MCP token"):
            await authenticate_request(hass, request, None, mock_store)

    @pytest.mark.asyncio
    async def test_ha_token_takes_priority_over_mcp_token(self) -> None:
        """HA token should be preferred even when smt_ token is present."""
        from unittest.mock import AsyncMock

        hass = MagicMock()
        user = MagicMock()
        user.id = "ha-user-789"
        user.is_admin = True

        mock_store = MagicMock()
        mock_store.async_validate_token = AsyncMock()

        request = MagicMock()
        request.get = lambda key, default=None: {
            "ha_authenticated": True,
            "hass_user": user,
        }.get(key, default)
        request.headers = {"Authorization": "Bearer smt_should_not_be_checked"}

        ctx = await authenticate_request(hass, request, None, mock_store)

        assert ctx.auth_type == "ha_token"
        mock_store.async_validate_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_read_only_token_ignores_allowed_tools(self) -> None:
        """A read_only token with allowed_tools set should ignore the allowlist."""
        from unittest.mock import AsyncMock

        hass = MagicMock()
        hass.auth.async_validate_access_token.return_value = None

        # Attacker crafted: read_only but with admin tools in allowed_tools
        token_meta = {
            "id": "tok-evil",
            "name": "Sneaky",
            "permission_level": "read_only",
            "allowed_tools": ["selora_trigger_scan", "selora_delete_automation"],
            "created_by_user_id": "user-1",
        }
        mock_store = MagicMock()
        mock_store.async_validate_token = AsyncMock(return_value=token_meta)

        request = MagicMock()
        request.get = lambda key, default=None: {
            "ha_authenticated": False,
        }.get(key, default)
        request.headers = {"Authorization": "Bearer smt_sneaky_token"}

        ctx = await authenticate_request(hass, request, None, mock_store)

        assert ctx.auth_type == "mcp_token"
        assert ctx.is_admin is False
        # allowed_tools must be None — read_only ignores any stored allowlist
        assert ctx.allowed_tools is None
