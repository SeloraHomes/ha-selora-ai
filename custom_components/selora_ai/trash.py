"""Recently deleted — automations and scenes the user can restore.

Every delete the user asked for (panel, chat, MCP, the stale-automation
auto-purge) drops the removed ``automations.yaml`` / ``scenes.yaml`` entry
here; the internal clean-ups that delete their own temporary automations
(scheduled actions) do not. An entry is kept as it was written, so a restore
puts back exactly what was removed, for automations Selora did not create as
much as for its own. Kept ``TRASH_DAYS``, at most ``TRASH_MAX_PER_KIND`` per
kind.

A restored Selora automation gets its version history back: the delete retires
its record instead of purging it (``AutomationStore.retire_record``), over the
same window. A restored Selora scene is revived by the scene store's reconcile,
which clears ``deleted_at`` when the id is back in ``scenes.yaml``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
from typing import Any
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
import yaml

from .const import (
    DOMAIN,
    TRASH_DAYS,
    TRASH_MAX_PER_KIND,
    TRASH_STORE_KEY,
    TRASH_STORE_VERSION,
)
from .types import TrashItem

_LOGGER = logging.getLogger(__name__)

KIND_AUTOMATION = "automation"
KIND_SCENE = "scene"


class TrashRestoreError(Exception):
    """A trash item could not be put back; ``code`` says why."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


def expires_at(item: TrashItem) -> str:
    """When the item leaves the trash for good."""
    deleted = datetime.fromisoformat(item["deleted_at"])
    return (deleted + timedelta(days=TRASH_DAYS)).isoformat()


class TrashStore:
    """The deleted entries, newest first."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, version=TRASH_STORE_VERSION, key=TRASH_STORE_KEY
        )
        self._items: list[TrashItem] | None = None
        self._lock = asyncio.Lock()

    async def _loaded(self) -> list[TrashItem]:
        if self._items is None:
            raw = await self._store.async_load()
            items = raw.get("items") if isinstance(raw, dict) else None
            self._items = list(items) if isinstance(items, list) else []
        return self._items

    def _prune(self, items: list[TrashItem]) -> bool:
        """Drop items past their time or beyond the per-kind count."""
        cutoff = (datetime.now(UTC) - timedelta(days=TRASH_DAYS)).isoformat()
        seen: dict[str, int] = {}
        keep: list[TrashItem] = []
        for item in items:
            if item.get("deleted_at", "") < cutoff:
                continue
            seen[item["kind"]] = seen.get(item["kind"], 0) + 1
            if seen[item["kind"]] <= TRASH_MAX_PER_KIND:
                keep.append(item)
        if len(keep) == len(items):
            return False
        items[:] = keep
        return True

    async def _save(self) -> None:
        await self._store.async_save({"items": self._items or []})

    async def async_add(self, kind: str, entry: dict[str, Any], name: str) -> TrashItem:
        """Keep a removed entry. ``entry`` is copied as plain JSON types."""
        item: TrashItem = {
            "trash_id": uuid.uuid4().hex,
            "kind": kind,
            "name": name,
            "entry": json.loads(json.dumps(entry, default=str)),
            "deleted_at": datetime.now(UTC).isoformat(),
        }
        async with self._lock:
            items = await self._loaded()
            items.insert(0, item)
            self._prune(items)
            await self._save()
        return item

    async def async_list(self) -> list[TrashItem]:
        """Every item still kept, newest first."""
        async with self._lock:
            items = await self._loaded()
            if self._prune(items):
                await self._save()
            return list(items)

    async def async_get(self, trash_id: str) -> TrashItem | None:
        return next((i for i in await self.async_list() if i["trash_id"] == trash_id), None)

    async def async_remove(self, trash_id: str) -> bool:
        async with self._lock:
            items = await self._loaded()
            kept = [i for i in items if i["trash_id"] != trash_id]
            if len(kept) == len(items):
                return False
            items[:] = kept
            await self._save()
            return True


def get_trash_store(hass: HomeAssistant) -> TrashStore:
    """Return (or lazily create) the TrashStore from hass.data."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if "_trash_store" not in domain_data:
        domain_data["_trash_store"] = TrashStore(hass)
    return domain_data["_trash_store"]


async def async_trash(hass: HomeAssistant, kind: str, entry: dict[str, Any]) -> None:
    """Keep an entry its caller has just removed from the YAML.

    Best effort: the delete has already happened, and failing it now would
    report a removed automation as still there.
    """
    if kind == KIND_AUTOMATION:
        name = entry.get("alias") or entry.get("id") or ""
    else:
        name = entry.get("name") or entry.get("id") or ""
    try:
        await get_trash_store(hass).async_add(kind, entry, str(name))
    except (HomeAssistantError, OSError, TypeError, ValueError) as exc:
        _LOGGER.warning("Could not keep deleted %s %r for restore: %s", kind, name, exc)


async def async_restore(hass: HomeAssistant, trash_id: str) -> TrashItem:
    """Put an item back where it was deleted from, and out of the trash."""
    store = get_trash_store(hass)
    item = await store.async_get(trash_id)
    if item is None:
        raise TrashRestoreError("not_found")
    if item["kind"] == KIND_AUTOMATION:
        await _restore_automation(hass, item["entry"])
    else:
        await _restore_scene(hass, item["entry"])
    await store.async_remove(trash_id)
    return item


async def async_delete_forever(hass: HomeAssistant, trash_id: str) -> bool:
    """Drop an item, and a Selora automation's kept history with it."""
    store = get_trash_store(hass)
    item = await store.async_get(trash_id)
    if item is None:
        return False
    automation_id = item["entry"].get("id")
    if item["kind"] == KIND_AUTOMATION and isinstance(automation_id, str) and automation_id:
        from .helpers import get_automation_store  # noqa: PLC0415

        await get_automation_store(hass).purge_retired(automation_id)
    return await store.async_remove(trash_id)


async def _restore_automation(hass: HomeAssistant, entry: dict[str, Any]) -> None:
    from pathlib import Path  # noqa: PLC0415

    from .automation_utils import (  # noqa: PLC0415
        AUTOMATIONS_YAML_LOCK,
        _attach_selora_label_to_entity,
        _read_automations_yaml,
        _write_automations_yaml,
        async_forget_deletion,
    )
    from .helpers import get_automation_store, is_selora_automation  # noqa: PLC0415

    automation_id = entry.get("id")
    path = Path(hass.config.config_dir) / "automations.yaml"
    async with AUTOMATIONS_YAML_LOCK:
        existing = await hass.async_add_executor_job(_read_automations_yaml, path)
        # Two automations under one id load as one; two id-less ones under one
        # alias cannot be told apart by the alias-keyed delete.
        if automation_id and any(a.get("id") == automation_id for a in existing):
            raise TrashRestoreError("in_use")
        alias = entry.get("alias")
        if not automation_id and any(not a.get("id") and a.get("alias") == alias for a in existing):
            raise TrashRestoreError("in_use")
        previous = list(existing)
        # Dumped before the write, which mutates the entry it is given.
        yaml_text = yaml.dump(entry, allow_unicode=True, default_flow_style=False)
        await hass.async_add_executor_job(_write_automations_yaml, path, [*existing, dict(entry)])
        try:
            await hass.services.async_call("automation", "reload", blocking=True)
        except Exception as exc:  # noqa: BLE001 — HA service handlers may raise beyond HA's hierarchy
            await hass.async_add_executor_job(_write_automations_yaml, path, previous)
            raise TrashRestoreError("reload_failed", str(exc)) from exc

    # A restored automation is one the user wants: suggesting it is fine again.
    await async_forget_deletion(hass, entry)
    if not isinstance(automation_id, str) or not is_selora_automation(entry):
        return
    await _attach_selora_label_to_entity(hass, automation_id)
    store = get_automation_store(hass)
    revived = await store.revive_record(automation_id)
    await store.add_version(
        automation_id,
        yaml_text,
        entry,
        "Restored from Recently deleted",
        action="restored" if revived else None,
    )


async def _restore_scene(hass: HomeAssistant, entry: dict[str, Any]) -> None:
    from .helpers import get_scene_store  # noqa: PLC0415
    from .scene_utils import (  # noqa: PLC0415
        _SCENES_YAML_LOCK,
        _get_scenes_path,
        _read_scenes_yaml,
        _write_scenes_yaml,
    )

    scene_id = entry.get("id")
    path = _get_scenes_path(hass)
    async with _SCENES_YAML_LOCK:
        existing = await hass.async_add_executor_job(_read_scenes_yaml, path)
        if scene_id and any(isinstance(s, dict) and s.get("id") == scene_id for s in existing):
            raise TrashRestoreError("in_use")
        # An id-less scene is deleted by its name, which must stay unique.
        name = entry.get("name")
        if not scene_id and any(isinstance(s, dict) and s.get("name") == name for s in existing):
            raise TrashRestoreError("in_use")
        previous = list(existing)
        await hass.async_add_executor_job(_write_scenes_yaml, path, [*existing, dict(entry)])
        try:
            await hass.services.async_call("scene", "reload", blocking=True)
        except Exception as exc:  # noqa: BLE001 — HA service handlers may raise beyond HA's hierarchy
            await hass.async_add_executor_job(_write_scenes_yaml, path, previous)
            raise TrashRestoreError("reload_failed", str(exc)) from exc
    # Clears a Selora scene's deleted_at, and imports anyone else's.
    await get_scene_store(hass).async_reconcile_yaml(force=True)
