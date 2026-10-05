"""Read, add, change and remove calendar events through the calendar entity.

Home Assistant's ``calendar.get_events`` service leaves out each event's
``uid``, and changing or removing an event has no service at all — only the
``calendar/event/*`` websocket commands, which take that uid. So this goes to
the calendar entity the way those commands do: found in the calendar
component, its supported features checked, and the event validated with the
websocket API's own schema before the entity sees it.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any, Final

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.components.calendar import CalendarEntity
    from homeassistant.core import HomeAssistant

_DEFAULT_DAYS: Final = 7
_MAX_DAYS: Final = 366
_MAX_EVENTS: Final = 200
# Home Assistant compares the range verbatim, and this is the only one it knows.
_RECURRENCE_RANGES: Final = frozenset({"THISANDFUTURE"})
# Calendar text is often written by someone else (a shared or subscribed
# calendar), so it is bounded and stripped of markup before a model reads it.
_TEXT_LIMITS: Final = {"summary": 200, "description": 1000, "location": 200}


def _calendar(hass: HomeAssistant, entity_id: str) -> CalendarEntity | str:
    """The calendar entity, or why there is none."""
    entity_id = str(entity_id or "").strip().lower()
    shown = sanitize_untrusted_text(entity_id, 80)
    if not entity_id.startswith("calendar."):
        return f"'{shown}' is not a calendar. Pass a calendar.* entity_id."
    if "calendar" not in hass.config.components:
        return "The calendar integration is not loaded, so there is no calendar to use."
    from homeassistant.components.calendar.const import DATA_COMPONENT  # noqa: PLC0415

    component = hass.data.get(DATA_COMPONENT)
    entity = component.get_entity(entity_id) if component is not None else None
    if entity is None:
        return f"No calendar {shown}. Call search_entities for its entity_id."
    return entity


def _supports(entity: CalendarEntity, feature_name: str) -> bool:
    from homeassistant.components.calendar.const import CalendarEntityFeature  # noqa: PLC0415

    feature = CalendarEntityFeature[feature_name]
    return bool(entity.supported_features and entity.supported_features & feature)


def _when(value: Any) -> datetime.datetime | None | str:
    """An ISO date or datetime as an aware local datetime; None when absent."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    text = str(value).strip()
    parsed = dt_util.parse_datetime(text)
    if parsed is None:
        day = dt_util.parse_date(text)
        if day is None:
            return f"'{sanitize_untrusted_text(text, 40)}' is not an ISO date or datetime."
        parsed = datetime.datetime.combine(day, datetime.time.min)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_local(parsed)


def _event_row(event: Any) -> dict[str, Any]:
    row = event.as_dict()
    for key, limit in _TEXT_LIMITS.items():
        if row.get(key):
            row[key] = sanitize_untrusted_text(str(row[key]), limit)
    return {key: value for key, value in row.items() if value not in (None, "")}


async def async_list_events(
    hass: HomeAssistant, entity_id: str, start: Any = None, end: Any = None
) -> dict[str, Any]:
    """Events overlapping [start, end), with the uid each change needs."""
    entity = _calendar(hass, entity_id)
    if isinstance(entity, str):
        return {"error": entity}
    since = _when(start)
    if isinstance(since, str):
        return {"error": since}
    until = _when(end)
    if isinstance(until, str):
        return {"error": until}
    since = since or dt_util.now()
    until = until or since + datetime.timedelta(days=_DEFAULT_DAYS)
    if until <= since:
        return {"error": "end must be after start."}
    if until - since > datetime.timedelta(days=_MAX_DAYS):
        return {"error": f"Ask for at most {_MAX_DAYS} days at a time."}

    try:
        events = await entity.async_get_events(hass, since, until)
    except HomeAssistantError as exc:
        return {
            "error": f"The calendar could not be read: {sanitize_untrusted_text(str(exc), 200)}"
        }
    rows = [_event_row(event) for event in events[:_MAX_EVENTS]]
    return {
        "entity_id": entity.entity_id,
        "start": since.isoformat(),
        "end": until.isoformat(),
        "events": rows,
        **({"truncated": True} if len(events) > _MAX_EVENTS else {}),
        "can_add": _supports(entity, "CREATE_EVENT"),
        "can_change": _supports(entity, "UPDATE_EVENT"),
        "can_remove": _supports(entity, "DELETE_EVENT"),
    }


def _recurrence(
    uid: str | None, recurrence_id: Any, recurrence_range: Any
) -> tuple[str | None, str | None] | str:
    """The occurrence selector, or why it cannot be used."""
    rid = str(recurrence_id).strip() if recurrence_id not in (None, "") else None
    rng = str(recurrence_range).strip() if recurrence_range not in (None, "") else None
    if (rid or rng) and not uid:
        return "recurrence_id and recurrence_range pick an occurrence of an existing event; pass its uid."
    if rng is not None and rng not in _RECURRENCE_RANGES:
        return "recurrence_range must be 'THISANDFUTURE', or left out for one occurrence."
    if rng is not None and rid is None:
        return "recurrence_range needs the recurrence_id it starts from."
    return rid, rng


async def async_set_event(
    hass: HomeAssistant,
    entity_id: str,
    fields: dict[str, Any],
    *,
    uid: str | None = None,
    recurrence_id: Any = None,
    recurrence_range: Any = None,
) -> dict[str, Any]:
    """Add an event, or replace an existing one (by uid) with *fields*."""
    from homeassistant.components.calendar import WEBSOCKET_EVENT_SCHEMA  # noqa: PLC0415

    entity = _calendar(hass, entity_id)
    if isinstance(entity, str):
        return {"error": entity}
    uid = str(uid).strip() if uid not in (None, "") else None
    occurrence = _recurrence(uid, recurrence_id, recurrence_range)
    if isinstance(occurrence, str):
        return {"error": occurrence}
    feature = "UPDATE_EVENT" if uid else "CREATE_EVENT"
    if not _supports(entity, feature):
        verb = "changing" if uid else "adding"
        return {"error": f"This calendar does not allow {verb} events from Home Assistant."}

    candidate = {
        target: fields[source]
        for source, target in (
            ("start", "dtstart"),
            ("end", "dtend"),
            ("summary", "summary"),
            ("description", "description"),
            ("location", "location"),
            ("rrule", "rrule"),
        )
        if fields.get(source) not in (None, "")
    }
    try:
        event = WEBSOCKET_EVENT_SCHEMA(candidate)
    except vol.Invalid as exc:
        return {
            "error": (
                f"Home Assistant would refuse that event: {sanitize_untrusted_text(str(exc), 200)}"
            )
        }
    try:
        if uid:
            await entity.async_update_event(
                uid, event, recurrence_id=occurrence[0], recurrence_range=occurrence[1]
            )
        else:
            await entity.async_create_event(**event)
    except (HomeAssistantError, ValueError) as exc:
        return {"error": f"The calendar refused it: {sanitize_untrusted_text(str(exc), 200)}"}
    return {
        "status": "changed" if uid else "added",
        "entity_id": entity.entity_id,
        "summary": sanitize_untrusted_text(str(event["summary"]), 200),
        **({"uid": uid} if uid else {}),
    }


async def async_delete_event(
    hass: HomeAssistant,
    entity_id: str,
    uid: str,
    *,
    recurrence_id: Any = None,
    recurrence_range: Any = None,
) -> dict[str, Any]:
    """Remove an event — one occurrence, it and the rest, or the whole series."""
    entity = _calendar(hass, entity_id)
    if isinstance(entity, str):
        return {"error": entity}
    uid = str(uid or "").strip()
    if not uid:
        return {"error": "Pass the event's uid, from list_calendar_events."}
    occurrence = _recurrence(uid, recurrence_id, recurrence_range)
    if isinstance(occurrence, str):
        return {"error": occurrence}
    if not _supports(entity, "DELETE_EVENT"):
        return {"error": "This calendar does not allow removing events from Home Assistant."}
    try:
        await entity.async_delete_event(
            uid, recurrence_id=occurrence[0], recurrence_range=occurrence[1]
        )
    except (HomeAssistantError, ValueError) as exc:
        return {"error": f"The calendar refused it: {sanitize_untrusted_text(str(exc), 200)}"}
    return {"status": "removed", "entity_id": entity.entity_id, "uid": uid}
