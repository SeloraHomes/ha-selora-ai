"""MCP tools for HACS: search, details, install/update, remove, custom repositories."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant


async def _tool_hacs_search(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Repositories HACS knows, matched — see ``hacs_bridge``."""
    from ..hacs_bridge import async_search  # noqa: PLC0415
    from ..tool_executor import _opt_bool, _opt_str  # noqa: PLC0415

    return await async_search(
        hass,
        _opt_str(arguments.get("query")),
        _opt_str(arguments.get("category")),
        _opt_bool(arguments.get("installed_only")) is True,
    )


async def _tool_hacs_info(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """One repository's details."""
    from ..hacs_bridge import async_info  # noqa: PLC0415

    return await async_info(hass, str(arguments.get("repository", "")))


async def _tool_hacs_install(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Install or update a repository, once the user confirmed it."""
    from ..hacs_bridge import async_download  # noqa: PLC0415
    from ..tool_executor import _opt_bool, _opt_str  # noqa: PLC0415

    return await async_download(
        hass,
        str(arguments.get("repository", "")),
        _opt_str(arguments.get("version")),
        confirmed=_opt_bool(arguments.get("confirmed")) is True,
    )


async def _tool_hacs_remove(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Uninstall a repository outright (MCP clients run their own confirmation)."""
    from ..hacs_bridge import async_remove  # noqa: PLC0415

    return await async_remove(hass, str(arguments.get("repository", "")))


async def _tool_hacs_add_repository(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Add a custom GitHub repository to HACS, once the user confirmed it."""
    from ..hacs_bridge import async_add_repository  # noqa: PLC0415
    from ..tool_executor import _opt_bool  # noqa: PLC0415

    return await async_add_repository(
        hass,
        str(arguments.get("repository", "")),
        str(arguments.get("category", "")).strip().lower(),
        confirmed=_opt_bool(arguments.get("confirmed")) is True,
    )
