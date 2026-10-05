"""MCP tools for group helpers."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .common import _sanitize

_LOGGER = logging.getLogger(__name__)


# ── Phase 4: Group helpers ────────────────────────────────────────────────────


async def _tool_list_groups(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List group helpers with their members.

    ``group_type`` narrows the result (e.g. only light groups). YAML-defined
    ``group.*`` entities are reported separately as read-only so the model can
    explain why they aren't editable instead of proposing a duplicate.
    """
    from ..group_manager import (  # noqa: PLC0415
        describe_group,
        group_entries,
        unmanaged_yaml_groups,
    )

    wanted = str(arguments.get("group_type", "")).strip()
    groups = [describe_group(hass, entry) for entry in group_entries(hass)]
    if wanted:
        groups = [g for g in groups if g["group_type"] == wanted]
    groups.sort(key=lambda g: g["name"].casefold())

    result: dict[str, Any] = {"groups": groups, "count": len(groups)}
    yaml_groups = unmanaged_yaml_groups(hass)
    if yaml_groups:
        result["read_only_yaml_groups"] = yaml_groups
    return result


async def _tool_create_group(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a Home Assistant group helper from a member list."""
    from ..group_manager import async_create_group  # noqa: PLC0415

    return await async_create_group(
        hass,
        name=str(arguments.get("name", "")),
        entities=arguments.get("entities"),
        group_type=str(arguments.get("group_type", "")).strip() or None,
        hide_members=bool(arguments.get("hide_members", False)),
        requires_all_members=(
            bool(arguments["requires_all_members"]) if "requires_all_members" in arguments else None
        ),
        statistic=str(arguments.get("statistic", "")).strip() or None,
    )


async def _tool_update_group(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Rename a group helper and/or change which entities belong to it."""
    from ..group_manager import async_update_group, resolve_group  # noqa: PLC0415

    entry, error = resolve_group(
        hass,
        entity_id=str(arguments.get("entity_id", "")).strip(),
        entry_id=str(arguments.get("entry_id", "")).strip(),
        name=str(arguments.get("group_name", "")).strip(),
    )
    if error or entry is None:
        return {"error": error or "Group not found"}

    new_name = arguments.get("new_name")
    return await async_update_group(
        hass,
        entry,
        name=str(new_name) if new_name is not None else None,
        entities=arguments.get("entities"),
        add_entities=arguments.get("add_entities"),
        remove_entities=arguments.get("remove_entities"),
        requires_all_members=(
            bool(arguments["requires_all_members"]) if "requires_all_members" in arguments else None
        ),
    )


async def _tool_delete_group(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a group helper.

    Identified by ``entry_id`` (what the confirmation card carries), or by
    ``entity_id`` / ``group_name`` for direct MCP callers.
    """
    from ..group_manager import async_delete_group, resolve_group  # noqa: PLC0415

    entry_id = str(arguments.get("entry_id", "")).strip()
    if not entry_id:
        entry, error = resolve_group(
            hass,
            entity_id=str(arguments.get("entity_id", "")).strip(),
            name=str(arguments.get("group_name", "")).strip(),
        )
        if error or entry is None:
            return {"error": error or "Group not found"}
        entry_id = entry.entry_id

    return await async_delete_group(hass, entry_id)


async def _preview_delete_group(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Resolve a delete_group target WITHOUT deleting it.

    Read-only counterpart of :func:`_tool_delete_group` used by the chat
    ``delete_group`` tool: it identifies the group and returns a
    ``{kind, target_id, entity_id, label}`` descriptor for the confirmation
    card. ``target_id`` is the config entry_id — immutable for the entry's
    lifetime, so unlike the id-less scene/automation paths there is no
    fingerprint to re-verify at confirm time.

    The label carries the blast radius: deleting a group that automations
    target breaks them silently, so the count belongs on the card the user
    actually reads, not in prose the tool-loop short-circuit discards.
    """
    from ..group_manager import group_dependents, group_entity_id, resolve_group  # noqa: PLC0415

    entry, error = resolve_group(
        hass,
        entity_id=str(arguments.get("entity_id", "")).strip(),
        entry_id=str(arguments.get("entry_id", "")).strip(),
        name=str(arguments.get("group_name", "")).strip(),
    )
    if error or entry is None:
        return {"error": error or "Group not found"}

    entity_id = group_entity_id(hass, entry)
    name = str(entry.options.get("name") or entry.title or "")
    label = _sanitize(name) or entity_id or entry.entry_id

    dependents = group_dependents(hass, entity_id)
    # Each kind is named rather than summed: an automation breaks, a parent
    # group quietly gets smaller, and the user needs to know which.
    in_use = len(dependents["automations"]) + len(dependents["scripts"])
    counts = (
        (in_use, f"automation{'s' if in_use != 1 else ''}/script"),
        (len(dependents["scenes"]), f"scene{'s' if len(dependents['scenes']) != 1 else ''}"),
        (len(dependents["groups"]), f"group{'s' if len(dependents['groups']) != 1 else ''}"),
    )
    if used_by := [f"{count} {noun}" for count, noun in counts if count]:
        label = f"{label} — used by {', '.join(used_by)}"

    return {
        "requires_approval": True,
        "delete": {
            "kind": "group",
            "target_id": entry.entry_id,
            "entity_id": entity_id or "",
            "name": _sanitize(name),
            "label": label,
        },
    }
