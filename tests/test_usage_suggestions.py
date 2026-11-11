"""Tests for home suggestions drawn from how the home is used."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest

from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.home_suggestions import (
    async_dismiss_home_suggestion,
    async_home_suggestions,
    async_snooze_home_suggestion,
)
from custom_components.selora_ai.pattern_store import PatternStore
from custom_components.selora_ai.usage_suggestions import (
    async_scene_rows,
    automation_idea_rows,
    scene_candidates,
)

LAMPS = ("light.sofa", "light.reading", "light.ceiling")
EVENING = (("light.sofa", "on"), ("light.reading", "on"), ("light.ceiling", "off"))


def _at(day: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 1 + day, 19, minute, tzinfo=UTC)


def _history(
    days: list[int], settings: tuple[tuple[str, str], ...] = EVENING, **flags: Any
) -> dict[str, list[dict[str, Any]]]:
    """*settings* applied a few seconds apart on each of *days*."""
    history: dict[str, list[dict[str, Any]]] = {}
    for day in days:
        for second, (entity_id, state) in enumerate(settings):
            ts = _at(day) + timedelta(seconds=10 * second)
            history.setdefault(entity_id, []).append(
                {"state": state, "prev": "x", "ts": ts.isoformat(), **flags}
            )
    return history


@pytest.fixture
def lamps(hass: HomeAssistant) -> HomeAssistant:
    for entity_id in LAMPS:
        hass.states.async_set(entity_id, "off", {"friendly_name": entity_id.split(".")[1]})
    return hass


def _bursts(*sets: tuple[int, frozenset[tuple[str, str]]]) -> list[Any]:
    return [(_at(day), settings) for day, settings in sets]


def test_scene_needs_the_same_settings_on_enough_days() -> None:
    evening = frozenset(EVENING)
    assert scene_candidates(_bursts((0, evening), (1, evening))) == []
    assert scene_candidates(_bursts((0, evening), (1, evening), (2, evening))) == [(evening, 3)]


def test_one_evening_is_one_day_however_often() -> None:
    evening = frozenset(EVENING)
    assert scene_candidates(_bursts((0, evening), (0, evening), (0, evening))) == []


def test_an_extra_device_one_day_keeps_the_habit() -> None:
    evening = frozenset(EVENING)
    extra = evening | {("light.desk", "on")}
    assert scene_candidates(_bursts((0, evening), (1, extra), (2, evening))) == [(evening, 3)]


def test_everything_off_is_not_a_scene() -> None:
    off = frozenset((e, "off") for e in LAMPS)
    assert scene_candidates(_bursts((0, off), (1, off), (2, off))) == []


async def test_scene_row_names_each_device_and_state(lamps: HomeAssistant) -> None:
    rows = await async_scene_rows(lamps, _history([0, 1, 2]))
    assert len(rows) == 1
    row = rows[0]
    assert row["type"] == "create_scene"
    assert row["days"] == 3
    assert {(e["entity_id"], e["state"]) for e in row["entities"]} == set(EVENING)
    assert row["suggestion_id"] == ("scene:light.ceiling=off,light.reading=on,light.sofa=on")


async def test_changes_an_automation_made_are_not_the_users(lamps: HomeAssistant) -> None:
    assert await async_scene_rows(lamps, _history([0, 1, 2], auto=True)) == []


async def test_changes_far_apart_are_not_one_action(lamps: HomeAssistant) -> None:
    history = _history([0, 1, 2], EVENING[:2])
    history["light.ceiling"] = [
        {"state": "off", "prev": "on", "ts": _at(day, 30).isoformat()} for day in (0, 1, 2)
    ]
    assert await async_scene_rows(lamps, history) == []


async def test_an_existing_scene_covers_the_habit(lamps: HomeAssistant) -> None:
    lamps.states.async_set("scene.evening", "unknown", {"entity_id": list(LAMPS)})
    assert await async_scene_rows(lamps, _history([0, 1, 2])) == []


async def test_a_dismissed_scene_is_left_out_before_the_cap(lamps: HomeAssistant) -> None:
    first = (await async_scene_rows(lamps, _history([0, 1, 2])))[0]["suggestion_id"]
    assert await async_scene_rows(lamps, _history([0, 1, 2]), {first}) == []


def _pattern_suggestion(
    suggestion_id: str = "abc", alias: str = "Porch light at dusk", confidence: float = 0.9
) -> dict[str, Any]:
    return {
        "suggestion_id": suggestion_id,
        "confidence": confidence,
        "description": "Turn the porch light on at 19:00",
        "evidence_summary": "Turned on around 19:00 on 9 of 10 days",
        "automation_yaml": "alias: Porch light at dusk\n",
        "automation_data": {
            "alias": alias,
            "triggers": [{"trigger": "time", "at": "19:00:00"}],
            "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}],
        },
    }


async def test_automation_idea_row(hass: HomeAssistant) -> None:
    hass.states.async_set("light.porch", "off", {"friendly_name": "Porch"})
    rows = automation_idea_rows(hass, [_pattern_suggestion()], [])
    assert rows == [
        {
            "suggestion_id": "pattern:abc",
            "type": "automation_idea",
            "entities": [{"entity_id": "light.porch", "name": "Porch"}],
            "collection": {"source": "pattern", "name": "Porch light at dusk", "ref": ""},
            "automation": {
                "alias": "Porch light at dusk",
                "description": "Turn the porch light on at 19:00",
                "yaml": "alias: Porch light at dusk\n",
            },
            "evidence": "Turned on around 19:00 on 9 of 10 days",
        }
    ]


async def test_weak_or_made_automation_ideas_are_left_out(hass: HomeAssistant) -> None:
    assert automation_idea_rows(hass, [_pattern_suggestion(confidence=0.6)], []) == []
    hass.states.async_set("automation.porch", "on", {"friendly_name": "Porch light at dusk"})
    assert automation_idea_rows(hass, [_pattern_suggestion()], []) == []


@pytest.fixture
async def store(hass: HomeAssistant) -> PatternStore:
    pattern_store = PatternStore(hass)
    hass.data.setdefault(DOMAIN, {})["entry"] = {"pattern_store": pattern_store}
    await pattern_store.save_suggestion(_pattern_suggestion())  # type: ignore[arg-type]
    return pattern_store


async def test_home_suggestions_include_usage_rows(
    hass: HomeAssistant, store: PatternStore
) -> None:
    rows = await async_home_suggestions(hass)
    assert [r["suggestion_id"] for r in rows if r["type"] == "automation_idea"] == ["pattern:abc"]


async def test_dismissing_tells_the_pattern_store(hass: HomeAssistant, store: PatternStore) -> None:
    await async_dismiss_home_suggestion(hass, "pattern:abc")
    assert (await store.get_suggestion("abc"))["status"] == "dismissed"
    assert not [r for r in await async_home_suggestions(hass) if r["type"] == "automation_idea"]


async def test_snoozing_tells_the_pattern_store(hass: HomeAssistant, store: PatternStore) -> None:
    await async_snooze_home_suggestion(hass, "pattern:abc")
    suggestion = await store.get_suggestion("abc")
    assert suggestion["status"] == "snoozed"
    assert suggestion["snooze_until"] > dt_util.utcnow().isoformat()


async def test_a_dismissed_idea_is_not_suggested_again(hass: HomeAssistant) -> None:
    """Dismissed for good, not for the week the suggestion alone is suppressed."""
    pattern_store = PatternStore(hass)
    hass.data.setdefault(DOMAIN, {})["entry"] = {"pattern_store": pattern_store}
    pattern_id = await pattern_store.save_pattern(
        {  # type: ignore[typeddict-item]
            "type": "time_based",
            "confidence": 0.9,
            "entity_ids": ["light.porch"],
            "description": "Porch light on at 19:00",
            "evidence": {"_signature": "light.porch:on@19:00"},
        }
    )
    await pattern_store.save_suggestion(
        {**_pattern_suggestion(), "pattern_id": pattern_id}  # type: ignore[typeddict-item]
    )

    await async_dismiss_home_suggestion(hass, "pattern:abc")

    # Neither a rescan nor the backfill of active patterns brings it back.
    assert not await pattern_store.get_patterns(status="active")
    (pattern,) = await pattern_store.get_patterns()
    assert pattern["status"] == "dismissed"


async def test_changes_from_before_they_were_flagged_are_not_the_users(
    lamps: HomeAssistant, store: PatternStore
) -> None:
    """An automation's change recorded before the ``auto`` flag existed looks
    like the user's: the history read for scenes starts when flagging did."""
    for entity_id, entries in _history([0, 1, 2]).items():
        for e in entries:
            await store.record_state_change(entity_id, e["state"], e["prev"], e["ts"])
    data = await store._get_loaded_data()
    data["meta"]["auto_flagged_since"] = (_at(2) + timedelta(hours=1)).isoformat()
    since = _at(0) - timedelta(days=1)

    assert await store.get_all_history(since)
    assert await store.get_user_history(since) == {}


async def test_a_group_and_its_members_are_one_device(lamps: HomeAssistant) -> None:
    lamps.states.async_set("light.living", "off", {"entity_id": ["light.sofa", "light.reading"]})
    settings = (("light.living", "on"), ("light.sofa", "on"), ("light.reading", "on"))

    # Two lamps and their group, switched on together, are not a scene of 3.
    assert await async_scene_rows(lamps, _history([0, 1, 2], settings)) == []
    # With a third device, the scene holds the lamps, not the group too.
    (row,) = await async_scene_rows(
        lamps, _history([0, 1, 2], (*settings, ("light.ceiling", "off")))
    )
    assert {e["entity_id"] for e in row["entities"]} == set(LAMPS)
