"""Tests for SuggestionGenerator — pattern-to-automation conversion."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.selora_ai.const import (
    CONFIDENCE_MEDIUM,
)
from custom_components.selora_ai.suggestion_generator import SuggestionGenerator

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_pattern_store() -> MagicMock:
    """Create a mock PatternStore with sensible defaults."""
    store = MagicMock()
    store.has_suggestion_for_pattern = AsyncMock(return_value=False)
    store.get_recently_dismissed_suggestions = AsyncMock(return_value=[])
    store.get_suggestions = AsyncMock(return_value=[])
    store.save_suggestion = AsyncMock(return_value="sugg_id_001")
    return store


# ═══════════════════════════════════════════════════════════════════════
# _build_action  (static, pure logic)
# ═══════════════════════════════════════════════════════════════════════


class TestBuildAction:
    """Tests for SuggestionGenerator._build_action."""

    def test_light_on(self):
        result = SuggestionGenerator._build_action("light", "light.living_room", "on")
        assert result == {
            "action": "light.turn_on",
            "target": {"entity_id": "light.living_room"},
        }

    def test_light_off(self):
        result = SuggestionGenerator._build_action("light", "light.living_room", "off")
        assert result == {
            "action": "light.turn_off",
            "target": {"entity_id": "light.living_room"},
        }

    def test_cover_open(self):
        result = SuggestionGenerator._build_action("cover", "cover.blinds", "open")
        assert result == {
            "action": "cover.open_cover",
            "target": {"entity_id": "cover.blinds"},
        }

    def test_cover_closed(self):
        result = SuggestionGenerator._build_action("cover", "cover.blinds", "closed")
        assert result == {
            "action": "cover.close_cover",
            "target": {"entity_id": "cover.blinds"},
        }

    def test_lock_locked(self):
        result = SuggestionGenerator._build_action("lock", "lock.front", "locked")
        assert result == {
            "action": "lock.lock",
            "target": {"entity_id": "lock.front"},
        }

    def test_lock_unlocked(self):
        result = SuggestionGenerator._build_action("lock", "lock.front", "unlocked")
        assert result == {
            "action": "lock.unlock",
            "target": {"entity_id": "lock.front"},
        }

    def test_unknown_state_returns_none(self):
        result = SuggestionGenerator._build_action("light", "light.x", "dim")
        assert result is None

    def test_generic_on_off_for_switch(self):
        result = SuggestionGenerator._build_action("switch", "switch.x", "on")
        assert result == {
            "action": "switch.turn_on",
            "target": {"entity_id": "switch.x"},
        }


# ═══════════════════════════════════════════════════════════════════════
# _build_evidence_summary  (static, pure logic)
# ═══════════════════════════════════════════════════════════════════════


class TestBuildEvidenceSummary:
    """Tests for SuggestionGenerator._build_evidence_summary."""

    def test_time_based(self, sample_time_pattern: dict[str, Any]):
        result = SuggestionGenerator._build_evidence_summary(sample_time_pattern)
        assert result == "Observed 5 times over 7 days at 18:00"

    def test_correlation(self, sample_correlation_pattern: dict[str, Any]):
        result = SuggestionGenerator._build_evidence_summary(sample_correlation_pattern)
        assert result == "Observed 8 co-occurrences (avg delay: 30.5s)"

    def test_sequence(self, sample_sequence_pattern: dict[str, Any]):
        result = SuggestionGenerator._build_evidence_summary(sample_sequence_pattern)
        assert result == "Observed 6 times in sequence"

    def test_unknown_type(self):
        pattern = {"type": "magical", "evidence": {}, "occurrence_count": 42}
        result = SuggestionGenerator._build_evidence_summary(pattern)
        assert result == "Observed 42 times"


# ═══════════════════════════════════════════════════════════════════════
# _build_dismissed_summary  (static, pure logic)
# ═══════════════════════════════════════════════════════════════════════


class TestBuildDismissedSummary:
    """Tests for SuggestionGenerator._build_dismissed_summary."""

    def test_empty_list(self):
        assert SuggestionGenerator._build_dismissed_summary([]) == ""

    def test_single_dismissed(self):
        dismissed = [{"description": "Turn on lights", "dismissal_reason": "not useful"}]
        result = SuggestionGenerator._build_dismissed_summary(dismissed)
        assert result == "- Turn on lights (reason: not useful)"

    def test_default_reason_when_none(self):
        dismissed = [{"description": "Turn on lights", "dismissal_reason": None}]
        result = SuggestionGenerator._build_dismissed_summary(dismissed)
        assert "reason: user-declined" in result

    def test_deduplicates_by_desc_and_reason(self):
        dismissed = [
            {"description": "Turn on lights", "dismissal_reason": "not useful"},
            {"description": "Turn on lights", "dismissal_reason": "not useful"},
        ]
        result = SuggestionGenerator._build_dismissed_summary(dismissed)
        assert result.count("Turn on lights") == 1

    def test_caps_at_10(self):
        dismissed = [
            {"description": f"Automation {i}", "dismissal_reason": f"reason_{i}"} for i in range(15)
        ]
        result = SuggestionGenerator._build_dismissed_summary(dismissed)
        assert len(result.strip().split("\n")) == 10


# ═══════════════════════════════════════════════════════════════════════
# _pattern_to_automation  (instance method, routing)
# ═══════════════════════════════════════════════════════════════════════


class TestPatternToAutomation:
    """Tests for _pattern_to_automation routing and sub-methods."""

    def _make_gen(self, hass: MagicMock) -> SuggestionGenerator:
        return SuggestionGenerator(hass, _make_pattern_store())

    def test_routes_time_based(self, hass: MagicMock, sample_time_pattern: dict[str, Any]):
        gen = self._make_gen(hass)
        result = gen._pattern_to_automation(sample_time_pattern)
        assert result is not None
        assert result["trigger"][0]["platform"] == "time"

    def test_routes_correlation(self, hass: MagicMock, sample_correlation_pattern: dict[str, Any]):
        gen = self._make_gen(hass)
        result = gen._pattern_to_automation(sample_correlation_pattern)
        assert result is not None
        assert result["trigger"][0]["platform"] == "state"

    def test_routes_sequence(self, hass: MagicMock, sample_sequence_pattern: dict[str, Any]):
        gen = self._make_gen(hass)
        result = gen._pattern_to_automation(sample_sequence_pattern)
        assert result is not None
        assert result["trigger"][0]["platform"] == "state"

    def test_unknown_type_returns_none(self, hass: MagicMock):
        gen = self._make_gen(hass)
        pattern = {"type": "unknown", "evidence": {}}
        assert gen._pattern_to_automation(pattern) is None


# ═══════════════════════════════════════════════════════════════════════
# _time_pattern_to_automation  (weekday / weekend / no-condition)
# ═══════════════════════════════════════════════════════════════════════


class TestTimePatternToAutomation:
    """Tests for _time_pattern_to_automation conditions."""

    def _make_gen(self, hass: MagicMock) -> SuggestionGenerator:
        return SuggestionGenerator(hass, _make_pattern_store())

    def test_weekday_condition(self, hass: MagicMock, sample_time_pattern: dict[str, Any]):
        gen = self._make_gen(hass)
        result = gen._time_pattern_to_automation(sample_time_pattern)
        assert result is not None
        assert len(result["condition"]) == 1
        assert result["condition"][0]["weekday"] == ["mon", "tue", "wed", "thu", "fri"]

    def test_weekend_condition(self, hass: MagicMock, sample_time_pattern: dict[str, Any]):
        sample_time_pattern["evidence"]["is_weekday"] = False
        gen = self._make_gen(hass)
        result = gen._time_pattern_to_automation(sample_time_pattern)
        assert result is not None
        assert result["condition"][0]["weekday"] == ["sat", "sun"]

    def test_no_weekday_flag_means_no_condition(
        self, hass: MagicMock, sample_time_pattern: dict[str, Any]
    ):
        del sample_time_pattern["evidence"]["is_weekday"]
        gen = self._make_gen(hass)
        result = gen._time_pattern_to_automation(sample_time_pattern)
        assert result is not None
        assert result["condition"] == []


# ═══════════════════════════════════════════════════════════════════════
# _correlation_to_automation
# ═══════════════════════════════════════════════════════════════════════


class TestCorrelationToAutomation:
    def test_trigger_and_action(self, hass: MagicMock, sample_correlation_pattern: dict[str, Any]):
        gen = SuggestionGenerator(hass, _make_pattern_store())
        result = gen._correlation_to_automation(sample_correlation_pattern)
        assert result is not None
        trigger = result["trigger"][0]
        assert trigger["entity_id"] == "binary_sensor.front_door"
        assert trigger["to"] == "on"
        action = result["action"][0]
        assert action["action"] == "light.turn_on"
        assert action["target"]["entity_id"] == "light.hallway"


# ═══════════════════════════════════════════════════════════════════════
# _sequence_to_automation
# ═══════════════════════════════════════════════════════════════════════


class TestSequenceToAutomation:
    def test_trigger_has_from_and_to(
        self, hass: MagicMock, sample_sequence_pattern: dict[str, Any]
    ):
        gen = SuggestionGenerator(hass, _make_pattern_store())
        result = gen._sequence_to_automation(sample_sequence_pattern)
        assert result is not None
        trigger = result["trigger"][0]
        assert trigger["from"] == "off"
        assert trigger["to"] == "on"

    def test_no_from_key_when_trigger_from_empty(
        self, hass: MagicMock, sample_sequence_pattern: dict[str, Any]
    ):
        sample_sequence_pattern["evidence"]["trigger_from"] = ""
        gen = SuggestionGenerator(hass, _make_pattern_store())
        result = gen._sequence_to_automation(sample_sequence_pattern)
        assert result is not None
        assert "from" not in result["trigger"][0]


# ═══════════════════════════════════════════════════════════════════════
# generate_from_patterns  (async, needs mocks)
# ═══════════════════════════════════════════════════════════════════════


class TestGenerateFromPatterns:
    """Tests for the main generate_from_patterns pipeline."""

    @pytest.mark.asyncio
    async def test_skips_low_confidence(self, hass: MagicMock, sample_time_pattern: dict[str, Any]):
        sample_time_pattern["confidence"] = CONFIDENCE_MEDIUM - 0.01
        store = _make_pattern_store()
        gen = SuggestionGenerator(hass, store)
        result = await gen.generate_from_patterns([sample_time_pattern])
        assert result == []
        store.save_suggestion.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_existing_suggestion(
        self, hass: MagicMock, sample_time_pattern: dict[str, Any]
    ):
        store = _make_pattern_store()
        store.has_suggestion_for_pattern = AsyncMock(return_value=True)
        gen = SuggestionGenerator(hass, store)
        result = await gen.generate_from_patterns([sample_time_pattern])
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_dismissed_pattern(
        self, hass: MagicMock, sample_time_pattern: dict[str, Any]
    ):
        store = _make_pattern_store()
        store.get_recently_dismissed_suggestions = AsyncMock(
            return_value=[
                {
                    "pattern_id": "pat_time_001",
                    "description": "Something",
                    "dismissal_reason": "not useful",
                }
            ]
        )
        gen = SuggestionGenerator(hass, store)
        result = await gen.generate_from_patterns([sample_time_pattern])
        assert result == []

    @pytest.mark.asyncio
    async def test_saves_valid_suggestion(
        self, hass: MagicMock, sample_time_pattern: dict[str, Any]
    ):
        store = _make_pattern_store()
        gen = SuggestionGenerator(hass, store)

        with patch(
            "custom_components.selora_ai.suggestion_generator.validate_automation_payload",
        ) as mock_validate:
            # Return a normalized payload that passes validation
            mock_validate.return_value = (
                True,
                "ok",
                {
                    "alias": "[Selora AI] Living Room turns on around 18:00 on weekdays",
                    "description": "Living Room turns on around 18:00 on weekdays",
                    "trigger": [{"platform": "time", "at": "18:00"}],
                    "condition": [
                        {
                            "condition": "time",
                            "weekday": ["mon", "tue", "wed", "thu", "fri"],
                        }
                    ],
                    "action": [
                        {
                            "action": "light.turn_on",
                            "target": {"entity_id": "light.living_room"},
                        }
                    ],
                    "mode": "single",
                },
            )

            result = await gen.generate_from_patterns([sample_time_pattern])

        assert len(result) == 1
        store.save_suggestion.assert_awaited_once()
        saved = store.save_suggestion.call_args[0][0]
        assert saved["pattern_id"] == "pat_time_001"
        assert saved["source"] == "pattern"
        assert saved["confidence"] == 0.71
        assert result[0]["suggestion_id"] == "sugg_id_001"

    @pytest.mark.asyncio
    async def test_deduplicates_batch_by_content(self, hass: MagicMock):
        """Two patterns that produce identical trigger+action should yield only one suggestion (#46)."""
        pattern_a = {
            "pattern_id": "pat_corr_001",
            "type": "correlation",
            "confidence": 0.8,
            "description": "Fridge express mode → kitchen cam on",
            "entity_ids": ["switch.fridge_express", "switch.kitchen_cam"],
            "evidence": {
                "trigger_entity": "switch.fridge_express",
                "trigger_state": "on",
                "response_entity": "switch.kitchen_cam",
                "response_state": "on",
            },
        }
        # Same trigger+action, different pattern_id and description
        pattern_b = {
            "pattern_id": "pat_corr_002",
            "type": "correlation",
            "confidence": 0.75,
            "description": "Fridge express → kitchen camera turns on",
            "entity_ids": ["switch.fridge_express", "switch.kitchen_cam"],
            "evidence": {
                "trigger_entity": "switch.fridge_express",
                "trigger_state": "on",
                "response_entity": "switch.kitchen_cam",
                "response_state": "on",
            },
        }

        store = _make_pattern_store()
        gen = SuggestionGenerator(hass, store)

        with patch(
            "custom_components.selora_ai.suggestion_generator.validate_automation_payload",
        ) as mock_validate:
            normalized = {
                "alias": "[Selora AI] Fridge express mode → kitchen cam on",
                "description": "Fridge express mode → kitchen cam on",
                "trigger": [
                    {
                        "platform": "state",
                        "entity_id": "switch.fridge_express",
                        "to": "on",
                    }
                ],
                "condition": [],
                "action": [
                    {
                        "action": "switch.turn_on",
                        "target": {"entity_id": "switch.kitchen_cam"},
                    }
                ],
                "mode": "single",
            }
            mock_validate.return_value = (True, "ok", normalized)

            result = await gen.generate_from_patterns([pattern_a, pattern_b])

        # Only the first should be kept; the duplicate is skipped
        assert len(result) == 1
        assert store.save_suggestion.await_count == 1

    @pytest.mark.asyncio
    async def test_deduplicates_against_stored_suggestions(self, hass: MagicMock):
        """A new pattern should be skipped if an identical suggestion already exists in the store (#46)."""
        normalized = {
            "alias": "[Selora AI] Kitchen motion on",
            "description": "Kitchen motion on",
            "trigger": [
                {
                    "platform": "state",
                    "entity_id": "binary_sensor.kitchen_motion",
                    "to": "on",
                }
            ],
            "condition": [],
            "action": [
                {
                    "action": "light.turn_on",
                    "target": {"entity_id": "light.kitchen"},
                }
            ],
            "mode": "single",
        }

        store = _make_pattern_store()
        # Simulate an already-stored suggestion with the same content
        stored = [{"automation_data": normalized, "status": "pending"}]
        store.get_suggestions = AsyncMock(
            side_effect=lambda status="pending": stored if status == "pending" else []
        )

        pattern = {
            "pattern_id": "pat_corr_new",
            "type": "correlation",
            "confidence": 0.8,
            "description": "Kitchen motion → light on",
            "entity_ids": ["binary_sensor.kitchen_motion", "light.kitchen"],
            "evidence": {
                "trigger_entity": "binary_sensor.kitchen_motion",
                "trigger_state": "on",
                "response_entity": "light.kitchen",
                "response_state": "on",
            },
        }

        gen = SuggestionGenerator(hass, store)

        with patch(
            "custom_components.selora_ai.suggestion_generator.validate_automation_payload",
        ) as mock_validate:
            mock_validate.return_value = (True, "ok", normalized)
            result = await gen.generate_from_patterns([pattern])

        assert len(result) == 0
        store.save_suggestion.assert_not_awaited()
