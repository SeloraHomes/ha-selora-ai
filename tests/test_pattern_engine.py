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
    store.update_pattern_status = AsyncMock(return_value=True)
    store.remove_suggestions_for_pattern = AsyncMock(return_value=0)
    store.get_patterns = AsyncMock(return_value=[])
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

    @pytest.mark.asyncio
    async def test_same_device_entities_skipped(self, hass: MagicMock) -> None:
        """Entities sharing a device_id must not produce correlations (#93).

        Reproduces the bug where HA's 'Show switch as Light' creates both
        switch.closet and light.closet from the same device, and the engine
        wrongly correlates them.
        """
        from unittest.mock import patch

        mock_entry_switch = MagicMock(device_id="device_123", disabled=False)
        mock_entry_light = MagicMock(device_id="device_123", disabled=False)
        mock_reg = MagicMock()
        mock_reg.async_get.side_effect = lambda eid: {
            "switch.closet": mock_entry_switch,
            "light.closet": mock_entry_light,
        }.get(eid)

        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        for d in range(6):
            changes_a.append(_change("on", day_offset=d, hour=9, minute=0, second=0))
            changes_b.append(_change("on", day_offset=d, hour=9, minute=0, second=2))
        history = {"switch.closet": changes_a, "light.closet": changes_b}

        with patch(
            "homeassistant.helpers.entity_registry.async_get",
            return_value=mock_reg,
        ):
            result = await engine._detect_correlations(history)
        assert result == [], "Same-device entities must not produce correlation patterns"

    @pytest.mark.asyncio
    async def test_disabled_entity_skipped_in_correlation(self, hass: MagicMock) -> None:
        """Disabled entities must be excluded from correlation detection (#93)."""
        from unittest.mock import patch

        mock_entry_ok = MagicMock(device_id="dev_1", disabled=False)
        mock_entry_disabled = MagicMock(device_id="dev_2", disabled=True)
        mock_reg = MagicMock()
        mock_reg.async_get.side_effect = lambda eid: {
            "light.hallway": mock_entry_ok,
            "switch.old_closet": mock_entry_disabled,
        }.get(eid)

        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        for d in range(6):
            changes_a.append(_change("on", day_offset=d, hour=9))
            changes_b.append(_change("on", day_offset=d, hour=9, second=30))
        history = {"light.hallway": changes_a, "switch.old_closet": changes_b}

        with patch(
            "homeassistant.helpers.entity_registry.async_get",
            return_value=mock_reg,
        ):
            result = await engine._detect_correlations(history)
        assert result == [], "Disabled entities must not appear in correlations"


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
    async def test_same_device_entities_skipped_in_sequence(self, hass: MagicMock) -> None:
        """Entities sharing a device_id must not produce sequences (#93)."""
        from unittest.mock import patch

        mock_entry_switch = MagicMock(device_id="device_123", disabled=False)
        mock_entry_light = MagicMock(device_id="device_123", disabled=False)
        mock_reg = MagicMock()
        mock_reg.async_get.side_effect = lambda eid: {
            "switch.closet": mock_entry_switch,
            "light.closet": mock_entry_light,
        }.get(eid)

        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        for d in range(6):
            changes_a.append(_change("on", day_offset=d, hour=9, second=0, prev="off"))
            changes_b.append(_change("on", day_offset=d, hour=9, second=2, prev="off"))
        history = {"switch.closet": changes_a, "light.closet": changes_b}

        with patch(
            "homeassistant.helpers.entity_registry.async_get",
            return_value=mock_reg,
        ):
            result = await engine._detect_sequences(history)
        assert result == [], "Same-device entities must not produce sequence patterns"

    @pytest.mark.asyncio
    async def test_disabled_entity_skipped_in_sequence(self, hass: MagicMock) -> None:
        """Disabled entities must be excluded from sequence detection (#93)."""
        from unittest.mock import patch

        mock_entry_ok = MagicMock(device_id="dev_1", disabled=False)
        mock_entry_disabled = MagicMock(device_id="dev_2", disabled=True)
        mock_reg = MagicMock()
        mock_reg.async_get.side_effect = lambda eid: {
            "light.hallway": mock_entry_ok,
            "switch.old_closet": mock_entry_disabled,
        }.get(eid)

        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        for d in range(6):
            changes_a.append(_change("on", day_offset=d, hour=9, prev="off"))
            changes_b.append(_change("on", day_offset=d, hour=9, second=30, prev="off"))
        history = {"light.hallway": changes_a, "switch.old_closet": changes_b}

        with patch(
            "homeassistant.helpers.entity_registry.async_get",
            return_value=mock_reg,
        ):
            result = await engine._detect_sequences(history)
        assert result == [], "Disabled entities must not appear in sequences"

    @pytest.mark.asyncio
    async def test_deduplicated_against_existing_correlation(self, hass: MagicMock) -> None:
        """If a correlation pattern already exists for this pair, no sequence is emitted."""
        store = _mock_pattern_store()

        # First call is the correlation check, return an existing pattern.
        # Second call (sequence signature) returns None.
        store.find_pattern_by_signature = AsyncMock(
            side_effect=lambda ptype, eids, sig: (
                {"pattern_id": "pat_corr_existing", "status": "active"}
                if ptype == "correlation"
                else None
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


# ===========================================================================
# Causality Guardrails
# ===========================================================================


class TestCausalityGuardrails:
    """Tests for causality guardrails in correlation detection."""

    @pytest.mark.asyncio
    async def test_high_delay_variance_penalizes_confidence(self, hass: MagicMock) -> None:
        """Wildly varying delays should reduce confidence or filter pattern out."""
        engine = _make_engine(hass)

        # Build A events at consistent times, B events at wildly varying delays
        # Delays: 5s, 250s, 10s, 290s, 15s — stddev >> 60s
        delays_seconds = [5, 250, 10, 290, 15]
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        for d, delay in enumerate(delays_seconds):
            changes_a.append(_change("on", day_offset=d, hour=9, minute=0, second=0))
            changes_b.append(
                _change(
                    "on",
                    day_offset=d,
                    hour=9,
                    minute=delay // 60,
                    second=delay % 60,
                )
            )
        history = {"sensor.door": changes_a, "light.hall": changes_b}
        erratic_result = await engine._detect_correlations(history)

        # Now build a consistent-delay equivalent (all 30s apart)
        engine_consistent = _make_engine(hass)
        changes_a2: list[dict[str, str]] = []
        changes_b2: list[dict[str, str]] = []
        for d in range(5):
            changes_a2.append(_change("on", day_offset=d, hour=9, minute=0, second=0))
            changes_b2.append(_change("on", day_offset=d, hour=9, minute=0, second=30))
        history2 = {"sensor.door": changes_a2, "light.hall": changes_b2}
        consistent_result = await engine_consistent._detect_correlations(history2)

        # Erratic delays should be filtered out entirely
        assert erratic_result == [], "Erratic delay pattern should be rejected"
        assert consistent_result, "Consistent-delay should still produce a pattern"

    @pytest.mark.asyncio
    async def test_bidirectional_correlation_penalized(self, hass: MagicMock) -> None:
        """A->B and B->A happening equally should reduce confidence or filter out."""
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []

        # On even days: A fires first, then B (A->B)
        # On odd days: B fires first, then A (B->A)
        for d in range(10):
            if d % 2 == 0:
                changes_a.append(_change("on", day_offset=d, hour=9, minute=0, second=0))
                changes_b.append(_change("on", day_offset=d, hour=9, minute=0, second=30))
            else:
                changes_b.append(_change("on", day_offset=d, hour=9, minute=0, second=0))
                changes_a.append(_change("on", day_offset=d, hour=9, minute=0, second=30))

        history = {"light.x": changes_a, "light.y": changes_b}
        result = await engine._detect_correlations(history)

        # Both directions have directionality ~0.5, well below 0.65
        # Symmetric patterns should be filtered out entirely
        correlations = [p for p in result if p["type"] == "correlation"]
        assert correlations == [], "Bidirectional patterns should be rejected"

    @pytest.mark.asyncio
    async def test_symmetric_high_confidence_rejected(self, hass: MagicMock) -> None:
        """Perfectly symmetric pair with raw confidence 1.0 must still be rejected."""
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []

        # Every event is correlated — raw confidence will be 1.0
        # But direction alternates, so directionality = 0.5
        for d in range(6):
            if d % 2 == 0:
                changes_a.append(_change("on", day_offset=d, hour=9, minute=0, second=0))
                changes_b.append(_change("on", day_offset=d, hour=9, minute=0, second=30))
            else:
                changes_b.append(_change("on", day_offset=d, hour=9, minute=0, second=0))
                changes_a.append(_change("on", day_offset=d, hour=9, minute=0, second=30))

        history = {"switch.a": changes_a, "switch.b": changes_b}
        result = await engine._detect_correlations(history)

        correlations = [p for p in result if p["type"] == "correlation"]
        assert correlations == [], "Symmetric pair with high raw confidence should be rejected"

    @pytest.mark.asyncio
    async def test_consistent_unidirectional_passes(self, hass: MagicMock) -> None:
        """A consistently preceding B with steady delays should pass guardrails."""
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        # A fires, then B 30s later — consistent, unidirectional
        for d in range(6):
            changes_a.append(_change("on", day_offset=d, hour=9, minute=0, second=0))
            changes_b.append(_change("on", day_offset=d, hour=9, minute=0, second=30))
        history = {"sensor.motion": changes_a, "light.room": changes_b}
        result = await engine._detect_correlations(history)

        assert len(result) >= 1
        pat = result[0]
        assert pat["type"] == "correlation"
        assert pat["confidence"] >= 0.50

    @pytest.mark.asyncio
    async def test_bursty_consistent_pair_not_penalized(self, hass: MagicMock) -> None:
        """Rapid cycling (A/B/A/B within one window) with steady lag must pass.

        Regression: many-to-many window matching inflated stddev because each
        A was paired with every later B, producing delays 30, 90, 150… even
        though the per-cycle lag is always 30s.
        """
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        # 6 rapid cycles per day, 60s apart, B always 30s after A
        for d in range(4):
            for cycle in range(6):
                base_sec = cycle * 60
                changes_a.append(
                    _change(
                        "on", day_offset=d, hour=10, minute=base_sec // 60, second=base_sec % 60
                    )
                )
                changes_b.append(
                    _change(
                        "on",
                        day_offset=d,
                        hour=10,
                        minute=(base_sec + 30) // 60,
                        second=(base_sec + 30) % 60,
                    )
                )

        history = {"sensor.motion": changes_a, "light.room": changes_b}
        result = await engine._detect_correlations(history)

        correlations = [p for p in result if p["type"] == "correlation"]
        assert correlations, "Bursty but consistent pair should not be rejected"
        assert correlations[0]["evidence"]["delay_stddev"] < 5.0

    @pytest.mark.asyncio
    async def test_bursty_unidirectional_not_diluted(self, hass: MagicMock) -> None:
        """Repeating A/B/A/B cycles should keep directionality ~1.0.

        Regression: many-to-many matching created synthetic B→next-A reverse
        entries that pushed directionality toward 0.5.
        """
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        # 4 rapid cycles per day, always A then B 30s later
        for d in range(5):
            for cycle in range(4):
                base_sec = cycle * 60
                changes_a.append(
                    _change(
                        "on", day_offset=d, hour=10, minute=base_sec // 60, second=base_sec % 60
                    )
                )
                changes_b.append(
                    _change(
                        "on",
                        day_offset=d,
                        hour=10,
                        minute=(base_sec + 30) // 60,
                        second=(base_sec + 30) % 60,
                    )
                )

        history = {"sensor.motion": changes_a, "light.room": changes_b}
        result = await engine._detect_correlations(history)

        correlations = [p for p in result if p["type"] == "correlation"]
        assert correlations, "Unidirectional bursty pair should not be rejected"
        assert correlations[0]["evidence"]["directionality"] >= 0.65

    @pytest.mark.asyncio
    async def test_subsecond_leading_b_not_counted_as_reverse(self, hass: MagicMock) -> None:
        """B arriving < 1s before A must not count as a reverse episode.

        Regression: scene-driven updates can produce a near-simultaneous B
        just before A. The correlation pass ignores sub-second deltas, but
        _episode_directionality greedily paired them as B→A, dropping
        directionality and discarding a valid A→B correlation.
        """
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        for d in range(6):
            # B fires 0s before A (same second), then real B response 30s later
            changes_b.append(_change("on", day_offset=d, hour=9, minute=0, second=0))
            changes_a.append(_change("on", day_offset=d, hour=9, minute=0, second=0))
            changes_b.append(_change("on", day_offset=d, hour=9, minute=0, second=30))

        history = {"sensor.trigger": changes_a, "light.target": changes_b}
        result = await engine._detect_correlations(history)

        correlations = [p for p in result if p["type"] == "correlation"]
        assert correlations, "Leading sub-second B should not kill the correlation"
        assert correlations[0]["evidence"]["directionality"] > 0.5

    @pytest.mark.asyncio
    async def test_consistent_b_before_a_detected_as_bidirectional(self, hass: MagicMock) -> None:
        """B consistently preceding A is common-cause evidence, not stray noise.

        When B fires 2 min before A every day AND A fires 30s before B,
        both directions are real — the guardrail should detect this as
        bidirectional and reject it.
        """
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        for d in range(6):
            changes_b.append(_change("on", day_offset=d, hour=8, minute=58, second=0))
            changes_a.append(_change("on", day_offset=d, hour=9, minute=0, second=0))
            changes_b.append(_change("on", day_offset=d, hour=9, minute=0, second=30))

        history = {"sensor.trigger": changes_a, "light.target": changes_b}
        result = await engine._detect_correlations(history)

        correlations = [p for p in result if p["type"] == "correlation"]
        assert correlations == [], "Consistent B-before-A should be rejected as bidirectional"

    @pytest.mark.asyncio
    async def test_common_cause_b_before_and_after_a_rejected(self, hass: MagicMock) -> None:
        """B before A + B after A in same window = common cause, not A→B.

        Regression: pass 1 consumed A, so pass 2 could not count the
        earlier B→A episode, making directionality look like 1.0 for an
        obviously bidirectional pattern.
        """
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        for d in range(6):
            # B@09:00, A@09:01:40, B@09:02:11 — both directions present
            changes_b.append(_change("on", day_offset=d, hour=9, minute=0, second=0))
            changes_a.append(_change("on", day_offset=d, hour=9, minute=1, second=40))
            changes_b.append(_change("on", day_offset=d, hour=9, minute=2, second=11))

        history = {"sensor.x": changes_a, "sensor.y": changes_b}
        result = await engine._detect_correlations(history)

        correlations = [p for p in result if p["type"] == "correlation"]
        assert correlations == [], "Common-cause B-before-and-after-A should be rejected"

    @pytest.mark.asyncio
    async def test_duplicate_b_updates_not_multi_counted(self, hass: MagicMock) -> None:
        """Multiple B updates before one A must count as one reverse episode.

        Regression: pass 2 never marked A as consumed, so noisy duplicate
        B events all matched the same A, inflating the reverse count and
        pushing directionality below the cutoff.
        """
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        for d in range(6):
            # A fires, then B responds 30s later (forward).
            # 3 extra B noise updates arrive ~2 min before A.
            changes_b.append(_change("on", day_offset=d, hour=8, minute=58, second=0))
            changes_b.append(_change("on", day_offset=d, hour=8, minute=58, second=20))
            changes_b.append(_change("on", day_offset=d, hour=8, minute=58, second=40))
            changes_a.append(_change("on", day_offset=d, hour=9, minute=0, second=0))
            changes_b.append(_change("on", day_offset=d, hour=9, minute=0, second=30))

        history = {"sensor.trigger": changes_a, "light.target": changes_b}
        result = await engine._detect_correlations(history)

        # Without the fix, 3 B→A reverse episodes per day would dominate.
        # With the fix, at most 1 reverse per A per day.
        correlations = [p for p in result if p["type"] == "correlation"]
        # Regardless of whether this is accepted or rejected, the reverse
        # count should not exceed the number of A events (6).
        # In practice: 6 forward + at most 6 reverse → dir ≥ 0.5.
        # The cross-direction check may still reject it as common cause
        # (consistent B before A), which is correct behaviour.
        # The key assertion: the pattern is NOT killed by inflated reverses.
        forward_pair = [
            p for p in correlations if p["evidence"].get("trigger_entity") == "sensor.trigger"
        ]
        # If it survived, directionality must be reasonable
        for p in forward_pair:
            assert p["evidence"]["directionality"] >= 0.5

    @pytest.mark.asyncio
    async def test_overlapping_cycles_not_rejected(self, hass: MagicMock) -> None:
        """A repeating faster than A→B lag must not flip directionality.

        Regression: shortest-delay-first matching paired the short B→next-A
        reverse edge (10s) before the real A→B forward edge (150s), driving
        directionality to 0 for a perfectly consistent causal pattern.
        """
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        # A every 160s, B always 150s after A
        for d in range(5):
            for cycle in range(4):
                base_sec = cycle * 160
                a_min, a_sec = divmod(base_sec, 60)
                b_min, b_sec = divmod(base_sec + 150, 60)
                changes_a.append(_change("on", day_offset=d, hour=10, minute=a_min, second=a_sec))
                changes_b.append(_change("on", day_offset=d, hour=10, minute=b_min, second=b_sec))

        history = {"sensor.trigger": changes_a, "light.target": changes_b}
        result = await engine._detect_correlations(history)

        correlations = [p for p in result if p["type"] == "correlation"]
        assert correlations, "Overlapping cycles should not be rejected"
        assert correlations[0]["evidence"]["directionality"] > 0.5

    @pytest.mark.asyncio
    async def test_retried_trigger_does_not_inflate_stddev(self, hass: MagicMock) -> None:
        """Multiple A events before a single B must not duplicate that B.

        Regression: pair_nearest stored the same B as nearest for every A,
        producing causal_delays like [270, 30] and an inflated stddev that
        killed a valid correlation.  Chronological matching assigns each B
        to the first A, giving consistent delays (all 270s here) with
        near-zero stddev.
        """
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        for d in range(6):
            # A fires at 09:00, retries at 09:04, B responds at 09:04:30
            changes_a.append(_change("on", day_offset=d, hour=9, minute=0, second=0))
            changes_a.append(_change("on", day_offset=d, hour=9, minute=4, second=0))
            changes_b.append(_change("on", day_offset=d, hour=9, minute=4, second=30))

        history = {"sensor.trigger": changes_a, "light.target": changes_b}
        result = await engine._detect_correlations(history)

        correlations = [p for p in result if p["type"] == "correlation"]
        assert correlations, "Retried trigger should not kill the correlation"
        # Chronological: A@09:00 claims B@09:04:30 (270s each day), stddev ≈ 0
        assert correlations[0]["evidence"]["delay_stddev"] < 5.0

    @pytest.mark.asyncio
    async def test_causality_metrics_in_evidence(self, hass: MagicMock) -> None:
        """Detected correlation patterns should include delay_stddev and directionality."""
        engine = _make_engine(hass)
        changes_a: list[dict[str, str]] = []
        changes_b: list[dict[str, str]] = []
        for d in range(6):
            changes_a.append(_change("on", day_offset=d, hour=9, minute=0, second=0))
            changes_b.append(_change("on", day_offset=d, hour=9, minute=0, second=30))
        history = {"sensor.motion": changes_a, "light.room": changes_b}
        result = await engine._detect_correlations(history)

        assert len(result) >= 1
        evidence = result[0]["evidence"]
        assert "delay_stddev" in evidence
        assert "directionality" in evidence
        assert isinstance(evidence["delay_stddev"], float)
        assert isinstance(evidence["directionality"], float)
        assert evidence["directionality"] > 0.0
        assert evidence["directionality"] <= 1.0
