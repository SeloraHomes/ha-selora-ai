"""MCP tools for scenes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant

from ..const import (
    DOMAIN,
)
from .common import _sanitize

if TYPE_CHECKING:
    from .. import ConversationStore


_LOGGER = logging.getLogger(__name__)


# ── Phase 3: Scenes ───────────────────────────────────────────────────────────


def _serialize_scene_record(record: dict[str, Any]) -> dict[str, Any]:
    """Strip internal fields and sanitize a SceneRecord for MCP responses.

    The cached ``entity_id`` is intentionally omitted — callers resolve the
    current entity_id through the registry and would otherwise have it
    overwritten with a stale value when this dict is merged on top of theirs.
    """
    return {
        "scene_id": record["scene_id"],
        "name": _sanitize(record.get("name", "")),
        "entity_count": record.get("entity_count", 0),
        "session_id": record.get("session_id"),
        "created_at": record.get("created_at", ""),
        "updated_at": record.get("updated_at", ""),
        "deleted_at": record.get("deleted_at"),
    }


def _resolve_yaml_scene_entity_id(hass: HomeAssistant, entry: dict[str, Any]) -> str | None:
    """Resolve the HA entity_id for a scenes.yaml entry (see scene_utils)."""
    from ..scene_utils import resolve_yaml_scene_entity_id  # noqa: PLC0415

    return resolve_yaml_scene_entity_id(hass, entry)


def _yaml_delete_error_message(code: str, detail: str | None, entity_id: str) -> str:
    """Human-readable message for an async_remove_yaml_scene_by_entity code."""
    ent = _sanitize(entity_id)
    messages = {
        "not_found": f"Scene {ent} not found",
        "yaml_read_failed": f"Failed to read scenes.yaml: {detail}",
        "not_yaml_managed": (
            f"Scene {ent} is not yaml-managed; delete it from the Home Assistant UI instead."
        ),
        "not_found_in_yaml": f"Scene {ent} not found in scenes.yaml",
        "no_identifier": (
            f"Scene {ent} has no 'id' or 'name' field in scenes.yaml; "
            "cannot identify the entry safely."
        ),
        "ambiguous_name": (
            f"Scene {ent} has no 'id' field and multiple entries share its name; "
            "add an 'id' to the target entry to enable safe deletion."
        ),
        "reload_failed": f"Scene reload failed: {detail}",
    }
    return messages.get(code, "Scene deletion failed")


async def _tool_list_scenes(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List all Home Assistant scenes (yaml + storage-managed).

    Each entry includes the entity_id, friendly name, source, and a
    ``selora_managed`` flag. Selora-managed entries also carry their
    SceneStore lifecycle metadata, scene_id, and rendered YAML.
    """
    import yaml as _yaml  # noqa: PLC0415

    from ..helpers import get_scene_store  # noqa: PLC0415
    from ..scene_utils import (  # noqa: PLC0415
        _get_scenes_path,
        _read_scenes_yaml,
        resolve_scene_entity_id,
    )

    selora_only: bool = bool(arguments.get("selora_only", False))

    store = get_scene_store(hass)
    await store.async_reconcile_yaml(force=True)
    records = await store.async_list_scenes()

    scenes_path = _get_scenes_path(hass)
    try:
        yaml_entries = await hass.async_add_executor_job(_read_scenes_yaml, scenes_path)
    except Exception:  # noqa: BLE001 — best-effort enrichment
        yaml_entries = []

    # Map yaml entries to their resolved HA entity_ids so we can identify
    # yaml-managed scenes that aren't tracked by the SceneStore (including
    # user-authored entries that omit the optional ``id`` field).
    yaml_by_entity_id: dict[str, dict[str, Any]] = {}
    for entry in yaml_entries:
        if not isinstance(entry, dict):
            continue
        eid = _resolve_yaml_scene_entity_id(hass, entry)
        if eid:
            yaml_by_entity_id[eid] = entry

    # Resolve each Selora record's *current* entity_id through the registry —
    # the cached value can go stale after a rename in HA without a content-hash
    # change, and an exact match would drop the record from selora_only filters
    # and misclassify it as non-Selora.
    record_by_entity_id: dict[str, dict[str, Any]] = {}
    for r in records:
        eid = resolve_scene_entity_id(hass, r["scene_id"], r.get("name")) or r.get("entity_id")
        if eid:
            record_by_entity_id[eid] = r

    scenes: list[dict[str, Any]] = []
    for state in hass.states.async_all("scene"):
        record = record_by_entity_id.get(state.entity_id)
        is_selora = record is not None
        if selora_only and not is_selora:
            continue

        yaml_entry = yaml_by_entity_id.get(state.entity_id)
        entities: dict[str, Any] = {}
        yaml_text = ""
        if yaml_entry is not None:
            raw_entities = yaml_entry.get("entities")
            if isinstance(raw_entities, dict):
                entities = raw_entities
            try:
                yaml_text = _yaml.dump(
                    {
                        "name": yaml_entry.get("name", state.attributes.get("friendly_name", "")),
                        "entities": entities,
                    },
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            except Exception:  # noqa: BLE001
                yaml_text = ""

        entry: dict[str, Any] = {
            "entity_id": state.entity_id,
            "name": _sanitize(
                state.attributes.get("friendly_name") or (record.get("name") if record else "")
            ),
            "selora_managed": is_selora,
            "source": "yaml" if yaml_entry else "storage",
            "entities": entities,
            "yaml": yaml_text,
        }
        if record is not None:
            entry.update(_serialize_scene_record(record))
        else:
            entry["entity_count"] = len(state.attributes.get("entity_id", []) or [])
        scenes.append(entry)

    return {"scenes": scenes, "count": len(scenes)}


async def _tool_get_scene(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return full detail for any HA scene.

    Accepts either ``scene_id`` (Selora SceneStore ID) or ``entity_id``
    (e.g. ``scene.movie_night``). Selora-managed scenes carry full
    lifecycle metadata; others expose entity_id, name, and YAML when
    yaml-managed.
    """
    import yaml as _yaml  # noqa: PLC0415

    from ..helpers import get_scene_store  # noqa: PLC0415
    from ..scene_utils import (  # noqa: PLC0415
        _get_scenes_path,
        _read_scenes_yaml,
        resolve_scene_entity_id,
    )

    scene_id = str(arguments.get("scene_id", "")).strip()
    entity_id_arg = str(arguments.get("entity_id", "")).strip()

    if not scene_id and not entity_id_arg:
        return {"error": "scene_id or entity_id is required"}

    store = get_scene_store(hass)
    await store.async_reconcile_yaml(force=True)

    record: dict[str, Any] | None = None
    entity_id = entity_id_arg

    if scene_id:
        record = await store.async_get_scene(scene_id)
        if record is None:
            return {"error": f"Scene {_sanitize(scene_id)} not found"}
        # Resolve through the registry first — the cached entity_id can go stale
        # when the user renames the scene in HA without touching scenes.yaml,
        # since reconcile keys off content hash and won't refresh entity_id.
        resolved = resolve_scene_entity_id(hass, scene_id, record.get("name"))
        if resolved is None:
            resolved = record.get("entity_id")
        if resolved is not None:
            entity_id = resolved
    else:
        if not entity_id_arg.startswith("scene."):
            return {"error": f"entity_id must be a scene entity, got {_sanitize(entity_id_arg)}"}
        if hass.states.get(entity_id_arg) is None:
            return {"error": f"Scene {_sanitize(entity_id_arg)} not found"}
        # Resolve each record's current entity_id via the registry — the cached
        # value can be stale after a rename, and a plain exact match would
        # misreport a Selora scene as non-Selora.
        records = await store.async_list_scenes()
        for r in records:
            current = resolve_scene_entity_id(hass, r["scene_id"], r.get("name")) or r.get(
                "entity_id"
            )
            if current == entity_id_arg:
                record = r
                break
        if record is not None:
            scene_id = record["scene_id"]

    state = hass.states.get(entity_id) if entity_id else None
    if state is None:
        return {"error": "Scene entity not loaded in Home Assistant"}

    scenes_path = _get_scenes_path(hass)
    try:
        yaml_entries = await hass.async_add_executor_job(_read_scenes_yaml, scenes_path)
    except Exception:  # noqa: BLE001
        yaml_entries = []

    # Find the yaml entry: by scene_id when we have a Selora record, otherwise
    # by resolving each yaml entry to its HA entity_id so non-Selora yaml scenes
    # — including those without an explicit ``id`` field — are recognised as
    # yaml-managed.
    yaml_entry: dict[str, Any] | None = None
    if scene_id:
        yaml_entry = next(
            (e for e in yaml_entries if isinstance(e, dict) and e.get("id") == scene_id),
            None,
        )
    else:
        for e in yaml_entries:
            if not isinstance(e, dict):
                continue
            if _resolve_yaml_scene_entity_id(hass, e) == entity_id:
                yaml_entry = e
                break
    entities: dict[str, Any] = {}
    yaml_text = ""
    if yaml_entry is not None:
        raw_entities = yaml_entry.get("entities")
        if isinstance(raw_entities, dict):
            entities = raw_entities
        try:
            yaml_text = _yaml.dump(
                {
                    "name": yaml_entry.get("name", state.attributes.get("friendly_name", "")),
                    "entities": entities,
                },
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        except Exception:  # noqa: BLE001
            yaml_text = ""

    result: dict[str, Any] = {
        "entity_id": entity_id,
        "name": _sanitize(
            state.attributes.get("friendly_name") or (record.get("name") if record else "")
        ),
        "selora_managed": record is not None,
        "source": "yaml" if yaml_entry else "storage",
        "entities": entities,
        "yaml": yaml_text,
    }
    if record is not None:
        result.update(_serialize_scene_record(record))
    else:
        members = list(state.attributes.get("entity_id", []) or [])
        result["entity_count"] = len(members)
        if not entities:
            result["entity_ids"] = members
    return result


async def _tool_validate_scene(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate a scene payload without writing anything."""
    import yaml as _yaml  # noqa: PLC0415

    from ..scene_utils import validate_scene_payload  # noqa: PLC0415
    from ..scene_validation import sanitize_scene_name, validate_scene_security  # noqa: PLC0415

    name = arguments.get("name")
    entities = arguments.get("entities")

    payload: dict[str, Any] = {}
    if isinstance(name, str):
        payload["name"] = name
    if isinstance(entities, dict):
        payload["entities"] = entities

    is_valid, reason, normalized = validate_scene_payload(payload, hass)
    if not is_valid or normalized is None:
        return {
            "valid": False,
            "errors": [reason] if reason else ["Validation failed"],
            "normalized_yaml": None,
        }

    is_safe, sec_warnings = validate_scene_security(normalized, hass)
    if not is_safe:
        return {
            "valid": False,
            "errors": [_sanitize(w) for w in sec_warnings] or ["Security validation failed"],
            "normalized_yaml": None,
        }

    sanitized_name = sanitize_scene_name(normalized["name"])
    if not sanitized_name:
        return {
            "valid": False,
            "errors": ["Scene name is empty after sanitization"],
            "normalized_yaml": None,
        }
    normalized["name"] = sanitized_name

    normalized_yaml = _yaml.dump(normalized, default_flow_style=False, allow_unicode=True)
    return {
        "valid": True,
        "errors": [],
        "warnings": [_sanitize(w) for w in sec_warnings],
        "normalized_yaml": normalized_yaml,
        "entity_count": len(normalized["entities"]),
        **_left_out(payload, hass),
    }


def _left_out(payload: dict[str, Any], hass: HomeAssistant) -> dict[str, Any]:
    """The entities left out because no scene can set them — said, not hidden."""
    from ..scene_utils import scene_left_out  # noqa: PLC0415

    left = scene_left_out(payload, hass)
    return {"left_out": left} if left else {}


async def _tool_create_scene(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a new Selora-managed scene from name + entities.

    Server-side validation runs unconditionally. The scene is recorded in the
    SceneStore and written to scenes.yaml. Requires admin access.
    """
    from ..helpers import get_scene_store  # noqa: PLC0415
    from ..scene_utils import (  # noqa: PLC0415
        SceneCreateError,
        async_create_scene,
        validate_scene_payload,
    )

    name = arguments.get("name")
    entities = arguments.get("entities")

    payload: dict[str, Any] = {}
    if isinstance(name, str):
        payload["name"] = name
    if isinstance(entities, dict):
        payload["entities"] = entities

    is_valid, reason, normalized = validate_scene_payload(payload, hass)
    if not is_valid or normalized is None:
        return {"error": f"Invalid scene: {reason}"}

    try:
        result = await async_create_scene(hass, normalized)
    except SceneCreateError as exc:
        return {"error": f"Scene creation failed: {exc}"}
    except Exception:  # noqa: BLE001 — surface a generic failure rather than leak internals
        _LOGGER.exception("Unexpected failure creating scene via MCP")
        return {"error": "Scene creation failed"}

    scene_store = get_scene_store(hass)
    try:
        await scene_store.async_add_scene(
            result["scene_id"],
            result["name"],
            result["entity_count"],
            entity_id=result.get("entity_id"),
            content_hash=result.get("content_hash"),
        )
    except Exception:  # noqa: BLE001 — store failure doesn't invalidate the created scene
        _LOGGER.warning("Failed to record scene %s in store", result["scene_id"])

    return {
        "scene_id": result["scene_id"],
        "name": _sanitize(result["name"]),
        "entity_count": result["entity_count"],
        "entity_id": result.get("entity_id"),
        "scene_yaml": result.get("scene_yaml", ""),
        "status": "created",
        **_left_out(payload, hass),
    }


async def _tool_delete_scene(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete any yaml-managed HA scene from scenes.yaml.

    Accepts ``scene_id`` (Selora SceneStore ID) or ``entity_id`` (any HA scene).
    Storage-managed (UI/integration) scenes cannot be removed through this tool.
    Selora-managed scenes are also soft-deleted in the SceneStore and purged
    from chat sessions.
    """
    from homeassistant.helpers.dispatcher import async_dispatcher_send  # noqa: PLC0415

    from .. import ConversationStore  # noqa: PLC0415
    from ..const import SIGNAL_SCENE_DELETED  # noqa: PLC0415
    from ..helpers import get_scene_store  # noqa: PLC0415
    from ..scene_utils import (  # noqa: PLC0415
        SceneDeleteError,
        async_remove_scene_yaml,
        async_remove_yaml_scene_by_entity,
        resolve_scene_entity_id,
    )

    scene_id = str(arguments.get("scene_id", "")).strip()
    entity_id_arg = str(arguments.get("entity_id", "")).strip()
    # Confirmed name fingerprint for the id-less delete-confirmation path.
    expected_name: str = str(arguments.get("expected_name", "")).strip()

    if not scene_id and not entity_id_arg:
        return {"error": "scene_id or entity_id is required"}
    if entity_id_arg and not entity_id_arg.startswith("scene."):
        return {"error": f"entity_id must be a scene entity, got {_sanitize(entity_id_arg)}"}

    # Confirmed id-less fingerprint path: enforce the captured name directly
    # against scenes.yaml BEFORE any SceneStore/entity resolution. If a new
    # Selora scene has since claimed this entity_id, the store lookup below
    # would find and delete THAT record — a scene the user never saw. Keying
    # on the confirmed name (and refusing when it's gone) avoids that.
    if expected_name and not scene_id:
        if not entity_id_arg:
            return {"error": "entity_id is required with expected_name"}
        _removed, code, detail = await async_remove_yaml_scene_by_entity(
            hass, entity_id_arg, expected_name=expected_name
        )
        if code is not None:
            return {"error": _yaml_delete_error_message(code, detail, entity_id_arg)}
        return {"entity_id": entity_id_arg, "status": "deleted"}

    store = get_scene_store(hass)
    await store.async_reconcile_yaml(force=True)

    # Resolve scene_id from entity_id when only entity_id was given.
    # Resolve each Selora record's *current* entity_id through the registry —
    # the cached value can go stale when the user renames the scene in HA
    # without touching scenes.yaml, and an exact match on stale data would
    # silently fall through to the non-Selora yaml path, skipping SceneStore
    # soft-delete, session cleanup, and dispatcher notifications.
    if not scene_id and entity_id_arg:
        records = await store.async_list_scenes()
        match: dict[str, Any] | None = None
        for r in records:
            current = resolve_scene_entity_id(hass, r["scene_id"], r.get("name")) or r.get(
                "entity_id"
            )
            if current == entity_id_arg:
                match = r
                break
        if match is not None:
            scene_id = match["scene_id"]
        else:
            # Non-Selora scene — resolve its scenes.yaml entry by entity_id and
            # remove it. Shared with the panel's delete_scene websocket handler
            # so both paths classify and delete id-less entries identically.
            # (The chat confirm path with a captured fingerprint is handled by
            # the expected_name short-circuit above, before this resolution.)
            _removed, code, detail = await async_remove_yaml_scene_by_entity(hass, entity_id_arg)
            if code is not None:
                return {"error": _yaml_delete_error_message(code, detail, entity_id_arg)}
            return {"entity_id": entity_id_arg, "status": "deleted"}

    try:
        found, removed = await store.async_delete_with_yaml(
            scene_id,
            lambda sid: async_remove_scene_yaml(hass, sid),
        )
    except SceneDeleteError as exc:
        await store.async_restore(scene_id)
        return {"error": f"Scene reload failed: {exc}"}
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected failure deleting scene %s", scene_id)
        await store.async_restore(scene_id)
        return {"error": "Scene deletion failed"}

    if not found and not removed:
        # Tracked by neither the store nor scenes.yaml — nothing to delete.
        return {"error": f"Scene {_sanitize(scene_id)} not found"}

    if not removed:
        try:
            await hass.services.async_call("scene", "reload", blocking=True)
        except Exception as exc:  # noqa: BLE001
            await store.async_restore(scene_id)
            return {"error": f"Scene reload failed: {exc}"}

    conv_store: ConversationStore = hass.data.setdefault(DOMAIN, {}).setdefault(
        "_conv_store", ConversationStore(hass)
    )
    await conv_store.remove_scene_from_sessions(scene_id)
    async_dispatcher_send(hass, SIGNAL_SCENE_DELETED, scene_id)

    return {"scene_id": scene_id, "status": "deleted"}


async def _preview_delete_scene(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Resolve a delete_scene target WITHOUT deleting it.

    Read-only counterpart of :func:`_tool_delete_scene` used by the chat
    ``delete_scene`` tool: it identifies the scene and returns a
    ``{kind, target_id, entity_id, label}`` descriptor for the confirmation
    card. The full deletability safeguards (yaml-managed only, soft-delete +
    reload rollback, session purge) run at confirm time in
    ``_tool_delete_scene`` — the preview only needs to confirm the scene
    exists and produce a human-readable label.
    """
    from ..helpers import get_scene_store  # noqa: PLC0415
    from ..scene_utils import resolve_scene_entity_id  # noqa: PLC0415

    scene_id = str(arguments.get("scene_id", "")).strip()
    entity_id_arg = str(arguments.get("entity_id", "")).strip()

    if not scene_id and not entity_id_arg:
        return {"error": "scene_id or entity_id is required"}
    if entity_id_arg and not entity_id_arg.startswith("scene."):
        return {"error": f"entity_id must be a scene entity, got {_sanitize(entity_id_arg)}"}

    store = get_scene_store(hass)
    await store.async_reconcile_yaml(force=True)
    records = await store.async_list_scenes()

    entity_id = entity_id_arg
    label: str | None = None
    # Immutable identity fingerprint for the id-less confirm path (see the
    # native-scene branch below). Empty unless the scene has no stable id.
    name_fingerprint: str = ""

    # Prefer a Selora SceneStore record so the confirm-time delete gets the
    # scene_id (soft-delete + session purge). Match by scene_id, then by the
    # record's *current* entity_id (resolved through the registry so a rename
    # in HA doesn't cause a miss).
    match: dict[str, Any] | None = None
    if scene_id:
        match = next((r for r in records if r["scene_id"] == scene_id), None)
    if match is None and entity_id_arg:
        for r in records:
            current = resolve_scene_entity_id(hass, r["scene_id"], r.get("name")) or r.get(
                "entity_id"
            )
            if current == entity_id_arg:
                match = r
                break
    if match is not None:
        scene_id = match["scene_id"]
        entity_id = (
            resolve_scene_entity_id(hass, scene_id, match.get("name"))
            or match.get("entity_id")
            or entity_id
        )
        label = match.get("name") or None

    if not entity_id:
        return {"error": f"Scene {_sanitize(scene_id)} not found"}

    state = hass.states.get(entity_id)

    if match is None:
        # No SceneStore record: this is a native (user-authored) scene. A
        # caller-supplied ``scene_id`` here is stale or non-Selora — it names
        # no store record — so discard it and derive identity from the actual
        # scenes.yaml entry instead. A storage/UI-managed scene has live state
        # but no yaml entry; offering a Delete button for it would surface a
        # card that ``_tool_delete_scene`` rejects at confirm time, so refuse
        # up front and point the user at the HA UI.
        from ..scene_utils import (  # noqa: PLC0415
            _get_scenes_path,
            _read_scenes_yaml,
            resolve_yaml_scene_entity_id,
        )

        if state is None:
            return {"error": f"Scene {_sanitize(entity_id)} not found"}
        try:
            yaml_entries = await hass.async_add_executor_job(
                _read_scenes_yaml, _get_scenes_path(hass)
            )
        except Exception:  # noqa: BLE001 — treat an unreadable file as no match
            yaml_entries = []
        yaml_match = next(
            (
                e
                for e in yaml_entries
                if isinstance(e, dict) and resolve_yaml_scene_entity_id(hass, e) == entity_id
            ),
            None,
        )
        if yaml_match is None:
            return {
                "error": (
                    f"Scene {_sanitize(entity_id)} is not yaml-managed; "
                    "delete it from the Home Assistant UI instead."
                )
            }
        yaml_id = yaml_match.get("id")
        if isinstance(yaml_id, str) and yaml_id:
            # The yaml entry has its own stable id — confirm by that id, the
            # same way SceneStore scenes and id-bearing automations delete.
            scene_id = yaml_id
        else:
            # Id-less entry: no stable handle, so the confirm delete must
            # re-resolve the mutable entity_id. Capture the entry's name as an
            # immutable fingerprint the confirm handler revalidates against the
            # current yaml before that irreversible by-entity delete — an edit
            # + reload could otherwise remap the entity to a different scene.
            scene_id = ""
            name_fingerprint = str(yaml_match.get("name") or "")
        label = yaml_match.get("name") or label

    if label is None:
        label = (state.attributes.get("friendly_name") if state is not None else None) or entity_id

    return {
        "requires_approval": True,
        "delete": {
            "kind": "scene",
            # target_id carries the SceneStore id or the yaml entry's own id
            # when either is known; the confirm-time delete falls back to
            # entity_id (revalidated via ``name``) for id-less native scenes.
            "target_id": scene_id,
            "entity_id": entity_id,
            "name": name_fingerprint,
            "label": str(label),
        },
    }


async def _tool_activate_scene(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Activate any Home Assistant scene by calling scene.turn_on.

    Accepts either ``entity_id`` (e.g. ``scene.movie_night``) for any HA scene,
    or ``scene_id`` for a Selora-managed scene (resolved via the SceneStore).
    """
    entity_id = str(arguments.get("entity_id", "")).strip()
    scene_id = str(arguments.get("scene_id", "")).strip()

    if not entity_id and not scene_id:
        return {"error": "entity_id or scene_id is required"}

    if entity_id:
        if not entity_id.startswith("scene."):
            return {"error": f"entity_id must be a scene entity, got {_sanitize(entity_id)}"}
        if hass.states.get(entity_id) is None:
            return {"error": f"Scene entity {_sanitize(entity_id)} not found"}
    else:
        from ..helpers import get_scene_store  # noqa: PLC0415
        from ..scene_utils import resolve_scene_entity_id  # noqa: PLC0415

        store = get_scene_store(hass)
        await store.async_reconcile_yaml(force=True)
        record = await store.async_get_scene(scene_id)
        if record is None:
            return {"error": f"Scene {_sanitize(scene_id)} not tracked by Selora"}
        if record.get("deleted_at") is not None:
            return {"error": "Scene has been deleted"}

        resolved = resolve_scene_entity_id(hass, scene_id, record.get("name"))
        if resolved is None:
            cached = record.get("entity_id")
            if cached and hass.states.get(cached) is not None:
                resolved = cached
        if resolved is None:
            try:
                await hass.services.async_call("scene", "reload", blocking=True)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("scene.reload failed before activation of %s", scene_id)
            resolved = resolve_scene_entity_id(hass, scene_id, record.get("name"))
        if resolved is None:
            return {"error": "Scene entity not found in Home Assistant"}
        entity_id = resolved

    try:
        await hass.services.async_call("scene", "turn_on", {"entity_id": entity_id}, blocking=True)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to activate scene %s: %s", entity_id, exc)
        return {"error": f"Activation failed: {exc}"}

    return {"entity_id": entity_id, "status": "activated"}
