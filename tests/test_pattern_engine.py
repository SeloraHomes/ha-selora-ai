"""Tests for the PatternEngine module — pure functions and async detectors."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.selora_ai.pattern_engine import (
    PatternEngine,
    _count_distinct_days,
    _parse_timestamp,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_DATE = datetime(2025, 1, 6, tzinfo=UTC)  # Monday


def _ts(day_offset: int = 0, hour: int = 18, minute: int = 0, second: int = 0) -> str:
    """Return an ISO timestamp offset from BASE_DATE."""
    dt = BASE_DATE + timedelta(days=day_offset, hours=hour, minutes=minute, seconds=second)
    return dt.isoformat()


def _change(
    state: str,
    day_offset: int = 0,
    hour: int = 18,
    minute: int = 0,
    second: int = 0,
    prev: str = "off",
) -> dict[str, str]:
    """Build a single state-change dict."""
    return {
        "ts": _ts(day_offset, hour, minute, second),
        "state": state,
        "prev": prev,
    }


def _mock_pattern_store() -> MagicMock:
    """Return a MagicMock that satisfies PatternStore's interface for the engine."""
    store = MagicMock()
    store.get_all_history = AsyncMock(return_value={})
    store.find_pattern_by_signature = AsyncMock(return_value=None)
    store.save_pattern = AsyncMock(return_value="pat_new_001")
    store._get_loaded_data = AsyncMock(return_value={"meta": {}})
    store._save = AsyncMock()
    return store


def _make_engine(hass: MagicMock, store: MagicMock | None = None) -> PatternEngine:
    """Create a PatternEngine with a mocked hass and store."""
    return PatternEngine(hass, store or _mock_pattern_store())


# ===========================================================================
# _parse_timestamp
# ===========================================================================


class TestParseTimestamp:
    """Tests for the _parse_timestamp helper."""

    def test_valid_iso_string(self) -> None:
        result = _parse_timestamp("2025-01-05T18:00:00+00:00")
        assert isinstance(result, datetime)
        assert result.hour == 18

    def test_valid_naive_iso_string(self) -> None:
        result = _parse_timestamp("2025-03-15T09:30:00")
        assert isinstance(result, datetime)
        assert result.minute == 30

    def test_invalid_string_returns_none(self) -> None:
        assert _parse_timestamp("not-a-date") is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_timestamp("") is None

    def test_none_input_returns_none(self) -> None:
        # None triggers TypeError which is caught
        assert _parse_timestamp(None) is None  # type: ignore[arg-type]


# ===========================================================================
# _count_distinct_days
# ===========================================================================


class TestCountDistinctDays:
    """Tests for the _count_distinct_days helper."""

    def test_empty_list(self) -> None:
        assert _count_distinct_days([]) == 0

    def test_single_entry(self) -> None:
        changes = [_change("on", day_offset=0)]
        assert _count_distinct_days(changes) == 1

    def test_multiple_entries_same_day(self) -> None:
        changes = [
            _change("on", day_offset=0, hour=8),
            _change("off", day_offset=0, hour=12),
            _change("on", day_offset=0, hour=20),
        ]
        assert _count_distinct_days(changes) == 1

    def test_entries_on_three_different_days(self) -> None:
        changes = [
            _change("on", day_offset=0),
            _change("on", day_offset=2),
            _change("on", day_offset=5),
        ]
        assert _count_distinct_days(changes) == 3

    def test_invalid_timestamps_ignored(self) -> None:
        changes = [
            {"ts": "bad", "state": "on", "prev": "off"},
            _change("on", day_offset=1),
        ]
        assert _count_distinct_days(changes) == 1


# ===========================================================================
# _detect_time_patterns
# ===========================================================================


class TestDetectTimePatterns:
    """Tests for PatternEngine._detect_time_patterns."""

    @pytest.mark.asyncio
    async def test_fewer_than_3_changes_no_patterns(self, hass: MagicMock) -> None:
        engine = _make_engine(hass)
        history = {
            "light.kitchen": [
                _change("on", day_offset=0),
                _change("on", day_offset=1),
            ]
        }
        result = await engine._detect_time_patterns(history)
        assert result == []

    @pytest.mark.asyncio
    async def test_fewer_than_3_distinct_days_no_patterns(self, hass: MagicMock) -> None:
        engine = _make_engine(hass)
        # 4 changes but only on 2 distinct days
        history = {
            "light.kitchen": [
                _change("on", day_offset=0, hour=18),
                _change("off", day_offset=0, hour=22),
                _change("on", day_offset=1, hour=18),
                _change("off", day_offset=1, hour=22),
            ]
        }
        result = await engine._detect_time_patterns(history)
        assert result == []

    @pytest.mark.asyncio
    async def test_recurring_pattern_detected(self, hass: MagicMock) -> None:
        """3 occurrences on 3 distinct weekdays at the same 15-min slot."""
        engine = _make_engine(hass)
        # Mon, Tue, Wed at 18:00 — all weekdays, same slot (72)
        history = {
            "light.living_room": [
                _change("on", day_offset=0, hour=18, minute=0),  # Mon
                _change("on", day_offset=1, hour=18, minute=5),  # Tue (same slot)
                _change("on", day_offset=2, hour=18, minute=14),  # Wed (same slot)
            ]
        }
        result = await engine._detect_time_patterns(history)
        assert len(result) == 1
        pat = result[0]
        assert pat["type"] == "time_based"
        assert pat["entity_ids"] == ["light.living_room"]
        assert pat["evidence"]["target_state"] == "on"
        assert pat["evidence"]["is_weekday"] is True
        assert pat["confidence"] >= 0.50

    @pytest.mark.asyncio
    async def test_skips_unavailable_states(self, hass: MagicMock) -> None:
        engine = _make_engine(hass)
        history = {
            "sensor.temp": [
                _change("unavailable", day_offset=0),
                _change("unavailable", day_offset=1),
                _change("unavailable", day_offset=2),
                _change("unavailable", day_offset=3),
            ]
        }
        result = await engine._detect_time_patterns(history)
        assert result == []

    @pytest.mark.asyncio
    async def test_low_confidence_filtered(self, hass: MagicMock) -> None:
        """Pattern present on 3 of 10 days → confidence 0.30 < 0.50 → filtered."""
        engine = _make_engine(hass)
        changes: list[dict[str, str]] = []
        # 3 occurrences at 18:00 on days 0, 1, 2
        for d in range(3):
            changes.append(_change("on", day_offset=d, hour=18))
        # 7 more on different days at different times to inflate total_days
        for d in range(3, 10):
            changes.append(_change("off", day_offset=d, hour=10))
        history = {"light.hall": changes}
        result = await engine._detect_time_patterns(history)
        # The "on" at 18:00 bucket has 3 distinct days out of 10 total → 0.30
        on_patterns = [p for p in result if p["evidence"]["target_state"] == "on"]
        assert on_patterns == []

    @pytest.mark.asyncio
    async def test_existing_pattern_not_returned_again(self, hass: MagicMock) -> None:
        """If the store already has this pattern, it's updated but not in the return list."""
        store = _mock_pattern_store()
        store.find_pattern_by_signature = AsyncMock(return_value={"pattern_id": "pat_existing"})
        engine = _make_engine(hass, store)
        history = {
            "light.living_room": [
                _change("on", day_offset=0, hour=18),
                _change("on", day_offset=1, hour=18),
                _change("on", day_offset=2, hour=18),
            ]
        }
        result = await engine._detect_time_patterns(history)
        assert result == []
        # But save_pattern was still called to update it
        store.save_pattern.assert_called()


# ===========================================================================
# _detect_correlations
# ===========================================================================


class TestDetectCorrelations:
    """Tests for PatternEngine._detect_correlations."""

    @pytest.mark.asyncio
    async def test_timeline_too_short_returns_empty(self, hass: MagicMock) -> None:
        engine = _make_engine(hass)
        # Only 6 events total, need at least 8 (MIN_COOCCURRENCES * 2)
        history = {
            "light.a": [
                _change("on", day_offset=0, hour=10),
                _change("on", day_offset=1, hour=10),
                _change("on", day_offset=2, hour=10),
            ],
            "light.b": [
                _change("on", day_offset=0, hour=10, minute=1),
                _change("on", day_offset=1, hour=10, minute=1),
                _change("on", day_offset=2, hour=10, minute=1),
            ],
        }
        result = await engine._detect_correlations(history)
        assert result == []

    @pytest.mark.asyncio
    async def test_correlated_pair_detected(self, hass: MagicMock) -> None:
        """Two entities with 5 co-occurrences within the 5-min window."""
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        for d in range(5):
            changes_a.append(_change("on", day_offset=d, hour=9, minute=0, second=0))
            changes_b.append(_change("on", day_offset=d, hour=9, minute=0, second=30))
        history = {"binary_sensor.door": changes_a, "light.hallway": changes_b}
        result = await engine._detect_correlations(history)
        assert len(result) >= 1
        pat = result[0]
        assert pat["type"] == "correlation"
        assert "binary_sensor.door" in pat["entity_ids"]
        assert "light.hallway" in pat["entity_ids"]
        assert pat["confidence"] >= 0.50

    @pytest.mark.asyncio
    async def test_same_entity_pair_ignored(self, hass: MagicMock) -> None:
        """Events from the same entity should not form a pair."""
        engine = _make_engine(hass)
        changes: list[dict[str, str]] = []
        for d in range(10):
            changes.append(_change("on", day_offset=d, hour=9, second=0))
            changes.append(_change("off", day_offset=d, hour=9, second=30))
        history = {"light.only": changes}
        result = await engine._detect_correlations(history)
        assert result == []

    @pytest.mark.asyncio
    async def test_sub_second_events_ignored(self, hass: MagicMock) -> None:
        """Events less than 1 second apart are ignored (delta < 1)."""
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        for d in range(6):
            # Exact same timestamp → delta == 0
            changes_a.append(_change("on", day_offset=d, hour=9, second=0))
            changes_b.append(_change("on", day_offset=d, hour=9, second=0))
        history = {"light.a": changes_a, "light.b": changes_b}
        result = await engine._detect_correlations(history)
        assert result == []

    @pytest.mark.asyncio
    async def test_outside_window_not_counted(self, hass: MagicMock) -> None:
        """Events more than 5 minutes apart should not form correlations."""
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        for d in range(6):
            changes_a.append(_change("on", day_offset=d, hour=9, minute=0))
            # 6 minutes later — outside the 5-min window
            changes_b.append(_change("on", day_offset=d, hour=9, minute=6))
        history = {"light.a": changes_a, "light.b": changes_b}
        result = await engine._detect_correlations(history)
        assert result == []

    @pytest.mark.asyncio
    async def test_low_confidence_filtered(self, hass: MagicMock) -> None:
        """4 co-occurrences but many individual changes → confidence < 0.50."""
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        # 4 co-occurrences
        for d in range(4):
            changes_a.append(_change("on", day_offset=d, hour=9, second=0))
            changes_b.append(_change("on", day_offset=d, hour=9, second=30))
        # Add many extra changes to light.a to push denominator up
        for d in range(4, 20):
            changes_a.append(_change("off", day_offset=d, hour=12))
        # Extra changes to b too to ensure enough timeline entries
        for d in range(4, 12):
            changes_b.append(_change("off", day_offset=d, hour=14))
        history = {"light.a": changes_a, "light.b": changes_b}
        result = await engine._detect_correlations(history)
        # min(a_count=20, b_count=12) = 12, confidence = 4/12 ≈ 0.33 < 0.50
        corr = [p for p in result if p["type"] == "correlation"]
        assert corr == []

    @pytest.mark.asyncio
    async def test_unavailable_states_skipped(self, hass: MagicMock) -> None:
        """Unavailable / unknown states should be excluded from the timeline."""
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        for d in range(6):
            changes_a.append(_change("unavailable", day_offset=d, hour=9))
            changes_b.append(_change("on", day_offset=d, hour=9, second=30))
        history = {"light.a": changes_a, "light.b": changes_b}
        result = await engine._detect_correlations(history)
        assert result == []


# ===========================================================================
# _detect_sequences
# ===========================================================================


class TestDetectSequences:
    """Tests for PatternEngine._detect_sequences."""

    @pytest.mark.asyncio
    async def test_timeline_too_short_returns_empty(self, hass: MagicMock) -> None:
        engine = _make_engine(hass)
        history = {
            "light.a": [_change("on", day_offset=0, hour=9, prev="off")],
            "light.b": [_change("on", day_offset=0, hour=9, second=30, prev="off")],
        }
        result = await engine._detect_sequences(history)
        assert result == []

    @pytest.mark.asyncio
    async def test_sequence_detected(self, hass: MagicMock) -> None:
        """A(off→on) followed by B turning on, 5 times → sequence pattern."""
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        for d in range(5):
            changes_a.append(_change("on", day_offset=d, hour=8, second=0, prev="off"))
            changes_b.append(_change("on", day_offset=d, hour=8, second=10, prev="off"))
        history = {"light.living": changes_a, "cover.blinds": changes_b}
        result = await engine._detect_sequences(history)
        assert len(result) >= 1
        pat = result[0]
        assert pat["type"] == "sequence"
        assert pat["evidence"]["trigger_from"] == "off"
        assert pat["evidence"]["trigger_to"] == "on"

    @pytest.mark.asyncio
    async def test_prev_equals_state_skipped(self, hass: MagicMock) -> None:
        """When prev == state, the transition is a no-op and should be skipped."""
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        for d in range(6):
            # prev == state → should be ignored as trigger
            changes_a.append(_change("on", day_offset=d, hour=8, second=0, prev="on"))
            changes_b.append(_change("on", day_offset=d, hour=8, second=10, prev="off"))
        history = {"light.a": changes_a, "light.b": changes_b}
        result = await engine._detect_sequences(history)
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_prev_skipped(self, hass: MagicMock) -> None:
        """When prev is empty, the event cannot be a meaningful trigger."""
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        for d in range(6):
            changes_a.append(_change("on", day_offset=d, hour=8, second=0, prev=""))
            changes_b.append(_change("on", day_offset=d, hour=8, second=10, prev="off"))
        history = {"light.a": changes_a, "light.b": changes_b}
        result = await engine._detect_sequences(history)
        assert result == []

    @pytest.mark.asyncio
    async def test_deduplicated_against_existing_correlation(self, hass: MagicMock) -> None:
        """If a correlation pattern already exists for this pair, no sequence is emitted."""
        store = _mock_pattern_store()

        # First call is the correlation check, return an existing pattern.
        # Second call (sequence signature) returns None.
        store.find_pattern_by_signature = AsyncMock(
            side_effect=lambda ptype, eids, sig: (
                {"pattern_id": "pat_corr_existing"} if ptype == "correlation" else None
            )
        )
        engine = _make_engine(hass, store)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        for d in range(6):
            changes_a.append(_change("on", day_offset=d, hour=8, second=0, prev="off"))
            changes_b.append(_change("on", day_offset=d, hour=8, second=10, prev="off"))
        history = {"light.a": changes_a, "light.b": changes_b}
        result = await engine._detect_sequences(history)
        assert result == []

    @pytest.mark.asyncio
    async def test_sequence_low_confidence_filtered(self, hass: MagicMock) -> None:
        """4 co-occurrences out of many A-events → confidence < 0.50."""
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        for d in range(4):
            changes_a.append(_change("on", day_offset=d, hour=8, second=0, prev="off"))
            changes_b.append(_change("on", day_offset=d, hour=8, second=10, prev="off"))
        # Inflate A's history so confidence = 4/20 = 0.20
        for d in range(4, 20):
            changes_a.append(_change("off", day_offset=d, hour=12, prev="on"))
        # Need extra B entries to keep timeline above 8
        for d in range(4, 10):
            changes_b.append(_change("off", day_offset=d, hour=14, prev="on"))
        history = {"light.a": changes_a, "light.b": changes_b}
        result = await engine._detect_sequences(history)
        seq = [p for p in result if p["type"] == "sequence"]
        assert seq == []
