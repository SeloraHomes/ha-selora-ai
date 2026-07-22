"""Selora AI websocket handlers: scenes.

Extracted from __init__.py. Handlers reach shared integration
helpers via ``from .. import`` (safe: this module is imported
lazily at registration time, after the package has loaded).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import decorators
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
import voluptuous as vol

from .. import (
    _async_apply_scene_entity,
    _find_active_scenes,
    _get_scene_store,
    _require_admin,
)
from ..const import (
    DOMAIN,
    SIGNAL_SCENE_DELETED,
)
from ..conversation_store import ConversationStore

_LOGGER = logging.getLogger(__name__)

# Human-readable messages for async_remove_yaml_scene_by_entity error codes.
_DELETE_SCENE_ERRORS = {
    "not_found": "Scene not found",
    "not_yaml_managed": "Scene is not yaml-managed; delete it from Home Assistant instead.",
    "not_found_in_yaml": "Scene not found in scenes.yaml",
    "no_identifier": "Scene has no 'id' or 'name' in scenes.yaml; cannot identify it safely.",
    "ambiguous_name": "Multiple scenes share this name; add an 'id' to delete it safely.",
}


def _delete_scene_error(code: str | None, detail: str | None) -> str:
    """Map an entity-delete error code (+ optional detail) to a message."""
    if code == "reload_failed":
        return f"Scene reload failed: {detail}" if detail else "Scene reload failed"
    if code == "yaml_read_failed":
        return f"Failed to read scenes.yaml: {detail}" if detail else "Failed to read scenes.yaml"
    return _DELETE_SCENE_ERRORS.get(code or "", "Scene deletion failed")


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/set_scene_status",
        vol.Required("session_id"): str,
        vol.Required("message_index"): int,
        vol.Required("status"): str,
    }
)
async def _handle_websocket_set_scene_status(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Update the acceptance status of a scene proposal in a session."""
    if not _require_admin(connection, msg):
        return

    valid_statuses = {"pending", "saved", "declined", "refining"}
    if msg["status"] not in valid_statuses:
        connection.send_error(
            msg["id"], "invalid_status", f"Status must be one of {valid_statuses}"
        )
        return

    store: ConversationStore = hass.data[DOMAIN].setdefault("_conv_store", ConversationStore(hass))
    ok = await store.set_scene_status(msg["session_id"], msg["message_index"], msg["status"])
    if not ok:
        connection.send_error(msg["id"], "not_found", "Session or message not found")
        return
    connection.send_result(msg["id"], {"status": "updated"})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/accept_scene",
        vol.Required("session_id"): str,
        vol.Required("message_index"): int,
    }
)
async def _handle_websocket_accept_scene(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Accept a scene proposal — actually create it in HA now.

    The refinement target is read from the stored proposal, never from the
    websocket payload, so a stale or crafted client cannot steer the accept
    into overwriting an unrelated Selora-managed scene.
    """
    if not _require_admin(connection, msg):
        return

    store: ConversationStore = hass.data[DOMAIN].setdefault("_conv_store", ConversationStore(hass))
    session = await store.get_session(msg["session_id"])
    if not session:
        connection.send_error(msg["id"], "not_found", "Session not found")
        return
    msgs = session.get("messages", [])
    mi = msg["message_index"]
    if mi < 0 or mi >= len(msgs):
        connection.send_error(msg["id"], "not_found", "Message not found")
        return
    chat_msg = msgs[mi]
    scene_data = chat_msg.get("scene")
    if not scene_data:
        connection.send_error(msg["id"], "no_scene", "No scene data on this message")
        return

    # Atomic check-and-reserve. Single-threaded asyncio guarantees no other
    # coroutine can interleave between this check and the in-memory status
    # mutation below — a concurrent accept (double-click, retry, stale tab,
    # second admin tab) will resume after this point and see "accepting",
    # so it cannot also reach the create path. The reservation is purely
    # in-memory; we only persist on success ("saved") or on failure
    # (restored to the original status), so a crash cannot leave a
    # message stuck in "accepting".
    current_status = chat_msg.get("scene_status")
    if chat_msg.get("scene_id") or current_status not in ("pending", "refining"):
        connection.send_error(
            msg["id"],
            "already_handled",
            f"Scene proposal is already {current_status or 'saved'}",
        )
        return
    chat_msg["scene_status"] = "accepting"

    try:
        from ..scene_utils import async_create_scene  # noqa: PLC0415

        scenes_ctx = _find_active_scenes(session, msgs)
        # Always pass a concrete set (even empty) so async_create_scene cannot
        # fall back to its allow-any-Selora-scene path when the session has
        # no recorded scenes.
        session_scene_ids = {s[0] for s in scenes_ctx}
        scene_result = await async_create_scene(
            hass,
            scene_data,
            existing_scene_id=chat_msg.get("refine_scene_id"),
            session_scene_ids=session_scene_ids,
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to create scene on accept: %s", exc)
        chat_msg["scene_status"] = current_status
        connection.send_error(msg["id"], "create_failed", str(exc))
        return

    if scene_result is not None:
        try:
            scene_store = _get_scene_store(hass)
            await scene_store.async_add_scene(
                scene_result["scene_id"],
                scene_result["name"],
                scene_result["entity_count"],
                session_id=msg["session_id"],
                entity_id=scene_result.get("entity_id"),
                content_hash=scene_result.get("content_hash"),
            )
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Failed to record scene %s in store", scene_result["scene_id"])

    await store.set_scene_status(
        msg["session_id"],
        mi,
        "saved",
        scene_id=scene_result["scene_id"],
        entity_id=scene_result.get("entity_id"),
    )
    connection.send_result(
        msg["id"],
        {
            "scene_id": scene_result["scene_id"],
            "entity_id": scene_result.get("entity_id"),
        },
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/apply_scene_states",
        vol.Required("entities"): dict,
    }
)
async def _handle_websocket_apply_scene_states(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Apply a set of entity states live, without saving.

    Used by the panel's "Test" button to preview an edited scene on the
    real devices. Applies each entity with direct per-domain services
    (not scene.apply) so optional attributes the scene omits (e.g. a
    fan's direction) don't trigger service calls with None values.
    """
    if not _require_admin(connection, msg):
        return

    entities: dict[str, Any] = msg["entities"]
    if not entities:
        connection.send_error(msg["id"], "no_entities", "No entities to apply")
        return

    _LOGGER.debug("Applying scene states for test: %s", entities)
    errors: list[str] = []
    for entity_id, state in entities.items():
        if not isinstance(state, dict) or "state" not in state:
            continue
        try:
            await _async_apply_scene_entity(hass, entity_id, state)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Failed to apply %s: %s", entity_id, exc)
            errors.append(f"{entity_id}: {exc}")

    if errors:
        connection.send_error(msg["id"], "apply_failed", "; ".join(errors))
        return

    connection.send_result(msg["id"], {"ok": True})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/save_scene_edits",
        vol.Required("scene_id"): str,
        vol.Required("entities"): dict,
    }
)
async def _handle_websocket_save_scene_edits(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Persist user edits to a saved Selora scene's desired states.

    The panel lets users adjust each entity's target state directly on the
    scene card; this rewrites the matching ``scenes.yaml`` entry, refreshes
    the scene store, and updates any session that references the scene.

    The scene name is taken from the stored record (never the payload) and
    the target is restricted to an existing Selora-managed scene, so a
    crafted client cannot create or retarget an arbitrary scene.
    """
    if not _require_admin(connection, msg):
        return

    scene_id = msg["scene_id"]
    scene_store = _get_scene_store(hass)
    record = await scene_store.async_get_scene(scene_id)
    if record is None or record.get("deleted_at") is not None:
        connection.send_error(msg["id"], "not_found", "Scene not found")
        return

    # Strip the "[Selora AI] " display prefix so the writer (which re-adds
    # it) doesn't double it.
    raw_name = record.get("name") or ""
    name = raw_name.removeprefix("[Selora AI] ").strip() or raw_name

    from ..scene_utils import async_create_scene, validate_scene_payload  # noqa: PLC0415

    ok, reason, normalized = validate_scene_payload(
        {"name": name, "entities": msg["entities"]}, hass
    )
    if not ok or normalized is None:
        connection.send_error(msg["id"], "invalid_scene", reason)
        return

    try:
        result = await async_create_scene(
            hass,
            normalized,
            existing_scene_id=scene_id,
            # Trusted admin edit of a known Selora scene — bypass the
            # session-membership gate (None) but keep the prefix check.
            session_scene_ids=None,
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to save scene edits for %s: %s", scene_id, exc)
        connection.send_error(msg["id"], "save_failed", str(exc))
        return

    try:
        await scene_store.async_add_scene(
            result["scene_id"],
            result["name"],
            result["entity_count"],
            entity_id=result.get("entity_id"),
            content_hash=result.get("content_hash"),
        )
    except Exception:  # noqa: BLE001
        _LOGGER.warning("Failed to update scene %s in store after edit", scene_id)

    try:
        store: ConversationStore = hass.data[DOMAIN].setdefault(
            "_conv_store", ConversationStore(hass)
        )
        await store.update_scene_in_sessions(scene_id, result["name"], result["scene_yaml"])
    except Exception:  # noqa: BLE001
        _LOGGER.warning("Failed to propagate scene %s edits to sessions", scene_id)

    connection.send_result(
        msg["id"],
        {
            "scene_id": result["scene_id"],
            "entity_id": result.get("entity_id"),
            "entity_count": result["entity_count"],
            "scene_yaml": result["scene_yaml"],
            "entities": normalized["entities"],
        },
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_scenes",
    }
)
async def _handle_websocket_get_scenes(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return all Selora-managed scenes (force-reconciles with scenes.yaml).

    Each scene record is enriched with the parsed ``entities`` map and a
    rendered ``yaml`` string so the panel can display rich entity state
    targets and the YAML view without a second round-trip.
    """
    if not _require_admin(connection, msg):
        return

    store = _get_scene_store(hass)
    await store.async_reconcile_yaml(force=True)
    scenes = await store.async_list_scenes()

    from ..recipes.attribution import async_build_recipe_attribution  # noqa: PLC0415

    attribution = await async_build_recipe_attribution(hass)

    def _recipe_fields(scene_id: str, name: str) -> dict[str, str]:
        ref = attribution["scenes_by_id"].get(scene_id) or attribution["scenes_by_name"].get(name)
        return {
            "recipe_slug": ref["slug"] if ref else "",
            "recipe_title": ref["title"] if ref else "",
        }

    from ..scene_utils import _get_scenes_path, _read_scenes_yaml  # noqa: PLC0415

    scenes_path = _get_scenes_path(hass)
    try:
        yaml_entries = await hass.async_add_executor_job(_read_scenes_yaml, scenes_path)
    except Exception as exc:  # noqa: BLE001 — best-effort enrichment, never block the list
        _LOGGER.warning("Failed to read scenes.yaml for enrichment: %s", exc)
        yaml_entries = []

    yaml_by_id: dict[str, dict[str, Any]] = {
        entry["id"]: entry
        for entry in yaml_entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }

    import yaml as pyyaml  # noqa: PLC0415

    from ..const import SCENE_ID_PREFIX  # noqa: PLC0415
    from ..scene_utils import (  # noqa: PLC0415
        resolve_scene_entity_id,
        resolve_yaml_scene_entity_id,
    )

    enriched: list[dict[str, Any]] = []
    for record in scenes:
        entry = yaml_by_id.get(record["scene_id"])
        if entry is None:
            enriched.append(
                {**record, "entities": {}, "yaml": "", "source": "selora", "deletable": True}
            )
            continue
        entities = entry.get("entities") or {}
        if not isinstance(entities, dict):
            entities = {}
        try:
            yaml_text = pyyaml.dump(
                {"name": entry.get("name", record["name"]), "entities": entities},
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        except Exception:  # noqa: BLE001 — fall back to empty YAML, never block the list
            yaml_text = ""
        enriched.append(
            {
                **record,
                "entities": entities,
                "yaml": yaml_text,
                "source": "selora",
                "deletable": True,
            }
        )

    # Include non-Selora scenes from yaml (e.g. hand-crafted or other-integration scenes)
    covered_scene_ids: set[str] = {r["scene_id"] for r in enriched}
    for entry in yaml_entries:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("id")
        if not isinstance(sid, str) or sid.startswith(SCENE_ID_PREFIX):
            continue
        if sid in covered_scene_ids:
            continue
        covered_scene_ids.add(sid)
        raw_name = entry.get("name", "")
        if not isinstance(raw_name, str):
            raw_name = str(raw_name)
        name = raw_name.strip() or sid
        entities = entry.get("entities") or {}
        if not isinstance(entities, dict):
            entities = {}
        entity_id = resolve_scene_entity_id(hass, sid, raw_name)
        try:
            yaml_text = pyyaml.dump(
                {"name": name, "entities": entities},
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        except Exception:  # noqa: BLE001
            yaml_text = ""
        enriched.append(
            {
                "scene_id": sid,
                "name": name,
                "entity_id": entity_id,
                "entity_count": len(entities),
                "session_id": None,
                "created_at": None,
                "updated_at": None,
                "deleted_at": None,
                "entities": entities,
                "yaml": yaml_text,
                "source": "home_assistant",
                # Present in scenes.yaml → removable via the YAML writer.
                "deletable": True,
            }
        )

    # Map id-less scenes.yaml entries to their loaded entity_id. These have no
    # usable `id` (so they're skipped by the id loop above) but are still
    # yaml-managed and deletable by name — the panel deletes them by entity_id.
    idless_yaml_by_entity: dict[str, dict[str, Any]] = {}
    for entry in yaml_entries:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("id")
        if isinstance(sid, str) and sid:
            continue  # id-bearing entries are handled above
        eid = resolve_yaml_scene_entity_id(hass, entry)
        if eid:
            idless_yaml_by_entity[eid] = entry

    # Include any remaining HA scene states not covered by yaml-by-id above.
    covered_entity_ids: set[str] = {r["entity_id"] for r in enriched if r.get("entity_id")}
    for state in hass.states.async_all("scene"):
        if state.entity_id in covered_entity_ids:
            continue
        object_id = state.entity_id.removeprefix("scene.")
        if object_id.startswith(SCENE_ID_PREFIX):
            continue
        name = state.attributes.get("friendly_name") or state.name or object_id
        # An id-less yaml entry is removable (by entity_id); anything else here
        # is HA UI storage or another integration and cannot be removed.
        idless_entry = idless_yaml_by_entity.get(state.entity_id)
        entities = (idless_entry or {}).get("entities") or {}
        if not isinstance(entities, dict):
            entities = {}
        yaml_text = ""
        if idless_entry is not None:
            try:
                yaml_text = pyyaml.dump(
                    {"name": name, "entities": entities},
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            except Exception:  # noqa: BLE001 — fall back to empty YAML, never block the list
                yaml_text = ""
        enriched.append(
            {
                "scene_id": object_id,
                "name": name,
                "entity_id": state.entity_id,
                "entity_count": len(entities),
                "session_id": None,
                "created_at": None,
                "updated_at": None,
                "deleted_at": None,
                "entities": entities,
                "yaml": yaml_text,
                "source": "home_assistant",
                "deletable": idless_entry is not None,
            }
        )

    for item in enriched:
        item.update(_recipe_fields(item.get("scene_id", ""), item.get("name", "")))

    connection.send_result(msg["id"], {"scenes": enriched})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/load_scene_to_session",
        vol.Required("scene_id"): str,
    }
)
async def _handle_websocket_load_scene_to_session(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Open a chat session pre-loaded with the scene's current entities for refinement.

    Inserts an assistant message showing the scene as a refining card.
    Subsequent selora_ai/chat messages refine it via the existing chat handler.
    """
    if not _require_admin(connection, msg):
        return

    scene_id = msg["scene_id"]

    # Resolve scene data — store first, then yaml, then HA states
    from ..const import SCENE_ID_PREFIX  # noqa: PLC0415
    from ..scene_utils import _get_scenes_path, _read_scenes_yaml  # noqa: PLC0415

    store = _get_scene_store(hass)
    await store.async_reconcile_yaml(force=True)
    record = await store.async_get_scene(scene_id)

    name: str = scene_id
    entities: dict[str, Any] = {}
    yaml_text: str = ""
    is_selora = scene_id.startswith(SCENE_ID_PREFIX)

    scenes_path = _get_scenes_path(hass)
    try:
        yaml_entries = await hass.async_add_executor_job(_read_scenes_yaml, scenes_path)
    except Exception:  # noqa: BLE001
        yaml_entries = []

    yaml_by_id: dict[str, dict[str, Any]] = {
        e["id"]: e for e in yaml_entries if isinstance(e, dict) and isinstance(e.get("id"), str)
    }

    if record:
        name = record.get("name") or scene_id
        entry = yaml_by_id.get(scene_id)
        if entry:
            entities = entry.get("entities") or {}
            if not isinstance(entities, dict):
                entities = {}
    else:
        # HA-owned scene — try yaml then HA state
        entry = yaml_by_id.get(scene_id)
        if entry:
            raw_name = entry.get("name", "")
            name = (raw_name.strip() if isinstance(raw_name, str) else scene_id) or scene_id
            entities = entry.get("entities") or {}
            if not isinstance(entities, dict):
                entities = {}
        else:
            state = hass.states.get(f"scene.{scene_id}")
            if state:
                name = state.attributes.get("friendly_name") or scene_id

    import yaml as pyyaml  # noqa: PLC0415

    if entities:
        try:
            yaml_text = pyyaml.dump(
                {"name": name, "entities": entities},
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        except Exception:  # noqa: BLE001
            yaml_text = ""

    conv_store: ConversationStore = hass.data[DOMAIN].setdefault(
        "_conv_store", ConversationStore(hass)
    )
    session = await conv_store.create_session()
    session_id = session["id"]

    await conv_store.update_session_title(session_id, f"Refining: {name}")

    await conv_store.append_message(
        session_id,
        "assistant",
        f"I've loaded the scene **{name}** for refinement. What changes would you like to make?",
        intent="scene",
        scene={"name": name, "entities": entities},
        scene_yaml=yaml_text or None,
        scene_id=scene_id if is_selora else None,
        refine_scene_id=scene_id if is_selora else None,
        scene_status="refining",
    )

    connection.send_result(
        msg["id"],
        {
            "session_id": session_id,
            "scene_id": scene_id,
            "name": name,
        },
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/delete_scene",
        vol.Required("scene_id"): str,
        vol.Optional("entity_id"): str,
    }
)
async def _handle_websocket_delete_scene(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete a scene — Selora-managed or any yaml-managed HA scene.

    Selora scenes and id-bearing ``scenes.yaml`` entries are removed by
    ``scene_id``. Id-less yaml entries (no ``id`` field) have no usable id,
    so they are removed by ``entity_id`` via the shared entity/name resolver
    — the same path the MCP ``delete_scene`` tool uses.
    """
    if not _require_admin(connection, msg):
        return

    scene_id = msg["scene_id"]
    entity_id = msg.get("entity_id", "")
    store = _get_scene_store(hass)
    await store.async_reconcile_yaml(force=True)

    try:
        from ..scene_utils import async_remove_scene_yaml  # noqa: PLC0415

        found, removed = await store.async_delete_with_yaml(
            scene_id,
            lambda sid: async_remove_scene_yaml(hass, sid),
        )
    except Exception as exc:  # noqa: BLE001 — propagate failure and undo soft-delete
        _LOGGER.warning("Failed to delete scene %s: %s", scene_id, exc)
        await store.async_restore(scene_id)
        connection.send_error(msg["id"], "delete_failed", str(exc))
        return

    if not found and not removed:
        # scene_id matched neither the store nor a scenes.yaml `id`. An id-less
        # yaml scene has no usable id, so fall back to entity-based removal when
        # the panel supplied an entity_id; otherwise it's genuinely not ours
        # (another integration or HA UI storage).
        if not entity_id:
            connection.send_error(msg["id"], "not_found", "Scene not found")
            return
        from ..scene_utils import async_remove_yaml_scene_by_entity  # noqa: PLC0415

        ok, code, detail = await async_remove_yaml_scene_by_entity(hass, entity_id)
        if not ok:
            connection.send_error(
                msg["id"], code or "delete_failed", _delete_scene_error(code, detail)
            )
            return
        # Removed from yaml; fall through to session cleanup + dispatch.
    elif not removed:
        # The YAML entry is already gone (external edit, another tool, or
        # stale backfill record).  Force a scene.reload so HA unloads the
        # entity if it's still active from a prior load.
        _LOGGER.info("Scene %s already absent from scenes.yaml — reloading scenes", scene_id)
        try:
            await hass.services.async_call("scene", "reload", blocking=True)
        except Exception as exc:  # noqa: BLE001 — HA may still have the scene loaded
            _LOGGER.warning("scene.reload failed after confirming %s removal: %s", scene_id, exc)
            await store.async_restore(scene_id)
            connection.send_error(msg["id"], "reload_failed", f"Scene reload failed: {exc}")
            return

    # Purge the deleted scene from all persisted session data (indexes +
    # message fields) and notify in-memory Assist indexes via dispatcher.
    conv_store: ConversationStore = hass.data[DOMAIN].setdefault(
        "_conv_store", ConversationStore(hass)
    )
    await conv_store.remove_scene_from_sessions(scene_id)
    async_dispatcher_send(hass, SIGNAL_SCENE_DELETED, scene_id)

    connection.send_result(msg["id"], {"success": True})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/activate_scene",
        vol.Required("scene_id"): str,
    }
)
async def _handle_websocket_activate_scene(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Activate a Selora-managed scene by calling scene.turn_on."""
    if not _require_admin(connection, msg):
        return

    scene_id = msg["scene_id"]

    store = _get_scene_store(hass)
    await store.async_reconcile_yaml(force=True)
    record = await store.async_get_scene(scene_id)
    if record is None:
        connection.send_error(msg["id"], "not_found", "Scene not tracked by Selora")
        return
    if record.get("deleted_at") is not None:
        connection.send_error(msg["id"], "deleted", "Scene has been deleted")
        return

    # Always resolve the entity_id from the registry — the cached value may
    # be stale after a scene.reload reassigns slugs due to name collisions.
    from ..scene_utils import resolve_scene_entity_id  # noqa: PLC0415

    entity_id = resolve_scene_entity_id(hass, scene_id, record.get("name"))
    if entity_id is None:
        # Fall back to the cached entity_id only if it still has a state
        cached = record.get("entity_id")
        if cached and hass.states.get(cached) is not None:
            entity_id = cached

    if entity_id is None:
        # Scene may exist in YAML but HA hasn't loaded it yet — reload and retry
        try:
            await hass.services.async_call("scene", "reload", blocking=True)
        except Exception:  # noqa: BLE001 — best-effort reload
            _LOGGER.debug("scene.reload failed before activation of %s", scene_id)
        entity_id = resolve_scene_entity_id(hass, scene_id, record.get("name"))

    if entity_id is None:
        connection.send_error(msg["id"], "not_found", "Scene entity not found in Home Assistant")
        return

    try:
        await hass.services.async_call("scene", "turn_on", {"entity_id": entity_id}, blocking=True)
        connection.send_result(msg["id"], {"success": True, "entity_id": entity_id})
    except Exception as exc:  # noqa: BLE001 — HA service handlers may raise beyond HA's hierarchy
        _LOGGER.error("Failed to activate scene %s: %s", scene_id, exc)
        connection.send_error(msg["id"], "activation_failed", str(exc))


def async_register(hass: HomeAssistant) -> None:
    """Register the scenes websocket commands."""
    from homeassistant.components import websocket_api

    websocket_api.async_register_command(hass, _handle_websocket_set_scene_status)
    websocket_api.async_register_command(hass, _handle_websocket_accept_scene)
    websocket_api.async_register_command(hass, _handle_websocket_save_scene_edits)
    websocket_api.async_register_command(hass, _handle_websocket_apply_scene_states)
    websocket_api.async_register_command(hass, _handle_websocket_get_scenes)
    websocket_api.async_register_command(hass, _handle_websocket_load_scene_to_session)
    websocket_api.async_register_command(hass, _handle_websocket_delete_scene)
    websocket_api.async_register_command(hass, _handle_websocket_activate_scene)
