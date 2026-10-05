"""MCP tools for calendar events: list them with their uid, add, change, remove."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant


async def _tool_list_calendar_events(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Events in a window — see ``calendar_manager``."""
    from ..calendar_manager import async_list_events  # noqa: PLC0415

    return await async_list_events(
        hass, str(arguments.get("entity_id", "")), arguments.get("start"), arguments.get("end")
    )


async def _tool_set_calendar_event(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Add an event, or replace the one named by uid."""
    from ..calendar_manager import async_set_event  # noqa: PLC0415

    return await async_set_event(
        hass,
        str(arguments.get("entity_id", "")),
        arguments,
        uid=arguments.get("uid"),
        recurrence_id=arguments.get("recurrence_id"),
        recurrence_range=arguments.get("recurrence_range"),
    )


async def _tool_delete_calendar_event(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Remove an event, or some occurrences of a recurring one."""
    from ..calendar_manager import async_delete_event  # noqa: PLC0415

    return await async_delete_event(
        hass,
        str(arguments.get("entity_id", "")),
        str(arguments.get("uid") or ""),
        recurrence_id=arguments.get("recurrence_id"),
        recurrence_range=arguments.get("recurrence_range"),
    )
