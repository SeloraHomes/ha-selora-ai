"""Tests for the Selora AI sensor platform."""

from __future__ import annotations

from unittest.mock import Mock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
import pytest

from custom_components.selora_ai.const import DOMAIN, SIGNAL_INSIGHTS_UPDATED
from custom_components.selora_ai.health_store import HealthStore
from custom_components.selora_ai.sensor import (
    HomeHealthSensor,
    _summarise_categories,
)

from .conftest import MockStore


def _audit(
    score: int | None,
    *,
    band: str = "",
    status: str = "ok",
    recommendations: list[dict[str, str]] | None = None,
    sections: list[dict[str, object]] | None = None,
    generated_at: str = "2026-01-01T06:00:00+00:00",
) -> dict[str, object]:
    """A minimal persisted-audit record, matching insights_audit._record."""
    return {
        "status": status,
        "score": score,
        "band": band,
        "recommendations": recommendations or [],
        "score_breakdown": {"sections": sections or []},
        "generated_at": generated_at,
    }


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


@pytest.mark.asyncio
async def test_home_health_sensor_mirrors_audit(hass: HomeAssistant) -> None:
    """The sensor reports the exact persisted audit score, with its band and
    per-severity finding counts as attributes — not a locally recomputed value."""
    with patch("custom_components.selora_ai.health_store.Store") as mock_cls:
        mock_cls.return_value = MockStore()
        store = HealthStore(hass)
        store._store = MockStore()

    await store.set_last_audit(
        _audit(
            82,
            band="B",
            recommendations=[
                {"severity": "critical"},
                {"severity": "warning"},
                {"severity": "warning"},
            ],
            sections=[{"title": "Devices offline", "points": 18.0, "count": 3}],
        )
    )
    hass.data.setdefault(DOMAIN, {})["e1"] = {"health_store": store}

    sensor = HomeHealthSensor(hass, entry=None)
    await sensor._async_refresh()

    assert sensor.available is True
    assert sensor.native_value == 82
    attrs = sensor.extra_state_attributes
    assert attrs["band"] == "B"
    assert attrs["active_signals"] == 3
    assert attrs["critical"] == 1
    assert attrs["warning"] == 2
    assert attrs["last_scan"] == "2026-01-01T06:00:00+00:00"
    assert attrs["breakdown"] == [{"title": "Devices offline", "points": 18.0, "count": 3}]


@pytest.mark.asyncio
async def test_home_health_sensor_unavailable_without_audit(hass: HomeAssistant) -> None:
    """A store with no completed audit yet (first run is ~3 min post-boot) must
    stay unavailable rather than publishing a placeholder score."""
    with patch("custom_components.selora_ai.health_store.Store") as mock_cls:
        mock_cls.return_value = MockStore()
        store = HealthStore(hass)
        store._store = MockStore()
    hass.data.setdefault(DOMAIN, {})["e1"] = {"health_store": store}

    sensor = HomeHealthSensor(hass, entry=None)
    await sensor._async_refresh()
    assert sensor.available is False


@pytest.mark.asyncio
async def test_home_health_sensor_unavailable_without_store(hass: HomeAssistant) -> None:
    """With Insights disabled no HealthStore exists, so the sensor must report
    unavailable — NOT a fabricated 100% healthy score that hides a subsystem
    that isn't running."""
    hass.data.setdefault(DOMAIN, {})  # no health_store wired
    sensor = HomeHealthSensor(hass, entry=None)
    assert sensor.available is False  # unavailable before any refresh
    await sensor._async_refresh()
    assert sensor.available is False


@pytest.mark.asyncio
async def test_home_health_sensor_refreshes_on_insights_signal(hass: HomeAssistant) -> None:
    """The sensor is added before the HealthStore is wired into hass.data, so
    it starts unavailable. SIGNAL_INSIGHTS_UPDATED (fired at Insights startup
    and after each scan) must pull the real score in without waiting for the
    60s poll."""
    sensor = HomeHealthSensor(hass, entry=None)
    # Not attached to a real platform in this unit test.
    sensor.async_write_ha_state = Mock()
    sensor.entity_id = "sensor.selora_ai_hub_home_health"
    await sensor.async_added_to_hass()
    assert sensor.available is False  # no store yet -> unavailable, not a fake 100

    with patch("custom_components.selora_ai.health_store.Store") as mock_cls:
        mock_cls.return_value = MockStore()
        store = HealthStore(hass)
        store._store = MockStore()
    await store.set_last_audit(_audit(85, band="B"))
    hass.data.setdefault(DOMAIN, {})["e1"] = {"health_store": store}

    async_dispatcher_send(hass, SIGNAL_INSIGHTS_UPDATED)
    await hass.async_block_till_done()

    assert sensor.available is True
    assert sensor.native_value == 85
    assert sensor.async_write_ha_state.called
    await sensor.async_will_remove_from_hass()
