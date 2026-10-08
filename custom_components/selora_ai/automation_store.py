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
        },
        "retired": {
            # A deleted automation's record, with "retired_at", kept so a
            # restore under its old id picks the history back up. Readers
            # never see it; pruned by age and count.
            "<automation_id>": AutomationRecord,
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

from datetime import UTC, datetime, timedelta
import difflib
import logging
from typing import TYPE_CHECKING, Any
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .automation_changes import CHANGES_FORMAT, compute_version_changes
from .const import (
    AUTOMATION_STORE_KEY,
    MAX_RETIRED_AUTOMATIONS,
    MAX_VERSIONS_PER_AUTOMATION,
    RETIRED_AUTOMATION_DAYS,
)
from .telemetry import record_activity
from .version_summaries import schedule_version_summary

if TYPE_CHECKING:
    from .types import (
        AutomationMetadata,
        AutomationRecord,
        AutomationStoreData,
        AutomationVersion,
        LineageEntry,
    )

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION = 1


def _backfill_changes(data: AutomationStoreData) -> bool:
    """Fill in ``changes`` where it is missing or from an older comparison.

    Runs once per version per ``CHANGES_FORMAT``: the result is saved with
    the format it was computed under. The oldest kept version gets None, since
    its predecessor may have been evicted. Returns whether anything changed.
    """
    filled = False
    for record in data.get("records", {}).values():
        versions = record.get("versions", [])
        for i, version in enumerate(versions):
            if version.get("changes_format") == CHANGES_FORMAT:
                continue
            before = versions[i - 1].get("data") if i else None
            version["changes"] = compute_version_changes(before, version.get("data") or {})
            version["changes_format"] = CHANGES_FORMAT
            filled = True
    return filled


def _prune_retired(data: AutomationStoreData) -> bool:
    """Drop retired records past their age or beyond the count. True if any went."""
    retired = data.get("retired")
    if not retired:
        return False
    cutoff = (datetime.now(UTC) - timedelta(days=RETIRED_AUTOMATION_DAYS)).isoformat()
    newest_first = sorted(
        retired.items(), key=lambda item: item[1].get("retired_at", ""), reverse=True
    )
    keep = {
        automation_id: record
        for automation_id, record in newest_first[:MAX_RETIRED_AUTOMATIONS]
        if record.get("retired_at", "") >= cutoff
    }
    if len(keep) == len(retired):
        return False
    data["retired"] = keep
    return True


class AutomationStore:
    """Version and lifecycle store for Selora-managed automations."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store[AutomationStoreData] = Store(
            hass, version=_STORE_VERSION, key=AUTOMATION_STORE_KEY
        )
        self._data: AutomationStoreData | None = None

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
                backfilled = _backfill_changes(self._data)
                if _prune_retired(self._data) or backfilled:
                    await self._store.async_save(self._data)
            else:
                self._data = {"records": {}, "session_index": {}}
            # Migrate: drop the abandoned "drafts" bucket. Draft rows were
            # never surfaced in the panel, so entries written by earlier
            # versions had no reachable delete path and accumulated here.
            # Popping on load lets the next save persist the cleanup.
            self._data.pop("drafts", None)  # type: ignore[misc]

    async def _get_loaded_data(self) -> AutomationStoreData:
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
        records: dict[str, AutomationRecord] = data_store["records"]
        is_new: bool = automation_id not in records
        previous = records[automation_id]["versions"] if not is_new else []
        version: AutomationVersion = {
            "version_id": version_id,
            "automation_id": automation_id,
            "created_at": now,
            "yaml": yaml_text,
            "data": data,
            "message": message,
            "session_id": session_id,
            "changes": compute_version_changes(
                previous[-1].get("data") if previous else None, data
            ),
            "changes_format": CHANGES_FORMAT,
        }
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
            versions_list = records[automation_id]["versions"]
            versions_list.append(version)
            # Keep only the latest N versions. Older YAML bodies are evicted to
            # cap storage growth on refinement-heavy workflows; the lineage
            # entries below preserve the audit trail.
            if len(versions_list) > MAX_VERSIONS_PER_AUTOMATION:
                del versions_list[: len(versions_list) - MAX_VERSIONS_PER_AUTOMATION]
            records[automation_id]["current_version_id"] = version_id

        # Resolve action label when not explicitly provided
        resolved_action: str = action or (
            "created" if is_new else ("refined" if session_id else "updated")
        )

        # Append lineage entry (always — even for non-session edits)
        lineage_entry: LineageEntry = {
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

        # Anonymous activity telemetry (opt-in, no-op otherwise). The
        # resolved action distinguishes a brand-new automation from a
        # refinement of an existing one.
        if resolved_action == "created":
            record_activity(self._hass, "automations_created")
        elif resolved_action in ("refined", "updated"):
            record_activity(self._hass, "automations_refined")

        # The written summary is an LLM call, so it is fired off rather than
        # awaited: the save has already landed and must not wait on it.
        schedule_version_summary(self._hass, self, automation_id, version_id)

        return version_id

    async def get_record(self, automation_id: str) -> AutomationRecord | None:
        """Return the full record for an automation, or None if not tracked."""
        data_store = await self._get_loaded_data()
        return data_store["records"].get(automation_id)

    async def get_versions(self, automation_id: str) -> list[AutomationVersion]:
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

    async def async_set_version_summary(
        self,
        automation_id: str,
        version_id: str,
        summary: str,
        language: str,
    ) -> bool:
        """Store the written summary of one version. False if it is gone."""
        record = await self.get_record(automation_id)
        version = next(
            (v for v in (record or {}).get("versions", []) if v["version_id"] == version_id),
            None,
        )
        if version is None:
            return False
        version["summary"] = summary
        version["summary_language"] = language
        await self._store.async_save(await self._get_loaded_data())
        return True

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def purge_record(self, automation_id: str) -> bool:
        """Permanently remove one automation record and all versions."""
        data_store = await self._get_loaded_data()
        records = data_store["records"]
        if automation_id not in records:
            return False
        del records[automation_id]
        await self._store.async_save(data_store)
        record_activity(self._hass, "automations_deleted")
        return True

    async def retire_record(self, automation_id: str) -> bool:
        """Take a deleted automation's record out of view, keeping its history.

        ``revive_record`` puts it back when the automation is made again under
        the same id; otherwise it is pruned with the other retired records.
        """
        data_store = await self._get_loaded_data()
        record = data_store["records"].pop(automation_id, None)
        if record is None:
            return False
        record["retired_at"] = datetime.now(UTC).isoformat()
        data_store.setdefault("retired", {})[automation_id] = record
        _prune_retired(data_store)
        await self._store.async_save(data_store)
        record_activity(self._hass, "automations_deleted")
        return True

    async def _unexpired_retired(self) -> dict[str, AutomationRecord]:
        """Retired records, pruned first: a running hub outlives the deadline."""
        data_store = await self._get_loaded_data()
        if _prune_retired(data_store):
            await self._store.async_save(data_store)
        return data_store.get("retired", {})

    async def is_retired(self, automation_id: str) -> bool:
        """Return whether a deleted automation's history is still kept."""
        return automation_id in await self._unexpired_retired()

    async def revive_record(self, automation_id: str) -> bool:
        """Bring a retired record back, unless the id is in use again.

        Not saved here: the caller's next ``add_version`` saves it.
        """
        retired = await self._unexpired_retired()
        data_store = await self._get_loaded_data()
        if automation_id not in retired or automation_id in data_store["records"]:
            return False
        record = retired.pop(automation_id)
        record.pop("retired_at", None)
        data_store["records"][automation_id] = record
        return True

    # ── Metadata helpers ─────────────────────────────────────────────────

    async def get_metadata(self, automation_id: str) -> AutomationMetadata | None:
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

    async def get_automation_lineage(self, automation_id: str) -> list[LineageEntry]:
        """Return the chronological lineage list for an automation."""
        record = await self.get_record(automation_id)
        if not record:
            return []
        return list(record.get("lineage", []))

    async def get_session_automations(self, session_id: str) -> list[str]:
        """Return automation_ids touched by a given session (via reverse index)."""
        data_store = await self._get_loaded_data()
        return list(data_store.get("session_index", {}).get(session_id, []))
