"""Tests for the Selora AI sensor platform."""

from __future__ import annotations

from custom_components.selora_ai.sensor import _summarise_categories


class TestSummariseCategories:
    """Tests for the _summarise_categories helper function."""

    def test_empty_dict_returns_no_devices(self) -> None:
        assert _summarise_categories({}) == "No devices"

    def test_single_device_singular(self) -> None:
        cats = {"tv": [{"name": "Samsung TV"}]}
        assert _summarise_categories(cats) == "1 tv"

    def test_multiple_devices_plural(self) -> None:
        cats = {"tv": [{"name": "Samsung TV"}, {"name": "LG TV"}]}
        assert _summarise_categories(cats) == "2 tvs"

    def test_multiple_categories_sorted_by_count(self) -> None:
        cats = {
            "lighting": [{"name": "L1"}],
            "tv": [{"name": "T1"}, {"name": "T2"}, {"name": "T3"}],
            "speaker": [{"name": "S1"}, {"name": "S2"}],
        }
        result = _summarise_categories(cats)
        # Sorted descending by count: 3 tvs, 2 speakers, 1 lighting
        assert result == "3 tvs, 2 speakers, 1 lighting"

    def test_single_item_per_category_uses_singular(self) -> None:
        cats = {
            "tv": [{"name": "T1"}],
            "speaker": [{"name": "S1"}],
        }
        result = _summarise_categories(cats)
        parts = result.split(", ")
        assert all("1 " in p for p in parts)
        # No trailing 's' for count 1
        for p in parts:
            cat_name = p.split(" ", 1)[1]
            assert not cat_name.endswith("s")
