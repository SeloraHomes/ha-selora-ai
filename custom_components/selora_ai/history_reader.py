"""Read the recorder: state changes over a time range, or long-term statistics.

Two sources, one shape per entity (a third, ``logbook``, is one merged
timeline and lives in ``logbook_reader``):

* **history** — every state change between ``start`` and ``end``, consecutive
  repeats folded. A busy sensor can change thousands of times a day, so a call
  returns at most ``limit`` changes, the newest; ``offset`` steps back past
  them and ``older`` says how many are left. At most ``_ROW_CAP`` rows are read
  per entity, off the event loop — a range holding more is narrowed to its
  newest part, and the answer says so.
* **statistics** — what the Energy and History dashboards plot: hourly (or
  daily, monthly…) mean/min/max or change, kept long after the recorder purges
  the states. Only entities with a ``state_class`` have them, and an entity
  without them is told so rather than handed an empty list.

An entity that no longer exists still has its history, so it is not refused
for having no current state; an id the recorder has never seen is.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.core import valid_entity_id
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from sqlalchemy.exc import SQLAlchemyError

from .helpers import format_entity_state, sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

MAX_ENTITIES = 10
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
HISTORY_MAX_DAYS = 31
STATISTICS_MAX_DAYS = 400
DEFAULT_HOURS = 6.0

PERIODS: dict[str, timedelta] = {
    "5minute": timedelta(minutes=5),
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(weeks=1),
    "month": timedelta(days=30),
}
STATISTIC_TYPES = ("mean", "min", "max", "change", "state", "sum")
# What the recorder computes per bucket, before the limit picks the newest:
# past this, the query is the cost, not the answer.
_MAX_BUCKETS = 2000

# HA's own signal that a value is a secret (`input_text`/`text` in password
# mode); its history is the secret's past values.
_REDACTED = "***"
# Rows read per entity per call, and how many times a range that holds more is
# halved toward its newest end before giving up on fitting it.
_ROW_CAP: Final = 5000
_MAX_NARROWING: Final = 16
# Chunks of _ROW_CAP walked forward from the narrowed start: bounds the queries
# a pathological entity (thousands of changes a second) can cost.
_MAX_CHUNKS: Final = 40
_TEXT_DOMAINS = frozenset({"input_text", "text"})


class _BadRequest(ValueError):
    """An argument the caller can fix; its message is the answer."""


def _parse_time(value: Any, name: str) -> datetime | None:
    """An ISO date or datetime; a naive one is in the home's time zone."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    parsed = dt_util.parse_datetime(text)
    if parsed is None:
        day = dt_util.parse_date(text)
        if day is None:
            raise _BadRequest(f"{name} is not an ISO date or datetime: {text[:40]!r}")
        parsed = datetime(day.year, day.month, day.day)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_utc(parsed)


def _window(
    arguments: dict[str, Any], max_days: int, hint: str = "ask for fewer days"
) -> tuple[datetime, datetime]:
    now = dt_util.utcnow()
    start = _parse_time(arguments.get("start"), "start")
    end = _parse_time(arguments.get("end"), "end") or now
    if start is None:
        try:
            hours = float(arguments.get("hours", DEFAULT_HOURS))
        except (TypeError, ValueError):
            hours = DEFAULT_HOURS
        hours = max(0.25, min(hours, max_days * 24.0))
        start = end - timedelta(hours=hours)
    end = min(end, now)
    if start >= end:
        raise _BadRequest("start must be before end (and in the past)")
    if end - start > timedelta(days=max_days):
        raise _BadRequest(f"The range is over {max_days} days; narrow start/end or {hint}.")
    return start, end


def _entity_ids(arguments: dict[str, Any], *, required: bool = True) -> list[str]:
    raw = arguments.get("entity_ids")
    ids = [str(e).strip() for e in raw] if isinstance(raw, list) else []
    if single := str(arguments.get("entity_id") or "").strip():
        ids.insert(0, single)
    ids = list(dict.fromkeys(i for i in ids if i))
    if not ids and required:
        raise _BadRequest("entity_id (or entity_ids) is required, e.g. 'light.kitchen'")
    if len(ids) > MAX_ENTITIES:
        raise _BadRequest(f"At most {MAX_ENTITIES} entities per call; split the request.")
    for entity_id in ids:
        if not valid_entity_id(entity_id):
            raise _BadRequest(f"'{sanitize_untrusted_text(entity_id, 80)}' is not an entity_id")
    return ids


def _int(arguments: dict[str, Any], name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(arguments.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(value, high))


def _page(rows: list[Any], offset: int, limit: int) -> tuple[list[Any], int]:
    """The newest ``limit`` rows after skipping ``offset``, oldest first, and
    how many older ones remain."""
    stop = max(len(rows) - offset, 0)
    begin = max(stop - limit, 0)
    return rows[begin:stop], begin


def _iso(when: datetime | float | None) -> str | None:
    if when is None:
        return None
    if isinstance(when, int | float):
        when = datetime.fromtimestamp(when, tz=UTC)
    return dt_util.as_local(when).isoformat()


def _is_secret(hass: HomeAssistant, entity_id: str, rows: list[Any]) -> bool:
    """Password mode now, or in any recorded row: an entity since removed or
    switched out of password mode still recorded its secret while it was one.
    A text entity with neither to go by is treated as one."""

    def _password(attributes: Any) -> bool:
        return str((attributes or {}).get("mode", "")).lower() == "password"

    state = hass.states.get(entity_id)
    if state is not None and _password(state.attributes):
        return True
    if any(_password(getattr(row, "attributes", None)) for row in rows):
        return True
    return (
        state is None
        and entity_id.split(".", 1)[0] in _TEXT_DOMAINS
        and not any(hasattr(row, "attributes") for row in rows)
    )


def _known(hass: HomeAssistant, entity_id: str) -> bool:
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    return (
        hass.states.get(entity_id) is not None
        or er.async_get(hass).async_get(entity_id) is not None
    )


def _empty_note(hass: HomeAssistant, entity_id: str) -> str:
    if not _known(hass, entity_id):
        return (
            "No entity by this id and nothing recorded for it; check the id with search_entities."
        )
    return (
        "Nothing recorded in this range. The entity may be excluded from the "
        "recorder, or the range may be older than its purge_keep_days."
    )


# ── history ─────────────────────────────────────────────────────────────────


def _fetch_changes(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> tuple[list[Any], datetime | None]:
    """An entity's newest state changes in [start, end], at most ``_ROW_CAP`` —
    run in the recorder's executor, never holding more than that many rows.

    The recorder's ``limit`` keeps the OLDEST rows, so a range holding more
    than the cap is first narrowed — its start halved toward the end while the
    newer half still has changes — then walked forward in chunks of the cap,
    each starting after the last row read, keeping the newest. The second value
    says where the range had to start, when it was narrowed. Attributes are
    read only for a text entity, whose password mode decides what is shown.
    """
    from homeassistant.components.recorder.history import (  # noqa: PLC0415
        state_changes_during_period,
    )

    keep_attributes = entity_id.split(".", 1)[0] in _TEXT_DOMAINS

    def _rows(since: datetime, opening_state: bool) -> list[Any]:
        return state_changes_during_period(
            hass,
            since,
            end,
            entity_id,
            no_attributes=not keep_attributes,
            limit=_ROW_CAP + 1,
            include_start_time_state=opening_state,
        ).get(entity_id.lower(), [])

    rows = _rows(start, True)
    if len(rows) <= _ROW_CAP:
        return rows, None
    window_start = start
    for _ in range(_MAX_NARROWING):
        middle = window_start + (end - window_start) / 2
        # Only while the newer half alone still overflows: once it fits, the
        # newest changes reach back into the older half, and the walk below
        # collects them — narrowing further would throw recent changes away.
        if len(_rows(middle, False)) <= _ROW_CAP:
            break
        window_start = middle
    newest: list[Any] = []
    since = window_start
    for _ in range(_MAX_CHUNKS):
        chunk = _rows(since, False)
        for row in chunk:
            # The recorder compares float timestamps, so a chunk can begin with
            # the row the previous one ended on: kept twice, it would read as a
            # repeat and be folded away with a real change.
            if newest and row.last_updated <= newest[-1].last_updated:
                continue
            newest.append(row)
        newest = newest[-_ROW_CAP:]
        if len(chunk) <= _ROW_CAP:
            break
        since = chunk[-1].last_updated
    return newest, window_start


async def _history(
    hass: HomeAssistant,
    ids: list[str],
    start: datetime,
    end: datetime,
    offset: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Each entity's newest changes, paged — fetched and folded off the event
    loop, at most ``_ROW_CAP`` rows per entity whatever the range holds."""
    from homeassistant.components.recorder import get_instance  # noqa: PLC0415

    secret_now = {
        entity_id: str(
            (state.attributes.get("mode") if (state := hass.states.get(entity_id)) else "") or ""
        ).lower()
        == "password"
        for entity_id in ids
    }
    present = {entity_id: hass.states.get(entity_id) is not None for entity_id in ids}

    def _job() -> list[dict[str, Any]]:
        results = []
        for entity_id in ids:
            rows, narrowed = _fetch_changes(hass, entity_id, start, end)
            secret = secret_now[entity_id] or _rows_secret(entity_id, rows, present[entity_id])
            changes: list[dict[str, Any]] = []
            prev: str | None = None
            for row in rows:
                value = _REDACTED if secret else _state_value(row.state)
                if value == prev:
                    continue
                prev = value
                changes.append({"state": value, "at": _iso(_as_datetime(row.last_changed))})
            page, older = _page(changes, offset, limit)
            entry: dict[str, Any] = {"entity_id": entity_id, "changes": page, "count": len(page)}
            if older:
                entry["older"] = older
                entry["next_offset"] = offset + len(page)
            if narrowed is not None:
                entry["range_start"] = _iso(narrowed)
                entry["range_note"] = (
                    f"This entity changed more than {_ROW_CAP} times in the range asked "
                    "for, so only the newest part is read, from range_start. For earlier "
                    "changes ask again with an earlier end, or use source='statistics'."
                )
            results.append(entry)
        return results

    entries = await get_instance(hass).async_add_executor_job(_job)
    for entry in entries:
        if not entry["changes"] and "range_start" not in entry:
            entry["note"] = _empty_note(hass, entry["entity_id"])
    return entries


def _rows_secret(entity_id: str, rows: list[Any], present: bool) -> bool:
    """Password mode recorded in any row — an entity since removed or switched
    out of password mode still recorded its secret while it was one. A text
    entity with no current state and no attributes to go by counts as one."""
    if any(
        str((getattr(row, "attributes", None) or {}).get("mode", "")).lower() == "password"
        for row in rows
    ):
        return True
    return (
        not present
        and entity_id.split(".", 1)[0] in _TEXT_DOMAINS
        and not any(getattr(row, "attributes", None) for row in rows)
    )


def _state_value(raw: Any) -> str:
    text = str(raw if raw is not None else "")
    formatted = format_entity_state(text)
    return sanitize_untrusted_text(formatted, 64) if formatted == text.strip() else formatted


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return dt_util.parse_datetime(value)
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC)
    return None


# ── statistics ──────────────────────────────────────────────────────────────


def _types(arguments: dict[str, Any]) -> set[str] | None:
    raw = arguments.get("statistic_types")
    if raw in (None, "", []):
        return None
    names = {str(t).strip() for t in (raw if isinstance(raw, list) else [raw])}
    unknown = names - set(STATISTIC_TYPES)
    if unknown:
        raise _BadRequest(
            f"Unknown statistic_types {sorted(unknown)}; use {', '.join(STATISTIC_TYPES)}."
        )
    return names


def _default_types(meta: dict[str, Any]) -> set[str]:
    """What the dashboards show for this kind of statistic: a meter's
    consumption per bucket, a measurement's mean and range."""
    if meta.get("has_sum"):
        return {"change"}
    return {"mean", "min", "max"}


async def _statistics(
    hass: HomeAssistant,
    ids: list[str],
    start: datetime,
    end: datetime,
    period: str,
    types: set[str] | None,
    offset: int,
    limit: int,
) -> list[dict[str, Any]]:
    from homeassistant.components.recorder import get_instance  # noqa: PLC0415
    from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
        get_metadata,
        statistics_during_period,
    )

    instance = get_instance(hass)
    metadata = await instance.async_add_executor_job(
        lambda: get_metadata(hass, statistic_ids=set(ids))
    )
    have = {i for i in ids if i in metadata}
    asked: set[str] = set(types or ())
    if types is None:
        for statistic_id in have:
            asked |= _default_types(dict(metadata[statistic_id][1]))
    rows_by_id = (
        await instance.async_add_executor_job(
            statistics_during_period, hass, start, end, have, period, None, asked
        )
        if have
        else {}
    )

    results: list[dict[str, Any]] = []
    for entity_id in ids:
        if entity_id not in have:
            results.append(
                {
                    "entity_id": entity_id,
                    "rows": [],
                    "count": 0,
                    "note": (
                        "No long-term statistics: only entities with a state_class "
                        "(most numeric sensors) have them. Use source='history'."
                        if _known(hass, entity_id)
                        else _empty_note(hass, entity_id)
                    ),
                }
            )
            continue
        meta = dict(metadata[entity_id][1])
        wanted = types or _default_types(meta)
        secret = _is_secret(hass, entity_id, [])
        rows = []
        for row in rows_by_id.get(entity_id) or []:
            out: dict[str, Any] = {"start": _iso(row.get("start"))}
            for name in STATISTIC_TYPES:
                if name in wanted and row.get(name) is not None:
                    out[name] = _REDACTED if secret else round(float(row[name]), 3)
            rows.append(out)
        page, older = _page(rows, offset, limit)
        entry: dict[str, Any] = {"entity_id": entity_id, "rows": page, "count": len(page)}
        state = hass.states.get(entity_id)
        unit = (state.attributes.get("unit_of_measurement") if state else None) or meta.get(
            "unit_of_measurement"
        )
        if unit:
            entry["unit"] = sanitize_untrusted_text(unit, 16)
        if older:
            entry["older"] = older
            entry["next_offset"] = offset + len(page)
        if not rows:
            entry["note"] = "No statistics in this range for this period."
        results.append(entry)
    return results


# ── entry point ─────────────────────────────────────────────────────────────


async def async_read_history(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Answer a history or statistics read; one entity reads flat, several as a list."""
    source = str(arguments.get("source") or "history").strip().lower()
    if source not in ("history", "statistics", "logbook"):
        return {"error": "source must be 'history', 'statistics' or 'logbook'"}
    if "recorder" not in hass.config.components:
        return {"error": "The recorder is not running, so there is no history."}
    if source == "logbook" and "logbook" not in hass.config.components:
        return {"error": "The logbook (Activity) integration is not loaded."}
    entries: list[dict[str, Any]] = []
    logbook: dict[str, Any] = {}
    try:
        ids = _entity_ids(arguments, required=source != "logbook")
        offset = _int(arguments, "offset", 0, 0, 1_000_000)
        limit = _int(arguments, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
        if source == "logbook":
            from .logbook_reader import (  # noqa: PLC0415
                LOGBOOK_HOME_MAX_DAYS,
                LOGBOOK_MAX_DAYS,
                async_read_logbook,
            )

            start, end = (
                _window(arguments, LOGBOOK_MAX_DAYS)
                if ids
                else _window(arguments, LOGBOOK_HOME_MAX_DAYS, "name the entities to read")
            )
            if offset:
                raise _BadRequest(
                    "The logbook pages by time, not offset: ask again with the "
                    "start and end in next_page from the previous answer."
                )
            logbook = await async_read_logbook(hass, ids, start, end, limit)
        elif source == "history":
            start, end = _window(arguments, HISTORY_MAX_DAYS)
            entries = await _history(hass, ids, start, end, offset, limit)
        else:
            period = str(arguments.get("period") or "hour").strip().lower()
            if period not in PERIODS:
                raise _BadRequest(f"period must be one of {', '.join(PERIODS)}")
            start, end = _window(arguments, STATISTICS_MAX_DAYS)
            if (end - start) / PERIODS[period] > _MAX_BUCKETS:
                raise _BadRequest(
                    f"That range holds over {_MAX_BUCKETS} {period} buckets; "
                    "use a coarser period (day, week, month) or a shorter range."
                )
            entries = await _statistics(
                hass, ids, start, end, period, _types(arguments), offset, limit
            )
    except _BadRequest as exc:
        return {"error": str(exc)}
    except (HomeAssistantError, SQLAlchemyError) as exc:
        _LOGGER.warning("History read failed: %s", exc)
        return {"error": f"The recorder could not answer: {sanitize_untrusted_text(exc, 200)}"}

    result: dict[str, Any] = {
        "source": source,
        "start": _iso(start),
        "end": _iso(end),
        "hours": round((end - start).total_seconds() / 3600, 2),
    }
    if source == "statistics":
        result["period"] = period
    if source == "logbook":
        result.update(logbook)
        if next_end := result.pop("next_end", None):
            # The start too: re-derived from `hours`, it would drift with each
            # page instead of holding the range the caller asked for.
            result["next_page"] = {"start": result["start"], "end": next_end}
    elif len(entries) == 1:
        result.update(entries[0])
    else:
        result["entities"] = entries
    return result
