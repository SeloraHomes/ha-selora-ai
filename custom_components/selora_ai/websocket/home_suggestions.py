"""Selora AI websocket handlers: suggestions from how the home is organised.

The panel lists them on the chat home and dismisses the ones the user turns
down. Acting on one is an ordinary chat request; see ``home_suggestions``.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import decorators
from homeassistant.core import HomeAssistant
import voluptuous as vol

from .. import _require_admin
from ..home_suggestions import async_dismiss_home_suggestion, async_home_suggestions


@websocket_api.async_response
@decorators.websocket_command({vol.Required("type"): "selora_ai/home_suggestions"})
async def _handle_websocket_home_suggestions(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the current home suggestions."""
    if not _require_admin(connection, msg):
        return
    connection.send_result(msg["id"], await async_home_suggestions(hass))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/home_suggestions/dismiss",
        vol.Required("suggestion_id"): str,
    }
)
async def _handle_websocket_dismiss_home_suggestion(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Stop offering one home suggestion."""
    if not _require_admin(connection, msg):
        return
    await async_dismiss_home_suggestion(hass, msg["suggestion_id"])
    connection.send_result(msg["id"], {"dismissed": True})


def async_register(hass: HomeAssistant) -> None:
    """Register the home-suggestion websocket commands."""
    websocket_api.async_register_command(hass, _handle_websocket_home_suggestions)
    websocket_api.async_register_command(hass, _handle_websocket_dismiss_home_suggestion)
