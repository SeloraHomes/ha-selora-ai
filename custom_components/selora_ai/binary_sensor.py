"""Binary sensor platform — whether the Health view has a device problem."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)

from .const import HEALTH_PROBLEM_UNIQUE_ID
from .sensor import HealthIssuesEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([HealthProblemBinarySensor(hass)])


class HealthProblemBinarySensor(HealthIssuesEntity, BinarySensorEntity):
    """On while any confirmed health issue is open."""

    _attr_unique_id = HEALTH_PROBLEM_UNIQUE_ID
    _attr_name = "Health problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool:
        return bool(self._issues)
