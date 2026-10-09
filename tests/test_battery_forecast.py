"""Battery depletion forecasts: which batteries qualify, and what is promised.

An installer plans a visit off ``depleted_at``, so a wrong date costs a trip
and a phone or a vacuum in the list is noise they learn to ignore. The
recorder queries themselves are covered in ``test_battery_forecast_recorder.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.battery_forecast import (
    RECHARGEABLE_INTEGRATIONS,
    BatteryForecaster,
    Companion,
    clean_series,
    forecast_battery,
    is_rechargeable,
    since_last_replacement,
)
from custom_components.selora_ai.const import HEALTH_BATTERY_LOW_PCT, KNOWN_INTEGRATIONS
from custom_components.selora_ai.types import BatteryForecast, BatteryForecastItem

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
DAY = 86400.0


def _days(*points: tuple[float, Any]) -> list[tuple[float, Any]]:
    """(days before NOW, state) → raw (timestamp, state) readings."""
    return [((NOW - timedelta(days=d)).timestamp(), v) for d, v in points]


def _linear(start: float, per_day: float, days: int, every: int = 1) -> list[tuple[float, Any]]:
    """A battery draining ``per_day`` from ``start``, read every ``every`` days,
    ending today."""
    return _days(*((days - d, start - per_day * d) for d in range(0, days + 1, every)))


def _forecast(
    series: list[tuple[float, Any]], level: int | None = None
) -> BatteryForecastItem | None:
    cleaned = since_last_replacement(clean_series(series))
    current = level if level is not None else round(cleaned[-1][1])
    return forecast_battery("sensor.door_battery", "dev1", current, cleaned, NOW)


# ── Exclusion ────────────────────────────────────────────────────────────


def test_a_mobile_app_phone_is_rechargeable() -> None:
    assert is_rechargeable({"mobile_app"}, [], clean_series(_linear(90, 1, 30)))


def test_a_battery_charging_binary_sensor_marks_the_device_rechargeable() -> None:
    companion = Companion("binary_sensor.tablet_charging", "battery_charging", "off")
    assert is_rechargeable({"zha"}, [companion], [])


@pytest.mark.parametrize("state", ["charging", "Discharging", "full", "not_charging"])
def test_a_charging_state_sensor_marks_the_device_rechargeable(state: str) -> None:
    companion = Companion("sensor.watch_battery_state", None, state)
    assert is_rechargeable({"zha"}, [companion], [])


def test_a_full_tank_is_not_a_charging_battery() -> None:
    """"full" is a charging state only on a sensor that is about the battery."""
    tank = Companion("sensor.dehumidifier_tank", "enum", "full", "Water tank tank_status")
    assert not is_rechargeable({"zha"}, [tank], [])


def test_a_charging_state_sensor_is_recognised_by_its_registry_name() -> None:
    companion = Companion("sensor.watch_status", "enum", "charging", "Watch Charger state")
    assert is_rechargeable({"zha"}, [companion], [])


def test_unrelated_companions_do_not() -> None:
    companions = [
        Companion("binary_sensor.door_contact", "door", "on"),
        Companion("sensor.door_temperature", "temperature", "21"),
    ]
    assert not is_rechargeable({"zha"}, companions, clean_series(_linear(80, 0.2, 60)))


def test_repeated_recharges_in_the_history_mark_the_device_rechargeable() -> None:
    cycles = _days((40, 90), (35, 40), (30, 95), (25, 45), (20, 92), (15, 50), (10, 60))
    assert is_rechargeable({"zha"}, [], clean_series(cycles))


def test_a_single_replacement_is_not_a_recharge() -> None:
    swap = _days((90, 40), (60, 20), (59, 100), (30, 95), (1, 90))
    assert not is_rechargeable({"zha"}, [], clean_series(swap))


def test_every_rechargeable_integration_is_one_the_codebase_knows() -> None:
    """The list must name real integrations, not guesses: each is in the
    integration catalog, or is the companion app (excluded from it on purpose)."""
    assert RECHARGEABLE_INTEGRATIONS - set(KNOWN_INTEGRATIONS) == {"mobile_app"}


# ── Building the series ────────────────────────────────────────────────


def test_unavailable_unknown_and_non_numeric_states_are_dropped_and_levels_clamped() -> None:
    raw = _days((3, "unavailable"), (2, "unknown"), (1, "n/a"), (0.5, "112"), (0, "-3"))
    assert [v for _ts, v in clean_series(raw)] == [100.0, 0.0]


def test_a_replacement_mid_series_restarts_the_series_and_since() -> None:
    old_battery = _linear(60, 1, 40)[:-30]  # 60% → 50% over 10 days, 40–30 days ago
    new_battery = _days(*((d, 100 - (29 - d) * 0.5) for d in range(29, -1, -1)))
    item = _forecast(old_battery + new_battery)

    assert item is not None
    assert datetime.fromisoformat(item["since"]) == NOW - timedelta(days=29)
    assert item["drain_per_day"] == pytest.approx(0.5, abs=0.01)


def test_unavailable_gaps_do_not_bend_the_fit() -> None:
    series = _linear(80, 0.5, 60)
    noisy = series + _days((45.5, "unavailable"), (20.5, "unknown"), (10.5, "unavailable"))
    assert _forecast(noisy) == _forecast(series)


# ── Minimums ──────────────────────────────────────────────────────────────


def test_too_few_distinct_levels_is_not_forecast() -> None:
    assert _forecast(_days((60, 80), (30, 80), (0, 70))) is None


def test_too_short_a_span_is_not_forecast() -> None:
    assert _forecast(_linear(80, 1, 10)) is None


def test_a_flat_series_is_not_forecast() -> None:
    assert _forecast(_days((90, 70), (60, 71), (30, 70), (0, 70))) is None


def test_a_rising_series_is_not_forecast() -> None:
    assert _forecast(_days((90, 60), (60, 62), (30, 64), (0, 66))) is None


def test_a_date_past_three_years_is_left_out() -> None:
    # 0.05%/day from 90% is ~5 years.
    assert _forecast(_linear(93, 0.05, 60)) is None


# ── Fit ───────────────────────────────────────────────────────────────────


def test_a_known_linear_drain_gives_the_expected_date() -> None:
    item = _forecast(_linear(80, 0.5, 60))  # 50% today, 0.5%/day → 100 days

    assert item is not None
    depleted = datetime.fromisoformat(item["depleted_at"])
    assert abs(depleted - (NOW + timedelta(days=100))) < timedelta(days=1)
    assert item["drain_per_day"] == pytest.approx(0.5, abs=0.001)
    assert item["level"] == 50
    assert item["points"] == 61
    assert item["confidence"] == "high"
    earliest = datetime.fromisoformat(item["depleted_range"]["earliest"])
    latest = datetime.fromisoformat(item["depleted_range"]["latest"])
    assert earliest <= depleted <= latest


def test_a_stepped_reporter_is_projected_from_its_last_step() -> None:
    """10% steps every 20 days, re-reported in between: 0.5%/day, and the last
    step (to 40%) was ten days ago, so it runs out 80 days after that."""
    steps = [(130 - 20 * k - repeat, 100 - 10 * k) for k in range(7) for repeat in (0, 5)]
    item = _forecast(_days(*steps, (0, 40)))

    assert item is not None
    assert item["drain_per_day"] == pytest.approx(0.5, abs=0.001)
    assert item["points"] == 7  # repeats at a level are not readings
    depleted = datetime.fromisoformat(item["depleted_at"])
    assert abs(depleted - (NOW + timedelta(days=70))) < timedelta(days=1)


def test_one_outlier_does_not_drag_the_slope() -> None:
    series = _linear(80, 0.5, 60)
    series[30] = (series[30][0], 5)  # a single bogus reading
    item = _forecast(series)

    assert item is not None
    assert item["drain_per_day"] == pytest.approx(0.5, abs=0.02)


def test_a_coin_cell_cliff_is_not_high_confidence() -> None:
    """Flat for months, then falling fast: no straight line follows it."""
    cliff = _days(*((d, 90 - (0.02 * (120 - d))) for d in range(120, 20, -5)))
    cliff += _days(*((d, 88 - 3 * (20 - d)) for d in range(20, -1, -2)))
    item = _forecast(cliff)

    assert item is not None
    assert item["confidence"] != "high"


def test_the_minimum_alone_is_low_confidence() -> None:
    item = _forecast(_days((15, 80), (8, 79), (0, 78)))
    assert item is not None
    assert item["confidence"] == "low"


def test_a_short_series_has_a_wide_range() -> None:
    item = _forecast(_days((15, 80), (8, 79), (0, 78)))
    assert item is not None
    earliest = datetime.fromisoformat(item["depleted_range"]["earliest"])
    latest = datetime.fromisoformat(item["depleted_range"]["latest"])
    assert latest - earliest > timedelta(days=30)
    assert latest <= NOW + timedelta(days=3 * 365)


# ── Forecaster over a home ────────────────────────────────────────────────


def _battery_device(
    hass: HomeAssistant,
    name: str,
    *,
    domain: str = "zha",
    level: str = "50",
    siblings: list[tuple[str, str | None, str]] | None = None,
) -> str:
    entry = MockConfigEntry(domain=domain, entry_id=f"{name}_entry")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(domain, name)}, name=name
    )
    ent_reg = er.async_get(hass)
    entity_id = ent_reg.async_get_or_create(
        "sensor", domain, f"{name}_battery", device_id=device.id, config_entry=entry
    ).entity_id
    hass.states.async_set(
        entity_id, level, {"device_class": "battery", "state_class": "measurement"}
    )
    for sibling_domain, device_class, state in siblings or []:
        sibling = ent_reg.async_get_or_create(
            sibling_domain,
            domain,
            f"{name}_{sibling_domain}_{device_class}",
            device_id=device.id,
            config_entry=entry,
        ).entity_id
        hass.states.async_set(
            sibling, state, {"device_class": device_class} if device_class else {}
        )
    return entity_id


async def _run(
    hass: HomeAssistant, histories: dict[str, list[tuple[float, Any]]]
) -> BatteryForecast | None:
    hass.config.components.add("recorder")
    forecaster = BatteryForecaster(hass)
    with (
        patch.object(BatteryForecaster, "_async_histories", return_value=histories),
        patch("custom_components.selora_ai.battery_forecast.datetime") as clock,
    ):
        clock.now.return_value = NOW
        clock.fromtimestamp.side_effect = datetime.fromtimestamp
        return await forecaster.async_get()


async def test_only_replaceable_batteries_are_forecast(hass: HomeAssistant) -> None:
    door = _battery_device(hass, "door")
    phone = _battery_device(hass, "phone", domain="mobile_app")
    tablet = _battery_device(hass, "tablet", siblings=[("binary_sensor", "battery_charging", "on")])
    vacuum = _battery_device(hass, "vacuum")
    drain = _linear(80, 0.5, 60)
    cycles = _days(*((d, 95 if (d // 10) % 2 else 40) for d in range(60, -1, -1)))

    forecast = await _run(hass, {door: drain, phone: drain, tablet: drain, vacuum: cycles})

    assert forecast is not None
    assert [item["entity_id"] for item in forecast["items"]] == [door]
    assert forecast["items"][0]["device_id"] == er.async_get(hass).async_get(door).device_id


async def test_items_are_soonest_first_and_skipped_devices_leave_no_trace(
    hass: HomeAssistant,
) -> None:
    slow = _battery_device(hass, "slow", level="80")
    fast = _battery_device(hass, "fast", level="20")
    unknown_now = _battery_device(hass, "gone", level="unavailable")
    flat = _battery_device(hass, "flat", level="70")

    forecast = await _run(
        hass,
        {
            slow: _linear(95, 0.25, 60),
            fast: _linear(50, 0.5, 60),
            unknown_now: _linear(80, 0.5, 60),
            flat: _days((60, 70), (30, 70), (0, 70)),
        },
    )

    assert forecast is not None
    assert [item["entity_id"] for item in forecast["items"]] == [fast, slow]
    assert all(set(item) >= {"depleted_at", "depleted_range"} for item in forecast["items"])


async def test_a_battery_swapped_since_the_last_statistic_restarts_from_the_live_level(
    hass: HomeAssistant,
) -> None:
    """Daily statistics lag up to a day. A battery replaced since must not be
    listed with the old battery's date next to its new 100%."""
    door = _battery_device(hass, "door", level="100")

    forecast = await _run(hass, {door: _linear(45, 0.5, 60)})  # old battery, down to 15%

    assert forecast is not None
    assert forecast["items"] == []


async def test_an_empty_home_is_an_empty_forecast(hass: HomeAssistant) -> None:
    forecast = await _run(hass, {})
    assert forecast == {"generated_at": NOW.isoformat(), "items": []}


async def test_no_recorder_means_no_forecast(hass: HomeAssistant) -> None:
    assert await BatteryForecaster(hass).async_get() is None


async def test_history_is_queried_at_most_every_six_hours(hass: HomeAssistant) -> None:
    hass.config.components.add("recorder")
    forecaster = BatteryForecaster(hass)
    with (
        patch.object(BatteryForecaster, "_async_histories", return_value={}) as histories,
        patch("custom_components.selora_ai.battery_forecast.datetime") as clock,
    ):
        _battery_device(hass, "door")
        for hours in (0, 1, 5.9, 6.1):
            clock.now.return_value = NOW + timedelta(hours=hours)
            await forecaster.async_get()

    assert histories.call_count == 2


# ── battery_low ───────────────────────────────────────────────────────────


async def test_an_urgent_forecast_always_has_a_battery_low_signal(hass: HomeAssistant) -> None:
    """Level ≤ 10% due inside 14 days must surface as the existing signal, not
    a new kind. Every forecast candidate is a battery sensor the health monitor
    also judges, so this holds as long as its threshold is at least 10%."""
    from custom_components.selora_ai.health_monitor import HealthMonitor
    from custom_components.selora_ai.health_store import HealthStore

    from .conftest import MockStore

    assert HEALTH_BATTERY_LOW_PCT >= 10

    lock = _battery_device(hass, "lock", level="8")
    forecast = await _run(hass, {lock: _linear(23, 1, 15)})
    assert forecast is not None
    (item,) = forecast["items"]
    assert item["level"] <= 10
    assert datetime.fromisoformat(item["depleted_at"]) - NOW < timedelta(days=14)

    with patch("custom_components.selora_ai.health_store.Store", return_value=MockStore()):
        store = HealthStore(hass)
        await HealthMonitor(hass, store).async_scan(trigger_audit=False)
        signals = await store.get_signals(kind="battery_low")

    assert [s["target"] for s in signals] == [lock]
