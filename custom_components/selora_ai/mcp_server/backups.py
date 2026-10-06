"""MCP tool for the home's backups and how automatic backups are doing."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant


async def _tool_get_backups(hass: HomeAssistant, _arguments: dict[str, Any]) -> dict[str, Any]:
    """See ``backup_status``."""
    from ..backup_status import async_backup_status  # noqa: PLC0415

    return await async_backup_status(hass)
