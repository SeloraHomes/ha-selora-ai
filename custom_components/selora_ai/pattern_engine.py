"""PatternEngine — lightweight local pattern detection for automation discovery.

Pure Python statistical analysis — no ML libraries. Runs every 15 minutes
and writes detected patterns to PatternStore.

Detectors:
  1. Time-based — recurring state changes at the same time of day
  2. Correlations — device pairs that change state within a short window
  3. Sequences — ordered A→B event chains
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Coroutine
from contextlib import suppress
from datetime import UTC, datetime, timedelta
import logging
import statistics
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CAUSALITY_DIRECTIONALITY_PENALTY,
    CAUSALITY_MAX_DELAY_STDDEV,
    CAUSALITY_MIN_DIRECTIONALITY,
    CONFIDENCE_MEDIUM,
    DEFAULT_PATTERN_INTERVAL,
    PATTERN_TYPE_CORRELATION,
    PATTERN_TYPE_SEQUENCE,
    PATTERN_TYPE_TIME_BASED,
)
from .pattern_store import PatternStore

_LOGGER = logging.getLogger(__name__)

# Minimum occurrences to consider a time-based pattern
_MIN_TIME_OCCURRENCES = 3

# Co-occurrence window for correlation detection (seconds)
_CORRELATION_WINDOW_SECS = 300  # 5 minutes

# Minimum co-occurrences to consider a correlation pattern
_MIN_COOCCURRENCES = 4

# Number of 15-minute time slots per day
_SLOTS_PER_DAY = 96

# Safety limits to prevent OOM on large histories
_MAX_HISTORY_PER_ENTITY_SCAN = 100  # Only scan the most recent N changes per entity
_MAX_TIMELINE_SIZE = 2000  # Hard cap on total timeline entries
_MAX_PAIR_SAMPLES = 50  # Max co-occurrence samples stored per pair
_YIELD_EVERY = 500  # Yield to event loop every N iterations

# States to skip in correlation/sequence detection
_SKIP_STATES = frozenset({"unavailable", "unknown", ""})


def _match_causal_episodes(
    timeline: list[tuple[datetime, str, str]],
    eid_a: str,
    state_a: str,
    eid_b: str,
    state_b: str,
    window_secs: float,
    *,
    min_ts: datetime | None = None,
) -> tuple[float, list[float]]:
    """Two-pass forward matching returning directionality and causal delays.

    Pass 1: each A claims its nearest unclaimed B (forward episodes).
    Pass 2: remaining B events claim their nearest unclaimed A (reverse).

    A-first priority correctly handles cycling (all forward), overlapping
    cycles where A repeats faster than the lag, and stray B events.
    Chronological processing ensures each B is attributed to the A that
    started the episode, not the closest A by delay.
    Runs in O(n_A * n_B) on the entity-filtered event lists, not the
    full timeline.

    Args:
        min_ts: ignore events before this timestamp so the metrics stay
                on the same recency window as the capped delay stats.

    Returns (directionality, forward_delays) where directionality is the
    fraction of episodes where A fired first (1.0 = purely A→B).
    """
    a_events: list[datetime] = []
    b_events: list[datetime] = []
    for ts, eid, state in timeline:
        if min_ts is not None and ts < min_ts:
            continue
        if eid == eid_a and state == state_a:
            a_events.append(ts)
        elif eid == eid_b and state == state_b:
            b_events.append(ts)
    # Both already sorted since timeline is sorted.

    forward = 0
    reverse = 0
    forward_delays: list[float] = []
    matched_b: set[int] = set()

    # Pass 1: for each A, find nearest unclaimed B response (B after A).
    for _ai, a_ts in enumerate(a_events):
        for bi, b_ts in enumerate(b_events):
            if bi in matched_b:
                continue
            delta = (b_ts - a_ts).total_seconds()
            if delta < 0:
                continue
            if delta > window_secs:
                break
            if delta < 1:
                continue
            matched_b.add(bi)
            forward += 1
            forward_delays.append(delta)
            break

    # Pass 2: remaining B events look for an A that follows them.
    # A events consumed in pass 1 are NOT excluded — a consumed A was a
    # *trigger* that can still be a *target* of an earlier unclaimed B.
    # Only consumed B events are excluded (already counted as responses).
    # Each A can only be claimed once in pass 2 so that duplicate B
    # updates don't inflate the reverse count against a single A.
    matched_a_p2: set[int] = set()
    for bi, b_ts in enumerate(b_events):
        if bi in matched_b:
            continue
        for ai, a_ts in enumerate(a_events):
            if ai in matched_a_p2:
                continue
            delta = (a_ts - b_ts).total_seconds()
            if delta < 0:
                continue
            if delta > window_secs:
                break
            if delta < 1:
                continue
            matched_a_p2.add(ai)
            reverse += 1
            break

    total = forward + reverse
    directionality = forward / total if total > 0 else 1.0
    return directionality, forward_delays


class PatternEngine:
    """Lightweight local pattern detection — no ML dependencies."""

    def __init__(
        self,
        hass: HomeAssistant,
        pattern_store: PatternStore,
    ) -> None:
        self._hass = hass
        self._store = pattern_store
        self._unsub_timer: Callable | None = None
        self._initial_scan_task: asyncio.Task | None = None
        self.on_patterns_detected: (
            Callable[[list[dict[str, Any]]], Coroutine[Any, Any, None]] | None
        ) = None

    async def async_start(self) -> None:
        """Start periodic pattern scanning."""
        self._unsub_timer = async_track_time_interval(
            self._hass,
            self._scheduled_scan,
            timedelta(seconds=DEFAULT_PATTERN_INTERVAL),
        )
        if self._initial_scan_task is None or self._initial_scan_task.done():
            self._initial_scan_task = self._hass.async_create_task(self._delayed_initial_scan())

    async def _delayed_initial_scan(self) -> None:
        """Wait 60 seconds after startup, then run the first scan."""
        await asyncio.sleep(60)
        await self._scheduled_scan(None)

    async def async_stop(self) -> None:
        """Stop the periodic timer."""
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None

        if self._initial_scan_task and not self._initial_scan_task.done():
            self._initial_scan_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._initial_scan_task
        self._initial_scan_task = None

    async def _scheduled_scan(self, _now: Any) -> None:
        """Periodic scan callback."""
        try:
            new_patterns = await self.scan()
            if new_patterns and self.on_patterns_detected:
                await self.on_patterns_detected(new_patterns)
        except Exception:
            _LOGGER.exception("Pattern scan failed")

    async def scan(self) -> list[dict[str, Any]]:
        """Run all pattern detectors and return new/updated patterns."""
        history = await self._store.get_all_history()
        if not history:
            return []

        new_patterns: list[dict[str, Any]] = []
        new_patterns.extend(await self._detect_time_patterns(history))
        new_patterns.extend(await self._detect_correlations(history))
        new_patterns.extend(await self._detect_sequences(history))

        # Update metadata
        data = await self._store._get_loaded_data()
        data["meta"]["last_pattern_scan"] = datetime.now(UTC).isoformat()
        await self._store._save()

        if new_patterns:
            _LOGGER.info("Detected %d new/updated patterns", len(new_patterns))

        return new_patterns

    async def _detect_time_patterns(
        self, history: dict[str, list[dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        """Detect recurring time-based state changes.

        Algorithm:
        1. For each entity, bucket state changes by (target_state, 15-min slot, weekday/weekend)
        2. Count distinct days with changes in each bucket
        3. Buckets with 3+ days are patterns
        4. Confidence = distinct_days / total_days_observed
        """
        patterns: list[dict[str, Any]] = []

        for entity_id, changes in history.items():
            if len(changes) < _MIN_TIME_OCCURRENCES:
                continue

            # Group by (target_state, 15-min slot, is_weekday)
            time_buckets: dict[tuple[str, int, bool], list[datetime]] = defaultdict(list)
            for change in changes:
                ts = _parse_timestamp(change["ts"])
                if ts is None:
                    continue
                slot = ts.hour * 4 + ts.minute // 15
                is_weekday = ts.weekday() < 5
                key = (change["state"], slot, is_weekday)
                time_buckets[key].append(ts)

            total_days = _count_distinct_days(changes)
            if total_days < 3:
                continue

            for (state, slot, is_weekday), timestamps in time_buckets.items():
                distinct_days = len({ts.date() for ts in timestamps})
                if distinct_days < _MIN_TIME_OCCURRENCES:
                    continue

                # Skip unavailable/unknown states
                if state in ("unavailable", "unknown", ""):
                    continue

                hour = (slot * 15) // 60
                minute = (slot * 15) % 60
                confidence = min(distinct_days / max(total_days, 1), 1.0)

                if confidence < CONFIDENCE_MEDIUM:
                    continue

                entity_name = entity_id.split(".")[-1].replace("_", " ").title()
                day_type = "weekdays" if is_weekday else "weekends"

                # Build a dedup signature
                signature = f"{entity_id}:{state}:{slot}:{is_weekday}"

                existing = await self._store.find_pattern_by_signature(
                    PATTERN_TYPE_TIME_BASED, [entity_id], signature
                )

                pattern: dict[str, Any] = {
                    "pattern_id": existing["pattern_id"] if existing else None,
                    "type": PATTERN_TYPE_TIME_BASED,
                    "entity_ids": [entity_id],
                    "description": (
                        f"{entity_name} turns {state} around {hour:02d}:{minute:02d} on {day_type}"
                    ),
                    "evidence": {
                        "_signature": signature,
                        "time_slot": f"{hour:02d}:{minute:02d}",
                        "is_weekday": is_weekday,
                        "target_state": state,
                        "occurrences": distinct_days,
                        "total_days": total_days,
                        "timestamps": [ts.isoformat() for ts in timestamps[-5:]],
                    },
                    "confidence": confidence,
                }

                pid = await self._store.save_pattern(pattern)
                pattern["pattern_id"] = pid
                if not existing:
                    patterns.append(pattern)

        return patterns

    async def _detect_correlations(
        self, history: dict[str, list[dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        """Detect device pairs that change state within a short window.

        Algorithm:
        1. Build sorted timeline of all state changes (capped per entity)
        2. Sliding window: for each change, scan forward up to 5 minutes
        3. Count co-occurrence pairs (A_state → B_state within window)
        4. Pairs with 4+ matches are patterns
        """
        # Build sorted timeline — cap per entity to avoid memory explosion
        timeline: list[tuple[datetime, str, str]] = []
        for entity_id, changes in history.items():
            recent = changes[-_MAX_HISTORY_PER_ENTITY_SCAN:]
            for change in recent:
                ts = _parse_timestamp(change["ts"])
                if ts is not None and change["state"] not in _SKIP_STATES:
                    timeline.append((ts, entity_id, change["state"]))

        timeline.sort(key=lambda x: x[0])

        # Cap total timeline to prevent runaway memory usage
        if len(timeline) > _MAX_TIMELINE_SIZE:
            timeline = timeline[-_MAX_TIMELINE_SIZE:]

        if len(timeline) < _MIN_COOCCURRENCES * 2:
            return []

        # Count co-occurrences within the time window using sliding window
        pair_counts: dict[tuple[str, str, str, str], list[float]] = defaultdict(list)
        # Which A-event timeline indices contributed each entry in
        # pair_counts (parallel list, trimmed together so the recency
        # window for causality metrics stays aligned with co-occurrence
        # counts).
        pair_contrib: dict[tuple[str, str, str, str], list[int]] = defaultdict(list)

        window_start = 0
        for i, (ts_a, eid_a, state_a) in enumerate(timeline):
            # Advance window_start to keep only events within range
            while window_start < i:
                if (ts_a - timeline[window_start][0]).total_seconds() <= _CORRELATION_WINDOW_SECS:
                    break
                window_start += 1

            # Look forward from i+1 while within the time window
            for j in range(i + 1, len(timeline)):
                ts_b, eid_b, state_b = timeline[j]
                delta = (ts_b - ts_a).total_seconds()
                if delta > _CORRELATION_WINDOW_SECS:
                    break
                if delta < 1 or eid_a == eid_b:
                    continue

                key = (eid_a, state_a, eid_b, state_b)
                pair_counts[key].append(delta)
                pair_contrib[key].append(i)
                # Cap per-pair storage to avoid unbounded list growth
                if len(pair_counts[key]) > _MAX_PAIR_SAMPLES:
                    pair_counts[key] = pair_counts[key][-_MAX_PAIR_SAMPLES:]
                    pair_contrib[key] = pair_contrib[key][-_MAX_PAIR_SAMPLES:]

            # Yield to event loop periodically to avoid blocking HA
            if i % _YIELD_EVERY == 0 and i > 0:
                await asyncio.sleep(0)

        patterns: list[dict[str, Any]] = []
        for (eid_a, state_a, eid_b, state_b), delays in pair_counts.items():
            if len(delays) < _MIN_COOCCURRENCES:
                continue

            # Calculate confidence based on co-occurrences relative to individual changes
            a_count = len(history.get(eid_a, []))
            b_count = len(history.get(eid_b, []))
            denom = min(a_count, b_count) if min(a_count, b_count) > 0 else 1
            confidence = min(len(delays) / denom, 1.0)

            if confidence < CONFIDENCE_MEDIUM:
                continue

            # -- Causality guardrails --
            # Compute one-to-one causal delays AND directionality in a
            # single two-pass forward match (A claims B first, then
            # remaining B claims A).  Chronological processing preserves
            # event order so overlapping cycles get the correct delay.
            # Limit to the same recency window as pair_counts via
            # pair_contrib so old bidirectional history can't suppress a
            # now-unidirectional pattern.
            contrib = pair_contrib.get((eid_a, state_a, eid_b, state_b), [])
            min_ts = None
            if contrib:
                min_ts = timeline[contrib[0]][0] - timedelta(seconds=_CORRELATION_WINDOW_SECS)
            directionality, causal_delays = _match_causal_episodes(
                timeline,
                eid_a,
                state_a,
                eid_b,
                state_b,
                _CORRELATION_WINDOW_SECS,
                min_ts=min_ts,
            )

            # Cross-direction check: if the reverse pair (B→A) also has
            # enough co-occurrences, run the episode matcher on it.  When
            # the reverse direction itself is bidirectional (rev_dir ≤ 0.5)
            # both directions are common-cause, not causal.  Cycling is
            # safe because its reverse direction has high forward
            # directionality (~0.75+), well above 0.5.
            reverse_key = (eid_b, state_b, eid_a, state_a)
            if directionality > 0.5 and len(pair_counts.get(reverse_key, [])) >= _MIN_COOCCURRENCES:
                # Derive the reverse cutoff from the reverse pair's own
                # retained samples so stale B→A history doesn't leak in.
                rev_contrib = pair_contrib.get(reverse_key, [])
                rev_min_ts = None
                if rev_contrib:
                    rev_min_ts = timeline[rev_contrib[0]][0] - timedelta(
                        seconds=_CORRELATION_WINDOW_SECS
                    )
                rev_dir, _ = _match_causal_episodes(
                    timeline,
                    eid_b,
                    state_b,
                    eid_a,
                    state_a,
                    _CORRELATION_WINDOW_SECS,
                    min_ts=rev_min_ts,
                )
                if rev_dir <= 0.5:
                    directionality = min(directionality, 0.5)

            # Delay variance penalty
            stddev = statistics.stdev(causal_delays) if len(causal_delays) >= 2 else 0.0
            if stddev > CAUSALITY_MAX_DELAY_STDDEV:
                confidence *= max(
                    0.0,
                    1.0
                    - (stddev - CAUSALITY_MAX_DELAY_STDDEV) / (CAUSALITY_MAX_DELAY_STDDEV * 1.5),
                )

            # Directionality penalty
            if directionality <= 0.5:
                confidence = 0.0
            elif directionality < CAUSALITY_MIN_DIRECTIONALITY:
                confidence *= (
                    directionality / CAUSALITY_MIN_DIRECTIONALITY * CAUSALITY_DIRECTIONALITY_PENALTY
                )

            # Re-check confidence after causality penalties
            if confidence < CONFIDENCE_MEDIUM:
                sig = f"{eid_a}:{state_a}->{eid_b}:{state_b}"
                existing = await self._store.find_pattern_by_signature(
                    PATTERN_TYPE_CORRELATION, [eid_a, eid_b], sig
                )
                if existing and existing.get("status") == "active":
                    await self._store.update_pattern_status(existing["pattern_id"], "rejected")
                    await self._store.remove_suggestions_for_pattern(existing["pattern_id"])
                continue

            avg_delay = (
                sum(causal_delays) / len(causal_delays)
                if causal_delays
                else sum(delays) / len(delays)
            )
            name_a = eid_a.split(".")[-1].replace("_", " ").title()
            name_b = eid_b.split(".")[-1].replace("_", " ").title()

            signature = f"{eid_a}:{state_a}->{eid_b}:{state_b}"

            existing = await self._store.find_pattern_by_signature(
                PATTERN_TYPE_CORRELATION, [eid_a, eid_b], signature
            )

            pattern: dict[str, Any] = {
                "pattern_id": existing["pattern_id"] if existing else None,
                "type": PATTERN_TYPE_CORRELATION,
                "entity_ids": [eid_a, eid_b],
                "description": (
                    f"{name_b} turns {state_b} within "
                    f"{int(avg_delay)}s of {name_a} turning {state_a}"
                ),
                "evidence": {
                    "_signature": signature,
                    "trigger_entity": eid_a,
                    "trigger_state": state_a,
                    "response_entity": eid_b,
                    "response_state": state_b,
                    "avg_delay_seconds": round(avg_delay, 1),
                    "co_occurrences": len(delays),
                    "window_minutes": _CORRELATION_WINDOW_SECS // 60,
                    "delay_stddev": round(stddev, 1),
                    "directionality": round(directionality, 2),
                },
                "confidence": confidence,
            }

            # save_pattern reactivates rejected patterns, so include
            # both new and reactivated patterns in the return list so the
            # suggestion pipeline can create fresh suggestions for them.
            was_rejected = existing and existing.get("status") == "rejected"
            pid = await self._store.save_pattern(pattern)
            pattern["pattern_id"] = pid
            if not existing or was_rejected:
                patterns.append(pattern)

            # When a correlation is reactivated, retire any fallback
            # sequence that was allowed while it was rejected.
            if was_rejected:
                sig = f"{eid_a}:{state_a}->{eid_b}:{state_b}"
                seq_patterns = await self._store.get_patterns(
                    status="active", pattern_type=PATTERN_TYPE_SEQUENCE
                )
                for sp in seq_patterns:
                    sp_sig = sp.get("evidence", {}).get("_signature", "")
                    # Sequence sig format: eid_a:prev->state_a=>eid_b:state_b
                    # Match on trigger state AND response, not just entity pair.
                    if (
                        set(sp["entity_ids"]) == {eid_a, eid_b}
                        and f"->{state_a}=>{eid_b}:{state_b}" in sp_sig
                    ):
                        await self._store.update_pattern_status(sp["pattern_id"], "rejected")
                        await self._store.remove_suggestions_for_pattern(sp["pattern_id"])

        return patterns

    async def _detect_sequences(
        self,
        history: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Detect ordered A→B event sequences.

        Similar to correlation detection but specifically tracks directional
        cause-effect relationships where A consistently precedes B.
        Only produces patterns not already captured by active
        correlation detection.
        """
        # Build sorted timeline — cap per entity
        timeline: list[tuple[datetime, str, str, str]] = []
        for entity_id, changes in history.items():
            recent = changes[-_MAX_HISTORY_PER_ENTITY_SCAN:]
            for change in recent:
                ts = _parse_timestamp(change["ts"])
                if ts is not None and change["state"] not in _SKIP_STATES:
                    timeline.append((ts, entity_id, change["state"], change.get("prev", "")))

        timeline.sort(key=lambda x: x[0])

        # Cap total timeline
        if len(timeline) > _MAX_TIMELINE_SIZE:
            timeline = timeline[-_MAX_TIMELINE_SIZE:]

        if len(timeline) < _MIN_COOCCURRENCES * 2:
            return []

        # Track directional sequences: A turns X → B turns Y
        # Only count if A's transition (prev→state) is meaningful
        seq_counts: dict[tuple[str, str, str, str, str], int] = defaultdict(int)

        for i, (ts_a, eid_a, state_a, prev_a) in enumerate(timeline):
            if not prev_a or prev_a == state_a:
                continue

            for j in range(i + 1, len(timeline)):
                ts_b, eid_b, state_b, _prev_b = timeline[j]
                delta = (ts_b - ts_a).total_seconds()
                if delta > _CORRELATION_WINDOW_SECS:
                    break
                if delta < 1 or eid_a == eid_b:
                    continue

                # Track the full transition: A(prev→state) → B turns state_b
                key = (eid_a, prev_a, state_a, eid_b, state_b)
                seq_counts[key] += 1

            # Yield to event loop periodically
            if i % _YIELD_EVERY == 0 and i > 0:
                await asyncio.sleep(0)

        patterns: list[dict[str, Any]] = []

        for (eid_a, prev_a, state_a, eid_b, state_b), count in seq_counts.items():
            if count < _MIN_COOCCURRENCES:
                continue

            a_total = len(history.get(eid_a, []))
            confidence = min(count / max(a_total, 1), 1.0)

            if confidence < CONFIDENCE_MEDIUM:
                continue

            # Skip if a correlation exists for this pair unless it was
            # rejected by causality guardrails.  Active, snoozed, and
            # dismissed correlations all block sequences so that
            # user hide/snooze controls aren't bypassed via a separate
            # pattern type.  Only "rejected" (guardrails evaluated and
            # failed) lets the more precise sequence detector try.
            corr_sig = f"{eid_a}:{state_a}->{eid_b}:{state_b}"
            existing_corr = await self._store.find_pattern_by_signature(
                PATTERN_TYPE_CORRELATION, [eid_a, eid_b], corr_sig
            )
            if existing_corr and existing_corr.get("status") != "rejected":
                continue

            name_a = eid_a.split(".")[-1].replace("_", " ").title()
            name_b = eid_b.split(".")[-1].replace("_", " ").title()

            signature = f"{eid_a}:{prev_a}->{state_a}=>{eid_b}:{state_b}"

            existing = await self._store.find_pattern_by_signature(
                PATTERN_TYPE_SEQUENCE, [eid_a, eid_b], signature
            )

            pattern: dict[str, Any] = {
                "pattern_id": existing["pattern_id"] if existing else None,
                "type": PATTERN_TYPE_SEQUENCE,
                "entity_ids": [eid_a, eid_b],
                "description": (
                    f"When {name_a} changes from {prev_a} to {state_a}, {name_b} turns {state_b}"
                ),
                "evidence": {
                    "_signature": signature,
                    "trigger_entity": eid_a,
                    "trigger_from": prev_a,
                    "trigger_to": state_a,
                    "response_entity": eid_b,
                    "response_state": state_b,
                    "occurrences": count,
                    "window_minutes": _CORRELATION_WINDOW_SECS // 60,
                },
                "confidence": confidence,
            }

            was_rejected = existing and existing.get("status") == "rejected"
            pid = await self._store.save_pattern(pattern)
            pattern["pattern_id"] = pid
            if not existing or was_rejected:
                patterns.append(pattern)

        return patterns


def _parse_timestamp(ts: str) -> datetime | None:
    """Parse ISO timestamp string to datetime."""
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _count_distinct_days(changes: list[dict[str, Any]]) -> int:
    """Count the number of distinct calendar days in a change list."""
    dates: set[str] = set()
    for change in changes:
        ts = _parse_timestamp(change["ts"])
        if ts:
            dates.add(ts.date().isoformat())
    return len(dates)
