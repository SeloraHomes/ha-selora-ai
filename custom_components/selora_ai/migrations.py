"""Config entry migrations for Selora AI."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .const import (
    CONF_COLLECTOR_INTERVAL,
    DEFAULT_COLLECTOR_INTERVAL,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry to a new version."""
    if entry.version == 1:
        _migrate_v1_to_v2(hass, entry)
    return True


def _migrate_v1_to_v2(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """V1→V2: update collector interval from old 1h default to 4h."""
    opts = dict(entry.options)
    old_default_interval = 3600
    if opts.get(CONF_COLLECTOR_INTERVAL) == old_default_interval:
        opts[CONF_COLLECTOR_INTERVAL] = DEFAULT_COLLECTOR_INTERVAL
        _LOGGER.info(
            "Migrating collector interval from %ds to %ds",
            old_default_interval,
            DEFAULT_COLLECTOR_INTERVAL,
        )
    hass.config_entries.async_update_entry(entry, options=opts, version=2)
