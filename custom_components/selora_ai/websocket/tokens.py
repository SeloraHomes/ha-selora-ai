"""Selora AI websocket handlers: tokens.

Extracted from __init__.py. Handlers reach shared integration
helpers via ``from .. import`` (safe: this module is imported
lazily at registration time, after the package has loaded).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import decorators
from homeassistant.core import HomeAssistant
import voluptuous as vol

from .. import (
    _in_flight_approvals,
    _require_admin,
    _resolve_approval,
)
from ..const import (
    DOMAIN,
)
from ..conversation_store import ConversationStore

_LOGGER = logging.getLogger(__name__)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/create_mcp_token",
        vol.Required("name"): str,
        vol.Required("permission_level"): str,
        vol.Optional("allowed_tools"): [str],
        vol.Optional("expires_in_days"): vol.Coerce(int),
    }
)
async def _handle_websocket_create_mcp_token(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create a new MCP token with the specified permissions."""
    if not _require_admin(connection, msg):
        return

    store = hass.data.get(DOMAIN, {}).get("mcp_token_store")
    if store is None:
        connection.send_error(msg["id"], "not_ready", "MCP token store not initialized")
        return

    from ..const import MCP_TOKEN_PERMISSION_CUSTOM

    permission_level = msg["permission_level"]
    allowed_tools = msg.get("allowed_tools")

    # Validate: custom permission requires allowed_tools
    if permission_level == MCP_TOKEN_PERMISSION_CUSTOM and not allowed_tools:
        connection.send_error(
            msg["id"],
            "invalid_params",
            "Custom permission level requires 'allowed_tools' list",
        )
        return

    # Ignore allowed_tools for non-custom tokens (prevent privilege escalation)
    if permission_level != MCP_TOKEN_PERMISSION_CUSTOM:
        allowed_tools = None

    # Compute expiration
    expires_at: str | None = None
    expires_in_days = msg.get("expires_in_days")
    if expires_in_days is not None:
        from datetime import UTC, datetime, timedelta

        expires_at = (datetime.now(UTC) + timedelta(days=expires_in_days)).isoformat()

    user = getattr(connection, "user", None)
    user_id = getattr(user, "id", "unknown") if user else "unknown"

    try:
        raw_token, meta = await store.async_create_token(
            name=msg["name"],
            permission_level=permission_level,
            allowed_tools=allowed_tools,
            expires_at=expires_at,
            created_by_user_id=user_id,
        )
    except ValueError as exc:
        connection.send_error(msg["id"], "invalid_params", str(exc))
        return

    connection.send_result(
        msg["id"],
        {
            "token": raw_token,
            "id": meta["id"],
            "name": meta["name"],
            "permission_level": meta["permission_level"],
            "allowed_tools": meta["allowed_tools"],
            "expires_at": meta["expires_at"],
        },
    )


@websocket_api.async_response
@decorators.websocket_command({vol.Required("type"): "selora_ai/list_mcp_tokens"})
async def _handle_websocket_list_mcp_tokens(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List all MCP tokens (metadata only, no secrets)."""
    if not _require_admin(connection, msg):
        return

    store = hass.data.get(DOMAIN, {}).get("mcp_token_store")
    if store is None:
        connection.send_error(msg["id"], "not_ready", "MCP token store not initialized")
        return

    tokens = await store.async_list_tokens()
    connection.send_result(msg["id"], {"tokens": tokens})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/revoke_mcp_token",
        vol.Required("token_id"): str,
    }
)
async def _handle_websocket_revoke_mcp_token(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Revoke an MCP token by ID."""
    if not _require_admin(connection, msg):
        return

    store = hass.data.get(DOMAIN, {}).get("mcp_token_store")
    if store is None:
        connection.send_error(msg["id"], "not_ready", "MCP token store not initialized")
        return

    revoked = await store.async_revoke_token(msg["token_id"])
    if not revoked:
        connection.send_error(msg["id"], "not_found", "Token not found")
        return

    connection.send_result(msg["id"], {"success": True})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/resolve_approval",
        vol.Required("session_id"): str,
        vol.Required("proposal_id"): str,
        vol.Required("scope"): vol.In(["once", "session", "always", "deny"]),
        # Per-entity vs wildcard recording of Session/Always grants:
        # - "this": grant only for the entities in this proposal
        #   (default; least-privilege).
        # - "all":  grant the service wildcard for any future entity.
        # Ignored for ``once``/``deny`` scopes.
        vol.Optional("entity_scope", default="this"): vol.In(["this", "all"]),
        vol.Optional("language"): vol.Any(str, None),
    }
)
async def _handle_websocket_resolve_approval(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Resolve a pending command approval (Once / Session / Always / Deny).

    On allow-* scopes we execute the proposal's calls server-side; the
    LLM is not involved in the second leg, so a denied call never runs
    and an approved call can't be silently rewritten between display
    and execution. The audit trail lives on the persisted message
    (approval_status + executed list).
    """
    if not _require_admin(connection, msg):
        return

    store: ConversationStore = hass.data[DOMAIN].setdefault("_conv_store", ConversationStore(hass))
    approval_store = hass.data.get(DOMAIN, {}).get("_approval_store")
    if approval_store is None:
        connection.send_error(msg["id"], "not_ready", "Approval store not initialized")
        return

    session_id = msg["session_id"]
    proposal_id = msg["proposal_id"]
    scope = msg["scope"]
    entity_scope = msg.get("entity_scope", "this")

    # Reject duplicate concurrent clicks BEFORE the first await. The
    # frontend has its own guard, but only this synchronous check
    # protects against rapid double-clicks that both reach the server
    # while the first is mid-execution.
    if proposal_id in _in_flight_approvals:
        connection.send_error(msg["id"], "in_flight", "Approval is already being processed")
        return
    _in_flight_approvals.add(proposal_id)
    try:
        await _resolve_approval(
            hass,
            connection,
            msg,
            store,
            approval_store,
            session_id,
            proposal_id,
            scope,
            entity_scope,
            language=msg.get("language"),
        )
    finally:
        _in_flight_approvals.discard(proposal_id)


@websocket_api.async_response
@decorators.websocket_command({vol.Required("type"): "selora_ai/list_approvals"})
async def _handle_websocket_list_approvals(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return all persistent ('Always') approvals for the Manage UI.

    Enriches each grant with the granting user's display name so the
    Settings list shows "granted by Phil 11m ago" instead of an opaque
    user_id. HA installs commonly have multiple users (family members
    sharing a system), so attributing the auto-approval to a specific
    account matters — otherwise revoking is a blind action.
    """
    if not _require_admin(connection, msg):
        return
    approval_store = hass.data.get(DOMAIN, {}).get("_approval_store")
    if approval_store is None:
        connection.send_error(msg["id"], "not_ready", "Approval store not initialized")
        return
    grants = await approval_store.async_list_grants()

    # Resolve user_id → name once per unique id. Falls back to a short
    # id prefix when the user has been deleted (so the row still
    # carries SOME attribution rather than dropping the field).
    enriched: list[dict[str, Any]] = []
    name_cache: dict[str, str] = {}
    for grant in grants:
        out = dict(grant)
        user_id = grant.get("granted_by_user_id")
        if user_id:
            name = name_cache.get(user_id)
            if name is None:
                user = await hass.auth.async_get_user(user_id)
                name = (user.name if user and user.name else None) or f"user {user_id[:8]}"
                name_cache[user_id] = name
            out["granted_by_name"] = name
        enriched.append(out)

    connection.send_result(msg["id"], {"grants": enriched})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/revoke_approval",
        # ``key`` is the full grant identifier — ``service`` for a
        # wildcard or ``service:entity_id`` for a per-entity grant.
        # The legacy ``service`` field is still accepted for one
        # release so older bundled frontends continue to work; new
        # callers should use ``key``.
        vol.Exclusive("key", "approval_identifier"): str,
        vol.Exclusive("service", "approval_identifier"): str,
    }
)
async def _handle_websocket_revoke_approval(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Revoke a persistent approval by its grant key.

    The ``list_approvals`` response includes a ``key`` field on each
    grant; revoke passes that same string back. Per-entity grants
    revoke just that pair, leaving any service wildcard intact.
    """
    if not _require_admin(connection, msg):
        return
    approval_store = hass.data.get(DOMAIN, {}).get("_approval_store")
    if approval_store is None:
        connection.send_error(msg["id"], "not_ready", "Approval store not initialized")
        return
    grant_key = msg.get("key") or msg.get("service")
    if not grant_key:
        connection.send_error(msg["id"], "invalid_params", "Missing 'key' or 'service'")
        return
    revoked = await approval_store.async_revoke(grant_key)
    if not revoked:
        connection.send_error(msg["id"], "not_found", "No persistent approval for that key")
        return
    connection.send_result(msg["id"], {"success": True})


def async_register(hass: HomeAssistant) -> None:
    """Register the tokens websocket commands."""
    from homeassistant.components import websocket_api

    websocket_api.async_register_command(hass, _handle_websocket_create_mcp_token)
    websocket_api.async_register_command(hass, _handle_websocket_list_mcp_tokens)
    websocket_api.async_register_command(hass, _handle_websocket_revoke_mcp_token)
    websocket_api.async_register_command(hass, _handle_websocket_resolve_approval)
    websocket_api.async_register_command(hass, _handle_websocket_list_approvals)
    websocket_api.async_register_command(hass, _handle_websocket_revoke_approval)
