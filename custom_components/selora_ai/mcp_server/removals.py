"""MCP tools that remove a device or an entity, after a confirmation."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant


async def _tool_remove_device(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """See ``registry_removal``."""
    from ..registry_removal import async_remove_device_on_request  # noqa: PLC0415
    from ..tool_executor import _opt_bool  # noqa: PLC0415

    return await async_remove_device_on_request(
        hass,
        str(arguments.get("device_id") or ""),
        confirmed=_opt_bool(arguments.get("confirmed")) is True,
    )


async def _tool_remove_entity(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """See ``registry_removal``."""
    from ..registry_removal import async_remove_entity_on_request  # noqa: PLC0415
    from ..tool_executor import _opt_bool, _opt_str  # noqa: PLC0415

    return await async_remove_entity_on_request(
        hass,
        str(arguments.get("entity_id") or ""),
        confirmed=_opt_bool(arguments.get("confirmed")) is True,
        registry_id=_opt_str(arguments.get("registry_id")),
    )
