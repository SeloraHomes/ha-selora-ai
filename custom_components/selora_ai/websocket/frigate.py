"""Selora AI websocket handlers: adding cameras to Frigate, and rescanning for them.

The card's camera logins come here straight from the panel, so they never
reach the model or the conversation; see ``frigate.setup``.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import decorators
from homeassistant.core import HomeAssistant
import voluptuous as vol

from .. import _require_admin
from ..frigate.discovery import async_scan
from ..frigate.ideas import async_camera_ideas
from ..frigate.setup import async_add_cameras_confirmed
from ..home_suggestions import get_home_suggestion_store

_LOGIN = vol.Schema(
    {
        vol.Required("username"): vol.All(str, vol.Length(max=128)),
        vol.Required("password"): vol.All(str, vol.Length(max=256)),
    }
)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/frigate/add_cameras",
        vol.Required("plan_id"): vol.All(str, vol.Length(min=1, max=64)),
        vol.Optional("credentials", default={}): vol.Schema({str: _LOGIN}),
    }
)
async def _handle_websocket_frigate_add_cameras(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Add the cameras a confirmed card proposed, with the logins typed into it."""
    if not _require_admin(connection, msg):
        return
    connection.send_result(
        msg["id"],
        await async_add_cameras_confirmed(hass, msg["plan_id"], msg["credentials"]),
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/frigate/scan",
        vol.Optional("sweep", default=False): bool,
    }
)
async def _handle_websocket_frigate_scan(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Scan the network for cameras now instead of on the next timer.

    For developer mode: the scan otherwise runs a minute after start and then
    every half hour, which is a long wait while testing. ``sweep`` also asks
    every local address directly, which the timer does once a day.
    """
    if not _require_admin(connection, msg):
        return
    found = await async_scan(hass, sweep=bool(msg.get("sweep")))
    connection.send_result(
        msg["id"],
        {
            "cameras": [
                {"name": c.name, "host": c.host, "brand": c.brand, "source": c.source}
                for c in found
            ]
        },
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/frigate/ideas",
        vol.Required("camera_entity"): vol.All(str, vol.Length(min=1, max=255)),
    }
)
async def _handle_websocket_frigate_ideas(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Automation ideas for one camera, for its result card."""
    if not _require_admin(connection, msg):
        return
    dismissed = await get_home_suggestion_store(hass).ids("dismissed")
    ideas = await async_camera_ideas(hass, msg["camera_entity"])
    connection.send_result(
        msg["id"], {"ideas": [i for i in ideas if i["idea_id"] not in dismissed]}
    )


def async_register(hass: HomeAssistant) -> None:
    """Register the Frigate websocket commands."""
    websocket_api.async_register_command(hass, _handle_websocket_frigate_add_cameras)
    websocket_api.async_register_command(hass, _handle_websocket_frigate_scan)
    websocket_api.async_register_command(hass, _handle_websocket_frigate_ideas)
