"""Read what an automation, a scene or a device is actually wired to do.

A name or description is the author's claim about an automation or scene, not
its behaviour, and the two drift: a "Goodnight Scene" automation that turns
porch lights on after sunset, a Goodnight scene that does not list the bathroom
lights. A diagnosis has to come from these reads, never from the name.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from .helpers import resolve_domain_ref, sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, State

_LOGGER = logging.getLogger(__name__)

_SCENE_NOTE = (
    "A scene sets ONLY the entities listed here, to the states listed; every other "
    "entity is left exactly as it was."
)
_REFERENCES_NOTE = (
    "Each list says that the item mentions the entity or device somewhere — as a "
    "trigger, condition, target or member. Read the item itself (get_automation, "
    "get_script, get_scene) to see which before describing what it does."
)


async def get_automation_config(hass: HomeAssistant, ref: str) -> dict[str, Any]:
    """Return one automation's configuration, whatever file or store holds it."""
    from .mcp_server.automations import _tool_get_automation  # noqa: PLC0415

    state, error = resolve_domain_ref(hass, "automation", ref)
    if state is None:
        return {"error": error}
    detail = await _tool_get_automation(hass, {"entity_id": state.entity_id})
    if "error" in detail:
        return detail
    config = detail.get("yaml") or _loaded_automation_yaml(hass, state.entity_id)
    return {
        "entity_id": state.entity_id,
        "name": detail.get("alias", ""),
        "status": detail.get("status"),
        "last_triggered": _iso(state.attributes.get("last_triggered")),
        "selora_managed": detail.get("selora_managed", False),
        "config": config or None,
    }


async def get_scene_config(hass: HomeAssistant, ref: str) -> dict[str, Any]:
    """Return the entities a scene sets and the state it sets each one to."""
    from .mcp_server.scenes import _tool_get_scene  # noqa: PLC0415

    state, error = resolve_domain_ref(hass, "scene", ref)
    if state is None:
        return {"error": error}
    detail = await _tool_get_scene(hass, {"entity_id": state.entity_id})
    if "error" in detail:
        return detail

    entities: dict[str, Any] | None = detail.get("entities") or None
    if detail.get("source") == "yaml":
        # scenes.yaml is authoritative, including a scene that sets nothing.
        entities = entities or {}
    result: dict[str, Any] = {
        "entity_id": state.entity_id,
        "name": detail.get("name", ""),
        "last_activated": state.state if state.state not in ("unknown", "") else None,
        "selora_managed": detail.get("selora_managed", False),
    }
    if entities is None:
        # Scenes outside scenes.yaml (configuration.yaml, packages) keep their
        # target states on the loaded entity.
        entities = _loaded_scene_states(hass, state.entity_id)
    if entities is None:
        result["entities"] = {}
        result["note"] = (
            "This scene's contents are not exposed — it is provided by an "
            "integration, which keeps them itself."
        )
        return result
    result["entities"] = entities
    result["note"] = _SCENE_NOTE
    return result


def _loaded_automation_yaml(hass: HomeAssistant, entity_id: str) -> str:
    """Return the config a loaded automation entity was built from, as YAML.

    automations.yaml is one source among several (packages, ``!include_dir``);
    the loaded entity holds the config whichever it was. That config has
    ``!secret`` values resolved, so it is read only behind ``get_automation``'s
    admin gate — core's ``automation/config`` is admin-only for the same reason.
    The loader's line-number ``str``/``dict`` subclasses would dump as
    ``!!python/object`` tags; a JSON round trip reduces them to plain values.
    """
    from homeassistant.components.automation import DATA_COMPONENT  # noqa: PLC0415
    import yaml  # noqa: PLC0415

    component = hass.data.get(DATA_COMPONENT)
    entity = component.get_entity(entity_id) if component else None
    raw = getattr(entity, "raw_config", None)
    if not isinstance(raw, dict):
        return ""
    plain = json.loads(json.dumps(raw, default=str))
    return str(yaml.safe_dump(plain, allow_unicode=True, default_flow_style=False, sort_keys=False))


def _loaded_scene_states(hass: HomeAssistant, entity_id: str) -> dict[str, Any] | None:
    """Return the target states a loaded Home Assistant scene entity applies.

    ``None`` when the scene is not one of Home Assistant's own (an integration
    scene keeps its contents itself); ``{}`` is a scene that sets nothing.
    """
    from homeassistant.components.homeassistant.scene import (  # noqa: PLC0415
        DATA_PLATFORM,
    )

    platform = hass.data.get(DATA_PLATFORM)
    entity = platform.entities.get(entity_id) if platform else None
    scene_config = getattr(entity, "scene_config", None)
    if scene_config is None:
        return None
    states = getattr(scene_config, "states", None) or {}
    return {
        member: {"state": target.state, **dict(target.attributes)}
        if target.attributes
        else target.state
        for member, target in states.items()
    }


def find_references(hass: HomeAssistant, ref: str) -> dict[str, Any]:
    """Return the automations, scripts and scenes that mention an entity or device.

    An entity reference includes its device's: a device trigger names the
    device, not the entity, and "what does this button do?" is asked about the
    button entity the user can see.
    """
    from homeassistant.components.automation import (  # noqa: PLC0415
        automations_with_device,
        automations_with_entity,
    )
    from homeassistant.components.homeassistant.scene import (  # noqa: PLC0415
        scenes_with_entity,
    )
    from homeassistant.components.script import (  # noqa: PLC0415
        scripts_with_device,
        scripts_with_entity,
    )
    from homeassistant.helpers import (  # noqa: PLC0415
        device_registry as dr,
    )
    from homeassistant.helpers import (  # noqa: PLC0415
        entity_registry as er,
    )

    ref = str(ref or "").strip()
    if not ref:
        return {"error": "An entity_id or device_id is required."}

    entity_id: str | None = None
    device_id: str | None = None
    if "." in ref:
        entry = er.async_get(hass).async_get(ref)
        if entry is None and hass.states.get(ref) is None:
            return {"error": f"No entity '{sanitize_untrusted_text(ref, 80)}'."}
        entity_id = ref
        device_id = entry.device_id if entry else None
    elif dr.async_get(hass).async_get(ref) is not None:
        device_id = ref
    else:
        return {
            "error": (
                f"'{sanitize_untrusted_text(ref, 80)}' is neither an entity_id nor a "
                "device_id. Resolve it with search_entities or list_devices first."
            )
        }

    automations: set[str] = set()
    scripts: set[str] = set()
    scenes: set[str] = set()
    if entity_id:
        automations.update(automations_with_entity(hass, entity_id))
        scripts.update(scripts_with_entity(hass, entity_id))
        scenes.update(scenes_with_entity(hass, entity_id))
    if device_id:
        automations.update(automations_with_device(hass, device_id))
        scripts.update(scripts_with_device(hass, device_id))

    result: dict[str, Any] = {
        "automations": _named(hass, automations),
        "scripts": _named(hass, scripts),
        "scenes": _named(hass, scenes),
        "note": _REFERENCES_NOTE,
    }
    if entity_id:
        result["entity_id"] = entity_id
    if device_id:
        result["device_id"] = device_id
    return result


def _named(hass: HomeAssistant, entity_ids: set[str]) -> list[dict[str, str]]:
    return [{"entity_id": eid, "name": _name(hass, eid)} for eid in sorted(entity_ids)]


def _name(hass: HomeAssistant, entity_id: str) -> str:
    state: State | None = hass.states.get(entity_id)
    return sanitize_untrusted_text(state.name if state else entity_id, 120)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)
