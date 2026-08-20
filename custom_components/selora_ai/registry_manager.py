"""Read and write Home Assistant's area, floor, entity, and device registries.

Backs the chat tools that answer "put the living room lights in the Living
Room", "call this one the Reading Lamp", and "stop showing that sensor in
Assist". Before these existed the model could see every entity's area in the
home snapshot but had no way to change one, so it fell back to reciting the
Settings → Devices & services → Entities click-path — the worst reply a butler
can give, because the user knows the product is already holding the entity.

Registry writes are synchronous and take effect immediately; there is no
config entry to reload and no YAML to render. What they are not is *local*:

* **An entity's area is an override of its device's area.** An entity with
  ``area_id = None`` inherits, and the UI shows it in the device's area. So
  "assign this light to the Living Room" has two correct outcomes depending on
  where its device sits, and picking the wrong one silently pins the entity —
  it stops following the device, and a later "move the dimmer to the Bedroom"
  leaves this entity behind in a room it no longer belongs to. See
  :func:`async_assign_area`.
* **Moving a device moves its entities with it**, minus the ones that carry
  their own override. The count belongs in the result, not in prose the tool
  loop may discard.
* **Deleting an area unassigns rather than deletes**, and it does so silently:
  every automation targeting ``area_id: living_room`` keeps validating and
  starts matching nothing. Hence the confirmation card.
* **Renaming an entity_id rewrites nobody's references.** HA does not rewrite
  automations, scripts, scenes, groups, or dashboards, so the old id becomes a
  dangling target. :func:`async_update_entity` refuses the rename when anything
  references it and names the referrers. Lovelace needs its own lookup —
  ``async_dashboards_with_entity`` — because it is the only referrer core ships
  no ``*_with_entity`` helper for.
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
    floor_registry as fr,
)

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# HA's Assist pipeline registers itself under this assistant key in
# ``exposed_entities``. The cloud assistants ("cloud.alexa",
# "cloud.google_assistant") use the same store but are Nabu Casa features we
# deliberately do not touch — a user without a subscription has no way to see
# or undo a change there.
ASSIST_ASSISTANT: Final = "conversation"

# Cap on the entity/device lists echoed back per area. ``*_count`` stays exact.
# Not cosmetic: ``ToolExecutor._find_longest_list`` only trims top-level lists
# and lists inside top-level dicts — never a list nested in a list *of* dicts —
# so one 200-entity area would otherwise get the whole ``areas[0]`` record
# popped and the caller would receive an area with no name and no id.
_MAX_LISTED: Final = 30


def _norm(value: object) -> str:
    """Casefold and collapse whitespace for name matching."""
    return " ".join(str(value or "").split()).casefold()


# ── Resolution ──────────────────────────────────────────────────────────────


def resolve_area(hass: HomeAssistant, ref: str) -> tuple[ar.AreaEntry | None, str | None]:
    """Resolve an area by id, name, or alias.

    Returns ``(area, None)`` or ``(None, error)``. The error names the
    available areas so the model can correct itself in one round rather than
    guessing again — with a typical home that list is short enough to be worth
    the tokens.
    """
    ref = str(ref or "").strip()
    if not ref:
        return None, "An area name or area_id is required."

    registry = ar.async_get(hass)
    if (area := registry.async_get_area(ref)) is not None:
        return area, None
    if (area := registry.async_get_area_by_name(ref)) is not None:
        return area, None

    wanted = _norm(ref)
    for candidate in registry.async_list_areas():
        if _norm(candidate.name) == wanted:
            return candidate, None

    # Aliases are NOT unique — HA's own lookup returns a *list*, which is the
    # registry saying so. Picking the first would assign entities to, rename, or
    # delete an arbitrary one of them. Names are unique (``async_create`` refuses
    # a duplicate), so only the alias branch can be ambiguous.
    by_alias = [
        candidate
        for candidate in registry.async_list_areas()
        if any(_norm(a) == wanted for a in candidate.aliases)
    ]
    if len(by_alias) == 1:
        return by_alias[0], None
    if len(by_alias) > 1:
        names = ", ".join(sorted(sanitize_untrusted_text(a.name, 40) for a in by_alias))
        return None, (
            f"'{sanitize_untrusted_text(ref, 60)}' is an alias of {len(by_alias)} areas "
            f"({names}). Use the area's name or area_id to say which one."
        )

    known = sorted(sanitize_untrusted_text(a.name, 60) for a in registry.async_list_areas())
    if not known:
        return (
            None,
            f"No area named '{sanitize_untrusted_text(ref, 60)}'. This home has no areas yet — create one first.",
        )
    return None, (
        f"No area named '{sanitize_untrusted_text(ref, 60)}'. Existing areas: {', '.join(known)}."
    )


def resolve_floor(hass: HomeAssistant, ref: str) -> tuple[fr.FloorEntry | None, str | None]:
    """Resolve a floor by id, name, or alias. Mirrors :func:`resolve_area`."""
    ref = str(ref or "").strip()
    if not ref:
        return None, "A floor name or floor_id is required."

    registry = fr.async_get(hass)
    if (floor := registry.async_get_floor(ref)) is not None:
        return floor, None
    if (floor := registry.async_get_floor_by_name(ref)) is not None:
        return floor, None

    wanted = _norm(ref)
    for candidate in registry.async_list_floors():
        if _norm(candidate.name) == wanted:
            return candidate, None

    # Floor aliases are no more unique than area ones, and a floor is chosen
    # when creating or moving an area — so picking the first match silently
    # files a room on an arbitrary storey, decided by registry iteration order.
    by_alias = [
        candidate
        for candidate in registry.async_list_floors()
        if any(_norm(a) == wanted for a in candidate.aliases)
    ]
    if len(by_alias) == 1:
        return by_alias[0], None
    if len(by_alias) > 1:
        names = ", ".join(sorted(sanitize_untrusted_text(f.name, 40) for f in by_alias))
        return None, (
            f"'{sanitize_untrusted_text(ref, 60)}' is an alias of {len(by_alias)} floors "
            f"({names}). Use the floor's name or floor_id to say which one."
        )
    return None, f"No floor named '{sanitize_untrusted_text(ref, 60)}'."


def resolve_device(hass: HomeAssistant, ref: str) -> tuple[dr.DeviceEntry | None, str | None]:
    """Resolve a device by device_id or by its user-visible name.

    The CURRENT display name — ``name_by_user`` when set, otherwise ``name`` —
    is matched first, and the integration-supplied ``name`` only as a fallback
    for devices that carry an override. Matching both at once let an obsolete
    manufacturer name target a renamed device, and worse, let it collide with a
    different device whose current name happens to be that string: an
    unambiguous request then failed as ambiguous.
    """
    ref = str(ref or "").strip()
    if not ref:
        return None, "A device_id or device name is required."

    registry = dr.async_get(hass)
    if (device := registry.async_get(ref)) is not None:
        return device, None

    wanted = _norm(ref)
    devices = list(registry.devices.values())

    def ambiguous(matches: list[dr.DeviceEntry]) -> str:
        ids = ", ".join(d.id for d in matches[:5])
        return (
            f"'{sanitize_untrusted_text(ref, 60)}' matches {len(matches)} devices. "
            f"Use one of these device_ids: {ids}."
        )

    current = [d for d in devices if _norm(d.name_by_user or d.name) == wanted]
    if len(current) == 1:
        return current[0], None
    if len(current) > 1:
        return None, ambiguous(current)

    # Nothing answers to that name now — fall back to the vendor name of a
    # device the user has since renamed, so "the Shelly" still finds it.
    original = [d for d in devices if d.name_by_user and _norm(d.name) == wanted]
    if len(original) == 1:
        return original[0], None
    if len(original) > 1:
        return None, ambiguous(original)
    return None, f"No device named '{sanitize_untrusted_text(ref, 60)}'."


def _entity_display_area(hass: HomeAssistant, entry: er.RegistryEntry) -> str | None:
    """The area_id an entity actually resolves to, override or inherited."""
    if entry.area_id:
        return entry.area_id
    if entry.device_id and (device := dr.async_get(hass).async_get(entry.device_id)):
        return device.area_id
    return None


# ── Reads ───────────────────────────────────────────────────────────────────


def area_overview(hass: HomeAssistant, *, include_entities: bool = False) -> dict[str, Any]:
    """Return every floor and area with occupancy counts.

    Areas with no floor are grouped under a ``null`` floor rather than dropped
    — an unassigned area is the common case in a home that never set floors up,
    and omitting it would make the model report the home as having no areas.
    """
    area_reg = ar.async_get(hass)
    floor_reg = fr.async_get(hass)
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    entities_by_area: dict[str, list[str]] = {}
    for entry in ent_reg.entities.values():
        if area_id := _entity_display_area(hass, entry):
            entities_by_area.setdefault(area_id, []).append(entry.entity_id)

    devices_by_area: dict[str, list[str]] = {}
    for device in dev_reg.devices.values():
        if device.area_id:
            devices_by_area.setdefault(device.area_id, []).append(device.id)

    areas: list[dict[str, Any]] = []
    for area in sorted(area_reg.async_list_areas(), key=lambda a: _norm(a.name)):
        ent_ids = sorted(entities_by_area.get(area.id, []))
        dev_ids = devices_by_area.get(area.id, [])
        record: dict[str, Any] = {
            "area_id": area.id,
            "name": sanitize_untrusted_text(area.name, 60),
            "floor_id": area.floor_id,
            "entity_count": len(ent_ids),
            "device_count": len(dev_ids),
        }
        if area.aliases:
            record["aliases"] = sorted(sanitize_untrusted_text(a, 60) for a in area.aliases)
        if include_entities:
            record["entities"] = ent_ids[:_MAX_LISTED]
            if len(ent_ids) > _MAX_LISTED:
                record["entities_omitted"] = len(ent_ids) - _MAX_LISTED
        areas.append(record)

    floors = [
        {
            "floor_id": floor.floor_id,
            "name": sanitize_untrusted_text(floor.name, 60),
            "level": floor.level,
            # Capped for the same reason the per-area entity list is: this sits
            # inside a list OF dicts, which ``_find_longest_list`` cannot reach,
            # so an oversized result gets a whole floor record popped instead.
            "area_ids": [a.id for a in area_reg.async_list_areas() if a.floor_id == floor.floor_id][
                :_MAX_LISTED
            ],
        }
        for floor in sorted(
            floor_reg.async_list_floors(), key=lambda f: (f.level or 0, _norm(f.name))
        )
    ]

    unassigned = sum(1 for e in ent_reg.entities.values() if _entity_display_area(hass, e) is None)
    return {
        "areas": areas,
        "floors": floors,
        "area_count": len(areas),
        "unassigned_entity_count": unassigned,
    }


def list_services(hass: HomeAssistant, domain: str | None = None) -> dict[str, Any]:
    """Return the callable services, optionally for one domain.

    Without a domain the field list is dropped and only names are returned:
    a full HA install exposes several hundred services and their schemas run to
    tens of thousands of tokens, which is not a payload any caller can use.
    Ask for a domain to get the fields.
    """
    all_services = hass.services.async_services()
    domain = str(domain or "").strip().lower()

    if domain:
        if domain not in all_services:
            known = ", ".join(sorted(all_services)[:40])
            return {"error": f"No services for domain '{domain}'. Domains include: {known}."}
        services = []
        for name, service in sorted(all_services[domain].items()):
            record: dict[str, Any] = {"service": f"{domain}.{name}"}
            schema = getattr(service, "schema", None)
            if schema is not None:
                with_fields = sorted(str(k) for k in getattr(schema, "schema", {}))
                if with_fields:
                    record["fields"] = with_fields[:_MAX_LISTED]
            services.append(record)
        return {"domain": domain, "services": services, "count": len(services)}

    return {
        "domains": sorted(all_services),
        "service_names": sorted(f"{d}.{s}" for d, entries in all_services.items() for s in entries)[
            : _MAX_LISTED * 10
        ],
        "note": "Call again with a domain to get each service's accepted fields.",
    }


# ── Area writes ─────────────────────────────────────────────────────────────


def _ensure_floor(hass: HomeAssistant, ref: str) -> tuple[str | None, str | None]:
    """Resolve a floor, creating it when the name is new.

    Auto-creation is safe here in a way that auto-creating an *area* is not: a
    floor carries no entities and no automation targets it, so a typo yields an
    empty label the user can delete, not a room that silently splits a home in
    two. Returns ``(floor_id, created_name_or_None)``.
    """
    floor, _error = resolve_floor(hass, ref)
    if floor is not None:
        return floor.floor_id, None
    created = fr.async_get(hass).async_create(str(ref).strip())
    return created.floor_id, created.name


def async_create_area(
    hass: HomeAssistant,
    *,
    name: str,
    floor: str | None = None,
    icon: str | None = None,
    aliases: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Create an area, or report the existing one with the same name.

    A duplicate is reported as ``status: "exists"`` rather than refused. The
    model reaches here from "put the lamp in the Study" after ``assign_area``
    said no such area, and two areas both called Study is a worse outcome than
    a no-op — HA allows it, and every subsequent lookup becomes ambiguous.
    """
    name = str(name or "").strip()
    if not name:
        return {"error": "An area name is required."}

    registry = ar.async_get(hass)
    existing, _error = resolve_area(hass, name)
    if existing is not None:
        return {
            "status": "exists",
            "area_id": existing.id,
            "name": sanitize_untrusted_text(existing.name, 60),
            "message": (
                f"An area named '{sanitize_untrusted_text(existing.name, 60)}' already exists."
            ),
        }

    floor_id: str | None = None
    created_floor: str | None = None
    if floor:
        floor_id, created_floor = _ensure_floor(hass, floor)

    area = registry.async_create(
        name,
        floor_id=floor_id,
        icon=str(icon).strip() or None if icon else None,
        aliases={str(a).strip() for a in aliases if str(a).strip()} if aliases else None,
    )
    result: dict[str, Any] = {
        "status": "created",
        "area_id": area.id,
        "name": sanitize_untrusted_text(area.name, 60),
    }
    if floor_id:
        result["floor_id"] = floor_id
    if created_floor:
        result["created_floor"] = sanitize_untrusted_text(created_floor, 60)
    return result


def async_update_area(
    hass: HomeAssistant,
    *,
    area: str,
    new_name: str | None = None,
    floor: str | None = None,
    icon: str | None = None,
    aliases: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Rename an area, move it to a floor, or change its icon/aliases.

    An empty optional argument is treated as absent — models routinely emit
    ``""`` / ``[]`` for parameters they are not using, and honouring those
    literally would blank an area's name or wipe its aliases as a side effect
    of a floor change.
    """
    area_entry, error = resolve_area(hass, area)
    if error or area_entry is None:
        return {"error": error or "Area not found."}

    changes: dict[str, Any] = {}
    new_name = str(new_name or "").strip()
    if new_name and new_name != area_entry.name:
        clash, _ = resolve_area(hass, new_name)
        if clash is not None and clash.id != area_entry.id:
            return {
                "error": f"An area named '{sanitize_untrusted_text(new_name, 60)}' already exists."
            }
        changes["name"] = new_name

    created_floor: str | None = None
    if floor:
        floor_id, created_floor = _ensure_floor(hass, floor)
        if floor_id != area_entry.floor_id:
            changes["floor_id"] = floor_id
    if icon:
        changes["icon"] = str(icon).strip()
    if aliases is not None:
        # An empty list is a real request — "drop the last alias" — so it is
        # written rather than skipped. Guarded on inequality so a redundant
        # clear on an area that has none does not report ``updated``.
        cleaned = {str(a).strip() for a in aliases if str(a).strip()}
        if cleaned != set(area_entry.aliases or ()):
            changes["aliases"] = cleaned

    if not changes:
        return {
            "status": "unchanged",
            "area_id": area_entry.id,
            "name": sanitize_untrusted_text(area_entry.name, 60),
            "message": "No changes were requested.",
        }

    updated = ar.async_get(hass).async_update(area_entry.id, **changes)
    result: dict[str, Any] = {
        "status": "updated",
        "area_id": updated.id,
        "name": sanitize_untrusted_text(updated.name, 60),
        "changed": sorted(changes),
    }
    if created_floor:
        result["created_floor"] = sanitize_untrusted_text(created_floor, 60)
    return result


def area_dependents(hass: HomeAssistant, area_id: str) -> dict[str, Any]:
    """Count what a delete would orphan, and list what targets the area.

    Entities and devices are counted; automations and scripts are listed. The
    difference is what the user can act on: an unassigned entity is visible and
    fixable in one drag, whereas an automation whose ``area_id`` target no
    longer resolves keeps loading, keeps validating, and quietly matches
    nothing — so those need naming, not tallying.

    A missing blast-radius hint must never block the delete itself, so each
    lookup degrades to an empty list when its component is not set up.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    entities = sum(1 for e in ent_reg.entities.values() if _entity_display_area(hass, e) == area_id)
    devices = sum(1 for d in dev_reg.devices.values() if d.area_id == area_id)

    automations: list[str] = []
    scripts: list[str] = []
    try:
        from homeassistant.components.automation import automations_with_area  # noqa: PLC0415

        automations = list(automations_with_area(hass, area_id))
    except (ImportError, KeyError, AttributeError):
        automations = []
    try:
        from homeassistant.components.script import scripts_with_area  # noqa: PLC0415

        scripts = list(scripts_with_area(hass, area_id))
    except (ImportError, KeyError, AttributeError):
        scripts = []

    return {
        "entities": entities,
        "devices": devices,
        "automations": automations,
        "scripts": scripts,
    }


async def async_delete_area(hass: HomeAssistant, area_id: str) -> dict[str, Any]:
    """Delete an area. Everything in it becomes unassigned, not deleted."""
    registry = ar.async_get(hass)
    entry = registry.async_get_area(area_id)
    if entry is None:
        return {"error": f"Area '{area_id}' no longer exists."}

    dependents = area_dependents(hass, area_id)
    name = sanitize_untrusted_text(entry.name, 60)
    registry.async_delete(area_id)
    return {
        "status": "deleted",
        "area_id": area_id,
        "name": name,
        "unassigned_entities": dependents["entities"],
        "unassigned_devices": dependents["devices"],
    }


# ── Assignment ──────────────────────────────────────────────────────────────


async def async_assign_area(
    hass: HomeAssistant,
    *,
    area: str,
    entity_ids: Iterable[str] | None = None,
    device_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Put entities and/or devices into an area.

    For an entity that belongs to a device already in the target area, the
    override is **cleared** rather than set. Both produce the same display
    today, but a stored override outlives the coincidence: move the device to
    the Bedroom next month and an entity pinned this way stays behind in the
    Living Room, with nothing in the UI to explain why one of a device's
    entities did not travel with it. Inheriting is what the user meant.
    """
    area_entry, error = resolve_area(hass, area)
    if error or area_entry is None:
        return {"error": error or "Area not found."}

    entity_ids = [str(e).strip() for e in (entity_ids or []) if str(e).strip()]
    device_ids = [str(d).strip() for d in (device_ids or []) if str(d).strip()]
    if not entity_ids and not device_ids:
        return {"error": "Provide at least one entity_id or device_id to assign."}

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    assigned: list[str] = []
    inherited: list[str] = []
    unchanged: list[str] = []
    failed: list[dict[str, str]] = []

    # Devices move FIRST. When one call names both a device and an entity that
    # belongs to it, running the entity loop first sees the device still in its
    # old area and writes an explicit override — which the device move then makes
    # redundant, and which pins the entity so it no longer follows the device.
    # That is precisely the inheritance this function exists to preserve.
    moved_devices: list[str] = []
    for device_id in device_ids:
        device, dev_error = resolve_device(hass, device_id)
        if device is None:
            failed.append({"device_id": device_id, "reason": dev_error or "Not found."})
            continue
        if device.area_id == area_entry.id:
            unchanged.append(device.id)
            continue
        dev_reg.async_update_device(device.id, area_id=area_entry.id)
        moved_devices.append(device.id)

    for entity_id in entity_ids:
        if not valid_entity_id(entity_id):
            failed.append({"entity_id": entity_id, "reason": "Not a valid entity_id."})
            continue
        registry_entry = ent_reg.async_get(entity_id)
        if registry_entry is None:
            failed.append(
                {
                    "entity_id": entity_id,
                    "reason": "Not in the entity registry — YAML-defined entities have no area.",
                }
            )
            continue

        device = dev_reg.async_get(registry_entry.device_id) if registry_entry.device_id else None
        if device is not None and device.area_id == area_entry.id:
            if registry_entry.area_id is None:
                unchanged.append(entity_id)
            else:
                ent_reg.async_update_entity(entity_id, area_id=None)
                inherited.append(entity_id)
            continue

        if registry_entry.area_id == area_entry.id:
            unchanged.append(entity_id)
            continue
        ent_reg.async_update_entity(entity_id, area_id=area_entry.id)
        assigned.append(entity_id)

    # Counted after the entity loop, not at move time: an entity named in the
    # same call may have had its override cleared just above, which changes
    # whether it is riding along with its device.
    carried = sum(
        1
        for device_id in moved_devices
        for e in er.async_entries_for_device(ent_reg, device_id, include_disabled_entities=True)
        if e.area_id is None
    )

    result: dict[str, Any] = {
        "status": "assigned" if (assigned or inherited or moved_devices) else "unchanged",
        "area_id": area_entry.id,
        "area": sanitize_untrusted_text(area_entry.name, 60),
        "entities_assigned": assigned,
        "devices_moved": moved_devices,
    }
    if inherited:
        # Named separately because the stored value went to None, not to the
        # area — a caller that reads the registry back would otherwise see a
        # blank area_id and read the assignment as having failed.
        result["entities_now_inheriting"] = inherited
    if carried:
        result["entities_carried_with_devices"] = carried
    if unchanged:
        result["already_in_area"] = unchanged
    if failed:
        result["failed"] = failed
    return result


# ── Entity / device writes ──────────────────────────────────────────────────


async def validate_entity_id_rename(
    hass: HomeAssistant, entity_id: str, new_entity_id: str
) -> str | None:
    """Why this entity_id rename cannot proceed, or ``None`` if it can.

    Shared by the direct write and by the confirmation-card preview so the two
    cannot disagree. Without it the preview would offer a card for a rename
    that is invalid on its face — a domain change, a taken id — and the user
    would tap Apply only to be told it was never possible.
    """
    if not valid_entity_id(new_entity_id):
        return f"'{sanitize_untrusted_text(new_entity_id, 60)}' is not a valid entity_id."
    if new_entity_id.split(".")[0] != entity_id.split(".")[0]:
        return "An entity_id cannot change domain."
    # Both halves of HA's own availability rule. The registry rejects a rename
    # onto an id that is registered OR merely present in the state machine, so
    # checking only the registry lets a YAML-defined entity — state, no registry
    # entry — pass here and fail at write time. That would put a confirmation
    # card in front of an action that can never succeed.
    if er.async_get(hass).async_get(new_entity_id) is not None:
        return f"'{new_entity_id}' is already taken."
    if hass.states.get(new_entity_id) is not None:
        return (
            f"'{new_entity_id}' is already in use by an entity Home Assistant does not "
            f"manage in the registry (a YAML-defined entity, for example), so the id "
            f"cannot be reassigned."
        )

    from .group_manager import group_dependents  # noqa: PLC0415
    from .recipes.dashboard import async_dashboards_with_entity  # noqa: PLC0415

    refs = group_dependents(hass, entity_id)
    blocking = [
        f"{len(refs[k])} {k}" for k in ("automations", "scripts", "scenes", "groups") if refs[k]
    ]
    # Lovelace is the one referrer HA ships no ``*_with_entity`` helper for, so
    # checking only the four above would report a rename as safe while leaving
    # cards pointed at an id that no longer resolves. YAML dashboards count
    # doubly: we cannot repair them, so the user would have to hand-edit a file
    # after working out why a card went blank.
    dashboards, unreadable = await async_dashboards_with_entity(hass, entity_id)
    if dashboards:
        blocking.append(f"{len(dashboards)} dashboard{'s' if len(dashboards) != 1 else ''}")
    if unreadable:
        # A dashboard we could not parse is not a dashboard we know is clean.
        return (
            f"Cannot safely rename '{entity_id}': the dashboard(s) "
            f"{', '.join(sorted(unreadable))} could not be read, so whether they "
            f"reference it is unknown. Home Assistant rewrites no references, so "
            f"renaming could break them silently. Set new_name instead to change "
            f"the display name, which is safe."
        )
    if not blocking:
        return None

    named = ", ".join(sorted([*refs["automations"], *refs["scripts"], *dashboards])[:5])
    return (
        f"'{entity_id}' is referenced by {', '.join(blocking)} — renaming the "
        f"entity_id would break them silently, because Home Assistant does not "
        f"rewrite references. Referrers include: {named}. "
        f"Set new_name instead to change the display name, which is safe."
    )


async def async_update_entity(
    hass: HomeAssistant,
    *,
    entity_id: str,
    new_name: str | None = None,
    aliases: Iterable[str] | None = None,
    icon: str | None = None,
    hidden: bool | None = None,
    disabled: bool | None = None,
    expose_to_assist: bool | None = None,
    new_entity_id: str | None = None,
) -> dict[str, Any]:
    """Rename, alias, hide, disable, or re-expose a single entity.

    ``new_name`` sets the *friendly name* and leaves the entity_id alone, which
    is what "call it the Reading Lamp" means — the id is plumbing the user
    never sees. ``new_entity_id`` is the plumbing change and is refused while
    anything references the old id, since HA rewrites no references and the
    automation that breaks does so silently.
    """
    entity_id = str(entity_id or "").strip()
    if not valid_entity_id(entity_id):
        return {"error": f"'{sanitize_untrusted_text(entity_id, 60)}' is not a valid entity_id."}

    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get(entity_id)
    if entry is None:
        return {
            "error": (
                f"'{entity_id}' is not in the entity registry. YAML-defined entities "
                "(template sensors, legacy groups) cannot be renamed this way."
            )
        }

    changes: dict[str, Any] = {}
    new_name = str(new_name or "").strip()
    if new_name:
        changes["name"] = new_name
    if icon:
        changes["icon"] = str(icon).strip()
    if aliases is not None:
        # See :func:`async_update_area` — an empty list clears the aliases.
        #
        # Entity aliases are ``list[AliasEntry]``, and ``AliasEntry`` is
        # ``str | ComputedNameType`` — a fresh entity already holds the
        # computed-name sentinel, not an empty list. Only the string entries
        # are the user's aliases, so only those are compared and replaced; the
        # sentinel is carried across untouched. Writing a plain ``[]`` would
        # silently drop it, changing how Assist names the entity as a side
        # effect of clearing an alias.
        cleaned = [str(a).strip() for a in aliases if str(a).strip()]
        existing = list(entry.aliases or ())
        if cleaned != [a for a in existing if isinstance(a, str)]:
            changes["aliases"] = [*(a for a in existing if not isinstance(a, str)), *cleaned]
    if hidden is not None:
        changes["hidden_by"] = er.RegistryEntryHider.USER if hidden else None
    if disabled is not None:
        changes["disabled_by"] = er.RegistryEntryDisabler.USER if disabled else None

    renamed_to: str | None = None
    new_entity_id = str(new_entity_id or "").strip()
    if new_entity_id and new_entity_id != entity_id:
        if error := await validate_entity_id_rename(hass, entity_id, new_entity_id):
            return {"error": error}
        changes["new_entity_id"] = new_entity_id
        renamed_to = new_entity_id

        # ``validate_entity_id_rename`` awaits — it inspects every dashboard —
        # and an entity_id is a mutable, reusable string. Another registry
        # operation can rename this entity and hand its id to a different one
        # inside that window, and the write below addresses entities BY id. A
        # check made before the await no longer speaks for the entity we are
        # about to change, so it is made again here, with nothing awaited
        # between it and the synchronous update.
        if (current := ent_reg.async_get(entity_id)) is None or current.id != entry.id:
            return {
                "error": (
                    f"'{entity_id}' no longer refers to the same entity — it changed "
                    f"while this request was being checked. Nothing was modified."
                )
            }

    exposed_change: bool | None = None
    if expose_to_assist is not None:
        from homeassistant.components.homeassistant.exposed_entities import (  # noqa: PLC0415
            async_expose_entity,
        )

        async_expose_entity(hass, ASSIST_ASSISTANT, entity_id, bool(expose_to_assist))
        exposed_change = bool(expose_to_assist)

    if not changes and exposed_change is None:
        return {
            "status": "unchanged",
            "entity_id": entity_id,
            "message": "No changes were requested.",
        }

    if changes:
        entry = ent_reg.async_update_entity(entity_id, **changes)

    result: dict[str, Any] = {
        "status": "updated",
        "entity_id": renamed_to or entity_id,
        "name": sanitize_untrusted_text(entry.name or entry.original_name, 60),
        "changed": sorted(
            [*changes, *(["expose_to_assist"] if exposed_change is not None else [])]
        ),
    }
    if renamed_to:
        result["previous_entity_id"] = entity_id
    if exposed_change is not None:
        result["exposed_to_assist"] = exposed_change
    return result


async def async_update_device(
    hass: HomeAssistant,
    *,
    device: str,
    new_name: str | None = None,
    area: str | None = None,
    disabled: bool | None = None,
) -> dict[str, Any]:
    """Rename a device, move it to an area, or disable it.

    The rename writes ``name_by_user`` and leaves the integration-supplied
    ``name`` intact, so clearing the override later restores the vendor name
    rather than leaving the device nameless.
    """
    entry, error = resolve_device(hass, device)
    if error or entry is None:
        return {"error": error or "Device not found."}

    changes: dict[str, Any] = {}
    new_name = str(new_name or "").strip()
    if new_name and new_name != entry.name_by_user:
        changes["name_by_user"] = new_name
    if disabled is not None:
        changes["disabled_by"] = dr.DeviceEntryDisabler.USER if disabled else None

    area_name: str | None = None
    if area:
        target, area_error = resolve_area(hass, area)
        if area_error or target is None:
            return {"error": area_error or "Area not found."}
        if target.id != entry.area_id:
            changes["area_id"] = target.id
            area_name = sanitize_untrusted_text(target.name, 60)

    if not changes:
        return {
            "status": "unchanged",
            "device_id": entry.id,
            "message": "No changes were requested.",
        }

    updated = dr.async_get(hass).async_update_device(entry.id, **changes)
    result: dict[str, Any] = {
        "status": "updated",
        "device_id": entry.id,
        "name": sanitize_untrusted_text(
            (updated.name_by_user or updated.name) if updated else new_name, 60
        ),
        "changed": sorted(changes),
    }
    if area_name:
        result["area"] = area_name
        result["entities_moved"] = sum(
            1
            for e in er.async_entries_for_device(
                er.async_get(hass), entry.id, include_disabled_entities=True
            )
            if e.area_id is None
        )
    return result


# ── Helpers ─────────────────────────────────────────────────────────────────

# The helper domains HA backs with a storage collection. These are created and
# deleted through the websocket API only — the collection object is a local in
# each component's ``async_setup`` and is never published to ``hass.data``, so
# there is no supported in-process way to add one. They are listed here so the
# model can find and USE an existing helper; creating one still means the UI.
_STORAGE_HELPER_DOMAINS: Final = (
    "input_boolean",
    "input_button",
    "input_datetime",
    "input_number",
    "input_select",
    "input_text",
    "counter",
    "timer",
    "schedule",
)


async def _config_entry_helper_domains(hass: HomeAssistant) -> set[str]:
    """Domains of the loaded integrations HA itself declares as helpers.

    Read from each integration's ``integration_type: helper`` manifest rather
    than kept as a literal here. A hand-written list was both wrong and
    unfixable-in-place: it named ``times_of_the_day``, which is not a domain
    (HA calls it ``tod``), and omitted ``filter``, ``manual``, and ``otp``
    — and it would have drifted again with the next helper HA ships, silently,
    because a missing domain just makes ``list_helpers`` quietly incomplete.

    Only domains that actually have config entries are looked up, so this costs
    nothing on an install without them. Failures degrade to "not a helper",
    which under-reports rather than inventing one.
    """
    from homeassistant.loader import Integration, async_get_integrations  # noqa: PLC0415

    domains = {entry.domain for entry in hass.config_entries.async_entries()}
    if not domains:
        return set()
    try:
        resolved = await async_get_integrations(hass, domains)
    except Exception as exc:  # noqa: BLE001 — an inventory must not break on one bad manifest
        _LOGGER.debug("Could not resolve integration types for helpers: %s", exc)
        return set()
    return {
        name
        for name, integration in resolved.items()
        if isinstance(integration, Integration) and integration.integration_type == "helper"
    }


async def helper_overview(hass: HomeAssistant, domain: str | None = None) -> dict[str, Any]:
    """List the home's helper entities, optionally for one helper domain.

    Covers both kinds HA ships: the storage-collection helpers above, and the
    config-entry helpers (``group``, ``derivative``, ``threshold``,
    ``utility_meter``, …) which appear in the entity registry with a config
    entry behind them. A caller wiring an automation to "the guest mode toggle"
    needs the entity_id, and it is the same lookup either way.
    """
    ent_reg = er.async_get(hass)
    wanted = str(domain or "").strip().lower()
    helper_domains = await _config_entry_helper_domains(hass)

    records: list[dict[str, Any]] = []
    for entry in ent_reg.entities.values():
        entity_domain = entry.entity_id.split(".", 1)[0]
        if entity_domain not in _STORAGE_HELPER_DOMAINS:
            continue
        if wanted and entity_domain != wanted:
            continue
        state = hass.states.get(entry.entity_id)
        records.append(
            {
                "entity_id": entry.entity_id,
                "domain": entity_domain,
                "name": sanitize_untrusted_text(
                    entry.name or entry.original_name or entry.entity_id, 80
                ),
                "state": state.state if state else "unavailable",
                "area_id": _entity_display_area(hass, entry),
            }
        )

    config_entry_helpers = []
    for config_entry in hass.config_entries.async_entries():
        if config_entry.domain not in helper_domains:
            continue
        if wanted and config_entry.domain != wanted:
            continue
        entity_ids = [
            e.entity_id for e in er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)
        ]
        if not entity_ids:
            continue
        config_entry_helpers.append(
            {
                "entry_id": config_entry.entry_id,
                "domain": config_entry.domain,
                "name": sanitize_untrusted_text(config_entry.title, 80),
                "entity_ids": entity_ids[:_MAX_LISTED],
            }
        )

    result: dict[str, Any] = {
        "helpers": sorted(records, key=lambda r: r["entity_id"])[:_MAX_LISTED],
        "count": len(records),
        "config_entry_helpers": config_entry_helpers[:_MAX_LISTED],
    }
    if len(records) > _MAX_LISTED:
        result["helpers_omitted"] = len(records) - _MAX_LISTED
    return result


# ── Floors ───────────────────────────────────────────────────────────────────
#
# A floor groups areas, and `_ensure_floor` has always created one as a side
# effect of placing an area — so a home could accumulate floors with no way to
# list, rename or remove them. These close that asymmetry.


def floor_overview(hass: HomeAssistant) -> dict[str, Any]:
    """Every floor with the areas on it.

    Ordered by ``level`` because that is the only field carrying the storeys'
    real relationship: a caller asked "what's upstairs" needs to know which
    floor is above which, and name order says nothing about it. A floor with no
    level sorts last rather than as zero — unset is not the ground floor.
    """
    area_registry = ar.async_get(hass)
    by_floor: dict[str, list[str]] = {}
    for area in area_registry.async_list_areas():
        if area.floor_id:
            by_floor.setdefault(area.floor_id, []).append(sanitize_untrusted_text(area.name, 60))

    floors = [
        {
            "floor_id": floor.floor_id,
            "name": sanitize_untrusted_text(floor.name, 60),
            "level": floor.level,
            "icon": floor.icon,
            "aliases": sorted(sanitize_untrusted_text(a, 40) for a in floor.aliases),
            "areas": sorted(by_floor.get(floor.floor_id, [])),
        }
        for floor in fr.async_get(hass).async_list_floors()
    ]
    floors.sort(key=lambda f: (f["level"] is None, f["level"] or 0, f["name"]))

    unassigned = sorted(
        sanitize_untrusted_text(a.name, 60)
        for a in area_registry.async_list_areas()
        if not a.floor_id
    )
    result: dict[str, Any] = {"count": len(floors), "floors": floors}
    if unassigned:
        # Named, not just counted: "which areas have no floor" is the question
        # that follows "list the floors", and a count sends the caller back for
        # a second round trip against the area list.
        result["areas_without_a_floor"] = unassigned
    return result


def async_create_floor(
    hass: HomeAssistant,
    *,
    name: str,
    level: int | None = None,
    icon: str | None = None,
    aliases: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Create a floor, or report the existing one with the same name.

    Reports a duplicate as ``status: "exists"`` rather than refusing, matching
    :func:`async_create_area`: HA's own ``async_create`` raises on a duplicate
    name, and a raised error here reads to the model as a failure worth
    retrying differently rather than as "it is already there".
    """
    name = str(name or "").strip()
    if not name:
        return {"error": "A floor name is required."}

    # The registry's own name lookup, not `resolve_floor`. That resolver also
    # matches aliases — deliberately, since a caller naming a floor from memory
    # should find it — but HA enforces uniqueness on the NAME alone, so a floor
    # whose name equals another's alias is one a user can legitimately create.
    # Same trap as the category duplicate check.
    existing = fr.async_get(hass).async_get_floor_by_name(name)
    if existing is not None:
        return {
            "status": "exists",
            "floor_id": existing.floor_id,
            "name": sanitize_untrusted_text(existing.name, 60),
            "message": (
                f"A floor named '{sanitize_untrusted_text(existing.name, 60)}' already exists."
            ),
        }

    floor = fr.async_get(hass).async_create(
        name,
        level=level,
        icon=str(icon).strip() or None if icon else None,
        aliases={str(a).strip() for a in aliases if str(a).strip()} if aliases else None,
    )
    return {
        "status": "created",
        "floor_id": floor.floor_id,
        "name": sanitize_untrusted_text(floor.name, 60),
        "level": floor.level,
    }


def async_update_floor(
    hass: HomeAssistant,
    *,
    floor: str,
    new_name: str | None = None,
    level: int | None = None,
    icon: str | None = None,
    aliases: Iterable[str] | None = None,
    clear: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Rename a floor or change its level, icon, or aliases.

    ``clear`` names fields to REMOVE. Setting and clearing need separate
    arguments because an empty string cannot mean "clear" here: `_opt_str`
    treats blank as absent throughout this codebase, precisely because models
    fill unused optional params with `""`, so reading that as a clear would
    strip the icon off any floor a padded call touched. Same shape as
    ``update_dashboard_view``.

    ``floor_id`` is derived from the name at creation and never rewritten, so a
    rename leaves every area's ``floor_id`` pointing at the right storey — no
    reference-chasing needed, unlike an entity_id rename.
    """
    entry, error = resolve_floor(hass, floor)
    if error or entry is None:
        return {"error": error or "Floor not found."}

    updates: dict[str, Any] = {}
    if new_name and str(new_name).strip():
        wanted = str(new_name).strip()
        # By NAME, as `async_create_floor` does. `resolve_floor` matches aliases
        # too, so a rename onto a name that is another floor's alias was refused
        # — a rename creation would have allowed, which is the inconsistency
        # rather than merely the stricter half.
        clash = fr.async_get(hass).async_get_floor_by_name(wanted)
        if clash is not None and clash.floor_id != entry.floor_id:
            return {
                "error": (
                    f"A floor named '{sanitize_untrusted_text(clash.name, 60)}' already exists."
                )
            }
        updates["name"] = wanted
    if level is not None:
        updates["level"] = level
    if icon and str(icon).strip():
        updates["icon"] = str(icon).strip()
    if aliases is not None:
        updates["aliases"] = {str(a).strip() for a in aliases if str(a).strip()}

    for field in clear or ():
        if field not in ("icon", "level"):
            return {"error": f"clear accepts 'icon' or 'level', not '{field}'."}
        updates[field] = None

    if not updates:
        return {"status": "unchanged", "floor_id": entry.floor_id}

    updated = fr.async_get(hass).async_update(entry.floor_id, **updates)
    return {
        "status": "updated",
        "floor_id": updated.floor_id,
        "name": sanitize_untrusted_text(updated.name, 60),
        "level": updated.level,
        "changed": sorted(updates),
    }


def floor_dependents(hass: HomeAssistant, floor_id: str) -> dict[str, Any]:
    """What a floor's removal would affect: the areas standing on it."""
    return {
        "areas": sorted(
            sanitize_untrusted_text(a.name, 60)
            for a in ar.async_get(hass).async_list_areas()
            if a.floor_id == floor_id
        )
    }


async def async_delete_floor(hass: HomeAssistant, floor_id: str) -> dict[str, Any]:
    """Delete a floor. Its areas survive, unassigned.

    HA's area registry listens for the floor-removed event and clears each
    area's ``floor_id``, so nothing is left dangling — but nothing announces it
    either, which is why the confirmation card carries the count.
    """
    registry = fr.async_get(hass)
    entry = registry.async_get_floor(floor_id)
    if entry is None:
        return {"error": f"No floor '{sanitize_untrusted_text(floor_id, 60)}'."}

    freed = floor_dependents(hass, floor_id)["areas"]
    registry.async_delete(floor_id)
    return {
        "status": "deleted",
        "floor_id": floor_id,
        "name": sanitize_untrusted_text(entry.name, 60),
        "areas_unassigned": freed,
    }
