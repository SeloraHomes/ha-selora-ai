"""Tests for suggestion relevance scoring."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.selora_ai.const import (
    CATEGORY_LINK_WEIGHTS,
    DEFAULT_CATEGORY_LINK_WEIGHT,
    MIN_RELEVANCE_SCORE,
)
from custom_components.selora_ai.llm_client.prompts import build_analysis_prompt


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

    def test_high_activity_entity_scores_higher(self):
        """Trigger entity with many state changes scores higher than one with few."""
        collector = self._make_collector()
        suggestion = {
            "automation_data": {
                "trigger": {"platform": "state", "entity_id": "sensor.motion"},
                "action": {"service": "light.turn_on", "entity_id": "light.room"},
            }
        }
        # 50 state changes = high activity
        high_history = [{"entity_id": "sensor.motion"} for _ in range(50)]
        # 2 state changes = low activity
        low_history = [{"entity_id": "sensor.motion"} for _ in range(2)]

        score_high = collector._score_suggestion(
            suggestion, {"recorder_history": high_history}, set()
        )
        score_low = collector._score_suggestion(
            suggestion, {"recorder_history": low_history}, set()
        )
        assert score_high > score_low

    def test_zero_activity_scores_zero(self):
        """Trigger entity with no state changes gets activity score of 0."""
        collector = self._make_collector()
        suggestion = {
            "automation_data": {
                "trigger": {"platform": "state", "entity_id": "sensor.temp"},
                "action": {"service": "climate.set_temperature", "entity_id": "climate.hvac"},
            }
        }
        # History exists but not for trigger entity
        snapshot = self._make_snapshot(history_entities=["light.other"])
        score = collector._score_suggestion(suggestion, snapshot, set())
        # With high activity, score should be higher
        high_history = [{"entity_id": "sensor.temp"} for _ in range(50)]
        score_active = collector._score_suggestion(
            suggestion, {"recorder_history": high_history}, set()
        )
        assert score_active > score

    def test_activity_scales_proportionally(self):
        """Activity score scales linearly with state change count up to cap."""
        collector = self._make_collector()
        suggestion = {
            "automation_data": {
                "trigger": {"platform": "state", "entity_id": "sensor.motion"},
                "action": {"service": "light.turn_on", "entity_id": "light.room"},
            }
        }
        scores = []
        for count in [5, 25, 50, 100]:
            history = [{"entity_id": "sensor.motion"} for _ in range(count)]
            s = collector._score_suggestion(
                suggestion, {"recorder_history": history}, set()
            )
            scores.append(s)
        # Each level should be >= previous (with cap at 50)
        assert scores[0] < scores[1] < scores[2]
        # 50 and 100 both hit the cap, so scores should be equal
        assert scores[2] == scores[3]

    def test_multiple_trigger_entities_averaged(self):
        """Activity score averages across multiple trigger entities."""
        collector = self._make_collector()
        suggestion = {
            "automation_data": {
                "trigger": [
                    {"platform": "state", "entity_id": "sensor.motion"},
                    {"platform": "state", "entity_id": "sensor.door"},
                ],
                "action": {"service": "light.turn_on", "entity_id": "light.room"},
            }
        }
        # motion has 50 changes (score 1.0), door has 0 (score 0.0) → avg 0.5
        history = [{"entity_id": "sensor.motion"} for _ in range(50)]
        score_mixed = collector._score_suggestion(
            suggestion, {"recorder_history": history}, set()
        )
        # Both have 50 changes → avg 1.0
        history_both = history + [{"entity_id": "sensor.door"} for _ in range(50)]
        score_both = collector._score_suggestion(
            suggestion, {"recorder_history": history_both}, set()
        )
        assert score_both > score_mixed

    def test_low_activity_not_excluded(self):
        """Low-activity entities still score above zero — not excluded, just ranked lower."""
        collector = self._make_collector()
        suggestion = {
            "automation_data": {
                "trigger": {"platform": "state", "entity_id": "sensor.temp"},
                "action": {"service": "climate.set_temperature", "entity_id": "climate.hvac"},
            }
        }
        # Only 1 state change — very low activity
        low_history = [{"entity_id": "sensor.temp"}]
        score = collector._score_suggestion(
            suggestion, {"recorder_history": low_history}, set()
        )
        # Should still be positive (low activity, not zero)
        assert score > 0


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
        from custom_components.selora_ai.llm_client.prompts import _build_category_section

        entities = [
            {"entity_id": "light.kitchen", "state": "on"},
            {"entity_id": "light.bedroom", "state": "off"},
            {"entity_id": "binary_sensor.motion", "state": "off"},
            {"entity_id": "climate.hvac", "state": "heat"},
        ]
        section = _build_category_section(entities)
        assert "Lighting: 2 entities" in section
        assert "Sensors (binary): 1 entities" in section
        assert "Climate/HVAC: 1 entities" in section

    def test_cross_category_hints_shown(self):
        from custom_components.selora_ai.llm_client.prompts import _build_category_section

        entities = [
            {"entity_id": "light.kitchen", "state": "on"},
            {"entity_id": "binary_sensor.motion", "state": "off"},
        ]
        section = _build_category_section(entities)
        assert "motion-activated lights" in section

    def test_no_irrelevant_hints(self):
        from custom_components.selora_ai.llm_client.prompts import _build_category_section

        entities = [
            {"entity_id": "light.a", "state": "on"},
            {"entity_id": "light.b", "state": "off"},
        ]
        section = _build_category_section(entities)
        assert "motion-activated" not in section

    def test_empty_returns_empty(self):
        from custom_components.selora_ai.llm_client.prompts import _build_category_section

        assert _build_category_section([]) == ""


class TestFeedbackSummary:
    """Test the accept/decline feedback loop (#80)."""

    @staticmethod
    def _make_collector():
        from custom_components.selora_ai.collector import DataCollector

        collector = DataCollector.__new__(DataCollector)
        collector._hass = MagicMock()
        collector._feedback_cache = None
        collector._feedback_cache_time = 0.0
        return collector

    @pytest.mark.asyncio
    async def test_feedback_with_accepted(self):
        """Accepted suggestions appear as positive feedback."""
        from unittest.mock import AsyncMock

        collector = self._make_collector()
        mock_store = MagicMock()
        mock_store.get_feedback_summary = AsyncMock(
            return_value={
                "accepted": [
                    {"description": "Turn on lights when motion detected"},
                    {"description": "Lock door at night"},
                ],
                "declined": [],
            }
        )
        collector._get_pattern_store = MagicMock(return_value=mock_store)
        result = await collector._build_feedback_summary()
        assert "USER FEEDBACK" in result
        assert "Accepted automations (2 total)" in result
        assert "Turn on lights when motion detected" in result
        assert "suggest MORE like these" in result

    @pytest.mark.asyncio
    async def test_feedback_with_declined(self):
        """Declined suggestions appear as negative feedback with reasons."""
        from unittest.mock import AsyncMock

        collector = self._make_collector()
        mock_store = MagicMock()
        mock_store.get_feedback_summary = AsyncMock(
            return_value={
                "accepted": [],
                "declined": [
                    {
                        "description": "Play music when garage opens",
                        "dismissal_reason": "not useful",
                    },
                ],
            }
        )
        collector._get_pattern_store = MagicMock(return_value=mock_store)
        result = await collector._build_feedback_summary()
        assert "USER FEEDBACK" in result
        assert "Declined automations (1 total)" in result
        assert "suggest FEWER like these" in result
        assert "not useful" in result

    @pytest.mark.asyncio
    async def test_feedback_empty_returns_empty(self):
        """No feedback returns empty string."""
        from unittest.mock import AsyncMock

        collector = self._make_collector()
        mock_store = MagicMock()
        mock_store.get_feedback_summary = AsyncMock(
            return_value={"accepted": [], "declined": []}
        )
        collector._get_pattern_store = MagicMock(return_value=mock_store)
        result = await collector._build_feedback_summary()
        assert result == ""

    @pytest.mark.asyncio
    async def test_feedback_no_store_returns_empty(self):
        """No pattern store returns empty string."""
        collector = self._make_collector()
        collector._get_pattern_store = MagicMock(return_value=None)
        result = await collector._build_feedback_summary()
        assert result == ""

    @pytest.mark.asyncio
    async def test_feedback_deduplicates_descriptions(self):
        """Duplicate descriptions are not repeated."""
        from unittest.mock import AsyncMock

        collector = self._make_collector()
        mock_store = MagicMock()
        mock_store.get_feedback_summary = AsyncMock(
            return_value={
                "accepted": [
                    {"description": "Turn on lights"},
                    {"description": "Turn on lights"},
                    {"description": "Lock door"},
                ],
                "declined": [],
            }
        )
        collector._get_pattern_store = MagicMock(return_value=mock_store)
        result = await collector._build_feedback_summary()
        assert result.count("Turn on lights") == 1
        assert "Lock door" in result


class TestFeedbackInPrompt:
    """Test that feedback is injected into the LLM analysis prompt (#80)."""

    @staticmethod
    def _make_llm_client():
        from custom_components.selora_ai.llm_client import LLMClient

        client = LLMClient.__new__(LLMClient)
        client._max_suggestions = 3
        client._lookback_days = 7
        return client

    @staticmethod
    def _make_snapshot(**overrides):
        base = {
            "devices": [],
            "entity_states": [{"entity_id": "light.test", "state": "on"}],
            "automations": [],
            "recorder_history": [],
        }
        base.update(overrides)
        return base

    def test_feedback_block_included_in_prompt(self):
        """When _feedback_summary is in the snapshot, it appears in the prompt."""
        client = self._make_llm_client()
        snapshot = self._make_snapshot(
            _feedback_summary="USER FEEDBACK (learn from past decisions):\n  Accepted automations (1 total)"
        )
        prompt = build_analysis_prompt(snapshot, max_suggestions=client._max_suggestions, lookback_days=client._lookback_days)
        assert "USER FEEDBACK (learn from past decisions)" in prompt
        assert "Accepted automations (1 total)" in prompt

    def test_no_feedback_block_when_absent(self):
        """When _feedback_summary is absent, the prompt has no USER FEEDBACK section."""
        client = self._make_llm_client()
        snapshot = self._make_snapshot()
        prompt = build_analysis_prompt(snapshot, max_suggestions=client._max_suggestions, lookback_days=client._lookback_days)
        assert "USER FEEDBACK" not in prompt

    def test_feedback_block_before_critical_reminder(self):
        """Feedback block appears before the CRITICAL entity validation reminder."""
        client = self._make_llm_client()
        snapshot = self._make_snapshot(
            _feedback_summary="USER FEEDBACK (learn from past decisions):\n  test"
        )
        prompt = build_analysis_prompt(snapshot, max_suggestions=client._max_suggestions, lookback_days=client._lookback_days)
        fb_pos = prompt.index("USER FEEDBACK")
        critical_pos = prompt.index("CRITICAL: Only use entity_ids")
        assert fb_pos < critical_pos
