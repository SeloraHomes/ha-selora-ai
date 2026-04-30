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

_V1_DEFAULT_COLLECTOR_INTERVAL = 3600


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry to a new version."""
    if entry.version == 1:
        _migrate_v1_to_v2(hass, entry)
    return True


def _migrate_v1_to_v2(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """V1→V2: migrate old default collector interval from 1h to 4h.

    If the stored interval is the old default (3600) or absent, update it
    to the new default (4h). Custom user-chosen values are preserved.
    """
    opts = dict(entry.options)
    current = opts.get(CONF_COLLECTOR_INTERVAL)
    if current is None or current == _V1_DEFAULT_COLLECTOR_INTERVAL:
        opts[CONF_COLLECTOR_INTERVAL] = DEFAULT_COLLECTOR_INTERVAL
        _LOGGER.info(
            "Migrating collector interval from %ds to %ds",
            _V1_DEFAULT_COLLECTOR_INTERVAL,
            DEFAULT_COLLECTOR_INTERVAL,
        )
    hass.config_entries.async_update_entry(entry, options=opts, version=2)
