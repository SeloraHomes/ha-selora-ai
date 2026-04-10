"""Selora Connect OAuth 2.0 — JWT validation and dual-auth orchestration.

MCP clients can authenticate via either:
  1. Home Assistant long-lived access token (existing path)
  2. Selora Connect JWT (OAuth 2.0 access token, per-installation HS256)

The authenticate_request() function tries HA auth first (set by HA middleware),
then falls back to Selora JWT validation if a validator is configured.

Key derivation (done by Connect, stored in config entry):
  HMAC-SHA256(jwtSecret, "mcp-auth:" + installationID)
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import logging
from typing import Any

from aiohttp import web
from homeassistant.core import HomeAssistant

from .const import (
    SELORA_ADMIN_ROLES,
    SELORA_JWT_ALGORITHM,
    SELORA_JWT_ISSUER,
    SELORA_JWT_LEEWAY_SECONDS,
    SELORA_JWT_MAX_SIZE,
)

_LOGGER = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Raised when MCP request authentication fails."""


# Import KEY_HASS_USER safely (same pattern as mcp_server.py)
try:
    from homeassistant.components.http import KEY_HASS_USER
except ImportError:
    KEY_HASS_USER = "hass_user"  # type: ignore[assignment]

# KEY_AUTHENTICATED is set by HA's auth middleware on every request
try:
    from homeassistant.helpers.http import KEY_AUTHENTICATED
except ImportError:
    KEY_AUTHENTICATED = "ha_authenticated"  # type: ignore[assignment]


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SeloraAuthContext:
    """Result of a successful authentication."""

    user_id: str
    email: str | None
    is_admin: bool
    auth_type: str  # "ha_token" or "selora_jwt"


# ── JWT Validator ─────────────────────────────────────────────────────────────


class SeloraJWTValidator:
    """Validates Selora Connect JWTs using a per-installation derived key."""

    def __init__(
        self,
        derived_key: bytes,
        installation_id: str,
        *,
        issuer: str = SELORA_JWT_ISSUER,
    ) -> None:
        self._derived_key = derived_key
        self._installation_id = installation_id
        self._issuer = issuer

    def validate(self, token: str) -> SeloraAuthContext:
        """Decode and validate a Selora Connect JWT.

        Raises Unauthorized on any validation failure.
        """
        import jwt

        # Reject oversized tokens before decode
        if len(token.encode()) > SELORA_JWT_MAX_SIZE:
            raise AuthenticationError("Token exceeds maximum size")

        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                self._derived_key,
                algorithms=[SELORA_JWT_ALGORITHM],
                issuer=self._issuer,
                audience="selora-mcp",
                options={"require": ["sub", "iss", "exp", "scope"]},
                leeway=SELORA_JWT_LEEWAY_SECONDS,
            )
        except jwt.ExpiredSignatureError as err:
            raise AuthenticationError("Selora token has expired") from err
        except jwt.InvalidIssuerError as err:
            raise AuthenticationError("Selora token has invalid issuer") from err
        except jwt.InvalidAlgorithmError as err:
            raise AuthenticationError("Selora token uses unsupported algorithm") from err
        except jwt.DecodeError as err:
            raise AuthenticationError("Selora token is malformed") from err
        except jwt.InvalidTokenError as err:
            raise AuthenticationError(f"Selora token validation failed: {err}") from err

        # Verify scope contains this installation
        scope = payload.get("scope", "")
        expected_scope = f"mcp:{self._installation_id}"
        scopes = scope.split() if isinstance(scope, str) else []
        if expected_scope not in scopes:
            raise AuthenticationError(f"Selora token scope does not include {expected_scope}")

        # Map role to admin status
        role = payload.get("role", "viewer")
        is_admin = role in SELORA_ADMIN_ROLES

        return SeloraAuthContext(
            user_id=payload["sub"],
            email=payload.get("email"),
            is_admin=is_admin,
            auth_type="selora_jwt",
        )


# ── Dual-auth orchestrator ────────────────────────────────────────────────────


async def authenticate_request(
    hass: HomeAssistant,
    request: web.Request,
    jwt_validator: SeloraJWTValidator | None,
) -> SeloraAuthContext:
    """Authenticate an MCP request using HA token or Selora JWT.

    Priority: HA token (set by middleware) > manual HA token check > Selora JWT.

    The MCP view uses ``requires_auth = False`` so that Selora-JWT requests
    are not rejected by the HA dispatch layer.  HA's auth middleware still
    runs and populates KEY_AUTHENTICATED for valid HA tokens, but we also
    perform a manual validation as a fallback in case the middleware skipped
    the check (e.g. future HA versions or middleware ordering changes).
    """
    # Path 1: HA already authenticated via middleware
    if request.get(KEY_AUTHENTICATED, False):
        user = request.get(KEY_HASS_USER)
        user_id = getattr(user, "id", "unknown") if user else "unknown"
        is_admin = bool(user and getattr(user, "is_admin", False))
        return SeloraAuthContext(
            user_id=user_id,
            email=None,
            is_admin=is_admin,
            auth_type="ha_token",
        )

    # Path 1b: Manual HA token validation (defensive — covers the case where
    # the auth middleware did not populate KEY_AUTHENTICATED for this view).
    # Note: async_validate_access_token is synchronous despite the name.
    token = _extract_bearer_token(request)
    if token is not None:
        try:
            refresh_token = hass.auth.async_validate_access_token(token)
        except Exception:  # noqa: BLE001
            refresh_token = None
        if refresh_token is not None:
            user = refresh_token.user
            return SeloraAuthContext(
                user_id=user.id,
                email=None,
                is_admin=user.is_admin,
                auth_type="ha_token",
            )

    # Path 2: Try Selora JWT
    if jwt_validator is not None and token is not None:
        ctx = jwt_validator.validate(token)
        _LOGGER.debug("Selora Connect auth succeeded for user %s", ctx.user_id)
        return ctx

    raise AuthenticationError("Authentication required")


def _extract_bearer_token(request: web.Request) -> str | None:
    """Extract Bearer token from the Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None


# ── Key helpers ───────────────────────────────────────────────────────────────


def decode_jwt_key(encoded_key: str) -> bytes:
    """Decode a base64-encoded JWT signing key from config entry data."""
    return base64.b64decode(encoded_key)
