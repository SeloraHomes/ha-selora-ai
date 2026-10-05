"""Remove a device or an entity on request, after a confirmation.

The device itself is removed by ``device_removal`` — the same path the Health
card uses, which releases it from its one owning integration or changes
nothing. This adds what a request needs on top: a first answer that removes
nothing and says what would go and what uses it (Home Assistant rewrites no
automation that referred to it), and the entity counterpart.

An entity is removed only once its integration no longer provides it, which is
when Home Assistant's own UI offers "Remove": one still provided is recreated
on the next reload, so disabling it is the way to hide it. A missing state is
not proof on its own — see ``_no_longer_provided``.

A confirmed entity removal must carry the ``registry_id`` its preview returned:
an entity_id is a name, freed by a rename, and only the entity shown may go.
Selora AI's own hub device and entities are never removed this way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_CONFIRM = "Tell the user, and only once they agree call again with confirmed=true."


def _device_finders() -> list[tuple[str, Any]]:
    """Home Assistant's own device-reference lookups, for the loaded components."""
    finders: list[tuple[str, Any]] = []
    try:
        from homeassistant.components.automation import automations_with_device  # noqa: PLC0415

        finders.append(("automations", automations_with_device))
    except ImportError:
        pass
    try:
        from homeassistant.components.script import scripts_with_device  # noqa: PLC0415

        finders.append(("scripts", scripts_with_device))
    except ImportError:
        pass
    return finders


async def _used_by(
    hass: HomeAssistant, entity_ids: list[str], device_ids: list[str] | None = None
) -> list[str]:
    """What refers to any of *entity_ids* or *device_ids*, counted once each.

    Gathered as ids before counting: two entities used by different
    automations are two automations, and one used by both is one. A device
    trigger or action names the device, not an entity, so devices are asked
    too.
    """
    from .group_manager import group_dependents  # noqa: PLC0415
    from .recipes.dashboard import async_dashboards_with_entity  # noqa: PLC0415

    found: dict[str, set[str]] = {
        kind: set() for kind in ("automations", "scripts", "scenes", "groups")
    }
    dashboards: set[str] = set()
    unreadable: set[str] = set()
    for entity_id in entity_ids:
        for kind, refs in group_dependents(hass, entity_id).items():
            found.setdefault(kind, set()).update(r for r in refs if r not in entity_ids)
        referencing, cannot_read = await async_dashboards_with_entity(hass, entity_id)
        dashboards.update(referencing)
        unreadable.update(cannot_read)
    for device_id in device_ids or ():
        for kind, finder in _device_finders():
            found[kind].update(finder(hass, device_id))

    def _count(n: int, noun: str) -> str:
        return f"{n} {noun if n == 1 else noun + 's'}"

    phrases = [_count(len(refs), kind[:-1]) for kind, refs in found.items() if refs]
    if dashboards:
        phrases.append(_count(len(dashboards), "dashboard"))
    if unreadable:
        phrases.append(_count(len(unreadable), "unreadable dashboard"))
    return phrases


async def async_remove_device_on_request(
    hass: HomeAssistant, device_id: str, *, confirmed: bool = False
) -> dict[str, Any]:
    """Remove a device its integration agrees to release, once confirmed."""
    from .device_removal import async_remove_device, device_is_removable  # noqa: PLC0415

    registry = dr.async_get(hass)
    device = registry.async_get(str(device_id or "").strip())
    if device is None:
        return {
            "error": (
                f"No device {sanitize_untrusted_text(str(device_id), 40)}. Call "
                "list_devices for its id."
            )
        }
    # A child device (2026.9+) is part of its parent and has a removal API of
    # its own; the release-and-detach path would fail halfway on one.
    if parent_id := getattr(device, "parent_device_id", None):
        parent = registry.async_get(parent_id)
        parent_name = (parent.name_by_user or parent.name) if parent is not None else parent_id
        return {
            "error": (
                f"That device is part of {sanitize_untrusted_text(str(parent_name), 80)}. "
                "Remove that device, or remove the part in Settings → Devices & services."
            )
        }
    owners = [
        entry
        for entry_id in device.config_entries
        if (entry := hass.config_entries.async_get_entry(entry_id)) is not None
    ]
    if any(entry.domain == DOMAIN for entry in owners):
        return {"error": "Selora AI does not remove its own device."}
    name = sanitize_untrusted_text(device.name_by_user or device.name or device.id, 80)
    if not device_is_removable(hass, device.id):
        return {
            "error": (
                f"{name} cannot be removed from here: its integration does not let "
                "devices be removed, or several integrations share it. Remove it in "
                "Settings → Devices & services, or remove the integration."
            )
        }
    # Removing a device takes its child devices (2026.9+) with it, and their
    # entities — all of which the confirmation has to name.
    device_ids = [device.id] + [
        child.id
        for child in getattr(registry, "child_devices", ())
        if getattr(child, "parent_device_id", None) == device.id
    ]
    entities = [
        e.entity_id
        for one in device_ids
        for e in er.async_entries_for_device(
            er.async_get(hass), one, include_disabled_entities=True
        )
    ]
    if not confirmed:
        used_by = await _used_by(hass, entities, device_ids)
        return {
            "requires_confirmation": True,
            "device": {
                "id": device.id,
                "name": name,
                "integration": owners[0].domain if owners else None,
            },
            "entities": entities,
            **({"parts": len(device_ids) - 1} if len(device_ids) > 1 else {}),
            "hint": (
                f"This removes {name}"
                + (f" with its {len(device_ids) - 1} part(s)" if len(device_ids) > 1 else "")
                + f" and {len(entities)} entit(ies)"
                + (f", used by {', '.join(used_by)}" if used_by else "")
                + f". {_CONFIRM}"
            ),
        }
    try:
        await async_remove_device(hass, device.id)
    except HomeAssistantError as exc:
        return {"error": f"{name} was not removed: {sanitize_untrusted_text(str(exc), 200)}."}
    return {"status": "removed", "device_id": device.id, "name": name}


def _no_longer_provided(hass: HomeAssistant, entry: er.RegistryEntry) -> bool:
    """Whether the entity's integration is running and did not provide it.

    Only a running integration says anything: while one is failed, retrying or
    still starting, Home Assistant gives every entity of it an unavailable
    ``restored`` state, and a disabled entity has no state at all — each comes
    back. With the integration up, no state or a restored one means it ran
    without this entity.
    """
    from homeassistant.config_entries import ConfigEntryState  # noqa: PLC0415

    if entry.disabled_by is not None:
        return False
    if entry.config_entry_id:
        owner = hass.config_entries.async_get_entry(entry.config_entry_id)
        if owner is None or owner.state is not ConfigEntryState.LOADED:
            return False
    elif entry.platform not in hass.config.components:
        return False
    state = hass.states.get(entry.entity_id)
    return state is None or bool(state.attributes.get("restored"))


async def async_remove_entity_on_request(
    hass: HomeAssistant,
    entity_id: str,
    *,
    confirmed: bool = False,
    registry_id: str | None = None,
) -> dict[str, Any]:
    """Remove an entity its integration no longer provides, once confirmed."""
    entity_id = str(entity_id or "").strip().lower()
    shown = sanitize_untrusted_text(entity_id, 80)
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None:
        return {
            "error": (
                f"{shown} is not in the entity registry, so there is nothing to remove "
                "here — an entity defined in YAML is removed from YAML."
            )
        }
    if entry.platform == DOMAIN:
        return {"error": "Selora AI does not remove its own entities."}
    if not _no_longer_provided(hass, entry):
        return {
            "error": (
                f"{shown} is still provided by {entry.platform} (or it cannot be told "
                "yet: disabled, or its integration is not running), so removing it "
                "would only bring it back. Disable it with update_entity, or remove "
                "its device or integration."
            )
        }
    if not confirmed:
        used_by = await _used_by(hass, [entity_id])
        return {
            "requires_confirmation": True,
            "entity_id": entity_id,
            # The registry entry's own id, which an entity_id is not: a rename
            # frees the entity_id for another entity. The confirmed call sends it
            # back, so only the entity shown is removed.
            "registry_id": entry.id,
            "hint": (
                f"This removes {shown}"
                + (f", used by {', '.join(used_by)}" if used_by else "")
                + f". {_CONFIRM}"
            ),
        }
    if registry_id != entry.id:
        return {
            "error": (
                f"{shown} is not the entity that was shown for confirmation (or no "
                "registry_id was passed). Ask again without confirmed to see it."
            )
        }
    registry.async_remove(entity_id)
    return {"status": "removed", "entity_id": entity_id}
