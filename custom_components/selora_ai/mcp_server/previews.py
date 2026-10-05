"""Confirmation previews for destructive non-delete edits."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .common import _sanitize
from .scripts_helpers import _tool_set_script

_LOGGER = logging.getLogger(__name__)


# ── Destructive non-delete previews ───────────────────────────────────────────
#
# Deletes are not the only irreversible-in-practice writes here. Disabling an
# entity removes it from the state machine; disabling a device takes all of its
# entities with it; renaming an entity_id breaks every reference HA will not
# rewrite; replacing a script discards the sequence that was there. None of
# those has an undo the user can reach from chat, so each returns a
# confirmation descriptor instead of executing, exactly as the deletes do.
#
# ``payload`` is the full argument set, replayed verbatim by ``_resolve_approval``
# on confirm. Replaying the arguments rather than a diff means the confirm path
# runs the same validated code as the direct path — there is no second
# implementation to drift.


def _destructive_card(
    *,
    kind: str,
    verb: str,
    target_id: str,
    entity_id: str,
    name: str,
    label: str,
    fingerprint: str = "",
) -> dict[str, Any]:
    """Shape a pending destructive action for the confirmation card.

    ``target_id`` is the immutable handle the confirm path binds to, and it is
    NOT necessarily what the model passed: a caller can name a device or script
    by its mutable display name, and between the card being shown and the user
    tapping Apply that name can move to a different object. Replaying the
    original argument would then act on whatever answers to the name now.

    ``fingerprint`` carries a second identity check for targets whose id is
    itself mutable — an entity_id, which is precisely what a rename changes.

    The card deliberately carries NO payload. The arguments to replay live in
    the tool log entry beside this result, which the synthesizer already reads
    and which is never echoed back to the model. Embedding them here instead
    duplicated the model's own arguments into its context — and for a script
    replacement that means the whole proposed sequence, which
    ``ToolExecutor._truncate_result`` cannot bound because it only finds lists
    at the top level or one dict deep.
    """
    return {
        "requires_approval": True,
        "destructive": {
            "kind": kind,
            "verb": verb,
            "target_id": target_id,
            "entity_id": entity_id,
            "name": _sanitize(name),
            "label": label,
            "fingerprint": fingerprint,
        },
    }


async def _preview_update_entity(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Hold a disable or an entity_id rename for confirmation.

    Every other field ``update_entity`` accepts — friendly name, aliases, icon,
    hidden, Assist exposure — is a metadata edit the user can reverse by asking
    for the opposite, so those still execute directly. Gating them would put a
    card in front of "call it the Reading Lamp", which teaches the user to tap
    through cards without reading them.
    """
    from ..registry_manager import async_update_entity  # noqa: PLC0415
    from ..tool_executor import _opt_bool, _opt_list, _opt_str  # noqa: PLC0415

    entity_id = str(arguments.get("entity_id", "")).strip()
    disabling = _opt_bool(arguments.get("disabled")) is True
    new_entity_id = _opt_str(arguments.get("new_entity_id"))
    if not disabling and not (new_entity_id and new_entity_id != entity_id):
        return await async_update_entity(
            hass,
            entity_id=entity_id,
            new_name=_opt_str(arguments.get("new_name")),
            aliases=_opt_list(arguments.get("aliases")),
            icon=_opt_str(arguments.get("icon")),
            hidden=_opt_bool(arguments.get("hidden")),
            disabled=_opt_bool(arguments.get("disabled")),
            expose_to_assist=_opt_bool(arguments.get("expose_to_assist")),
            new_entity_id=None,
        )

    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return {"error": f"'{_sanitize(entity_id, 60)}' is not in the entity registry."}

    # Validate BEFORE offering the card. Asking the user to confirm a rename
    # that cannot happen — wrong domain, taken id, live references — spends
    # their attention on a decision with no outcome, and teaches them the card
    # is noise.
    if new_entity_id and new_entity_id != entity_id:
        from ..registry_manager import validate_entity_id_rename  # noqa: PLC0415

        if error := await validate_entity_id_rename(hass, entity_id, new_entity_id):
            return {"error": error}

    friendly = _sanitize(entry.name or entry.original_name or entity_id)

    # EVERY destructive change in this call goes on the label, not just the
    # first one matched. A single ``update_entity`` can disable an entity AND
    # rename its id, and the payload replayed on confirm applies both — so a
    # label naming only the disable would have the user approving a rename they
    # were never shown. The tool loop short-circuits on ``requires_approval``
    # and discards the model's prose, so the card is the only place this can be
    # said.
    parts: list[str] = []
    if disabling:
        parts.append(
            f"Disable {friendly} ({entity_id}) — it stops updating and leaves the state machine"
        )
    renaming = bool(new_entity_id and new_entity_id != entity_id)
    if renaming:
        parts.append(f"Rename the id {entity_id} → {new_entity_id}")
    # The verb drives the confirm-side allowlist; the label carries the detail.
    # Disable is the stronger of the two, so it names the card when both apply.
    verb = "disable" if disabling else "rename_id"
    label = "; ".join(parts)

    return _destructive_card(
        kind="entity",
        verb=verb,
        target_id=entity_id,
        entity_id=entity_id,
        name=friendly,
        label=label,
        # entity_id is exactly what a rename changes, so it cannot identify the
        # target across the life of the card. RegistryEntry.id is the registry's
        # own immutable handle; the confirm path re-resolves through it and
        # refuses if the entity_id now points somewhere else.
        fingerprint=entry.id,
    )


async def _preview_update_device(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Hold a device disable for confirmation; rename and area move execute."""
    from ..registry_manager import async_update_device, resolve_device  # noqa: PLC0415
    from ..tool_executor import _opt_bool, _opt_str  # noqa: PLC0415

    if _opt_bool(arguments.get("disabled")) is not True:
        return await async_update_device(
            hass,
            device=str(arguments.get("device", "")),
            new_name=_opt_str(arguments.get("new_name")),
            area=_opt_str(arguments.get("area")),
            disabled=_opt_bool(arguments.get("disabled")),
        )

    device, error = resolve_device(hass, str(arguments.get("device", "")))
    if error or device is None:
        return {"error": error or "Device not found"}

    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    count = len(er.async_entries_for_device(er.async_get(hass), device.id, True))
    friendly = _sanitize(device.name_by_user or device.name or device.id)
    label = f"Disable {friendly}"
    if count:
        # The blast radius is the point: disabling a device silently takes every
        # one of its entities with it, and the user is thinking about one thing.
        label = f"{label} — and its {count} entit{'ies' if count != 1 else 'y'}"

    return _destructive_card(
        kind="device",
        verb="disable",
        target_id=device.id,
        entity_id="",
        name=friendly,
        label=label,
    )


async def _preview_set_script(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Hold a script REPLACEMENT for confirmation; creation executes directly.

    ``set_script`` replaces wholesale, so overwriting discards the sequence that
    was there with no undo. Creating a new script discards nothing, and gating
    it would put a card in front of every "make me a Movie Night routine".
    """
    from ..script_manager import (  # noqa: PLC0415
        ScriptsFileError,
        _load,
        _unreadable,
        resolve_write_target,
        script_fingerprint,
    )
    from ..tool_executor import _opt_str  # noqa: PLC0415

    alias = str(arguments.get("alias", "")).strip()
    try:
        scripts = await _load(hass)
    except ScriptsFileError as exc:
        return _unreadable(exc)

    # The SAME resolver the write uses, so the two cannot disagree about
    # whether this is a creation. When they disagreed, a slug collision was
    # classified as a creation here, executed directly, and overwrote an
    # unrelated script with no card ever shown.
    existing, replaces, ambiguous = resolve_write_target(
        scripts, object_id=_opt_str(arguments.get("object_id")), alias=alias
    )
    if ambiguous:
        return {"error": ambiguous}
    if not replaces:
        # Creation executes directly, but the classification was made before the
        # write took the lock. The flag makes the write re-check it atomically
        # rather than silently becoming a replacement.
        return await _tool_set_script(hass, {**arguments, "expect_create": True})

    current = scripts[existing] or {}
    steps = current.get("sequence") or []
    friendly = _sanitize(current.get("alias") or existing)
    label = f"Replace the {friendly} script"
    if isinstance(steps, list) and steps:
        label = f"{label} — its {len(steps)} existing step{'s' if len(steps) != 1 else ''} are discarded"

    return _destructive_card(
        kind="script",
        verb="replace",
        target_id=existing,
        entity_id=f"script.{existing}",
        name=friendly,
        label=label,
        # Hash of the config being replaced. An alias survives an in-place
        # edit, so it cannot tell "the script the user approved overwriting"
        # from "a newer version of it".
        fingerprint=script_fingerprint(current),
    )
