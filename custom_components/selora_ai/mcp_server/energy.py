"""MCP tools for the Energy dashboard's configuration: read it, change it."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant


async def _tool_get_energy_prefs(hass: HomeAssistant, _arguments: dict[str, Any]) -> dict[str, Any]:
    """The configuration, its issues and its hash — see ``energy_prefs``."""
    from ..energy_prefs import async_get_energy_prefs  # noqa: PLC0415

    return await async_get_energy_prefs(hass)


async def _tool_set_energy_prefs(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Replace the lists given, if nothing changed since the read."""
    from ..energy_prefs import async_set_energy_prefs  # noqa: PLC0415

    config_hash = arguments.get("config_hash")
    return await async_set_energy_prefs(
        hass, arguments, str(config_hash) if config_hash not in (None, "") else None
    )
