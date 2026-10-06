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
from .entity_capabilities import scene_exclusion, scene_supports
from .scene_state_mapper import validate_entity_states

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

    # Two passes on purpose. This one answers the questions that need *this*
    # home -- does the entity exist, may it go in a scene at all -- and is the
    # only place a non-scene entity is STRIPPED rather than rejected, since a
    # config switch swept in with a room is not a payload the model should be
    # sent back to fix. What survives is a payload question, and
    # ``validate_entity_states`` answers those against the per-domain schemas.
    candidates: dict[str, Any] = {}
    for raw_entity_id, state_data in entities.items():
        if not isinstance(raw_entity_id, str):
            return False, f"Entity ID must be a string, got {type(raw_entity_id).__name__}", None
        # Normalize to lowercase so mixed-case LLM output is accepted
        entity_id = raw_entity_id.lower()
        if not _ENTITY_ID_RE.match(entity_id):
            return False, f"Invalid entity_id format: {entity_id!r}", None
        # An entity no scene can set (a sensor swept in with a room) is left
        # out — and NAMED, via ``scene_left_out``, rather than dropped without
        # a word. Every domain whose integration can restore a state is kept,
        # whatever its name.
        if not scene_supports(hass, entity_id):
            continue
        if known_entity_ids is not None and entity_id not in known_entity_ids:
            return False, f"Entity {entity_id!r} does not exist in Home Assistant", None
        candidates[entity_id] = state_data

    if not candidates:
        return False, "No scene-capable entities remain after filtering", None

    ok, reason, normalized_entities = validate_entity_states(candidates, hass)
    if not ok or normalized_entities is None:
        return False, reason, None

    normalized: dict[str, Any] = {
        "name": name,
        "entities": normalized_entities,
    }

    return True, "valid", normalized


def scene_left_out(scene: Any, hass: HomeAssistant | None = None) -> list[dict[str, str]]:
    """The entities ``validate_scene_payload`` leaves out of *scene*, and why."""
    entities = scene.get("entities") if isinstance(scene, dict) else None
    if not isinstance(entities, dict):
        return []
    left: list[dict[str, str]] = []
    for eid in sorted(e.lower() for e in entities if isinstance(e, str)):
        if reason := scene_exclusion(hass, eid):
            left.append({"entity_id": eid, "reason": reason})
    return left


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

    is_safe, sec_warnings = validate_scene_security(scene_data, hass)
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
        if icon := str(scene_data.get("icon") or "").strip():
            scene_entry["icon"] = icon

        # Replace existing entry if refining, otherwise append. Which of the
        # two happened is reported back: the caller confirms the write to the
        # user, and "created" is the wrong word for a refinement — a rename
        # asked for in chat was being announced as a new scene.
        replaced = False
        if existing_scene_id:
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
        "replaced": replaced,
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


# ---------------------------------------------------------------------------
# Scene rename
# ---------------------------------------------------------------------------


class SceneRenameError(Exception):
    """Raised when a scene's name cannot be rewritten in scenes.yaml."""


def _rename_applied(hass: HomeAssistant, scene_id: str, stored_name: str) -> bool:
    """Whether the reloaded scene platform is serving *stored_name*.

    The registry's ``original_name`` is the name the platform registered, and a
    reload refreshes it. The state's ``friendly_name`` answers a different
    question: a user who renamed the scene entity in Home Assistant overrides
    it, so checking that would refuse every rename of that scene forever.

    Unverifiable is not failure. With no registry entry there is nothing to
    compare against, and rolling back a write that most likely landed is worse
    than accepting one that may not have.
    """
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("scene", "homeassistant", scene_id)
    entry = registry.async_get(entity_id) if entity_id else None
    if entry is None:
        _LOGGER.debug("Scene %s has no registry entry; cannot verify the rename", scene_id)
        return True
    return entry.original_name == stored_name


def _loaded_matches(hass: HomeAssistant, scene_id: str, entry: dict[str, Any]) -> bool:
    """Whether the reloaded scene is serving *entry*'s icon and states.

    The name and the member set can survive a reload that read nothing — an
    icon-only change, or new states for the same members, would then pass —
    so what was written is compared with the scene the platform loaded.
    A registered scene the loaded platform no longer serves was NOT applied:
    a configuration left without a ``scene:`` section reloads to no scenes at
    all. Unverifiable (no platform, no registry entry) is not failure, as for
    a rename.
    """
    try:
        from homeassistant.components.homeassistant.scene import (  # noqa: PLC0415
            DATA_PLATFORM,
        )
    except ImportError:
        return True
    platform = hass.data.get(DATA_PLATFORM)
    entity_id = er.async_get(hass).async_get_entity_id("scene", "homeassistant", scene_id)
    if platform is None or entity_id is None:
        return True
    config = getattr(platform.entities.get(entity_id), "scene_config", None)
    if config is None:
        return False
    if (config.icon or None) != (entry.get("icon") or None):
        return False
    written = entry.get("entities") or {}
    if set(config.states) != set(written):
        return False
    for member, value in written.items():
        state = value.get("state") if isinstance(value, dict) else value
        if state is not None and str(config.states[member].state) != str(state):
            return False
    return True


async def async_rename_scene_yaml(
    hass: HomeAssistant,
    scene_id: str,
    new_name: str,
) -> dict[str, Any]:
    """Rewrite one scene's display name in scenes.yaml and reload — see
    ``async_edit_scene_yaml``, which this is with only a name.

    Raises ``SceneRenameError`` when the name is unusable, the scene is absent
    from the file, or the reload rejects the result (rolled back in that case),
    and ``ScenesYamlError`` when the file cannot be parsed.
    """
    return await async_edit_scene_yaml(hass, scene_id, name=new_name)


async def async_edit_scene_yaml(
    hass: HomeAssistant,
    scene_id: str,
    *,
    name: str | None = None,
    entities: dict[str, Any] | None = None,
    icon: str | None = None,
    clear_icon: bool = False,
) -> dict[str, Any]:
    """Change one scene in scenes.yaml in place — its name, the states it
    sets, its icon — and reload.

    Rebuilding the entry through ``async_create_scene`` is the obvious reuse
    and the wrong one. A name change would re-validate every member against
    the state machine and raise when one is missing, so a scene holding a bulb
    that has since been unpaired could not be renamed — refused over an entity
    nobody mentioned. And Home Assistant's editor stores ``icon`` and
    ``metadata`` alongside the entities, while every entity can carry extras of
    its own (``device_id``, ``zone_id``, ``friendly_name`` on a Lutron scene);
    a rebuilt entry keeps only id/name/entities and drops the rest silently.
    So the entry is MUTATED: only what is passed changes. ``entities``, when
    given, replaces the scene's states and is validated as a new scene's are;
    ``metadata`` for an entity no longer in it is dropped.

    The entity_id survives. HA registers a YAML scene under its ``id`` as the
    unique_id, so anything pointing at ``scene.<slug>`` keeps working.

    A scene Home Assistant's own editor wrote is edited here too, and the
    display prefix is NOT applied to it: the id decides who manages a scene,
    so prefixing someone else's would only claim it in the list.

    Raises ``SceneRenameError`` when the change is unusable, the scene is
    absent from the file, or the reload rejects the result (rolled back in
    that case), and ``ScenesYamlError`` when the file cannot be parsed.
    """
    from .scene_store import scene_content_hash  # noqa: PLC0415
    from .scene_validation import (  # noqa: PLC0415
        sanitize_scene_name,
        validate_entities_exist,
        validate_scene_security,
    )

    selora_managed = scene_id.startswith(SCENE_ID_PREFIX)

    clean_name: str | None = None
    if name is not None:
        # The panel shows a Selora name without the prefix the writer adds, so
        # a user editing what they see hands back a bare name — but one who
        # selects the whole field and retypes it hands back a prefixed one, and
        # re-adding it below would double it. A Home Assistant scene never had
        # the prefix, so nothing is stripped from it.
        raw_name = name.strip()
        if selora_managed:
            raw_name = raw_name.removeprefix("[Selora AI] ")
        clean_name = sanitize_scene_name(raw_name)
        if not clean_name:
            raise SceneRenameError("Scene name is empty after sanitization")

    new_entities: dict[str, Any] | None = None
    if entities is not None:
        ok, reason, normalized = validate_scene_payload(
            {"name": clean_name or "scene", "entities": entities}, hass
        )
        if not ok or normalized is None:
            raise SceneRenameError(f"Invalid scene: {reason}")
        is_safe, warnings = validate_scene_security(normalized, hass)
        if not is_safe:
            raise SceneRenameError(f"Scene rejected by security validation: {warnings[0]}")
        _, missing = await validate_entities_exist(hass, list(normalized["entities"]))
        if missing:
            raise SceneRenameError(f"Entities not found in Home Assistant: {', '.join(missing)}")
        new_entities = dict(normalized["entities"])

    scenes_path = _get_scenes_path(hass)

    async with _SCENES_YAML_LOCK:
        existing = await hass.async_add_executor_job(_read_scenes_yaml, scenes_path)

        # Taken before the mutation below, so a rollback restores the entry as
        # it was rather than as it is being written.
        import copy  # noqa: PLC0415

        previous = copy.deepcopy(existing)

        target = next(
            (s for s in existing if isinstance(s, dict) and s.get("id") == scene_id),
            None,
        )
        if target is None:
            raise SceneRenameError(f"Scene {scene_id!r} is not in scenes.yaml")

        if clean_name is not None:
            target["name"] = f"[Selora AI] {clean_name}" if selora_managed else clean_name
        stored_name = str(target.get("name") or "")
        display_name = stored_name.removeprefix("[Selora AI] ") if selora_managed else stored_name
        if new_entities is not None:
            target["entities"] = new_entities
            if isinstance(target.get("metadata"), dict):
                target["metadata"] = {
                    k: v for k, v in target["metadata"].items() if k in new_entities
                }
        if icon:
            target["icon"] = icon.strip()
        elif clear_icon:
            target.pop("icon", None)
        entities_now = target.get("entities") or {}

        # Snapshotted before the writer runs: ``_write_scenes_yaml`` rewrites
        # YAML-unsafe strings in place as ruamel scalars, which the PyYAML dump
        # below would emit as python-specific tags.
        import json as _json  # noqa: PLC0415

        sanitized_scene: ScenePayload = _json.loads(
            _json.dumps({"name": display_name, "entities": entities_now}, default=str)
        )

        await hass.async_add_executor_job(_write_scenes_yaml, scenes_path, existing)
        _LOGGER.info("Edited scene id=%s in scenes.yaml", scene_id)

        try:
            await hass.services.async_call("scene", "reload", blocking=True)
        except Exception as exc:  # noqa: BLE001 — HA service handlers may raise beyond HA's hierarchy
            await hass.async_add_executor_job(_write_scenes_yaml, scenes_path, previous)
            _LOGGER.error(
                "scene.reload failed after editing scene %s — rolled back: %s", scene_id, exc
            )
            raise SceneRenameError(f"Scene reload failed after the change: {exc}") from exc

        # ``scene.reload`` swallows its own failures: ``reload_config`` logs and
        # RETURNS when configuration.yaml does not parse, and again when the
        # reloaded config carries no ``scene:`` section at all. So the call
        # above succeeds against a file Home Assistant never read, and the
        # change would be reported to the user — and written into the store and
        # every session that mentions the scene — while HA goes on serving the
        # old scene until something else happens to reload it.
        applied = _rename_applied(hass, scene_id, stored_name) and _loaded_matches(
            hass, scene_id, target
        )
        if not applied:
            await hass.async_add_executor_job(_write_scenes_yaml, scenes_path, previous)
            _LOGGER.error(
                "scene.reload did not apply the change to %s — rolled back scenes.yaml",
                scene_id,
            )
            raise SceneRenameError(
                "Home Assistant did not reload the changed scene. Check that "
                "configuration.yaml is valid and still includes "
                "'scene: !include scenes.yaml'."
            )

    import yaml  # noqa: PLC0415

    edited_entities = sanitized_scene["entities"]
    return {
        "scene_id": scene_id,
        "name": display_name,
        "entity_count": len(edited_entities),
        "entity_id": resolve_scene_entity_id(hass, scene_id, display_name),
        "content_hash": scene_content_hash(scene_id, stored_name, edited_entities),
        "scene_yaml": yaml.dump(sanitized_scene, default_flow_style=False, allow_unicode=True),
    }


async def async_propagate_scene_edit(
    hass: HomeAssistant, scene_id: str, result: dict[str, Any], *, tracked: bool
) -> None:
    """Carry an edited scene into the copies kept of it: the SceneStore record
    (when Selora tracks it), every chat session that mentions it, and Assist.

    Each is a cache of the file, so a failure is logged and the edit — which
    landed — stands.
    """
    from homeassistant.helpers.dispatcher import async_dispatcher_send  # noqa: PLC0415

    from .const import DOMAIN, SIGNAL_SCENE_REFRESHED  # noqa: PLC0415
    from .conversation_store import ConversationStore  # noqa: PLC0415
    from .helpers import get_scene_store  # noqa: PLC0415

    if tracked:
        try:
            await get_scene_store(hass).async_add_scene(
                scene_id,
                result["name"],
                result["entity_count"],
                entity_id=result.get("entity_id"),
                content_hash=result["content_hash"],
            )
        except Exception:  # noqa: BLE001 — the edit landed; a store refresh must not undo it
            _LOGGER.warning("Failed to update scene %s in store after an edit", scene_id)

    try:
        store: ConversationStore = hass.data[DOMAIN].setdefault(
            "_conv_store", ConversationStore(hass)
        )
        await store.update_scene_in_sessions(scene_id, result["name"], result["scene_yaml"])
    except Exception:  # noqa: BLE001 — same: sessions are a cache of the file, not the record
        _LOGGER.warning("Failed to propagate scene %s edit to sessions", scene_id)

    # Assist holds its own in-memory copy of each conversation's scenes, and
    # nothing repairs it afterwards: the store now carries the new content
    # hash, so the next reconcile sees no drift and never fires this signal of
    # its own accord. Left out, an open conversation goes on naming the scene
    # as it was for the life of the process. Dispatched outside the try above
    # because the two caches are independent — a failed session write is no
    # reason to leave Assist stale as well.
    async_dispatcher_send(
        hass, SIGNAL_SCENE_REFRESHED, scene_id, result["name"], result["scene_yaml"]
    )


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


def resolve_yaml_scene_entity_id(hass: HomeAssistant, entry: dict[str, Any]) -> str | None:
    """Resolve the HA entity_id for a scenes.yaml entry, with or without an id.

    Honors the entity registry (authoritative for HA-side renames and collision
    suffixes) and falls back to the ``scene.<slug(name)>`` state-machine probe
    so user-authored yaml entries that omit the optional ``id`` field can still
    be matched to their loaded entity.
    """
    sid = entry.get("id")
    name = entry.get("name") if isinstance(entry.get("name"), str) else None
    if isinstance(sid, str) and sid:
        eid = resolve_scene_entity_id(hass, sid, name)
        if eid:
            return eid
    if name:
        candidate = f"scene.{slugify(name)}"
        if hass.states.get(candidate) is not None:
            return candidate
    return None


async def _remove_idless_scene_by_name(
    hass: HomeAssistant,
    expected_name: str,
) -> tuple[bool, str | None, str | None]:
    """Atomically remove the id-less ``scenes.yaml`` entry whose name equals
    *expected_name*, holding ``_SCENES_YAML_LOCK`` across the find, ambiguity
    check, and removal.

    Used by the chat delete-confirmation flow, where the name fingerprint was
    captured when the card was shown. Enforcing it here — re-reading the file
    inside the lock — closes the TOCTOU window between a separate identity
    check and the delete: the entry removed is always the one matching the
    confirmed name, never whatever a since-changed entity_id now resolves to.
    """
    scenes_path = _get_scenes_path(hass)
    normalized = expected_name.strip()

    def _name_matches(entry: object) -> bool:
        return (
            isinstance(entry, dict)
            and isinstance(entry.get("name"), str)
            and entry.get("name", "").strip() == normalized
        )

    def _is_idless_match(entry: object) -> bool:
        return (
            _name_matches(entry)
            and isinstance(entry, dict)
            and not (isinstance(entry.get("id"), str) and entry["id"])
        )

    async with _SCENES_YAML_LOCK:
        try:
            yaml_entries = await hass.async_add_executor_job(_read_scenes_yaml, scenes_path)
        except Exception as exc:  # noqa: BLE001 — surface a clean error
            return False, "yaml_read_failed", str(exc)
        matches = [e for e in yaml_entries if _is_idless_match(e)]
        if not matches:
            return False, "not_found_in_yaml", None
        if len(matches) > 1:
            return False, "ambiguous_name", None
        # An id-BEARING entry sharing the name is ambiguity too, and it is not
        # resolvable from here: HA derives a scene's entity slug from its name, so
        # two same-named entries produce `scene.<slug>` and `scene.<slug>_2` with
        # no way to tell from the file which got which. The caller's entity_id was
        # matched against `resolve_yaml_scene_entity_id`, whose name-slug branch
        # returns the slug whenever *a* state exists there — so it can hand back
        # the id-less entry for an entity the id-bearing one actually owns.
        # Deleting on that basis removes the row the caller did not pick, so refuse
        # and let them disambiguate by adding an `id`.
        if any(_name_matches(e) and not _is_idless_match(e) for e in yaml_entries):
            return False, "ambiguous_name", None
        previous = list(yaml_entries)
        remaining = [e for e in yaml_entries if not _is_idless_match(e)]
        await hass.async_add_executor_job(_write_scenes_yaml, scenes_path, remaining)
        try:
            await hass.services.async_call("scene", "reload", blocking=True)
        except Exception as exc:  # noqa: BLE001 — restore yaml on reload failure
            await hass.async_add_executor_job(_write_scenes_yaml, scenes_path, previous)
            return False, "reload_failed", str(exc)
    return True, None, None


async def async_yaml_scene_id_conflicts_with_entity(
    hass: HomeAssistant,
    scene_id: str,
    entity_id: str,
) -> bool:
    """Return True when a ``scenes.yaml`` entry carries ``id: scene_id`` but that
    entry loaded as a **different** entity than *entity_id*.

    Guards the delete-by-id path. Id-less yaml entries are listed to the panel
    with ``scene_id`` set to their entity object_id, which is not a yaml ``id``;
    if an unrelated entry happens to carry that string as its ``id``, removing by
    id would delete that other scene and report success. A caller that knows
    which entity the user picked routes to the entity resolver instead.

    Deliberately reports only *positive* evidence of a collision. A missing entry
    (already deleted externally) or one whose entity can't be resolved (not
    loaded) is NOT a conflict — those must keep taking the id path, which is the
    only one that works when there is no live entity to resolve from.
    """
    scenes_path = _get_scenes_path(hass)
    try:
        yaml_entries = await hass.async_add_executor_job(_read_scenes_yaml, scenes_path)
    except Exception:  # noqa: BLE001 — unreadable file proves nothing; caller keeps its path
        return False
    for entry in yaml_entries:
        if not isinstance(entry, dict) or entry.get("id") != scene_id:
            continue
        resolved = resolve_yaml_scene_entity_id(hass, entry)
        return resolved is not None and resolved != entity_id
    return False


async def async_remove_yaml_scene_by_entity(
    hass: HomeAssistant,
    entity_id: str,
    *,
    expected_name: str | None = None,
) -> tuple[bool, str | None, str | None]:
    """Remove a non-Selora, yaml-managed scene identified by its HA entity_id.

    Resolves the ``scenes.yaml`` entry whose loaded entity matches *entity_id*
    (via the registry or the name-slug probe) and removes it, handling both
    id-bearing and id-less entries. Id-less entries are matched by their unique
    ``name`` — the only stable handle HA uses to derive the entity slug; an
    ambiguous name is refused rather than risk removing the wrong scene.

    Shared by the MCP ``delete_scene`` tool and the panel's ``delete_scene``
    websocket handler so both classify and delete id-less scenes identically.

    When *expected_name* is given (the chat delete-confirmation flow), the
    id-less removal is keyed on that confirmed fingerprint and performed
    atomically under the yaml lock, so a since-changed entity mapping can't
    redirect the delete to a different entry.

    Returns ``(removed, error_code, detail)``. On success ``(True, None, None)``.
    On failure ``removed`` is False and ``error_code`` is one of ``not_found`` /
    ``yaml_read_failed`` / ``not_yaml_managed`` / ``not_found_in_yaml`` /
    ``no_identifier`` / ``ambiguous_name`` / ``reload_failed``; ``detail``
    carries the underlying exception text for the failures that have one.
    """
    # Confirmed-fingerprint path: enforce the captured name atomically instead
    # of trusting the (mutable) entity → yaml mapping at delete time. This runs
    # BEFORE the live-state check on purpose — the path is independent of the
    # entity mapping, so a scene whose entity became unloaded or remapped after
    # the card was shown must still be deletable by its confirmed yaml name.
    if expected_name is not None:
        return await _remove_idless_scene_by_name(hass, expected_name)

    if hass.states.get(entity_id) is None:
        return False, "not_found", None

    scenes_path = _get_scenes_path(hass)
    try:
        yaml_entries = await hass.async_add_executor_job(_read_scenes_yaml, scenes_path)
    except Exception as exc:  # noqa: BLE001 — surface a clean error to the caller
        return False, "yaml_read_failed", str(exc)

    yaml_match = next(
        (
            e
            for e in yaml_entries
            if isinstance(e, dict) and resolve_yaml_scene_entity_id(hass, e) == entity_id
        ),
        None,
    )
    if yaml_match is None:
        return False, "not_yaml_managed", None

    yaml_id = yaml_match.get("id")
    if isinstance(yaml_id, str) and yaml_id:
        try:
            removed = await async_remove_scene_yaml(hass, yaml_id)
        except SceneDeleteError as exc:
            return False, "reload_failed", str(exc)
        if not removed:
            return False, "not_found_in_yaml", None
        return True, None, None

    # Id-less entry: match by name, the only stable handle. Delegate to the
    # locked resolver so the find, ambiguity check, and write all run under
    # _SCENES_YAML_LOCK against a single read — the snapshot above was taken
    # outside the lock to classify the entry and may already be stale, so
    # writing a list derived from it would clobber a concurrent create.
    target_name = yaml_match.get("name")
    if not isinstance(target_name, str) or not target_name.strip():
        return False, "no_identifier", None
    return await _remove_idless_scene_by_name(hass, target_name)


async def get_area_names(hass: HomeAssistant) -> list[str]:
    """Return all area names from the HA area registry."""
    from homeassistant.helpers import area_registry as ar  # noqa: PLC0415

    area_reg = ar.async_get(hass)
    return [area.name for area in area_reg.async_list_areas()]
