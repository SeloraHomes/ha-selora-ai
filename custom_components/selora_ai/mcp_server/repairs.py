"""MCP tools for Home Assistant's repairs: list, ignore, fix."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant


async def _tool_list_repairs(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """See ``repairs_manager``."""
    from ..repairs_manager import async_list_repairs  # noqa: PLC0415
    from ..tool_executor import _opt_bool  # noqa: PLC0415

    return await async_list_repairs(
        hass, include_ignored=_opt_bool(arguments.get("include_ignored")) is True
    )


async def _tool_ignore_repair(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    from ..repairs_manager import async_ignore_repair  # noqa: PLC0415
    from ..tool_executor import _opt_bool  # noqa: PLC0415

    return async_ignore_repair(
        hass,
        str(arguments.get("domain") or ""),
        str(arguments.get("issue_id") or ""),
        ignore=_opt_bool(arguments.get("ignore")) is not False,
    )


async def _tool_fix_repair(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    from ..repairs_manager import async_fix_repair  # noqa: PLC0415
    from ..tool_executor import _opt_str  # noqa: PLC0415

    fields = arguments.get("fields")
    return await async_fix_repair(
        hass,
        str(arguments.get("domain") or ""),
        str(arguments.get("issue_id") or ""),
        flow_id=_opt_str(arguments.get("flow_id")),
        fields=fields if isinstance(fields, dict) else None,
        choice=_opt_str(arguments.get("choice")),
    )
