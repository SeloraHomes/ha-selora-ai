"""Removing a device, entity, integration or blueprint from chat, behind a card.

One chat tool covers the four (every cloud turn carries every tool's schema).
The preview is each kind's own removal check, asked without confirmation —
what it refuses, chat refuses — turned into a delete-card descriptor carrying
the target's identity:

* device — its id (immutable);
* entity — its registry entry id (an entity_id is a name a rename frees);
* integration — its entry_id (immutable);
* blueprint — its file's content hash (a deleted path can be taken again).

Confirming replays the same removal with that identity, so what goes is what
the card named.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

REMOVAL_KINDS: Final = ("device", "entity", "integration", "blueprint")


def _card(
    kind: str, target_id: str, label: str, fingerprint: str, entity_id: str = ""
) -> dict[str, Any]:
    return {
        "requires_approval": True,
        "delete": {
            "kind": kind,
            "target_id": target_id,
            "entity_id": entity_id,
            "name": label,
            "label": sanitize_untrusted_text(label, 300),
            "fingerprint": fingerprint,
        },
    }


def _used_by(hint: str) -> str:
    """The 'used by …' clause the removal checks put in their hint, if any."""
    marker = ", used by "
    if marker not in hint:
        return ""
    return " — used by " + hint.split(marker, 1)[1].split(".", 1)[0]


async def async_preview_removal(
    hass: HomeAssistant, kind: str, target: str, domain: str | None = None
) -> dict[str, Any]:
    """A delete card for removing *target*, or why it cannot be removed."""
    from .blueprint_import import async_blueprint_fingerprint, async_delete_blueprint
    from .integration_manager import async_remove_integration
    from .registry_removal import (
        async_remove_device_on_request,
        async_remove_entity_on_request,
    )

    kind = str(kind or "").strip()
    target = str(target or "").strip()
    if kind == "device":
        res = await async_remove_device_on_request(hass, target)
        if "error" in res:
            return res
        device = res["device"]
        label = f"Remove {device['name']} and its {len(res['entities'])} entities"
        if parts := res.get("parts"):
            label += f" (with {len(parts)} part(s))"
        return _card("device", device["id"], label + _used_by(res["hint"]), device["id"])
    if kind == "entity":
        res = await async_remove_entity_on_request(hass, target)
        if "error" in res:
            return res
        entity_id = res["entity_id"]
        return _card(
            "entity",
            entity_id,
            f"Remove {entity_id}" + _used_by(res["hint"]),
            res["registry_id"],
            entity_id=entity_id,
        )
    if kind == "integration":
        res = await async_remove_integration(hass, target)
        if "error" in res:
            return res
        row = res["integration"]
        name = row["title"] or row["domain"]
        return _card(
            "integration",
            row["entry_id"],
            f"Remove the {name} integration with its {row['devices']} device(s) and "
            f"{row['entities']} entities",
            row["entry_id"],
        )
    if kind == "blueprint":
        res = await async_delete_blueprint(hass, str(domain or ""), target)
        if "error" in res:
            return res
        fingerprint = await async_blueprint_fingerprint(hass, res["domain"], res["path"])
        if fingerprint is None:
            return {"error": "That blueprint is gone."}
        return _card(
            "blueprint",
            f"{res['domain']}/{res['path']}",
            f"Delete the {res['domain']} blueprint {res['path']}",
            fingerprint,
        )
    return {"error": f"kind must be one of {', '.join(REMOVAL_KINDS)}."}


async def async_confirm_removal(hass: HomeAssistant, descriptor: dict[str, Any]) -> dict[str, Any]:
    """Carry out a confirmed removal card, on the identity it carries."""
    from .blueprint_import import async_delete_blueprint
    from .integration_manager import async_remove_integration
    from .registry_removal import (
        async_remove_device_on_request,
        async_remove_entity_on_request,
    )

    kind = str(descriptor.get("kind") or "")
    target_id = str(descriptor.get("target_id") or "").strip()
    fingerprint = str(descriptor.get("fingerprint") or "")
    if not target_id or not fingerprint:
        return {"error": "missing identity; not removed"}
    if kind == "device":
        return await async_remove_device_on_request(hass, target_id, confirmed=True)
    if kind == "entity":
        return await async_remove_entity_on_request(
            hass, target_id, confirmed=True, registry_id=fingerprint
        )
    if kind == "integration":
        return await async_remove_integration(hass, target_id, confirmed=True)
    if kind == "blueprint":
        domain, _, path = target_id.partition("/")
        return await async_delete_blueprint(
            hass, domain, path, confirmed=True, expected_fingerprint=fingerprint
        )
    return {"error": f"unknown removal kind {kind}"}
