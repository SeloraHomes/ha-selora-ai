"""Reading the recorder over a time range, and its long-term statistics.

History was one entity, the last 24 hours and the latest 50 changes, with no
way past any of them: "how much energy did we use each day last month?" or
"when did the door open last Tuesday?" had no answer, and an entity that had
since been removed was refused although the recorder still held its past.
These run against a real recorder.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
import pytest

from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.definitions import _TOOL_DEFINITIONS
from custom_components.selora_ai.mcp_server.names import TOOL_GET_ENTITY_HISTORY


@pytest.fixture(autouse=True)
def _enable_custom_component(recorder_db_url: str, enable_custom_integrations: None) -> None:
    """Overrides the repo-wide one: the recorder's database must be set up
    before ``hass``."""


@pytest.fixture
async def recorder(recorder_mock: Any, hass: HomeAssistant) -> HomeAssistant:
    return hass


async def _read(hass: HomeAssistant, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[TOOL_GET_ENTITY_HISTORY](hass, arguments)


async def _record(hass: HomeAssistant, entity_id: str, *states: str, **attrs: Any) -> None:
    for state in states:
        hass.states.async_set(entity_id, state, attrs)
        await hass.async_block_till_done()
    await async_wait_recording_done(hass)


async def test_repeats_are_folded_and_paging_reaches_the_oldest(recorder: HomeAssistant) -> None:
    await _record(recorder, "light.hall", "off", "on", "off", "on", "off", "on", "off")

    newest = await _read(recorder, entity_id="light.hall", limit=3)
    before = await _read(recorder, entity_id="light.hall", limit=3, offset=newest["next_offset"])
    oldest = await _read(recorder, entity_id="light.hall", limit=3, offset=6)

    assert [c["state"] for c in newest["changes"]] == ["off", "on", "off"]
    assert newest["older"] == 4
    assert before["older"] == 1
    assert [c["state"] for c in oldest["changes"]] == ["off"]
    assert "older" not in oldest


async def test_an_explicit_range_reads_only_inside_it(recorder: HomeAssistant) -> None:
    """A naive time is the home's local time, as the user would say it."""
    start = dt_util.now().replace(tzinfo=None) - timedelta(hours=1)
    await _record(recorder, "binary_sensor.door", "off", "on")
    middle = dt_util.utcnow()
    await _record(recorder, "binary_sensor.door", "unlocked_by_mistake")

    before = await _read(
        recorder, entity_id="binary_sensor.door", start=start.isoformat(), end=middle.isoformat()
    )
    after = await _read(recorder, entity_id="binary_sensor.door", start=middle.isoformat())

    assert [c["state"] for c in before["changes"]] == ["off", "on"]
    # The state the range opens on, then what changed inside it.
    assert [c["state"] for c in after["changes"]] == ["on", "unlocked_by_mistake"]


async def test_a_range_past_the_cap_is_refused_with_why(recorder: HomeAssistant) -> None:
    result = await _read(
        recorder, entity_id="light.hall", start="2026-01-01T00:00:00", end="2026-03-01T00:00:00"
    )

    assert "31 days" in result["error"]


async def test_several_entities_in_one_call(recorder: HomeAssistant) -> None:
    await _record(recorder, "light.hall", "off", "on")
    await _record(recorder, "switch.fan", "on")

    result = await _read(recorder, entity_ids=["light.hall", "switch.fan"])

    assert [e["entity_id"] for e in result["entities"]] == ["light.hall", "switch.fan"]
    assert result["entities"][0]["changes"][-1]["state"] == "on"


async def test_a_removed_entity_still_has_its_history(recorder: HomeAssistant) -> None:
    await _record(recorder, "sensor.old_probe", "12", "13")
    recorder.states.async_remove("sensor.old_probe")
    await async_wait_recording_done(recorder)

    result = await _read(recorder, entity_id="sensor.old_probe")

    assert "error" not in result, result
    assert [c["state"] for c in result["changes"]][:2] == ["12", "13"]


async def test_an_id_nobody_knows_says_so(recorder: HomeAssistant) -> None:
    result = await _read(recorder, entity_id="light.nope")

    assert result["changes"] == []
    assert "search_entities" in result["note"]


async def test_a_password_field_history_is_withheld(recorder: HomeAssistant) -> None:
    await _record(recorder, "input_text.wifi_key", "hunter2", "swordfish", mode="password")

    result = await _read(recorder, entity_id="input_text.wifi_key")

    assert {c["state"] for c in result["changes"]} == {"***"}


async def test_a_removed_password_field_stays_withheld(recorder: HomeAssistant) -> None:
    """The secret was recorded while the entity was one; removing the entity
    does not make its past values readable."""
    await _record(recorder, "text.alarm_code", "1234", "5678", mode="password")
    recorder.states.async_remove("text.alarm_code")
    await async_wait_recording_done(recorder)

    result = await _read(recorder, entity_id="text.alarm_code")

    assert result["changes"]
    assert {c["state"] for c in result["changes"]} == {"***"}


async def test_too_many_entities_or_a_bad_id_is_refused(recorder: HomeAssistant) -> None:
    many = await _read(recorder, entity_ids=[f"light.l{i}" for i in range(11)])
    bad = await _read(recorder, entity_id="not an id")

    assert "At most 10" in many["error"]
    assert "not an entity_id" in bad["error"]


# ── statistics ──────────────────────────────────────────────────────────────


def _import(
    hass: HomeAssistant, statistic_id: str, rows: list[dict[str, Any]], **meta: Any
) -> None:
    from homeassistant.components.recorder.models import StatisticMeanType
    from homeassistant.components.recorder.statistics import async_import_statistics

    async_import_statistics(
        hass,
        {
            "has_sum": False,
            "mean_type": StatisticMeanType.NONE,
            "name": None,
            "source": "recorder",
            "statistic_id": statistic_id,
            "unit_class": None,
            "unit_of_measurement": None,
            **meta,
        },
        rows,
    )


def _hours_ago(n: int) -> Any:
    return dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=n)


async def test_a_meter_reads_as_consumption_per_period(recorder: HomeAssistant) -> None:
    recorder.states.async_set(
        "sensor.energy", "10", {"unit_of_measurement": "kWh", "state_class": "total_increasing"}
    )
    _import(
        recorder,
        "sensor.energy",
        [{"start": _hours_ago(n), "state": 10 - n, "sum": 10 - n} for n in range(5, 1, -1)],
        has_sum=True,
        unit_of_measurement="kWh",
    )
    await async_wait_recording_done(recorder)

    result = await _read(recorder, entity_id="sensor.energy", source="statistics", hours=12)

    assert result["period"] == "hour"
    assert result["unit"] == "kWh"
    assert [set(r) for r in result["rows"]] == [{"start", "change"}] * len(result["rows"])
    assert [r["change"] for r in result["rows"]][-3:] == [1.0, 1.0, 1.0]


async def test_a_measurement_reads_as_mean_min_max(recorder: HomeAssistant) -> None:
    from homeassistant.components.recorder.models import StatisticMeanType

    _import(
        recorder,
        "sensor.temp",
        [{"start": _hours_ago(3), "mean": 20.5, "min": 19.0, "max": 22.25}],
        mean_type=StatisticMeanType.ARITHMETIC,
        unit_of_measurement="°C",
    )
    await async_wait_recording_done(recorder)

    result = await _read(recorder, entity_id="sensor.temp", source="statistics", hours=12)
    only_max = await _read(
        recorder, entity_id="sensor.temp", source="statistics", hours=12, statistic_types=["max"]
    )

    assert result["rows"][0]["mean"] == 20.5
    assert result["rows"][0]["min"] == 19.0
    assert set(only_max["rows"][0]) == {"start", "max"}


async def test_an_entity_without_statistics_is_pointed_at_history(
    recorder: HomeAssistant,
) -> None:
    recorder.states.async_set("light.hall", "on")

    result = await _read(recorder, entity_id="light.hall", source="statistics")

    assert result["rows"] == []
    assert "state_class" in result["note"]


async def test_too_many_buckets_asks_for_a_coarser_period(recorder: HomeAssistant) -> None:
    result = await _read(
        recorder, entity_id="sensor.temp", source="statistics", period="5minute", hours=24 * 30
    )

    assert "coarser period" in result["error"]


def test_mcp_and_chat_share_one_schema() -> None:
    from custom_components.selora_ai.tool_registry import TOOL_MAP

    (mcp,) = [t for t in _TOOL_DEFINITIONS if t.name == TOOL_GET_ENTITY_HISTORY]
    chat = TOOL_MAP["get_entity_history"].to_anthropic()["input_schema"]

    assert mcp.inputSchema == chat
    assert {"entity_ids", "start", "end", "source", "period", "offset"} <= set(chat["properties"])


async def test_a_busy_entity_is_read_from_its_newest_end(
    recorder: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At most _ROW_CAP rows are read per entity, whatever the range holds; the
    range narrows toward the newest so what comes back is still the newest."""
    from custom_components.selora_ai import history_reader

    monkeypatch.setattr(history_reader, "_ROW_CAP", 3)
    await _record(recorder, "sensor.power", *[str(n) for n in range(12)])

    result = await _read(recorder, entity_id="sensor.power", hours=1)

    # The newest three — not one or two left by over-narrowing.
    assert [c["state"] for c in result["changes"]] == ["9", "10", "11"]
    assert "range_start" in result
