"""Unit tests for the deterministic chat_answer override detectors in
``custom_components.selora_ai.providers.selora_local``.

These cover the inventory/state-filter helpers in isolation. The full
streaming and HASS-state wiring is exercised separately via
integration tests.
"""

from __future__ import annotations

import pytest

from custom_components.selora_ai.providers.selora_local import (
    _detect_category_question,
    _detect_state_filter_question,
    _is_pure_inventory_question,
)


class TestDetectCategoryQuestion:
    @pytest.mark.parametrize(
        ("prompt", "expected_domain"),
        [
            ("how many lights do I have?", "light"),
            ("how many switches?", "switch"),
            ("what lights do I have?", "light"),
            ("what fans are there?", "fan"),
            ("do I have any locks?", "lock"),
            ("list my switches", "switch"),
            ("show me all of my fans", "fan"),
            ("list my switch", "switch"),  # singular inventory request
        ],
    )
    def test_recognised_inventory_phrasings(self, prompt: str, expected_domain: str) -> None:
        detected = _detect_category_question(prompt)
        assert detected is not None
        assert detected[0] == expected_domain

    @pytest.mark.parametrize(
        "prompt",
        [
            "do I have to turn off the lights?",
            "how many lights should I turn on for dinner?",
            "turn off the lights",
            "what lights are on?",
        ],
    )
    def test_rejects_non_inventory_prompts(self, prompt: str) -> None:
        assert _detect_category_question(prompt) is None

    @pytest.mark.parametrize(
        "prompt",
        [
            "Do I have lights and switches?",
            "how many lights and fans do I have?",
        ],
    )
    def test_rejects_multi_category_compound_prompts(self, prompt: str) -> None:
        """The first-match path used to silently answer about ``lights`` and
        drop ``switches``. Multi-category prompts must defer to the LoRA so
        neither half is dropped."""
        assert _detect_category_question(prompt) is None

    @pytest.mark.parametrize(
        "prompt",
        [
            "what lights are on in the kitchen?",  # belongs to state filter, but scope rejects
            "how many lights are there in the bedroom?",
            "list my kitchen lights",
            "how many lights upstairs?",
        ],
    )
    def test_rejects_scope_qualified_prompts(self, prompt: str) -> None:
        """Whole-home roll-call cannot honour an area/floor qualifier —
        defer to the LoRA."""
        assert _detect_category_question(prompt) is None

    @pytest.mark.parametrize(
        "prompt",
        [
            "do I have any lights on?",
            "what lights do I have turned on?",
            "do I have any covers open?",
            "how many lights that are on do I have",
        ],
    )
    def test_rejects_trailing_state_filter_prompts(self, prompt: str) -> None:
        """The inventory regex matches the prefix, but the trailing state
        word (``on`` / ``turned on`` / ``that are on``) means the user
        is asking for the subset, not the whole domain. Defer to the
        state-filter / LoRA path."""
        assert _detect_category_question(prompt) is None

    @pytest.mark.parametrize(
        ("prompt", "expected_domain"),
        [
            ("how many lights do I have in total?", "light"),
            ("show all my lights", "light"),
            ("list all the lights", "light"),
            ("list all of the lights", "light"),
            ("show all lights", "light"),
            ("list all our switches", "switch"),
            ("show me all of my switches", "switch"),
        ],
    )
    def test_accepts_whole_home_quantifier_prompts(self, prompt: str, expected_domain: str) -> None:
        """``in total`` / ``all my <category>`` / ``all the <category>``
        expand scope rather than narrowing it. The deterministic
        override must still fire on these — earlier code mis-classified
        them as area qualifiers and deferred to the LoRA."""
        detected = _detect_category_question(prompt)
        assert detected is not None
        assert detected[0] == expected_domain


class TestDetectStateFilterQuestion:
    @pytest.mark.parametrize(
        ("prompt", "expected"),
        [
            ("what lights are on?", ("light", "on")),
            ("what switches are off?", ("switch", "off")),
            ("what fans are running?", ("fan", "running")),
            ("what covers are open?", ("cover", "open")),
            ("what locks are unlocked?", ("lock", "unlocked")),
        ],
    )
    def test_recognised_state_filter_pairs(self, prompt: str, expected: tuple[str, str]) -> None:
        detected = _detect_state_filter_question(prompt)
        assert detected is not None
        assert (detected[0], detected[1]) == expected

    @pytest.mark.parametrize(
        "prompt",
        [
            "what lights are locked?",
            "what switches are running?",
            "what fans are playing?",
        ],
    )
    def test_rejects_invalid_domain_state_pairs(self, prompt: str) -> None:
        """``running`` for ``light`` etc. would silently report zero.
        Detector rejects unmapped pairs so the override doesn't fire on a
        prompt whose natural word has no HA-state translation."""
        assert _detect_state_filter_question(prompt) is None

    def test_state_filter_returns_singular_label(self) -> None:
        """The singular slot must come from the noun map, not
        ``rstrip('s')`` — otherwise ``switches`` produces ``switche``."""
        detected = _detect_state_filter_question("what switches are on?")
        assert detected is not None
        domain, target, plural, singular = detected
        assert domain == "switch"
        assert target == "on"
        assert plural == "switches"
        assert singular == "switch"

    def test_rejects_scope_qualified_prompt(self) -> None:
        assert _detect_state_filter_question("what lights are on in the kitchen?") is None


class TestIsPureInventoryQuestion:
    @pytest.mark.parametrize(
        "prompt",
        [
            "how many lights do I have?",
            "show my lights",
            "tell my switches",
            "list my fans",
        ],
    )
    def test_short_inventory_only_prompts_are_pure(self, prompt: str) -> None:
        assert _is_pure_inventory_question(prompt) is True

    @pytest.mark.parametrize(
        "prompt",
        [
            "turn off the lights and tell me how many lights I have",  # command verb + conjunction
            "how many lights and fans do I have",  # conjunction
            "switch the kitchen lights",  # command verb (switch as verb)
            "hello there",  # no inventory grammar
        ],
    )
    def test_compound_or_action_prompts_are_not_pure(self, prompt: str) -> None:
        assert _is_pure_inventory_question(prompt) is False

    def test_long_prompt_rejected(self) -> None:
        long_prompt = (
            "honestly I would really like to know exactly how many lights "
            "I have around the house including the garage and the basement"
        )
        assert _is_pure_inventory_question(long_prompt) is False
