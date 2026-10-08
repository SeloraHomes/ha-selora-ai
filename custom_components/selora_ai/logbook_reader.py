"""Read the logbook: what happened, in order, and what caused each change.

History says a light turned on at 03:12; the logbook says an automation, a
person or another entity turned it on. It is read through core's own
``EventProcessor``, so what counts as an entry (continuous sensors skipped,
integrations' described events, ``logbook.log`` messages) and how a change is
attributed to its cause are Home Assistant's, not re-derived here.

Unlike history, the answer is ONE timeline merged across the entities asked
for — the order between them is the point ("the door opened, then the hall
light came on"). With no entity, it is the whole home's timeline, over a
shorter range since nothing narrows the query.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import partial
import logging
import math
from typing import TYPE_CHECKING, Any, Final

from .helpers import sanitize_untrusted_text
from .history_reader import _iso

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

LOGBOOK_MAX_DAYS: Final = 31
# Nothing narrows a whole-home query, and core's processor returns every row
# of the range at once.
LOGBOOK_HOME_MAX_DAYS: Final = 2

# The walk back from the newest end starts this wide and doubles, up to the
# cap: grown over a quiet stretch, an unbounded window swallows the burst
# before it whole — a week of a busy motion sensor in one read for a page of 200.
_FIRST_WINDOW: Final = timedelta(minutes=5)
_MAX_WINDOW: Final = timedelta(hours=6)
_EPSILON: Final = timedelta(microseconds=1)

# A text entity's states may be a password's past values, and a logbook row
# carries no attributes to tell; history reads the recorded mode and redacts
# only what was secret.
_TEXT_DOMAINS: Final = frozenset({"input_text", "text"})
_REDACTED: Final = "***"

# Core's context fields, renamed for the answer's ``caused_by``.
_CAUSE_FIELDS: Final = {
    "context_entity_id": "entity_id",
    "context_entity_id_name": "entity_name",
    "context_state": "state",
    "context_event_type": "event",
    "context_domain": "domain",
    "context_service": "service",
    "context_name": "name",
    "context_message": "message",
    "context_source": "trigger",
}
_ENTRY_TEXT: Final = {"name": 80, "message": 200, "source": 200}


def _text(value: Any, limit: int) -> str | None:
    if value in (None, ""):
        return None
    return sanitize_untrusted_text(str(value), limit)


def _secret(entity_id: Any) -> bool:
    return isinstance(entity_id, str) and entity_id.split(".", 1)[0] in _TEXT_DOMAINS


def _people(hass: HomeAssistant) -> dict[str, str]:
    """User id → the person it belongs to: what the logbook card shows, and
    readable by anyone who can read states (a user's own name is admin-only)."""
    people: dict[str, str] = {}
    for state in hass.states.async_all("person"):
        if user_id := state.attributes.get("user_id"):
            name = state.attributes.get("friendly_name") or state.object_id
            people[str(user_id)] = sanitize_untrusted_text(str(name), 60)
    return people


def _entry(raw: dict[str, Any], people: dict[str, str]) -> dict[str, Any]:
    entity_id = raw.get("entity_id")
    out: dict[str, Any] = {"at": _iso(raw.get("when"))}
    if entity_id:
        out["entity_id"] = str(entity_id)
    for key, limit in _ENTRY_TEXT.items():
        if (value := _text(raw.get(key), limit)) is not None:
            out[key] = value
    if raw.get("state") is not None:
        out["state"] = _REDACTED if _secret(entity_id) else _text(raw["state"], 64)
    elif not entity_id and raw.get("domain"):
        out["domain"] = _text(raw["domain"], 40)

    cause: dict[str, Any] = {}
    for field, name in _CAUSE_FIELDS.items():
        if (value := _text(raw.get(field), _ENTRY_TEXT.get(name, 80))) is not None:
            cause[name] = value
    if "state" in cause and _secret(raw.get("context_entity_id")):
        cause["state"] = _REDACTED
    if user_id := raw.get("context_user_id"):
        cause["user"] = people.get(str(user_id), "a user with no person")
    if cause:
        out["caused_by"] = cause
    return out


def _walk_back(
    processor: Any, start: datetime, end: datetime, limit: int, *, reread: Any = None
) -> tuple[list[dict[str, Any]], bool]:
    """The newest ``limit`` rows in (start, end), and whether older ones may
    remain — read in windows walking back from ``end``, never the whole range.

    Core's processor returns every row of the range it is given, oldest first,
    so a month of a busy entity read in one call is the cost, however small
    the page. The first window is short and doubles while the page is not
    full, so a busy home is read minutes at a time and a quiet one in a few
    calls.

    Core attaches a cause only if it has already seen it in the same read, and
    windows read newest first see an effect before its cause. An entity read
    is unaffected — core fetches its causes by context id — but a whole-home
    read then reads the range the walk covered once more with ``reread``, a
    FRESH processor, in one chronological pass, so every cause inside it is
    attached — core keeps the first row it meets for each context, so the walk's
    own processor would still hold the effect where the cause belongs. At most twice
    the rows; a cause from before the covered range is lost as it is at the
    start of any range Home Assistant's own logbook shows.
    """
    collected: list[dict[str, Any]] = []
    window = _FIRST_WINDOW
    upper = end
    first = True
    while upper > start and len(collected) < limit:
        lower = max(start, upper - window)
        # Core's bounds are strict on both ends: a row exactly on a boundary
        # is read by the older window, which reaches just past it. The first
        # window does not, so a ``next_end`` does not return its own row.
        top = upper if first else upper + _EPSILON
        rows = processor.get_events(lower, top)
        collected = rows + collected
        upper, first = lower, False
        window = min(window * 2, _MAX_WINDOW)
    if reread is not None and collected:
        collected = reread.get_events(upper, end)
    cut = max(len(collected) - limit, 0)
    # Never part-way through a timestamp: ``next_end`` is a strict bound, so a
    # tied row left for the next page would never be read.
    while 0 < cut < len(collected) and collected[cut - 1]["when"] == collected[cut]["when"]:
        cut -= 1
    return collected[cut:], upper > start or cut > 0


def _next_end(when: float) -> str | None:
    """The page's oldest time, rounded DOWN to the microsecond an ISO time
    holds: rounded up, the strict bound would read that row a second time."""
    floored = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=math.floor(when * 1e6))
    return _iso(floored)


async def async_read_logbook(
    hass: HomeAssistant,
    ids: list[str],
    start: datetime,
    end: datetime,
    limit: int,
) -> dict[str, Any]:
    """The newest ``limit`` logbook entries in [start, end], oldest first."""
    from homeassistant.components.logbook.helpers import (  # noqa: PLC0415
        async_determine_event_types,
        async_filter_entities,
    )
    from homeassistant.components.logbook.processor import EventProcessor  # noqa: PLC0415
    from homeassistant.components.recorder import get_instance  # noqa: PLC0415

    result: dict[str, Any] = {}
    asked = ids or None
    if ids:
        kept = async_filter_entities(hass, ids)
        if skipped := [i for i in ids if i not in kept]:
            result["skipped"] = skipped
            result["skipped_note"] = (
                "The logbook leaves out sensors that change continuously (those with "
                "a unit) and counters; read them with source='history'."
            )
        if not kept:
            result.update(entries=[], count=0)
            return result
        asked = kept

    def _processor() -> Any:
        return EventProcessor(
            hass,
            async_determine_event_types(hass, asked, None),
            asked,
            None,
            None,
            timestamp=True,
            include_entity_name=True,
        )

    processor = _processor()
    reread = None if ids else _processor()
    rows, more = await get_instance(hass).async_add_executor_job(
        partial(_walk_back, processor, start, end, limit, reread=reread)
    )
    people = _people(hass)
    entries = [_entry(row, people) for row in rows]
    result.update(entries=entries, count=len(entries))
    if more and rows:
        result["more"] = True
        result["next_end"] = _next_end(rows[0]["when"])
    if not entries:
        result["note"] = (
            "Nothing in the logbook for this range. An entity excluded from the "
            "recorder or logbook has no entries, and the range may be older than "
            "purge_keep_days."
        )
    return result
