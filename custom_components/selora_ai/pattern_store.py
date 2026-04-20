"""PatternStore — persists state history, detected patterns, and proactive suggestions.

Backed by HA's Store API (same pattern as AutomationStore).

Data layout:
    {
        "state_history": {
            "<entity_id>": [
                {"state": "on", "prev": "off", "ts": "ISO-8601"},
                ...  # ring buffer, max PATTERN_HISTORY_MAX_PER_ENTITY per entity
            ]
        },
        "patterns": {
            "<pattern_id>": {
                "pattern_id": str,
                "type": "time_based" | "correlation" | "sequence",
                "confidence": float,
                "entity_ids": [str],
                "description": str,
                "evidence": dict,
                "detected_at": str,
                "last_seen": str,
                "occurrence_count": int,
                "status": "active" | "dismissed" | "snoozed" | "accepted",
                "snooze_until": str | None,
            }
        },
        "suggestions": {
            "<suggestion_id>": {
                "suggestion_id": str,
                "pattern_id": str,
                "source": "pattern" | "hybrid",
                "confidence": float,
                "automation_data": dict,
                "automation_yaml": str,
                "description": str,
                "evidence_summary": str,
                "created_at": str,
                "status": "pending" | "accepted" | "dismissed" | "snoozed",
                "snooze_until": str | None,
            }
        },
        "deleted_hashes": {
            "<content_hash>": {
                "hash": str,
                "alias": str,
                "deleted_at": str,
            }
        },
        "meta": {
            "last_history_collection": str,
            "last_pattern_scan": str,
        }
    }
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .types import (
        AnalyticsSummary,
        EntityActivity,
        FeedbackSummary,
        HistorySummary,
        PatternDict,
        PatternStoreData,
        StateChange,
        StateTransitionCount,
        SuggestionDict,
        TopState,
        UsageWindow,
    )

from .const import (
    DISMISSAL_SUPPRESSION_WINDOW_DAYS,
    DOMAIN,
    PATTERN_HISTORY_MAX_PER_ENTITY,
    PATTERN_HISTORY_RETENTION_DAYS,
    PATTERN_MAX_DELETED_HASHES,
    PATTERN_MAX_PATTERNS,
    PATTERN_MAX_SUGGESTIONS,
    PATTERN_STORE_KEY,
)

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION = 1


class PatternStore:
    """Persistent store for state history, patterns, and proactive suggestions."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(
            hass, version=_STORE_VERSION, key=PATTERN_STORE_KEY
        )
        self._data: PatternStoreData | None = None
        self._pending_state_changes: int = 0

    async def _ensure_loaded(self) -> None:
        if self._data is not None:
            return
        raw = await self._store.async_load()
        if isinstance(raw, dict):
            self._data = raw
            self._data.setdefault("state_history", {})
            self._data.setdefault("patterns", {})
            self._data.setdefault("suggestions", {})
            self._data.setdefault("deleted_hashes", {})
            self._data.setdefault("meta", {})
        else:
            self._data = {
                "state_history": {},
                "patterns": {},
                "suggestions": {},
                "deleted_hashes": {},
                "meta": {},
            }

        # Migration: ensure all suggestions have dismissal fields (added in v2)
        for s in self._data["suggestions"].values():
            s.setdefault("dismissed_at", None)
            s.setdefault("dismissal_reason", None)

    async def _get_loaded_data(self) -> PatternStoreData:
        await self._ensure_loaded()
        if self._data is None:
            raise RuntimeError("Pattern store data failed to load")
        return self._data

    async def _save(self) -> None:
        if self._data is not None:
            await self._store.async_save(self._data)

    @staticmethod
    def _evict_oldest(items: dict[str, Any], max_size: int, time_key: str = "detected_at") -> None:
        """Remove the oldest entries in-place when the dict exceeds max_size."""
        if len(items) <= max_size:
            return
        sorted_keys = sorted(items, key=lambda k: items[k].get(time_key, ""))
        for key in sorted_keys[: len(items) - max_size]:
            del items[key]

    # ── State History ────────────────────────────────────────────────────

    async def record_state_change(
        self,
        entity_id: str,
        new_state: str,
        old_state: str,
        timestamp: str,
    ) -> None:
        """Append a state change to the ring buffer for this entity."""
        data = await self._get_loaded_data()
        history = data["state_history"]
        entries = history.setdefault(entity_id, [])
        entries.append({"state": new_state, "prev": old_state, "ts": timestamp})

        # Enforce ring buffer limit
        if len(entries) > PATTERN_HISTORY_MAX_PER_ENTITY:
            history[entity_id] = entries[-PATTERN_HISTORY_MAX_PER_ENTITY:]

        # Batch save: defer to periodic flush to avoid excessive I/O.
        # The caller (state listener) calls this on every state change,
        # so we only persist every 50 events or when explicitly flushed.
        self._pending_state_changes += 1
        if self._pending_state_changes >= 50:
            await self._save()
            self._pending_state_changes = 0

    async def flush(self) -> None:
        """Force-persist any buffered state history to disk."""
        await self._save()
        self._pending_state_changes = 0

    async def get_entity_history(
        self, entity_id: str, since: datetime | None = None
    ) -> list[StateChange]:
        """Return state history for a single entity, optionally filtered by time."""
        data = await self._get_loaded_data()
        entries = data["state_history"].get(entity_id, [])
        if since is None:
            return list(entries)
        cutoff = since.isoformat()
        return [e for e in entries if e["ts"] >= cutoff]

    async def get_all_history(self, since: datetime | None = None) -> dict[str, list[StateChange]]:
        """Return state history for all entities."""
        data = await self._get_loaded_data()
        history = data["state_history"]
        if since is None:
            return {k: list(v) for k, v in history.items()}
        cutoff = since.isoformat()
        return {
            k: [e for e in v if e["ts"] >= cutoff]
            for k, v in history.items()
            if any(e["ts"] >= cutoff for e in v)
        }

    async def prune_old_history(self, older_than_days: int = PATTERN_HISTORY_RETENTION_DAYS) -> int:
        """Remove state history entries older than the retention window.

        Returns the number of entries removed.
        """
        data = await self._get_loaded_data()
        cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
        removed = 0
        history = data["state_history"]
        empty_keys: list[str] = []
        for entity_id, entries in history.items():
            before = len(entries)
            history[entity_id] = [e for e in entries if e["ts"] >= cutoff]
            removed += before - len(history[entity_id])
            if not history[entity_id]:
                empty_keys.append(entity_id)
        for key in empty_keys:
            del history[key]
        if removed > 0:
            await self._save()
            _LOGGER.info(
                "Pruned %d state history entries older than %d days",
                removed,
                older_than_days,
            )
        return removed

    async def get_history_summary(self) -> list[HistorySummary]:
        data = await self._get_loaded_data()
        summaries: list[HistorySummary] = []

        for entity_id, changes in data["state_history"].items():
            if not changes:
                continue

            dates: set[str] = set()
            state_counts: dict[str, int] = {}
            for change in changes:
                ts = change.get("ts", "")
                if ts:
                    dates.add(ts[:10])
                state = change.get("state", "")
                if state and state not in ("unavailable", "unknown"):
                    state_counts[state] = state_counts.get(state, 0) + 1

            top_states = sorted(state_counts.items(), key=lambda x: x[1], reverse=True)[:5]

            timestamps = [c["ts"] for c in changes if c.get("ts")]
            top_state_list: list[TopState] = [{"state": s, "count": c} for s, c in top_states]
            summaries.append(
                {
                    "entity_id": entity_id,
                    "change_count": len(changes),
                    "active_days": len(dates),
                    "first_seen": min(timestamps) if timestamps else None,
                    "last_seen": max(timestamps) if timestamps else None,
                    "top_states": top_state_list,
                }
            )

        summaries.sort(key=lambda x: x["change_count"], reverse=True)
        return summaries

    # ── Analytics Queries ─────────────────────────────────────────────────

    def _local_hour(self, ts: str) -> int | None:
        """Parse an ISO-8601 timestamp and return the hour in HA's local timezone."""
        try:
            dt = datetime.fromisoformat(ts)
            local_dt = dt_util.as_local(dt)
            return local_dt.hour
        except (ValueError, TypeError):
            return None

    def _rank_entities(
        self,
        history: dict[str, list[StateChange]],
        limit: int,
    ) -> list[EntityActivity]:
        """Rank entities by state change count (sync, no I/O)."""
        results: list[EntityActivity] = []
        for entity_id, changes in history.items():
            if not changes:
                continue
            dates: set[str] = set()
            for change in changes:
                ts = change.get("ts", "")
                if ts:
                    dates.add(ts[:10])
            results.append(
                {
                    "entity_id": entity_id,
                    "change_count": len(changes),
                    "active_days": len(dates),
                    "domain": entity_id.split(".")[0] if "." in entity_id else "",
                }
            )
        results.sort(key=lambda x: x["change_count"], reverse=True)
        return results[:limit]

    async def get_most_active_entities(self, limit: int = 10) -> list[EntityActivity]:
        """Return the top N entities by state change count.

        Each entry includes entity_id, change_count, active_days, and domain.
        Sorted by change_count descending.
        """
        data = await self._get_loaded_data()
        return self._rank_entities(data["state_history"], limit)

    async def get_usage_windows(self, entity_id: str) -> list[UsageWindow]:
        """Group an entity's state changes by hour-of-day.

        Returns a list of hourly buckets with count and primary (most common) state.
        Shows when the entity is most active.
        """
        data = await self._get_loaded_data()
        entries = data["state_history"].get(entity_id, [])

        hour_counts: dict[int, int] = {}
        hour_states: dict[int, dict[str, int]] = {}

        for entry in entries:
            ts = entry.get("ts", "")
            state = entry.get("state", "")
            if not ts:
                continue
            hour = self._local_hour(ts)
            if hour is None:
                continue

            hour_counts[hour] = hour_counts.get(hour, 0) + 1
            if state and state not in ("unavailable", "unknown"):
                states = hour_states.setdefault(hour, {})
                states[state] = states.get(state, 0) + 1

        results: list[UsageWindow] = []
        for hour in sorted(hour_counts):
            states = hour_states.get(hour, {})
            primary_state = ""
            if states:
                primary_state = max(states, key=lambda s: states[s])
            results.append(
                {
                    "hour": hour,
                    "count": hour_counts[hour],
                    "primary_state": primary_state,
                }
            )

        return results

    async def get_state_transition_counts(self, entity_id: str) -> list[StateTransitionCount]:
        """Count distinct state transitions for an entity.

        Returns a list of from/to/count dicts sorted by count descending.
        Example: [{"from": "off", "to": "on", "count": 45}, ...]
        """
        data = await self._get_loaded_data()
        entries = data["state_history"].get(entity_id, [])

        transition_counts: dict[tuple[str, str], int] = {}
        for entry in entries:
            prev = entry.get("prev", "")
            state = entry.get("state", "")
            if prev and state and prev != state:
                key = (prev, state)
                transition_counts[key] = transition_counts.get(key, 0) + 1

        results: list[StateTransitionCount] = [
            {"from": k[0], "to": k[1], "count": v} for k, v in transition_counts.items()
        ]
        results.sort(key=lambda x: x["count"], reverse=True)
        return results

    async def get_analytics_summary(self) -> AnalyticsSummary:
        """Return a high-level home analytics summary.

        Includes total entities tracked, total state changes, top 5 most
        active entities, busiest hour of day, and earliest tracking timestamp.
        """
        data = await self._get_loaded_data()
        history = data["state_history"]

        total_entities = 0
        total_changes = 0
        earliest_ts: str | None = None
        hour_totals: dict[int, int] = {}

        for entries in history.values():
            if not entries:
                continue
            total_entities += 1
            total_changes += len(entries)

            for entry in entries:
                ts = entry.get("ts", "")
                if ts:
                    if earliest_ts is None or ts < earliest_ts:
                        earliest_ts = ts
                    hour = self._local_hour(ts)
                    if hour is not None:
                        hour_totals[hour] = hour_totals.get(hour, 0) + 1

        busiest_hour: int | None = None
        if hour_totals:
            busiest_hour = max(hour_totals, key=lambda h: hour_totals[h])

        most_active = self._rank_entities(history, limit=5)

        return {
            "total_entities_tracked": total_entities,
            "total_state_changes": total_changes,
            "most_active": most_active,
            "busiest_hour": busiest_hour,
            "tracking_since": earliest_ts,
        }

    async def get_pattern_detail(self, pattern_id: str) -> dict[str, Any] | None:
        data = await self._get_loaded_data()
        pattern = data["patterns"].get(pattern_id)
        if not pattern:
            return None

        entity_history: dict[str, list[StateChange]] = {}
        for eid in pattern.get("entity_ids", []):
            history = data["state_history"].get(eid, [])
            entity_history[eid] = history[-20:]

        return {
            **pattern,
            "entity_history": entity_history,
        }

    async def backfill_from_recorder(self, hass: HomeAssistant, lookback_days: int = 7) -> int:
        """One-time import of recent state history from HA Recorder.

        Only runs when state_history is empty (first start).
        Returns the number of entries imported.
        """
        data = await self._get_loaded_data()
        if data["state_history"]:
            return 0  # Already have data

        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import (
                get_significant_states,
            )
        except ImportError:
            _LOGGER.warning("Recorder not available for backfill")
            return 0

        from .const import COLLECTOR_DOMAINS
        from .entity_filter import EntityFilter

        now = datetime.now(UTC)
        start = now - timedelta(days=lookback_days)

        all_states = hass.states.async_all()
        ef = EntityFilter(hass, [s.entity_id for s in all_states])
        entity_ids = [
            s.entity_id
            for s in all_states
            if s.entity_id.split(".")[0] in COLLECTOR_DOMAINS and ef.is_active(s.entity_id)
        ]
        if not entity_ids:
            return 0

        try:
            states = await get_instance(hass).async_add_executor_job(
                get_significant_states,
                hass,
                start,
                now,
                entity_ids,
            )
        except Exception:
            _LOGGER.exception("Failed to backfill from recorder")
            return 0

        count = 0
        history = data["state_history"]
        for entity_id, entity_states in states.items():
            entries: list[StateChange] = []
            prev_state = ""
            for state in entity_states:
                if state.state == prev_state:
                    continue
                entries.append(
                    {
                        "state": state.state,
                        "prev": prev_state,
                        "ts": state.last_changed.isoformat(),
                    }
                )
                prev_state = state.state
            if entries:
                history[entity_id] = entries[-PATTERN_HISTORY_MAX_PER_ENTITY:]
                count += len(history[entity_id])

        if count:
            data["meta"]["last_history_collection"] = now.isoformat()
            await self._save()
            _LOGGER.info("Backfilled %d state history entries from recorder", count)
        return count

    # ── Patterns ─────────────────────────────────────────────────────────

    async def save_pattern(self, pattern: PatternDict) -> str:
        data = await self._get_loaded_data()
        now = datetime.now(UTC).isoformat()

        pattern_id = pattern.get("pattern_id") or str(uuid.uuid4())
        existing = data["patterns"].get(pattern_id)

        if existing:
            existing["confidence"] = pattern["confidence"]
            existing["last_seen"] = now
            existing["occurrence_count"] = existing.get("occurrence_count", 0) + 1
            existing["evidence"] = pattern.get("evidence", existing["evidence"])
            # Reactivate patterns that were previously rejected but now pass
            if existing["status"] == "rejected":
                existing["status"] = "active"
        else:
            data["patterns"][pattern_id] = {
                "pattern_id": pattern_id,
                "type": pattern["type"],
                "confidence": pattern["confidence"],
                "entity_ids": pattern["entity_ids"],
                "description": pattern["description"],
                "evidence": pattern.get("evidence", {}),
                "detected_at": now,
                "last_seen": now,
                "occurrence_count": 1,
                "status": "active",
                "snooze_until": None,
            }

        self._evict_oldest(data["patterns"], PATTERN_MAX_PATTERNS)
        await self._save()
        return pattern_id

    async def get_patterns(
        self,
        status: str | None = None,
        pattern_type: str | None = None,
    ) -> list[PatternDict]:
        data = await self._get_loaded_data()
        now = datetime.now(UTC).isoformat()
        results: list[PatternDict] = []
        did_unsnooze = False

        for p in data["patterns"].values():
            if status and p["status"] != status:
                # Un-snooze patterns whose snooze window has passed
                if p["status"] == "snoozed" and p.get("snooze_until"):
                    if p["snooze_until"] <= now:
                        p["status"] = "active"
                        did_unsnooze = True
                    elif status == "active":
                        continue
                else:
                    continue
            if pattern_type and p["type"] != pattern_type:
                continue
            results.append(p)

        if did_unsnooze:
            await self._save()

        return results

    async def update_pattern_status(
        self,
        pattern_id: str,
        status: str,
        snooze_until: str | None = None,
    ) -> bool:
        """Update a pattern's status."""
        data = await self._get_loaded_data()
        pattern = data["patterns"].get(pattern_id)
        if not pattern:
            return False
        pattern["status"] = status
        pattern["snooze_until"] = snooze_until
        await self._save()
        return True

    async def find_pattern_by_signature(
        self,
        pattern_type: str,
        entity_ids: list[str],
        evidence_key: str,
    ) -> PatternDict | None:
        data = await self._get_loaded_data()
        entity_set = set(entity_ids)
        for p in data["patterns"].values():
            if p["type"] != pattern_type:
                continue
            if set(p["entity_ids"]) != entity_set:
                continue
            # Match on a distinguishing evidence field
            if p.get("evidence", {}).get("_signature") == evidence_key:
                return p
        return None

    # ── Suggestions ──────────────────────────────────────────────────────

    async def save_suggestion(self, suggestion: SuggestionDict) -> str:
        data = await self._get_loaded_data()
        suggestion_id = suggestion.get("suggestion_id") or str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()

        data["suggestions"][suggestion_id] = {
            "suggestion_id": suggestion_id,
            "pattern_id": suggestion.get("pattern_id", ""),
            "source": suggestion.get("source", "pattern"),
            "confidence": suggestion.get("confidence", 0.0),
            "automation_data": suggestion.get("automation_data", {}),
            "automation_yaml": suggestion.get("automation_yaml", ""),
            "description": suggestion.get("description", ""),
            "evidence_summary": suggestion.get("evidence_summary", ""),
            "created_at": now,
            "status": "pending",
            "snooze_until": None,
            "dismissed_at": None,
            "dismissal_reason": None,
        }

        self._evict_oldest(data["suggestions"], PATTERN_MAX_SUGGESTIONS, time_key="created_at")
        await self._save()
        return suggestion_id

    async def get_suggestions(self, status: str | None = None) -> list[SuggestionDict]:
        data = await self._get_loaded_data()
        now = datetime.now(UTC).isoformat()
        results: list[SuggestionDict] = []
        did_unsnooze = False

        for s in data["suggestions"].values():
            # Un-snooze expired suggestions
            if s["status"] == "snoozed" and s.get("snooze_until") and s["snooze_until"] <= now:
                s["status"] = "pending"
                did_unsnooze = True

            if status and s["status"] != status:
                continue
            results.append(s)

        if did_unsnooze:
            await self._save()

        return results

    async def update_suggestion_status(
        self,
        suggestion_id: str,
        status: str,
        snooze_until: str | None = None,
        dismissed_at: str | None = None,
        dismissal_reason: str | None = None,
    ) -> bool:
        """Update a suggestion's status, including optional dismissal metadata."""
        data = await self._get_loaded_data()
        suggestion = data["suggestions"].get(suggestion_id)
        if not suggestion:
            return False
        suggestion["status"] = status
        suggestion["snooze_until"] = snooze_until
        if dismissed_at is not None:
            suggestion["dismissed_at"] = dismissed_at
        if dismissal_reason is not None:
            suggestion["dismissal_reason"] = dismissal_reason
        await self._save()
        return True

    async def remove_suggestions_for_pattern(self, pattern_id: str) -> int:
        """Remove pending suggestions linked to a rejected pattern.

        Only targets ``pending`` suggestions — snoozed suggestions are
        preserved so the user's snooze deadline is honored.
        Deletes rather than dismissing so the removal doesn't pollute the
        recently-dismissed list used by the collector for alias suppression.
        """
        data = await self._get_loaded_data()
        to_remove = [
            sid
            for sid, s in data["suggestions"].items()
            if s.get("pattern_id") == pattern_id and s["status"] == "pending"
        ]
        for sid in to_remove:
            del data["suggestions"][sid]
        if to_remove:
            await self._save()
        return len(to_remove)

    async def get_recently_dismissed_suggestions(
        self, window_days: int = DISMISSAL_SUPPRESSION_WINDOW_DAYS
    ) -> list[SuggestionDict]:
        """Return suggestions dismissed within the suppression window.

        Used by SuggestionGenerator to avoid re-surfacing recently rejected
        patterns and to pass dismissal context to the LLM, and by the
        collector to suppress LLM-generated automations by alias/hash.
        """
        data = await self._get_loaded_data()
        cutoff = (datetime.now(UTC) - timedelta(days=window_days)).isoformat()
        return [
            s
            for s in data["suggestions"].values()
            if s.get("status") == "dismissed"
            and s.get("dismissed_at")
            and s["dismissed_at"] >= cutoff
        ]

    async def get_suggestion(self, suggestion_id: str) -> SuggestionDict | None:
        data = await self._get_loaded_data()
        return data["suggestions"].get(suggestion_id)

    async def has_suggestion_for_pattern(self, pattern_id: str) -> bool:
        """Check if a non-dismissed suggestion already exists for a pattern."""
        data = await self._get_loaded_data()
        for s in data["suggestions"].values():
            if s["pattern_id"] == pattern_id and s["status"] in ("pending", "snoozed"):
                return True
        return False

    # ── Deleted automation hashes ───────────────────────────────────────

    async def record_deleted_automation(self, content_hash: str, alias: str) -> None:
        """Record a deleted automation's content hash so it is never re-suggested."""
        data = await self._get_loaded_data()
        data["deleted_hashes"][content_hash] = {
            "hash": content_hash,
            "alias": alias,
            "deleted_at": datetime.now(UTC).isoformat(),
        }
        self._evict_oldest(
            data["deleted_hashes"], PATTERN_MAX_DELETED_HASHES, time_key="deleted_at"
        )
        await self._save()

    async def get_deleted_hashes(self) -> set[str]:
        """Return all content hashes of previously deleted automations."""
        data = await self._get_loaded_data()
        return set(data["deleted_hashes"].keys())

    async def get_feedback_summary(self, *, limit: int = 8) -> FeedbackSummary:
        _FIELDS_ACCEPTED = ("description", "created_at")
        _FIELDS_DECLINED = ("description", "created_at", "dismissed_at", "dismissal_reason")

        data = await self._get_loaded_data()
        accepted: list[dict[str, str]] = []
        declined: list[dict[str, str]] = []

        for s in data["suggestions"].values():
            status = s.get("status")
            if status == "accepted":
                accepted.append({k: s[k] for k in _FIELDS_ACCEPTED if k in s})
            elif status == "dismissed":
                declined.append({k: s[k] for k in _FIELDS_DECLINED if k in s})

        # Sort most-recent first, then trim
        accepted.sort(key=lambda s: s.get("created_at", ""), reverse=True)
        declined.sort(
            key=lambda s: s.get("dismissed_at", s.get("created_at", "")),
            reverse=True,
        )

        return {
            "accepted": accepted[:limit],
            "declined": declined[:limit],
        }


# ── Module-level helper ─────────────────────────────────────────────────


def get_pattern_store(hass: HomeAssistant) -> PatternStore | None:
    """Find the PatternStore from any active config entry."""
    domain_data = hass.data.get(DOMAIN, {})
    for key, val in domain_data.items():
        if key.startswith("_") or not isinstance(val, dict):
            continue
        store = val.get("pattern_store")
        if store is not None:
            return store
    return None
