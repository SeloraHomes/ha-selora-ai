"""MCP tools for integrations (config entries): list, reload, enable or disable,
remove, change options."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant


async def _tool_list_integrations(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Config entries with their state — see ``integration_manager``."""
    from ..integration_manager import async_list_integrations  # noqa: PLC0415
    from ..tool_executor import _opt_bool, _opt_str  # noqa: PLC0415

    return await async_list_integrations(
        hass,
        _opt_str(arguments.get("domain")),
        problems_only=_opt_bool(arguments.get("problems_only")) is True,
    )


async def _tool_reload_integration(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    from ..integration_manager import async_reload_integration  # noqa: PLC0415

    return await async_reload_integration(hass, str(arguments.get("entry_id") or ""))


async def _tool_set_integration_enabled(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    from ..integration_manager import async_set_integration_enabled  # noqa: PLC0415
    from ..tool_executor import _opt_bool  # noqa: PLC0415

    enabled = _opt_bool(arguments.get("enabled"))
    if enabled is None:
        return {"error": "Pass enabled=true or enabled=false."}
    return await async_set_integration_enabled(hass, str(arguments.get("entry_id") or ""), enabled)


async def _tool_remove_integration(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    from ..integration_manager import async_remove_integration  # noqa: PLC0415
    from ..tool_executor import _opt_bool  # noqa: PLC0415

    return await async_remove_integration(
        hass,
        str(arguments.get("entry_id") or ""),
        confirmed=_opt_bool(arguments.get("confirmed")) is True,
    )


async def _tool_set_integration_options(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    from ..integration_manager import async_integration_options  # noqa: PLC0415
    from ..tool_executor import _opt_str  # noqa: PLC0415

    options = arguments.get("options")
    return await async_integration_options(
        hass,
        str(arguments.get("entry_id") or ""),
        _opt_str(arguments.get("type")),
        options if isinstance(options, dict) else None,
    )
