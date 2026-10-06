"""Read the recorder: state changes over a time range, or long-term statistics.

Two sources, one shape per entity:

* **history** — every state change between ``start`` and ``end``, consecutive
  repeats folded. A busy sensor can change thousands of times a day, so a call
  returns at most ``limit`` changes, the newest; ``offset`` steps back past
  them and ``older`` says how many are left.
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
from typing import TYPE_CHECKING, Any

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


def _window(arguments: dict[str, Any], max_days: int) -> tuple[datetime, datetime]:
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
        raise _BadRequest(
            f"The range is over {max_days} days; narrow start/end or ask for fewer days."
        )
    return start, end


def _entity_ids(arguments: dict[str, Any]) -> list[str]:
    raw = arguments.get("entity_ids")
    ids = [str(e).strip() for e in raw] if isinstance(raw, list) else []
    if single := str(arguments.get("entity_id") or "").strip():
        ids.insert(0, single)
    ids = list(dict.fromkeys(i for i in ids if i))
    if not ids:
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


async def _history(
    hass: HomeAssistant,
    ids: list[str],
    start: datetime,
    end: datetime,
    offset: int,
    limit: int,
) -> list[dict[str, Any]]:
    from homeassistant.components.recorder import get_instance  # noqa: PLC0415
    from homeassistant.components.recorder.history import (  # noqa: PLC0415
        get_significant_states,
    )

    found = await get_instance(hass).async_add_executor_job(
        get_significant_states, hass, start, end, ids
    )
    results: list[dict[str, Any]] = []
    for entity_id in ids:
        rows = found.get(entity_id) or []
        secret = _is_secret(hass, entity_id, rows)
        changes: list[dict[str, Any]] = []
        prev: str | None = None
        for row in rows:
            raw = row.get("state") if isinstance(row, dict) else row.state
            when = row.get("last_changed") if isinstance(row, dict) else row.last_changed
            value = _REDACTED if secret else _state_value(raw)
            if value == prev:
                continue
            prev = value
            changes.append({"state": value, "at": _iso(_as_datetime(when))})
        page, older = _page(changes, offset, limit)
        entry: dict[str, Any] = {"entity_id": entity_id, "changes": page, "count": len(page)}
        if older:
            entry["older"] = older
            entry["next_offset"] = offset + len(page)
        if not changes:
            entry["note"] = _empty_note(hass, entity_id)
        results.append(entry)
    return results


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
    if source not in ("history", "statistics"):
        return {"error": "source must be 'history' or 'statistics'"}
    if "recorder" not in hass.config.components:
        return {"error": "The recorder is not running, so there is no history."}
    try:
        ids = _entity_ids(arguments)
        offset = _int(arguments, "offset", 0, 0, 1_000_000)
        limit = _int(arguments, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
        if source == "history":
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
    if len(entries) == 1:
        result.update(entries[0])
    else:
        result["entities"] = entries
    return result
