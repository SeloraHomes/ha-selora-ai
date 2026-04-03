"""Tests for PatternStore — state history, patterns, and suggestions persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from custom_components.selora_ai.pattern_store import PatternStore

from .conftest import MockStore


@pytest.fixture
def pattern_store(hass):
    """Create a PatternStore backed by a MockStore."""
    with patch("custom_components.selora_ai.pattern_store.Store") as mock_cls:
        store_inst = MockStore()
        mock_cls.return_value = store_inst
        ps = PatternStore(hass)
        ps._store = store_inst
        yield ps, store_inst


# ── State History ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_state_change_appends(pattern_store):
    """record_state_change adds an entry to the entity ring buffer."""
    ps, mock_st = pattern_store
    ts = datetime.now(UTC).isoformat()

    await ps.record_state_change("light.kitchen", "on", "off", ts)

    data = await ps._get_loaded_data()
    entries = data["state_history"]["light.kitchen"]
    assert len(entries) == 1
    assert entries[0] == {"state": "on", "prev": "off", "ts": ts}


@pytest.mark.asyncio
async def test_record_state_change_batched_save(pattern_store):
    """State changes are only persisted every 50 events."""
    ps, mock_st = pattern_store
    ts = datetime.now(UTC).isoformat()

    for _i in range(49):
        await ps.record_state_change("light.kitchen", "on", "off", ts)

    assert len(mock_st.saved_data) == 0

    await ps.record_state_change("light.kitchen", "on", "off", ts)
    assert len(mock_st.saved_data) == 1


@pytest.mark.asyncio
async def test_record_state_change_ring_buffer_limit(pattern_store):
    """Ring buffer enforces PATTERN_HISTORY_MAX_PER_ENTITY (500) limit."""
    ps, mock_st = pattern_store
    ts = datetime.now(UTC).isoformat()

    for _i in range(510):
        await ps.record_state_change("light.kitchen", "on", "off", ts)

    data = await ps._get_loaded_data()
    assert len(data["state_history"]["light.kitchen"]) == 500


@pytest.mark.asyncio
async def test_flush_persists_and_resets_counter(pattern_store):
    """flush() saves immediately and resets the pending counter."""
    ps, mock_st = pattern_store
    ts = datetime.now(UTC).isoformat()

    await ps.record_state_change("light.kitchen", "on", "off", ts)
    assert len(mock_st.saved_data) == 0

    await ps.flush()
    assert len(mock_st.saved_data) == 1
    assert ps._pending_state_changes == 0


@pytest.mark.asyncio
async def test_get_entity_history_no_filter(pattern_store):
    """get_entity_history returns all entries when no since filter."""
    ps, _ = pattern_store
    ts1 = "2026-03-01T10:00:00+00:00"
    ts2 = "2026-03-02T10:00:00+00:00"

    await ps.record_state_change("light.kitchen", "on", "off", ts1)
    await ps.record_state_change("light.kitchen", "off", "on", ts2)

    result = await ps.get_entity_history("light.kitchen")
    assert len(result) == 2


@pytest.mark.asyncio
async def test_get_entity_history_with_since_filter(pattern_store):
    """get_entity_history filters entries by ISO timestamp cutoff."""
    ps, _ = pattern_store
    ts_old = "2026-03-01T10:00:00+00:00"
    ts_new = "2026-03-20T10:00:00+00:00"

    await ps.record_state_change("light.kitchen", "on", "off", ts_old)
    await ps.record_state_change("light.kitchen", "off", "on", ts_new)

    since = datetime(2026, 3, 15, tzinfo=UTC)
    result = await ps.get_entity_history("light.kitchen", since=since)
    assert len(result) == 1
    assert result[0]["ts"] == ts_new


@pytest.mark.asyncio
async def test_get_all_history_with_since_filter(pattern_store):
    """get_all_history only includes entities with entries after cutoff."""
    ps, _ = pattern_store
    ts_old = "2026-03-01T10:00:00+00:00"
    ts_new = "2026-03-20T10:00:00+00:00"

    await ps.record_state_change("light.old", "on", "off", ts_old)
    await ps.record_state_change("light.new", "on", "off", ts_new)

    since = datetime(2026, 3, 15, tzinfo=UTC)
    result = await ps.get_all_history(since=since)
    assert "light.new" in result
    assert "light.old" not in result


@pytest.mark.asyncio
async def test_prune_old_history(pattern_store):
    """prune_old_history removes entries older than the cutoff and cleans up empty keys."""
    ps, mock_st = pattern_store
    old_ts = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    recent_ts = datetime.now(UTC).isoformat()

    await ps.record_state_change("light.old_only", "on", "off", old_ts)
    await ps.record_state_change("light.mixed", "on", "off", old_ts)
    await ps.record_state_change("light.mixed", "off", "on", recent_ts)

    removed = await ps.prune_old_history(older_than_days=14)
    assert removed == 2

    data = await ps._get_loaded_data()
    assert "light.old_only" not in data["state_history"]
    assert len(data["state_history"]["light.mixed"]) == 1


@pytest.mark.asyncio
async def test_prune_old_history_no_save_when_nothing_removed(pattern_store):
    """prune_old_history does not save when no entries are removed."""
    ps, mock_st = pattern_store
    recent_ts = datetime.now(UTC).isoformat()
    await ps.record_state_change("light.recent", "on", "off", recent_ts)
    await ps.flush()
    saves_before = len(mock_st.saved_data)

    removed = await ps.prune_old_history(older_than_days=14)
    assert removed == 0
    assert len(mock_st.saved_data) == saves_before


# ── Patterns ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_pattern_creates_new(pattern_store, sample_time_pattern):
    """save_pattern creates a new pattern with status active and occurrence_count 1."""
    ps, mock_st = pattern_store

    pid = await ps.save_pattern(sample_time_pattern)
    assert pid == "pat_time_001"

    data = await ps._get_loaded_data()
    p = data["patterns"][pid]
    assert p["status"] == "active"
    assert p["occurrence_count"] == 1
    assert p["type"] == "time_based"
    assert p["snooze_until"] is None
    assert len(mock_st.saved_data) >= 1


@pytest.mark.asyncio
async def test_save_pattern_updates_existing(pattern_store, sample_time_pattern):
    """save_pattern increments occurrence_count and updates confidence on re-save."""
    ps, _ = pattern_store

    await ps.save_pattern(sample_time_pattern)

    updated = {**sample_time_pattern, "confidence": 0.9}
    await ps.save_pattern(updated)

    data = await ps._get_loaded_data()
    p = data["patterns"]["pat_time_001"]
    assert p["occurrence_count"] == 2
    assert p["confidence"] == 0.9


@pytest.mark.asyncio
async def test_get_patterns_filters_by_status_and_type(pattern_store):
    """get_patterns returns only matching patterns."""
    ps, _ = pattern_store

    await ps.save_pattern(
        {
            "pattern_id": "p1",
            "type": "time_based",
            "confidence": 0.7,
            "entity_ids": ["light.a"],
            "description": "Pattern 1",
        }
    )
    await ps.save_pattern(
        {
            "pattern_id": "p2",
            "type": "correlation",
            "confidence": 0.8,
            "entity_ids": ["light.b"],
            "description": "Pattern 2",
        }
    )

    results = await ps.get_patterns(pattern_type="time_based")
    assert len(results) == 1
    assert results[0]["pattern_id"] == "p1"

    results = await ps.get_patterns(status="active", pattern_type="correlation")
    assert len(results) == 1
    assert results[0]["pattern_id"] == "p2"


@pytest.mark.asyncio
async def test_get_patterns_unsnoozes_expired(pattern_store):
    """get_patterns auto-unsnoozes patterns whose snooze_until has passed."""
    ps, _ = pattern_store
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    await ps.save_pattern(
        {
            "pattern_id": "p_snoozed",
            "type": "time_based",
            "confidence": 0.7,
            "entity_ids": ["light.a"],
            "description": "Snoozed pattern",
        }
    )
    data = await ps._get_loaded_data()
    data["patterns"]["p_snoozed"]["status"] = "snoozed"
    data["patterns"]["p_snoozed"]["snooze_until"] = past

    results = await ps.get_patterns(status="active")
    assert any(p["pattern_id"] == "p_snoozed" for p in results)
    assert data["patterns"]["p_snoozed"]["status"] == "active"


@pytest.mark.asyncio
async def test_find_pattern_by_signature(pattern_store, sample_correlation_pattern):
    """find_pattern_by_signature matches on type, entity set, and evidence._signature."""
    ps, _ = pattern_store
    await ps.save_pattern(sample_correlation_pattern)

    found = await ps.find_pattern_by_signature(
        "correlation",
        ["light.hallway", "binary_sensor.front_door"],  # order differs
        "binary_sensor.front_door:on->light.hallway:on",
    )
    assert found is not None
    assert found["pattern_id"] == "pat_corr_001"

    # Non-matching signature
    not_found = await ps.find_pattern_by_signature(
        "correlation",
        ["light.hallway", "binary_sensor.front_door"],
        "wrong_signature",
    )
    assert not_found is None


# ── Suggestions ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_suggestion_creates_pending(pattern_store):
    """save_suggestion creates a suggestion with status pending and null dismissal fields."""
    ps, mock_st = pattern_store

    sid = await ps.save_suggestion(
        {
            "pattern_id": "pat_001",
            "description": "Turn on porch light at sunset",
            "confidence": 0.85,
        }
    )

    data = await ps._get_loaded_data()
    s = data["suggestions"][sid]
    assert s["status"] == "pending"
    assert s["snooze_until"] is None
    assert s["dismissed_at"] is None
    assert s["dismissal_reason"] is None
    assert s["pattern_id"] == "pat_001"


@pytest.mark.asyncio
async def test_get_suggestions_unsnoozes_expired(pattern_store):
    """get_suggestions changes expired snoozed suggestions back to pending."""
    ps, _ = pattern_store
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    sid = await ps.save_suggestion({"pattern_id": "p1", "description": "Test"})
    data = await ps._get_loaded_data()
    data["suggestions"][sid]["status"] = "snoozed"
    data["suggestions"][sid]["snooze_until"] = past

    results = await ps.get_suggestions(status="pending")
    assert len(results) == 1
    assert results[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_has_suggestion_for_pattern(pattern_store):
    """has_suggestion_for_pattern returns True for pending/snoozed, False otherwise."""
    ps, _ = pattern_store

    await ps.save_suggestion({"pattern_id": "pat_a", "description": "Test"})
    assert await ps.has_suggestion_for_pattern("pat_a") is True
    assert await ps.has_suggestion_for_pattern("pat_nonexistent") is False

    # Dismiss the suggestion and check again
    data = await ps._get_loaded_data()
    for s in data["suggestions"].values():
        if s["pattern_id"] == "pat_a":
            s["status"] = "dismissed"

    assert await ps.has_suggestion_for_pattern("pat_a") is False


@pytest.mark.asyncio
async def test_get_recently_dismissed_suggestions(pattern_store):
    """get_recently_dismissed_suggestions returns only recently dismissed entries."""
    ps, _ = pattern_store

    sid_recent = await ps.save_suggestion({"pattern_id": "p1", "description": "Recent"})
    sid_old = await ps.save_suggestion({"pattern_id": "p2", "description": "Old"})

    data = await ps._get_loaded_data()
    recent_ts = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    old_ts = (datetime.now(UTC) - timedelta(days=30)).isoformat()

    data["suggestions"][sid_recent]["status"] = "dismissed"
    data["suggestions"][sid_recent]["dismissed_at"] = recent_ts
    data["suggestions"][sid_old]["status"] = "dismissed"
    data["suggestions"][sid_old]["dismissed_at"] = old_ts

    results = await ps.get_recently_dismissed_suggestions(window_days=7)
    assert len(results) == 1
    assert results[0]["suggestion_id"] == sid_recent


# ── Data Loading ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_loaded_initialises_empty_store(hass):
    """When the store returns None, PatternStore initialises with empty sections."""
    with patch("custom_components.selora_ai.pattern_store.Store") as mock_cls:
        store_inst = MockStore(initial_data=None)
        mock_cls.return_value = store_inst
        ps = PatternStore(hass)
        ps._store = store_inst

        data = await ps._get_loaded_data()
        assert data == {
            "state_history": {},
            "patterns": {},
            "suggestions": {},
            "deleted_hashes": {},
            "meta": {},
        }


@pytest.mark.asyncio
async def test_ensure_loaded_migrates_suggestion_fields(hass):
    """Loading data adds dismissed_at/dismissal_reason to legacy suggestions."""
    legacy_data = {
        "state_history": {},
        "patterns": {},
        "suggestions": {
            "s1": {
                "suggestion_id": "s1",
                "pattern_id": "p1",
                "status": "pending",
                "snooze_until": None,
                # missing dismissed_at and dismissal_reason
            }
        },
        "meta": {},
    }
    with patch("custom_components.selora_ai.pattern_store.Store") as mock_cls:
        store_inst = MockStore(initial_data=legacy_data)
        mock_cls.return_value = store_inst
        ps = PatternStore(hass)
        ps._store = store_inst

        data = await ps._get_loaded_data()
        s = data["suggestions"]["s1"]
        assert s["dismissed_at"] is None
        assert s["dismissal_reason"] is None


# ── Deleted hash persistence ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_deleted_hashes_returns_old_entries(pattern_store):
    """get_deleted_hashes returns all hashes regardless of age."""
    ps, _mock_st = pattern_store
    data = await ps._get_loaded_data()

    fresh = datetime.now(UTC).isoformat()
    old = (datetime.now(UTC) - timedelta(days=90)).isoformat()

    data["deleted_hashes"] = {
        "hash_fresh": {"hash": "hash_fresh", "alias": "Fresh", "deleted_at": fresh},
        "hash_old": {"hash": "hash_old", "alias": "Old", "deleted_at": old},
    }

    result = await ps.get_deleted_hashes()
    assert result == {"hash_fresh", "hash_old"}


@pytest.mark.asyncio
async def test_record_deleted_automation_persists(pattern_store):
    """record_deleted_automation saves the new hash to the store."""
    ps, mock_st = pattern_store

    await ps.record_deleted_automation("hash_abc", "Test Automation")

    data = await ps._get_loaded_data()
    assert "hash_abc" in data["deleted_hashes"]
    assert data["deleted_hashes"]["hash_abc"]["alias"] == "Test Automation"
    assert mock_st.saved_data
