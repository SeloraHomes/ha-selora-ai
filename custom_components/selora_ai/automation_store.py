"""AutomationStore — persists automation versions and lifecycle metadata.

Backed by HA's Store API (same pattern as ConversationStore in __init__.py).
No custom SQLite — the data volume (50 automations × 20 versions) does not
justify schema management overhead.

Data layout in storage:
    {
        "records": {
            "<automation_id>": {
                "automation_id": str,
                "current_version_id": str,
                "versions": [AutomationVersion, ...],
                # Note: "deleted_at" may exist in legacy records but is no longer used.
                "lineage": [LineageEntry, ...],  # ordered chronologically
            }
        },
        "session_index": {
            "<session_id>": [automation_id, ...]  # reverse index
        }
    }

LineageEntry shape:
    {
        "version_id": str,
        "session_id": str | None,
        "message_index": int | None,   # position in session message list
        "action": str,                 # "created" | "updated" | "restored" | "refined"
        "timestamp": str,              # ISO datetime
    }
"""

from __future__ import annotations

from datetime import UTC, datetime
import difflib
import logging
from typing import Any
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import AUTOMATION_STORE_KEY

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION = 1


class AutomationStore:
    """Version and lifecycle store for Selora-managed automations."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store = Store(hass, version=_STORE_VERSION, key=AUTOMATION_STORE_KEY)
        self._data: dict[str, Any] | None = None

    async def _ensure_loaded(self) -> None:
        if self._data is None:
            raw = await self._store.async_load()
            if isinstance(raw, dict):
                self._data = raw
                # Migrate: ensure top-level session_index exists
                if "session_index" not in self._data:
                    self._data["session_index"] = {}
                # Migrate: ensure every record has a lineage list
                for record in self._data.get("records", {}).values():
                    if "lineage" not in record:
                        record["lineage"] = []
            else:
                self._data = {"records": {}, "session_index": {}}
            self._data.setdefault("drafts", {})

    async def _get_loaded_data(self) -> dict[str, Any]:
        await self._ensure_loaded()
        if self._data is None:
            raise RuntimeError("Automation store data failed to load")
        return self._data

    # ── Version management ───────────────────────────────────────────────

    async def add_version(
        self,
        automation_id: str,
        yaml_text: str,
        data: dict[str, Any],
        message: str,
        session_id: str | None = None,
        *,
        action: str | None = None,
        message_index: int | None = None,
    ) -> str:
        """Append a new immutable version record and update current_version_id.

        Returns the new version_id.
        Creates the AutomationRecord if this is the first version.

        A LineageEntry is always appended to track every change.  When
        session_id is provided the entry is also added to the session_index
        so sessions can be reverse-looked-up by automation.
        """
        data_store = await self._get_loaded_data()
        version_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        version: dict[str, Any] = {
            "version_id": version_id,
            "automation_id": automation_id,
            "created_at": now,
            "yaml": yaml_text,
            "data": data,
            "message": message,
            "session_id": session_id,
        }
        records = data_store["records"]
        is_new = automation_id not in records
        if is_new:
            records[automation_id] = {
                "automation_id": automation_id,
                "current_version_id": version_id,
                "versions": [version],
                "lineage": [],
            }
        else:
            # Migrate existing records that pre-date lineage support
            if "lineage" not in records[automation_id]:
                records[automation_id]["lineage"] = []
            records[automation_id]["versions"].append(version)
            records[automation_id]["current_version_id"] = version_id

        # Resolve action label when not explicitly provided
        resolved_action: str = action or (
            "created" if is_new else ("refined" if session_id else "updated")
        )

        # Append lineage entry (always — even for non-session edits)
        lineage_entry: dict[str, Any] = {
            "version_id": version_id,
            "session_id": session_id,
            "message_index": message_index,
            "action": resolved_action,
            "timestamp": now,
        }
        records[automation_id]["lineage"].append(lineage_entry)

        # Maintain session → automations reverse index
        if session_id:
            session_index: dict[str, list[str]] = data_store.setdefault("session_index", {})
            touched = session_index.setdefault(session_id, [])
            if automation_id not in touched:
                touched.append(automation_id)

        await self._store.async_save(data_store)
        return version_id

    async def get_record(self, automation_id: str) -> dict[str, Any] | None:
        """Return the full record for an automation, or None if not tracked."""
        data_store = await self._get_loaded_data()
        return data_store["records"].get(automation_id)

    async def get_versions(self, automation_id: str) -> list[dict[str, Any]]:
        """Return ordered version list for an automation (oldest first)."""
        record = await self.get_record(automation_id)
        return record["versions"] if record else []

    async def get_diff(
        self, automation_id: str, version_id_a: str, version_id_b: str
    ) -> str | None:
        """Return a unified diff between two versions.

        Returns None if either version_id is not found.
        """
        versions = await self.get_versions(automation_id)
        by_id = {v["version_id"]: v for v in versions}
        va = by_id.get(version_id_a)
        vb = by_id.get(version_id_b)
        if not va or not vb:
            return None
        diff = difflib.unified_diff(
            va["yaml"].splitlines(keepends=True),
            vb["yaml"].splitlines(keepends=True),
            fromfile=f"version:{version_id_a[:8]}",
            tofile=f"version:{version_id_b[:8]}",
        )
        return "".join(diff)

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def purge_record(self, automation_id: str) -> bool:
        """Permanently remove one automation record and all versions."""
        data_store = await self._get_loaded_data()
        records = data_store["records"]
        if automation_id not in records:
            return False
        del records[automation_id]
        await self._store.async_save(data_store)
        return True

    # ── Metadata helpers ─────────────────────────────────────────────────

    async def get_metadata(self, automation_id: str) -> dict[str, Any] | None:
        """Return lightweight metadata (no version YAML). None if not tracked."""
        record = await self.get_record(automation_id)
        if not record:
            return None
        return {
            "automation_id": automation_id,
            "version_count": len(record["versions"]),
            "current_version_id": record["current_version_id"],
        }

    # ── Lineage ──────────────────────────────────────────────────────────

    async def get_automation_lineage(self, automation_id: str) -> list[dict[str, Any]]:
        """Return the chronological lineage list for an automation."""
        record = await self.get_record(automation_id)
        if not record:
            return []
        return list(record.get("lineage", []))

    async def get_session_automations(self, session_id: str) -> list[str]:
        """Return automation_ids touched by a given session (via reverse index)."""
        data_store = await self._get_loaded_data()
        return list(data_store.get("session_index", {}).get(session_id, []))

    # ── Draft automations ─────────────────────────────────────────────────

    async def create_draft(self, alias: str, session_id: str) -> dict[str, Any]:
        """Create a draft automation linked to a chat session."""
        data_store = await self._get_loaded_data()
        draft_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        draft = {
            "draft_id": draft_id,
            "alias": alias,
            "session_id": session_id,
            "created_at": now,
        }
        data_store.setdefault("drafts", {})[draft_id] = draft
        await self._store.async_save(data_store)
        return draft

    async def list_drafts(self) -> list[dict[str, Any]]:
        """Return all draft automations."""
        data_store = await self._get_loaded_data()
        return list(data_store.get("drafts", {}).values())

    async def remove_draft(self, draft_id: str) -> bool:
        """Remove a draft (e.g. after the automation is created)."""
        data_store = await self._get_loaded_data()
        drafts = data_store.get("drafts", {})
        if draft_id not in drafts:
            return False
        del drafts[draft_id]
        await self._store.async_save(data_store)
        return True
