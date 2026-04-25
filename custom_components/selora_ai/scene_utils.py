"""Scene utilities -- validation, creation, and YAML I/O for HA scenes.

Mirrors the automation_utils.py pattern but adapted for scene payloads.
Scenes are named snapshots of device states (no triggers or conditions).

Current scope: single-domain scenes only (all entities must share the same
HA domain, e.g. all lights or all media players).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any
import uuid

from homeassistant.config import SCENE_CONFIG_PATH
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import SCENE_ID_PREFIX

if TYPE_CHECKING:
    from .types import ScenePayload

_LOGGER = logging.getLogger(__name__)

# Domain part: lowercase letters/underscores. Object ID: letters, digits,
# underscores, hyphens.  We lowercase before matching so mixed-case LLM
# output is accepted.
_ENTITY_ID_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z0-9][a-z0-9_-]*$")

# Serializes read-modify-write cycles on the scenes YAML file so that
# concurrent requests (e.g. two browser tabs) don't overwrite each other.
_SCENES_YAML_LOCK = asyncio.Lock()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_scene_payload(
    scene: dict[str, Any],
    hass: HomeAssistant | None = None,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Validate a scene payload from the LLM.

    When *hass* is provided, each entity ID is checked against the current
    entity registry so hallucinated or stale IDs are caught before writing
    to the scenes YAML file.

    Returns (is_valid, reason, normalized_scene | None).
    """
    if not isinstance(scene, dict):
        return False, "Scene payload must be a dict", None

    raw_name = scene.get("name", "")
    if not isinstance(raw_name, str):
        return False, "Scene 'name' must be a string", None
    name = raw_name.strip()
    if not name:
        return False, "Scene must have a non-empty 'name'", None

    entities = scene.get("entities")
    if not isinstance(entities, dict) or not entities:
        return False, "Scene must have a non-empty 'entities' dict", None

    # Build a set of known entity IDs for existence checks
    known_entity_ids: set[str] | None = None
    if hass is not None:
        known_entity_ids = {s.entity_id for s in hass.states.async_all()}

    # Single-domain constraint: all entity IDs must share the same domain.
    domains: set[str] = set()
    normalized_entities: dict[str, dict[str, Any]] = {}
    for entity_id, state_data in entities.items():
        # Normalize to lowercase so mixed-case LLM output is accepted
        entity_id = entity_id.lower()
        if not isinstance(entity_id, str) or not _ENTITY_ID_RE.match(entity_id):
            return False, f"Invalid entity_id format: {entity_id!r}", None
        if known_entity_ids is not None and entity_id not in known_entity_ids:
            return False, f"Entity {entity_id!r} does not exist in Home Assistant", None
        if not isinstance(state_data, dict):
            return False, f"State data for {entity_id} must be a dict", None
        if "state" not in state_data:
            return False, f"State data for {entity_id} must include 'state'", None
        # Copy to avoid mutating the caller's input dict
        state_data = dict(state_data)
        # HA scene states are always strings — coerce bools/numbers from LLM
        # but reject containers (list, dict) and None which indicate a bad payload.
        raw_state = state_data["state"]
        if isinstance(raw_state, bool):
            state_data["state"] = "on" if raw_state else "off"
        elif isinstance(raw_state, (int, float)):
            state_data["state"] = str(raw_state)
        elif not isinstance(raw_state, str):
            return (
                False,
                f"Invalid state value for {entity_id}: expected a string, bool, or number",
                None,
            )
        domains.add(entity_id.split(".")[0])
        normalized_entities[entity_id] = state_data

    normalized: dict[str, Any] = {
        "name": name,
        "entities": normalized_entities,
    }

    return True, "valid", normalized


def generate_scene_id() -> str:
    """Generate a unique scene ID with the Selora prefix."""
    return f"{SCENE_ID_PREFIX}scene_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# YAML I/O (mirrors automation_utils pattern)
# ---------------------------------------------------------------------------


class ScenesYamlError(Exception):
    """Raised when scenes.yaml exists but cannot be parsed.

    Prevents ``async_create_scene`` from silently overwriting the file
    with only the new scene (dropping all pre-existing entries).
    """


def _read_scenes_yaml(path: Path) -> list[dict[str, Any]]:
    """Read and parse scenes.yaml (runs in executor).

    Raises ``ScenesYamlError`` when the file exists but is corrupt so that
    callers can abort instead of silently losing existing scenes.
    """
    from ruamel.yaml import YAML

    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8").strip()
    if not text or text == "[]":
        return []
    try:
        ryaml = YAML()
        data = ryaml.load(text)
    except Exception as exc:
        raise ScenesYamlError(f"Failed to parse scenes.yaml: {exc}") from exc
    if data is None:
        # Comment-only or document-marker-only file — treat as empty
        return []
    if not isinstance(data, list):
        raise ScenesYamlError(f"scenes.yaml must contain a YAML list, got {type(data).__name__}")
    # Convert ruamel types to plain Python dicts
    import json

    return json.loads(json.dumps(data, default=str))


def _write_scenes_yaml(path: Path, scenes: list[dict[str, Any]]) -> None:
    """Write scenes list to YAML atomically."""
    from ruamel.yaml import YAML
    from ruamel.yaml.scalarstring import DoubleQuotedScalarString

    # Quote string values that YAML 1.1 would silently reinterpret:
    # - booleans: on/off/yes/no/true/false
    # - sexagesimal integers: HH:MM or HH:MM:SS patterns (e.g. "23:46:00"
    #   becomes 85560 under YAML 1.1)
    # Real bool values are left untouched — they represent intentional
    # boolean attributes (e.g. climate flags) and must round-trip as booleans.
    _YAML_BOOL_STRINGS = frozenset({"true", "false", "yes", "no", "on", "off", "y", "n"})
    _SEXAGESIMAL_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")

    def _quote_unsafe_strings(obj: Any) -> Any:
        if isinstance(obj, dict):
            for k, v in obj.items():
                obj[k] = _quote_unsafe_strings(v)
            return obj
        if isinstance(obj, list):
            for i, v in enumerate(obj):
                obj[i] = _quote_unsafe_strings(v)
            return obj
        if isinstance(obj, str) and (
            obj.lower() in _YAML_BOOL_STRINGS or _SEXAGESIMAL_RE.match(obj)
        ):
            return DoubleQuotedScalarString(obj)
        return obj

    for scene in scenes:
        _quote_unsafe_strings(scene)

    ryaml = YAML()
    ryaml.default_flow_style = False
    ryaml.allow_unicode = True
    tmp_path = path.with_suffix(".yaml.tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        ryaml.dump(scenes, fh)
    tmp_path.replace(path)


# ---------------------------------------------------------------------------
# Scene creation
# ---------------------------------------------------------------------------


class SceneCreateError(Exception):
    """Raised when scene creation fails after writing to scenes.yaml."""


def _get_scenes_path(hass: HomeAssistant) -> Path:
    """Return the path to the scenes YAML file.

    Uses HA's ``SCENE_CONFIG_PATH`` constant so the path matches the
    default ``scene: !include scenes.yaml`` in configuration.yaml.
    """
    return Path(hass.config.config_dir) / SCENE_CONFIG_PATH


async def async_create_scene(
    hass: HomeAssistant,
    scene_data: dict[str, Any],
    *,
    existing_scene_id: str | None = None,
    session_scene_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Write a scene to the scenes YAML file and reload HA scenes.

    When *existing_scene_id* is provided **and** it passes validation, the
    matching entry in the file is replaced instead of appending a new one
    (refinement flow).

    *session_scene_ids*, when supplied, is the set of scene IDs that were
    created in the current conversation.  ``existing_scene_id`` is only
    honoured when it appears in this set (or when the set is ``None``
    for backwards-compatibility).

    This does NOT apply the scene immediately — the scene is saved for the
    user to activate later (just like automations are created disabled).

    The read-modify-write cycle is serialized via ``_SCENES_YAML_LOCK`` so
    concurrent requests don't overwrite each other.

    Raises ``SceneCreateError`` if the scene is not loadable (e.g. the HA
    configuration does not include the scenes file, or the reload rejected it).

    Returns a result dict with success status and scene_id.
    """
    # Only honour refine IDs that belong to the active session and were
    # created by Selora.  Reject anything else with an error so the caller
    # can surface the failure instead of silently creating a duplicate.
    if existing_scene_id:
        if not existing_scene_id.startswith(SCENE_ID_PREFIX):
            raise SceneCreateError(
                f"Cannot refine scene {existing_scene_id!r}: not a Selora-managed scene."
            )
        if session_scene_ids is not None and existing_scene_id not in session_scene_ids:
            raise SceneCreateError(
                f"Cannot refine scene {existing_scene_id!r}: not part of the current session."
            )

    # --- Security validation (scene_validation.py) ---
    from .scene_validation import (  # noqa: PLC0415
        sanitize_scene_name,
        validate_entities_exist,
        validate_scene_security,
    )

    is_safe, sec_warnings = validate_scene_security(scene_data)
    if not is_safe:
        raise SceneCreateError(f"Scene rejected by security validation: {sec_warnings[0]}")
    for warning in sec_warnings:
        _LOGGER.warning("Scene security warning: %s", warning)

    # Sanitize the name (strips unsafe characters and truncates)
    scene_data = dict(scene_data)
    scene_data["name"] = sanitize_scene_name(scene_data.get("name", ""))
    if not scene_data["name"]:
        raise SceneCreateError("Scene name is empty after sanitization")

    # Reject entities that don't exist in HA — fail loudly so the caller
    # can surface the error instead of silently creating a partial scene.
    entity_ids = list(scene_data.get("entities", {}).keys())
    _, missing_ids = await validate_entities_exist(hass, entity_ids)
    if missing_ids:
        raise SceneCreateError(f"Entities not found in Home Assistant: {', '.join(missing_ids)}")

    scene_id = existing_scene_id or generate_scene_id()
    name = scene_data["name"]
    entities = scene_data["entities"]

    # Snapshot the sanitized payload *before* the YAML writer mutates
    # shared dicts (ruamel's _quote_unsafe_strings converts "on"/"off"
    # to DoubleQuotedScalarString in-place, which PyYAML would emit as
    # Python-specific tags).
    import json as _json  # noqa: PLC0415

    sanitized_scene: ScenePayload = _json.loads(
        _json.dumps({"name": name, "entities": entities}, default=str)
    )

    scenes_path = _get_scenes_path(hass)

    # The entire write/reload/verify/rollback sequence is serialized so that
    # a concurrent request cannot interleave its write between ours and the
    # rollback, which would cause stale-snapshot data loss.
    async with _SCENES_YAML_LOCK:
        file_existed = scenes_path.exists()

        existing = await hass.async_add_executor_job(_read_scenes_yaml, scenes_path)

        # Capture pre-write state *before* any mutation so rollback restores
        # the exact original list (not a truncated post-mutation copy).
        previous = [dict(s) for s in existing]

        scene_entry: dict[str, Any] = {
            "id": scene_id,
            "name": f"[Selora AI] {name}",
            "entities": entities,
        }

        # Replace existing entry if refining, otherwise append
        if existing_scene_id:
            replaced = False
            for i, s in enumerate(existing):
                if s.get("id") == existing_scene_id:
                    existing[i] = scene_entry
                    replaced = True
                    break
            if not replaced:
                existing.append(scene_entry)
        else:
            existing.append(scene_entry)

        def _rollback() -> None:
            if file_existed:
                _write_scenes_yaml(scenes_path, previous)
            elif scenes_path.exists():
                scenes_path.unlink()

        await hass.async_add_executor_job(_write_scenes_yaml, scenes_path, existing)
        _LOGGER.info("Wrote scene '%s' (id=%s) with %d entities", name, scene_id, len(entities))

        try:
            await hass.services.async_call("scene", "reload", blocking=True)
        except Exception as exc:
            await hass.async_add_executor_job(_rollback)
            _LOGGER.error("scene.reload failed — rolled back scenes.yaml: %s", exc)
            raise SceneCreateError(
                f"Scene reload failed: {exc}. Ensure 'scene:' is included in your configuration.yaml."
            ) from exc

        # Verify the scene was actually loaded.  On UI-only installs that
        # don't include scenes.yaml from configuration.yaml, the reload is a
        # no-op and the entity won't exist.
        #
        # Check the entity registry first (authoritative unique_id → entity_id
        # mapping).  When another scene already owns the base slug, HA suffixes
        # the entity_id (e.g. ``_2``), so the registry is the only reliable
        # lookup.  Fall back to probing both ``scene.<id>`` and
        # ``scene.<slug(name)>`` to cover environments where the entity
        # platform isn't fully wired up.
        # Resolve the authoritative entity_id.  The entity registry is the
        # single source of truth (HA may suffix the slug when names collide).
        # Fall back to probing ``scene.<id>`` and ``scene.<slug(name)>``.
        registry = er.async_get(hass)
        resolved_entity_id = registry.async_get_entity_id("scene", "homeassistant", scene_id)
        if resolved_entity_id is None:
            # Not in the entity registry — try state-based probes
            for candidate in (f"scene.{scene_id}", f"scene.{slugify(f'[Selora AI] {name}')}"):
                if hass.states.get(candidate) is not None:
                    resolved_entity_id = candidate
                    break

        if resolved_entity_id is None:
            await hass.async_add_executor_job(_rollback)
            _LOGGER.error(
                "Scene '%s' (id=%s) was written to scenes.yaml but no entity "
                "appeared after reload — rolled back scenes.yaml",
                name,
                scene_id,
            )
            raise SceneCreateError(
                "Scene was saved to scenes.yaml but Home Assistant did not load it. "
                "Ensure 'scene:' is included in your configuration.yaml."
            )

    import yaml  # noqa: PLC0415

    from .scene_store import scene_content_hash  # noqa: PLC0415

    return {
        "success": True,
        "scene_id": scene_id,
        "name": name,
        "entity_count": len(entities),
        "entity_id": resolved_entity_id,
        "content_hash": scene_content_hash(scene_id, f"[Selora AI] {name}", entities),
        "scene": sanitized_scene,
        "scene_yaml": yaml.dump(sanitized_scene, default_flow_style=False, allow_unicode=True),
    }


def resolve_scene_entity_id(
    hass: HomeAssistant,
    scene_id: str,
    name: str | None = None,
) -> str | None:
    """Resolve the actual HA entity_id for a scene.

    Checks the entity registry first (authoritative), then falls back to
    state-based probes.  The name-based probe uses both the prefixed name
    (``[Selora AI] <name>``, which is how scenes are written to YAML) and
    the bare name.  Returns ``None`` when the scene is not loaded.
    """
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("scene", "homeassistant", scene_id)
    if entity_id is not None:
        return entity_id
    # State-based fallback
    candidate = f"scene.{scene_id}"
    if hass.states.get(candidate) is not None:
        return candidate
    if name:
        # async_create_scene writes "[Selora AI] <name>" to scenes.yaml, so
        # the HA entity slug may be based on that prefixed name.
        for variant in (f"[Selora AI] {name}", name):
            candidate = f"scene.{slugify(variant)}"
            if hass.states.get(candidate) is not None:
                return candidate
    return None


class SceneDeleteError(Exception):
    """Raised when a scene cannot be cleanly removed from scenes.yaml."""


async def async_remove_scene_yaml(
    hass: HomeAssistant,
    scene_id: str,
) -> bool:
    """Remove a scene from scenes.yaml and reload.

    Returns True if the scene was found and removed, False if it wasn't
    present in the YAML file.  Raises ``ScenesYamlError`` if the file
    cannot be parsed, or ``SceneDeleteError`` if the reload fails (the
    file is rolled back in that case).
    """
    scenes_path = _get_scenes_path(hass)

    async with _SCENES_YAML_LOCK:
        existing = await hass.async_add_executor_job(_read_scenes_yaml, scenes_path)

        # Tolerate non-dict items from manual edits — keep them as-is,
        # only match dict entries by id for removal.
        filtered = [s for s in existing if not isinstance(s, dict) or s.get("id") != scene_id]
        if len(filtered) == len(existing):
            return False

        # Keep the pre-write state so we can roll back on reload failure
        previous = list(existing)

        await hass.async_add_executor_job(_write_scenes_yaml, scenes_path, filtered)
        _LOGGER.info("Removed scene id=%s from scenes.yaml", scene_id)

        try:
            await hass.services.async_call("scene", "reload", blocking=True)
        except Exception as exc:  # noqa: BLE001 — HA service handlers may raise beyond HA's hierarchy
            await hass.async_add_executor_job(_write_scenes_yaml, scenes_path, previous)
            _LOGGER.error(
                "scene.reload failed after removing scene %s — rolled back: %s",
                scene_id,
                exc,
            )
            raise SceneDeleteError(f"Scene reload failed after removal: {exc}") from exc

    return True
