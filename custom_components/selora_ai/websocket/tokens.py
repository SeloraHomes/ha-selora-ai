"""Selora AI websocket handlers: tokens.

Extracted from __init__.py. Handlers reach shared integration
helpers via ``from .. import`` (safe: this module is imported
lazily at registration time, after the package has loaded).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import decorators
from homeassistant.core import HomeAssistant
import voluptuous as vol

from .. import (
    _find_pending_approval,
    _in_flight_approvals,
    _require_admin,
    _resolve_approval,
)
from ..const import (
    DOMAIN,
)
from ..conversation_store import ConversationStore
from ..dashboard_manager import async_initialize_created_dashboard
from ..helpers import caller_scope, sanitize_untrusted_text
from ..llm_client.command_policy import (
    _done_text,
    action_failed_line,
    dashboard_created_line,
    dashboard_deleted_line,
)

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
        # ``delete`` / ``cancel`` are the delete-confirmation-card scopes
        # (approval_kind == "delete"); the rest drive service-call approvals.
        vol.Required("scope"): vol.In(["once", "session", "always", "deny", "delete", "cancel"]),
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
        # The second leg runs long after the ToolExecutor scope that built the
        # card has ended, so CALLER_IS_ADMIN has reverted to its deny-by-default
        # False. Anything the confirmation executes that carries a per-object
        # admin check would then be refused on behalf of the very admin who just
        # tapped confirm — a require_admin dashboard's view removal failing as
        # "No dashboard". Re-established from the connection rather than
        # hardcoded True: _require_admin above means it can only be True today,
        # and reading it keeps that a fact about the gate rather than an
        # assumption baked in here.
        user = getattr(connection, "user", None)
        with caller_scope(bool(getattr(user, "is_admin", False))):
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
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/client_action_result",
        vol.Required("session_id"): str,
        vol.Required("proposal_id"): str,
        vol.Required("results"): list,
        vol.Optional("language"): str,
    }
)
async def _handle_websocket_client_action_result(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Record what the panel's own execution actually did.

    The panel performs `lovelace/dashboards/create` itself — the integration
    cannot — so this is the only way the conversation learns the outcome.
    Without it the card resolves in the UI while the transcript still implies
    nothing happened, or worse, that it succeeded when it did not.

    Only the outcome is stored. The panel already ran the command; there is
    nothing to replay here and nothing here that grants access.
    """
    if not _require_admin(connection, msg):
        return

    # Reserved BEFORE the first await, like the approval resolver: two tabs, or
    # a double click, would otherwise both pass the lookup below and append
    # contradictory outcomes while racing the status write.
    proposal_id = msg["proposal_id"]
    if proposal_id in _in_flight_approvals:
        connection.send_error(msg["id"], "in_flight", "That result is already being recorded")
        return
    _in_flight_approvals.add(proposal_id)
    try:
        await _record_client_action_result(hass, connection, msg)
    finally:
        _in_flight_approvals.discard(proposal_id)


async def _record_client_action_result(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Inner recorder — runs inside the in-flight guard."""
    store: ConversationStore = hass.data[DOMAIN].setdefault("_conv_store", ConversationStore(hass))

    # By proposal_id, not by position: pruning between the panel's execution and
    # this arrival shifts message indices, and `set_approval_status` addresses
    # the message by index. Same lookup the delete resolver uses.
    session = await store.get_session(msg["session_id"])
    located = _find_pending_approval(session, msg["proposal_id"])
    if located is None:
        connection.send_error(
            msg["id"], "not_found", "Approval proposal not found or already resolved"
        )
        return
    message_index, message = located

    # This endpoint resolves ONE kind of card. `_find_pending_approval` returns
    # any pending proposal, so without this an authenticated admin could mark a
    # service-call or deletion card approved — appending a fabricated outcome
    # for work nothing performed. The panel is trusted to report faithfully;
    # it is not trusted to say which approval it was reporting on.
    approval = message.get("command_approval")
    approval = approval if isinstance(approval, dict) else {}
    if approval.get("approval_kind") != "client_action":
        connection.send_error(msg["id"], "wrong_kind", "That proposal is not resolved by the panel")
        return

    results = [r for r in msg["results"] if isinstance(r, dict)]

    # And the results must answer the actions this card actually proposed —
    # otherwise the transcript records an outcome for something never offered.
    expected = [
        str(a.get("kind", "")) for a in approval.get("client_actions", []) if isinstance(a, dict)
    ]
    if [str(r.get("kind", "")) for r in results] != expected:
        connection.send_error(
            msg["id"], "mismatch", "Those results do not match what this card proposed"
        )
        return

    ok = bool(results) and all(bool(r.get("ok")) for r in results)

    # The panel sends the language it has been answering in, exactly as
    # resolve_approval does — these lines are deterministic and built here, so
    # nothing else would make them match the rest of the conversation.
    language = msg.get("language")
    lines: list[str] = []
    for result in results:
        detail = result.get("detail")
        if (
            result.get("ok")
            and isinstance(detail, dict)
            and str(result.get("kind")) == "delete_dashboard"
        ):
            # No seeding, no card marker: the page is gone, and a link to it
            # would be a link to nothing.
            lines.append(
                dashboard_deleted_line(
                    sanitize_untrusted_text(str(detail.get("title") or ""), 60),
                    language,
                )
            )
        elif result.get("ok") and isinstance(detail, dict):
            # The entry exists now; the DOCUMENT does not, and storage reports a
            # dashboard with no document as `auto-gen` — indistinguishable from a
            # generated Overview. Every write would be refused with the Take
            # control note, on the dashboard we were just asked to make. Seeding
            # an empty document here is what makes the next request able to fill
            # it. Never overwrites, so a re-report cannot blank it.
            if url_path := str(detail.get("url_path") or "").strip():
                await async_initialize_created_dashboard(hass, url_path)
            lines.append(
                dashboard_created_line(
                    sanitize_untrusted_text(str(detail.get("title") or ""), 60),
                    url_path,
                    language,
                )
            )
            # The card that takes the user to it. This outcome is written HERE,
            # not by the synthesizer — the panel performed the work and no LLM
            # turn produced this line — so the marker every other dashboard
            # answer gets has to be added here too, or the one reply that
            # announces a brand-new dashboard is the one with no way in.
            if url_path:
                lines.append(_dashboard_card_marker(url_path, str(detail.get("title") or "")))
        elif result.get("ok"):
            lines.append(_done_text(language))
        else:
            lines.append(action_failed_line(sanitize_untrusted_text(str(detail), 200), language))

    # Status FIRST. `append_message` prunes a middle message once the session
    # hits its cap, which shifts the index located above — the write would then
    # land on the wrong message, usually the result we just appended, leaving
    # the card itself pending forever.
    await store.set_approval_status(
        msg["session_id"], message_index, "approved" if ok else "denied"
    )
    await store.append_message(
        msg["session_id"],
        "assistant",
        "\n".join(lines) or "Nothing to do.",
    )
    connection.send_result(msg["id"], {"ok": ok})


def _dashboard_card_marker(url_path: str, title: str) -> str:
    """The panel's dashboard-card marker for a freshly created dashboard.

    Same shape `append_dashboard_link` emits, so both paths render the same
    element. `url_path` is the validated slug the proposal carried, so the
    leading slash makes it the absolute, same-origin path the panel requires;
    the title is whatever the user named it, and the marker delimiters are
    stripped out of it.
    """
    label = re.sub(r"[\[\]|]", "", title).strip()[:60]
    return f"[[dashboard:/{url_path}|{label}]]" if label else f"[[dashboard:/{url_path}]]"


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
    websocket_api.async_register_command(hass, _handle_websocket_client_action_result)
    websocket_api.async_register_command(hass, _handle_websocket_list_approvals)
    websocket_api.async_register_command(hass, _handle_websocket_revoke_approval)
