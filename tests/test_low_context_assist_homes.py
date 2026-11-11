"""The low-context entity filter keeps the entity an Assist request is about.

``fixtures/assist_homes/`` holds three homes of allenporter/home-assistant-datasets
(``datasets/<dataset>/<home>/_fixtures.yaml`` at
c422946344b31406f5336e63a865e2d587918e33), verbatim, named
``<dataset>_<home>.yaml``. Each case is a sentence from that dataset whose
target the filter used to drop: a to-do list is named for its owner or shop,
not for "todo" or "shopping", and "off" matched "coffee" inside
``switch.coffee_maker`` so the speaker lost its place to the coffee maker.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from custom_components.selora_ai.llm_client.intent import (
    _LOW_CONTEXT_MAX_ENTITIES,
    _filter_entities_by_keywords,
    _low_context_keywords,
)

_HOMES = Path(__file__).parent / "fixtures" / "assist_homes"


def _home_entities(name: str) -> list[dict[str, Any]]:
    """The fixture home's entities as the snapshot fields the filter reads."""
    docs = [d for d in yaml.safe_load_all((_HOMES / f"{name}.yaml").read_text("utf-8")) if d]
    fixture: dict[str, list[dict[str, Any]]] = {}
    for doc in docs:
        for key, value in doc.items():
            fixture.setdefault(key, []).extend(value)
    area_names = {a["id"]: a["name"] for a in fixture.get("areas", [])}
    device_area = {d["id"]: d.get("area") for d in fixture.get("devices", [])}
    entities: list[dict[str, Any]] = []
    for e in fixture["entities"]:
        area_id = e.get("area") or device_area.get(e.get("device"))
        entities.append(
            {
                "entity_id": e["id"],
                "state": str(e.get("state", "")),
                "attributes": {"friendly_name": e.get("name", "")},
                "area_name": area_names.get(area_id, ""),
            }
        )
    return entities


@pytest.mark.parametrize("cap", [_LOW_CONTEXT_MAX_ENTITIES, 60])
@pytest.mark.parametrize(
    ("home", "message", "target"),
    [
        (
            "assist-mini_dom1-pl-todo",
            "Add clean the kitchen to my todo list",
            "todo.personal_tasks",
        ),
        ("assist_dom1-pl", "put apples on the shopping list", "todo.personal_tasks"),
        ("assist_dom1-pl", "put apples on the shopping list", "todo.trader_joe_s"),
        ("assist_home7-dk", "Turn off the music", "media_player.outdoor_speakers"),
    ],
)
def test_request_target_survives_filter(home: str, message: str, target: str, cap: int) -> None:
    entities = _home_entities(home)
    assert target in {e["entity_id"] for e in entities}
    kept = _filter_entities_by_keywords(
        entities, _low_context_keywords(message), cap=cap, message=message
    )
    assert target in [e["entity_id"] for e in kept]


def test_off_does_not_pull_in_the_coffee_maker() -> None:
    """A whole-word-only keyword never scores as a substring of a longer word."""
    message = "Turn off the music"
    kept = _filter_entities_by_keywords(
        _home_entities("assist_home7-dk"),
        _low_context_keywords(message),
        cap=_LOW_CONTEXT_MAX_ENTITIES,
        message=message,
    )
    assert "switch.coffee_maker" not in [e["entity_id"] for e in kept]
