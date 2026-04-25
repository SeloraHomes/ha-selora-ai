"""Scene validation -- security hardening for scene creation pipeline.

Validates that scene entities exist in HA, belong to the expected area,
and that scene payloads are safe from injection and oversized inputs.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_MAX_SCENE_NAME_LEN = 100
_MAX_ENTITIES_PER_SCENE = 50
# Domain part: lowercase letters/underscores.  Object ID: letters, digits,
# underscores, hyphens — matches the pattern accepted by scene_utils.
_ENTITY_ID_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z0-9][a-z0-9_-]*$")

# Characters that could indicate injection attempts in scene names
_UNSAFE_NAME_RE = re.compile(r"[<>&\"'`;\{\}\[\]\\]")


def sanitize_scene_name(name: str) -> str:
    """Sanitize a scene name: strip, truncate, and remove unsafe characters."""
    clean = name.strip()
    clean = _UNSAFE_NAME_RE.sub("", clean)
    if len(clean) > _MAX_SCENE_NAME_LEN:
        clean = clean[:_MAX_SCENE_NAME_LEN]
    return clean.strip()


async def validate_entities_exist(
    hass: HomeAssistant,
    entity_ids: list[str],
) -> tuple[list[str], list[str]]:
    """Check which entity IDs actually exist in HA.

    Returns (existing_ids, missing_ids).
    """
    existing: list[str] = []
    missing: list[str] = []

    for eid in entity_ids:
        state = hass.states.get(eid)
        if state is not None:
            existing.append(eid)
        else:
            missing.append(eid)

    return existing, missing


async def validate_entities_in_area(
    hass: HomeAssistant,
    entity_ids: list[str],
    expected_area: str,
) -> tuple[list[str], list[str]]:
    """Check which entities belong to the expected area.

    Returns (in_area_ids, out_of_area_ids).
    """
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    area_reg = ar.async_get(hass)
    entity_reg = er.async_get(hass)
    device_reg = dr.async_get(hass)

    # Find the area ID for the expected area name
    target_area_id: str | None = None
    for area in area_reg.async_list_areas():
        if area.name.lower() == expected_area.lower():
            target_area_id = area.id
            break

    if target_area_id is None:
        # Area doesn't exist -- all entities are "out of area"
        return [], list(entity_ids)

    in_area: list[str] = []
    out_of_area: list[str] = []

    for eid in entity_ids:
        entry = entity_reg.async_get(eid)
        if entry is None:
            out_of_area.append(eid)
            continue

        # Check entity's own area first, then fall back to its parent device
        entity_area = entry.area_id
        if entity_area is None and entry.device_id:
            device = device_reg.async_get(entry.device_id)
            if device is not None:
                entity_area = device.area_id

        if entity_area == target_area_id:
            in_area.append(eid)
        else:
            out_of_area.append(eid)

    return in_area, out_of_area


def validate_scene_security(
    scene: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Run security checks on a scene payload.

    Returns (is_safe, list_of_warnings).
    Rejects payloads that are structurally dangerous.
    """
    warnings: list[str] = []

    if not isinstance(scene, dict):
        return False, ["Scene payload is not a dict"]

    # Check name
    name = scene.get("name")
    if name is None:
        return False, ["Scene payload is missing 'name' key"]
    if not isinstance(name, str):
        return False, ["Scene name must be a string"]
    if not name.strip():
        return False, ["Scene name must not be empty"]

    if _UNSAFE_NAME_RE.search(name):
        warnings.append(f"Scene name contains unsafe characters: {name!r}")

    if len(name) > _MAX_SCENE_NAME_LEN:
        warnings.append(f"Scene name exceeds {_MAX_SCENE_NAME_LEN} characters")

    # Check entities
    entities = scene.get("entities", {})
    if not isinstance(entities, dict):
        return False, ["Scene entities must be a dict"]

    if len(entities) > _MAX_ENTITIES_PER_SCENE:
        return False, [f"Scene exceeds maximum of {_MAX_ENTITIES_PER_SCENE} entities"]

    for entity_id, state_data in entities.items():
        if not isinstance(entity_id, str):
            return False, [f"Entity ID must be a string, got {type(entity_id).__name__}"]

        if not _ENTITY_ID_RE.match(entity_id):
            return False, [f"Invalid entity_id format: {entity_id!r}"]

        if not isinstance(state_data, dict):
            return False, [f"State data for {entity_id} must be a dict"]

        # Check for deeply nested structures (potential injection)
        if _has_deep_nesting(state_data, max_depth=3):
            return False, [f"State data for {entity_id} has excessive nesting"]

        # Check for oversized string values in state data
        for key, value in state_data.items():
            if isinstance(value, str) and len(value) > 200:
                warnings.append(f"Long string value for {entity_id}.{key} (truncated)")

    is_safe = True
    return is_safe, warnings


def _has_deep_nesting(obj: Any, max_depth: int, current_depth: int = 0) -> bool:
    """Check if an object has nesting deeper than max_depth."""
    if current_depth >= max_depth:
        return isinstance(obj, (dict, list))

    if isinstance(obj, dict):
        for value in obj.values():
            if _has_deep_nesting(value, max_depth, current_depth + 1):
                return True
    elif isinstance(obj, list):
        for item in obj:
            if _has_deep_nesting(item, max_depth, current_depth + 1):
                return True

    return False
