"""MCP tools for scripts, labels, helpers, logs and traces."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .common import _sanitize

_LOGGER = logging.getLogger(__name__)


# ── Script / label / helper / diagnostic tools ────────────────────────────────


async def _tool_list_scripts(hass: HomeAssistant, _arguments: dict[str, Any]) -> dict[str, Any]:
    """Every script with its alias and step count."""
    from ..script_manager import async_list_scripts  # noqa: PLC0415

    return await async_list_scripts(hass)


async def _tool_get_script(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """One script's full configuration."""
    from ..script_manager import async_get_script  # noqa: PLC0415

    return await async_get_script(hass, str(arguments.get("script", "")))


async def _tool_set_script(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create or wholesale-replace a script."""
    from ..script_manager import async_set_script  # noqa: PLC0415
    from ..tool_executor import _opt_str  # noqa: PLC0415

    sequence = arguments.get("sequence")
    if not isinstance(sequence, list):
        return {"error": "sequence must be a list of action steps."}
    return await async_set_script(
        hass,
        alias=str(arguments.get("alias", "")),
        sequence=sequence,
        object_id=_opt_str(arguments.get("object_id")),
        description=_opt_str(arguments.get("description")),
        mode=_opt_str(arguments.get("mode")),
        icon=_opt_str(arguments.get("icon")),
        expected_fingerprint=_opt_str(arguments.get("expected_fingerprint")),
        expect_create=bool(arguments.get("expect_create")),
    )


async def _tool_delete_script(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a script outright (MCP clients run their own confirmation)."""
    from ..script_manager import async_delete_script  # noqa: PLC0415
    from ..tool_executor import _opt_str  # noqa: PLC0415

    return await async_delete_script(
        hass,
        str(arguments.get("script", "")),
        expected_fingerprint=_opt_str(arguments.get("expected_fingerprint")),
    )


async def _preview_delete_script(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Resolve a delete_script target WITHOUT deleting it.

    ``target_id`` is the object_id, which is the script's key in
    ``scripts.yaml`` and cannot change without the script being rewritten — so
    it stays valid between the card being rendered and the user tapping Delete.
    """
    from ..script_manager import (  # noqa: PLC0415
        ScriptsFileError,
        _load,
        _unreadable,
        resolve_script,
        script_dependents,
        script_fingerprint,
    )

    try:
        scripts = await _load(hass)
    except ScriptsFileError as exc:
        return _unreadable(exc)
    object_id, ambiguous = resolve_script(scripts, str(arguments.get("script", "")))
    if ambiguous:
        return {"error": ambiguous}
    if object_id is None:
        return {"error": f"No script matching '{_sanitize(arguments.get('script'), 60)}'."}

    entity_id = f"script.{object_id}"
    alias = str((scripts[object_id] or {}).get("alias") or object_id)
    label = _sanitize(alias) or entity_id

    dependents = script_dependents(hass, entity_id)
    callers = len(dependents["automations"]) + len(dependents["scripts"])
    if callers:
        label = f"{label} — called by {callers} automation{'s' if callers != 1 else ''}/script"

    return {
        "requires_approval": True,
        "delete": {
            "kind": "script",
            "target_id": object_id,
            "entity_id": entity_id,
            "name": _sanitize(alias),
            "label": label,
            # An object_id is a slug and freely reusable, and an alias survives
            # an edit — so neither identifies the script the user approved.
            # Hashing the stored config catches a recreate AND an in-place edit.
            "fingerprint": script_fingerprint(scripts[object_id]),
        },
    }


async def _tool_list_labels(hass: HomeAssistant, _arguments: dict[str, Any]) -> dict[str, Any]:
    """Every label with per-target-kind usage counts."""
    from ..label_manager import label_overview  # noqa: PLC0415

    return label_overview(hass)


async def _tool_create_label(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a label, or report the existing one with that name."""
    from ..label_manager import async_create_label  # noqa: PLC0415
    from ..tool_executor import _opt_str  # noqa: PLC0415

    return async_create_label(
        hass,
        name=str(arguments.get("name", "")),
        icon=_opt_str(arguments.get("icon")),
        color=_opt_str(arguments.get("color")),
    )


async def _tool_assign_labels(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Apply label deltas across entities, devices, and areas."""
    from ..label_manager import async_assign_labels  # noqa: PLC0415
    from ..tool_executor import _as_list  # noqa: PLC0415

    return await async_assign_labels(
        hass,
        add_labels=_as_list(arguments.get("add_labels")),
        remove_labels=_as_list(arguments.get("remove_labels")),
        entity_ids=_as_list(arguments.get("entity_ids")),
        device_ids=_as_list(arguments.get("device_ids")),
        areas=_as_list(arguments.get("areas")),
    )


async def _tool_delete_label(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a label outright (MCP clients run their own confirmation)."""
    from ..label_manager import async_delete_label, resolve_label  # noqa: PLC0415

    label, error = resolve_label(hass, str(arguments.get("label", "")))
    if error or label is None:
        return {"error": error or "Label not found"}
    return async_delete_label(hass, label.label_id)


async def _preview_delete_label(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Resolve a delete_label target WITHOUT deleting it."""
    from ..label_manager import label_dependents, label_usage, resolve_label  # noqa: PLC0415

    label, error = resolve_label(hass, str(arguments.get("label", "")))
    if error or label is None:
        return {"error": error or "Label not found"}

    counts = label_usage(hass, label.label_id)
    label_text = _sanitize(label.name) or label.label_id
    carried = counts["entity_count"] + counts["device_count"] + counts["area_count"]
    if carried:
        label_text = f"{label_text} — on {carried} target{'s' if carried != 1 else ''}"

    # Carriers merely lose a tag; a targeter silently stops matching. Named
    # separately so the card distinguishes the recoverable from the invisible.
    dependents = label_dependents(hass, label.label_id)
    targeting = len(dependents["automations"]) + len(dependents["scripts"])
    if targeting:
        label_text = (
            f"{label_text}; {targeting} automation{'s' if targeting != 1 else ''}/script target it"
        )

    return {
        "requires_approval": True,
        "delete": {
            "kind": "label",
            "target_id": label.label_id,
            "entity_id": "",
            "name": _sanitize(label.name),
            "label": label_text,
            # LabelRegistry._generate_id derives the id from the name, exactly
            # as areas do — same reuse hazard, same fingerprint.
            "fingerprint": label.created_at.isoformat() if label.created_at else "",
        },
    }


async def _tool_list_helpers(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Helper entities, optionally for one helper domain."""
    from ..registry_manager import helper_overview  # noqa: PLC0415
    from ..tool_executor import _opt_str  # noqa: PLC0415

    return await helper_overview(hass, _opt_str(arguments.get("domain")))


async def _tool_update_helper(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Change a UI-created helper's settings, keeping the rest."""
    from ..helper_manager import async_update_helper  # noqa: PLC0415
    from ..tool_executor import update_helper_kwargs  # noqa: PLC0415

    return await async_update_helper(hass, **update_helper_kwargs(arguments))


async def _tool_delete_helper(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a UI-created helper outright (MCP clients run their own confirmation)."""
    from ..helper_manager import async_delete_helper  # noqa: PLC0415

    return await async_delete_helper(hass, str(arguments.get("entity_id", "")))


async def _tool_create_helper(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a helper: a storage helper directly, any other through its setup flow.

    Chat proposes a storage helper for the panel to create; MCP has no panel,
    so it is created here, after the same validation.
    """
    from ..helper_flow import async_create_flow_helper  # noqa: PLC0415
    from ..helper_manager import CREATABLE_HELPER_DOMAINS, async_create_helper  # noqa: PLC0415
    from ..tool_executor import create_helper_fields, flow_helper_args  # noqa: PLC0415

    domain = str(arguments.get("domain", "")).strip().lower()
    if domain in CREATABLE_HELPER_DOMAINS:
        return await async_create_helper(hass, domain, create_helper_fields(arguments))
    return await async_create_flow_helper(hass, domain, *flow_helper_args(arguments))


async def _tool_get_logs(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Recent deduplicated errors and warnings."""
    from ..diagnostics_tools import get_logs  # noqa: PLC0415
    from ..tool_executor import _opt_str  # noqa: PLC0415

    return get_logs(
        hass,
        level=_opt_str(arguments.get("level")),
        contains=_opt_str(arguments.get("contains")),
    )


async def _tool_get_automation_traces(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Recent runs of one automation."""
    from ..diagnostics_tools import get_automation_traces  # noqa: PLC0415

    return await get_automation_traces(hass, str(arguments.get("automation", "")))


async def _tool_find_references(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Automations, scripts and scenes that use an entity or device."""
    from ..config_inspect import find_references  # noqa: PLC0415

    return find_references(hass, str(arguments.get("target", "")))
