"""Scene discovery helpers -- area lookup for scene creation context."""

from __future__ import annotations

from homeassistant.core import HomeAssistant


async def get_area_names(hass: HomeAssistant) -> list[str]:
    """Return all area names from the HA area registry."""
    from homeassistant.helpers import area_registry as ar

    area_reg = ar.async_get(hass)
    return [area.name for area in area_reg.async_list_areas()]
