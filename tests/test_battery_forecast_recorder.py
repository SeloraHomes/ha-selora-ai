"""The battery forecast's recorder reads, against a real recorder.

A battery sensor with a ``state_class`` is read from daily long-term statistics
(which outlive the purge); one without falls back to recorder states.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.selora_ai.battery_forecast import BatteryForecaster


@pytest.fixture(autouse=True)
def _enable_custom_component(recorder_db_url: str, enable_custom_integrations: None) -> None:
    """Overrides the repo-wide one: the recorder's database must be set up
    before ``hass``."""


@pytest.fixture
async def recorder(recorder_mock: Any, hass: HomeAssistant) -> HomeAssistant:
    return hass


def _battery(hass: HomeAssistant, name: str, attrs: dict[str, Any]) -> str:
    entry = MockConfigEntry(domain="zha", entry_id=f"{name}_entry")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("zha", name)}
    )
    entity_id = (
        er.async_get(hass)
        .async_get_or_create("sensor", "zha", name, device_id=device.id, config_entry=entry)
        .entity_id
    )
    hass.states.async_set(entity_id, "50", {"device_class": "battery", **attrs})
    return entity_id


async def test_a_measurement_battery_is_forecast_from_daily_statistics(
    recorder: HomeAssistant,
) -> None:
    from homeassistant.components.recorder.models import StatisticMeanType
    from homeassistant.components.recorder.statistics import async_import_statistics

    # No unit on the state: unpinned, the statistics would be converted to a
    # 0-1 fraction on the way out.
    entity_id = _battery(recorder, "door", {"state_class": "measurement"})
    midnight = dt_util.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    async_import_statistics(
        recorder,
        {
            "has_sum": False,
            "mean_type": StatisticMeanType.ARITHMETIC,
            "name": None,
            "source": "recorder",
            "statistic_id": entity_id,
            "unit_class": None,
            "unit_of_measurement": "%",
        },
        [
            {"start": midnight - timedelta(days=d), "mean": 50 + d * 0.5, "min": 0, "max": 100}
            for d in range(60, 0, -1)
        ],
    )
    await async_wait_recording_done(recorder)

    forecast = await BatteryForecaster(recorder).async_get()

    assert forecast is not None
    (item,) = forecast["items"]
    assert item["entity_id"] == entity_id
    assert item["drain_per_day"] == pytest.approx(0.5, abs=0.01)
    assert item["points"] == 61  # 60 daily means + the live state


async def test_a_battery_without_state_class_is_read_from_recorder_states(
    recorder: HomeAssistant,
) -> None:
    entity_id = _battery(recorder, "remote", {})
    for state in ("49", "unavailable", "48"):
        recorder.states.async_set(entity_id, state, {"device_class": "battery"})
        await recorder.async_block_till_done()
    await async_wait_recording_done(recorder)
    forecaster = BatteryForecaster(recorder)
    (candidate,) = forecaster._candidates()
    now = dt_util.utcnow()

    histories = await forecaster._async_histories([candidate], now - timedelta(days=1), now)

    assert [state for _ts, state in histories[entity_id]] == ["50", "49", "unavailable", "48"]
