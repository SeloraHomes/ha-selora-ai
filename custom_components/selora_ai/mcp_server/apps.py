"""MCP tools for apps (formerly add-ons): list, logs, find, install, options."""

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


async def _tool_search_app_store(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """See ``apps_manager``."""
    from ..apps_manager import async_search_app_store  # noqa: PLC0415

    return await async_search_app_store(hass, str(arguments.get("query") or ""))


async def _tool_install_app(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """See ``apps_manager``."""
    from ..apps_manager import async_install_app  # noqa: PLC0415
    from ..tool_executor import _opt_bool, _opt_str  # noqa: PLC0415

    return await async_install_app(
        hass,
        str(arguments.get("slug") or ""),
        confirmed=_opt_bool(arguments.get("confirmed")) is True,
        fingerprint=_opt_str(arguments.get("fingerprint")),
    )


async def _tool_get_app_options(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """See ``apps_manager``."""
    from ..apps_manager import async_get_app_options  # noqa: PLC0415

    return await async_get_app_options(hass, str(arguments.get("slug") or ""))


async def _tool_set_app_options(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """See ``apps_manager``."""
    from ..apps_manager import async_set_app_options  # noqa: PLC0415
    from ..tool_executor import _opt_options  # noqa: PLC0415

    return await async_set_app_options(
        hass, str(arguments.get("slug") or ""), _opt_options(arguments.get("options"))
    )
