"""Selora AI websocket handlers: Recently deleted (trash.py)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import decorators
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import voluptuous as vol

from .. import _require_admin
from ..trash import (
    TrashRestoreError,
    async_delete_forever,
    async_restore,
    candidate,
    expires_at,
    get_trash_store,
)

_LOGGER = logging.getLogger(__name__)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/trash/list",
        vol.Optional("kind"): vol.In(["automation", "scene"]),
    }
)
async def _handle_trash_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """The deleted items still kept, newest first. Not the entries themselves."""
    if not _require_admin(connection, msg):
        return
    kind = msg.get("kind")
    items = await get_trash_store(hass).async_list()
    connection.send_result(
        msg["id"],
        [
            {
                "trash_id": item["trash_id"],
                "kind": item["kind"],
                "name": item["name"],
                "description": item["entry"].get("description") or "",
                "deleted_at": item["deleted_at"],
                "expires_at": expires_at(item),
            }
            for item in items
            if kind is None or item["kind"] == kind
        ],
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/trash/get",
        vol.Required("trash_ids"): [str],
    }
)
async def _handle_trash_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Items in full, for a chat restore card; one no longer kept is left out."""
    if not _require_admin(connection, msg):
        return
    wanted = set(msg["trash_ids"])
    items = await get_trash_store(hass).async_list()
    connection.send_result(
        msg["id"],
        {"items": [candidate(i) for i in items if i["trash_id"] in wanted]},
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/trash/restore",
        vol.Required("trash_id"): str,
    }
)
async def _handle_trash_restore(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Put a deleted automation or scene back."""
    if not _require_admin(connection, msg):
        return
    try:
        item = await async_restore(hass, msg["trash_id"])
    except TrashRestoreError as exc:
        connection.send_error(msg["id"], exc.code, str(exc))
        return
    except (HomeAssistantError, OSError) as exc:
        _LOGGER.warning("Failed to restore %s: %s", msg["trash_id"], exc)
        connection.send_error(msg["id"], "restore_failed", str(exc))
        return
    connection.send_result(msg["id"], {"kind": item["kind"], "name": item["name"]})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/trash/delete",
        vol.Required("trash_id"): str,
    }
)
async def _handle_trash_delete(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete an item for good, before its time is up."""
    if not _require_admin(connection, msg):
        return
    if not await async_delete_forever(hass, msg["trash_id"]):
        connection.send_error(msg["id"], "not_found", "Not in Recently deleted")
        return
    connection.send_result(msg["id"], {"status": "deleted"})


def async_register(hass: HomeAssistant) -> None:
    """Register the trash websocket commands."""
    websocket_api.async_register_command(hass, _handle_trash_list)
    websocket_api.async_register_command(hass, _handle_trash_get)
    websocket_api.async_register_command(hass, _handle_trash_restore)
    websocket_api.async_register_command(hass, _handle_trash_delete)
