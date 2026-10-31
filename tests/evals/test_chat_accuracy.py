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
    Case(
        "kitchen-light",
        "Turn on the kitchen light",
        services=("light.turn_on light.kitchen",),
        no_service_on=("media_player.living_room", "camera.e1_zoom"),
    ),
)


def _tiles(response: str) -> set[str]:
    return {eid.strip() for group in _TILES.findall(response) for eid in group.split(",")}


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
    if case.says and not any(s.casefold() in response.casefold() for s in case.says):
        failures.append(f"names none of {list(case.says)}")
    failures += [f"did not run {s}" for s in case.services if s not in services]
    failures += [f"ran {s}" for s in services if s.split(" ", 1)[1] in case.no_service_on]
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
