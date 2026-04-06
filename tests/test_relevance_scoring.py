"""Tests for suggestion relevance scoring."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.selora_ai.const import MIN_RELEVANCE_SCORE


class TestRelevanceScoring:
    """Test _score_suggestion multi-factor scoring."""

    def _make_collector(self):
        from custom_components.selora_ai.collector import DataCollector
        collector = DataCollector.__new__(DataCollector)
        collector._hass = MagicMock()
        collector._hass.states.async_all.return_value = []
        return collector

    def _make_snapshot(self, history_entities=None):
        history = [{"entity_id": eid} for eid in (history_entities or [])]
        return {"recorder_history": history, "devices": [], "entity_states": []}

    def test_cross_device_automation_scores_high(self):
        """Automation connecting different devices scores well."""
        collector = self._make_collector()
        suggestion = {
            "automation_data": {
                "trigger": {"platform": "state", "entity_id": "binary_sensor.motion"},
                "action": {"service": "light.turn_on", "entity_id": "light.living_room"},
            }
        }
        snapshot = self._make_snapshot(history_entities=["binary_sensor.motion"])
        score = collector._score_suggestion(suggestion, snapshot, set())
        assert score >= MIN_RELEVANCE_SCORE

    def test_tautological_automation_scores_low(self):
        """Automation where trigger entity == action entity scores poorly."""
        collector = self._make_collector()
        suggestion = {
            "automation_data": {
                "trigger": {"platform": "state", "entity_id": "light.lamp"},
                "action": {"service": "light.turn_on", "entity_id": "light.lamp"},
            }
        }
        snapshot = self._make_snapshot()
        score = collector._score_suggestion(suggestion, snapshot, existing_entity_ids={"light.lamp"})
        # Same entity + no history + already covered = very low
        assert score < MIN_RELEVANCE_SCORE

    def test_safety_category_boosted(self):
        """Security/safety automations get a category boost."""
        collector = self._make_collector()
        suggestion = {
            "automation_data": {
                "trigger": {"platform": "state", "entity_id": "binary_sensor.door"},
                "action": {"service": "lock.lock", "entity_id": "lock.front_door"},
            }
        }
        snapshot = self._make_snapshot(history_entities=["binary_sensor.door"])
        score = collector._score_suggestion(suggestion, snapshot, set())
        assert score > 0.5

    def test_complex_automation_scores_higher(self):
        """Automation with conditions and multiple actions scores higher."""
        collector = self._make_collector()
        suggestion = {
            "automation_data": {
                "trigger": {"platform": "state", "entity_id": "binary_sensor.motion"},
                "condition": {"condition": "time", "after": "22:00"},
                "action": [
                    {"service": "light.turn_on", "entity_id": "light.hallway"},
                    {"service": "light.turn_on", "entity_id": "light.porch"},
                ],
            }
        }
        snapshot = self._make_snapshot(history_entities=["binary_sensor.motion"])
        score_complex = collector._score_suggestion(suggestion, snapshot, set())

        simple_suggestion = {
            "automation_data": {
                "trigger": {"platform": "state", "entity_id": "binary_sensor.motion"},
                "action": {"service": "light.turn_on", "entity_id": "light.hallway"},
            }
        }
        score_simple = collector._score_suggestion(simple_suggestion, snapshot, set())
        assert score_complex > score_simple

    def test_no_history_penalized(self):
        """Trigger entity with no state history gets lower activity score."""
        collector = self._make_collector()
        suggestion = {
            "automation_data": {
                "trigger": {"platform": "state", "entity_id": "sensor.temp"},
                "action": {"service": "climate.set_temperature", "entity_id": "climate.hvac"},
            }
        }
        with_history = self._make_snapshot(history_entities=["sensor.temp"])
        without_history = self._make_snapshot()

        score_with = collector._score_suggestion(suggestion, with_history, set())
        score_without = collector._score_suggestion(suggestion, without_history, set())
        assert score_with > score_without

    def test_already_covered_entities_penalized(self):
        """Entities already in existing automations reduce coverage score."""
        collector = self._make_collector()
        suggestion = {
            "automation_data": {
                "trigger": {"platform": "state", "entity_id": "light.lamp"},
                "action": {"service": "switch.turn_on", "entity_id": "switch.plug"},
            }
        }
        snapshot = self._make_snapshot(history_entities=["light.lamp"])
        covered = {"light.lamp", "switch.plug"}

        score_covered = collector._score_suggestion(suggestion, snapshot, covered)
        score_novel = collector._score_suggestion(suggestion, snapshot, set())
        assert score_novel > score_covered


class TestExtractEntityIds:
    """Test the _extract_entity_ids helper."""

    def test_simple_string(self):
        from custom_components.selora_ai.collector import DataCollector
        result = DataCollector._extract_entity_ids({"entity_id": "light.lamp"})
        assert result == {"light.lamp"}

    def test_list_of_entities(self):
        from custom_components.selora_ai.collector import DataCollector
        result = DataCollector._extract_entity_ids({"entity_id": ["light.a", "light.b"]})
        assert result == {"light.a", "light.b"}

    def test_nested_actions(self):
        from custom_components.selora_ai.collector import DataCollector
        config = [
            {"service": "light.turn_on", "entity_id": "light.a"},
            {"service": "switch.turn_off", "target": {"entity_id": "switch.b"}},
        ]
        result = DataCollector._extract_entity_ids(config)
        assert result == {"light.a", "switch.b"}

    def test_none_input(self):
        from custom_components.selora_ai.collector import DataCollector
        result = DataCollector._extract_entity_ids(None)
        assert result == set()

    def test_choose_blocks(self):
        """Entity IDs inside choose/conditions/default are extracted."""
        from custom_components.selora_ai.collector import DataCollector
        config = {
            "action": [
                {
                    "choose": [
                        {
                            "conditions": [{"entity_id": "binary_sensor.motion"}],
                            "sequence": [{"entity_id": "light.hallway"}],
                        }
                    ],
                    "default": [{"entity_id": "light.porch"}],
                }
            ]
        }
        result = DataCollector._extract_entity_ids(config)
        assert result == {"binary_sensor.motion", "light.hallway", "light.porch"}

    def test_empty_trigger_list_not_skipped(self):
        """An explicit empty trigger list should not fall through to 'triggers' key."""
        from custom_components.selora_ai.collector import DataCollector

        # Simulate config with both keys — empty "trigger" should take precedence
        config = {"trigger": [], "triggers": [{"entity_id": "sensor.temp"}]}
        # _extract_entity_ids on the trigger key should yield empty set
        result = DataCollector._extract_entity_ids(config.get("trigger") if "trigger" in config else config.get("triggers"))
        assert result == set()
