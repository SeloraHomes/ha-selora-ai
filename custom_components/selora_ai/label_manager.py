"""Create and apply Home Assistant labels.

A label is the cross-cutting counterpart to an area: an area answers "where is
it", a label answers "what kind of thing is it" — ``holiday``, ``kids``,
``battery-powered``, ``do-not-automate``. Automations can target a label
directly, so labelling ten entities gives an automation one stable target that
survives adding an eleventh, without the per-domain constraint a group helper
carries.

Labels attach to entities, devices, and areas. All three keep them in their own
registry as a ``set[str]`` of label_ids, which is why every write here is
read-modify-write rather than an assignment: HA has no add/remove primitive, so
a naive ``labels={new}`` silently drops every label the target already had.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.core import valid_entity_id
from homeassistant.helpers import (
    area_registry as ar,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers import (
    label_registry as lr,
)

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_MAX_LISTED: Final = 50


def _norm(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def resolve_label(hass: HomeAssistant, ref: str) -> tuple[lr.LabelEntry | None, str | None]:
    """Resolve a label by label_id or name."""
    ref = str(ref or "").strip()
    if not ref:
        return None, "A label name or label_id is required."

    registry = lr.async_get(hass)
    if (label := registry.async_get_label(ref)) is not None:
        return label, None
    if (label := registry.async_get_label_by_name(ref)) is not None:
        return label, None

    wanted = _norm(ref)
    for candidate in registry.async_list_labels():
        if _norm(candidate.name) == wanted:
            return candidate, None

    known = sorted(sanitize_untrusted_text(x.name, 40) for x in registry.async_list_labels())
    if not known:
        return None, f"No label named '{sanitize_untrusted_text(ref, 60)}'. No labels exist yet."
    return None, (
        f"No label named '{sanitize_untrusted_text(ref, 60)}'. Existing labels: {', '.join(known)}."
    )


def label_usage(hass: HomeAssistant, label_id: str) -> dict[str, int]:
    """Exact counts for one label, independent of any display cap.

    :func:`label_overview` truncates its list at ``_MAX_LISTED``, so looking a
    label up in that result silently returns nothing once a home has more
    labels than the cap — and the delete path would then report zero removals
    and show a confirmation card with no blast radius, on the labels most
    likely to be in heavy use.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    area_reg = ar.async_get(hass)
    return {
        "entity_count": sum(1 for e in ent_reg.entities.values() if label_id in (e.labels or ())),
        "device_count": sum(1 for d in dev_reg.devices.values() if label_id in (d.labels or ())),
        "area_count": sum(1 for a in area_reg.async_list_areas() if label_id in (a.labels or ())),
    }


def label_dependents(hass: HomeAssistant, label_id: str) -> dict[str, list[str]]:
    """Automations and scripts that TARGET this label.

    Distinct from the entities/devices/areas that merely carry it: those just
    lose a tag, whereas an automation with ``target: {label_id: holiday}`` keeps
    loading, keeps validating, and quietly acts on nothing. That is the part of
    a delete the user cannot see coming, so it belongs on the card.

    Each lookup degrades to an empty list when its component is not set up — a
    missing blast-radius hint must never block the delete itself.
    """
    automations: list[str] = []
    scripts: list[str] = []
    try:
        from homeassistant.components.automation import automations_with_label  # noqa: PLC0415

        automations = list(automations_with_label(hass, label_id))
    except (ImportError, KeyError, AttributeError):
        automations = []
    try:
        from homeassistant.components.script import scripts_with_label  # noqa: PLC0415

        scripts = list(scripts_with_label(hass, label_id))
    except (ImportError, KeyError, AttributeError):
        scripts = []
    return {"automations": automations, "scripts": scripts}


def label_overview(hass: HomeAssistant) -> dict[str, Any]:
    """Every label with how many entities, devices, and areas carry it."""
    registry = lr.async_get(hass)
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    area_reg = ar.async_get(hass)

    records = []
    for label in sorted(registry.async_list_labels(), key=lambda label: _norm(label.name)):
        records.append(
            {
                "label_id": label.label_id,
                "name": sanitize_untrusted_text(label.name, 40),
                "entity_count": sum(
                    1 for e in ent_reg.entities.values() if label.label_id in (e.labels or ())
                ),
                "device_count": sum(
                    1 for d in dev_reg.devices.values() if label.label_id in (d.labels or ())
                ),
                "area_count": sum(
                    1 for a in area_reg.async_list_areas() if label.label_id in (a.labels or ())
                ),
            }
        )
    return {"labels": records[:_MAX_LISTED], "count": len(records)}


def async_create_label(
    hass: HomeAssistant,
    *,
    name: str,
    icon: str | None = None,
    color: str | None = None,
) -> dict[str, Any]:
    """Create a label, or report the existing one with the same name.

    Same reasoning as ``create_area``: two labels called ``holiday`` is a worse
    outcome than a no-op, because every later lookup becomes ambiguous and an
    automation targeting one of them silently misses the entities carrying the
    other.
    """
    name = str(name or "").strip()
    if not name:
        return {"error": "A label name is required."}

    existing, _error = resolve_label(hass, name)
    if existing is not None:
        return {
            "status": "exists",
            "label_id": existing.label_id,
            "name": sanitize_untrusted_text(existing.name, 40),
        }

    label = lr.async_get(hass).async_create(
        name,
        icon=str(icon).strip() if icon else None,
        color=str(color).strip() if color else None,
    )
    return {
        "status": "created",
        "label_id": label.label_id,
        "name": sanitize_untrusted_text(label.name, 40),
    }


def async_delete_label(hass: HomeAssistant, label_id: str) -> dict[str, Any]:
    """Delete a label. HA strips it from every target that carried it."""
    registry = lr.async_get(hass)
    label = registry.async_get_label(label_id)
    if label is None:
        return {"error": f"Label '{label_id}' no longer exists."}

    name = sanitize_untrusted_text(label.name, 40)
    counts = label_usage(hass, label_id)
    registry.async_delete(label_id)
    return {
        "status": "deleted",
        "label_id": label_id,
        "name": name,
        "removed_from_entities": counts["entity_count"],
        "removed_from_devices": counts["device_count"],
        "removed_from_areas": counts["area_count"],
    }


def _apply(current: Iterable[str] | None, add: set[str], remove: set[str]) -> set[str]:
    return (set(current or ()) | add) - remove


async def async_assign_labels(
    hass: HomeAssistant,
    *,
    add_labels: Iterable[str] | None = None,
    remove_labels: Iterable[str] | None = None,
    entity_ids: Iterable[str] | None = None,
    device_ids: Iterable[str] | None = None,
    areas: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Add and/or remove labels across entities, devices, and areas.

    Deltas rather than replacement, and deliberately so: labels are the one
    registry field several unrelated concerns write to at once, and a
    replacement call from a model that only knows about ``holiday`` would drop
    the ``battery-powered`` label some other flow set. There is no "set the
    labels to exactly this" operation here for that reason.

    A label named in ``add_labels`` that does not exist is **created**. Unlike
    an area, a label has no contents and no automation can target one that was
    never made, so refusing would turn every "tag these as holiday" into two
    round-trips for no protection.
    """
    add_refs = [str(x).strip() for x in (add_labels or []) if str(x).strip()]
    remove_refs = [str(x).strip() for x in (remove_labels or []) if str(x).strip()]
    if not add_refs and not remove_refs:
        return {"error": "Provide at least one label to add or remove."}

    entity_ids = [str(x).strip() for x in (entity_ids or []) if str(x).strip()]
    device_ids = [str(x).strip() for x in (device_ids or []) if str(x).strip()]
    areas = [str(x).strip() for x in (areas or []) if str(x).strip()]
    if not entity_ids and not device_ids and not areas:
        return {"error": "Provide at least one entity_id, device_id, or area to label."}

    # Removals are resolved FIRST, because resolving an addition can create a
    # label. With the loops the other way round, "add holiday, remove typo"
    # creates ``holiday`` and then fails on ``typo`` — the caller sees only an
    # error while the registry has already gained a label nobody asked to keep.
    # Nothing here mutates until every reference has been validated.
    remove_ids: set[str] = set()
    for ref in remove_refs:
        label, error = resolve_label(hass, ref)
        if label is None:
            return {"error": error or f"No label named '{sanitize_untrusted_text(ref, 40)}'."}
        remove_ids.add(label.label_id)

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    area_reg = ar.async_get(hass)
    updated: dict[str, list[str]] = {"entities": [], "devices": [], "areas": []}
    failed: list[dict[str, str]] = []

    # Targets are resolved BEFORE any label is created, for the same reason
    # removals are: creating an addition is a mutation, and a call whose targets
    # are all bogus would otherwise leave a label behind while reporting nothing
    # but failures. Resolution is pure — the writes happen further down.
    from .registry_manager import resolve_area  # noqa: PLC0415

    target_entities = []
    for entity_id in entity_ids:
        entry = ent_reg.async_get(entity_id) if valid_entity_id(entity_id) else None
        if entry is None:
            failed.append({"target": entity_id, "reason": "Not in the entity registry."})
            continue
        target_entities.append(entry)

    target_devices = []
    for device_id in device_ids:
        entry = dev_reg.async_get(device_id)
        if entry is None:
            failed.append({"target": device_id, "reason": "No such device_id."})
            continue
        target_devices.append(entry)

    target_areas = []
    for area_ref in areas:
        area, error = resolve_area(hass, area_ref)
        if area is None:
            failed.append({"target": area_ref, "reason": error or "No such area."})
            continue
        target_areas.append(area)

    if not (target_entities or target_devices or target_areas):
        return {
            "error": "None of the given targets could be resolved; nothing was changed.",
            "failed": failed,
        }

    add_ids: set[str] = set()
    created: list[str] = []
    for ref in add_refs:
        label, _error = resolve_label(hass, ref)
        if label is None:
            label = lr.async_get(hass).async_create(ref)
            created.append(sanitize_untrusted_text(label.name, 40))
        add_ids.add(label.label_id)

    for entry in target_entities:
        ent_reg.async_update_entity(
            entry.entity_id, labels=_apply(entry.labels, add_ids, remove_ids)
        )
        updated["entities"].append(entry.entity_id)

    for entry in target_devices:
        dev_reg.async_update_device(entry.id, labels=_apply(entry.labels, add_ids, remove_ids))
        updated["devices"].append(entry.id)

    for area in target_areas:
        area_reg.async_update(area.id, labels=_apply(area.labels, add_ids, remove_ids))
        updated["areas"].append(area.id)

    result: dict[str, Any] = {
        "status": "updated" if any(updated.values()) else "unchanged",
        "labels_added": sorted(add_ids),
        "labels_removed": sorted(remove_ids),
        "updated": updated,
    }
    if created:
        result["labels_created"] = created
    if failed:
        result["failed"] = failed
    return result
