"""WebSocket command registration for the Selora AI panel.

Handlers live in per-domain modules in this package. Registration is
centralized here and invoked lazily from ``async_setup`` so this package
imports only after the integration package has finished loading (which is
what lets the domain modules pull shared helpers via ``from .. import``
without an import cycle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components import websocket_api

from . import (
    automations,
    devices,
    insights,
    linking,
    scenes,
    sessions,
    suggestions,
    tokens,
    usage,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register every Selora AI websocket command on ``hass``."""
    # Chat handlers remain in the package root (deeply coupled to the chat
    # orchestration helpers there); register them directly.
    from .. import _handle_websocket_chat, _handle_websocket_chat_stream

    websocket_api.async_register_command(hass, _handle_websocket_chat)
    websocket_api.async_register_command(hass, _handle_websocket_chat_stream)

    # Per-domain handler modules.
    automations.async_register(hass)
    scenes.async_register(hass)
    sessions.async_register(hass)
    suggestions.async_register(hass)
    insights.async_register(hass)
    usage.async_register(hass)
    linking.async_register(hass)
    tokens.async_register(hass)
    devices.async_register(hass)

    # HA-mediated OAuth link (works inside Companion app WebViews).
    from ..oauth_link import async_register as _register_oauth_link

    _register_oauth_link(hass)
