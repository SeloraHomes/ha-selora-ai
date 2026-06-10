"""Pre-computed entity-registry lookups for disabled / same-device filtering."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def resolve_exclude_label_id(hass: HomeAssistant) -> str | None:
    """Look up the Selora exclude label by ID, then by case-insensitive name.

    Returns None when the label hasn't been created yet (e.g. label registry
    not initialised). Centralised so every caller — filter, listing, WS
    apply / remove — agrees on which label they match.
    """
    from .const import SELORA_EXCLUDE_LABEL_ID, SELORA_EXCLUDE_LABEL_NAME

    try:
        from homeassistant.helpers import label_registry as lr

        label_reg = lr.async_get(hass)
        label = label_reg.async_get_label(SELORA_EXCLUDE_LABEL_ID)
        if label is None:
            for candidate in label_reg.async_list_labels():
                if candidate.name.lower() == SELORA_EXCLUDE_LABEL_NAME.lower():
                    label = candidate
                    break
        return label.label_id if label else None
    except Exception:  # noqa: BLE001 — label registry is best-effort
        return None


def resolve_label_tagged_items(hass: HomeAssistant) -> dict[str, list[str]]:
    """Return entities / devices / areas explicitly carrying the exclude label.

    Distinct from ``resolve_ignored_entity_ids`` which expands devices and
    areas down to their entities — this listing keeps each kind at its
    natural granularity so the UI can render a faithful chip per tag.
    """
    label_id = resolve_exclude_label_id(hass)
    if label_id is None:
        return {"entities": [], "devices": [], "areas": []}

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    entities = sorted(
        e.entity_id for e in ent_reg.entities.values() if label_id in (e.labels or ())
    )
    devices = sorted(d.id for d in dev_reg.devices.values() if label_id in (d.labels or ()))
    areas: list[str] = []
    try:
        from homeassistant.helpers import area_registry as ar

        area_reg = ar.async_get(hass)
        areas = sorted(a.id for a in area_reg.async_list_areas() if label_id in (a.labels or ()))
    except Exception:  # noqa: BLE001 — area-label lookup is best-effort
        pass
    return {"entities": entities, "devices": devices, "areas": areas}


def resolve_ignored_entity_ids(hass: HomeAssistant) -> frozenset[str]:
    """Return every entity_id excluded from proactive suggestions.

    The Selora exclude HA label is the single source of truth. An entity is
    ignored when itself, its device, or its area carries the label. Lookup
    runs on every call so changes to labels take effect immediately without
    an integration reload.
    """
    label_id = resolve_exclude_label_id(hass)
    if label_id is None:
        return frozenset()

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    # Pre-compute device-label and area-label sets so the per-entity loop
    # below is a flat membership check instead of two more registry reads
    # per entity.
    label_devices: set[str] = {
        d.id for d in dev_reg.devices.values() if label_id in (d.labels or ())
    }
    label_areas: set[str] = set()
    try:
        from homeassistant.helpers import area_registry as ar

        area_reg = ar.async_get(hass)
        label_areas = {a.id for a in area_reg.async_list_areas() if label_id in (a.labels or ())}
    except Exception:  # noqa: BLE001 — area-label lookup is best-effort
        pass

    result: set[str] = set()
    for ent in ent_reg.entities.values():
        if label_id in (ent.labels or ()):
            result.add(ent.entity_id)
            continue
        if ent.device_id and ent.device_id in label_devices:
            result.add(ent.entity_id)
            continue
        if not label_areas:
            continue
        area_id = ent.area_id
        if not area_id and ent.device_id:
            dev = dev_reg.async_get(ent.device_id)
            area_id = dev.area_id if dev else None
        if area_id and area_id in label_areas:
            result.add(ent.entity_id)

    return frozenset(result)


class EntityFilter:
    """One-pass registry scan that answers is_active / same_device queries.

    Constructed once per detection run so the entity registry is read exactly
    once, then every call-site can filter cheaply without re-fetching.
    """

    def __init__(self, hass: HomeAssistant, entity_ids: Iterable[str]) -> None:
        registry = er.async_get(hass)
        self._inactive: set[str] = set()
        self._device_ids: dict[str, str | None] = {}
        for eid in entity_ids:
            entry = registry.async_get(eid)
            self._device_ids[eid] = entry.device_id if entry else None
            if entry is None:
                continue
            if entry.disabled:
                self._inactive.add(eid)
                continue
            if entry.entity_category is not None:
                self._inactive.add(eid)

    def is_active(self, entity_id: str) -> bool:
        """Return True if the entity is part of the user-facing surface.

        False for entities that are disabled, marked as ``config``, or
        marked as ``diagnostic`` in the entity registry. The pattern
        engines, collector snapshot, and LLM context use this to skip
        the per-device noise (battery level, signal strength, firmware
        version, identify buttons, etc.) that ships with most Zigbee /
        Z-Wave / Matter devices.
        """
        return entity_id not in self._inactive

    def same_device(self, entity_a: str, entity_b: str) -> bool:
        """Return True if both entities belong to the same physical device."""
        dev_a = self._device_ids.get(entity_a)
        dev_b = self._device_ids.get(entity_b)
        return bool(dev_a and dev_b and dev_a == dev_b)
