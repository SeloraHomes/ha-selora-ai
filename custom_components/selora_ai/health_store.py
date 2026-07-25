"""HealthStore — persists Layer 1 health signals + export bookkeeping.

Backed by HA's Store API (same pattern as PatternStore). Signals are
deduplicated by ``signal_id`` (derived from ``kind`` + ``target``) so a
device that keeps flapping upserts one record and bumps ``count`` rather than
flooding the store.

Data layout::

    {
        "signals": {
            "<signal_id>": HealthSignal,
        },
        "meta": {
            "export_sequence": int,   # monotonic, restart-proof
            "last_scan": str,         # ISO-8601
        },
    }
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    DOMAIN,
    HEALTH_MAX_SIGNALS,
    HEALTH_SIGNAL_RETENTION_DAYS,
    HEALTH_STORE_KEY,
)

if TYPE_CHECKING:
    from .types import HealthSignal, HealthStoreData

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION = 1

_STATUS_ACTIVE = "active"
_STATUS_RESOLVED = "resolved"


def health_signal_id(kind: str, target: str) -> str:
    """Stable id for a (kind, target) pair so re-detection upserts one record."""
    return f"{kind}:{target}"


# Audit fields that move on every run even when nothing about the home changed.
# Excluded when deciding whether an audit is worth a disk write.
_AUDIT_VOLATILE_KEYS = frozenset({"generated_at"})

# How stale the PERSISTED audit timestamp may get while content is unchanged.
# The Home Health sensor surfaces `generated_at` as its `last_scan` attribute and
# reads it from the store, so skipping every unchanged write outright would make
# that read back an arbitrarily old time after a restart. Checkpointing bounds
# the inaccuracy while keeping most of the write reduction (at the default 15-min
# scan cadence: ~24 writes/day instead of ~96).
_AUDIT_TIMESTAMP_CHECKPOINT = timedelta(hours=1)


def _audit_material(audit: dict[str, Any]) -> dict[str, Any]:
    """The part of an audit record that represents real state, for change detection."""
    return {k: v for k, v in audit.items() if k not in _AUDIT_VOLATILE_KEYS}


def _parse_iso(value: object) -> datetime | None:
    """Parse an ISO-8601 string, or None when it isn't one."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _checkpoint_due(baseline: datetime | None, current: datetime | None) -> bool:
    """Whether a timestamp-only write is due. Errs toward writing when unsure —
    an unparseable pair, mismatched awareness, or a backwards clock must not
    silently freeze the persisted timestamp forever."""
    if baseline is None or current is None:
        return True
    if (baseline.tzinfo is None) != (current.tzinfo is None):
        return True
    elapsed = current - baseline
    if elapsed < timedelta(0):
        # The persisted stamp is in the FUTURE of the current audit — an NTP
        # correction after a fast-clock boot, or a backup written under one.
        # Waiting for the window to elapse would pin a future `last_scan` across
        # restarts until wall time caught up, so write now to correct it.
        return True
    return elapsed >= _AUDIT_TIMESTAMP_CHECKPOINT


class HealthStore:
    """Persistent store for health signals and export sequence bookkeeping."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(
            hass, version=_STORE_VERSION, key=HEALTH_STORE_KEY
        )
        self._data: HealthStoreData | None = None
        # `generated_at` of the audit record actually written to disk, so an
        # unchanged-content run can tell how stale the persisted copy has become.
        self._audit_persisted_ts: datetime | None = None

    async def _ensure_loaded(self) -> None:
        if self._data is not None:
            return
        raw = await self._store.async_load()
        if isinstance(raw, dict):
            self._data = raw  # type: ignore[assignment]
            self._data.setdefault("signals", {})
            self._data.setdefault("meta", {})
        else:
            self._data = {"signals": {}, "meta": {}}
        # Seed the checkpoint baseline from what is actually on disk. Deriving it
        # from the in-memory record instead would slide forward on every skipped
        # write, so the staleness window could never elapse after a restart.
        loaded_audit = self._data["meta"].get("last_audit")
        if isinstance(loaded_audit, dict):
            self._audit_persisted_ts = _parse_iso(loaded_audit.get("generated_at"))

    async def _get_loaded_data(self) -> HealthStoreData:
        await self._ensure_loaded()
        if self._data is None:
            raise RuntimeError("Health store data failed to load")
        return self._data

    async def _save(self) -> None:
        if self._data is not None:
            self._enforce_cap(self._data["signals"])
            await self._store.async_save(self._data)
            # Any write persists the whole document, so it flushes whatever
            # `last_audit` currently holds — including writes driven by other
            # callers (set_last_scan, record_signal). Tracking the baseline here
            # rather than at the audit call site keeps it truthful about disk
            # state and avoids a redundant checkpoint write.
            saved_audit = self._data["meta"].get("last_audit")
            if isinstance(saved_audit, dict):
                self._audit_persisted_ts = _parse_iso(saved_audit.get("generated_at"))

    @staticmethod
    def _enforce_cap(signals: dict[str, HealthSignal]) -> int:
        """Bound the stored signal count. Drop resolved-oldest first, then
        active-oldest, keyed by ``last_seen``. Returns the count dropped.
        """
        if len(signals) <= HEALTH_MAX_SIGNALS:
            return 0
        # Rank drop candidates: resolved before active, oldest last_seen first.
        ranked = sorted(
            signals.items(),
            key=lambda kv: (
                0 if kv[1].get("status") == _STATUS_RESOLVED else 1,
                kv[1].get("last_seen", ""),
            ),
        )
        drop = len(signals) - HEALTH_MAX_SIGNALS
        for sid, _sig in ranked[:drop]:
            del signals[sid]
        if drop:
            _LOGGER.info(
                "Health store: dropped %d signals to stay under the %d cap",
                drop,
                HEALTH_MAX_SIGNALS,
            )
        return drop

    # ── Signals ──────────────────────────────────────────────────────────

    async def flush(self) -> None:
        """Persist pending mutations (used to batch a scan's writes into one)."""
        await self._save()

    async def record_signal(
        self,
        *,
        kind: str,
        target: str,
        target_kind: str,
        severity: str,
        evidence: dict[str, Any],
        area_name: str = "",
        device_id: str | None = None,
        save: bool = True,
    ) -> str:
        """Upsert a signal for (kind, target).

        New target/kind -> create. Already active -> refresh (bump count,
        update last_seen/severity/evidence, first_seen preserved so the current
        episode's duration survives an HA restart). Previously resolved ->
        reactivate as a NEW episode: first_seen is reset to now so the duration
        anchors to this outage, not the prior one; ``count`` keeps accumulating
        across episodes as the cross-episode lifetime metric.
        """
        data = await self._get_loaded_data()
        now = datetime.now(UTC).isoformat()
        sid = health_signal_id(kind, target)
        existing = data["signals"].get(sid)

        if existing is not None:
            reactivating = existing.get("status") == _STATUS_RESOLVED
            existing["last_seen"] = now
            existing["count"] = existing.get("count", 0) + 1
            existing["severity"] = severity
            existing["evidence"] = evidence
            if area_name:
                existing["area_name"] = area_name
            existing["device_id"] = device_id
            existing["status"] = _STATUS_ACTIVE
            if reactivating:
                existing["first_seen"] = now
                # A new episode is a distinct problem. Drop any stale user
                # status override (dismiss/acknowledge/resolve) carried over
                # from the prior episode — the insight_id (signal:<signal_id>)
                # is stable across episodes, so without this, dismissing one
                # outage would permanently hide every future outage of the
                # same entity.
                overrides = data["meta"].get("insight_status")
                if isinstance(overrides, dict):
                    overrides.pop(f"signal:{sid}", None)
        else:
            data["signals"][sid] = {
                "signal_id": sid,
                "kind": kind,
                "severity": severity,
                "target": target,
                "target_kind": target_kind,
                "device_id": device_id,
                "area_name": area_name,
                "evidence": evidence,
                "first_seen": now,
                "last_seen": now,
                "count": 1,
                "status": _STATUS_ACTIVE,
            }

        if save:
            await self._save()
        return sid

    async def resolve_signal(self, kind: str, target: str, *, save: bool = True) -> bool:
        """Mark the (kind, target) signal resolved (its condition cleared)."""
        data = await self._get_loaded_data()
        sig = data["signals"].get(health_signal_id(kind, target))
        if sig is None or sig.get("status") == _STATUS_RESOLVED:
            return False
        sig["status"] = _STATUS_RESOLVED
        sig["last_seen"] = datetime.now(UTC).isoformat()
        if save:
            await self._save()
        return True

    async def get_signals(
        self, status: str | None = None, kind: str | None = None
    ) -> list[HealthSignal]:
        # Return deep copies, not references to the store's live dicts: callers
        # (exporter, sensor, websocket, audit) hold these across awaits, and a
        # concurrent scan — same default cadence — resolves/updates the same
        # dicts. Snapshotting keeps each caller's view internally consistent
        # (e.g. the export's active-only list can't gain a since-resolved
        # signal or mix data from two scans mid-serialization).
        data = await self._get_loaded_data()
        out: list[HealthSignal] = []
        for sig in data["signals"].values():
            if status is not None and sig.get("status") != status:
                continue
            if kind is not None and sig.get("kind") != kind:
                continue
            out.append(deepcopy(sig))
        out.sort(key=lambda s: s.get("last_seen", ""), reverse=True)
        return out

    async def get_active_signals(self) -> list[HealthSignal]:
        return await self.get_signals(status=_STATUS_ACTIVE)

    async def prune_resolved(self, older_than_days: int = HEALTH_SIGNAL_RETENTION_DAYS) -> int:
        """Drop resolved signals whose last_seen is beyond the retention window."""
        data = await self._get_loaded_data()
        cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
        stale = [
            sid
            for sid, sig in data["signals"].items()
            if sig.get("status") == _STATUS_RESOLVED and sig.get("last_seen", "") < cutoff
        ]
        for sid in stale:
            del data["signals"][sid]
        # Drop insight-status overrides (ack/dismiss) whose signal no longer
        # exists, so the map can't grow without bound as signals come and go.
        # Signal-insight overrides are keyed ``signal:<signal_id>`` (see
        # InsightsEngine); leave any other override (e.g. suggestions) untouched.
        overrides = data["meta"].get("insight_status")
        orphaned: list[str] = []
        if overrides:
            orphaned = [
                key
                for key in overrides
                if key.startswith("signal:") and key[len("signal:") :] not in data["signals"]
            ]
            for key in orphaned:
                del overrides[key]
        if stale or orphaned:
            await self._save()
            _LOGGER.info(
                "Pruned %d resolved health signals, %d orphaned overrides",
                len(stale),
                len(orphaned),
            )
        return len(stale)

    # ── Export sequence bookkeeping ──────────────────────────────────────

    async def next_export_sequence(self, epoch_seconds: int) -> int:
        """Return the next monotonic export sequence and persist it.

        ``max(persisted + 1, epoch_seconds)`` is restart-proof (epoch is far
        ahead of any prior in-memory counter after a restart) AND monotonic
        under an NTP step backward (the ``persisted + 1`` floor). See
        project-insights-export memory rule #1.
        """
        data = await self._get_loaded_data()
        last = int(data["meta"].get("export_sequence", 0))
        seq = max(last + 1, epoch_seconds)
        data["meta"]["export_sequence"] = seq
        await self._save()
        return seq

    async def set_last_scan(self, when: str) -> None:
        data = await self._get_loaded_data()
        data["meta"]["last_scan"] = when
        await self._save()

    async def get_last_scan(self) -> str | None:
        data = await self._get_loaded_data()
        scan = data["meta"].get("last_scan")
        return scan if isinstance(scan, str) else None

    # ── Insight status overrides ─────────────────────────────────────────
    # Layer 2 insights are (re)generated deterministically from signals, so
    # only the user's per-insight action (dismiss/acknowledge/resolve) needs
    # persisting. Keyed by the stable insight_id.

    # ── Daily home audit (LLM) result cache ──────────────────────────────
    # The background audit's last result is persisted so the panel shows it
    # instantly across restarts; a fingerprint lets the runner skip the LLM
    # call when the home hasn't changed.

    async def get_last_audit(self) -> dict[str, Any] | None:
        data = await self._get_loaded_data()
        audit = data["meta"].get("last_audit")
        return dict(audit) if isinstance(audit, dict) else None

    async def set_last_audit(self, audit: dict[str, Any]) -> None:
        """Cache the latest audit, persisting only when its content actually moved.

        The audit re-runs on every health scan (default every 15 min) so the Home
        Health sensor tracks state at the scan cadence rather than the 24h one.
        Persisting each run rewrote this whole document ~96x/day for no gain — a
        steady home produces a record identical apart from ``generated_at``, and
        that write amplification is real flash wear on SD-card/eMMC installs. The
        in-memory copy is always refreshed; only the disk write is conditional.
        """
        data = await self._get_loaded_data()
        previous = data["meta"].get("last_audit")
        data["meta"]["last_audit"] = audit
        current_ts = _parse_iso(audit.get("generated_at"))
        # Unchanged content: persist only once the copy ON DISK has gone stale.
        # `self._audit_persisted_ts` tracks exactly that — seeded at load, refreshed
        # by every `_save`. Deriving the baseline from the in-memory `previous`
        # instead would move it forward on each skipped write, so the window would
        # never elapse and the disk copy could stay stale indefinitely.
        if (
            isinstance(previous, dict)
            and _audit_material(previous) == _audit_material(audit)
            and not _checkpoint_due(self._audit_persisted_ts, current_ts)
        ):
            return
        await self._save()

    async def get_insight_overrides(self) -> dict[str, str]:
        data = await self._get_loaded_data()
        overrides = data["meta"].get("insight_status")
        return dict(overrides) if isinstance(overrides, dict) else {}

    async def set_insight_override(self, insight_id: str, status: str) -> None:
        data = await self._get_loaded_data()
        overrides = data["meta"].setdefault("insight_status", {})
        overrides[insight_id] = status
        await self._save()


def get_health_store(hass: HomeAssistant) -> HealthStore | None:
    """Find the HealthStore from any active config entry."""
    domain_data = hass.data.get(DOMAIN, {})
    for key, val in domain_data.items():
        if key.startswith("_") or not isinstance(val, dict):
            continue
        store = val.get("health_store")
        if store is not None:
            return store
    return None
