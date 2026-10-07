"""Calendar and to-do questions reach the answer specialist with their data.

Home Assistant exposes neither events nor items as state, so a turn about them
fetches both and attaches them to the snapshot. The rendered lines are a
training contract: the models repo adds corpus examples in exactly this shape.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.components.calendar.const import DATA_COMPONENT as CALENDAR_COMPONENT
from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity
from homeassistant.components.todo.const import DATA_COMPONENT as TODO_COMPONENT
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai import _collect_entity_states
from custom_components.selora_ai.const import SELORA_LOCAL_BACKEND_LLAMA
from custom_components.selora_ai.llm_client import LLMClient
from custom_components.selora_ai.llm_client.intent import _classify_chat_intent
from custom_components.selora_ai.llm_client.schedule_context import (
    MAX_EVENTS,
    MAX_TODO_ITEMS,
    async_attach_schedule_data,
    event_window,
    is_schedule_question,
)
from custom_components.selora_ai.providers import create_provider
from custom_components.selora_ai.providers.selora_local import SeloraLocalProvider

# The Allen benchmark's questions dataset (urban-loft-au), which runs at this "now".
_NOW = datetime.datetime(2025, 4, 2, 8, 30, tzinfo=datetime.timezone(datetime.timedelta(hours=10)))

_BENCHMARK_QUESTIONS = [
    "What classes do I have today?",
    "What classes are on my personal calendar today?",
    "Who am I meeting for dinner?",
    "According to my personal calendar, who am I meeting for dinner?",
    "Am I leaving the house today?",
    "Do i have any personal calendar events away from home today?",
    "Are we leaving the house today?",
    "According to my personal calendar, are we leaving the house today?",
    "According to my personal calendar, do I have any events away from home today?",
    "How many nights this week do I need to cook?",
    "From my personal calendar, how many nights do I need to cook this week?",
    "Is anyone coming to visit?",
    "According to my calendar, is anyone visiting?",
    "How many items are on my task list?",
    "Who is on my task list to call?",
    "Who do i need to call?",
    "What chores around the house do I need to complete?",
    "What's is on my task list to buy at the grocery store?",
]


class _Calendar(CalendarEntity):
    def __init__(self, entity_id: str, events: list[CalendarEvent]) -> None:
        self.entity_id = entity_id
        self._attr_name = entity_id.split(".")[1]
        self._events = events

    @property
    def event(self) -> CalendarEvent | None:
        return None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime.datetime, end_date: datetime.datetime
    ) -> list[CalendarEvent]:
        return [
            e
            for e in self._events
            if e.start_datetime_local < end_date and e.end_datetime_local > start_date
        ]


class _TodoList(TodoListEntity):
    def __init__(self, entity_id: str, items: list[TodoItem]) -> None:
        self.entity_id = entity_id
        self._attr_name = entity_id.split(".")[1]
        self._attr_todo_items = items


def _at(day: int, hour: int, minute: int = 0) -> datetime.datetime:
    return _NOW.replace(day=day, hour=hour, minute=minute)


@pytest.fixture
async def home(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("Australia/Brisbane")
    assert await async_setup_component(hass, "calendar", {})
    assert await async_setup_component(hass, "todo", {})
    personal = _Calendar(
        "calendar.personal",
        [
            CalendarEvent(start=_at(2, 11), end=_at(2, 11, 55), summary="Chemistry class"),
            CalendarEvent(
                start=_at(2, 19),
                end=_at(2, 20, 30),
                summary="Dinner with Liza",
                description="Don't forget to bring flowers",
                location="Peninsula CR Steak & Seafood Grill",
            ),
            CalendarEvent(
                start=datetime.date(2025, 4, 5),
                end=datetime.date(2025, 4, 7),
                summary="Liza visit",
                location="Home",
            ),
            CalendarEvent(
                start=datetime.date(2025, 4, 12),
                end=datetime.date(2025, 4, 13),
                summary="Next week's trip",
            ),
        ],
    )
    await hass.data[CALENDAR_COMPONENT].async_add_entities([personal])
    tasks = _TodoList(
        "todo.tasks",
        [
            TodoItem(summary="Call Liza", uid="1", status=TodoItemStatus.NEEDS_ACTION),
            TodoItem(
                summary="Repair the terrace light fixture",
                uid="2",
                status=TodoItemStatus.NEEDS_ACTION,
            ),
            TodoItem(summary="Buy salad", uid="3", status=TodoItemStatus.COMPLETED),
        ],
    )
    await hass.data[TODO_COMPONENT].async_add_entities([tasks])
    await hass.async_block_till_done()


async def _entity_block(hass: HomeAssistant, message: str) -> str:
    """What the provider renders for ``message``, as the benchmark would run it."""
    entities = _collect_entity_states(hass)
    with patch("homeassistant.util.dt.now", return_value=_NOW):
        relevant = await async_attach_schedule_data(hass, message, [], entities)
    provider = SeloraLocalProvider(
        MagicMock(), host="hub.local", selora_local_backend=SELORA_LOCAL_BACKEND_LLAMA
    )
    provider.set_call_kind("chat_answer")
    try:
        return provider._format_entities_block(relevant)
    finally:
        provider.set_call_kind(None)


@pytest.mark.parametrize("message", _BENCHMARK_QUESTIONS)
def test_calendar_and_todo_questions_route_to_answer(message: str) -> None:
    entities: list[Any] = [
        {"entity_id": "calendar.personal", "state": "off", "attributes": {}},
        {"entity_id": "todo.tasks", "state": "2", "attributes": {}},
        {"entity_id": "light.terrace_light", "state": "off", "attributes": {}},
    ]
    assert _classify_chat_intent(message, entities) == "answer"


@pytest.mark.parametrize(
    "message",
    [
        "Can you add milk to my shopping list?",
        "add milk to my shopping list",
        "turn on the dining room light for dinner",
        "Schedule the porch light to turn on every evening",
        "Could you please remove eggs from the grocery list?",
        "Turn on the dinner lights?",
    ],
)
def test_requests_about_lists_are_not_schedule_questions(message: str) -> None:
    """Only questions are rerouted; a request keeps whatever route it had."""
    assert not is_schedule_question(message)


@pytest.mark.parametrize(
    "message",
    ["Can you tell me what's on my calendar", "show me my task list", "Could you list my chores?"],
)
def test_polite_reads_are_schedule_questions(message: str) -> None:
    assert is_schedule_question(message)


def test_the_window_is_the_day_the_question_names() -> None:
    def days(message: str) -> tuple[int, int]:
        start, end = event_window(message, _NOW)
        return start.day, end.day

    start, _end = event_window("What classes do I have today?", _NOW)
    assert start.isoformat() == "2025-04-02T00:00:00+10:00"
    assert days("What classes do I have today?") == (2, 3)
    assert days("What's on my calendar tomorrow?") == (3, 4)
    assert days("What was on my calendar yesterday?") == (1, 2)
    assert days("What's on my calendar today and tomorrow?") == (2, 4)
    assert days("Is anyone coming to visit?") == (2, 9)
    # Finished events cannot take the next one's place under the cap.
    assert event_window("When is my next appointment?", _NOW)[0] == _NOW
    assert event_window("What's my next appointment today?", _NOW)[0] == _NOW
    assert days("What's my next appointment tomorrow?") == (3, 4)


async def test_a_today_question_gets_todays_events(hass: HomeAssistant, home: None) -> None:
    block = await _entity_block(hass, "Am I leaving the house today?")
    assert block.splitlines()[1:4] == [
        "  - entity_id=calendar.personal; state=off; friendly_name=personal; "
        "today=2025-04-02; events:",
        "      - Chemistry class (start=2025-04-02T11:00, end=2025-04-02T11:55)",
        "      - Dinner with Liza (start=2025-04-02T19:00, end=2025-04-02T20:30, "
        "location=Peninsula CR Steak & Seafood Grill)",
    ]


async def test_an_open_question_gets_the_coming_week(hass: HomeAssistant, home: None) -> None:
    """All-day events keep their dates, with Home Assistant's exclusive end."""
    block = await _entity_block(hass, "Is anyone coming to visit?")
    assert "      - Liza visit (start=2025-04-05, end=2025-04-07, location=Home)" in block
    assert "Next week's trip" not in block


async def test_an_empty_calendar_says_so(hass: HomeAssistant, home: None) -> None:
    await hass.data[CALENDAR_COMPONENT].async_add_entities([_Calendar("calendar.work", [])])
    await hass.async_block_till_done()
    block = await _entity_block(hass, "What's on my work calendar today?")
    assert block.splitlines()[1] == (
        "  - entity_id=calendar.work; state=off; friendly_name=work; today=2025-04-02; events=none"
    )


async def test_a_task_question_gets_the_open_items(hass: HomeAssistant, home: None) -> None:
    block = await _entity_block(hass, "Who do i need to call?")
    assert block.splitlines()[1:4] == [
        "  - entity_id=todo.tasks; state=2; friendly_name=tasks; open_items (2):",
        "      - Call Liza",
        "      - Repair the terrace light fixture",
    ]
    assert "calendar.personal" not in block


@pytest.mark.parametrize(
    "message",
    ["turn on the kitchen light", "Schedule the porch light to turn on every evening"],
)
async def test_other_turns_are_left_alone(hass: HomeAssistant, home: None, message: str) -> None:
    """Even with calendars in the snapshot, a command keeps its targets first."""
    relevant: list[Any] = [{"entity_id": "light.porch", "state": "on", "attributes": {}}]
    entities = [
        *_collect_entity_states(hass),
        {"entity_id": "calendar.personal", "state": "off", "attributes": {}},
    ]
    assert await async_attach_schedule_data(hass, message, relevant, entities) is relevant


async def test_without_the_calendar_integration_the_line_stays_plain(hass: HomeAssistant) -> None:
    entities: list[Any] = [{"entity_id": "calendar.work", "state": "off", "attributes": {}}]
    (calendar,) = await async_attach_schedule_data(
        hass, "What's on my calendar today?", [], entities
    )
    assert "events" not in calendar["attributes"]


async def test_the_earliest_events_are_kept_and_count_against_the_line_cap(
    hass: HomeAssistant,
) -> None:
    await hass.config.async_set_time_zone("Australia/Brisbane")
    assert await async_setup_component(hass, "calendar", {})
    busy = [
        CalendarEvent(start=_at(2, 9 + i), end=_at(2, 9 + i, 30), summary=f"Meeting {i}")
        for i in reversed(range(MAX_EVENTS + 4))
    ]
    await hass.data[CALENDAR_COMPONENT].async_add_entities([_Calendar("calendar.work", busy)])
    await hass.async_block_till_done()
    lights: list[Any] = [
        {"entity_id": f"light.l{i}", "state": "off", "attributes": {}} for i in range(80)
    ]
    entities = _collect_entity_states(hass) + lights
    with patch("homeassistant.util.dt.now", return_value=_NOW):
        relevant = await async_attach_schedule_data(
            hass, "What meetings do I have today?", lights, entities
        )
    provider = SeloraLocalProvider(
        MagicMock(), host="hub.local", selora_local_backend=SELORA_LOCAL_BACKEND_LLAMA
    )
    provider.set_call_kind("chat_answer")
    block = provider._format_entities_block(relevant)
    provider.set_call_kind(None)
    lines = block.splitlines()[1:-1]
    assert lines[0].endswith(f"; events ({MAX_EVENTS + 4}, first {MAX_EVENTS}):")
    meetings = [ln for ln in lines if ln.startswith("      - Meeting")]
    assert len(meetings) == MAX_EVENTS
    assert meetings[0].startswith("      - Meeting 0 (start=2025-04-02T09:00")
    assert len(lines) == 60


async def test_the_allowlist_does_not_hide_them_from_a_question(
    hass: HomeAssistant, home: None
) -> None:
    """The shipped snapshot leaves calendars and to-do lists out; a question still reads them."""
    ids = {e["entity_id"] for e in _collect_entity_states(hass)}
    assert not ids & {"calendar.personal", "todo.tasks"}
    assert "calendar.personal" in await _entity_block(hass, "Who am I meeting for dinner?")
    assert "calendar.personal" not in await _entity_block(hass, "turn on the light for dinner")


async def test_a_chat_turn_hands_the_answer_specialist_the_events(
    hass: HomeAssistant, home: None
) -> None:
    provider = create_provider("selora_local", hass)
    seen: dict[str, Any] = {}

    async def send_request(*_args: Any, **_kwargs: Any) -> tuple[str | None, str | None]:
        seen["kind"] = provider._call_kind.get()
        seen["turn"] = provider._build_training_user_content()
        return None, "stubbed"

    provider.send_request = send_request  # type: ignore[method-assign]
    with patch("homeassistant.util.dt.now", return_value=_NOW):
        await LLMClient(hass, provider).architect_chat(
            "Who am I meeting for dinner?", entities=_collect_entity_states(hass)
        )
    assert seen["kind"] == "chat_answer"
    assert "      - Dinner with Liza (start=2025-04-02T19:00" in seen["turn"]


async def test_a_capped_list_shows_its_total_and_is_not_counted_short(
    hass: HomeAssistant,
) -> None:
    assert await async_setup_component(hass, "todo", {})
    chores = [
        TodoItem(summary=f"Chore {i}", uid=str(i), status=TodoItemStatus.NEEDS_ACTION)
        for i in range(MAX_TODO_ITEMS + 2)
    ]
    await hass.data[TODO_COMPONENT].async_add_entities([_TodoList("todo.chores", chores)])
    await hass.async_block_till_done()
    message = "How many chores are on my list?"
    relevant = await async_attach_schedule_data(hass, message, [], _collect_entity_states(hass))
    provider = create_provider("selora_local", hass)
    provider.set_call_kind("chat_answer")
    provider.set_chat_context(
        user_message=message, entities=relevant, existing_automations=[], history=[]
    )
    block = provider._format_entities_block(relevant)
    assert block.splitlines()[1].endswith(
        f"; open_items ({MAX_TODO_ITEMS + 2}, first {MAX_TODO_ITEMS}):"
    )
    assert provider._maybe_todo_question_envelope() is None
    provider.set_call_kind(None)


def test_an_entry_that_does_not_fit_says_how_many_it_shows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = SeloraLocalProvider(
        MagicMock(), host="hub.local", selora_local_backend=SELORA_LOCAL_BACKEND_LLAMA
    )
    monkeypatch.setattr(provider, "_entity_line_cap", lambda: 4)
    calendar = {
        "entity_id": "calendar.work",
        "state": "on",
        "attributes": {
            "today": "2025-04-02",
            "events": [{"summary": f"Meeting {i}"} for i in range(10)],
        },
    }
    light = {"entity_id": "light.desk", "state": "on", "attributes": {}}
    block = provider._format_entities_block([calendar, light])
    assert block.splitlines()[1:] == [
        "  - entity_id=calendar.work; state=on; friendly_name=calendar.work; "
        "today=2025-04-02; events (10, first 3):",
        "      - Meeting 0",
        "      - Meeting 1",
        "      - Meeting 2",
        "  - ... (1 more entities not listed)",
    ]
    # Lists left out still count once the cap is reached.
    listed = {**light, "entity_id": "todo.chores", "attributes": {"schedule_entities_omitted": 2}}
    assert provider._format_entities_block([calendar, light, listed]).splitlines()[-1] == (
        "  - ... (4 more entities not listed)"
    )
    # No room for a row: the plain line, not "events=none".
    monkeypatch.setattr(provider, "_entity_line_cap", lambda: 1)
    assert provider._format_entities_block([calendar]).splitlines()[1] == (
        "  - entity_id=calendar.work; state=on; friendly_name=calendar.work"
    )


async def test_calendars_left_out_are_counted_and_not_answered_short(
    hass: HomeAssistant, home: None
) -> None:
    extra = [_Calendar(f"calendar.extra_{i}", []) for i in range(3)]
    await hass.data[CALENDAR_COMPONENT].async_add_entities(extra)
    await hass.async_block_till_done()
    message = "What's on my calendar today?"
    block = await _entity_block(hass, message)
    assert block.splitlines()[-1] == "  - ... (1 more entities not listed)"
    provider = create_provider("selora_local", hass)
    with patch("homeassistant.util.dt.now", return_value=_NOW):
        relevant = await async_attach_schedule_data(hass, message, [], _collect_entity_states(hass))
    provider.set_call_kind("chat_answer")
    provider.set_chat_context(
        user_message=message, entities=relevant, existing_automations=[], history=[]
    )
    assert provider._maybe_calendar_question_envelope() is None
    provider.set_call_kind(None)


async def test_the_calendar_handler_leaves_another_day_to_the_model(
    hass: HomeAssistant, home: None
) -> None:
    """It scopes only today or the week, so it would answer tomorrow with today's events."""
    provider = create_provider("selora_local", hass)
    for message, answered in (
        ("What's on my calendar today?", True),
        ("What's on my calendar tomorrow?", False),
        ("Do I have class on Friday?", False),
    ):
        with patch("homeassistant.util.dt.now", return_value=_NOW):
            relevant = await async_attach_schedule_data(
                hass, message, [], _collect_entity_states(hass)
            )
        provider.set_call_kind("chat_answer")
        provider.set_chat_context(
            user_message=message, entities=relevant, existing_automations=[], history=[]
        )
        assert (provider._maybe_calendar_question_envelope() is not None) is answered, message
        provider.set_call_kind(None)
