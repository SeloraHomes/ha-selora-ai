"""The schedule helper: weekly on/off time blocks, created and changed.

"Make a schedule for the heating: weekdays 6 to 8 and 17 to 22" had no tool —
schedules were listed but could not be created or changed. Home Assistant's
own schema checks the blocks (times, order, overlap); a day named wrongly is
refused rather than dropped, or the schedule would never come on.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai.helper_manager import CREATABLE_HELPER_DOMAINS
from custom_components.selora_ai.mcp_server.scripts_helpers import (
    _tool_create_helper,
    _tool_update_helper,
)
from custom_components.selora_ai.tool_executor import ToolExecutor

_WEEKDAY = [{"from": "06:00:00", "to": "08:00:00"}, {"from": "17:00:00", "to": "22:00:00"}]


@pytest.fixture
def expected_lingering_timers() -> bool:
    """A schedule entity arms a timer for its next on/off change, as it should."""
    return True


@pytest.fixture
async def schedules(hass: HomeAssistant) -> HomeAssistant:
    assert await async_setup_component(hass, "schedule", {})
    await hass.async_block_till_done()
    return hass


async def _stored(hass: HomeAssistant, entity_id: str) -> dict[str, Any]:
    from homeassistant.helpers import entity_registry as er

    from custom_components.selora_ai.helper_manager import _helper_collection

    item_id = er.async_get(hass).async_get(entity_id).unique_id
    return _helper_collection(hass, "schedule").data[item_id]


async def test_a_schedule_is_created_with_its_week(schedules: HomeAssistant) -> None:
    result = await _tool_create_helper(
        schedules,
        {
            "domain": "schedule",
            "name": "Heating",
            "schedule": {"monday": _WEEKDAY, "fri": _WEEKDAY, "Saturday": []},
        },
    )

    assert result["status"] == "created", result
    stored = await _stored(schedules, result["entity_id"])
    assert stored["monday"] == _WEEKDAY
    assert stored["friday"] == _WEEKDAY
    assert stored["sunday"] == []


@pytest.mark.parametrize(
    ("week", "says"),
    [
        ({"monday": [{"from": "09:00:00", "to": "08:00:00"}]}, "refuse"),
        (
            {
                "monday": [
                    {"from": "07:00:00", "to": "09:00:00"},
                    {"from": "08:00:00", "to": "10:00:00"},
                ]
            },
            "refuse",
        ),
        ({"mon-fri": _WEEKDAY}, "is not a day"),
        ("weekdays 6-8", "object of days"),
    ],
)
async def test_a_week_that_would_not_work_creates_nothing(
    schedules: HomeAssistant, week: Any, says: str
) -> None:
    result = await _tool_create_helper(
        schedules, {"domain": "schedule", "name": "Heating", "schedule": week}
    )

    assert says in result["error"]
    assert not schedules.states.async_entity_ids("schedule")


async def test_an_update_changes_only_the_days_named(schedules: HomeAssistant) -> None:
    created = await _tool_create_helper(
        schedules,
        {
            "domain": "schedule",
            "name": "Heating",
            "schedule": {"monday": _WEEKDAY, "sunday": _WEEKDAY},
        },
    )

    result = await _tool_update_helper(
        schedules,
        {
            "entity_id": created["entity_id"],
            "schedule": {"monday": [{"from": "05:30:00", "to": "07:00:00"}], "sunday": []},
        },
    )

    assert result["status"] == "updated", result
    stored = await _stored(schedules, created["entity_id"])
    assert stored["monday"] == [{"from": "05:30:00", "to": "07:00:00"}]
    assert stored["sunday"] == []
    assert stored["name"] == "Heating"


async def test_chat_proposes_it_for_the_panel_to_create(schedules: HomeAssistant) -> None:
    result = await ToolExecutor(schedules, MagicMock(), is_admin=True).execute(
        "create_helper",
        {"domain": "schedule", "name": "Heating", "schedule": {"monday": _WEEKDAY}},
    )

    action = result["client_action"]
    assert action["domain"] == "schedule"
    assert action["fields"]["monday"] == _WEEKDAY


def test_the_panel_creates_every_helper_the_backend_proposes() -> None:
    """The panel holds its own allowlist of helper types (client-actions.js);
    one missing there turns a proposal into a card whose button always fails."""
    source = (
        Path(__file__).parent.parent
        / "custom_components/selora_ai/frontend/src/panel/client-actions.js"
    ).read_text()
    block = source[
        source.index("const HELPER_FIELDS = {") : source.index(
            "};", source.index("const HELPER_FIELDS")
        )
    ]
    panel_domains = set(re.findall(r"^  (\w+): \[", block, re.MULTILINE))

    assert panel_domains == set(CREATABLE_HELPER_DOMAINS)
    for day in ("monday", "sunday"):
        assert f'"{day}"' in block
