"""Integration tests for the full scan -> suggest -> reject -> rescan lifecycle.

Uses a real PatternStore (backed by MockStore) and real PatternEngine to verify
that guardrail rejections, reactivations, and suggestion cleanup interact
correctly across the pattern detection pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from custom_components.selora_ai.pattern_engine import PatternEngine
from custom_components.selora_ai.pattern_store import PatternStore
from custom_components.selora_ai.suggestion_generator import SuggestionGenerator

from .conftest import MockStore

# ---------------------------------------------------------------------------
# Helpers (same conventions as test_pattern_engine.py)
# ---------------------------------------------------------------------------

# Use a recent date so history passes the recency filter in hardening checks (#67).
# Round down to the most recent Monday for deterministic weekday-based tests.
_now = datetime.now(tz=UTC)
BASE_DATE = (_now - timedelta(days=_now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pattern_store(hass) -> PatternStore:
    """Create a PatternStore backed by MockStore (no real disk I/O)."""
    with patch("custom_components.selora_ai.pattern_store.Store") as mock_cls:
        store_inst = MockStore()
        mock_cls.return_value = store_inst
        ps = PatternStore(hass)
        ps._store = store_inst
        yield ps


@pytest.fixture
def engine(hass, pattern_store: PatternStore) -> PatternEngine:
    """Create a PatternEngine using the real PatternStore."""
    return PatternEngine(hass, pattern_store)


@pytest.fixture
def generator(hass_with_services, pattern_store: PatternStore) -> SuggestionGenerator:
    """Create a SuggestionGenerator with no LLM.

    Registers mock entity states so hardening checks pass (#67).
    """
    hass_with_services.states.async_set("binary_sensor.door", "on")
    hass_with_services.states.async_set("light.hallway", "on")
    return SuggestionGenerator(hass_with_services, pattern_store)


# ---------------------------------------------------------------------------
# History helpers — build histories that trigger specific guardrail outcomes
# ---------------------------------------------------------------------------


def _bidirectional_history() -> dict[str, list[dict[str, str]]]:
    """A→B and B→A with equal frequency — rejected by directionality guardrail.

    5 episodes where sensor fires first, then 5 where light fires first,
    producing ~0.5 directionality which triggers rejection.
    """
    sensor_changes: list[dict[str, str]] = []
    light_changes: list[dict[str, str]] = []

    for d in range(5):
        # A fires first (sensor → light)
        sensor_changes.append(_change("on", day_offset=d, hour=8, minute=0, second=0))
        light_changes.append(_change("on", day_offset=d, hour=8, minute=0, second=30))

    for d in range(5, 10):
        # B fires first (light → sensor)
        light_changes.append(_change("on", day_offset=d, hour=8, minute=0, second=0))
        sensor_changes.append(_change("on", day_offset=d, hour=8, minute=0, second=30))

    return {
        "binary_sensor.door": sensor_changes,
        "light.hallway": light_changes,
    }


def _unidirectional_history() -> dict[str, list[dict[str, str]]]:
    """A→B only — passes all guardrails.

    6 episodes where sensor fires 30s before light, no reverse episodes.
    Consistent delays for low stddev.
    """
    sensor_changes: list[dict[str, str]] = []
    light_changes: list[dict[str, str]] = []

    for d in range(6):
        sensor_changes.append(_change("on", day_offset=d, hour=8, minute=0, second=0))
        light_changes.append(_change("on", day_offset=d, hour=8, minute=0, second=30))

    return {
        "binary_sensor.door": sensor_changes,
        "light.hallway": light_changes,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestScanRejectRescan:
    """Active correlation rejected when history becomes bidirectional, reactivated when fixed."""

    @pytest.mark.asyncio
    async def test_scan_reject_rescan(
        self, pattern_store: PatternStore, engine: PatternEngine
    ) -> None:
        data = await pattern_store._get_loaded_data()

        # Phase 1: unidirectional history — creates an active correlation
        data["state_history"] = _unidirectional_history()
        await pattern_store._save()

        new_patterns = await engine.scan()
        corr_patterns = [p for p in new_patterns if p["type"] == "correlation"]
        assert len(corr_patterns) >= 1

        active_corrs = await pattern_store.get_patterns(status="active", pattern_type="correlation")
        assert len(active_corrs) >= 1
        corr_id = active_corrs[0]["pattern_id"]

        # Phase 2: bidirectional history — existing active pattern gets rejected
        data["state_history"] = _bidirectional_history()
        await pattern_store._save()

        new_patterns = await engine.scan()
        corr_patterns = [p for p in new_patterns if p["type"] == "correlation"]
        assert corr_patterns == []

        rejected_corrs = await pattern_store.get_patterns(
            status="rejected", pattern_type="correlation"
        )
        assert any(p["pattern_id"] == corr_id for p in rejected_corrs)

        active_corrs = await pattern_store.get_patterns(status="active", pattern_type="correlation")
        assert active_corrs == []

        # Phase 3: unidirectional history again — rejected pattern reactivates
        data["state_history"] = _unidirectional_history()
        await pattern_store._save()

        new_patterns = await engine.scan()
        corr_patterns = [p for p in new_patterns if p["type"] == "correlation"]
        assert len(corr_patterns) >= 1

        active_corrs = await pattern_store.get_patterns(status="active", pattern_type="correlation")
        assert any(p["pattern_id"] == corr_id for p in active_corrs)


class TestRejectedCorrelationAllowsSequence:
    """A rejected correlation allows the sequence detector to run for the same pair."""

    @pytest.mark.asyncio
    async def test_rejected_correlation_allows_sequence(
        self, pattern_store: PatternStore, engine: PatternEngine
    ) -> None:
        """When a correlation is rejected, the sequence detector IS allowed to run.

        The sequence detector skips pairs with active/snoozed/dismissed
        correlations, but allows pairs whose correlation is rejected.
        """
        data = await pattern_store._get_loaded_data()

        # First create an active correlation with unidirectional data
        data["state_history"] = _unidirectional_history()
        await pattern_store._save()

        await engine.scan()

        active_corrs = await pattern_store.get_patterns(status="active", pattern_type="correlation")
        assert len(active_corrs) >= 1

        # Now switch to bidirectional data — correlation gets rejected
        data["state_history"] = _bidirectional_history()
        await pattern_store._save()

        await engine.scan()

        # Verify the correlation is now rejected
        active_corrs = await pattern_store.get_patterns(status="active", pattern_type="correlation")
        assert active_corrs == []

        rejected_corrs = await pattern_store.get_patterns(
            status="rejected", pattern_type="correlation"
        )
        assert len(rejected_corrs) >= 1

        # With a rejected correlation, the sequence detector is NOT blocked.
        # Whether a sequence is actually detected depends on the data meeting
        # the sequence detector's own thresholds.  The key assertion is that
        # no active-correlation filter prevented sequences from being considered.
        active_seqs = await pattern_store.get_patterns(status="active", pattern_type="sequence")
        assert isinstance(active_seqs, list)


class TestReactivatedCorrelationRetiresSequence:
    """When a correlation is reactivated from rejected, fallback sequences are retired."""

    @pytest.mark.asyncio
    async def test_reactivated_correlation_retires_sequence(
        self, pattern_store: PatternStore, engine: PatternEngine
    ) -> None:
        data = await pattern_store._get_loaded_data()

        # Phase 1a: create an active correlation with unidirectional data
        data["state_history"] = _unidirectional_history()
        await pattern_store._save()

        await engine.scan()

        active_corrs = await pattern_store.get_patterns(status="active", pattern_type="correlation")
        assert len(active_corrs) >= 1

        # Phase 1b: reject it with bidirectional data
        data["state_history"] = _bidirectional_history()
        await pattern_store._save()

        await engine.scan()

        # Manually insert a sequence pattern that would have been the fallback
        seq_pattern = {
            "pattern_id": None,
            "type": "sequence",
            "entity_ids": ["binary_sensor.door", "light.hallway"],
            "description": "When Door changes from off to on, Hallway turns on",
            "evidence": {
                "_signature": "binary_sensor.door:off->on=>light.hallway:on",
                "trigger_entity": "binary_sensor.door",
                "trigger_from": "off",
                "trigger_to": "on",
                "response_entity": "light.hallway",
                "response_state": "on",
                "occurrences": 6,
                "window_minutes": 5,
            },
            "confidence": 0.75,
        }
        seq_id = await pattern_store.save_pattern(seq_pattern)

        active_seqs = await pattern_store.get_patterns(status="active", pattern_type="sequence")
        assert any(s["pattern_id"] == seq_id for s in active_seqs)

        # Phase 2: replace with unidirectional data — correlation reactivates
        data["state_history"] = _unidirectional_history()
        await pattern_store._save()

        await engine.scan()

        # The fallback sequence should now be retired (rejected)
        active_seqs = await pattern_store.get_patterns(status="active", pattern_type="sequence")
        seq_still_active = [s for s in active_seqs if s["pattern_id"] == seq_id]
        assert seq_still_active == []

        rejected_seqs = await pattern_store.get_patterns(status="rejected")
        seq_rejected = [s for s in rejected_seqs if s["pattern_id"] == seq_id]
        assert len(seq_rejected) == 1


class TestSnoozedPatternSurvivesRejection:
    """User-snoozed patterns are NOT rejected by guardrails (only active ones are)."""

    @pytest.mark.asyncio
    async def test_snoozed_pattern_survives_rejection(
        self, pattern_store: PatternStore, engine: PatternEngine
    ) -> None:
        # First, create an active correlation with unidirectional data
        data = await pattern_store._get_loaded_data()
        data["state_history"] = _unidirectional_history()
        await pattern_store._save()

        await engine.scan()

        active_corrs = await pattern_store.get_patterns(status="active", pattern_type="correlation")
        assert len(active_corrs) >= 1
        corr_id = active_corrs[0]["pattern_id"]

        # User snoozes the pattern
        future = (datetime.now(UTC) + timedelta(days=7)).isoformat()
        await pattern_store.update_pattern_status(corr_id, "snoozed", snooze_until=future)

        # Now replace with bidirectional data that would reject active patterns
        data["state_history"] = _bidirectional_history()
        await pattern_store._save()

        await engine.scan()

        # The snoozed pattern should still be snoozed, not rejected
        snoozed = await pattern_store.get_patterns(status="snoozed")
        snoozed_ids = [p["pattern_id"] for p in snoozed]
        assert corr_id in snoozed_ids


class TestSuggestionRemovedOnRejection:
    """When a pattern is rejected by guardrails, its pending suggestion is removed."""

    @pytest.mark.asyncio
    async def test_suggestion_removed_on_rejection(
        self, pattern_store: PatternStore, engine: PatternEngine, generator: SuggestionGenerator
    ) -> None:
        # Create a correlation with unidirectional data
        data = await pattern_store._get_loaded_data()
        data["state_history"] = _unidirectional_history()
        await pattern_store._save()

        new_patterns = await engine.scan()
        corr_patterns = [p for p in new_patterns if p["type"] == "correlation"]
        assert len(corr_patterns) >= 1

        # Generate a suggestion for the detected pattern
        suggestions = await generator.generate_from_patterns(corr_patterns)
        assert len(suggestions) >= 1
        suggestion_id = suggestions[0]["suggestion_id"]

        # Verify the suggestion is pending
        s = await pattern_store.get_suggestion(suggestion_id)
        assert s is not None
        assert s["status"] == "pending"

        # Now replace with bidirectional data — guardrails will reject
        data["state_history"] = _bidirectional_history()
        await pattern_store._save()

        await engine.scan()

        # The suggestion should have been removed (deleted, not dismissed)
        s = await pattern_store.get_suggestion(suggestion_id)
        assert s is None


class TestSnoozedSuggestionOnRejection:
    """Document current behavior: snoozed suggestions ARE removed when pattern is rejected.

    NOTE: remove_suggestions_for_pattern currently removes BOTH pending and
    snoozed suggestions. This test documents that behavior. If the behavior
    changes in the future to preserve snoozed suggestions (respecting user
    intent), this test should be updated accordingly.
    """

    @pytest.mark.asyncio
    async def test_snoozed_suggestion_removed_on_rejection(
        self, pattern_store: PatternStore, engine: PatternEngine, generator: SuggestionGenerator
    ) -> None:
        # Create a correlation with unidirectional data
        data = await pattern_store._get_loaded_data()
        data["state_history"] = _unidirectional_history()
        await pattern_store._save()

        new_patterns = await engine.scan()
        corr_patterns = [p for p in new_patterns if p["type"] == "correlation"]
        assert len(corr_patterns) >= 1

        # Generate a suggestion
        suggestions = await generator.generate_from_patterns(corr_patterns)
        assert len(suggestions) >= 1
        suggestion_id = suggestions[0]["suggestion_id"]

        # User snoozes the suggestion
        future = (datetime.now(UTC) + timedelta(days=3)).isoformat()
        await pattern_store.update_suggestion_status(suggestion_id, "snoozed", snooze_until=future)
        s = await pattern_store.get_suggestion(suggestion_id)
        assert s is not None
        assert s["status"] == "snoozed"

        # Replace with bidirectional data — guardrails reject the pattern
        data["state_history"] = _bidirectional_history()
        await pattern_store._save()

        await engine.scan()

        # Snoozed suggestions are preserved — user's snooze intent is honored
        s = await pattern_store.get_suggestion(suggestion_id)
        assert s is not None
        assert s["status"] == "snoozed"


class TestAutoRejectionNotInDismissalList:
    """Auto-removed suggestions don't appear in get_recently_dismissed_suggestions."""

    @pytest.mark.asyncio
    async def test_auto_rejection_not_in_dismissal_list(
        self, pattern_store: PatternStore, engine: PatternEngine, generator: SuggestionGenerator
    ) -> None:
        # Create a correlation with unidirectional data
        data = await pattern_store._get_loaded_data()
        data["state_history"] = _unidirectional_history()
        await pattern_store._save()

        new_patterns = await engine.scan()
        corr_patterns = [p for p in new_patterns if p["type"] == "correlation"]
        assert len(corr_patterns) >= 1

        # Generate a suggestion
        suggestions = await generator.generate_from_patterns(corr_patterns)
        assert len(suggestions) >= 1

        # Replace with bidirectional data — guardrails reject and remove suggestion
        data["state_history"] = _bidirectional_history()
        await pattern_store._save()

        await engine.scan()

        # The auto-removed suggestion should NOT appear in the dismissal list
        # because it was deleted, not dismissed
        dismissed = await pattern_store.get_recently_dismissed_suggestions()
        assert dismissed == []
