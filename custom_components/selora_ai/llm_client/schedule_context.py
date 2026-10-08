"""Calendar events and to-do items for low-context turns about them.

Home Assistant exposes neither as state: a calendar's state is whether an
event is on now, a to-do list's is its open count. A turn about a schedule or
a list fetches them (``calendar.get_events`` / ``todo.get_items``) and attaches
them to the entity snapshot as ``attributes.events`` / ``attributes.todo_items``,
which Selora AI Local renders in the layout pinned in
``docs/dev/selora-local-prompts.md``.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
import voluptuous as vol

from ..const import SCHEDULE_ENTITIES_OMITTED

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..types import EntitySnapshot

_LOGGER = logging.getLogger(__name__)

CALENDAR_TOPIC = re.compile(
    r"\b(?:calendar|schedule|agenda|appointments?|events?|class(?:es)?|"
    r"meetings?|dinner|lunch|leaving\s+the\s+house|away\s+from\s+home|"
    r"out\s+of\s+the\s+house|visit(?:ing|ors?|s)?|cook)\b",
    re.IGNORECASE,
)
TODO_TOPIC = re.compile(
    r"\b(?:to-?do|task\s+list|tasks?|chores?|shopping\s+list|grocer(?:y|ies)|"
    r"need\s+to\s+(?:call|buy|do|complete|get|pick\s+up|finish))\b",
    re.IGNORECASE,
)

# "According to my calendar, is anyone visiting?" — the question follows the preamble.
_SOURCE_PREAMBLE = re.compile(
    r"^\s*(?:according\s+to|from|on|in|looking\s+at)\s+(?:my|our|the)\b[^,]*,\s*",
    re.IGNORECASE,
)
_QUESTION_START = re.compile(
    r"^(?:what|what's|whats|where|when|why|how|which|who|who's|whos|is|are|am|"
    r"was|were|do|does|did|have|has|will|should|can|could|would|tell\s+me|show\s+me|"
    r"list|any(?:one|body|thing)?)\b",
    re.IGNORECASE,
)
# Changes to a calendar or list ("can you add milk to the shopping list?"), and
# device commands that mention one ("turn on the dinner lights?").
_REQUEST_START = re.compile(
    r"^(?:please\s+)?(?:(?:can|could|would|will)\s+(?:you|we)\s+(?:please\s+)?)?"
    r"(?:add|remove|delete|put|mark|create|schedule|remind|move|cancel|book|clear|"
    r"check\s+off|turn|switch|toggle|set|start|stop|play|pause|resume|open|close|"
    r"lock|unlock|dim|brighten|activate|deactivate|enable|disable|run|trigger)\b",
    re.IGNORECASE,
)
_TODAY = re.compile(r"\b(?:today|tonight|this\s+(?:morning|afternoon|evening))\b", re.IGNORECASE)
_TOMORROW = re.compile(r"\btomorrow\b", re.IGNORECASE)
_YESTERDAY = re.compile(r"\byesterday\b", re.IGNORECASE)
_NEXT = re.compile(r"\b(?:next|upcoming|coming\s+up)\b", re.IGNORECASE)

_WEEK_DAYS = 7
# A cloud calendar that hangs must not hold the chat turn: past this the
# question is answered from the plain entity line.
_FETCH_TIMEOUT_S = 8
# The window the entity-line cap already bounds; every detail line counts against it.
MAX_EVENTS = 10
MAX_TODO_ITEMS = 15
_MAX_LISTS = 3


def is_schedule_question(message: str) -> bool:
    """A question about a calendar or a to-do list, which only the answer specialist can read."""
    if not (CALENDAR_TOPIC.search(message) or TODO_TOPIC.search(message)):
        return False
    question = _SOURCE_PREAMBLE.sub("", message.strip())
    if _REQUEST_START.match(question):
        return False
    return question.rstrip().endswith("?") or bool(_QUESTION_START.match(question))


def event_window(
    message: str, now: datetime.datetime
) -> tuple[datetime.datetime, datetime.datetime]:
    """The days the question names (yesterday, today, tomorrow), else the coming week.

    Midnight in ``now``'s own zone, which is the one its date belongs to. A
    question about the next event starts at ``now``, so finished ones cannot
    take its place under the cap.
    """
    start = datetime.datetime.combine(now.date(), datetime.time.min, tzinfo=now.tzinfo)
    named = [
        offset
        for offset, pattern in ((-1, _YESTERDAY), (0, _TODAY), (1, _TOMORROW))
        if pattern.search(message)
    ]
    if named:
        lower = start + datetime.timedelta(days=min(named))
        upper = start + datetime.timedelta(days=max(named) + 1)
    else:
        lower, upper = start, start + datetime.timedelta(days=_WEEK_DAYS)
    if _NEXT.search(message) and lower < now < upper:
        lower = now
    return lower, upper


def _compact_time(value: Any, now: datetime.datetime) -> str:
    """``2025-04-02T11:00`` in ``now``'s zone, or the date alone for an all-day event."""
    text = str(value or "").strip()
    parsed = dt_util.parse_datetime(text) if ":" in text else None
    if parsed is None:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now.tzinfo)
    return parsed.astimezone(now.tzinfo).strftime("%Y-%m-%dT%H:%M")


async def _service(
    hass: HomeAssistant, domain: str, service: str, data: dict[str, Any], ids: list[str]
) -> dict[str, Any]:
    if not ids or not hass.services.has_service(domain, service):
        return {}
    try:
        async with asyncio.timeout(_FETCH_TIMEOUT_S):
            response = await hass.services.async_call(
                domain,
                service,
                data,
                target={"entity_id": ids},
                blocking=True,
                return_response=True,
            )
    except (HomeAssistantError, vol.Invalid, TimeoutError) as exc:
        _LOGGER.debug("%s.%s failed for %s: %s", domain, service, ids, exc)
        return {}
    return response if isinstance(response, dict) else {}


def _pick(
    hass: HomeAssistant, entities: list[EntitySnapshot], domain: str, message: str
) -> tuple[list[EntitySnapshot], int]:
    """This domain's entities, the ones the message names first, at most three, and how many were left out.

    Read from Home Assistant when the snapshot left them out: the command
    allowlist keeps the model from targeting them, not from reading them.
    """
    words = set(re.findall(r"\w+", message.casefold()))
    found = [e for e in entities if str(e.get("entity_id", "")).startswith(f"{domain}.")]
    if not found:
        found = [
            {
                "entity_id": state.entity_id,
                "state": state.state,
                "attributes": {"friendly_name": state.attributes.get("friendly_name", "")},
            }
            for state in hass.states.async_all(domain)
        ]

    def named(e: EntitySnapshot) -> bool:
        name = str((e.get("attributes") or {}).get("friendly_name") or e.get("entity_id", ""))
        return bool(words & set(re.findall(r"\w+", name.casefold())))

    found.sort(key=lambda e: not named(e))
    return found[:_MAX_LISTS], max(len(found) - _MAX_LISTS, 0)


def _mark_omitted(picked: list[EntitySnapshot], first: int, omitted: int) -> None:
    """Record the calendars or lists left out on ``picked[first]``, so the reply knows the data is partial."""
    if omitted and len(picked) > first:
        e = picked[first]
        picked[first] = {
            **e,
            "attributes": {**(e.get("attributes") or {}), SCHEDULE_ENTITIES_OMITTED: omitted},
        }


async def async_attach_schedule_data(
    hass: HomeAssistant,
    message: str,
    relevant: list[EntitySnapshot],
    entities: list[EntitySnapshot],
) -> list[EntitySnapshot]:
    """``relevant`` with the calendars or to-do lists a question asks about first, their data attached.

    Returns ``relevant`` unchanged for anything but a schedule question, so a
    command or automation keeps its targets inside the line cap.
    """
    if not is_schedule_question(message):
        return relevant
    picked: list[EntitySnapshot] = []
    if CALENDAR_TOPIC.search(message):
        calendars, omitted = _pick(hass, entities, "calendar", message)
        now = dt_util.now()
        start, end = event_window(message, now)
        response = await _service(
            hass,
            "calendar",
            "get_events",
            {"start_date_time": start, "end_date_time": end},
            [str(e["entity_id"]) for e in calendars],
        )
        for e in calendars:
            rows = (response.get(e["entity_id"]) or {}).get("events")
            if not isinstance(rows, list):
                picked.append(e)
                continue
            found = sorted(
                (
                    {
                        "summary": str(ev.get("summary") or ""),
                        "start": _compact_time(ev.get("start"), now),
                        "end": _compact_time(ev.get("end"), now),
                        "location": str(ev.get("location") or ""),
                    }
                    for ev in rows
                    if isinstance(ev, dict)
                ),
                # An all-day date sorts before that day's timed events.
                key=lambda ev: ev["start"],
            )
            attrs = {**(e.get("attributes") or {}), "today": now.date().isoformat()}
            attrs["events"] = found[:MAX_EVENTS]
            if len(found) > MAX_EVENTS:
                attrs["events_total"] = len(found)
            picked.append({**e, "attributes": attrs})
        _mark_omitted(picked, 0, omitted)
    if TODO_TOPIC.search(message):
        lists, omitted = _pick(hass, entities, "todo", message)
        first = len(picked)
        response = await _service(
            hass,
            "todo",
            "get_items",
            {"status": ["needs_action"]},
            [str(e["entity_id"]) for e in lists],
        )
        for e in lists:
            rows = (response.get(e["entity_id"]) or {}).get("items")
            if not isinstance(rows, list):
                picked.append(e)
                continue
            items = [
                str(item.get("summary") or "")
                for item in rows
                if isinstance(item, dict) and item.get("summary")
            ]
            attrs = {**(e.get("attributes") or {}), "todo_items": items[:MAX_TODO_ITEMS]}
            if len(items) > MAX_TODO_ITEMS:
                attrs["todo_items_total"] = len(items)
            picked.append({**e, "attributes": attrs})
        _mark_omitted(picked, first, omitted)
    if not picked:
        return relevant
    ids = {e.get("entity_id") for e in picked}
    return picked + [e for e in relevant if e.get("entity_id") not in ids]
