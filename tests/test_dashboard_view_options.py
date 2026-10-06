"""A dashboard page's options, its badges and its sections.

The view tools could set a title, path, icon and layout only: no theme, no
background, no subview, no "only Alex sees this page", no badges, and a
sections page was one grid — every card went into the first section, with no
way to start another or set a section's width. Lovelace validates none of it,
so each option is checked before anything is stored.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import TOOL_UPDATE_DASHBOARD_VIEW
from custom_components.selora_ai.tool_executor import ToolExecutor


@pytest.fixture
async def board(hass: HomeAssistant) -> HomeAssistant:
    assert await async_setup_component(hass, "lovelace", {"lovelace": {"mode": "storage"}})
    await hass.async_block_till_done()
    for entity_id in ("light.lamp", "sensor.temp", "person.alex"):
        hass.states.async_set(entity_id, "on")
    hass.data["frontend_themes"] = {"Midnight": {}, "Daylight": {}}
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    await hass.data[LOVELACE_DATA].dashboards[None].async_save({"views": [{"title": "Home"}]})
    return hass


async def _run(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await ToolExecutor(hass, MagicMock(), is_admin=True).execute(tool, arguments)


async def _views(hass: HomeAssistant) -> list[dict[str, Any]]:
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    return (await hass.data[LOVELACE_DATA].dashboards[None].async_load(False))["views"]


async def test_a_page_is_created_with_its_options(board: HomeAssistant) -> None:
    alex = await board.auth.async_create_user("Alex")
    result = await _run(
        board,
        "add_dashboard_view",
        title="Night",
        options={
            "theme": "Midnight",
            "background": {"image": "/local/stars.jpg", "opacity": 40},
            "visible": ["alex"],
            "badges": ["person.alex", {"entity": "sensor.temp", "show_name": True}],
        },
    )

    assert result["status"] == "created", result
    view = (await _views(board))[-1]
    assert view["theme"] == "Midnight"
    assert view["background"] == {"image": "/local/stars.jpg", "opacity": 40}
    assert view["visible"] == [{"user": alex.id}]
    assert view["badges"] == [
        {"type": "entity", "entity": "person.alex"},
        {"type": "entity", "entity": "sensor.temp", "show_name": True},
    ]


@pytest.mark.parametrize(
    ("options", "says"),
    [
        ({"theme": "Neon"}, "Installed: Daylight, Midnight"),
        ({"badges": ["sensor.nope"]}, "no entity 'sensor.nope'"),
        ({"visible": ["nobody"]}, "No single user"),
        ({"max_columns": 3}, "sections page only"),
        ({"back_path": "/lovelace/0"}, "subview=true"),
        ({"colour": "red"}, "Unknown view options"),
    ],
)
async def test_an_option_that_would_not_work_writes_nothing(
    board: HomeAssistant, options: dict[str, Any], says: str
) -> None:
    result = await _run(board, "add_dashboard_view", title="Bad", options=options)

    assert says in result["error"]
    assert len(await _views(board)) == 1


async def test_options_are_changed_and_cleared(board: HomeAssistant) -> None:
    await _run(board, "add_dashboard_view", title="Detail", layout="sections")

    set_ = await _run(
        board,
        "update_dashboard_view",
        view="1",
        options={"subview": True, "back_path": "/lovelace/0", "max_columns": 3},
    )
    cleared = await _run(board, "update_dashboard_view", view="1", clear=["max_columns"])
    view = (await _views(board))[1]

    assert set_["changed"] == ["back_path", "max_columns", "subview"]
    assert cleared["changed"] == ["cleared max_columns"]
    assert view["subview"] is True
    assert "max_columns" not in view


async def test_cards_go_into_the_section_named_or_a_new_one(board: HomeAssistant) -> None:
    await _run(board, "add_dashboard_view", title="Rooms", layout="sections")

    first = await _run(
        board, "insert_dashboard_card", view="1", card={"type": "tile", "entity": "light.lamp"}
    )
    new = await _run(
        board,
        "insert_dashboard_card",
        view="1",
        section="new",
        card={"type": "tile", "entity": "sensor.temp"},
        tag="t2",
    )
    again = await _run(
        board,
        "insert_dashboard_card",
        view="1",
        section="1",
        card={"type": "heading", "heading": "Climate"},
        tag="t3",
    )
    missing = await _run(
        board,
        "insert_dashboard_card",
        view="1",
        section="7",
        card={"type": "tile", "entity": "light.lamp"},
        tag="t4",
    )

    assert first["ok"] and new["ok"] and again["ok"], (first, new, again)
    sections = (await _views(board))[1]["sections"]
    assert [c["type"] for c in sections[0]["cards"]] == ["tile"]
    assert [c["type"] for c in sections[1]["cards"]] == ["tile", "heading"]
    assert missing["reason"] == "section_not_found"


async def test_a_section_is_read_and_its_options_set(board: HomeAssistant) -> None:
    await _run(board, "add_dashboard_view", title="Rooms", layout="sections")
    await _run(
        board, "insert_dashboard_card", view="1", card={"type": "tile", "entity": "light.lamp"}
    )
    await _run(
        board,
        "insert_dashboard_card",
        view="1",
        section="new",
        card={"type": "tile", "entity": "sensor.temp"},
        tag="t2",
    )

    result = await _run(
        board,
        "update_dashboard_view",
        view="1",
        section=1,
        options={
            "column_span": 2,
            "visibility": [{"condition": "screen", "media_query": "(min-width: 1024px)"}],
        },
    )
    read = await _run(board, "get_dashboard", view="1")

    assert result["status"] == "updated", result
    assert [c["section"] for c in read["view"]["cards"]] == [0, 1]
    assert read["view"]["sections"][1]["column_span"] == 2
    assert read["view"]["sections"][1]["card_count"] == 1


async def test_section_options_are_refused_where_they_do_not_apply(board: HomeAssistant) -> None:
    await _run(board, "add_dashboard_view", title="Plain")
    await _run(board, "add_dashboard_view", title="Rooms", layout="sections")

    no_sections = await _run(
        board, "update_dashboard_view", view="1", section=0, options={"column_span": 2}
    )
    too_wide = await _run(
        board, "update_dashboard_view", view="2", section=0, options={"column_span": 9}
    )
    on_masonry = await _run(
        board,
        "insert_dashboard_card",
        view="1",
        section="new",
        card={"type": "tile", "entity": "light.lamp"},
    )

    assert "no sections" in no_sections["error"]
    assert "1 to 4" in too_wide["error"]
    assert on_masonry["reason"] == "section_not_found"


async def test_a_read_reports_the_options_set(board: HomeAssistant) -> None:
    await _run(
        board, "add_dashboard_view", title="Night", options={"theme": "Midnight", "subview": True}
    )

    read = await _run(board, "get_dashboard", view="1")

    assert read["view"]["options"] == {"theme": "Midnight", "subview": True}


async def test_mcp_takes_the_same_options(board: HomeAssistant) -> None:
    await _run(board, "add_dashboard_view", title="Night")

    result = await mcp_dispatch._get_tool_handlers()[TOOL_UPDATE_DASHBOARD_VIEW](
        board, {"view": "1", "options": '{"theme": "Daylight"}'}
    )

    assert result["status"] == "updated", result
    assert (await _views(board))[1]["theme"] == "Daylight"


async def test_malformed_options_are_refused_not_dropped(board: HomeAssistant) -> None:
    for options in ("{theme: Midnight", ["theme"], "Midnight"):
        result = await _run(board, "add_dashboard_view", title="Bad", options=options)
        assert "options must be an object" in result["error"], options
    assert len(await _views(board)) == 1


async def test_a_back_path_cannot_outlive_its_subview(board: HomeAssistant) -> None:
    await _run(
        board,
        "add_dashboard_view",
        title="Detail",
        options={"subview": True, "back_path": "/lovelace/0"},
    )

    turned_off = await _run(board, "update_dashboard_view", view="1", options={"subview": False})
    cleared = await _run(board, "update_dashboard_view", view="1", clear=["subview"])
    both = await _run(board, "update_dashboard_view", view="1", clear=["subview", "back_path"])

    assert "back_path is for a subview" in turned_off["error"]
    assert "back_path is for a subview" in cleared["error"]
    assert both["status"] == "updated", both
    view = (await _views(board))[1]
    assert "subview" not in view and "back_path" not in view


async def test_an_entity_badge_needs_its_entity(board: HomeAssistant) -> None:
    empty = await _run(board, "add_dashboard_view", title="B", options={"badges": [{}]})
    custom = await _run(
        board,
        "add_dashboard_view",
        title="C",
        options={"badges": [{"type": "custom:weather-badge"}]},
    )

    assert "no entity" in empty["error"]
    assert custom["status"] == "created", custom


async def test_leaving_sections_drops_what_only_sections_use(board: HomeAssistant) -> None:
    await _run(
        board,
        "add_dashboard_view",
        title="Rooms",
        layout="sections",
        options={"max_columns": 3, "dense_section_placement": True, "theme": "Midnight"},
    )

    await _run(board, "update_dashboard_view", view="1", layout="masonry")
    view = (await _views(board))[1]

    assert "max_columns" not in view and "dense_section_placement" not in view
    assert view["theme"] == "Midnight"
