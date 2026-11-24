"""Selora AI websocket handlers: temporary browser-code access (``file_access``).

Deciding, revoking and dismissing need an admin on a LOGIN session — the browser or the
companion app. A long-lived access token is refused: an agent connected with
one could otherwise approve its own request, which is the one thing the grant
exists to prevent. An installer's request is listed to, and answered by, only
the support user it names; to anyone else it does not exist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.auth.models import TOKEN_TYPE_NORMAL
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import decorators
import voluptuous as vol

from .. import _require_admin
from ..const import DOMAIN
from ..file_access import MINUTE_CHOICES, FileAccessError, FileAccessManager
from ..recipes.resources import async_registered_urls

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _manager(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> FileAccessManager | None:
    manager = hass.data.get(DOMAIN, {}).get("file_access")
    if manager is None:
        connection.send_error(msg["id"], "not_ready", "Selora AI is still starting")
    return manager


def _require_visible(
    manager: FileAccessManager,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    record_id: str,
) -> bool:
    if manager.can_see(record_id, connection.user):
        return True
    connection.send_error(msg["id"], "not_found", "That access does not exist any more.")
    return False


def _require_login_session(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> bool:
    token_id = getattr(connection, "refresh_token_id", None)
    token = hass.auth.async_get_refresh_token(token_id) if token_id else None
    if token is not None and token.token_type == TOKEN_TYPE_NORMAL:
        return True
    connection.send_error(
        msg["id"],
        "login_session_required",
        "Approve from the Home Assistant app or a browser login, not with an access token.",
    )
    return False


@websocket_api.async_response
@decorators.websocket_command({vol.Required("type"): "selora_ai/file_access/list"})
async def _handle_list(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Pending requests, and grants live or awaiting dismissal."""
    if not _require_admin(connection, msg) or (manager := _manager(hass, connection, msg)) is None:
        return
    connection.send_result(
        msg["id"],
        _with_loaded(manager.snapshot(connection.user), await async_registered_urls(hass)),
    )


def _with_loaded(snapshot: dict[str, Any], resources: set[str] | None) -> dict[str, Any]:
    """Mark each written file a dashboard resource loads (``loaded``).

    The panel says what a file is for: one the dashboards load runs on every
    one of them; one they don't is a library another file imports. Unknown
    (resources unreadable) leaves the flag out. Copies, never the stored
    records.
    """
    if resources is None:
        return snapshot
    for grant in snapshot["grants"]:
        grant["writes"] = [
            {**w, "loaded": w["url"] in resources} if w.get("url") else w
            for w in grant.get("writes", [])
        ]
    return snapshot


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/file_access/decide",
        vol.Required("request_id"): str,
        vol.Required("approve"): bool,
        vol.Optional("minutes"): vol.In(MINUTE_CHOICES),
    }
)
async def _handle_decide(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Approve or deny a request."""
    if not _require_admin(connection, msg) or not _require_login_session(hass, connection, msg):
        return
    if (manager := _manager(hass, connection, msg)) is None:
        return
    if not _require_visible(manager, connection, msg, msg["request_id"]):
        return
    user = connection.user
    try:
        grant = await manager.async_decide(
            msg["request_id"],
            msg["approve"],
            msg.get("minutes"),
            user.id,
            user.name or user.id,
        )
    except FileAccessError as exc:
        connection.send_error(msg["id"], exc.code, str(exc))
        return
    connection.send_result(msg["id"], {"grant": grant} if grant else {"status": "denied"})


@websocket_api.async_response
@decorators.websocket_command(
    {vol.Required("type"): "selora_ai/file_access/revoke", vol.Required("grant_id"): str}
)
async def _handle_revoke(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """End a grant now."""
    if not _require_admin(connection, msg) or not _require_login_session(hass, connection, msg):
        return
    if (manager := _manager(hass, connection, msg)) is None:
        return
    if not _require_visible(manager, connection, msg, msg["grant_id"]):
        return
    try:
        grant = await manager.async_revoke(msg["grant_id"])
    except FileAccessError as exc:
        connection.send_error(msg["id"], exc.code, str(exc))
        return
    connection.send_result(msg["id"], {"grant": grant})


@websocket_api.async_response
@decorators.websocket_command(
    {vol.Required("type"): "selora_ai/file_access/dismiss", vol.Required("grant_id"): str}
)
async def _handle_dismiss(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Hide an ended grant's summary — from a login session, like deciding.

    An agent holding the user's token must not be able to clear the record of
    what it wrote.
    """
    if not _require_admin(connection, msg) or not _require_login_session(hass, connection, msg):
        return
    if (manager := _manager(hass, connection, msg)) is None:
        return
    if not _require_visible(manager, connection, msg, msg["grant_id"]):
        return
    try:
        await manager.async_dismiss(msg["grant_id"])
    except FileAccessError as exc:
        connection.send_error(msg["id"], exc.code, str(exc))
        return
    connection.send_result(msg["id"], {"ok": True})


def async_register(hass: HomeAssistant) -> None:
    """Register the file-access commands."""
    websocket_api.async_register_command(hass, _handle_list)
    websocket_api.async_register_command(hass, _handle_decide)
    websocket_api.async_register_command(hass, _handle_revoke)
    websocket_api.async_register_command(hass, _handle_dismiss)
