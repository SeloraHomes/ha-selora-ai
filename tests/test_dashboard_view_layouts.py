"""Tests for a dashboard page's layout: masonry, sections, panel, sidebar.

A panel page is how full-screen wall-panel pages are built — one card, full
width, with the tiles inside a grid — and the tools could not make one, so a
model asked to restyle such a dashboard reached for another MCP server.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai.tool_executor import ToolExecutor

GRID = {
    "type": "grid",
    "columns": 3,
    "cards": [
        {"type": "tile", "entity": "light.lamp"},
        {"type": "tile", "entity": "switch.door"},
    ],
}


@pytest.fixture
async def board(hass: HomeAssistant) -> HomeAssistant:
    assert await async_setup_component(hass, "lovelace", {"lovelace": {"mode": "storage"}})
    await hass.async_block_till_done()
    for entity_id in ("light.lamp", "switch.door"):
        hass.states.async_set(entity_id, "off")
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    await hass.data[LOVELACE_DATA].dashboards[None].async_save({"views": [{"title": "Home"}]})
    return hass


async def _run(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await ToolExecutor(hass, MagicMock(), is_admin=True).execute(tool, arguments)


async def _views(hass: HomeAssistant) -> list[dict[str, Any]]:
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    return (await hass.data[LOVELACE_DATA].dashboards[None].async_load(False))["views"]


async def test_a_full_width_page_is_a_panel_page_with_one_grid(board: HomeAssistant) -> None:
    result = await _run(board, "add_dashboard_view", title="Wall", layout="panel", cards=[GRID])

    assert "error" not in result, result
    wall = (await _views(board))[-1]
    assert wall["type"] == "panel"
    assert wall["cards"] == [GRID]


async def test_a_panel_page_holds_one_card(board: HomeAssistant) -> None:
    result = await _run(
        board,
        "add_dashboard_view",
        title="Wall",
        layout="panel",
        cards=GRID["cards"],
    )

    assert "only its first card" in result["error"]
    assert len(await _views(board)) == 1


async def test_a_second_card_on_a_panel_page_is_refused(board: HomeAssistant) -> None:
    await _run(board, "add_dashboard_view", title="Wall", layout="panel", cards=[GRID])

    result = await _run(
        board,
        "insert_dashboard_card",
        view="1",
        card={"type": "tile", "entity": "light.lamp"},
    )

    assert result["ok"] is False
    assert result["reason"] == "panel_full"
    assert (await _views(board))[1]["cards"] == [GRID]


async def test_changing_a_layout_carries_the_cards_over(board: HomeAssistant) -> None:
    await _run(
        board,
        "add_dashboard_view",
        title="Lights",
        cards=[{"type": "tile", "entity": "light.lamp"}],
    )

    to_sections = await _run(board, "update_dashboard_view", view="1", layout="sections")
    sections = (await _views(board))[1]
    to_panel = await _run(board, "update_dashboard_view", view="1", layout="panel")
    panel = (await _views(board))[1]

    assert to_sections["status"] == "updated", to_sections
    assert sections["type"] == "sections"
    assert sections["sections"][0]["cards"] == [{"type": "tile", "entity": "light.lamp"}]
    assert to_panel["status"] == "updated", to_panel
    assert panel == {
        "title": "Lights",
        "type": "panel",
        "cards": [{"type": "tile", "entity": "light.lamp"}],
    }


async def test_a_page_with_several_cards_cannot_become_a_panel(board: HomeAssistant) -> None:
    await _run(board, "add_dashboard_view", title="Lights", cards=GRID["cards"])

    result = await _run(board, "update_dashboard_view", view="1", layout="panel")

    assert "has 2 cards" in result["error"]
    assert "type" not in (await _views(board))[1]


async def test_the_older_sections_flag_still_works(board: HomeAssistant) -> None:
    await _run(board, "add_dashboard_view", title="New", sections=True)

    assert (await _views(board))[-1]["type"] == "sections"


async def test_cards_moved_onto_a_panel_page_must_fit_on_it(board: HomeAssistant) -> None:
    await _run(board, "add_dashboard_view", title="Wall", layout="panel", cards=[GRID])
    await _run(
        board,
        "add_dashboard_view",
        title="Lights",
        cards=[{"type": "tile", "entity": "light.lamp"}],
    )

    result = await _run(board, "move_dashboard_card", view="2", from_index=0, to_view="1")

    assert "panel page" in result["error"]
    views = await _views(board)
    assert views[1]["cards"] == [GRID]
    assert views[2]["cards"] == [{"type": "tile", "entity": "light.lamp"}]


async def test_a_read_reports_the_layout_names_the_tools_take(board: HomeAssistant) -> None:
    await _run(board, "add_dashboard_view", title="Wall", layout="panel", cards=[GRID])

    result = await _run(board, "get_dashboard")

    assert [v["type"] for v in result["views"]] == ["masonry", "panel"]


async def test_the_button_card_is_a_card_in_a_home_with_buttons(board: HomeAssistant) -> None:
    """button.* entities are everywhere; the core button card must not be
    mistaken for "a domain, not a card type"."""
    board.states.async_set("button.restart", "unknown")

    result = await _run(
        board,
        "insert_dashboard_card",
        view="0",
        card={"type": "button", "entity": "button.restart"},
    )

    assert result["ok"] is True, result
