"""MCP tools for areas, floors, categories, blueprints, services and entity/device settings."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .common import _sanitize

_LOGGER = logging.getLogger(__name__)


# ── Registry tools ────────────────────────────────────────────────────────────


async def _tool_list_areas(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Every area and floor with occupancy counts."""
    from ..registry_manager import area_overview  # noqa: PLC0415

    return area_overview(hass, include_entities=bool(arguments.get("include_entities")))


async def _tool_list_services(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Callable services, optionally expanded for one domain."""
    from ..registry_manager import list_services  # noqa: PLC0415

    return list_services(hass, arguments.get("domain"))


async def _tool_assign_area(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Place entities and/or devices in an area."""
    from ..registry_manager import async_assign_area  # noqa: PLC0415
    from ..tool_executor import _as_list  # noqa: PLC0415

    return await async_assign_area(
        hass,
        area=str(arguments.get("area", "")),
        entity_ids=_as_list(arguments.get("entity_ids")),
        device_ids=_as_list(arguments.get("device_ids")),
    )


async def _tool_create_area(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create an area, reporting an existing one of the same name instead."""
    from ..registry_manager import async_create_area  # noqa: PLC0415
    from ..tool_executor import _opt_list, _opt_str  # noqa: PLC0415

    return async_create_area(
        hass,
        name=str(arguments.get("name", "")),
        floor=_opt_str(arguments.get("floor")),
        icon=_opt_str(arguments.get("icon")),
        aliases=_opt_list(arguments.get("aliases")),
    )


def _update_area_kwargs(arguments: dict[str, Any]) -> dict[str, Any]:
    """``update_area``'s arguments, one reading for chat and MCP."""
    from ..tool_executor import _opt_list, _opt_str  # noqa: PLC0415

    return {
        "area": str(arguments.get("area", "")),
        "new_name": _opt_str(arguments.get("new_name")),
        "floor": _opt_str(arguments.get("floor")),
        "icon": _opt_str(arguments.get("icon")),
        "aliases": _opt_list(arguments.get("aliases")),
        "clear": _opt_list(arguments.get("clear")),
        "temperature_sensor": _opt_str(arguments.get("temperature_sensor")),
        "humidity_sensor": _opt_str(arguments.get("humidity_sensor")),
    }


async def _tool_update_area(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Rename an area or change its floor, icon, or aliases."""
    from ..registry_manager import async_update_area  # noqa: PLC0415

    return async_update_area(hass, **_update_area_kwargs(arguments))


async def _tool_delete_area(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete an area outright.

    The MCP surface deletes directly, matching ``_tool_delete_group``: an MCP
    client runs its own confirmation UX, whereas the chat path routes through
    :func:`_preview_delete_area` and the user's tap on a card.
    """
    from ..registry_manager import async_delete_area, resolve_area  # noqa: PLC0415

    entry, error = resolve_area(hass, str(arguments.get("area", "")))
    if error or entry is None:
        return {"error": error or "Area not found"}
    return await async_delete_area(hass, entry.id)


def _update_entity_kwargs(arguments: dict[str, Any]) -> dict[str, Any]:
    """``update_entity``'s arguments as ``async_update_entity`` takes them —
    one reading for the direct call, the preview and the confirmed card."""
    from ..entity_exposure import ASSISTANTS  # noqa: PLC0415
    from ..tool_executor import _opt_bool, _opt_list, _opt_options, _opt_str  # noqa: PLC0415

    expose = {
        name: flag
        for name in ASSISTANTS
        if (flag := _opt_bool(arguments.get(f"expose_to_{name}"))) is not None
    }
    return {
        "entity_id": str(arguments.get("entity_id", "")),
        "new_name": _opt_str(arguments.get("new_name")),
        "aliases": _opt_list(arguments.get("aliases")),
        "icon": _opt_str(arguments.get("icon")),
        "hidden": _opt_bool(arguments.get("hidden")),
        "disabled": _opt_bool(arguments.get("disabled")),
        "expose": expose,
        "new_entity_id": _opt_str(arguments.get("new_entity_id")),
        "clear": _opt_list(arguments.get("clear")),
        "show_as": _opt_str(arguments.get("show_as")),
        "settings": _opt_options(arguments.get("settings")),
    }


async def _tool_update_entity(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Change one entity's registry settings."""
    from ..registry_manager import async_update_entity  # noqa: PLC0415

    return await async_update_entity(hass, **_update_entity_kwargs(arguments))


def _update_device_kwargs(arguments: dict[str, Any]) -> dict[str, Any]:
    """``update_device``'s arguments, one reading for the direct call, the
    preview and the confirmed card."""
    from ..tool_executor import _opt_bool, _opt_list, _opt_str  # noqa: PLC0415

    return {
        "device": str(arguments.get("device", "")),
        "new_name": _opt_str(arguments.get("new_name")),
        "area": _opt_str(arguments.get("area")),
        "disabled": _opt_bool(arguments.get("disabled")),
        "clear": _opt_list(arguments.get("clear")),
    }


async def _tool_update_device(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Rename a device, move it to an area, or disable it."""
    from ..registry_manager import async_update_device  # noqa: PLC0415

    return await async_update_device(hass, **_update_device_kwargs(arguments))


async def _tool_list_blueprints(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Installed blueprints with the inputs each asks for."""
    from ..blueprint_manager import async_list_blueprints  # noqa: PLC0415
    from ..tool_executor import _opt_str  # noqa: PLC0415

    return await async_list_blueprints(hass, _opt_str(arguments.get("domain")))


async def _tool_get_blueprint(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """One blueprint's inputs in full."""
    from ..blueprint_manager import async_get_blueprint  # noqa: PLC0415

    return await async_get_blueprint(
        hass, str(arguments.get("domain", "")), str(arguments.get("path", ""))
    )


async def _tool_list_categories(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Categories with how many entities each holds."""
    from ..category_manager import category_overview  # noqa: PLC0415
    from ..tool_executor import _opt_str  # noqa: PLC0415

    return category_overview(hass, _opt_str(arguments.get("scope")))


async def _tool_create_category(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a category within one list, or change one."""
    from ..category_manager import async_create_category  # noqa: PLC0415
    from ..tool_executor import category_kwargs  # noqa: PLC0415

    return async_create_category(hass, **category_kwargs(arguments))


async def _tool_assign_category(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """File entities under a category, or take them out of one."""
    from ..category_manager import async_assign_category  # noqa: PLC0415
    from ..tool_executor import _as_list, _opt_str  # noqa: PLC0415

    return await async_assign_category(
        hass,
        entity_ids=_as_list(arguments.get("entity_ids")),
        scope=str(arguments.get("scope", "")),
        category=_opt_str(arguments.get("category")),
    )


async def _tool_delete_category(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a category outright (MCP clients run their own confirmation)."""
    from ..category_manager import async_delete_category, resolve_category  # noqa: PLC0415

    # Stripped once, here. `resolve_category` normalizes internally, so a scope
    # with stray whitespace RESOLVES and then fails at the delete — and on the
    # preview path it is baked into `target_id`, producing a card whose confirm
    # cannot find the category it just described.
    scope = str(arguments.get("scope", "")).strip()
    entry, error = resolve_category(hass, scope, str(arguments.get("category", "")))
    if error or entry is None:
        return {"error": error or "Category not found."}
    return async_delete_category(hass, scope, entry.category_id)


async def _preview_delete_category(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Resolve a delete_category target WITHOUT deleting it.

    Entities filed under it survive — HA's entity registry calls
    ``async_clear_category_id`` on the removal event — but nothing announces it,
    so the label carries the count.
    """
    from ..category_manager import category_dependents, resolve_category  # noqa: PLC0415

    # Stripped once, here. `resolve_category` normalizes internally, so a scope
    # with stray whitespace RESOLVES and then fails at the delete — and on the
    # preview path it is baked into `target_id`, producing a card whose confirm
    # cannot find the category it just described.
    scope = str(arguments.get("scope", "")).strip()
    entry, error = resolve_category(hass, scope, str(arguments.get("category", "")))
    if error or entry is None:
        return {"error": error or "Category not found."}

    held = len(category_dependents(hass, scope, entry.category_id)["entities"])
    label = f"{_sanitize(entry.name)} ({_sanitize(scope)})"
    if held:
        label = f"{label} — {held} item{'s' if held != 1 else ''} would stop being categorised"

    return {
        "requires_approval": True,
        "delete": {
            "kind": "category",
            # The scope is part of the identity: the same name under two scopes
            # is two categories, and a target_id alone would not say which.
            "target_id": f"{scope}#{entry.category_id}",
            "entity_id": "",
            "name": _sanitize(entry.name),
            "label": label,
            # Unlike area_id and floor_id, a category_id is a random uuid rather
            # than name-derived, so it cannot be reused by a recreated category
            # and needs no timestamp to tell instances apart.
            "fingerprint": "",
        },
    }


async def _tool_list_floors(hass: HomeAssistant, _arguments: dict[str, Any]) -> dict[str, Any]:
    """Every floor with the areas standing on it."""
    from ..registry_manager import floor_overview  # noqa: PLC0415

    return floor_overview(hass)


async def _tool_create_floor(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a floor to group areas under."""
    from ..registry_manager import async_create_floor  # noqa: PLC0415
    from ..tool_executor import _opt_level, _opt_list, _opt_str  # noqa: PLC0415

    return async_create_floor(
        hass,
        name=str(arguments.get("name", "")),
        level=_opt_level(arguments.get("level")),
        icon=_opt_str(arguments.get("icon")),
        aliases=_opt_list(arguments.get("aliases")),
    )


async def _tool_update_floor(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Rename a floor or change its level, icon, or aliases."""
    from ..registry_manager import async_update_floor  # noqa: PLC0415
    from ..tool_executor import _opt_level, _opt_list, _opt_str  # noqa: PLC0415

    return async_update_floor(
        hass,
        floor=str(arguments.get("floor", "")),
        new_name=_opt_str(arguments.get("new_name")),
        level=_opt_level(arguments.get("level")),
        icon=_opt_str(arguments.get("icon")),
        aliases=_opt_list(arguments.get("aliases")),
        clear=_opt_list(arguments.get("clear")),
    )


async def _tool_delete_floor(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a floor outright (MCP clients run their own confirmation)."""
    from ..registry_manager import async_delete_floor, resolve_floor  # noqa: PLC0415

    entry, error = resolve_floor(hass, str(arguments.get("floor", "")))
    if error or entry is None:
        return {"error": error or "Floor not found."}
    return await async_delete_floor(hass, entry.floor_id)


async def _preview_delete_floor(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Resolve a delete_floor target WITHOUT deleting it.

    The areas survive — HA's area registry clears their ``floor_id`` when the
    floor-removed event fires — but nothing announces it, so the label names
    them. Naming rather than counting: "and 4 areas" does not tell the user
    whether the one they care about is among them.
    """
    from ..registry_manager import floor_dependents, resolve_floor  # noqa: PLC0415

    entry, error = resolve_floor(hass, str(arguments.get("floor", "")))
    if error or entry is None:
        return {"error": error or "Floor not found."}

    areas = floor_dependents(hass, entry.floor_id)["areas"]
    label = _sanitize(entry.name) or entry.floor_id
    if areas:
        shown = ", ".join(areas[:4])
        if len(areas) > 4:
            shown = f"{shown} and {len(areas) - 4} more"
        label = f"{label} — {shown} would lose their floor"

    return {
        "requires_approval": True,
        "delete": {
            "kind": "floor",
            "target_id": entry.floor_id,
            "entity_id": "",
            "name": _sanitize(entry.name),
            "label": label,
            # floor_id is derived from the NAME, exactly like area_id, so it is
            # reusable once the floor is gone. The creation timestamp tells the
            # instances apart if one is recreated while the card sits open.
            "fingerprint": entry.created_at.isoformat() if entry.created_at else "",
        },
    }


async def _preview_delete_area(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Resolve a delete_area target WITHOUT deleting it.

    Read-only counterpart of :func:`_tool_delete_area` for the chat path.
    ``target_id`` is the ``area_id``, which HA derives from the name at
    creation and never rewrites on rename — so it stays valid between the card
    being shown and the user tapping Delete.

    The label carries the blast radius, because the tool-loop short-circuit on
    ``requires_approval`` discards whatever prose the model wrote. Entities and
    devices are counted (they survive, merely unassigned); automations and
    scripts are called out separately because they do not fail — they keep
    running against an area that no longer matches anything.
    """
    from ..registry_manager import area_dependents, resolve_area  # noqa: PLC0415

    entry, error = resolve_area(hass, str(arguments.get("area", "")))
    if error or entry is None:
        return {"error": error or "Area not found"}

    dependents = area_dependents(hass, entry.id)
    label = _sanitize(entry.name) or entry.id
    counts = (
        (dependents["entities"], "entity", "entities"),
        (dependents["devices"], "device", "devices"),
    )
    if holds := [f"{n} {one if n == 1 else many}" for n, one, many in counts if n]:
        label = f"{label} — holds {', '.join(holds)}"
    breaks = len(dependents["automations"]) + len(dependents["scripts"])
    if breaks:
        label = f"{label}; {breaks} automation{'s' if breaks != 1 else ''}/script target it"

    return {
        "requires_approval": True,
        "delete": {
            "kind": "area",
            "target_id": entry.id,
            "entity_id": "",
            "name": _sanitize(entry.name),
            "label": label,
            # area_id is derived from the NAME (AreaRegistry._generate_id), so
            # deleting "Study" and creating a new "Study" while the card is open
            # yields the same id — the card would then delete the new area. The
            # creation timestamp distinguishes the instances.
            "fingerprint": entry.created_at.isoformat() if entry.created_at else "",
        },
    }
