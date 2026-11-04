"""Home suggestions drawn from how the home is used.

``home_suggestions`` reads how the home is organised; this reads what its
people do, from the pattern store's state history. Two kinds of row:

* **automation_idea** — a pending suggestion of the pattern engine
  (``suggestion_generator``) confident enough to show: something the user does
  at the same time, or right after something else, every day.
* **create_scene** — three or more devices the user keeps setting to the same
  states together (within ``_BURST`` of each other) on several days: what a
  scene would do in one tap.

Nothing is applied from here: like every home suggestion, the panel turns a
row into a chat request, where the usual tools and cards do the work.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timedelta
from itertools import combinations
import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .automation_utils import _collect_referenced_entity_ids
from .entity_capabilities import scene_supports
from .entity_filter import resolve_ignored_entity_ids
from .pattern_store import get_pattern_store

if TYPE_CHECKING:
    from .types import HomeSuggestion, HomeSuggestionEntity, StateChange, SuggestionDict

_LOGGER = logging.getLogger(__name__)

PATTERN_PREFIX: Final = "pattern:"
_SCENE_PREFIX: Final = "scene:"

# Below this the pattern engine is guessing: those stay in the store, for
# the MCP tools only.
_MIN_CONFIDENCE: Final = 0.8
_MAX_AUTOMATION_IDEAS: Final = 6

# Changes this close together are one action of the user's: a walk round the
# living room switching three lamps on and the TV with them.
_BURST: Final = timedelta(minutes=2)
_MIN_SCENE_SIZE: Final = 3
# Days, not times: switching the same lamps on three times in one evening is
# one habit seen once.
_MIN_SCENE_DAYS: Final = 3
_SCENE_LOOKBACK: Final = timedelta(days=14)
_MAX_SCENE_IDEAS: Final = 2
# Bounds the event-loop work: candidates are pairwise intersections of bursts.
_MAX_BURSTS: Final = 200

# States a scene can be asked to restore; the rest are on the way to one.
_TRANSIENT_STATES: Final = frozenset(
    {"unavailable", "unknown", "", "opening", "closing", "buffering"}
)
_OFF_STATES: Final = frozenset({"off", "closed", "idle", "standby"})

_Setting = tuple[str, str]  # (entity_id, state)


def _names(hass: HomeAssistant, entity_ids: list[str]) -> list[HomeSuggestionEntity]:
    out: list[HomeSuggestionEntity] = []
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        name = state.attributes.get("friendly_name") if state is not None else None
        out.append({"entity_id": entity_id, "name": str(name or entity_id)})
    return out


def _covered(entity_ids: set[str], groups: list[frozenset[str]]) -> bool:
    """True when one existing automation or scene already references them all."""
    return any(entity_ids <= group for group in groups)


def _automation_groups(automations: list[dict[str, Any]]) -> list[frozenset[str]]:
    return [frozenset(_collect_referenced_entity_ids(a)) for a in automations]


def _scene_groups(hass: HomeAssistant) -> list[frozenset[str]]:
    return [
        frozenset(st.attributes.get("entity_id") or []) for st in hass.states.async_all("scene")
    ]


def _automation_aliases(hass: HomeAssistant) -> set[str]:
    return {
        str(st.attributes.get("friendly_name") or "").casefold()
        for st in hass.states.async_all("automation")
    }


def automation_idea_rows(
    hass: HomeAssistant,
    pending: list[SuggestionDict],
    automations: list[dict[str, Any]],
) -> list[HomeSuggestion]:
    """The pattern engine's confident pending suggestions, strongest first.

    One the user already made (in chat, from this very row) drops out: its
    alias exists, or one automation already references all its entities.
    """
    from . import _suggestion_ignore_filter  # noqa: PLC0415

    is_ignored = _suggestion_ignore_filter(hass)
    aliases = _automation_aliases(hass)
    groups = _automation_groups(automations)
    seen: set[str] = set()
    rows: list[HomeSuggestion] = []
    for s in sorted(pending, key=lambda s: s.get("confidence", 0.0), reverse=True):
        if s.get("confidence", 0.0) < _MIN_CONFIDENCE:
            continue
        auto = s.get("automation_data") or {}
        alias = str(auto.get("alias") or "")
        description = str(s.get("description") or auto.get("description") or alias)
        key = (alias or description).casefold().strip()
        if not key or key in seen or key in aliases or is_ignored(dict(s)):
            continue
        entity_ids = sorted(_collect_referenced_entity_ids(auto))
        if len(entity_ids) >= 2 and _covered(set(entity_ids), groups):
            continue
        seen.add(key)
        rows.append(
            {
                "suggestion_id": f"{PATTERN_PREFIX}{s['suggestion_id']}",
                "type": "automation_idea",
                "entities": _names(hass, entity_ids),
                "collection": {"source": "pattern", "name": alias or description, "ref": ""},
                "automation": {
                    "alias": alias,
                    "description": description,
                    "yaml": str(s.get("automation_yaml") or ""),
                },
                "evidence": str(s.get("evidence_summary") or ""),
            }
        )
        if len(rows) >= _MAX_AUTOMATION_IDEAS:
            break
    return rows


def _bursts(
    hass: HomeAssistant, history: dict[str, list[StateChange]]
) -> list[tuple[datetime, frozenset[_Setting]]]:
    """The user's own changes, grouped into bursts: what each burst left each
    device at, and when it started."""
    ignored = resolve_ignored_entity_ids(hass)
    changes: list[tuple[datetime, str, str]] = []
    for entity_id, entries in history.items():
        if entity_id in ignored or hass.states.get(entity_id) is None:
            continue
        if not scene_supports(hass, entity_id):
            continue
        for e in entries:
            if e.get("auto") or e["state"] in _TRANSIENT_STATES:
                continue
            if (ts := dt_util.parse_datetime(e["ts"])) is not None:
                changes.append((ts, entity_id, e["state"]))
    changes.sort()
    groups: list[list[tuple[datetime, str, str]]] = []
    for change in changes:
        if not groups or change[0] - groups[-1][0][0] > _BURST:
            groups.append([])
        groups[-1].append(change)
    bursts: list[tuple[datetime, frozenset[_Setting]]] = []
    for group in groups:
        # The last change wins: switched on, then off again, is off.
        settings = {entity_id: state for _, entity_id, state in group}
        if len(settings) >= _MIN_SCENE_SIZE:
            bursts.append((group[0][0], frozenset(settings.items())))
    return bursts[-_MAX_BURSTS:]


def scene_candidates(
    bursts: list[tuple[datetime, frozenset[_Setting]]],
) -> list[tuple[frozenset[_Setting], int]]:
    """Settings repeated on enough days, with how many: most repeated first.

    A burst may hold one device more than the habit (a lamp switched on that
    evening only), so a candidate is a burst or what two bursts share.
    """
    candidates: set[frozenset[_Setting]] = {b for _, b in bursts}
    for (_, a), (_, b) in combinations(bursts, 2):
        if len(shared := a & b) >= _MIN_SCENE_SIZE:
            candidates.add(shared)
    found: list[tuple[frozenset[_Setting], int]] = []
    for candidate in candidates:
        # "Everything off" is what leaving a room looks like, not a scene.
        if all(state in _OFF_STATES for _, state in candidate):
            continue
        days = {dt_util.as_local(ts).date() for ts, b in bursts if candidate <= b}
        if len(days) >= _MIN_SCENE_DAYS:
            found.append((candidate, len(days)))
    # Largest, then most repeated. A smaller habit inside a kept one is the
    # same habit seen partly.
    found.sort(key=lambda f: (-len(f[0]), -f[1], sorted(f[0])))
    kept: list[tuple[frozenset[_Setting], int]] = []
    for candidate, days in found:
        if any(candidate < k for k, _ in kept):
            continue
        kept.append((candidate, days))
    kept.sort(key=lambda f: (-f[1], -len(f[0])))
    return kept


def scene_rows(
    hass: HomeAssistant,
    history: dict[str, list[StateChange]],
    put_away: Collection[str] = (),
) -> list[HomeSuggestion]:
    """Scenes for the devices the user keeps setting together, leaving out
    the ones in ``put_away`` before counting, so the next habit takes the
    place of a dismissed one."""
    groups = _scene_groups(hass)
    rows: list[HomeSuggestion] = []
    for settings, days in scene_candidates(_bursts(hass, history)):
        entity_ids = sorted(e for e, _ in settings)
        # An existing scene already does this; the burst was it. An automation
        # mentioning them is not: its changes never count as the user's, and it
        # may set them otherwise.
        if _covered(set(entity_ids), groups):
            continue
        states = dict(settings)
        # Named by what it sets, so the same habit keeps its dismissal.
        suggestion_id = _SCENE_PREFIX + ",".join(f"{e}={states[e]}" for e in entity_ids)
        if suggestion_id in put_away:
            continue
        rows.append(
            {
                "suggestion_id": suggestion_id,
                "type": "create_scene",
                "entities": [
                    {**entity, "state": states[entity["entity_id"]]}
                    for entity in _names(hass, entity_ids)
                ],
                "collection": {"source": "usage", "name": "", "ref": ""},
                "days": days,
            }
        )
        if len(rows) >= _MAX_SCENE_IDEAS:
            break
    return rows


async def async_usage_suggestions(
    hass: HomeAssistant, automations: list[dict[str, Any]], put_away: Collection[str] = ()
) -> list[HomeSuggestion]:
    """Scene and automation rows from the pattern store, scenes first,
    without the ones in ``put_away``."""
    store = get_pattern_store(hass)
    if store is None:
        return []
    history = await store.get_all_history(since=dt_util.utcnow() - _SCENE_LOOKBACK)
    pending = await store.get_suggestions(status="pending")
    rows = scene_rows(hass, history, put_away) + automation_idea_rows(hass, pending, automations)
    return [r for r in rows if r["suggestion_id"] not in put_away]


async def async_put_away_pattern_suggestion(
    hass: HomeAssistant, suggestion_id: str, how: str, until: datetime | None = None
) -> None:
    """Tell the pattern store a row of its was dismissed or snoozed, so the
    engine learns from it (its dismissal window and feedback summary)."""
    if not suggestion_id.startswith(PATTERN_PREFIX):
        return
    store = get_pattern_store(hass)
    if store is None:
        return
    pattern_suggestion_id = suggestion_id.removeprefix(PATTERN_PREFIX)
    if how == "dismiss":
        await store.update_suggestion_status(
            pattern_suggestion_id,
            "dismissed",
            dismissed_at=dt_util.utcnow().isoformat(),
        )
    else:
        await store.update_suggestion_status(
            pattern_suggestion_id,
            "snoozed",
            snooze_until=until.isoformat() if until is not None else None,
        )
