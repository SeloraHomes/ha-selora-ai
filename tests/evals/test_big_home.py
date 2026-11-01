"""Does a real model answer a state question exactly in a home too big to dump?

Hubs have hundreds of entities. `get_home_snapshot` truncates there, and an
answer read off a truncated list misses what it is asked about — "One light is
on … the snapshot was truncated, so this may not include every light." The
state question has an exact answer through a filter.
"""

from __future__ import annotations

import re

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest

from ..chat_harness import ChatHarness

pytestmark = pytest.mark.eval

_TILES = re.compile(r"\[\[entit(?:y|ies):([^\]|]+)")
_ON = {f"light.big_{i:03d}" for i in (7, 41, 88, 123, 149)}
_SIZE = 400


async def _many_lights(hass: HomeAssistant) -> None:
    """A hub-sized home: lights, a few on, among sensors that crowd them out."""
    entities = er.async_get(hass)
    for i in range(_SIZE):
        domain = "light" if i < 150 else "sensor"
        entity_id = f"{domain}.big_{i:03d}"
        entities.async_get_or_create(domain, "eval", entity_id, suggested_object_id=f"big_{i:03d}")
        if domain == "light":
            state = "on" if entity_id in _ON else "off"
            attrs = {
                "friendly_name": f"Ceiling Light Number {i:03d} In The Long Hallway",
                "brightness": 180,
                "supported_color_modes": ["brightness"],
            }
        else:
            state = "21.5"
            attrs = {
                "friendly_name": f"Temperature Sensor Number {i:03d} In The Basement",
                "unit_of_measurement": "°C",
                "device_class": "temperature",
            }
        hass.states.async_set(entity_id, state, attrs)
    await hass.async_block_till_done()


@pytest.mark.parametrize("message", ["Which lights are on?", "Give me an overview of what's on"])
async def test_what_is_on_in_a_big_home(
    hass: HomeAssistant, home: ChatHarness, message: str, run: int
) -> None:
    await _many_lights(hass)

    turn = await home.live(message)

    response = str(turn.done.get("response") or "")
    tiled = {eid.strip() for group in _TILES.findall(response) for eid in group.split(",")}
    tools = [(c["tool"], c["arguments"]) for c in turn.tool_calls]
    assert tiled >= _ON, f"tools: {tools}\nreply: {response}"
    assert not {t for t in tiled if t.startswith("light.big_")} - _ON, f"tiled off lights: {tiled}"
    assert "truncat" not in response.lower(), f"tools: {tools}\nreply: {response}"


async def test_more_on_than_a_reply_can_hold_says_how_many(
    hass: HomeAssistant, home: ChatHarness, run: int
) -> None:
    """80 lights on: past every list cap. The reply gives the real total and
    tiles every one of them, through the listing's marker."""
    entities = er.async_get(hass)
    for i in range(120):
        entity_id = f"light.many_{i:03d}"
        entities.async_get_or_create("light", "eval", entity_id, suggested_object_id=entity_id[6:])
        hass.states.async_set(
            entity_id,
            "on" if i < 80 else "off",
            {"friendly_name": f"Warehouse Light {i:03d}", "supported_color_modes": ["onoff"]},
        )
    await hass.async_block_till_done()

    turn = await home.live("Which lights are on?")

    response = str(turn.done.get("response") or "")
    tools = [(c["tool"], c["arguments"]) for c in turn.tool_calls]
    # 80 warehouse lights + the living room lamp.
    assert re.search(r"\b81\b", response), f"tools: {tools}\nreply: {response}"
    tiled = {eid.strip() for group in _TILES.findall(response) for eid in group.split(",")}
    lit = {f"light.many_{i:03d}" for i in range(80)}
    assert tiled >= lit, f"{len(lit - tiled)} lit lights without a tile\ntools: {tools}"
    assert not {t for t in tiled if t.startswith("light.many_")} - lit, "tiled off lights"
