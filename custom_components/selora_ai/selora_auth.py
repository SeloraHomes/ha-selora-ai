"""Selora Connect OAuth 2.0 — JWT validation and multi-auth orchestration.

MCP clients can authenticate via:
  1. Home Assistant long-lived access token (existing path)
  2. Selora MCP token (locally-created API key with per-tool permissions)
  3. Selora Connect JWT (OAuth 2.0 access token, per-installation HS256)

The authenticate_request() function tries HA auth first (set by HA middleware),
then checks for a Selora MCP token (``smt_`` prefix), and finally falls back
to Selora JWT validation if a validator is configured.

Key derivation (done by Connect, stored in config entry):
  HMAC-SHA256(jwtSecret, "mcp-auth:" + installationID)
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web
from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from .mcp_token_store import MCPTokenStore

from .const import (
    MCP_TOKEN_PERMISSION_ADMIN,
    MCP_TOKEN_PERMISSION_CUSTOM,
    MCP_TOKEN_PREFIX,
    SELORA_ADMIN_ROLES,
    SELORA_JWT_ALGORITHM,
    SELORA_JWT_AUDIENCE_MCP,
    SELORA_JWT_ISSUER,
    SELORA_JWT_LEEWAY_SECONDS,
    SELORA_JWT_MAX_SIZE,
    SELORA_JWT_SCOPE_PREFIX_MCP,
)
from .types import MCPTokenMeta

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
    auth_type: str  # "ha_token", "selora_jwt", or "mcp_token"
    allowed_tools: frozenset[str] | None = None  # None → use is_admin logic
    token_id: str | None = None  # set only for mcp_token
    scopes: frozenset[str] = frozenset()  # OAuth scopes (selora_jwt only)


# ── JWT Validator ─────────────────────────────────────────────────────────────


class SeloraJWTValidator:
    """Validates Selora Connect JWTs using a per-feature derived key.

    One validator answers for exactly one feature. ``derived_key`` comes from
    that feature's Pangolin resource and its ``key_epoch``; ``audience`` and
    ``scope_prefix`` name what the token is for. Nothing here is shared between
    features, so a token minted for one is refused by the other's validator
    three ways over — signature, audience, and scope — and each of those is
    worth keeping: the key alone would stop being enough the moment Connect
    derived two features from one secret, and the failure would be silent.
    """

    def __init__(
        self,
        derived_key: bytes,
        installation_id: str,
        *,
        issuer: str = SELORA_JWT_ISSUER,
        audience: str = SELORA_JWT_AUDIENCE_MCP,
        scope_prefix: str = SELORA_JWT_SCOPE_PREFIX_MCP,
    ) -> None:
        self._derived_key = derived_key
        self._installation_id = installation_id
        self._issuer = issuer
        self._audience = audience
        self._scope_prefix = scope_prefix

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
                audience=self._audience,
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

        # Verify the scope carries a grant for THIS feature. The signature
        # already proves the token belongs to this installation and to this
        # feature's key epoch, so only the prefix is checked rather than a
        # specific format — Connect issues mcp:<subdomain>, mcp:device:<id>,
        # alexa:<subdomain> and so on, and pinning the tail here would make
        # every new shape a hub-side release.
        scope = payload.get("scope", "")
        scopes = scope.split() if isinstance(scope, str) else []
        if not any(s.startswith(self._scope_prefix) for s in scopes):
            raise AuthenticationError(
                f"Selora token carries no {self._scope_prefix.rstrip(':')} scope"
            )

        # Map role to admin status
        role = payload.get("role", "viewer")
        is_admin = role in SELORA_ADMIN_ROLES

        return SeloraAuthContext(
            user_id=payload["sub"],
            email=payload.get("email"),
            is_admin=is_admin,
            auth_type="selora_jwt",
            scopes=frozenset(scopes),
        )


def alexa_credential_conflict(audience: str, scope_prefix: str) -> str | None:
    """Why these delivered values cannot be used for the Alexa path.

    The audience and scope arrive in the config entry rather than being
    compiled in, which is what keeps them from drifting away from the key they
    belong to. The cost is that entry data now decides what the voice path
    accepts, and the one thing it must never be able to say is "accept MCP
    tokens" — that collapses the mutual rejection the whole per-feature key
    design rests on, silently, in the direction where nothing fails.

    Today a cross-path token also fails on the signature, because the two keys
    come from different epochs. That is exactly the reassurance not to lean on:
    ``const.py`` keeps the audience and scope checks load-bearing precisely so
    the property survives Connect ever deriving both features from one secret,
    and a check that is only redundant until the day it isn't is worth having.

    Scope prefixes are disjoint when neither is a prefix of the other. That
    rules out the empty string, which is a prefix of everything and would
    accept every scope there is.
    """
    audience = audience.strip()
    scope_prefix = scope_prefix.strip()

    if not audience:
        return "the Alexa audience is empty"
    if audience == SELORA_JWT_AUDIENCE_MCP:
        return f"the Alexa audience is the MCP audience ({audience!r})"
    if not scope_prefix:
        return "the Alexa scope is empty, which would match every scope"
    if scope_prefix.startswith(SELORA_JWT_SCOPE_PREFIX_MCP) or (
        SELORA_JWT_SCOPE_PREFIX_MCP.startswith(scope_prefix)
    ):
        return (
            f"the Alexa scope {scope_prefix!r} overlaps the MCP scope "
            f"{SELORA_JWT_SCOPE_PREFIX_MCP!r}"
        )
    return None


# ── Dual-auth orchestrator ────────────────────────────────────────────────────


async def authenticate_request(
    hass: HomeAssistant,
    request: web.Request,
    jwt_validator: SeloraJWTValidator | None,
    mcp_token_store: MCPTokenStore | None = None,
) -> SeloraAuthContext:
    """Authenticate an MCP request.

    Priority: HA token (middleware) > manual HA token > Selora MCP token > Selora JWT.

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

    # Path 2: Selora MCP token (prefix-gated for fast routing)
    if mcp_token_store is not None and token is not None and token.startswith(MCP_TOKEN_PREFIX):
        meta = await mcp_token_store.async_validate_token(token)
        if meta is None:
            raise AuthenticationError("Invalid or expired MCP token")
        return _build_mcp_token_context(meta)

    # Path 3: Try Selora JWT
    if jwt_validator is not None and token is not None:
        ctx = jwt_validator.validate(token)
        _LOGGER.debug("Selora Connect auth succeeded for user %s", ctx.user_id)
        return ctx

    raise AuthenticationError("Authentication required")


def _build_mcp_token_context(meta: MCPTokenMeta) -> SeloraAuthContext:
    """Build an auth context from validated MCP token metadata."""
    permission = meta["permission_level"]
    is_admin = permission == MCP_TOKEN_PERMISSION_ADMIN

    # Only honor allowed_tools for custom tokens.  read_only and admin
    # tokens ignore any stored allowlist to prevent privilege escalation.
    allowed_tools: frozenset[str] | None = None
    if permission == MCP_TOKEN_PERMISSION_CUSTOM and meta.get("allowed_tools") is not None:
        allowed_tools = frozenset(meta["allowed_tools"])

    return SeloraAuthContext(
        user_id=f"mcp_token:{meta['id']}",
        email=None,
        is_admin=is_admin,
        auth_type="mcp_token",
        allowed_tools=allowed_tools,
        token_id=meta["id"],
    )


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
