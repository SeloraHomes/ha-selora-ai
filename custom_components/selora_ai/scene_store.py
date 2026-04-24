"""SceneStore -- persists scene lifecycle metadata.

Backed by HA's Store API (same pattern as AutomationStore).
Tracks which scenes were created by Selora, their metadata, and
soft-delete state.

Data layout in storage:
    {
        "scenes": {
            "<scene_id>": {
                "scene_id": str,
                "name": str,
                "entity_count": int,
                "session_id": str | None,
                "created_at": str,        # ISO datetime
                "updated_at": str,        # ISO datetime
                "deleted_at": str | None, # ISO datetime or None
            }
        }
    }
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
import logging
import time
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import SCENE_STORE_KEY

if TYPE_CHECKING:
    from .types import SceneRecord, SceneStoreData

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION = 1
# Re-read scenes.yaml at most every 30 seconds to pick up external edits
# without hammering disk on every websocket call.
_RECONCILE_INTERVAL_S = 30.0


def scene_content_hash(scene_id: str, name: str, entities: dict) -> str:
    """Compute a deterministic SHA-256 digest of a scene's content."""
    import hashlib
    import json

    payload = json.dumps({"id": scene_id, "name": name, "entities": entities}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


class SceneStore:
    """Lifecycle store for Selora-managed scenes."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store[SceneStoreData] = Store(
            hass, version=_STORE_VERSION, key=SCENE_STORE_KEY
        )
        self._data: SceneStoreData | None = None
        self._last_reconcile: float = 0.0
        self._lock = asyncio.Lock()

    async def _ensure_loaded(self) -> None:
        if self._data is None:
            raw = await self._store.async_load()
            if isinstance(raw, dict):
                self._data = raw
            else:
                self._data = {"scenes": {}}

    async def async_reconcile_yaml(self, *, force: bool = False) -> int:
        """Reconcile the store with scenes.yaml.

        Imports new Selora scenes, restores soft-deleted scenes that
        reappeared, refreshes metadata for active scenes that were edited
        externally, and soft-deletes records whose YAML entry was removed.

        Serialized via ``_lock`` so a concurrent delete cannot race with
        an in-flight reconcile.  Throttled to run at most once per
        ``_RECONCILE_INTERVAL_S`` seconds unless *force* is True (used
        by explicit scene-management endpoints that need fresh state).
        A transient read failure skips the cycle without updating the
        timer so the next call retries immediately.

        Returns the number of scenes imported (not counting reconciled).
        """
        now_mono = time.monotonic()
        if not force and now_mono - self._last_reconcile < _RECONCILE_INTERVAL_S:
            return 0

        async with self._lock:
            return await self._reconcile_locked(now_mono, force=force)

    async def _reconcile_locked(self, now_mono: float, *, force: bool = False) -> int:
        """Inner reconcile body — must be called under ``_lock``."""
        # Re-check after acquiring the lock (another caller may have run)
        if not force and now_mono - self._last_reconcile < _RECONCILE_INTERVAL_S:
            return 0

        await self._ensure_loaded()
        assert self._data is not None  # type narrowing after _ensure_loaded

        try:
            from .const import SCENE_ID_PREFIX  # noqa: PLC0415
            from .scene_utils import (  # noqa: PLC0415
                _get_scenes_path,
                _read_scenes_yaml,
                resolve_scene_entity_id,
            )

            scenes_path = _get_scenes_path(self._hass)
            yaml_scenes = await self._hass.async_add_executor_job(_read_scenes_yaml, scenes_path)
        except Exception:  # noqa: BLE001 — best-effort migration, don't block the endpoint
            _LOGGER.debug("Reconcile skipped: could not read scenes.yaml")
            return 0

        # YAML read succeeded — update timer so we don't re-read too often
        self._last_reconcile = time.monotonic()

        # Filter to schema-valid Selora entries: must be a dict with a string
        # id starting with the Selora prefix and a dict entities field.  Stray
        # items, non-string ids, or null/missing entities from manual edits
        # are silently skipped so one bad entry can't crash reconciliation.
        def _is_valid_selora(entry: object) -> bool:
            if not isinstance(entry, dict):
                return False
            sid = entry.get("id")
            return (
                isinstance(sid, str)
                and sid.startswith(SCENE_ID_PREFIX)
                and isinstance(entry.get("entities"), dict)
            )

        valid_entries = [e for e in yaml_scenes if _is_valid_selora(e)]

        # Track *all* Selora-prefixed IDs present in the file, even if the
        # entry is temporarily malformed (e.g. ``entities: null`` from a
        # manual edit).  This prevents the delete pass from tombstoning an
        # entry that is still physically present but unreadable.
        yaml_ids: set[str] = {
            entry["id"]
            for entry in yaml_scenes
            if isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and entry["id"].startswith(SCENE_ID_PREFIX)
        }

        dirty = False
        now = datetime.now(UTC).isoformat()

        # Import, restore, or refresh scenes from YAML
        imported = 0
        # (scene_id, session_id, name, scene_yaml)
        restored: list[tuple[str, str | None, str, str]] = []
        refreshed: list[tuple[str, str, str]] = []  # (scene_id, name, yaml_repr)
        for entry in valid_entries:
            sid: str = entry["id"]  # guaranteed str by _is_valid_selora

            raw_name = entry.get("name", "")
            if not isinstance(raw_name, str):
                raw_name = str(raw_name)
            name = raw_name.removeprefix("[Selora AI] ") if raw_name else sid
            entities: dict = entry["entities"]  # guaranteed dict by _is_valid_selora
            entity_id = resolve_scene_entity_id(self._hass, sid, raw_name)
            ent_count = len(entities)

            content_hash = scene_content_hash(sid, raw_name, entities)
            existing = self._data["scenes"].get(sid)

            if existing is None:
                self._data["scenes"][sid] = {
                    "scene_id": sid,
                    "name": name,
                    "entity_count": ent_count,
                    "entity_id": entity_id,
                    "session_id": None,
                    "created_at": now,
                    "updated_at": now,
                    "deleted_at": None,
                    "_content_hash": content_hash,
                }
                imported += 1
                dirty = True
            elif existing.get("deleted_at") is not None:
                import yaml as pyyaml  # noqa: PLC0415

                scene_yaml = pyyaml.dump(
                    {"name": name, "entities": entities},
                    default_flow_style=False,
                    allow_unicode=True,
                )
                existing["deleted_at"] = None
                existing["name"] = name
                existing["entity_count"] = ent_count
                existing["entity_id"] = entity_id
                existing["_content_hash"] = content_hash
                existing["updated_at"] = now
                restored.append((sid, existing.get("session_id"), name, scene_yaml))
                imported += 1
                dirty = True
                _LOGGER.info("Restored previously deleted scene %s from scenes.yaml", sid)
            else:
                # Compare content hash to detect any edit (including
                # content-only changes like brightness/color).
                if content_hash != existing.get("_content_hash", ""):
                    # Build YAML text matching the format llm_client produces
                    # for scene_yaml (used by session history and LLM context).
                    import yaml as pyyaml  # noqa: PLC0415

                    scene_yaml = pyyaml.dump(
                        {"name": name, "entities": entities},
                        default_flow_style=False,
                        allow_unicode=True,
                    )
                    refreshed.append((sid, name, scene_yaml))
                    existing["name"] = name
                    existing["entity_count"] = ent_count
                    existing["entity_id"] = entity_id
                    existing["_content_hash"] = content_hash
                    existing["updated_at"] = now
                    dirty = True

        # Soft-delete store records whose YAML entry was removed externally
        reconciled_ids: list[str] = []
        for sid, record in self._data["scenes"].items():
            if record.get("deleted_at") is not None:
                continue
            if sid not in yaml_ids:
                record["deleted_at"] = now
                dirty = True
                reconciled_ids.append(sid)
                _LOGGER.info("Reconciled scene %s: missing from scenes.yaml, marked deleted", sid)

        if dirty:
            await self._save()

        # Update session context and fire dispatcher signals
        if reconciled_ids or refreshed or restored:
            from homeassistant.helpers.dispatcher import async_dispatcher_send  # noqa: PLC0415

            from . import ConversationStore  # noqa: PLC0415
            from .const import (  # noqa: PLC0415
                DOMAIN,
                SIGNAL_SCENE_DELETED,
                SIGNAL_SCENE_REFRESHED,
                SIGNAL_SCENE_RESTORED,
            )

            domain_data = self._hass.data.setdefault(DOMAIN, {})
            conv_store = domain_data.setdefault("_conv_store", ConversationStore(self._hass))

            for sid in reconciled_ids:
                await conv_store.remove_scene_from_sessions(sid)
                async_dispatcher_send(self._hass, SIGNAL_SCENE_DELETED, sid)

            # Update stale session YAML for externally edited scenes —
            # both persisted sessions and Assist's in-memory index.
            for sid, s_name, yaml_repr in refreshed:
                await conv_store.update_scene_in_sessions(sid, s_name, yaml_repr)
                async_dispatcher_send(self._hass, SIGNAL_SCENE_REFRESHED, sid, s_name, yaml_repr)

            # Rehydrate restored scenes into their originating session and
            # clear the Assist tombstone so they can be refined again.
            for sid, sess_id, s_name, yaml_repr in restored:
                if sess_id:
                    await conv_store.add_scene_to_session(sess_id, sid, s_name, yaml_repr)
                async_dispatcher_send(self._hass, SIGNAL_SCENE_RESTORED, sid, s_name, yaml_repr)

        if imported:
            _LOGGER.info("Backfilled %d pre-existing Selora scene(s) from scenes.yaml", imported)
        return imported

    def reset_reconcile_timer(self) -> None:
        """Reset the throttle so the next reconcile runs after a full interval.

        Called after an explicit delete to prevent a concurrent reconcile
        (which may have read YAML before the deletion) from restoring the
        scene.
        """
        self._last_reconcile = time.monotonic()

    async def _save(self) -> None:
        if self._data is not None:
            await self._store.async_save(self._data)

    async def async_add_scene(
        self,
        scene_id: str,
        name: str,
        entity_count: int,
        session_id: str | None = None,
        entity_id: str | None = None,
        content_hash: str | None = None,
    ) -> SceneRecord:
        """Record a newly created or refined scene.

        On refinement (same *scene_id* already exists) the mutable fields
        are updated but ``created_at`` and the original ``session_id`` are
        preserved so lifecycle provenance stays accurate.

        *content_hash* is a SHA-256 digest of the scene's content (from
        ``scene_content_hash``).  When provided, it prevents the next
        reconcile from spuriously refreshing this scene.

        Serialized with ``_lock`` so an in-flight reconcile cannot see
        the new record and mark it deleted from a stale ``yaml_ids`` set.
        """
        async with self._lock:
            await self._ensure_loaded()
            assert self._data is not None  # type narrowing after _ensure_loaded

            now = datetime.now(UTC).isoformat()
            existing = self._data["scenes"].get(scene_id)

            record: SceneRecord = {
                "scene_id": scene_id,
                "name": name,
                "entity_count": entity_count,
                "entity_id": entity_id,
                "session_id": (existing["session_id"] or session_id) if existing else session_id,
                "created_at": existing["created_at"] if existing else now,
                "updated_at": now,
                "deleted_at": None,
            }
            if content_hash is not None:
                record["_content_hash"] = content_hash  # type: ignore[typeddict-unknown-key]
            elif existing and "_content_hash" in existing:
                record["_content_hash"] = existing["_content_hash"]  # type: ignore[typeddict-unknown-key]
            self._data["scenes"][scene_id] = record
            await self._save()
            return record

    async def async_update_scene(
        self,
        scene_id: str,
        entity_count: int | None = None,
        name: str | None = None,
    ) -> SceneRecord | None:
        """Update metadata for an existing scene."""
        await self._ensure_loaded()
        assert self._data is not None  # type narrowing after _ensure_loaded

        record = self._data["scenes"].get(scene_id)
        if record is None:
            return None

        if entity_count is not None:
            record["entity_count"] = entity_count
        if name is not None:
            record["name"] = name
        record["updated_at"] = datetime.now(UTC).isoformat()
        await self._save()
        return record

    async def async_get_scene(self, scene_id: str) -> SceneRecord | None:
        """Retrieve a scene record by ID."""
        await self._ensure_loaded()
        assert self._data is not None  # type narrowing after _ensure_loaded
        return self._data["scenes"].get(scene_id)

    async def async_list_scenes(self, include_deleted: bool = False) -> list[SceneRecord]:
        """List all tracked scenes."""
        await self._ensure_loaded()
        assert self._data is not None  # type narrowing after _ensure_loaded

        scenes = list(self._data["scenes"].values())
        if not include_deleted:
            scenes = [s for s in scenes if s.get("deleted_at") is None]
        return scenes

    async def async_soft_delete(self, scene_id: str) -> bool:
        """Mark a scene as soft-deleted.

        Serialized with ``async_reconcile_yaml`` so an in-flight reconcile
        cannot restore a scene that is being deleted.
        """
        async with self._lock:
            return await self._soft_delete_locked(scene_id)

    async def _soft_delete_locked(self, scene_id: str) -> bool:
        """Inner soft-delete — must be called under ``_lock``."""
        await self._ensure_loaded()
        assert self._data is not None  # type narrowing after _ensure_loaded

        record = self._data["scenes"].get(scene_id)
        if record is None:
            return False

        record["deleted_at"] = datetime.now(UTC).isoformat()
        await self._save()
        return True

    async def async_delete_with_yaml(
        self,
        scene_id: str,
        remove_yaml_fn: Callable[[str], Coroutine[None, None, bool]],
    ) -> tuple[bool, bool]:
        """Soft-delete a scene and remove it from scenes.yaml atomically.

        Holds ``_lock`` across both operations so a concurrent reconcile
        cannot restore the record between the store mutation and the YAML
        removal.

        *remove_yaml_fn* is an ``async`` callable that removes the scene
        from YAML and returns ``bool`` (True if the entry was found).
        Raises on reload failure.

        Returns ``(found_in_store, removed_from_yaml)``.
        """
        async with self._lock:
            if not await self._soft_delete_locked(scene_id):
                return False, False

            removed = await remove_yaml_fn(scene_id)

            # Re-stamp deleted_at in case a concurrent reconcile (which
            # could have read YAML before the removal) ran between
            # awaits inside remove_yaml_fn and cleared deleted_at.
            await self._ensure_loaded()
            assert self._data is not None
            record = self._data["scenes"].get(scene_id)
            if record is not None and record.get("deleted_at") is None:
                record["deleted_at"] = datetime.now(UTC).isoformat()
                await self._save()

            self._last_reconcile = time.monotonic()
            return True, removed

    async def async_restore(self, scene_id: str) -> bool:
        """Un-delete a soft-deleted scene."""
        await self._ensure_loaded()
        assert self._data is not None  # type narrowing after _ensure_loaded

        record = self._data["scenes"].get(scene_id)
        if record is None or record.get("deleted_at") is None:
            return False

        record["deleted_at"] = None
        record["updated_at"] = datetime.now(UTC).isoformat()
        await self._save()
        return True
