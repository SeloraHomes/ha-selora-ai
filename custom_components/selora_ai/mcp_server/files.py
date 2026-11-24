"""MCP tools for files in www/, themes/, custom_templates/, dashboards/, blueprints/."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant


async def _tool_list_files(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """One folder's entries — see ``config_files``."""
    from ..config_files import async_list  # noqa: PLC0415
    from ..tool_executor import _opt_str  # noqa: PLC0415

    return await async_list(
        hass, str(arguments.get("folder", "")), _opt_str(arguments.get("pattern"))
    )


async def _tool_read_file(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """A text file, a chunk at a time."""
    from ..config_files import async_read  # noqa: PLC0415
    from ..tool_executor import _as_index, _opt_bool, _opt_str  # noqa: PLC0415

    return await async_read(
        hass,
        str(arguments.get("file", "")),
        _as_index(arguments.get("offset")),
        backups=_opt_bool(arguments.get("backups")) is True,
        backup=_opt_str(arguments.get("backup")),
    )


async def _tool_write_file(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create or (with overwrite) replace a text file."""
    from ..config_files import async_write  # noqa: PLC0415
    from ..tool_executor import _opt_bool, _opt_str  # noqa: PLC0415

    content = arguments.get("content")
    return await async_write(
        hass,
        str(arguments.get("file", "")),
        content if isinstance(content, str) else None,
        overwrite=_opt_bool(arguments.get("overwrite")) is True,
        from_backup=_opt_str(arguments.get("from_backup")),
    )


async def _tool_delete_file(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a text file outright (MCP clients run their own confirmation)."""
    from ..config_files import async_delete  # noqa: PLC0415

    return await async_delete(hass, str(arguments.get("file", "")))


async def _tool_request_file_access(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Ask the user to let this client write browser code to one www/ folder."""
    from ..const import DOMAIN  # noqa: PLC0415
    from ..file_access import FileAccessError  # noqa: PLC0415
    from ..helpers import CALLER_CREDENTIAL  # noqa: PLC0415

    manager = hass.data.get(DOMAIN, {}).get("file_access")
    if manager is None:
        return {"error": "Selora AI is still starting; try again in a moment."}
    raw_minutes = arguments.get("minutes")
    try:
        minutes = int(raw_minutes) if raw_minutes is not None else 60
    except (TypeError, ValueError):
        return {"error": "minutes must be a whole number from 15 to 60."}
    try:
        return await manager.async_request(
            CALLER_CREDENTIAL.get(),
            str(arguments.get("folder", "")),
            str(arguments.get("reason", "")),
            minutes,
        )
    except FileAccessError as exc:
        return {"error": str(exc)}
