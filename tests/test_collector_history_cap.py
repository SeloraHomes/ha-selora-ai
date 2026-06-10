"""Tests for the recorder_history record-count cap + entity batching.

``get_significant_states`` returns every state change for every entity
inside the lookback window. On a 200-entity install with a busy week,
that's tens of thousands of dicts held in heap for the duration of the
LLM analysis call (up to ``ANALYSIS_LLM_TIMEOUT`` = 300 s).

The cap (``_cap_history_records``) bounds the result list AFTER it
comes back. The batching loop in ``_collect_recorder_history`` is the
companion guard: it bounds the peak working set DURING the recorder
fetch itself. A 1300-entity HA Green crashed 5 times in one night
because a single ``get_significant_states(entity_ids=<1300>, start=7d)``
call materialised the full result inside the recorder thread before
returning — capping the result list later doesn't help when the
recorder OOMs on the way back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from custom_components.selora_ai.collector import DataCollector, _cap_history_records
from custom_components.selora_ai.const import (
    DEFAULT_RECORDER_HISTORY_MAX_RECORDS,
    DEFAULT_RECORDER_QUERY_BATCH_SIZE,
)


def _rec(entity_id: str, ts: str) -> dict:
    return {"entity_id": entity_id, "state": "on", "last_changed": ts}


def _fake_state(entity_id: str, ts: datetime):
    """Mimic the HA State fields _collect_recorder_history reads."""
    s = MagicMock()
    s.entity_id = entity_id
    s.state = "on"
    s.last_changed = ts
    return s


async def _populate_entities(hass, count: int, prefix: str = "sensor.s") -> list[str]:
    """Register ``count`` entities so collector's async_all() returns them."""
    eids = [f"{prefix}{i}" for i in range(count)]
    for eid in eids:
        hass.states.async_set(eid, "on")
    return eids


def test_under_cap_returns_input_unchanged() -> None:
    """Below the ceiling — return every record, in original order, no sort."""
    history = [_rec(f"light.l{i}", f"2026-01-01T00:00:{i:02d}+00:00") for i in range(50)]
    out = _cap_history_records(history, 5000, lookback_days=7)
    assert out is history  # same object — no copy, no sort
    assert len(out) == 50


def test_over_cap_keeps_newest_drops_oldest_tail() -> None:
    """Above the ceiling — sort newest-first and keep the cap; drop the oldest tail."""
    cap = 100
    overflow = cap + 50
    # Insert in oldest-first order so any kept-without-sort would be wrong.
    history = [
        _rec(f"sensor.s{i}", f"2026-01-01T{(i // 60):02d}:{(i % 60):02d}:00+00:00")
        for i in range(overflow)
    ]
    out = _cap_history_records(history, cap, lookback_days=7)
    assert len(out) == cap
    # Newest record (highest timestamp) is at the head.
    assert out[0]["entity_id"] == f"sensor.s{overflow - 1}"
    # First 50 (oldest) entries are gone.
    kept_ids = {r["entity_id"] for r in out}
    assert "sensor.s0" not in kept_ids
    assert "sensor.s49" not in kept_ids
    assert f"sensor.s{cap}" in kept_ids  # boundary kept


def test_tolerates_missing_last_changed() -> None:
    """A record with ``last_changed = None`` sorts to the tail via empty-string
    key and gets dropped — the sort must not raise."""
    history = [
        {"entity_id": "sensor.nullts", "state": "on", "last_changed": None},
        *[_rec(f"sensor.s{i}", f"2026-01-01T00:00:{i:02d}+00:00") for i in range(150)],
    ]
    out = _cap_history_records(history, 100, lookback_days=7)
    assert len(out) == 100
    # The null-timestamp record sorted to the tail and was dropped.
    assert all(r["entity_id"] != "sensor.nullts" for r in out)


def test_at_exact_cap_returns_unchanged() -> None:
    """Boundary — length equals cap. No sort, no log."""
    history = [_rec(f"x.{i}", f"2026-01-01T00:00:{i:02d}+00:00") for i in range(10)]
    out = _cap_history_records(history, 10, lookback_days=7)
    assert out is history
    assert len(out) == 10


# ── Recorder query batching ───────────────────────────────────────────


def _make_collector(hass) -> DataCollector:
    return DataCollector(hass, MagicMock())


@pytest.mark.asyncio
async def test_recorder_query_splits_into_batches(hass) -> None:
    """A 250-entity install issues ceil(250 / batch_size) recorder calls,
    each scoped to ≤ batch_size entity_ids — bounds peak memory of any
    single ``get_significant_states`` call."""
    n_entities = 250
    await _populate_entities(hass, n_entities)
    collector = _make_collector(hass)
    base = datetime.now(UTC) - timedelta(days=1)

    batch_sizes: list[int] = []

    async def _fake_exec(_fn, _hass, _start, _now, batch_ids):
        batch_sizes.append(len(batch_ids))
        return {eid: [_fake_state(eid, base)] for eid in batch_ids}

    instance = MagicMock()
    instance.async_add_executor_job = _fake_exec

    with (
        patch("homeassistant.components.recorder.get_instance", return_value=instance),
        patch(
            "custom_components.selora_ai.entity_filter.resolve_ignored_entity_ids",
            return_value=set(),
        ),
    ):
        history = await collector._collect_recorder_history()

    expected_batches = (n_entities + DEFAULT_RECORDER_QUERY_BATCH_SIZE - 1) // (
        DEFAULT_RECORDER_QUERY_BATCH_SIZE
    )
    assert len(batch_sizes) == expected_batches
    # Every batch except the last is full; the last carries the remainder.
    assert all(sz <= DEFAULT_RECORDER_QUERY_BATCH_SIZE for sz in batch_sizes)
    assert sum(batch_sizes) == n_entities
    assert len(history) == n_entities


@pytest.mark.asyncio
async def test_recorder_query_trims_between_batches_without_skipping_entities(
    hass,
) -> None:
    """When buffered records exceed 2× the final cap, the accumulator is
    trimmed to the cap and the loop continues — so a chatty early batch
    can't starve later entities whose changes may be newer."""
    n_entities = 500  # 5 batches at BATCH_SIZE=100
    await _populate_entities(hass, n_entities)
    collector = _make_collector(hass)
    base = datetime.now(UTC) - timedelta(days=7)

    # First batch (entities 0–99) gets old timestamps and enough changes
    # to overflow the trim threshold on its own. Later batches (entities
    # 100–499) get newer timestamps with few changes per entity — those
    # must survive the streaming trim and appear in the final result.
    overflow_changes = (DEFAULT_RECORDER_HISTORY_MAX_RECORDS * 2) // (
        DEFAULT_RECORDER_QUERY_BATCH_SIZE
    ) + 1
    newest = datetime.now(UTC)
    batches_run = 0

    async def _fake_exec(_fn, _hass, _start, _now, batch_ids):
        nonlocal batches_run
        batches_run += 1
        result = {}
        for eid in batch_ids:
            idx = int(eid.removeprefix("sensor.s"))
            if idx < DEFAULT_RECORDER_QUERY_BATCH_SIZE:
                # Old, chatty entity — many changes at base time.
                result[eid] = [
                    _fake_state(eid, base + timedelta(seconds=i))
                    for i in range(overflow_changes)
                ]
            else:
                # Newer, quiet entity — one recent change.
                result[eid] = [_fake_state(eid, newest)]
        return result

    instance = MagicMock()
    instance.async_add_executor_job = _fake_exec

    with (
        patch("homeassistant.components.recorder.get_instance", return_value=instance),
        patch(
            "custom_components.selora_ai.entity_filter.resolve_ignored_entity_ids",
            return_value=set(),
        ),
    ):
        history = await collector._collect_recorder_history()

    # All batches must run — no early-stop skipping of later entities.
    expected_batches = (n_entities + DEFAULT_RECORDER_QUERY_BATCH_SIZE - 1) // (
        DEFAULT_RECORDER_QUERY_BATCH_SIZE
    )
    assert batches_run == expected_batches

    # Final cap honoured.
    assert len(history) <= DEFAULT_RECORDER_HISTORY_MAX_RECORDS

    # Late entities (idx ≥ 100) contributed the newest records, so they
    # must appear in the capped result.
    kept_ids = {r["entity_id"] for r in history}
    late_entities_kept = {eid for eid in kept_ids if int(eid.removeprefix("sensor.s")) >= 100}
    assert late_entities_kept, "late entities were dropped — streaming trim is biased"


@pytest.mark.asyncio
async def test_recorder_query_no_entities_returns_empty(hass) -> None:
    """No registered entities → no recorder calls at all."""
    collector = _make_collector(hass)
    instance = MagicMock()
    instance.async_add_executor_job = MagicMock()

    with (
        patch("homeassistant.components.recorder.get_instance", return_value=instance),
        patch(
            "custom_components.selora_ai.entity_filter.resolve_ignored_entity_ids",
            return_value=set(),
        ),
    ):
        history = await collector._collect_recorder_history()

    assert history == []
    instance.async_add_executor_job.assert_not_called()
