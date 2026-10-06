"""MCP tools for updates: what can be updated, and an update's release notes."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant


async def _tool_list_updates(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """See ``update_manager``."""
    from ..tool_executor import _opt_bool  # noqa: PLC0415
    from ..update_manager import async_list_updates  # noqa: PLC0415

    return async_list_updates(
        hass, include_skipped=_opt_bool(arguments.get("include_skipped")) is True
    )


async def _tool_get_release_notes(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """See ``update_manager``."""
    from ..update_manager import async_release_notes  # noqa: PLC0415

    return await async_release_notes(hass, str(arguments.get("entity_id") or ""))
