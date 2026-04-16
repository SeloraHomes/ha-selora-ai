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

# Minimal HA service registry used by _build_action to verify that a
# domain actually supports the service being generated.
_REGISTERED_SERVICES: dict[str, set[str]] = {
    "light": {"turn_on", "turn_off", "toggle"},
    "switch": {"turn_on", "turn_off", "toggle"},
    "fan": {"turn_on", "turn_off", "toggle"},
    "cover": {"open_cover", "close_cover", "stop_cover"},
    "lock": {"lock", "unlock"},
    "climate": {"turn_on", "turn_off", "set_temperature"},
    "media_player": {"turn_on", "turn_off"},
}


def _make_pattern_store() -> MagicMock:
    """Create a mock PatternStore with sensible defaults."""
    store = MagicMock()
    store.has_suggestion_for_pattern = AsyncMock(return_value=False)
    store.get_recently_dismissed_suggestions = AsyncMock(return_value=[])
    store.get_suggestions = AsyncMock(return_value=[])
    store.save_suggestion = AsyncMock(return_value="sugg_id_001")
    return store


def _make_gen_with_services(
    services: dict[str, set[str]] | None = None,
    store: MagicMock | None = None,
) -> SuggestionGenerator:
    """Create a SuggestionGenerator whose hass mock exposes *services*."""
    registry = services or _REGISTERED_SERVICES
    mock_hass = MagicMock()
    mock_hass.services.has_service.side_effect = (
        lambda domain, service: service in registry.get(domain, set())
    )
    return SuggestionGenerator(mock_hass, store or _make_pattern_store())


# ═══════════════════════════════════════════════════════════════════════
# _build_action  (instance method — checks HA service registry)
# ═══════════════════════════════════════════════════════════════════════


class TestBuildAction:
    """Tests for SuggestionGenerator._build_action."""

    def test_light_on(self):
        gen = _make_gen_with_services()
        assert gen._build_action("light", "light.living_room", "on") == {
            "action": "light.turn_on",
            "target": {"entity_id": "light.living_room"},
        }

    def test_light_off(self):
        gen = _make_gen_with_services()
        assert gen._build_action("light", "light.living_room", "off") == {
            "action": "light.turn_off",
            "target": {"entity_id": "light.living_room"},
        }

    def test_cover_open(self):
        gen = _make_gen_with_services()
        assert gen._build_action("cover", "cover.blinds", "open") == {
            "action": "cover.open_cover",
            "target": {"entity_id": "cover.blinds"},
        }

    def test_cover_closed(self):
        gen = _make_gen_with_services()
        assert gen._build_action("cover", "cover.blinds", "closed") == {
            "action": "cover.close_cover",
            "target": {"entity_id": "cover.blinds"},
        }

    def test_lock_locked(self):
        gen = _make_gen_with_services()
        assert gen._build_action("lock", "lock.front", "locked") == {
            "action": "lock.lock",
            "target": {"entity_id": "lock.front"},
        }

    def test_lock_unlocked(self):
        gen = _make_gen_with_services()
        assert gen._build_action("lock", "lock.front", "unlocked") == {
            "action": "lock.unlock",
            "target": {"entity_id": "lock.front"},
        }

    def test_unknown_state_returns_none(self):
        gen = _make_gen_with_services()
        assert gen._build_action("light", "light.x", "dim") is None

    def test_generic_on_off_for_switch(self):
        gen = _make_gen_with_services()
        assert gen._build_action("switch", "switch.x", "on") == {
            "action": "switch.turn_on",
            "target": {"entity_id": "switch.x"},
        }

    def test_read_only_domain_returns_none(self):
        """Domains without services (sensor, binary_sensor, etc.) are rejected (#91)."""
        gen = _make_gen_with_services()
        assert gen._build_action("binary_sensor", "binary_sensor.motion", "on") is None
        assert gen._build_action("binary_sensor", "binary_sensor.door", "off") is None
        assert gen._build_action("sensor", "sensor.temperature", "on") is None
        assert gen._build_action("device_tracker", "device_tracker.phone", "on") is None
        assert gen._build_action("person", "person.john", "on") is None
        assert gen._build_action("input_select", "input_select.mode", "on") is None


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

    def test_routes_time_based(self, sample_time_pattern: dict[str, Any]):
        gen = _make_gen_with_services()
        result = gen._pattern_to_automation(sample_time_pattern)
        assert result is not None
        assert result["triggers"][0]["platform"] == "time"

    def test_routes_correlation(self, sample_correlation_pattern: dict[str, Any]):
        gen = _make_gen_with_services()
        result = gen._pattern_to_automation(sample_correlation_pattern)
        assert result is not None
        assert result["triggers"][0]["platform"] == "state"

    def test_routes_sequence(self, sample_sequence_pattern: dict[str, Any]):
        gen = _make_gen_with_services()
        result = gen._pattern_to_automation(sample_sequence_pattern)
        assert result is not None
        assert result["triggers"][0]["platform"] == "state"

    def test_unknown_type_returns_none(self):
        gen = _make_gen_with_services()
        pattern = {"type": "unknown", "evidence": {}}
        assert gen._pattern_to_automation(pattern) is None


# ═══════════════════════════════════════════════════════════════════════
# _time_pattern_to_automation  (weekday / weekend / no-condition)
# ═══════════════════════════════════════════════════════════════════════


class TestTimePatternToAutomation:
    """Tests for _time_pattern_to_automation conditions."""

    def test_weekday_condition(self, sample_time_pattern: dict[str, Any]):
        gen = _make_gen_with_services()
        result = gen._time_pattern_to_automation(sample_time_pattern)
        assert result is not None
        assert len(result["conditions"]) == 1
        assert result["conditions"][0]["weekday"] == ["mon", "tue", "wed", "thu", "fri"]

    def test_weekend_condition(self, sample_time_pattern: dict[str, Any]):
        sample_time_pattern["evidence"]["is_weekday"] = False
        gen = _make_gen_with_services()
        result = gen._time_pattern_to_automation(sample_time_pattern)
        assert result is not None
        assert result["conditions"][0]["weekday"] == ["sat", "sun"]

    def test_no_weekday_flag_means_no_condition(self, sample_time_pattern: dict[str, Any]):
        del sample_time_pattern["evidence"]["is_weekday"]
        gen = _make_gen_with_services()
        result = gen._time_pattern_to_automation(sample_time_pattern)
        assert result is not None
        assert result["conditions"] == []

    def test_binary_sensor_time_pattern_rejected(self):
        """A time pattern targeting a binary_sensor must return None (#91)."""
        gen = _make_gen_with_services()
        pattern = {
            "pattern_id": "pat_bad_time",
            "type": "time_based",
            "entity_ids": ["binary_sensor.motion"],
            "description": "Motion sensor turns on around 07:00",
            "evidence": {
                "_signature": "binary_sensor.motion:on:28:True",
                "time_slot": "07:00",
                "is_weekday": True,
                "target_state": "on",
                "occurrences": 5,
                "total_days": 7,
            },
            "confidence": 0.71,
        }
        result = gen._time_pattern_to_automation(pattern)
        assert result is None, "binary_sensor should never be used as an action target"


# ═══════════════════════════════════════════════════════════════════════
# _correlation_to_automation
# ═══════════════════════════════════════════════════════════════════════


class TestCorrelationToAutomation:
    def test_trigger_and_action(self, sample_correlation_pattern: dict[str, Any]):
        gen = _make_gen_with_services()
        result = gen._correlation_to_automation(sample_correlation_pattern)
        assert result is not None
        trigger = result["triggers"][0]
        assert trigger["entity_id"] == "binary_sensor.front_door"
        assert trigger["to"] == "on"
        action = result["actions"][0]
        assert action["action"] == "light.turn_on"
        assert action["target"]["entity_id"] == "light.hallway"

    def test_binary_sensor_as_response_rejected(self):
        """A correlation where the response (action) entity is a binary_sensor must be rejected (#91)."""
        gen = _make_gen_with_services()
        bad_pattern = {
            "pattern_id": "pat_bad_001",
            "type": "correlation",
            "entity_ids": ["light.hallway", "binary_sensor.motion"],
            "description": "Motion detected after hallway light on",
            "evidence": {
                "_signature": "light.hallway:on->binary_sensor.motion:on",
                "trigger_entity": "light.hallway",
                "trigger_state": "on",
                "response_entity": "binary_sensor.motion",
                "response_state": "on",
                "avg_delay_seconds": 5.0,
                "co_occurrences": 10,
                "window_minutes": 5,
            },
            "confidence": 0.8,
        }
        result = gen._correlation_to_automation(bad_pattern)
        assert result is None, "binary_sensor should never be used as an action target"


# ═══════════════════════════════════════════════════════════════════════
# _sequence_to_automation
# ═══════════════════════════════════════════════════════════════════════


class TestSequenceToAutomation:
    def test_trigger_has_from_and_to(self, sample_sequence_pattern: dict[str, Any]):
        gen = _make_gen_with_services()
        result = gen._sequence_to_automation(sample_sequence_pattern)
        assert result is not None
        trigger = result["triggers"][0]
        assert trigger["from"] == "off"
        assert trigger["to"] == "on"

    def test_no_from_key_when_trigger_from_empty(
        self, sample_sequence_pattern: dict[str, Any]
    ):
        sample_sequence_pattern["evidence"]["trigger_from"] = ""
        gen = _make_gen_with_services()
        result = gen._sequence_to_automation(sample_sequence_pattern)
        assert result is not None
        assert "from" not in result["triggers"][0]

    def test_binary_sensor_as_sequence_response_rejected(self):
        """Sequence pattern with binary_sensor as response entity must return None (#91)."""
        gen = _make_gen_with_services()
        pattern = {
            "pattern_id": "pat_bad_seq",
            "type": "sequence",
            "entity_ids": ["light.living_room", "binary_sensor.occupancy"],
            "description": "When light turns on, occupancy follows",
            "evidence": {
                "_signature": "light.living_room:off->on=>binary_sensor.occupancy:on",
                "trigger_entity": "light.living_room",
                "trigger_from": "off",
                "trigger_to": "on",
                "response_entity": "binary_sensor.occupancy",
                "response_state": "on",
                "occurrences": 4,
                "window_minutes": 5,
            },
            "confidence": 0.75,
        }
        result = gen._sequence_to_automation(pattern)
        assert result is None, "binary_sensor should never be used as an action target"


# ═══════════════════════════════════════════════════════════════════════
# generate_from_patterns  (async, needs mocks)
# ═══════════════════════════════════════════════════════════════════════


class TestGenerateFromPatterns:
    """Tests for the main generate_from_patterns pipeline."""

    @pytest.mark.asyncio
    async def test_skips_low_confidence(self, sample_time_pattern: dict[str, Any]):
        sample_time_pattern["confidence"] = CONFIDENCE_MEDIUM - 0.01
        store = _make_pattern_store()
        gen = _make_gen_with_services(store=store)
        result = await gen.generate_from_patterns([sample_time_pattern])
        assert result == []
        store.save_suggestion.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_existing_suggestion(self, sample_time_pattern: dict[str, Any]):
        store = _make_pattern_store()
        store.has_suggestion_for_pattern = AsyncMock(return_value=True)
        gen = _make_gen_with_services(store=store)
        result = await gen.generate_from_patterns([sample_time_pattern])
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_dismissed_pattern(self, sample_time_pattern: dict[str, Any]):
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
        gen = _make_gen_with_services(store=store)
        result = await gen.generate_from_patterns([sample_time_pattern])
        assert result == []

    @pytest.mark.asyncio
    async def test_saves_valid_suggestion(self, sample_time_pattern: dict[str, Any]):
        store = _make_pattern_store()
        gen = _make_gen_with_services(store=store)

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
    async def test_deduplicates_batch_by_content(self):
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
        gen = _make_gen_with_services(store=store)

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
    async def test_deduplicates_against_stored_suggestions(self):
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

        gen = _make_gen_with_services(store=store)

        with patch(
            "custom_components.selora_ai.suggestion_generator.validate_automation_payload",
        ) as mock_validate:
            mock_validate.return_value = (True, "ok", normalized)
            result = await gen.generate_from_patterns([pattern])

        assert len(result) == 0
        store.save_suggestion.assert_not_awaited()
