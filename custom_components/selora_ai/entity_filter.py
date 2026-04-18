"""Pre-computed entity-registry lookups for disabled / same-device filtering."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from homeassistant.helpers import entity_registry as er

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class EntityFilter:
    """One-pass registry scan that answers is_active / same_device queries.

    Constructed once per detection run so the entity registry is read exactly
    once, then every call-site can filter cheaply without re-fetching.
    """

    def __init__(self, hass: HomeAssistant, entity_ids: Iterable[str]) -> None:
        registry = er.async_get(hass)
        self._disabled: set[str] = set()
        self._device_ids: dict[str, str | None] = {}
        for eid in entity_ids:
            entry = registry.async_get(eid)
            self._device_ids[eid] = entry.device_id if entry else None
            if entry and entry.disabled:
                self._disabled.add(eid)

    def is_active(self, entity_id: str) -> bool:
        """Return True if the entity is not disabled."""
        return entity_id not in self._disabled

    def same_device(self, entity_a: str, entity_b: str) -> bool:
        """Return True if both entities belong to the same physical device."""
        dev_a = self._device_ids.get(entity_a)
        dev_b = self._device_ids.get(entity_b)
        return bool(dev_a and dev_b and dev_a == dev_b)
