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
    store.update_pattern_status = AsyncMock(return_value=True)
    # Default: no active patterns to backfill (#67)
    store.get_patterns = AsyncMock(return_value=[])
    # Default: enough history to pass hardening checks (#67)
    store.get_entity_history = AsyncMock(
        return_value=[{"state": "on", "ts": "t1"}, {"state": "off", "ts": "t2"}]
    )
    return store


def _make_gen_with_services(
    services: dict[str, set[str]] | None = None,
    store: MagicMock | None = None,
) -> SuggestionGenerator:
    """Create a SuggestionGenerator whose hass mock exposes *services*."""
    registry = services or _REGISTERED_SERVICES
    mock_hass = MagicMock()
    mock_hass.services.has_service.side_effect = lambda domain, service: (
        service in registry.get(domain, set())
    )
    # Default: states.get returns a valid "on" state for hardening checks (#67)
    mock_state = MagicMock()
    mock_state.state = "on"
    mock_hass.states.get.return_value = mock_state
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

    def test_no_from_key_when_trigger_from_empty(self, sample_sequence_pattern: dict[str, Any]):
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
# _validate_suggestion_entities  (hardening checks, #67)
# ═══════════════════════════════════════════════════════════════════════


class TestValidateSuggestionEntities:
    """Tests for suggestion entity hardening (#67)."""

    @pytest.mark.asyncio
    async def test_missing_entity_rejected(self) -> None:
        """Suggestions for entities that don't exist should be skipped."""
        store = _make_pattern_store()
        gen = _make_gen_with_services(store=store)
        gen._hass.states.get.return_value = None
        pattern = {
            "entity_ids": ["light.nonexistent"],
            "evidence": {"trigger_entity": "light.nonexistent"},
        }
        result = await gen._validate_suggestion_entities(pattern)
        assert result is False

    @pytest.mark.asyncio
    async def test_unavailable_entity_rejected(self) -> None:
        """Suggestions for unavailable entities should be skipped."""
        store = _make_pattern_store()
        gen = _make_gen_with_services(store=store)
        mock_state = MagicMock()
        mock_state.state = "unavailable"
        gen._hass.states.get.return_value = mock_state
        pattern = {
            "entity_ids": ["light.broken"],
            "evidence": {"trigger_entity": "light.broken"},
        }
        result = await gen._validate_suggestion_entities(pattern)
        assert result is False

    @pytest.mark.asyncio
    async def test_no_history_rejected(self) -> None:
        """Suggestions for entities with no recent history should be skipped."""
        store = _make_pattern_store()
        store.get_entity_history = AsyncMock(return_value=[])
        gen = _make_gen_with_services(store=store)
        pattern = {
            "entity_ids": ["light.hallway"],
            "evidence": {"trigger_entity": "light.hallway"},
        }
        result = await gen._validate_suggestion_entities(pattern)
        assert result is False

    @pytest.mark.asyncio
    async def test_valid_entity_passes(self) -> None:
        """Entities that exist, are available, and have history should pass."""
        store = _make_pattern_store()
        gen = _make_gen_with_services(store=store)
        pattern = {
            "entity_ids": ["light.hallway"],
            "evidence": {"trigger_entity": "light.hallway"},
        }
        result = await gen._validate_suggestion_entities(pattern)
        assert result is True

    @pytest.mark.asyncio
    async def test_empty_entity_ids_rejected(self) -> None:
        """Patterns with no entity_ids should be rejected."""
        gen = _make_gen_with_services()
        pattern = {"entity_ids": [], "evidence": {}}
        result = await gen._validate_suggestion_entities(pattern)
        assert result is False

    @pytest.mark.asyncio
    async def test_history_query_uses_recency_window(self) -> None:
        """get_entity_history must be called with a `since` cutoff, not unbounded."""
        store = _make_pattern_store()
        gen = _make_gen_with_services(store=store)
        pattern = {
            "entity_ids": ["light.hallway"],
            "evidence": {"trigger_entity": "light.hallway"},
        }
        await gen._validate_suggestion_entities(pattern)
        store.get_entity_history.assert_awaited_once()
        call_kwargs = store.get_entity_history.call_args
        assert call_kwargs.kwargs.get("since") is not None


# ═══════════════════════════════════════════════════════════════════════
# _backfill_unsugested_patterns  (retry after transient failures, #67)
# ═══════════════════════════════════════════════════════════════════════


class TestBackfillUnsugestedPatterns:
    """Ensure active patterns that previously failed validation are retried."""

    @pytest.mark.asyncio
    async def test_backfills_active_pattern_without_suggestion(self) -> None:
        """An active pattern missing a suggestion should be added to the candidate list."""
        store = _make_pattern_store()
        orphan: dict[str, Any] = {
            "pattern_id": "pat_orphan",
            "type": "time_based",
            "confidence": 0.8,
            "entity_ids": ["light.hallway"],
            "evidence": {},
        }
        store.get_patterns = AsyncMock(return_value=[orphan])
        # No suggestion exists for this pattern
        store.has_suggestion_for_pattern = AsyncMock(return_value=False)

        gen = _make_gen_with_services(store=store)
        result = await gen._backfill_unsugested_patterns([])
        assert any(p["pattern_id"] == "pat_orphan" for p in result)

    @pytest.mark.asyncio
    async def test_does_not_duplicate_incoming_pattern(self) -> None:
        """A pattern already in the incoming list should not be added twice."""
        store = _make_pattern_store()
        incoming: dict[str, Any] = {
            "pattern_id": "pat_existing",
            "type": "time_based",
            "confidence": 0.8,
            "entity_ids": ["light.hallway"],
            "evidence": {},
        }
        store.get_patterns = AsyncMock(return_value=[incoming])
        store.has_suggestion_for_pattern = AsyncMock(return_value=False)

        gen = _make_gen_with_services(store=store)
        result = await gen._backfill_unsugested_patterns([incoming])
        ids = [p["pattern_id"] for p in result]
        assert ids.count("pat_existing") == 1

    @pytest.mark.asyncio
    async def test_skips_pattern_with_existing_suggestion(self) -> None:
        """Active patterns that already have a suggestion should not be backfilled."""
        store = _make_pattern_store()
        pattern: dict[str, Any] = {
            "pattern_id": "pat_suggested",
            "type": "time_based",
            "confidence": 0.8,
            "entity_ids": ["light.hallway"],
            "evidence": {},
        }
        store.get_patterns = AsyncMock(return_value=[pattern])
        store.has_suggestion_for_pattern = AsyncMock(return_value=True)

        gen = _make_gen_with_services(store=store)
        result = await gen._backfill_unsugested_patterns([])
        assert result == []


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


# ═══════════════════════════════════════════════════════════════════════
# Quality gate: cap, fan-out collapse, LLM scoring, fallback
# ═══════════════════════════════════════════════════════════════════════


def _corr_pattern(
    i: int,
    *,
    trigger: str | None = None,
    trigger_state: str = "on",
    confidence: float = 0.8,
) -> dict[str, Any]:
    """Build a distinct correlation pattern for gate tests."""
    trig = trigger or f"binary_sensor.motion_{i}"
    resp = f"light.room_{i}"
    return {
        "pattern_id": f"pat_{i}",
        "type": "correlation",
        "confidence": confidence,
        "description": f"{trig} {trigger_state} -> {resp} on",
        "entity_ids": [trig, resp],
        "evidence": {
            "trigger_entity": trig,
            "trigger_state": trigger_state,
            "response_entity": resp,
            "response_state": "on",
        },
    }


def _patch_devices(count: int) -> Any:
    """Patch the device registry so the cap resolves to a known home size.

    Only ``len(devices)`` matters to ``_suggestion_cap``; the MagicMock's
    default empty ``__iter__`` keeps ``resolve_ignored_entity_ids`` a no-op
    (it iterates ``devices.values()``).
    """
    mock_dr = patch("custom_components.selora_ai.suggestion_generator.dr.async_get")
    started = mock_dr.start()
    mock_devices = MagicMock()
    mock_devices.__len__.return_value = count
    started.return_value.devices = mock_devices
    return mock_dr


def _echo_validate() -> Any:
    """Patch validate_automation_payload to accept and echo the built payload."""
    p = patch("custom_components.selora_ai.suggestion_generator.validate_automation_payload")
    started = p.start()
    started.side_effect = lambda auto, hass: (True, "ok", auto)
    return p


def _make_gen_with_llm(store: MagicMock, llm: MagicMock) -> SuggestionGenerator:
    """Build a SuggestionGenerator wired to a mock LLM for scoring-gate tests."""
    mock_hass = MagicMock()
    mock_hass.services.has_service.side_effect = lambda d, s: (
        s in _REGISTERED_SERVICES.get(d, set())
    )
    mock_state = MagicMock()
    mock_state.state = "on"
    mock_hass.states.get.return_value = mock_state
    return SuggestionGenerator(mock_hass, store, llm=llm)


class TestClusterKey:
    """_cluster_key must separate distinct automations while grouping fan-out."""

    def test_time_weekday_and_weekend_differ(self):
        base = {
            "type": "time_based",
            "entity_ids": ["light.hall"],
            "evidence": {"time_slot": "18:00", "target_state": "on"},
        }
        weekday = {**base, "evidence": {**base["evidence"], "is_weekday": True}}
        weekend = {**base, "evidence": {**base["evidence"], "is_weekday": False}}
        assert SuggestionGenerator._cluster_key(weekday) != SuggestionGenerator._cluster_key(
            weekend
        )

    def test_sequence_trigger_from_differs(self):
        base = {
            "type": "sequence",
            "entity_ids": ["cover.garage", "light.porch"],
            "evidence": {
                "trigger_entity": "cover.garage",
                "trigger_to": "open",
                "response_entity": "light.porch",
                "response_state": "on",
            },
        }
        from_closed = {**base, "evidence": {**base["evidence"], "trigger_from": "closed"}}
        from_opening = {**base, "evidence": {**base["evidence"], "trigger_from": "opening"}}
        assert SuggestionGenerator._cluster_key(from_closed) != SuggestionGenerator._cluster_key(
            from_opening
        )

    def test_sequence_fan_out_same_trigger_groups(self):
        """Same trigger (entity/from/to), different response target → same cluster."""
        base = {
            "type": "sequence",
            "entity_ids": ["cover.garage", "light.porch"],
            "evidence": {
                "trigger_entity": "cover.garage",
                "trigger_from": "closed",
                "trigger_to": "open",
                "response_state": "on",
            },
        }
        to_porch = {**base, "evidence": {**base["evidence"], "response_entity": "light.porch"}}
        to_deck = {**base, "evidence": {**base["evidence"], "response_entity": "light.deck"}}
        assert SuggestionGenerator._cluster_key(to_porch) == SuggestionGenerator._cluster_key(
            to_deck
        )

    def test_correlation_fan_out_same_trigger_groups(self):
        base = {
            "type": "correlation",
            "entity_ids": ["binary_sensor.door", "light.a"],
            "evidence": {"trigger_entity": "binary_sensor.door", "trigger_state": "off"},
        }
        to_a = {**base, "evidence": {**base["evidence"], "response_entity": "light.a"}}
        to_b = {**base, "evidence": {**base["evidence"], "response_entity": "light.b"}}
        assert SuggestionGenerator._cluster_key(to_a) == SuggestionGenerator._cluster_key(to_b)


class TestSuggestionQualityGate:
    """Tests for the cap / collapse / LLM-scoring gate."""

    @pytest.mark.asyncio
    async def test_caps_to_home_size(self):
        """More candidates than slots → capped to the home-size cap (llm-less fallback)."""
        store = _make_pattern_store()
        gen = _make_gen_with_services(store=store)  # llm=None → confidence fallback
        patterns = [_corr_pattern(i, confidence=0.9 - i * 0.01) for i in range(20)]

        dr_patch = _patch_devices(132)  # ceil(132/15)=9, clamped to [3,15] → 9
        val_patch = _echo_validate()
        try:
            result = await gen.generate_from_patterns(patterns)
        finally:
            dr_patch.stop()
            val_patch.stop()

        assert len(result) == 9
        assert store.save_suggestion.await_count == 9

    @pytest.mark.asyncio
    async def test_slots_full_returns_empty(self):
        """Already at cap → no generation, no saves."""
        store = _make_pattern_store()
        # Default MagicMock device count → cap floor (3). Three pending fills it.
        store.get_suggestions = AsyncMock(
            side_effect=lambda status="pending": (
                [{"automation_data": {}, "status": "pending"}] * 3 if status == "pending" else []
            )
        )
        gen = _make_gen_with_services(store=store)
        result = await gen.generate_from_patterns([_corr_pattern(1)])
        assert result == []
        store.save_suggestion.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_collapses_fan_out_variants(self):
        """Same trigger, different targets → collapse to the highest-confidence one."""
        store = _make_pattern_store()
        gen = _make_gen_with_services(store=store)  # llm=None
        # One trigger (front door motion off) fanning out to three lights.
        patterns = [
            _corr_pattern(
                0, trigger="binary_sensor.front_door", trigger_state="off", confidence=0.6
            ),
            _corr_pattern(
                1, trigger="binary_sensor.front_door", trigger_state="off", confidence=0.9
            ),
            _corr_pattern(
                2, trigger="binary_sensor.front_door", trigger_state="off", confidence=0.7
            ),
        ]

        val_patch = _echo_validate()
        try:
            result = await gen.generate_from_patterns(patterns)
        finally:
            val_patch.stop()

        assert len(result) == 1
        assert store.save_suggestion.await_count == 1
        saved = store.save_suggestion.call_args[0][0]
        assert saved["confidence"] == 0.9

    @pytest.mark.asyncio
    async def test_llm_filters_low_quality(self):
        """LLM scores gate the output: low-score / keep=false candidates are dropped."""
        store = _make_pattern_store()
        mock_llm = MagicMock()
        mock_llm.send_request = AsyncMock(
            return_value=(
                '[{"score": 90, "keep": true, "reason": "sensible"},'
                ' {"score": 20, "keep": false, "reason": "coincidence"}]',
                None,
            )
        )
        mock_hass = MagicMock()
        mock_hass.services.has_service.side_effect = lambda d, s: (
            s in _REGISTERED_SERVICES.get(d, set())
        )
        mock_state = MagicMock()
        mock_state.state = "on"
        mock_hass.states.get.return_value = mock_state
        gen = SuggestionGenerator(mock_hass, store, llm=mock_llm)

        patterns = [_corr_pattern(0, confidence=0.8), _corr_pattern(1, confidence=0.8)]

        val_patch = _echo_validate()
        try:
            result = await gen.generate_from_patterns(patterns)
        finally:
            val_patch.stop()

        mock_llm.send_request.assert_awaited_once()
        assert len(result) == 1
        assert result[0]["pattern_id"] == "pat_0"
        # The rejected pattern is durably persisted so backfill / re-detection
        # won't re-score it (#67). quality_rejected is NOT reactivated by
        # save_pattern the way the causality "rejected" status is.
        store.update_pattern_status.assert_awaited_once_with("pat_1", "quality_rejected")

    @pytest.mark.asyncio
    async def test_fallback_does_not_persist_rejections(self):
        """Without usable LLM scores we can't judge quality — nothing is marked rejected."""
        store = _make_pattern_store()
        mock_llm = MagicMock()
        mock_llm.send_request = AsyncMock(side_effect=RuntimeError("boom"))
        gen = _make_gen_with_llm(store, mock_llm)

        val_patch = _echo_validate()
        try:
            await gen.generate_from_patterns([_corr_pattern(0, confidence=0.8)])
        finally:
            val_patch.stop()

        store.update_pattern_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cluster_keeps_sensible_lower_confidence_variant(self):
        """In a fan-out cluster, a rejected high-confidence variant must not bury a passing one."""
        store = _make_pattern_store()
        mock_llm = MagicMock()
        # Candidate order: A (spurious, high conf) then B (sensible, low conf),
        # both sharing the front_door trigger cluster.
        mock_llm.send_request = AsyncMock(
            return_value=(
                '[{"score": 10, "keep": false}, {"score": 80, "keep": true}]',
                None,
            )
        )
        gen = _make_gen_with_llm(store, mock_llm)

        patterns = [
            _corr_pattern(
                0, trigger="binary_sensor.front_door", trigger_state="off", confidence=0.9
            ),
            _corr_pattern(
                1, trigger="binary_sensor.front_door", trigger_state="off", confidence=0.6
            ),
        ]

        val_patch = _echo_validate()
        try:
            result = await gen.generate_from_patterns(patterns)
        finally:
            val_patch.stop()

        # The sensible lower-confidence variant survives; the cluster isn't empty.
        assert len(result) == 1
        assert result[0]["pattern_id"] == "pat_1"

    @pytest.mark.asyncio
    async def test_llm_string_keep_false_rejects(self):
        """A quoted keep=\"false\" must reject, even with a passing score (not bool('false')=True)."""
        store = _make_pattern_store()
        mock_llm = MagicMock()
        mock_llm.send_request = AsyncMock(
            return_value=(
                '[{"score": 90, "keep": "true"}, {"score": 95, "keep": "false"}]',
                None,
            )
        )
        gen = _make_gen_with_llm(store, mock_llm)

        patterns = [_corr_pattern(0, confidence=0.8), _corr_pattern(1, confidence=0.8)]

        val_patch = _echo_validate()
        try:
            result = await gen.generate_from_patterns(patterns)
        finally:
            val_patch.stop()

        assert len(result) == 1
        assert result[0]["pattern_id"] == "pat_0"

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_confidence(self):
        """LLM raising → fall back to confidence ranking, still capped."""
        store = _make_pattern_store()
        mock_llm = MagicMock()
        mock_llm.send_request = AsyncMock(side_effect=RuntimeError("boom"))
        mock_hass = MagicMock()
        mock_hass.services.has_service.side_effect = lambda d, s: (
            s in _REGISTERED_SERVICES.get(d, set())
        )
        mock_state = MagicMock()
        mock_state.state = "on"
        mock_hass.states.get.return_value = mock_state
        gen = SuggestionGenerator(mock_hass, store, llm=mock_llm)

        # 5 distinct patterns, default cap 3 → top-3 by confidence survive.
        confidences = [0.9, 0.85, 0.8, 0.7, 0.6]
        patterns = [_corr_pattern(i, confidence=c) for i, c in enumerate(confidences)]

        val_patch = _echo_validate()
        try:
            result = await gen.generate_from_patterns(patterns)
        finally:
            val_patch.stop()

        assert len(result) == 3
        saved_conf = sorted(
            (call.args[0]["confidence"] for call in store.save_suggestion.call_args_list),
            reverse=True,
        )
        assert saved_conf == [0.9, 0.85, 0.8]

    @pytest.mark.asyncio
    async def test_llm_length_matching_non_dict_falls_back(self):
        """Length-matching but non-dict verdicts are unusable → confidence fallback, no crash."""
        store = _make_pattern_store()
        mock_llm = MagicMock()
        # Two entries (matches candidate count) but not objects.
        mock_llm.send_request = AsyncMock(return_value=('["keep it", 42]', None))
        gen = _make_gen_with_llm(store, mock_llm)

        patterns = [_corr_pattern(0, confidence=0.9), _corr_pattern(1, confidence=0.8)]

        val_patch = _echo_validate()
        try:
            result = await gen.generate_from_patterns(patterns)
        finally:
            val_patch.stop()

        # Fell back to confidence ranking (cap 3) — both kept, nothing raised.
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_llm_numeric_string_scores_coerced(self):
        """Numeric-string scores coerce; a genuine low score is dropped and persisted."""
        store = _make_pattern_store()
        mock_llm = MagicMock()
        mock_llm.send_request = AsyncMock(
            return_value=(
                '[{"score": "90", "keep": true}, {"score": "55", "keep": true}]',
                None,
            )
        )
        gen = _make_gen_with_llm(store, mock_llm)

        patterns = [_corr_pattern(0, confidence=0.8), _corr_pattern(1, confidence=0.8)]

        val_patch = _echo_validate()
        try:
            result = await gen.generate_from_patterns(patterns)
        finally:
            val_patch.stop()

        assert len(result) == 1
        assert result[0]["pattern_id"] == "pat_0"
        # 55 < MIN_SCORE is a real quality verdict → durably rejected.
        store.update_pattern_status.assert_awaited_once_with("pat_1", "quality_rejected")

    @pytest.mark.asyncio
    async def test_llm_non_numeric_score_falls_back_without_persisting(self):
        """A non-numeric score is a formatting glitch, not a verdict → fallback, no rejection."""
        store = _make_pattern_store()
        mock_llm = MagicMock()
        mock_llm.send_request = AsyncMock(
            return_value=(
                '[{"score": "bad", "keep": true}, {"score": 90, "keep": true}]',
                None,
            )
        )
        gen = _make_gen_with_llm(store, mock_llm)

        patterns = [_corr_pattern(0, confidence=0.9), _corr_pattern(1, confidence=0.8)]

        val_patch = _echo_validate()
        try:
            result = await gen.generate_from_patterns(patterns)
        finally:
            val_patch.stop()

        # Whole batch unusable → confidence fallback keeps both; nothing suppressed.
        assert len(result) == 2
        store.update_pattern_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_score_timeout_is_threaded_to_scoring_call(self):
        """The caller's score_timeout bounds the LLM wait_for (interactive budget)."""
        store = _make_pattern_store()
        mock_llm = MagicMock()
        # Plain MagicMock (not AsyncMock): wait_for is patched, so send_request's
        # return value is never awaited — avoids an un-awaited-coroutine warning.
        mock_llm.send_request = MagicMock(return_value=object())
        gen = _make_gen_with_llm(store, mock_llm)

        patterns = [_corr_pattern(0, confidence=0.8)]

        val_patch = _echo_validate()
        with patch(
            "custom_components.selora_ai.suggestion_generator.asyncio.wait_for",
            new=AsyncMock(return_value=('[{"score": 90, "keep": true}]', None)),
        ) as mock_wait_for:
            try:
                await gen.generate_from_patterns(patterns, score_timeout=7)
            finally:
                val_patch.stop()

        assert mock_wait_for.await_args.kwargs["timeout"] == 7

    @pytest.mark.asyncio
    async def test_llm_missing_score_falls_back_without_persisting(self):
        """A verdict missing `score` makes the batch unusable → no durable rejection."""
        store = _make_pattern_store()
        mock_llm = MagicMock()
        mock_llm.send_request = AsyncMock(
            return_value=('[{"keep": true}, {"score": 90, "keep": true}]', None)
        )
        gen = _make_gen_with_llm(store, mock_llm)

        patterns = [_corr_pattern(0, confidence=0.9), _corr_pattern(1, confidence=0.8)]

        val_patch = _echo_validate()
        try:
            result = await gen.generate_from_patterns(patterns)
        finally:
            val_patch.stop()

        assert len(result) == 2
        store.update_pattern_status.assert_not_awaited()
