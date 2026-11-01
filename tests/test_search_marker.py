"""A listing search hands the reply a marker that the chat expands to every match."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai.conversation import _unwrap_entity_markers
from custom_components.selora_ai.tool_executor import ToolExecutor, expand_search_markers


@pytest.fixture
async def lights(hass: HomeAssistant) -> HomeAssistant:
    for i in range(120):
        hass.states.async_set(
            f"light.l{i:03d}", "on" if i < 80 else "off", {"friendly_name": f"Light {i:03d}"}
        )
    await hass.async_block_till_done()
    return hass


def _executor(hass: HomeAssistant) -> ToolExecutor:
    return ToolExecutor(hass, MagicMock(), is_admin=True)


def _ids(text: str) -> list[str]:
    return text.split("[[entities:", 1)[1].split("]]", 1)[0].split(",")


async def test_a_listing_expands_past_its_row_cap(lights: HomeAssistant) -> None:
    executor = _executor(lights)
    result = await executor.execute("search_entities", {"domain": "light", "state": "on"})

    assert result["count"] == 50 and result["omitted"] == 30
    assert result["tiles"] == "[[search:1]]"

    text = expand_search_markers(f"80 lights are on:\n{result['tiles']}", executor.listings)

    assert text.startswith("80 lights are on:\n[[entities:")
    assert sorted(_ids(text)) == [f"light.l{i:03d}" for i in range(80)]


async def test_the_model_limit_does_not_shrink_the_grid(lights: HomeAssistant) -> None:
    executor = _executor(lights)
    result = await executor.execute("search_entities", {"domain": "light", "limit": 5})

    text = expand_search_markers(result["tiles"], executor.listings)

    assert len(_ids(text)) == 120


async def test_each_listing_gets_its_own_marker(lights: HomeAssistant) -> None:
    executor = _executor(lights)
    on = await executor.execute("search_entities", {"domain": "light", "state": "on"})
    off = await executor.execute("search_entities", {"domain": "light", "state": "off"})

    text = expand_search_markers(f"{on['tiles']}\n{off['tiles']}", executor.listings)

    first, second = text.split("\n")
    assert len(_ids(first)) == 80 and len(_ids(second)) == 40


async def test_the_grid_is_what_the_model_counted(lights: HomeAssistant) -> None:
    executor = _executor(lights)
    result = await executor.execute("search_entities", {"domain": "light", "state": "on"})
    lights.states.async_set("light.l100", "on", {"friendly_name": "Light 100"})

    text = expand_search_markers(result["tiles"], executor.listings)

    assert len(_ids(text)) == 80


async def test_no_marker_past_the_tile_cap(
    lights: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("custom_components.selora_ai.tool_executor.MAX_SEARCH_MARKER_TILES", 100)
    result = await _executor(lights).execute("search_entities", {"domain": "light"})

    assert result["omitted"] == 70
    assert "tiles" not in result


async def test_a_name_search_hands_out_no_marker(lights: HomeAssistant) -> None:
    result = await _executor(lights).execute("search_entities", {"query": "Light 007"})

    assert "tiles" not in result


async def test_a_marker_no_search_handed_out_is_dropped(lights: HomeAssistant) -> None:
    executor = _executor(lights)
    await executor.execute("search_entities", {"domain": "light"})

    text = expand_search_markers("Here:\n[[search:7]]", executor.listings)

    assert text == "Here:\n"


async def test_assist_drops_the_marker() -> None:
    assert _unwrap_entity_markers("80 lights are on.\n[[search:1]]", []) == "80 lights are on."
