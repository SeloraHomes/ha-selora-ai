"""Does a real model answer from the right kind of thing?

Each case is a question a user asked and got wrong: the reply tiled a speaker
named after the room someone was seen in, or read a last-camera sensor
instead of the cameras' sightings and lost the snapshots. A case checks what
the turn DID — tools called, tiles shown, services run, the fact it named —
never the wording, so a prompt change is judged on behaviour.

A new failure from a hub becomes a case here before the prompt changes, and a
prompt change ships when every case still passes over a few runs.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any

from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import Event, HomeAssistant, callback
import pytest

from ..chat_harness import ChatHarness, ChatTurn

pytestmark = pytest.mark.eval

_TILES = re.compile(r"\[\[entit(?:y|ies):([^\]|]+)")
_CLOCK = re.compile(r"\b\d{1,2}(?::\d{2})?\s?[ap]\.?m\b|\b\d{1,2}:\d{2}\b", re.IGNORECASE)


@dataclass(frozen=True)
class Case:
    id: str
    message: str
    calls: tuple[str, ...] = ()
    never_calls: tuple[str, ...] = ()
    tiles: tuple[str, ...] = ()
    no_tiles: tuple[str, ...] = ()
    # The reply names at least one of these (casefolded).
    says: tuple[str, ...] = ()
    # "domain.service entity_id" the turn must run.
    services: tuple[str, ...] = ()
    no_service_on: tuple[str, ...] = ()
    # The card shows events, and only events whose description says this.
    shows: str | None = None
    # The card folds none of this camera's events: their label answers.
    folds_none_of: str | None = None
    # The confirmation card the turn must end on (its approval_kind).
    card: str = ""
    # find_camera_events runs exactly this often: a question that opens with
    # its search (camera_question.py) needs no second one.
    searches: int = 0
    # The reply names at most this many clock times: the card shows each
    # event's time under its snapshot, so a list of them only repeats it.
    max_times: int | None = None


_NAMESAKES = (
    "media_player.living_room",
    "camera.living_room",
    "camera.e1_zoom",
    "light.kitchen",
    "lock.front_door",
    "camera.front_door",
)

CASES = (
    Case(
        "last-seen",
        "When was Sam seen for the last time in the house",
        calls=("find_camera_events",),
        no_tiles=_NAMESAKES,
        says=("E1 Zoom", "Kitchen"),
    ),
    Case(
        "last-spotted",
        "where was sam spotted for the last time",
        calls=("find_camera_events",),
        no_tiles=_NAMESAKES,
        says=("E1 Zoom", "Kitchen"),
    ),
    Case(
        "someone-at-door",
        "Was anyone at the front door in the last 3 hours?",
        calls=("find_camera_events",),
        no_tiles=_NAMESAKES,
        folds_none_of="front_door",
        searches=1,
        max_times=1,
    ),
    # The search opens the turn; the model reads it and picks the match.
    Case(
        "courier-described",
        "Did a courier in a red jacket come by the front door in the last 3 hours?",
        calls=("find_camera_events", "show_camera_events"),
        no_tiles=_NAMESAKES,
        shows="red jacket",
        searches=1,
        max_times=1,
    ),
    Case(
        "backpack-verified",
        "Was anyone wearing a backpack at the front door in the last 3 hours? "
        "If you can't verify it, don't show me the clips.",
        calls=("find_camera_events", "show_camera_events"),
        no_tiles=_NAMESAKES,
        shows="backpack",
    ),
    Case(
        "speaker-state",
        "Is the living room speaker playing?",
        never_calls=("find_camera_events",),
        tiles=("media_player.living_room",),
        no_tiles=("camera.living_room", "light.living_room_lamp"),
    ),
    Case(
        "lock-state",
        "Is the front door locked?",
        never_calls=("find_camera_events",),
        tiles=("lock.front_door",),
        no_tiles=("camera.front_door",),
    ),
    # A card is how a delete is asked — never a prose "are you sure?" first.
    Case(
        "delete-automation",
        "Delete the Morning Kitchen Light automation",
        calls=("delete_automation",),
        card="delete",
    ),
    # Inventory: every matching device gets its tile.
    Case(
        "which-lights",
        "Which lights do I have?",
        tiles=("light.kitchen", "light.living_room_lamp"),
    ),
    # The snapshot shows its entities as tiles, not as name — state bullets.
    Case(
        "home-overview",
        "Give me an overview of what's on and off in my home",
        tiles=("light.kitchen", "light.living_room_lamp"),
    ),
    # What is on, and only that (test_big_home.py: when the home is too big
    # to dump).
    Case(
        "whats-on",
        "What's on right now?",
        tiles=("light.living_room_lamp",),
        no_tiles=("light.kitchen",),
    ),
    # A how-to answer is about Home Assistant, not about a device in it.
    Case(
        "how-to-no-tiles",
        "How do I add a Zigbee light to Home Assistant?",
        no_tiles=("light.kitchen", "light.living_room_lamp"),
    ),
    # An explicit request is the authorization: no "should I proceed?" first.
    Case(
        "setup-integration",
        "Set up the Philips Hue integration",
        calls=("add_integration",),
    ),
    # A Matter node id is looked up, not answered with Settings directions.
    Case(
        "matter-node",
        'How can I know which device is the thread node "Node 31 @1:1f"',
        says=("Office Plug",),
    ),
    Case(
        "kitchen-light",
        "Turn on the kitchen light",
        services=("light.turn_on light.kitchen",),
        no_service_on=("media_player.living_room", "camera.e1_zoom"),
    ),
)


def _tiles(response: str) -> set[str]:
    return {eid.strip() for group in _TILES.findall(response) for eid in group.split(",")}


def _shown(turn: ChatTurn, word: str) -> list[str]:
    """What is wrong with the events the card shows, for ``Case.shows``."""
    listed = {
        e["id"]: e
        for c in turn.tool_calls
        if c["tool"] == "find_camera_events"
        for e in (c.get("result") or {}).get("events") or []
    }
    shown = [
        i
        for c in turn.tool_calls
        if c["tool"] == "show_camera_events"
        for i in (c.get("result") or {}).get("shown") or []
    ]
    if not shown:
        return ["showed no event"]
    return [
        f"showed {i} ({(listed.get(i) or {}).get('description') or 'not listed'})"
        for i in shown
        if word not in str((listed.get(i) or {}).get("description") or "").casefold()
    ]


def _folded(turn: ChatTurn, camera: str) -> list[str]:
    """The camera's listed events the card would fold away, for ``Case.folds_none_of``."""
    calls = turn.tool_calls
    picks = [c for c in calls if c["tool"] == "show_camera_events"]
    searched = [c for c in calls if c["tool"] == "find_camera_events"]
    if not picks and not any((c.get("arguments") or {}).get("query") for c in searched):
        return []
    shown = {i for c in picks for i in (c.get("result") or {}).get("shown") or []}
    listed = [
        e["id"]
        for c in searched
        for e in (c.get("result") or {}).get("events") or []
        if e.get("camera") == camera
    ]
    return [f"folded {i}" for i in listed if i not in shown]


def _service_calls(hass: HomeAssistant) -> list[str]:
    seen: list[str] = []

    @callback
    def _record(event: Event) -> None:
        data = event.data
        targets = (data.get("service_data") or {}).get("entity_id") or []
        for eid in [targets] if isinstance(targets, str) else targets:
            seen.append(f"{data['domain']}.{data['service']} {eid}")

    hass.bus.async_listen(EVENT_CALL_SERVICE, _record)
    return seen


def _grade(case: Case, turn: ChatTurn, services: list[str]) -> list[str]:
    response = str(turn.done.get("response") or "")
    called = [c["tool"] for c in turn.tool_calls]
    tiles = _tiles(response)
    failures = [f"did not call {t}" for t in case.calls if t not in called]
    failures += [f"called {t}" for t in case.never_calls if t in called]
    failures += [f"no tile for {e}" for e in case.tiles if e not in tiles]
    failures += [f"tiled {e}" for e in case.no_tiles if e in tiles]
    # One block: the panel groups by area, and its tile budget is per reply.
    if (blocks := response.count("[[entities:")) > 1:
        failures.append(f"split the tiles into {blocks} blocks")
    if case.says and not any(s.casefold() in response.casefold() for s in case.says):
        failures.append(f"names none of {list(case.says)}")
    failures += [f"did not run {s}" for s in case.services if s not in services]
    failures += [f"ran {s}" for s in services if s.split(" ", 1)[1] in case.no_service_on]
    if case.shows:
        failures += _shown(turn, case.shows)
    if case.folds_none_of:
        failures += _folded(turn, case.folds_none_of)
    searched = called.count("find_camera_events")
    if case.searches and searched != case.searches:
        failures.append(f"searched {searched} times, not {case.searches}")
    if case.max_times is not None and (times := len(_CLOCK.findall(response))) > case.max_times:
        failures.append(f"listed {times} times the card already shows")
    if case.card:
        approval = turn.done.get("command_approval") or {}
        if approval.get("approval_kind") != case.card:
            failures.append(f"no {case.card} card (got {approval.get('approval_kind')})")
    return failures


def _report(case: Case, run: int, turn: ChatTurn, failures: list[str]) -> None:
    """Append the turn to ``SELORA_EVAL_REPORT``, to compare runs and prompts."""
    path = os.environ.get("SELORA_EVAL_REPORT")
    if not path:
        return
    row: dict[str, Any] = {
        "case": case.id,
        "run": run,
        "passed": not failures,
        "failures": failures,
        "tools": [{"tool": c["tool"], "arguments": c["arguments"]} for c in turn.tool_calls],
        "response": turn.done.get("response"),
    }
    with Path(path).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_chat_answers_from_the_right_kind(
    hass: HomeAssistant, home: ChatHarness, case: Case, run: int
) -> None:
    services = _service_calls(hass)

    turn = await home.live(case.message)

    # A provider that refused the turn (expired token, no credit) is not an
    # answer to grade: say why it failed.
    assert not turn.errors, turn.errors
    assert not [e for e in turn.events if e.get("type") == "error"], turn.events[-1]
    failures = _grade(case, turn, services)
    await hass.async_add_executor_job(_report, case, run, turn, failures)
    tools = [(c["tool"], c["arguments"]) for c in turn.tool_calls]
    assert not failures, f"{failures}\ntools: {tools}\nreply: {turn.done.get('response')}"
