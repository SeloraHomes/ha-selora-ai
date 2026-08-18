"""Tests for the dashboard read/edit chat tools.

These drive HA's real Lovelace storage — ``LovelaceStorage.async_load`` /
``async_save`` against a real ``hass`` — so a change to how a dashboard document
is stored fails here rather than at runtime.
"""

from __future__ import annotations

import inspect
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai import dashboard_manager as dm
from custom_components.selora_ai.mcp_server import _preview_remove_dashboard_view
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import (
    COMMAND_TOOL_NAMES,
    CONFIG_TOOL_NAMES,
    TOOL_MAP,
)

_DASHBOARD_TOOLS = (
    "get_dashboard",
    "get_dashboard_card",
    "add_dashboard_view",
    "update_dashboard_view",
    "remove_dashboard_view",
    "update_dashboard_card",
    "remove_dashboard_card",
)


def _make_executor(hass: HomeAssistant, *, is_admin: bool = True) -> ToolExecutor:
    return ToolExecutor(hass, MagicMock(), is_admin=is_admin)


def _document() -> dict[str, Any]:
    """A dashboard with a classic view and a sections view."""
    return {
        "title": "Home",
        "views": [
            {
                "title": "Living",
                "path": "living",
                "cards": [
                    {"type": "light", "entity": "light.lamp"},
                    {"type": "thermostat", "entity": "climate.main"},
                ],
            },
            {
                "title": "Garage",
                "path": "garage",
                "type": "sections",
                "sections": [
                    {"type": "grid", "cards": [{"type": "button", "entity": "switch.door"}]},
                    {"type": "grid", "cards": [{"type": "gauge", "entity": "sensor.temp"}]},
                ],
            },
        ],
    }


@pytest.fixture
async def board(hass: HomeAssistant) -> HomeAssistant:
    """A hass with lovelace up and a storage dashboard holding _document()."""
    assert await async_setup_component(hass, "lovelace", {"lovelace": {"mode": "storage"}})
    await hass.async_block_till_done()

    # The cards below reference these; card writes now refuse unknown entity
    # ids, because Lovelace stores them happily and renders "Entity not found".
    for entity_id, state in (
        ("light.lamp", "off"),
        ("climate.main", "heat"),
        ("switch.door", "off"),
        ("sensor.temp", "20"),
        ("light.other", "off"),
    ):
        hass.states.async_set(entity_id, state)

    from homeassistant.components.lovelace.const import LOVELACE_DATA

    await hass.data[LOVELACE_DATA].dashboards[None].async_save(_document())
    return hass


# ── Registration ────────────────────────────────────────────────────────────


def test_dashboard_tools_are_registered() -> None:
    for tool in _DASHBOARD_TOOLS:
        assert tool in TOOL_MAP, f"{tool} missing"


def test_dashboard_tools_are_reachable_from_both_lanes() -> None:
    """ "Add a card to my dashboard" classifies as a command; "reorganise my
    dashboard" as config. The family serves both, and before this it was in
    NEITHER lane — so a command-classified turn could not see it at all."""
    for tool in (*_DASHBOARD_TOOLS, "list_dashboards", "insert_dashboard_card"):
        assert tool in COMMAND_TOOL_NAMES, f"{tool} unreachable on a command turn"
        assert tool in CONFIG_TOOL_NAMES, f"{tool} unreachable on a config turn"


def test_dashboard_writes_require_admin() -> None:
    for tool in ("get_dashboard", "get_dashboard_card"):
        assert TOOL_MAP[tool].requires_admin is False
    for tool in (
        "add_dashboard_view",
        "update_dashboard_view",
        "remove_dashboard_view",
        "update_dashboard_card",
        "remove_dashboard_card",
    ):
        assert TOOL_MAP[tool].requires_admin is True


# ── Reads ───────────────────────────────────────────────────────────────────


async def test_get_dashboard_lists_views_with_counts(board: HomeAssistant) -> None:
    result = await _make_executor(board).execute("get_dashboard", {})
    assert result["view_count"] == 2
    assert result["editable"] is True
    assert [v["title"] for v in result["views"]] == ["Living", "Garage"]
    # A sections view's cards live under sections[] — counted, not missed.
    assert result["views"][1]["card_count"] == 2
    # Without a view, no cards: a whole dashboard is unreadable in one payload.
    assert "view" not in result


async def test_get_dashboard_returns_cards_for_one_view(board: HomeAssistant) -> None:
    result = await _make_executor(board).execute("get_dashboard", {"view": "living"})
    cards = result["view"]["cards"]
    assert [c["type"] for c in cards] == ["light", "thermostat"]
    assert cards[0]["entity"] == "light.lamp"
    assert all(c["fingerprint"] for c in cards)


async def test_sections_cards_are_addressed_by_a_flat_index(board: HomeAssistant) -> None:
    """A sections view spreads cards across sections[].cards; the caller should
    not have to know which section a card is in."""
    result = await _make_executor(board).execute("get_dashboard", {"view": "garage"})
    assert [c["type"] for c in result["view"]["cards"]] == ["button", "gauge"]
    assert [c["index"] for c in result["view"]["cards"]] == [0, 1]


async def test_get_card_returns_the_full_config(board: HomeAssistant) -> None:
    result = await _make_executor(board).execute(
        "get_dashboard_card", {"view": "living", "card_index": 1}
    )
    assert result["card"] == {"type": "thermostat", "entity": "climate.main"}
    assert result["fingerprint"]


async def test_unknown_dashboard_names_the_real_ones(board: HomeAssistant) -> None:
    result = await _make_executor(board).execute("get_dashboard", {"dashboard_target": "nope"})
    assert "No dashboard" in result["error"]


# ── View resolution ─────────────────────────────────────────────────────────


def test_duplicate_view_titles_are_refused_not_guessed() -> None:
    """Lovelace validates nothing server-side, so titles are not unique.
    Taking the first match would edit an arbitrary page."""
    document = {"views": [{"title": "Home"}, {"title": "Home"}]}
    index, error = dm.resolve_view(document, "Home")
    assert index is None
    assert "matches 2 views" in error

    # The index is the unambiguous handle the error points at.
    assert dm.resolve_view(document, 1) == (1, None)
    assert dm.resolve_view(document, "1") == (1, None)


def test_view_out_of_range_is_reported(board: HomeAssistant) -> None:
    index, error = dm.resolve_view(_document(), 9)
    assert index is None
    assert "out of range" in error


# ── View writes ─────────────────────────────────────────────────────────────


async def test_add_view_appends_and_is_usable(board: HomeAssistant) -> None:
    result = await _make_executor(board).execute(
        "add_dashboard_view", {"title": "Garden", "path": "garden", "icon": "mdi:flower"}
    )
    assert result["status"] == "created"
    assert result["view_index"] == 2

    listing = await _make_executor(board).execute("get_dashboard", {})
    assert [v["title"] for v in listing["views"]] == ["Living", "Garage", "Garden"]


async def test_add_sections_view_is_seeded(board: HomeAssistant) -> None:
    """A sections view with no sections silently drops the first card added."""
    await _make_executor(board).execute("add_dashboard_view", {"title": "New", "sections": True})
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    document = await board.data[LOVELACE_DATA].dashboards[None].async_load(False)
    assert document["views"][2]["sections"] == [{"type": "grid", "cards": []}]


async def test_add_view_refuses_a_duplicate_path(board: HomeAssistant) -> None:
    result = await _make_executor(board).execute(
        "add_dashboard_view", {"title": "Other", "path": "living"}
    )
    assert "already exists" in result["error"]


async def test_update_view_renames(board: HomeAssistant) -> None:
    result = await _make_executor(board).execute(
        "update_dashboard_view", {"view": "living", "title": "Lounge"}
    )
    assert result["changed"] == ["title"]
    listing = await _make_executor(board).execute("get_dashboard", {})
    assert listing["views"][0]["title"] == "Lounge"


async def test_update_view_noop_when_nothing_asked(board: HomeAssistant) -> None:
    result = await _make_executor(board).execute("update_dashboard_view", {"view": "living"})
    assert result["status"] == "unchanged"


# ── Card writes ─────────────────────────────────────────────────────────────


async def test_update_card_replaces_in_place(board: HomeAssistant) -> None:
    executor = _make_executor(board)
    read = await executor.execute("get_dashboard_card", {"view": "living", "card_index": 0})

    result = await executor.execute(
        "update_dashboard_card",
        {
            "view": "living",
            "card_index": 0,
            "card": {"type": "light", "entity": "light.lamp", "name": "Reading"},
            "expected_fingerprint": read["fingerprint"],
        },
    )
    assert result["status"] == "updated"
    after = await executor.execute("get_dashboard_card", {"view": "living", "card_index": 0})
    assert after["card"]["name"] == "Reading"


async def test_update_card_refuses_a_stale_fingerprint(board: HomeAssistant) -> None:
    """The index is the only handle a caller has, and the UI edits this same
    document — so a moved card must not be overwritten."""
    executor = _make_executor(board)
    result = await executor.execute(
        "update_dashboard_card",
        {
            "view": "living",
            "card_index": 0,
            "card": {"type": "light", "entity": "light.other"},
            "expected_fingerprint": "0000000000000000",
        },
    )
    assert "changed since it was read" in result["error"]
    after = await executor.execute("get_dashboard_card", {"view": "living", "card_index": 0})
    assert after["card"]["entity"] == "light.lamp"


async def test_remove_card_returns_the_removed_config(board: HomeAssistant) -> None:
    """Removal is reversible only if the caller gets the card back."""
    executor = _make_executor(board)
    result = await executor.execute("remove_dashboard_card", {"view": "garage", "card_index": 1})
    assert result["status"] == "deleted"
    assert result["card_type"] == "gauge"

    listing = await executor.execute("get_dashboard", {"view": "garage"})
    assert [c["type"] for c in listing["view"]["cards"]] == ["button"]


async def test_remove_card_from_a_sections_view_hits_the_right_section(
    board: HomeAssistant,
) -> None:
    """Flat index 0 is in section 0; removing it must not disturb section 1."""
    await _make_executor(board).execute(
        "remove_dashboard_card", {"view": "garage", "card_index": 0}
    )
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    document = await board.data[LOVELACE_DATA].dashboards[None].async_load(False)
    sections = document["views"][1]["sections"]
    assert sections[0]["cards"] == []
    assert sections[1]["cards"] == [{"type": "gauge", "entity": "sensor.temp"}]


async def test_card_index_out_of_range(board: HomeAssistant) -> None:
    result = await _make_executor(board).execute(
        "remove_dashboard_card", {"view": "living", "card_index": 9}
    )
    assert "out of range" in result["error"]


async def test_a_non_numeric_card_index_is_rejected(board: HomeAssistant) -> None:
    """Rather than silently addressing card 0."""
    result = await _make_executor(board).execute(
        "remove_dashboard_card", {"view": "living", "card_index": "second"}
    )
    assert "out of range" in result["error"]


# ── View removal goes through the confirmation card ─────────────────────────


async def test_remove_view_asks_first_and_names_the_blast_radius(
    board: HomeAssistant,
) -> None:
    result = await _make_executor(board).execute("remove_dashboard_view", {"view": "garage"})
    assert result["requires_approval"] is True
    card = result["destructive"]
    assert card["kind"] == "dashboard_view"
    assert "2 cards" in card["label"]
    # The card is a question — the view is still there.
    listing = await _make_executor(board).execute("get_dashboard", {})
    assert listing["view_count"] == 2


async def test_confirmed_removal_verifies_view_content(board: HomeAssistant) -> None:
    """A view has no id, so the confirmation carries a content fingerprint."""
    document = _document()
    stale = dm.view_fingerprint({"title": "Something else"})
    result = await dm.async_remove_view(board, view=1, expected_fingerprint=stale)
    assert "changed since it was shown" in result["error"]

    ok = await dm.async_remove_view(
        board, view=1, expected_fingerprint=dm.view_fingerprint(document["views"][1])
    )
    assert ok["status"] == "deleted"
    assert ok["cards_removed"] == 2


async def test_two_views_with_equal_card_counts_are_distinguished(
    board: HomeAssistant,
) -> None:
    """A card COUNT collides, so it cannot be the identity check.

    Approve removing Beta at index 1; Beta is then deleted elsewhere and Gamma
    takes index 1 — with the same two cards. A count check passes and deletes
    Gamma, a page the user never approved.
    """
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    two_cards = [{"type": "light"}, {"type": "gauge"}]
    document = {
        "views": [
            {"title": "Alpha", "cards": list(two_cards)},
            {"title": "Beta", "cards": list(two_cards)},
        ]
    }
    await board.data[LOVELACE_DATA].dashboards[None].async_save(document)

    preview = await _preview_remove_dashboard_view(board, {"view": 1})
    approved = preview["destructive"]["fingerprint"]

    # Beta goes; Gamma arrives at the same index with the same card count.
    document["views"][1] = {"title": "Gamma", "cards": list(two_cards)}
    await board.data[LOVELACE_DATA].dashboards[None].async_save(document)

    result = await dm.async_remove_view(board, view=1, expected_fingerprint=approved)
    assert "changed since it was shown" in result["error"]

    after = await board.data[LOVELACE_DATA].dashboards[None].async_load(False)
    assert [v["title"] for v in after["views"]] == ["Alpha", "Gamma"]


async def test_preview_carries_a_content_fingerprint(board: HomeAssistant) -> None:
    preview = await _preview_remove_dashboard_view(board, {"view": "garage"})
    assert preview["destructive"]["fingerprint"] == dm.view_fingerprint(_document()["views"][1])
    # The count is blast radius on the label, not identity.
    assert "2 cards" in preview["destructive"]["label"]


async def test_removal_skips_non_dict_entries_in_the_views_list(
    board: HomeAssistant,
) -> None:
    """Lovelace storage is free-form, so views may hold a stray non-dict.

    resolve_view indexes the dict-only list; applying that index to the raw
    list removes the wrong element and reports success for a page still there.
    """
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    document = {"views": [None, {"title": "Garage", "cards": []}]}
    await board.data[LOVELACE_DATA].dashboards[None].async_save(document)

    result = await dm.async_remove_view(board, view="Garage")
    assert result["status"] == "deleted"
    assert result["title"] == "Garage"

    after = await board.data[LOVELACE_DATA].dashboards[None].async_load(False)
    assert not [v for v in after["views"] if isinstance(v, dict)]


async def test_removed_card_comes_back_for_restoration(board: HomeAssistant) -> None:
    """The tool promises the removed config is returned so it can be put back —
    a type alone restores none of a card's entities, actions, or styling."""
    executor = _make_executor(board)
    before = await executor.execute("get_dashboard_card", {"view": "living", "card_index": 1})

    result = await executor.execute("remove_dashboard_card", {"view": "living", "card_index": 1})
    assert result["card"] == before["card"]

    # And it round-trips: the returned config restores the card as it was.
    await executor.execute("insert_dashboard_card", {"card": result["card"], "view": "living"})
    listing = await executor.execute("get_dashboard", {"view": "living"})
    assert any(c.get("entity") == "climate.main" for c in listing["view"]["cards"])


# ── YAML dashboards ─────────────────────────────────────────────────────────


async def test_yaml_dashboard_is_readable_but_not_writable(board: HomeAssistant) -> None:
    """The user can see it in their sidebar, so "not found" would read as our bug."""
    from homeassistant.components.lovelace.const import MODE_YAML

    with patch.object(type(board.data["lovelace"].dashboards[None]), "mode", MODE_YAML):
        read = await _make_executor(board).execute("get_dashboard", {})
        assert read["editable"] is False
        assert "YAML" in read["note"]

        write = await _make_executor(board).execute("add_dashboard_view", {"title": "Nope"})
        assert "YAML-mode" in write["error"]


# ── Reordering and grouping ─────────────────────────────────────────────────


async def test_move_card_to_the_top(board: HomeAssistant) -> None:
    """The only way to reorder. Without it "keep the garage door at the top"
    is unachievable — a caller can only append, replace, or remove."""
    executor = _make_executor(board)
    result = await executor.execute(
        "move_dashboard_card", {"view": "living", "from_index": 1, "to_index": 0}
    )
    assert result["status"] == "moved"

    listing = await executor.execute("get_dashboard", {"view": "living"})
    assert [c["type"] for c in listing["view"]["cards"]] == ["thermostat", "light"]


async def test_move_card_refuses_a_stale_fingerprint(board: HomeAssistant) -> None:
    result = await _make_executor(board).execute(
        "move_dashboard_card",
        {"view": "living", "from_index": 0, "to_index": 1, "expected_fingerprint": "deadbeef"},
    )
    assert "changed since it was read" in result["error"]


async def test_group_cards_into_a_row(board: HomeAssistant) -> None:
    """A masonry view has no rows, so side-by-side is a container — no amount
    of reordering achieves it."""
    executor = _make_executor(board)
    result = await executor.execute(
        "group_dashboard_cards",
        {
            "view": "living",
            "card_indices": [0, 1],
            "container": {"type": "grid", "columns": 2, "square": False, "title": "Lounge"},
        },
    )
    assert result["status"] == "grouped"
    assert result["container"] == "grid"

    from homeassistant.components.lovelace.const import LOVELACE_DATA

    document = await board.data[LOVELACE_DATA].dashboards[None].async_load(False)
    cards = document["views"][0]["cards"]
    assert len(cards) == 1
    # The container is the caller's, passed through verbatim; only `cards` is ours.
    assert cards[0] == {
        "type": "grid",
        "columns": 2,
        "square": False,
        "title": "Lounge",
        "cards": [
            {"type": "light", "entity": "light.lamp"},
            {"type": "thermostat", "entity": "climate.main"},
        ],
    }


async def test_group_refuses_a_container_without_a_type(board: HomeAssistant) -> None:
    result = await _make_executor(board).execute(
        "group_dashboard_cards", {"view": "living", "card_indices": [0, 1], "container": {}}
    )
    assert "container must be a card object" in result["error"]


async def test_grouping_moves_the_cards_untouched(board: HomeAssistant) -> None:
    """The grouped cards are the original objects — the caller never rebuilds
    them, which is how a mistyped entity got onto the dashboard."""
    executor = _make_executor(board)
    before = await executor.execute("get_dashboard_card", {"view": "living", "card_index": 1})
    await executor.execute(
        "group_dashboard_cards",
        {"view": "living", "card_indices": [0, 1], "container": {"type": "grid"}},
    )

    from homeassistant.components.lovelace.const import LOVELACE_DATA

    document = await board.data[LOVELACE_DATA].dashboards[None].async_load(False)
    assert document["views"][0]["cards"][0]["cards"][1] == before["card"]


async def test_group_needs_at_least_two_cards(board: HomeAssistant) -> None:
    result = await _make_executor(board).execute(
        "group_dashboard_cards",
        {"view": "living", "card_indices": [0], "container": {"type": "grid"}},
    )
    assert "at least two" in result["error"]


async def test_group_reports_an_out_of_range_index(board: HomeAssistant) -> None:
    result = await _make_executor(board).execute(
        "group_dashboard_cards",
        {"view": "living", "card_indices": [0, 9], "container": {"type": "grid"}},
    )
    assert "out of range" in result["error"]


# ── Entity validation ───────────────────────────────────────────────────────


async def test_a_card_naming_an_unknown_entity_is_refused(board: HomeAssistant) -> None:
    """Lovelace stores whatever it is given and renders "Entity not found" on
    the user's wall panel — nothing else catches it."""
    result = await _make_executor(board).execute(
        "update_dashboard_card",
        {
            "view": "living",
            "card_index": 0,
            "card": {"type": "light", "entity": "light.does_not_exist"},
        },
    )
    assert "do not exist" in result["error"]
    assert "light.does_not_exist" in result["error"]


async def test_validation_walks_nested_cards(board: HomeAssistant) -> None:
    """An entity can hide inside a stack's cards, not just at the top level."""
    result = await _make_executor(board).execute(
        "update_dashboard_card",
        {
            "view": "living",
            "card_index": 0,
            "card": {
                "type": "vertical-stack",
                "cards": [
                    {"type": "light", "entity": "light.lamp"},
                    {"type": "light", "entity": "light.ghost"},
                ],
            },
        },
    )
    assert "light.ghost" in result["error"]


async def test_insert_also_validates_entities(board: HomeAssistant) -> None:
    result = await _make_executor(board).execute(
        "insert_dashboard_card",
        {"card": {"type": "light", "entity": "light.phantom"}, "view": "living"},
    )
    assert "do not exist" in result["error"]


async def test_a_valid_card_still_writes(board: HomeAssistant) -> None:
    """The guard must not block ordinary edits."""
    result = await _make_executor(board).execute(
        "update_dashboard_card",
        {
            "view": "living",
            "card_index": 0,
            "card": {"type": "light", "entity": "light.lamp", "name": "Reading"},
        },
    )
    assert result["status"] == "updated"


# ── Chat presentation ───────────────────────────────────────────────────────


def test_entity_tiles_are_dropped_after_a_dashboard_turn() -> None:
    """The tiles are real HA cards grouped under ### Area headings — a good
    answer to "which lights are on?", and actively misleading right after a
    dashboard edit, where they read as "here is your dashboard"."""
    from custom_components.selora_ai.llm_client.command_policy import (
        strip_entity_tiles_after_dashboard_turn,
    )

    result = strip_entity_tiles_after_dashboard_turn(
        {
            "response": (
                "I moved the garage door card to the top.\n\n"
                "### Garage\n[[entities:cover.garage]]\n\n"
                "### Living Room\n[[entities:scene.a,scene.b]]"
            )
        },
        [{"tool": "update_dashboard_card", "result": {}}],
    )
    assert result["response"] == "I moved the garage door card to the top."
    assert "[[entit" not in result["response"]
    assert "###" not in result["response"]


def test_entity_tiles_survive_an_ordinary_turn() -> None:
    """ "Which lights are on?" is exactly where the tiles earn their place."""
    from custom_components.selora_ai.llm_client.command_policy import (
        strip_entity_tiles_after_dashboard_turn,
    )

    original = {"response": "2 lights are on:\n[[entities:light.a,light.b]]"}
    assert (
        strip_entity_tiles_after_dashboard_turn(
            original, [{"tool": "get_home_snapshot", "result": {}}]
        )["response"]
        == original["response"]
    )


def test_prose_survives_when_a_dashboard_turn_has_no_tiles() -> None:
    from custom_components.selora_ai.llm_client.command_policy import (
        strip_entity_tiles_after_dashboard_turn,
    )

    result = strip_entity_tiles_after_dashboard_turn(
        {"response": "Your dashboard has three views."},
        [{"tool": "get_dashboard", "result": {}}],
    )
    assert result["response"] == "Your dashboard has three views."


def test_a_name_matching_one_view_by_title_and_another_by_path_is_refused() -> None:
    """Checking path first and returning on a single hit made the view TITLED
    'foo' invisible — the reference was ambiguous and resolved silently."""
    document = {
        "views": [{"title": "foo", "cards": []}, {"title": "Garage", "path": "foo", "cards": []}]
    }
    index, error = dm.resolve_view(document, "foo")
    assert index is None
    assert "matches 2 views" in error
    assert "by title" in error and "by path" in error


def test_a_name_matching_one_view_by_both_fields_still_resolves() -> None:
    """Two fields on the SAME view is one target, not an ambiguity."""
    document = {"views": [{"title": "foo", "path": "foo", "cards": []}, {"title": "Other"}]}
    assert dm.resolve_view(document, "foo") == (0, None)


async def test_update_view_refuses_a_stale_fingerprint(board: HomeAssistant) -> None:
    """A rename landing on the wrong page is quieter than a deletion — nothing
    disappears, so nobody looks."""
    result = await _make_executor(board).execute(
        "update_dashboard_view",
        {"view": 0, "title": "Renamed", "expected_fingerprint": "deadbeef"},
    )
    assert "changed since it was read" in result["error"]

    listing = await _make_executor(board).execute("get_dashboard", {})
    assert listing["views"][0]["title"] == "Living"


async def test_update_view_accepts_the_fingerprint_from_get_dashboard(
    board: HomeAssistant,
) -> None:
    """The guard must be usable: the read has to hand out what the write wants."""
    executor = _make_executor(board)
    listing = await executor.execute("get_dashboard", {})
    fingerprint = listing["views"][0]["fingerprint"]
    assert fingerprint

    result = await executor.execute(
        "update_dashboard_view",
        {"view": 0, "title": "Lounge", "expected_fingerprint": fingerprint},
    )
    assert result["status"] == "updated"


async def test_group_refuses_a_stale_view_fingerprint(board: HomeAssistant) -> None:
    """Every card index is relative to the view as it was read."""
    result = await _make_executor(board).execute(
        "group_dashboard_cards",
        {
            "view": "living",
            "card_indices": [0, 1],
            "container": {"type": "grid"},
            "expected_view_fingerprint": "deadbeef",
        },
    )
    assert "no longer mean what they did" in result["error"]


@pytest.mark.parametrize(
    ("from_index", "to_index", "expected"),
    [
        (0, 1, ["b", "a", "c", "d"]),  # forward, not to the end — overshot by one
        (0, 2, ["b", "c", "a", "d"]),
        (0, 3, ["b", "c", "d", "a"]),  # forward, to the end
        (3, 1, ["a", "d", "b", "c"]),  # backward
        (1, 0, ["b", "a", "c", "d"]),
        (2, 2, ["a", "b", "c", "d"]),  # no-op
    ],
)
async def test_move_lands_the_card_at_the_index_asked_for(
    board: HomeAssistant, from_index: int, to_index: int, expected: list[str]
) -> None:
    """``pop(from); insert(to)`` semantics. Adding one for a forward move landed
    the card AFTER the destination, so 0 -> 1 in [a, b, c] gave [b, c, a]."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    await (
        board.data[LOVELACE_DATA]
        .dashboards[None]
        .async_save(
            {
                "views": [
                    {"title": "V", "cards": [{"type": "markdown", "content": c} for c in "abcd"]}
                ]
            }
        )
    )

    result = await _make_executor(board).execute(
        "move_dashboard_card", {"view": 0, "from_index": from_index, "to_index": to_index}
    )
    assert result["status"] == "moved"

    document = await board.data[LOVELACE_DATA].dashboards[None].async_load(False)
    assert [c["content"] for c in document["views"][0]["cards"]] == expected


async def test_bare_entities_list_is_validated(board: HomeAssistant) -> None:
    """``entities: ["light.one"]`` is the commonest spelling of an entities card.
    A list passes its parent key down, so these arrive keyed ``entities`` and
    went unchecked — the typo was saved and rendered 'Entity not found'."""
    result = await _make_executor(board).execute(
        "insert_dashboard_card",
        {"card": {"type": "entities", "entities": ["light.lamp", "light.no_such_lamp"]}},
    )
    assert "light.no_such_lamp" in result["error"]
    assert "light.lamp," not in result["error"]


async def test_a_label_row_in_an_entities_list_is_not_an_entity(board: HomeAssistant) -> None:
    """A row there may be a label or a divider. Refusing those blocks a card the
    home renders perfectly well; a typo we care about always has a dot."""
    result = await _make_executor(board).execute(
        "insert_dashboard_card",
        {"card": {"type": "entities", "entities": ["Upstairs", {"entity": "light.lamp"}]}},
    )
    assert "error" not in result


def test_remove_view_exposes_the_fingerprint_to_mcp_clients() -> None:
    """The MCP handler deletes immediately and reads ``expected_fingerprint``, so
    a schema that hides it leaves an MCP delete unpinnable."""
    from custom_components.selora_ai.mcp_server import _TOOL_DEFINITIONS

    tool = next(t for t in _TOOL_DEFINITIONS if t.name == "selora_remove_dashboard_view")
    assert "expected_fingerprint" in tool.inputSchema["properties"]


async def test_mcp_remove_view_refuses_a_stale_fingerprint(board: HomeAssistant) -> None:
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    from custom_components.selora_ai.mcp_server import _tool_remove_dashboard_view

    result = await _tool_remove_dashboard_view(
        board, {"view": 0, "expected_fingerprint": "deadbeef"}
    )
    assert "error" in result

    document = await board.data[LOVELACE_DATA].dashboards[None].async_load(False)
    assert len(document["views"]) == 2


async def test_chat_remove_view_preview_refuses_a_stale_fingerprint(board: HomeAssistant) -> None:
    """Caught before the user taps confirm on a card naming the wrong page."""
    result = await _make_executor(board).execute(
        "remove_dashboard_view", {"view": 0, "expected_fingerprint": "deadbeef"}
    )
    assert "changed since it was read" in result["error"]
    assert "requires_approval" not in result


async def test_an_oversized_card_is_refused_not_handed_out_truncated(
    board: HomeAssistant,
) -> None:
    """`_truncate_result` drops items from the longest list while the fingerprint
    still describes the WHOLE card, so writing the shortened copy back passes the
    identity check and silently deletes every trimmed row."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    rows = [{"entity": "light.lamp", "name": f"Row {i} {'x' * 60}"} for i in range(400)]
    await (
        board.data[LOVELACE_DATA]
        .dashboards[None]
        .async_save({"views": [{"title": "V", "cards": [{"type": "entities", "entities": rows}]}]})
    )

    result = await _make_executor(board).execute("get_dashboard_card", {"view": 0, "card_index": 0})
    assert "too large to fetch intact" in result["error"]
    assert "fingerprint" not in result
    assert "card" not in result


async def test_a_card_that_fits_still_comes_back_whole(board: HomeAssistant) -> None:
    """The ceiling must not swallow ordinary cards."""
    result = await _make_executor(board).execute("get_dashboard_card", {"view": 0, "card_index": 0})
    assert result["card"] == {"type": "light", "entity": "light.lamp"}
    assert result["fingerprint"]


async def test_add_view_reports_an_index_the_view_tools_accept(board: HomeAssistant) -> None:
    """A stray non-dict in the stored list shifts the raw index off the filtered
    one every reader here uses, so the reported index came back out of range."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    document = _document()
    document["views"].insert(0, "not a view")
    await board.data[LOVELACE_DATA].dashboards[None].async_save(document)

    created = await _make_executor(board).execute("add_dashboard_view", {"title": "Attic"})
    index = created["view_index"]

    read = await _make_executor(board).execute("get_dashboard", {"view": index})
    assert read["view"]["title"] == "Attic"


async def test_recipe_reinstall_replaces_a_card_the_user_grouped(board: HomeAssistant) -> None:
    """Grouping hides a tagged card from the top-level scans, so the re-install
    added a second copy instead of replacing the first."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    from custom_components.selora_ai.recipes.dashboard import async_place_card

    await async_place_card(board, card={"type": "light", "entity": "light.lamp"}, tag="my-recipe")
    await _make_executor(board).execute(
        "group_dashboard_cards", {"view": 0, "card_indices": [0, 2], "container": {"type": "grid"}}
    )

    await async_place_card(board, card={"type": "light", "entity": "light.lamp"}, tag="my-recipe")

    document = await board.data[LOVELACE_DATA].dashboards[None].async_load(False)
    assert json.dumps(document).count('"selora_recipe": "my-recipe"') == 1


async def test_recipe_uninstall_removes_a_card_the_user_grouped(board: HomeAssistant) -> None:
    """Otherwise the card is left on the dashboard with nothing that knows it
    belongs to a recipe."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    from custom_components.selora_ai.recipes.dashboard import (
        async_place_card,
        async_remove_cards,
    )

    await async_place_card(board, card={"type": "light", "entity": "light.lamp"}, tag="my-recipe")
    await _make_executor(board).execute(
        "group_dashboard_cards", {"view": 0, "card_indices": [0, 2], "container": {"type": "grid"}}
    )

    assert await async_remove_cards(board, "my-recipe") == 1

    document = await board.data[LOVELACE_DATA].dashboards[None].async_load(False)
    assert "selora_recipe" not in json.dumps(document)


async def test_a_container_emptied_by_uninstall_goes_with_it(board: HomeAssistant) -> None:
    """An empty grid renders as a labelled box holding nothing — that reads as
    breakage, not as the tidy removal uninstall promised."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    from custom_components.selora_ai.recipes.dashboard import async_remove_cards

    await (
        board.data[LOVELACE_DATA]
        .dashboards[None]
        .async_save(
            {
                "views": [
                    {
                        "title": "V",
                        "cards": [
                            {
                                "type": "grid",
                                "columns": 2,
                                "cards": [
                                    {
                                        "type": "light",
                                        "entity": "light.lamp",
                                        "selora_recipe": "r1",
                                    },
                                    {
                                        "type": "light",
                                        "entity": "light.other",
                                        "selora_recipe": "r1",
                                    },
                                ],
                            },
                            {"type": "markdown", "content": "keep me"},
                        ],
                    }
                ]
            }
        )
    )

    assert await async_remove_cards(board, "r1") == 2

    document = await board.data[LOVELACE_DATA].dashboards[None].async_load(False)
    assert document["views"][0]["cards"] == [{"type": "markdown", "content": "keep me"}]


async def test_a_container_that_keeps_a_card_survives_uninstall(board: HomeAssistant) -> None:
    """Only a container emptied BY the purge goes; one still holding a card the
    user put there stays, with the recipe's card gone from inside it."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    from custom_components.selora_ai.recipes.dashboard import async_remove_cards

    await (
        board.data[LOVELACE_DATA]
        .dashboards[None]
        .async_save(
            {
                "views": [
                    {
                        "title": "V",
                        "cards": [
                            {
                                "type": "horizontal-stack",
                                "cards": [
                                    {
                                        "type": "light",
                                        "entity": "light.lamp",
                                        "selora_recipe": "r1",
                                    },
                                    {"type": "light", "entity": "light.other"},
                                ],
                            }
                        ],
                    }
                ]
            }
        )
    )

    assert await async_remove_cards(board, "r1") == 1

    document = await board.data[LOVELACE_DATA].dashboards[None].async_load(False)
    assert document["views"][0]["cards"] == [
        {"type": "horizontal-stack", "cards": [{"type": "light", "entity": "light.other"}]}
    ]


def test_the_dashboard_lock_is_shared_with_the_recipe_writers() -> None:
    """Both write the whole document; two locks means the later save silently
    discards the other's work."""
    from custom_components.selora_ai import dashboard_manager, helpers
    from custom_components.selora_ai.recipes import dashboard as recipes_dashboard

    assert dashboard_manager.DASHBOARD_LOCK is helpers.DASHBOARD_LOCK
    assert recipes_dashboard.DASHBOARD_LOCK is helpers.DASHBOARD_LOCK


async def test_a_rejected_edit_does_not_reach_the_cached_document(
    board: HomeAssistant,
) -> None:
    """async_load hands back HA's cached config object and dict() copies only
    the root, so a writer that mutates before it validates left the rejected
    change sitting in the cache for the next save to persist."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    dashboard = board.data[LOVELACE_DATA].dashboards[None]

    # Renames the view, THEN hits the duplicate-path check and bails.
    result = await _make_executor(board).execute(
        "update_dashboard_view", {"view": 0, "title": "Renamed", "path": "garage"}
    )
    assert "already uses the path" in result["error"]

    cached = await dashboard.async_load(False)
    assert cached["views"][0]["title"] == "Living"


async def test_a_read_does_not_hand_out_a_live_reference(board: HomeAssistant) -> None:
    """The returned card would otherwise be the cached object, so anything that
    trims the tool result on its way out shortens HA's own config."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    result = await _make_executor(board).execute("get_dashboard_card", {"view": 0, "card_index": 0})
    result["card"]["entity"] = "light.mutated"

    cached = await board.data[LOVELACE_DATA].dashboards[None].async_load(False)
    assert cached["views"][0]["cards"][0]["entity"] == "light.lamp"


async def test_list_dashboards_includes_yaml_boards_marked_read_only(
    board: HomeAssistant,
) -> None:
    """get_dashboard reads YAML boards fine, so omitting them left the user able
    to see a dashboard in their sidebar that Selora insisted was not there."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    yaml_board = MagicMock()
    yaml_board.mode = "yaml"
    yaml_board.config = {"title": "Wall Panel"}
    board.data[LOVELACE_DATA].dashboards["wall-panel"] = yaml_board

    result = await _make_executor(board).execute("list_dashboards", {})
    by_path = {d["url_path"]: d for d in result["dashboards"]}

    assert by_path["wall-panel"]["title"] == "Wall Panel"
    assert by_path["wall-panel"]["editable"] is False
    assert by_path[None]["editable"] is True


async def test_a_non_admin_can_discover_dashboards_it_may_read(board: HomeAssistant) -> None:
    """The read tools are non-admin; gating the only discovery path on admin left
    a non-admin caller able to read the default dashboard and no other."""
    result = await _make_executor(board, is_admin=False).execute("list_dashboards", {})
    assert "error" not in result
    assert result["dashboards"]


async def test_an_oversized_malformed_card_reports_its_size(board: HomeAssistant) -> None:
    """Lovelace storage is free-form and the rest of this module handles a
    non-dict card deliberately; the size error assumed a mapping and turned one
    into an AttributeError surfacing as 'Tool execution failed'."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    await (
        board.data[LOVELACE_DATA]
        .dashboards[None]
        .async_save({"views": [{"title": "V", "cards": [["x" * 200] * 200]}]})
    )

    result = await _make_executor(board).execute("get_dashboard_card", {"view": 0, "card_index": 0})
    assert "too large to fetch intact" in result["error"]


@pytest.mark.parametrize(
    ("mcp_name", "chat_name"),
    [
        ("selora_list_dashboards", "list_dashboards"),
        ("selora_insert_dashboard_card", "insert_dashboard_card"),
    ],
)
def test_mcp_exposes_the_tools_its_own_dashboard_workflow_needs(
    mcp_name: str, chat_name: str
) -> None:
    """The other MCP dashboard tools tell clients to call these two: without
    them a client cannot discover a non-default target, and a view made with
    selora_add_dashboard_view can never be filled."""
    from custom_components.selora_ai import mcp_server

    assert any(t.name == mcp_name for t in mcp_server._TOOL_DEFINITIONS)
    assert mcp_name in mcp_server._get_tool_handlers()
    assert mcp_server._DERIVED_MCP_TOOLS[mcp_name] == chat_name


def test_mcp_dashboard_access_matches_the_chat_definitions() -> None:
    """A read tool in the admin set is unreachable for a read-only credential;
    a write tool missing from it is reachable by one."""
    from custom_components.selora_ai import mcp_server
    from custom_components.selora_ai.tool_registry import TOOL_MAP

    for mcp_name, chat_name in mcp_server._DERIVED_MCP_TOOLS.items():
        if "dashboard" not in chat_name:
            continue
        if TOOL_MAP[chat_name].requires_admin:
            assert mcp_name in mcp_server._ADMIN_TOOLS, mcp_name
        else:
            assert mcp_name in mcp_server._READ_ONLY_TOOLS, mcp_name


async def test_mcp_can_fill_a_view_it_just_created(board: HomeAssistant) -> None:
    """The end-to-end gap: every other MCP write tool edits a card already there."""
    from custom_components.selora_ai.mcp_server import (
        _tool_add_dashboard_view,
        _tool_insert_dashboard_card,
        _tool_list_dashboards,
    )

    assert any(
        d["url_path"] is None for d in (await _tool_list_dashboards(board, {}))["dashboards"]
    )

    created = await _tool_add_dashboard_view(board, {"title": "Attic"})
    result = await _tool_insert_dashboard_card(
        board,
        {"view": created["view_index"], "card": {"type": "light", "entity": "light.lamp"}},
    )
    assert result["ok"] is True

    read = await _make_executor(board).execute("get_dashboard", {"view": created["view_index"]})
    assert read["view"]["card_count"] == 1


async def test_mcp_insert_validates_exactly_as_chat_does(board: HomeAssistant) -> None:
    """Both surfaces share one body, so an unknown entity is refused on each."""
    from custom_components.selora_ai.mcp_server import _tool_insert_dashboard_card

    args = {"card": {"type": "light", "entity": "light.no_such_lamp"}}
    mcp_result = await _tool_insert_dashboard_card(board, args)
    chat_result = await _make_executor(board).execute("insert_dashboard_card", args)
    assert mcp_result == chat_result
    assert "light.no_such_lamp" in mcp_result["error"]


@pytest.fixture
async def autogen(hass: HomeAssistant) -> HomeAssistant:
    """Lovelace up with the default dashboard still auto-generated."""
    assert await async_setup_component(hass, "lovelace", {"lovelace": {"mode": "storage"}})
    await hass.async_block_till_done()
    hass.states.async_set("light.lamp", "off")
    return hass


async def test_an_auto_generated_dashboard_is_not_reported_as_empty(
    autogen: HomeAssistant,
) -> None:
    """async_load raises ConfigNotFound while HA is generating the dashboard, and
    the user is looking at a full Overview — reading that as {} reported zero
    views for a page covered in cards."""
    result = await _make_executor(autogen).execute("get_dashboard", {})
    assert "Take control" in result["error"]
    assert result.get("view_count") != 0 or "views" not in result


async def test_adding_a_view_cannot_replace_the_generated_overview(
    autogen: HomeAssistant,
) -> None:
    """The serious case: saving a document holding only the new view replaces
    everything the user could see with one page."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    result = await _make_executor(autogen).execute("add_dashboard_view", {"title": "Attic"})
    assert "Take control" in result["error"]

    # Still generated — nothing was written.
    info = await autogen.data[LOVELACE_DATA].dashboards[None].async_get_info()
    assert info["mode"] == "auto-gen"


async def test_every_write_refuses_while_the_dashboard_is_generated(
    autogen: HomeAssistant,
) -> None:
    """Guarded in _load_config so a tool added later inherits it."""
    executor = _make_executor(autogen)
    for tool, args in (
        ("update_dashboard_view", {"view": 0, "title": "x"}),
        ("remove_dashboard_view", {"view": 0}),
        ("update_dashboard_card", {"view": 0, "card_index": 0, "card": {"type": "light"}}),
        ("remove_dashboard_card", {"view": 0, "card_index": 0}),
        ("move_dashboard_card", {"view": 0, "from_index": 0, "to_index": 1}),
        (
            "group_dashboard_cards",
            {"view": 0, "card_indices": [0, 1], "container": {"type": "grid"}},
        ),
        ("get_dashboard_card", {"view": 0, "card_index": 0}),
    ):
        assert "Take control" in (await executor.execute(tool, args))["error"], tool


async def test_an_unqualified_target_resolves_the_way_ha_does(board: HomeAssistant) -> None:
    """HA is migrating the default Overview onto a "lovelace" entry and leaving
    dashboards[None] behind as an empty placeholder it registers no panel for,
    so an unqualified read went to a dashboard the user cannot see."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    real = board.data[LOVELACE_DATA].dashboards.pop(None)
    board.data[LOVELACE_DATA].dashboards["lovelace"] = real
    placeholder = MagicMock()
    placeholder.mode = "storage"
    placeholder.config = None
    board.data[LOVELACE_DATA].dashboards[None] = placeholder

    for args in ({}, {"dashboard_target": "lovelace"}, {"dashboard_target": ""}):
        result = await _make_executor(board).execute("get_dashboard", args)
        assert result["view_count"] == 2, args


async def test_the_leftover_placeholder_is_not_offered_as_a_dashboard(
    board: HomeAssistant,
) -> None:
    """HA registers no panel for it, so listing it offers a dashboard the user
    cannot find anywhere."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    real = board.data[LOVELACE_DATA].dashboards.pop(None)
    board.data[LOVELACE_DATA].dashboards["lovelace"] = real
    placeholder = MagicMock()
    placeholder.mode = "storage"
    placeholder.config = None
    board.data[LOVELACE_DATA].dashboards[None] = placeholder

    paths = [
        d["url_path"]
        for d in (await _make_executor(board).execute("list_dashboards", {}))["dashboards"]
    ]
    assert paths == ["lovelace"]


async def test_card_insertion_follows_the_same_default(board: HomeAssistant) -> None:
    """recipes._get_storage_dashboard looked the target up raw, so an insert
    landed on the invisible placeholder."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    real = board.data[LOVELACE_DATA].dashboards.pop(None)
    board.data[LOVELACE_DATA].dashboards["lovelace"] = real
    placeholder = MagicMock()
    placeholder.mode = "storage"
    placeholder.config = None
    board.data[LOVELACE_DATA].dashboards[None] = placeholder

    result = await _make_executor(board).execute(
        "insert_dashboard_card", {"card": {"type": "light", "entity": "light.lamp"}}
    )
    assert result["ok"] is True
    placeholder.async_save.assert_not_called()

    document = await real.async_load(False)
    assert len(document["views"][0]["cards"]) == 3


def _admin_only_board(hass: HomeAssistant) -> MagicMock:
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    secret = MagicMock()
    secret.mode = "storage"
    secret.config = {"title": "Cameras", "require_admin": True, "url_path": "cameras"}
    secret.async_load = AsyncMock(
        return_value={"views": [{"title": "Cams", "cards": [{"type": "picture-entity"}]}]}
    )
    hass.data[LOVELACE_DATA].dashboards["cameras"] = secret
    return secret


async def test_a_non_admin_cannot_read_an_admin_only_dashboard(board: HomeAssistant) -> None:
    """HA registers no panel for a require_admin dashboard for a non-admin, so
    it is invisible to them in the UI — the read tools are non-admin and handed
    back its full card configuration."""
    _admin_only_board(board)

    result = await _make_executor(board, is_admin=False).execute(
        "get_dashboard", {"dashboard_target": "cameras", "view": 0}
    )
    assert "No dashboard" in result["error"]
    assert "cameras" not in result["error"].split(".")[1]  # not named in "Available:"


async def test_an_admin_still_reads_an_admin_only_dashboard(board: HomeAssistant) -> None:
    _admin_only_board(board)

    result = await _make_executor(board, is_admin=True).execute(
        "get_dashboard", {"dashboard_target": "cameras"}
    )
    assert result["view_count"] == 1


async def test_an_admin_only_dashboard_is_not_listed_to_a_non_admin(
    board: HomeAssistant,
) -> None:
    """Listing it is the same disclosure as reading it — it confirms the
    dashboard exists, which is the bit HA is withholding."""
    _admin_only_board(board)

    hidden = await _make_executor(board, is_admin=False).execute("list_dashboards", {})
    shown = await _make_executor(board, is_admin=True).execute("list_dashboards", {})

    assert "cameras" not in [d["url_path"] for d in hidden["dashboards"]]
    assert "cameras" in [d["url_path"] for d in shown["dashboards"]]


async def test_an_unscoped_call_is_treated_as_non_admin(board: HomeAssistant) -> None:
    """The ContextVar defaults to False, so a caller that forgets to open the
    scope gets less access, never more."""
    from custom_components.selora_ai.dashboard_manager import async_get_dashboard

    _admin_only_board(board)
    result = await async_get_dashboard(board, "cameras", None)
    assert "No dashboard" in result["error"]


async def test_the_mcp_surface_carries_caller_identity(board: HomeAssistant) -> None:
    from custom_components.selora_ai.helpers import caller_scope
    from custom_components.selora_ai.mcp_server import _tool_get_dashboard

    _admin_only_board(board)
    with caller_scope(False):
        assert (
            "No dashboard"
            in (await _tool_get_dashboard(board, {"dashboard_target": "cameras"}))["error"]
        )
    with caller_scope(True):
        assert (await _tool_get_dashboard(board, {"dashboard_target": "cameras"}))[
            "view_count"
        ] == 1


@pytest.mark.parametrize(
    "value",
    [
        "[[[ return states['light.lamp'].state === 'on' ? 'light.lamp' : 'light.other' ]]]",
        "{{ states('input_text.target') }}",
        "",
    ],
)
async def test_a_templated_entity_value_is_not_treated_as_an_id(
    board: HomeAssistant, value: str
) -> None:
    """A custom card may hold a template where an id goes; the state lookup finds
    no such entity and refused an otherwise valid card."""
    result = await _make_executor(board).execute(
        "insert_dashboard_card", {"card": {"type": "custom:button-card", "entity": value}}
    )
    assert "error" not in result


async def test_a_real_typo_is_still_refused(board: HomeAssistant) -> None:
    """The shape check must not turn the validation off."""
    result = await _make_executor(board).execute(
        "insert_dashboard_card", {"card": {"type": "light", "entity": "light.no_such_lamp"}}
    )
    assert "light.no_such_lamp" in result["error"]


async def test_removing_a_view_from_an_admin_only_dashboard_needs_the_scope(
    board: HomeAssistant,
) -> None:
    """Why the confirmation has to re-establish it: the manager's per-object check
    hides the dashboard from an unscoped caller, so the write cannot find it."""
    from custom_components.selora_ai.dashboard_manager import async_remove_view
    from custom_components.selora_ai.helpers import caller_scope

    secret = _admin_only_board(board)
    secret.async_save = AsyncMock()

    unscoped = await async_remove_view(board, target="cameras", view="0")
    assert "No dashboard" in unscoped["error"]
    secret.async_save.assert_not_called()

    with caller_scope(True):
        scoped = await async_remove_view(board, target="cameras", view="0")
    assert scoped["status"] == "deleted"


async def test_the_confirmation_leg_runs_as_the_admin_who_confirmed(
    hass: HomeAssistant,
) -> None:
    """The second leg runs long after the ToolExecutor scope that built the card
    has ended, so CALLER_IS_ADMIN had reverted to its deny-by-default False and
    every confirmed removal on a require_admin dashboard failed as
    "No dashboard"."""
    from custom_components.selora_ai.const import DOMAIN
    from custom_components.selora_ai.helpers import CALLER_IS_ADMIN
    from custom_components.selora_ai.websocket import tokens

    seen: list[bool] = []

    async def _probe(*_args: Any, **_kwargs: Any) -> None:
        seen.append(CALLER_IS_ADMIN.get())

    hass.data.setdefault(DOMAIN, {})["_approval_store"] = MagicMock()

    connection = MagicMock()
    connection.user.is_admin = True
    msg = {
        "id": 1,
        "session_id": "s1",
        "proposal_id": "p1",
        "scope": "allow_once",
    }

    # websocket_api.async_response wraps the handler in a sync scheduler; the
    # original coroutine is what this test is about.
    handler = inspect.unwrap(tokens._handle_websocket_resolve_approval)
    with patch.object(tokens, "_resolve_approval", _probe):
        await handler(hass, connection, msg)

    assert seen == [True]
    # And the scope does not leak past the confirmation.
    assert CALLER_IS_ADMIN.get() is False


async def test_reinstall_refreshes_a_grouped_card_where_it_sits(board: HomeAssistant) -> None:
    """Purging then appending dedupes correctly and still undoes the grouping,
    moving the card back to the end of the view with nothing to say why."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    from custom_components.selora_ai.recipes.dashboard import async_place_card

    await async_place_card(board, card={"type": "light", "entity": "light.lamp"}, tag="r1")
    await _make_executor(board).execute(
        "group_dashboard_cards", {"view": 0, "card_indices": [0, 2], "container": {"type": "grid"}}
    )

    await async_place_card(
        board, card={"type": "light", "entity": "light.other", "name": "v2"}, tag="r1"
    )

    document = await board.data[LOVELACE_DATA].dashboards[None].async_load(False)
    cards = document["views"][0]["cards"]
    container = next(c for c in cards if c.get("type") == "grid")

    # Refreshed inside the container the user built, not appended after it.
    inner = [c for c in container["cards"] if c.get("selora_recipe") == "r1"]
    assert len(inner) == 1
    assert inner[0]["entity"] == "light.other"
    assert inner[0]["name"] == "v2"
    assert not [c for c in cards if c.get("selora_recipe") == "r1"]


async def test_reinstall_still_leaves_exactly_one_card(board: HomeAssistant) -> None:
    """In-place replacement must not reintroduce the duplication it replaced."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    from custom_components.selora_ai.recipes.dashboard import async_place_card

    for _ in range(3):
        await async_place_card(board, card={"type": "light", "entity": "light.lamp"}, tag="r1")

    document = await board.data[LOVELACE_DATA].dashboards[None].async_load(False)
    assert json.dumps(document).count('"selora_recipe": "r1"') == 1


async def test_reinstall_drops_a_stray_duplicate_it_finds(board: HomeAssistant) -> None:
    """The first tagged card is refreshed; any others go."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    from custom_components.selora_ai.recipes.dashboard import async_place_card

    await (
        board.data[LOVELACE_DATA]
        .dashboards[None]
        .async_save(
            {
                "views": [
                    {
                        "title": "V",
                        "cards": [
                            {
                                "type": "grid",
                                "cards": [
                                    {"type": "light", "entity": "light.lamp", "selora_recipe": "r1"}
                                ],
                            },
                            {"type": "light", "entity": "light.lamp", "selora_recipe": "r1"},
                        ],
                    }
                ]
            }
        )
    )

    await async_place_card(board, card={"type": "light", "entity": "light.other"}, tag="r1")

    document = await board.data[LOVELACE_DATA].dashboards[None].async_load(False)
    cards = document["views"][0]["cards"]
    assert json.dumps(document).count('"selora_recipe": "r1"') == 1
    assert cards[0]["cards"][0]["entity"] == "light.other"
    assert len(cards) == 1


async def test_a_first_install_still_appends(board: HomeAssistant) -> None:
    """Only a genuinely new card is appended."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    from custom_components.selora_ai.recipes.dashboard import async_place_card

    result = await async_place_card(
        board, card={"type": "light", "entity": "light.lamp"}, tag="brand-new"
    )
    assert result.ok is True

    document = await board.data[LOVELACE_DATA].dashboards[None].async_load(False)
    assert document["views"][0]["cards"][-1]["selora_recipe"] == "brand-new"


async def test_insert_refuses_while_the_dashboard_is_generated(autogen: HomeAssistant) -> None:
    """async_place_card seeds its own {"views": [{"title": "Home"}]} on
    ConfigNotFound, so insert was the one write still able to replace a
    generated Overview — with a single card — while the rest refused."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    result = await _make_executor(autogen).execute(
        "insert_dashboard_card", {"card": {"type": "light", "entity": "light.lamp"}}
    )
    # No preflight here — async_place_card refuses after its own load, which is
    # the only place the answer is knowable.
    assert result["ok"] is False
    assert result["reason"] == "auto_generated"
    assert "Take control" in result["message"]

    info = await autogen.data[LOVELACE_DATA].dashboards[None].async_get_info()
    assert info["mode"] == "auto-gen"


async def test_get_dashboard_reports_the_title_from_metadata(board: HomeAssistant) -> None:
    """The title lives in the dashboard's metadata, not in the document
    async_load returns — which normally holds only `views`."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    named = MagicMock()
    named.mode = "storage"
    named.config = {"title": "Upstairs", "url_path": "upstairs"}
    named.async_load = AsyncMock(return_value={"views": [{"title": "V", "cards": []}]})
    board.data[LOVELACE_DATA].dashboards["upstairs"] = named

    result = await _make_executor(board).execute("get_dashboard", {"dashboard_target": "upstairs"})
    assert result["title"] == "Upstairs"

    listed = await _make_executor(board).execute("list_dashboards", {})
    assert {d["url_path"]: d["title"] for d in listed["dashboards"]}["upstairs"] == "Upstairs"


async def test_a_recipe_install_does_not_take_over_a_generated_overview(
    autogen: HomeAssistant,
) -> None:
    """The recipe pipeline calls async_place_card directly, so the tool
    wrapper's preflight never runs for it."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    from custom_components.selora_ai.recipes.dashboard import async_place_card

    result = await async_place_card(
        autogen, card={"type": "light", "entity": "light.lamp"}, tag="r1"
    )
    assert result.ok is False
    assert result.reason == "auto_generated"
    assert "Take control" in result.message

    info = await autogen.data[LOVELACE_DATA].dashboards[None].async_get_info()
    assert info["mode"] == "auto-gen"


async def test_a_genuinely_blank_dashboard_is_still_seeded(hass: HomeAssistant) -> None:
    """ConfigNotFound is ambiguous — seeding is right when nothing is generated."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    from custom_components.selora_ai.recipes.dashboard import async_place_card

    assert await async_setup_component(hass, "lovelace", {"lovelace": {"mode": "storage"}})
    await hass.async_block_till_done()
    hass.states.async_set("light.lamp", "off")

    blank = hass.data[LOVELACE_DATA].dashboards[None]
    blank.async_get_info = AsyncMock(return_value={"mode": "storage"})

    result = await async_place_card(hass, card={"type": "light", "entity": "light.lamp"}, tag="r1")
    assert result.ok is True

    document = await blank.async_load(False)
    assert document["views"][0]["cards"][0]["selora_recipe"] == "r1"


async def test_move_refuses_a_stale_view_fingerprint(board: HomeAssistant) -> None:
    """The card fingerprint pins the SOURCE only — another edit can leave it at
    from_index while to_index now names somewhere else."""
    result = await _make_executor(board).execute(
        "move_dashboard_card",
        {
            "view": 0,
            "from_index": 0,
            "to_index": 1,
            "expected_view_fingerprint": "deadbeef",
        },
    )
    assert "no longer means what it did" in result["error"]


async def test_move_accepts_the_fingerprint_from_get_dashboard(board: HomeAssistant) -> None:
    executor = _make_executor(board)
    listing = await executor.execute("get_dashboard", {})
    result = await executor.execute(
        "move_dashboard_card",
        {
            "view": 0,
            "from_index": 0,
            "to_index": 1,
            "expected_view_fingerprint": listing["views"][0]["fingerprint"],
        },
    )
    assert result["status"] == "moved"


def _sections_view(cards_per_section: list[list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "views": [
            {
                "title": "V",
                "type": "sections",
                "sections": [{"type": "grid", "cards": c} for c in cards_per_section],
            }
        ]
    }


async def test_moving_the_only_card_keeps_it_in_its_section(board: HomeAssistant) -> None:
    """_insert_target_cards always targets the FIRST section, so a lone card in
    a later one was relocated by a move that asked for no such thing."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    await (
        board.data[LOVELACE_DATA]
        .dashboards[None]
        .async_save(_sections_view([[], [{"type": "light", "entity": "light.lamp"}]]))
    )

    result = await _make_executor(board).execute(
        "move_dashboard_card", {"view": 0, "from_index": 0, "to_index": 0}
    )
    assert result["status"] == "moved"

    sections = (await board.data[LOVELACE_DATA].dashboards[None].async_load(False))["views"][0][
        "sections"
    ]
    assert sections[0]["cards"] == []
    assert sections[1]["cards"] == [{"type": "light", "entity": "light.lamp"}]


async def test_grouping_every_card_keeps_the_group_in_its_section(
    board: HomeAssistant,
) -> None:
    """The group is meant to stay at the first grouped card's position."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    await (
        board.data[LOVELACE_DATA]
        .dashboards[None]
        .async_save(
            _sections_view(
                [
                    [],
                    [
                        {"type": "light", "entity": "light.lamp"},
                        {"type": "light", "entity": "light.other"},
                    ],
                ]
            )
        )
    )

    result = await _make_executor(board).execute(
        "group_dashboard_cards",
        {"view": 0, "card_indices": [0, 1], "container": {"type": "grid"}},
    )
    assert result["status"] == "grouped"

    sections = (await board.data[LOVELACE_DATA].dashboards[None].async_load(False))["views"][0][
        "sections"
    ]
    assert sections[0]["cards"] == []
    assert sections[1]["cards"][0]["type"] == "grid"
    assert len(sections[1]["cards"][0]["cards"]) == 2


async def test_insertion_uses_the_indices_the_reads_advertise(board: HomeAssistant) -> None:
    """get_dashboard reports indices over the dict-only views while insertion
    indexed the raw stored list, so a stray non-dict sent an index the caller
    had just been handed to a different page."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    document = _document()
    document["views"].insert(0, "not a view")
    await board.data[LOVELACE_DATA].dashboards[None].async_save(document)

    listing = await _make_executor(board).execute("get_dashboard", {})
    garage = next(v["index"] for v in listing["views"] if v["title"] == "Garage")

    result = await _make_executor(board).execute(
        "insert_dashboard_card",
        {"view": str(garage), "card": {"type": "light", "entity": "light.lamp"}},
    )
    assert result["ok"] is True

    read = await _make_executor(board).execute("get_dashboard", {"view": garage})
    assert read["view"]["title"] == "Garage"
    assert read["view"]["card_count"] == 3


@pytest.mark.parametrize(
    "mcp_name",
    [
        "selora_remove_dashboard_view",
        "selora_delete_area",
        "selora_delete_label",
        "selora_delete_script",
    ],
)
def test_mcp_says_a_confirmation_gated_tool_runs_immediately(mcp_name: str) -> None:
    """The shared description promises a confirmation card, but MCP has none —
    an agent acting on that contract loses the view before anyone is asked."""
    from custom_components.selora_ai import mcp_server

    tool = next(t for t in mcp_server._TOOL_DEFINITIONS if t.name == mcp_name)
    assert "runs IMMEDIATELY" in tool.description


def test_a_tool_with_no_card_gets_no_such_warning() -> None:
    """Derived from the preview allowlists, so it does not leak onto reads."""
    from custom_components.selora_ai import mcp_server

    tool = next(t for t in mcp_server._TOOL_DEFINITIONS if t.name == "selora_get_dashboard")
    assert "runs IMMEDIATELY" not in tool.description


@pytest.mark.parametrize("intent", ["command", "delayed_command", "answer"])
def test_a_dashboard_turn_drops_its_tiles_whatever_the_intent(intent: str) -> None:
    """The intent-specific early returns carried the markers straight out."""
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    result = synthesize_approval_from_tool_log(
        {"intent": intent, "response": "Done.\n[[entities:light.lamp,light.other]]"},
        [{"tool": "update_dashboard_card", "arguments": {}, "result": {"status": "updated"}}],
        None,
    )
    assert "[[entities:" not in result["response"]


@pytest.mark.parametrize("intent", ["command", "delayed_command"])
def test_a_non_dashboard_turn_keeps_its_tiles(intent: str) -> None:
    """Only a dashboard turn is misleading; 'which lights are on' is not."""
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    result = synthesize_approval_from_tool_log(
        {"intent": intent, "response": "2 on:\n[[entities:light.lamp,light.other]]"},
        [{"tool": "get_home_snapshot", "arguments": {}, "result": {}}],
        None,
    )
    assert "[[entities:" in result["response"]


async def test_a_generated_dashboard_is_not_listed_as_editable(autogen: HomeAssistant) -> None:
    """A fresh install's Overview is storage-mode and still generated, so a
    mode-only answer invited a workflow every write then refused."""
    result = await _make_executor(autogen).execute("list_dashboards", {})
    assert [d["editable"] for d in result["dashboards"]] == [False]


async def test_a_dashboard_taken_control_of_is_editable(board: HomeAssistant) -> None:
    """The guard must not make every storage dashboard read-only."""
    result = await _make_executor(board).execute("list_dashboards", {})
    assert [d["editable"] for d in result["dashboards"]] == [True]


async def test_a_strategy_dashboard_refuses_edits(board: HomeAssistant) -> None:
    """The built-in Map stores {"strategy": ...} and no views. async_load
    succeeds, so a saved `views` list sat in the document while the frontend
    kept rendering the strategy — the edit reported success and never showed."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    strategy = MagicMock()
    strategy.mode = "storage"
    strategy.config = {"title": "Map", "url_path": "map"}
    strategy.async_load = AsyncMock(return_value={"strategy": {"type": "map"}})
    strategy.async_get_info = AsyncMock(return_value={"mode": "storage"})
    board.data[LOVELACE_DATA].dashboards["map"] = strategy

    for tool, args in (
        ("add_dashboard_view", {"dashboard_target": "map", "title": "Mine"}),
        ("get_dashboard", {"dashboard_target": "map"}),
    ):
        result = await _make_executor(board).execute(tool, args)
        assert "generated by a strategy" in result["error"], tool

    strategy.async_save.assert_not_called()


async def test_a_view_icon_and_path_can_be_cleared(board: HomeAssistant) -> None:
    """Set and clear need separate arguments — an empty string means 'not set'
    everywhere else here, so reading it as a clear would strip the icon off any
    view updated by a model that padded its optional params."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    await _make_executor(board).execute("update_dashboard_view", {"view": 0, "icon": "mdi:sofa"})

    result = await _make_executor(board).execute(
        "update_dashboard_view", {"view": 0, "clear": ["icon", "path"]}
    )
    assert result["status"] == "updated"

    view = (await board.data[LOVELACE_DATA].dashboards[None].async_load(False))["views"][0]
    assert "icon" not in view
    assert "path" not in view
    assert view["title"] == "Living"


async def test_an_empty_string_still_does_not_clear(board: HomeAssistant) -> None:
    """The convention _opt_str exists to protect: models pad unused params."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    await _make_executor(board).execute("update_dashboard_view", {"view": 0, "icon": "mdi:sofa"})
    await _make_executor(board).execute(
        "update_dashboard_view", {"view": 0, "title": "Lounge", "icon": "", "path": ""}
    )

    view = (await board.data[LOVELACE_DATA].dashboards[None].async_load(False))["views"][0]
    assert view["icon"] == "mdi:sofa"
    assert view["path"] == "living"
    assert view["title"] == "Lounge"


async def test_clearing_an_unknown_field_is_refused(board: HomeAssistant) -> None:
    result = await _make_executor(board).execute(
        "update_dashboard_view", {"view": 0, "clear": ["cards"]}
    )
    assert "clear accepts" in result["error"]


async def test_the_container_card_is_entity_validated(board: HomeAssistant) -> None:
    """The container is a caller-supplied card like any other, so a custom one
    naming a typo'd entity would be stored and render 'Entity not found'."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    result = await _make_executor(board).execute(
        "group_dashboard_cards",
        {
            "view": "living",
            "card_indices": [0, 1],
            "container": {"type": "custom:foo", "entity": "light.no_such_lamp"},
        },
    )
    assert "light.no_such_lamp" in result["error"]

    document = await board.data[LOVELACE_DATA].dashboards[None].async_load(False)
    assert len(document["views"][0]["cards"]) == 2


async def test_a_container_naming_a_real_entity_is_accepted(board: HomeAssistant) -> None:
    result = await _make_executor(board).execute(
        "group_dashboard_cards",
        {
            "view": "living",
            "card_indices": [0, 1],
            "container": {"type": "custom:foo", "entity": "light.lamp"},
        },
    )
    assert result["status"] == "grouped"


async def test_a_read_only_caller_is_not_told_the_dashboard_is_editable(
    board: HomeAssistant,
) -> None:
    """Every mutation tool is admin-gated, so advertising editable to a
    read-only credential offers a workflow it cannot finish."""
    listed = await _make_executor(board, is_admin=False).execute("list_dashboards", {})
    assert [d["editable"] for d in listed["dashboards"]] == [False]

    read = await _make_executor(board, is_admin=False).execute("get_dashboard", {})
    assert read["editable"] is False
    assert "cannot write dashboards" in read["note"]


async def test_a_yaml_dashboard_still_says_it_is_yaml(board: HomeAssistant) -> None:
    """Naming the wrong reason is worse than naming none — the model repeats it."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    yaml_board = MagicMock()
    yaml_board.mode = "yaml"
    yaml_board.config = {"title": "Wall", "url_path": "wall"}
    yaml_board.async_load = AsyncMock(return_value={"views": [{"title": "V"}]})
    yaml_board.async_get_info = AsyncMock(return_value={"mode": "yaml"})
    board.data[LOVELACE_DATA].dashboards["wall"] = yaml_board

    read = await _make_executor(board).execute("get_dashboard", {"dashboard_target": "wall"})
    assert read["editable"] is False
    assert "YAML-mode" in read["note"]


async def test_a_selected_view_carries_its_fingerprint_past_the_cap(
    board: HomeAssistant,
) -> None:
    """Past _MAX_VIEWS the view is absent from the summary, so without this the
    caller cannot supply the expected_view_fingerprint the writes demand."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    from custom_components.selora_ai.dashboard_manager import _MAX_VIEWS

    await (
        board.data[LOVELACE_DATA]
        .dashboards[None]
        .async_save(
            {
                "views": [
                    {"title": f"V{i}", "cards": [{"type": "light", "entity": "light.lamp"}]}
                    for i in range(_MAX_VIEWS + 3)
                ]
            }
        )
    )

    target = _MAX_VIEWS + 1
    read = await _make_executor(board).execute("get_dashboard", {"view": target})
    assert read["views_omitted"] == 3
    assert not [v for v in read["views"] if v["index"] == target]

    fingerprint = read["view"]["fingerprint"]
    assert fingerprint

    result = await _make_executor(board).execute(
        "update_dashboard_view",
        {"view": target, "title": "Renamed", "expected_fingerprint": fingerprint},
    )
    assert result["status"] == "updated"


def _strategy_board(hass: HomeAssistant, url_path: str = "map") -> MagicMock:
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    board_mock = MagicMock()
    board_mock.mode = "storage"
    board_mock.config = {"title": "Map", "url_path": url_path}
    board_mock.async_load = AsyncMock(return_value={"strategy": {"type": "map"}})
    # A strategy board reports plain storage — nothing cheaper distinguishes it.
    board_mock.async_get_info = AsyncMock(return_value={"mode": "storage", "views": 0})
    hass.data[LOVELACE_DATA].dashboards[url_path] = board_mock
    return board_mock


async def test_a_strategy_dashboard_is_not_listed_as_editable(board: HomeAssistant) -> None:
    """Its mode is storage and async_get_info says storage too, so it satisfied
    every cheap check while every mutation refused it."""
    _strategy_board(board)

    listed = await _make_executor(board).execute("list_dashboards", {})
    assert {d["url_path"]: d["editable"] for d in listed["dashboards"]}["map"] is False


async def test_inserting_into_a_strategy_dashboard_is_refused(board: HomeAssistant) -> None:
    """async_place_card loads directly, so it seeded a view, saved it alongside
    the strategy and reported success for a card that never appears."""
    strategy = _strategy_board(board)

    result = await _make_executor(board).execute(
        "insert_dashboard_card",
        {"dashboard_target": "map", "card": {"type": "light", "entity": "light.lamp"}},
    )
    assert result.get("ok") is False or "strategy" in str(result.get("error", ""))
    strategy.async_save.assert_not_called()


async def test_a_recipe_install_is_refused_on_a_strategy_dashboard(
    board: HomeAssistant,
) -> None:
    from custom_components.selora_ai.recipes.dashboard import async_place_card

    strategy = _strategy_board(board)

    result = await async_place_card(
        board, card={"type": "light", "entity": "light.lamp"}, tag="r1", target="map"
    )
    assert result.ok is False
    assert result.reason == "strategy_dashboard"
    strategy.async_save.assert_not_called()


async def test_a_missing_yaml_file_is_a_read_error_not_an_empty_board(
    board: HomeAssistant,
) -> None:
    """LovelaceYAML raises ConfigNotFound with mode yaml, so the auto-gen probe
    says nothing — and zero views hides a config problem behind an empty page."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA, ConfigNotFound

    broken = MagicMock()
    broken.mode = "yaml"
    broken.config = {"title": "Wall", "url_path": "wall"}
    broken.async_load = AsyncMock(side_effect=ConfigNotFound)
    broken.async_get_info = AsyncMock(
        return_value={"mode": "yaml", "error": "/config/wall.yaml not found"}
    )
    board.data[LOVELACE_DATA].dashboards["wall"] = broken

    result = await _make_executor(board).execute("get_dashboard", {"dashboard_target": "wall"})
    assert "could not read" in result["error"].lower()
    assert "wall.yaml" in result["error"]
    assert result.get("view_count") != 0


async def test_an_authorized_non_admin_writer_is_told_it_can_edit(
    board: HomeAssistant,
) -> None:
    """A custom MCP token allowlisted for the mutation tools, or a JWT with the
    write scope, may write without being an HA admin — telling it the dashboard
    is read-only contradicted the calls that then succeeded. Driven through the
    MCP handler because that is the only surface where the two answers differ."""
    from custom_components.selora_ai.helpers import caller_scope
    from custom_components.selora_ai.mcp_server import _tool_get_dashboard, _tool_list_dashboards

    with caller_scope(False, can_write=True):
        read = await _tool_get_dashboard(board, {})
        listed = await _tool_list_dashboards(board, {})
    assert read["editable"] is True
    assert "note" not in read
    assert [d["editable"] for d in listed["dashboards"]] == [True]


async def test_a_read_only_credential_is_still_told_it_cannot_edit(
    board: HomeAssistant,
) -> None:
    from custom_components.selora_ai.helpers import caller_scope
    from custom_components.selora_ai.mcp_server import _tool_get_dashboard

    with caller_scope(False, can_write=False):
        read = await _tool_get_dashboard(board, {})
    assert read["editable"] is False
    assert "cannot write dashboards" in read["note"]


async def test_write_capability_does_not_unhide_an_admin_only_dashboard(
    board: HomeAssistant,
) -> None:
    """require_admin is about who HA hides the page from, not about scopes —
    so the two answers must stay separate booleans."""
    from custom_components.selora_ai.helpers import caller_scope
    from custom_components.selora_ai.mcp_server import _tool_get_dashboard

    _admin_only_board(board)

    with caller_scope(False, can_write=True):
        result = await _tool_get_dashboard(board, {"dashboard_target": "cameras"})
    assert "No dashboard" in result["error"]


async def test_insertion_survives_a_failing_metadata_probe(board: HomeAssistant) -> None:
    """Probing before the load refused a perfectly loadable dashboard whenever
    async_get_info merely hiccuped, disabling insertion alone."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    default = board.data[LOVELACE_DATA].dashboards[None]
    default.async_get_info = AsyncMock(side_effect=RuntimeError("storage busy"))

    result = await _make_executor(board).execute(
        "insert_dashboard_card", {"card": {"type": "light", "entity": "light.lamp"}}
    )
    assert result["ok"] is True

    document = await default.async_load(False)
    assert len(document["views"][0]["cards"]) == 3


def _mcp_token_ctx(allowed: set[str]) -> MagicMock:
    ctx = MagicMock()
    ctx.auth_type = "mcp_token"
    ctx.allowed_tools = allowed
    ctx.is_admin = False
    return ctx


@pytest.mark.parametrize(
    "allowed",
    [
        {"selora_list_dashboards", "selora_insert_dashboard_card"},
        {"selora_list_dashboards", "selora_add_dashboard_view"},
        {"selora_list_dashboards", "selora_group_dashboard_cards"},
    ],
)
def test_any_allowed_dashboard_mutation_counts_as_write(allowed: set[str]) -> None:
    """An allowlist naming only `insert` authorises a real editing workflow —
    testing one representative tool called that credential read-only."""
    from custom_components.selora_ai.mcp_server import (
        _can_access_tool,
        _dashboard_write_tools,
    )

    ctx = _mcp_token_ctx(allowed)
    assert any(_can_access_tool(ctx, name) for name in _dashboard_write_tools())


def test_a_read_only_allowlist_is_not_a_write_capability() -> None:
    from custom_components.selora_ai.mcp_server import (
        _can_access_tool,
        _dashboard_write_tools,
    )

    ctx = _mcp_token_ctx({"selora_list_dashboards", "selora_get_dashboard"})
    assert not any(_can_access_tool(ctx, name) for name in _dashboard_write_tools())


def test_the_write_tool_set_is_derived_not_listed() -> None:
    """A mutation added to the family must be covered without anyone naming it
    here — the failure otherwise is quiet."""
    from custom_components.selora_ai import mcp_server
    from custom_components.selora_ai.tool_registry import TOOL_MAP

    derived = mcp_server._dashboard_write_tools()
    for mcp_name, chat_name in mcp_server._DERIVED_MCP_TOOLS.items():
        if "dashboard" not in chat_name:
            continue
        assert (mcp_name in derived) is TOOL_MAP[chat_name].requires_admin, mcp_name
