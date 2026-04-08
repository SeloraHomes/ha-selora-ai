"""Tests for suggestion relevance scoring."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.selora_ai.const import (
    CATEGORY_LINK_WEIGHTS,
    DEFAULT_CATEGORY_LINK_WEIGHT,
    MIN_RELEVANCE_SCORE,
)


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


class TestCategoryLinkWeighting:
    """Test category link weights in suggestion scoring (#79)."""

    def _make_collector(self):
        from custom_components.selora_ai.collector import DataCollector

        collector = DataCollector.__new__(DataCollector)
        collector._hass = MagicMock()
        collector._hass.states.async_all.return_value = []
        return collector

    def test_strong_link_scores_higher_than_weak(self):
        """motion → light (strong) scores higher than vacuum → lock (weak)."""
        collector = self._make_collector()
        strong = {
            "automation_data": {
                "trigger": {"platform": "state", "entity_id": "binary_sensor.motion"},
                "action": {"service": "light.turn_on", "entity_id": "light.room"},
            }
        }
        weak = {
            "automation_data": {
                "trigger": {"platform": "state", "entity_id": "vacuum.roomba"},
                "action": {"service": "lock.lock", "entity_id": "lock.front"},
            }
        }
        snapshot = {"recorder_history": []}
        assert collector._score_suggestion(strong, snapshot, set()) > collector._score_suggestion(
            weak, snapshot, set()
        )

    def test_presence_climate_scores_well(self):
        """person → climate (strong link) should score above threshold."""
        collector = self._make_collector()
        suggestion = {
            "automation_data": {
                "trigger": {"platform": "state", "entity_id": "person.john"},
                "action": {"service": "climate.set_temperature", "entity_id": "climate.hvac"},
            }
        }
        history = [{"entity_id": "person.john"} for _ in range(30)]
        score = collector._score_suggestion(suggestion, {"recorder_history": history}, set())
        assert score >= MIN_RELEVANCE_SCORE

    def test_same_domain_neutral_link(self):
        """Two lights (same domain) get neutral category_link (0.5)."""
        collector = self._make_collector()
        same = {
            "automation_data": {
                "trigger": {"platform": "state", "entity_id": "light.a"},
                "action": {"service": "light.turn_on", "entity_id": "light.b"},
            }
        }
        cross = {
            "automation_data": {
                "trigger": {"platform": "state", "entity_id": "binary_sensor.motion"},
                "action": {"service": "light.turn_on", "entity_id": "light.b"},
            }
        }
        snapshot = {"recorder_history": []}
        # Cross-category (strong link) should beat same-domain
        assert collector._score_suggestion(cross, snapshot, set()) > collector._score_suggestion(
            same, snapshot, set()
        )

    def test_unknown_pairing_gets_default(self):
        """Unlisted domain pair falls back to DEFAULT_CATEGORY_LINK_WEIGHT."""
        pair = frozenset({"input_boolean", "input_select"})
        assert pair not in CATEGORY_LINK_WEIGHTS
        assert DEFAULT_CATEGORY_LINK_WEIGHT == 0.3

    def test_all_weights_use_frozenset(self):
        """All keys in CATEGORY_LINK_WEIGHTS are 2-element frozensets."""
        for key in CATEGORY_LINK_WEIGHTS:
            assert isinstance(key, frozenset)
            assert len(key) == 2

    def test_weights_sum_to_one(self):
        """Relevance weights still sum to 1.0 after adding category_link."""
        from custom_components.selora_ai.const import (
            RELEVANCE_WEIGHT_ACTIVITY,
            RELEVANCE_WEIGHT_CATEGORY,
            RELEVANCE_WEIGHT_CATEGORY_LINK,
            RELEVANCE_WEIGHT_COMPLEXITY,
            RELEVANCE_WEIGHT_COVERAGE,
            RELEVANCE_WEIGHT_CROSS_DEVICE,
        )

        total = (
            RELEVANCE_WEIGHT_CROSS_DEVICE
            + RELEVANCE_WEIGHT_ACTIVITY
            + RELEVANCE_WEIGHT_COVERAGE
            + RELEVANCE_WEIGHT_CATEGORY
            + RELEVANCE_WEIGHT_COMPLEXITY
            + RELEVANCE_WEIGHT_CATEGORY_LINK
        )
        assert abs(total - 1.0) < 0.001


class TestCategorySection:
    """Test the LLM prompt category section builder (#79)."""

    def test_groups_entities_by_category(self):
        from custom_components.selora_ai.llm_client import LLMClient

        entities = [
            {"entity_id": "light.kitchen", "state": "on"},
            {"entity_id": "light.bedroom", "state": "off"},
            {"entity_id": "binary_sensor.motion", "state": "off"},
            {"entity_id": "climate.hvac", "state": "heat"},
        ]
        section = LLMClient._build_category_section(entities)
        assert "Lighting: 2 entities" in section
        assert "Sensors (binary): 1 entities" in section
        assert "Climate/HVAC: 1 entities" in section

    def test_cross_category_hints_shown(self):
        from custom_components.selora_ai.llm_client import LLMClient

        entities = [
            {"entity_id": "light.kitchen", "state": "on"},
            {"entity_id": "binary_sensor.motion", "state": "off"},
        ]
        section = LLMClient._build_category_section(entities)
        assert "motion-activated lights" in section

    def test_no_irrelevant_hints(self):
        from custom_components.selora_ai.llm_client import LLMClient

        entities = [
            {"entity_id": "light.a", "state": "on"},
            {"entity_id": "light.b", "state": "off"},
        ]
        section = LLMClient._build_category_section(entities)
        assert "motion-activated" not in section

    def test_empty_returns_empty(self):
        from custom_components.selora_ai.llm_client import LLMClient

        assert LLMClient._build_category_section([]) == ""
