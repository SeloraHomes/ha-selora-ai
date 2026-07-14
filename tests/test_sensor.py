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
    _compute_health_score,
    _summarise_categories,
)

from .conftest import MockStore


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


def test_compute_health_score() -> None:
    # No signals → perfect.
    assert _compute_health_score({}) == 100
    # Severity-weighted penalties (critical 15, warning 5, info 1).
    assert _compute_health_score({"warning": 1}) == 95
    assert _compute_health_score({"critical": 2}) == 70
    assert _compute_health_score({"critical": 1, "warning": 2, "info": 3}) == 72
    # Clamped at 0 — a flood of issues never goes negative, and never above 100.
    assert _compute_health_score({"warning": 100}) == 0
    assert _compute_health_score({"critical": 50}) == 0


@pytest.mark.asyncio
async def test_home_health_sensor_populates_last_scan(hass: HomeAssistant) -> None:
    """_async_refresh must surface the store's last_scan (not leave it null)."""
    with patch("custom_components.selora_ai.health_store.Store") as mock_cls:
        mock_cls.return_value = MockStore()
        store = HealthStore(hass)
        store._store = MockStore()

    await store.record_signal(
        kind="unavailable",
        target="light.x",
        target_kind="entity",
        severity="warning",
        evidence={},
    )
    await store.set_last_scan("2026-01-01T06:00:00+00:00")
    hass.data.setdefault(DOMAIN, {})["e1"] = {"health_store": store}

    sensor = HomeHealthSensor(hass, entry=None)
    await sensor._async_refresh()

    # A store is wired -> the sensor is available and reports a 0-100 health
    # score (one warning → 100 - 5); the raw signal count moves to the
    # ``active_signals`` attribute.
    assert sensor.available is True
    assert sensor.native_value == 95
    assert sensor.extra_state_attributes["active_signals"] == 1
    assert sensor.extra_state_attributes["last_scan"] == "2026-01-01T06:00:00+00:00"


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
    await store.record_signal(
        kind="unavailable",
        target="light.x",
        target_kind="entity",
        severity="critical",
        evidence={},
    )
    hass.data.setdefault(DOMAIN, {})["e1"] = {"health_store": store}

    async_dispatcher_send(hass, SIGNAL_INSIGHTS_UPDATED)
    await hass.async_block_till_done()

    assert sensor.available is True
    assert sensor.native_value == 85  # one critical -> 100 - 15
    assert sensor.async_write_ha_state.called
    await sensor.async_will_remove_from_hass()
