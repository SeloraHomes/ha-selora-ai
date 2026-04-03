"""Tests for dismissed suggestion filtering and service/entity validation."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.selora_ai.collector import DataCollector


class TestNormalizeAlias:
    """Test alias normalization for dismissal matching."""

    def test_strips_selora_prefix(self):
        assert DataCollector._normalize_alias("[Selora AI] Turn on lights") == "turn on lights"

    def test_case_insensitive(self):
        assert DataCollector._normalize_alias("[SELORA AI] Night Mode") == "night mode"

    def test_collapses_whitespace(self):
        assert DataCollector._normalize_alias("  turn   on   lights  ") == "turn on lights"

    def test_combined_normalization(self):
        assert DataCollector._normalize_alias("[Selora AI]   Night   Mode  ") == "night mode"

    def test_no_prefix(self):
        assert DataCollector._normalize_alias("simple automation") == "simple automation"


class TestServiceEntityCompat:
    """Test service/entity domain compatibility validation."""

    def _make_collector(self):
        collector = DataCollector.__new__(DataCollector)
        collector._hass = MagicMock()
        return collector

    def test_matching_domains_pass(self):
        collector = self._make_collector()
        suggestion = {
            "automation_data": {
                "action": {"service": "light.turn_on", "entity_id": "light.living_room"},
            }
        }
        is_compat, reason = collector._validate_service_entity_compat(suggestion)
        assert is_compat is True

    def test_mismatched_domains_fail(self):
        collector = self._make_collector()
        suggestion = {
            "automation_data": {
                "action": {"service": "light.turn_on", "entity_id": "sensor.temperature"},
            }
        }
        is_compat, reason = collector._validate_service_entity_compat(suggestion)
        assert is_compat is False
        assert "domain mismatch" in reason

    def test_generic_domains_always_pass(self):
        collector = self._make_collector()
        suggestion = {
            "automation_data": {
                "action": {"service": "homeassistant.turn_off", "entity_id": "light.lamp"},
            }
        }
        is_compat, reason = collector._validate_service_entity_compat(suggestion)
        assert is_compat is True

    def test_notify_service_passes(self):
        collector = self._make_collector()
        suggestion = {
            "automation_data": {
                "action": {
                    "service": "notify.persistent_notification",
                    "entity_id": "light.lamp",
                },
            }
        }
        is_compat, reason = collector._validate_service_entity_compat(suggestion)
        assert is_compat is True

    def test_target_entity_checked(self):
        collector = self._make_collector()
        suggestion = {
            "automation_data": {
                "action": {
                    "service": "climate.set_temperature",
                    "target": {"entity_id": "light.lamp"},
                },
            }
        }
        is_compat, reason = collector._validate_service_entity_compat(suggestion)
        assert is_compat is False

    def test_multiple_actions(self):
        collector = self._make_collector()
        suggestion = {
            "automation_data": {
                "action": [
                    {"service": "light.turn_on", "entity_id": "light.lamp"},
                    {"service": "switch.turn_off", "entity_id": "light.other"},  # mismatch
                ],
            }
        }
        is_compat, reason = collector._validate_service_entity_compat(suggestion)
        assert is_compat is False


class TestDismissalFiltering:
    """Test content-hash and alias-based dismissal matching."""

    def test_same_content_hash_filtered(self):
        """Suggestion with same trigger+action as dismissed one is filtered."""
        trigger = {"platform": "state", "entity_id": "binary_sensor.motion"}
        action = {"service": "light.turn_on", "entity_id": "light.hallway"}

        dismissed_auto = {"trigger": trigger, "action": action, "alias": "Old Name"}
        new_suggestion = {"trigger": trigger, "action": action, "alias": "New Name"}

        hash1 = DataCollector._suggestion_hash(dismissed_auto)
        hash2 = DataCollector._suggestion_hash(new_suggestion)
        assert hash1 == hash2  # Same content = same hash

    def test_different_content_different_hash(self):
        """Different trigger+action produces different hash."""
        auto1 = {
            "trigger": {"platform": "state", "entity_id": "binary_sensor.motion"},
            "action": {"service": "light.turn_on", "entity_id": "light.hallway"},
        }
        auto2 = {
            "trigger": {"platform": "state", "entity_id": "binary_sensor.door"},
            "action": {"service": "lock.lock", "entity_id": "lock.front"},
        }
        assert DataCollector._suggestion_hash(auto1) != DataCollector._suggestion_hash(auto2)

    def test_alias_variant_caught_by_normalization(self):
        """Slightly different alias naming still matches after normalization."""
        assert DataCollector._normalize_alias("[Selora AI] Turn on hallway light") == \
               DataCollector._normalize_alias("turn on hallway light")
        assert DataCollector._normalize_alias("[Selora AI]  Turn  On  Hallway  Light") == \
               DataCollector._normalize_alias("Turn on hallway light")
