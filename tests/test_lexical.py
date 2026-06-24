"""Tests for the shared lexical normalization/fuzzy primitives."""

from __future__ import annotations

import pytest

from custom_components.selora_ai import lexical
from custom_components.selora_ai.lexical import _difflib_ratio, fuzzy_ratio, normalize


class TestNormalize:
    def test_empty(self) -> None:
        assert normalize("") == ""

    def test_casefold_lower(self) -> None:
        assert normalize("Kitchen Light") == "kitchen light"

    def test_underscore_becomes_space(self) -> None:
        # Entity slugs tokenize like natural language.
        assert normalize("living_room_lamp") == "living room lamp"

    def test_punctuation_stripped_and_whitespace_collapsed(self) -> None:
        assert normalize("Light,  Kitchen!!") == "light kitchen"

    def test_accents_preserved_and_casefolded(self) -> None:
        # Accented chars survive (fr/de/es/it locales); only case folds.
        assert normalize("Salon Éclairage") == "salon éclairage"

    def test_german_sharp_s_casefold(self) -> None:
        # casefold (not lower) expands ß → ss.
        assert normalize("Straße") == "strasse"

    def test_nfkc_fullwidth(self) -> None:
        # Full-width latin normalizes to ASCII under NFKC.
        assert normalize("Ｌｉｇｈｔ") == "light"


class TestFuzzyRatio:
    def test_empty_inputs(self) -> None:
        assert fuzzy_ratio("", "kitchen") == 0.0
        assert fuzzy_ratio("kitchen", "") == 0.0

    def test_exact_match_is_one(self) -> None:
        assert fuzzy_ratio("kitchen light", "kitchen light") == 1.0

    def test_typo_scores_high(self) -> None:
        # "lite" → "light" should still rank as a strong match.
        assert fuzzy_ratio("kitchen lite", "kitchen light") > 0.8

    def test_word_order_insensitive(self) -> None:
        assert fuzzy_ratio("kitchen light", "light kitchen") > 0.9

    def test_unrelated_scores_low(self) -> None:
        assert fuzzy_ratio("bedroom lamp", "garage door") < 0.5


class TestDifflibFallback:
    """The RapidFuzz-absent path (minimal/exotic platforms)."""

    def test_difflib_ratio_bounds(self) -> None:
        assert _difflib_ratio("kitchen light", "kitchen light") == 1.0
        assert _difflib_ratio("kitchen", "kitchen lite") > 0.7

    def test_fuzzy_ratio_uses_fallback_when_rapidfuzz_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(lexical, "_HAVE_RAPIDFUZZ", False)
        # Empty-input guard still holds on the fallback path.
        assert fuzzy_ratio("", "kitchen") == 0.0
        # Matches _difflib_ratio exactly (order-sensitive, unlike RapidFuzz).
        assert fuzzy_ratio("kitchen lite", "kitchen light") == _difflib_ratio(
            "kitchen lite", "kitchen light"
        )
