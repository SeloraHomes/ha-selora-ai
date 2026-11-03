"""The eval home holds the traps it claims to, with no model involved.

Runs in the normal suite: an eval whose home quietly stopped serving events
or recording the sensor would pass for the wrong reason.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant

from custom_components.selora_ai.tool_executor import ToolExecutor

from .home import BACKPACK, LAST_CAMERA, MATTER_PLUG, build_home, fake_frigate


async def test_the_cameras_saw_sam_last_where_the_sensor_does_not_say(
    recorder_mock: Any, hass: HomeAssistant
) -> None:
    await build_home(hass)
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)

    with fake_frigate():
        events = await executor.execute("find_camera_events", {"names": ["Sam"]})
    history = await executor.execute("get_entity_history", {"entity_id": LAST_CAMERA})

    assert [e["camera"] for e in events["events"]] == ["e1_zoom", "e1_zoom", "living_room"]
    assert [c["state"] for c in history["changes"]][-2:] == ["Living Room", "unknown"]


async def test_one_front_door_visitor_is_described_with_a_backpack(
    recorder_mock: Any, hass: HomeAssistant
) -> None:
    await build_home(hass)
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)

    with fake_frigate():
        found = await executor.execute(
            "find_camera_events", {"cameras": ["front_door"], "hours": 3}
        )

    descriptions = [e.get("description") for e in found["events"]]
    assert len(descriptions) == 5
    assert [d for d in descriptions if d and "backpack" in d] == [BACKPACK]
    assert descriptions.count(None) == 2


async def test_names_collide_across_kinds(recorder_mock: Any, hass: HomeAssistant) -> None:
    await build_home(hass)

    named = [s.entity_id for s in hass.states.async_all() if s.name == "Living Room"]

    assert sorted(named) == ["camera.living_room", "media_player.living_room"]


async def test_the_matter_plug_is_found_by_its_node_and_not_by_its_name(
    recorder_mock: Any, hass: HomeAssistant
) -> None:
    await build_home(hass)
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)

    found = await executor.execute("search_entities", {"query": "Node 31 @1:1f"})

    assert [m["entity_id"] for m in found["matches"]] == [MATTER_PLUG]
    assert not [s for s in hass.states.async_all() if "31" in (s.name or "")]
