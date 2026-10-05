"""Tests for listing, adding, changing and removing calendar events over MCP.

The calendar here is a small in-memory one, registered with Home Assistant's
calendar component the way an integration's would be — the Local Calendar
needs a library the test environment does not install.
"""

from __future__ import annotations

import datetime
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.components.calendar.const import DATA_COMPONENT, CalendarEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest

from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import (
    TOOL_DELETE_CALENDAR_EVENT,
    TOOL_LIST_CALENDAR_EVENTS,
    TOOL_SET_CALENDAR_EVENT,
)


class _Calendar(CalendarEntity):
    """Keeps events in a dict by uid, and records how each change was asked."""

    def __init__(self, entity_id: str, features: CalendarEntityFeature) -> None:
        self.entity_id = entity_id
        self._attr_name = entity_id.split(".")[1]
        self._attr_supported_features = features
        self.events: dict[str, CalendarEvent] = {}
        self.calls: list[tuple[str, Any]] = []

    @property
    def event(self) -> CalendarEvent | None:
        return None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime.datetime, end_date: datetime.datetime
    ) -> list[CalendarEvent]:
        return [
            e
            for e in self.events.values()
            if e.start_datetime_local < end_date and e.end_datetime_local > start_date
        ]

    async def async_create_event(self, **kwargs: Any) -> None:
        uid = f"uid-{len(self.events) + 1}"
        self.events[uid] = CalendarEvent(
            start=kwargs["dtstart"],
            end=kwargs["dtend"],
            summary=kwargs["summary"],
            description=kwargs.get("description"),
            location=kwargs.get("location"),
            rrule=kwargs.get("rrule"),
            uid=uid,
        )

    async def async_update_event(
        self,
        uid: str,
        event: dict[str, Any],
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        self.calls.append(("update", (uid, recurrence_id, recurrence_range)))
        self.events[uid] = CalendarEvent(
            start=event["dtstart"],
            end=event["dtend"],
            summary=event["summary"],
            description=event.get("description"),
            uid=uid,
        )

    async def async_delete_event(
        self, uid: str, recurrence_id: str | None = None, recurrence_range: str | None = None
    ) -> None:
        self.calls.append(("delete", (uid, recurrence_id, recurrence_range)))
        self.events.pop(uid)


ALL = (
    CalendarEntityFeature.CREATE_EVENT
    | CalendarEntityFeature.UPDATE_EVENT
    | CalendarEntityFeature.DELETE_EVENT
)


@pytest.fixture
async def family(hass: HomeAssistant) -> _Calendar:
    assert await async_setup_component(hass, "calendar", {})
    calendar = _Calendar("calendar.family", ALL)
    read_only = _Calendar("calendar.holidays", CalendarEntityFeature(0))
    await hass.data[DATA_COMPONENT].async_add_entities([calendar, read_only])
    await hass.async_block_till_done()
    return calendar


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[tool](hass, arguments)


def _tomorrow_at(hour: int) -> str:
    day = dt_util.now().date() + datetime.timedelta(days=1)
    return datetime.datetime.combine(day, datetime.time(hour)).isoformat()


async def test_an_event_is_added_and_listed_with_its_uid(
    hass: HomeAssistant, family: _Calendar
) -> None:
    added = await _mcp(
        hass,
        TOOL_SET_CALENDAR_EVENT,
        entity_id="calendar.family",
        summary="Dentist",
        start=_tomorrow_at(14),
        end=_tomorrow_at(15),
        location="Main St",
    )
    assert added["status"] == "added", added

    listed = await _mcp(hass, TOOL_LIST_CALENDAR_EVENTS, entity_id="calendar.family")

    (event,) = listed["events"]
    assert event["summary"] == "Dentist"
    assert event["location"] == "Main St"
    assert event["uid"] == "uid-1"
    assert listed["can_change"] is True


async def test_a_change_replaces_the_event_by_uid(hass: HomeAssistant, family: _Calendar) -> None:
    await _mcp(
        hass,
        TOOL_SET_CALENDAR_EVENT,
        entity_id="calendar.family",
        summary="Dentist",
        start=_tomorrow_at(14),
        end=_tomorrow_at(15),
    )

    changed = await _mcp(
        hass,
        TOOL_SET_CALENDAR_EVENT,
        entity_id="calendar.family",
        uid="uid-1",
        summary="Dentist (moved)",
        start=_tomorrow_at(16),
        end=_tomorrow_at(17),
        recurrence_id="20261006T140000",
        recurrence_range="THISANDFUTURE",
    )

    assert changed["status"] == "changed", changed
    assert family.events["uid-1"].summary == "Dentist (moved)"
    assert family.calls == [("update", ("uid-1", "20261006T140000", "THISANDFUTURE"))]


async def test_an_event_is_removed(hass: HomeAssistant, family: _Calendar) -> None:
    await _mcp(
        hass,
        TOOL_SET_CALENDAR_EVENT,
        entity_id="calendar.family",
        summary="Dentist",
        start=_tomorrow_at(14),
        end=_tomorrow_at(15),
    )

    removed = await _mcp(hass, TOOL_DELETE_CALENDAR_EVENT, entity_id="calendar.family", uid="uid-1")

    assert removed["status"] == "removed", removed
    assert family.events == {}


async def test_what_home_assistant_would_refuse_is_refused(
    hass: HomeAssistant, family: _Calendar
) -> None:
    # A date-only start with a datetime end is not an event Home Assistant takes.
    result = await _mcp(
        hass,
        TOOL_SET_CALENDAR_EVENT,
        entity_id="calendar.family",
        summary="Trip",
        start="2026-11-01",
        end=_tomorrow_at(9),
    )

    assert "would refuse" in result["error"]
    assert family.events == {}


async def test_a_calendar_that_takes_no_changes_says_so(
    hass: HomeAssistant, family: _Calendar
) -> None:
    result = await _mcp(
        hass,
        TOOL_SET_CALENDAR_EVENT,
        entity_id="calendar.holidays",
        summary="Day off",
        start="2026-12-24",
        end="2026-12-25",
    )

    assert "does not allow adding" in result["error"]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"recurrence_id": "20261006T140000"}, "pass its uid"),
        ({"uid": "uid-1", "recurrence_range": "THIS_AND_FUTURE"}, "must be 'THISANDFUTURE'"),
        ({"uid": "uid-1", "recurrence_range": "THISANDFUTURE"}, "needs the recurrence_id"),
    ],
)
async def test_an_occurrence_is_picked_the_way_home_assistant_reads_it(
    hass: HomeAssistant, family: _Calendar, arguments: dict[str, Any], message: str
) -> None:
    result = await _mcp(
        hass,
        TOOL_SET_CALENDAR_EVENT,
        entity_id="calendar.family",
        summary="Standup",
        start=_tomorrow_at(9),
        end=_tomorrow_at(10),
        **arguments,
    )

    assert message in result["error"]


async def test_the_window_is_bounded(hass: HomeAssistant, family: _Calendar) -> None:
    result = await _mcp(
        hass,
        TOOL_LIST_CALENDAR_EVENTS,
        entity_id="calendar.family",
        start="2026-01-01",
        end="2028-01-01",
    )

    assert "at most 366 days" in result["error"]


async def test_event_text_is_treated_as_untrusted(hass: HomeAssistant, family: _Calendar) -> None:
    family.events["x"] = CalendarEvent(
        start=dt_util.now() + datetime.timedelta(hours=1),
        end=dt_util.now() + datetime.timedelta(hours=2),
        summary="Party",
        description="x" * 5000,
        uid="x",
    )

    listed = await _mcp(hass, TOOL_LIST_CALENDAR_EVENTS, entity_id="calendar.family")

    assert len(listed["events"][0]["description"]) <= 1001


async def test_only_admins_change_a_calendar() -> None:
    assert TOOL_LIST_CALENDAR_EVENTS in mcp_access._READ_ONLY_TOOLS
    assert TOOL_SET_CALENDAR_EVENT in mcp_access._ADMIN_TOOLS
    assert TOOL_DELETE_CALENDAR_EVENT in mcp_access._ADMIN_TOOLS
