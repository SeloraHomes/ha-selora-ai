"""Reading a script's traces, as an automation's are read.

"Why did my bedtime script stop halfway?" had the same answer as for an
automation — its trace — and no tool would read it: the trace reader resolved
automations only. Runs a real script and reads back the run it traced.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai.tool_executor import ToolExecutor


@pytest.fixture
async def bedtime(hass: HomeAssistant) -> HomeAssistant:
    hass.states.async_set("input_boolean.guests", "on")
    assert await async_setup_component(
        hass,
        "script",
        {
            "script": {
                "bedtime": {
                    "alias": "Bedtime",
                    "sequence": [
                        {"event": "bedtime_started"},
                        {
                            "condition": "state",
                            "entity_id": "input_boolean.guests",
                            "state": "off",
                        },
                        {"event": "lights_off"},
                    ],
                },
                "v2": {"alias": "Bedtime v2.0", "sequence": [{"event": "v2"}]},
            }
        },
    )
    await hass.async_block_till_done()
    await hass.services.async_call("script", "bedtime", blocking=True)
    await hass.async_block_till_done()
    return hass


async def _traces(hass: HomeAssistant, ref: str) -> dict[str, Any]:
    return await ToolExecutor(hass, MagicMock(), is_admin=True).execute(
        "get_automation_traces", {"automation": ref}
    )


async def test_a_script_run_says_where_it_stopped_and_why(bedtime: HomeAssistant) -> None:
    result = await _traces(bedtime, "script.bedtime")

    assert result["trace_key"] == "script.bedtime"
    (run,) = result["traces"]
    # Recorded at the condition's leaf; the step shown is the condition itself.
    assert run["last_step"].startswith("sequence/1")
    assert run["stopped_at"]["config"]["condition"] == "state"
    assert run["stopped_at"]["config"]["entity_id"] == "input_boolean.guests"
    assert run["stopped_at"]["result"]["result"] is False


async def test_a_script_is_found_by_its_name(bedtime: HomeAssistant) -> None:
    result = await _traces(bedtime, "Bedtime")

    assert result["trace_key"] == "script.bedtime"


async def test_a_name_an_automation_shares_is_refused(bedtime: HomeAssistant) -> None:
    assert await async_setup_component(
        bedtime,
        "automation",
        {
            "automation": {
                "id": "bedtime_auto",
                "alias": "Bedtime",
                "triggers": [{"trigger": "event", "event_type": "night"}],
                "actions": [{"action": "script.bedtime"}],
            }
        },
    )
    await bedtime.async_block_till_done()

    result = await _traces(bedtime, "Bedtime")

    assert "2 automations or scripts are named" in result["error"]
    assert (await _traces(bedtime, "automation.bedtime"))["trace_key"] == "automation.bedtime_auto"


async def test_a_name_with_a_period_is_a_name(bedtime: HomeAssistant) -> None:
    result = await _traces(bedtime, "Bedtime v2.0")

    assert result["trace_key"] == "script.v2"


async def test_two_automations_sharing_a_name_are_not_resolved_to_a_script(
    bedtime: HomeAssistant,
) -> None:
    assert await async_setup_component(
        bedtime,
        "automation",
        {
            "automation": [
                {
                    "id": f"a{n}",
                    "alias": "Bedtime",
                    "triggers": [{"trigger": "event", "event_type": "night"}],
                    "actions": [],
                }
                for n in (1, 2)
            ]
        },
    )
    await bedtime.async_block_till_done()

    result = await _traces(bedtime, "Bedtime")

    assert "3 automations or scripts are named" in result["error"]
