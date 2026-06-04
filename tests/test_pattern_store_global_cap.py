"""Tests for the pattern_store global state_history cap helper.

The per-entity ring buffer bounds each list at
PATTERN_HISTORY_MAX_PER_ENTITY (500) records, but with 200 entities at
full capacity the store would hold 100 000 records — persisted to disk
every 50 events and loaded back into memory at startup. The global cap
keeps the file (and the heap representation) bounded regardless of how
many entities the user tracks.
"""

from __future__ import annotations

from custom_components.selora_ai.const import PATTERN_HISTORY_MAX_TOTAL
from custom_components.selora_ai.pattern_store import PatternStore


def _hist(entity_count: int, per_entity: int) -> dict[str, list[dict]]:
    """Build a synthetic state_history with monotonically-increasing timestamps.

    Records are inserted in *time* order across the whole structure (not
    per-entity) so the global newest-first sort can be verified end-to-end
    rather than just within a single entity's bucket.
    """
    history: dict[str, list[dict]] = {f"sensor.s{e}": [] for e in range(entity_count)}
    seq = 0
    for _ in range(per_entity):
        for e in range(entity_count):
            ts = f"2026-01-01T{(seq // 3600) % 24:02d}:{(seq // 60) % 60:02d}:{seq % 60:02d}+00:00"
            history[f"sensor.s{e}"].append({"state": "on", "prev": "off", "ts": ts, "seq": seq})
            seq += 1
    return history


def test_under_cap_no_drops() -> None:
    """Total ≤ ceiling — nothing dropped, structure unchanged in shape."""
    history = _hist(entity_count=5, per_entity=10)  # 50 total
    dropped = PatternStore._enforce_global_history_cap(history)
    assert dropped == 0
    assert sum(len(v) for v in history.values()) == 50
    assert set(history.keys()) == {f"sensor.s{i}" for i in range(5)}


def test_over_cap_drops_oldest_globally() -> None:
    """Total > ceiling — drops the oldest records across the whole store,
    keeping the newest ``PATTERN_HISTORY_MAX_TOTAL``."""
    # Pick an overflow that exercises both per-entity slicing and entity
    # eviction without spending forever building data.
    overflow = 200
    entity_count = 25
    per_entity = (PATTERN_HISTORY_MAX_TOTAL + overflow) // entity_count + 1
    history = _hist(entity_count=entity_count, per_entity=per_entity)
    total_before = sum(len(v) for v in history.values())
    assert total_before > PATTERN_HISTORY_MAX_TOTAL

    dropped = PatternStore._enforce_global_history_cap(history)
    total_after = sum(len(v) for v in history.values())

    assert dropped == total_before - total_after
    assert total_after == PATTERN_HISTORY_MAX_TOTAL
    # The newest record (highest seq) was inserted last → must survive.
    surviving_seqs = {r["seq"] for entries in history.values() for r in entries}
    max_seq = total_before - 1
    assert max_seq in surviving_seqs
    # The oldest record (seq=0) is dropped.
    assert 0 not in surviving_seqs


def test_over_cap_evicts_empty_entities() -> None:
    """When an entity's records are all older than the cut, drop the key.

    Without this, eviction would leave thousands of ``{eid: []}`` entries
    in the dict and the on-disk file would still bloat.
    """
    # First entity loaded with very-old timestamps; the rest with newer
    # ones that easily fill the cap by themselves.
    old_entity = "sensor.ancient"
    fresh = _hist(entity_count=10, per_entity=(PATTERN_HISTORY_MAX_TOTAL // 10) + 5)
    history = {
        old_entity: [
            {"state": "on", "prev": "off", "ts": "1990-01-01T00:00:00+00:00"} for _ in range(20)
        ],
        **fresh,
    }
    dropped = PatternStore._enforce_global_history_cap(history)
    assert dropped > 0
    # The ancient entity had only old records — all dropped, key gone.
    assert old_entity not in history


def test_handles_missing_ts_field() -> None:
    """Records with ``ts=""`` (or missing) sort to the tail and get dropped
    first — the sort key never raises on ``None``/missing keys."""
    history = _hist(entity_count=5, per_entity=10)
    # Inject one record without a ts at the front of one entity's bucket
    history["sensor.s0"].insert(0, {"state": "x", "prev": "x", "ts": "", "seq": -1})
    # Push us over the cap to force eviction
    overflow_per_entity = (PATTERN_HISTORY_MAX_TOTAL // 5) + 5
    history = _hist(entity_count=5, per_entity=overflow_per_entity)
    history["sensor.s0"].insert(0, {"state": "x", "prev": "x", "ts": "", "seq": -1})

    dropped = PatternStore._enforce_global_history_cap(history)
    assert dropped > 0
    # The ts-less record was the first to be dropped.
    surviving_seqs = {r.get("seq") for entries in history.values() for r in entries}
    assert -1 not in surviving_seqs


def test_idempotent_when_called_twice() -> None:
    """Calling enforce twice on the same dict is a no-op the second time."""
    history = _hist(entity_count=20, per_entity=(PATTERN_HISTORY_MAX_TOTAL // 20) + 10)
    PatternStore._enforce_global_history_cap(history)
    total_after_first = sum(len(v) for v in history.values())
    second_dropped = PatternStore._enforce_global_history_cap(history)
    assert second_dropped == 0
    assert sum(len(v) for v in history.values()) == total_after_first
