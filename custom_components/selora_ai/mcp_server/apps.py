"""MCP tools for apps (formerly add-ons): list them, read one's logs."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant


async def _tool_list_apps(hass: HomeAssistant, _arguments: dict[str, Any]) -> dict[str, Any]:
    """See ``apps_manager``."""
    from ..apps_manager import async_list_apps  # noqa: PLC0415

    return async_list_apps(hass)


async def _tool_get_app_logs(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """See ``apps_manager``."""
    from ..apps_manager import async_app_logs  # noqa: PLC0415

    return await async_app_logs(hass, str(arguments.get("slug") or ""), arguments.get("lines"))
