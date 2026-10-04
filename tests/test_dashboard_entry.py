"""Tests for creating and deleting dashboard ENTRIES in-process (the MCP path).

`DashboardsCollection` is a local in `lovelace.async_setup`; it is reached
through the handler of `lovelace/dashboards/create`. That layout is Home
Assistant's, not an API, so the first test pins it against the installed core —
when it moves, this file goes red rather than MCP quietly losing the tools.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai import dashboard_manager, mcp_server
from custom_components.selora_ai.helpers import caller_scope


@pytest.fixture
async def lovelace(hass: HomeAssistant) -> HomeAssistant:
    assert await async_setup_component(hass, "lovelace", {"lovelace": {"mode": "storage"}})
    await hass.async_block_till_done()
    return hass


async def _create(hass: HomeAssistant, **arguments: Any) -> dict[str, Any]:
    with caller_scope(True):
        return await mcp_server._tool_create_dashboard(hass, arguments)


async def _delete(hass: HomeAssistant, target: str) -> dict[str, Any]:
    with caller_scope(True):
        return await mcp_server._tool_delete_dashboard(hass, {"dashboard_target": target})


# ── Reaching the collection ─────────────────────────────────────────────────


async def test_the_collection_is_reachable_on_this_core(lovelace: HomeAssistant) -> None:
    from homeassistant.components.lovelace.dashboard import DashboardsCollection

    assert isinstance(dashboard_manager._dashboards_collection(lovelace), DashboardsCollection)


async def test_an_unrecognised_layout_reports_the_limitation(lovelace: HomeAssistant) -> None:
    """A future core that registers the command differently must produce an
    answer the user can act on, not an AttributeError."""
    handlers = lovelace.data["websocket_api"]
    handlers["lovelace/dashboards/create"] = (lambda *_: None, None)

    result = await _create(lovelace, title="Pool")

    assert "Settings > Dashboards" in result["error"]
    assert "pool" not in lovelace.data[LOVELACE_DATA].dashboards


async def test_no_lovelace_reports_the_limitation(hass: HomeAssistant) -> None:
    assert dashboard_manager._dashboards_collection(hass) is None


# ── Create ──────────────────────────────────────────────────────────────────


async def test_mcp_creates_a_dashboard_it_can_then_fill(lovelace: HomeAssistant) -> None:
    """The point of the tool: a dashboard that exists, is in the sidebar, and
    takes a page in the next call rather than refusing it as auto-generated."""
    from homeassistant.components.frontend import DATA_PANELS

    lovelace.states.async_set("switch.aqua_rite", "off")

    result = await _create(lovelace, title="Pool", icon="mdi:pool")

    assert result["status"] == "created"
    assert result["url_path"] == "pool"
    assert result["url"] == "/pool"
    assert "pool" in lovelace.data[LOVELACE_DATA].dashboards
    assert "pool" in lovelace.data.get(DATA_PANELS, {})

    with caller_scope(True):
        added = await mcp_server._tool_add_dashboard_view(
            lovelace,
            {
                "dashboard_target": "pool",
                "title": "Pool",
                "cards": [{"type": "tile", "entity": "switch.aqua_rite"}],
            },
        )
    assert "error" not in added, added
    stored = await lovelace.data[LOVELACE_DATA].dashboards["pool"].async_load(False)
    assert stored["views"][0]["cards"] == [{"type": "tile", "entity": "switch.aqua_rite"}]


async def test_create_stores_what_was_asked(lovelace: HomeAssistant) -> None:
    """String booleans from loose providers must not invert visibility."""
    await _create(
        lovelace,
        title="Basement Pool",
        icon="mdi:pool",
        require_admin="true",
        show_in_sidebar="false",
    )

    # Looked up by url_path: the collection id is HA's slug of it, with
    # underscores ("basement_pool"), and only coincides for one-word paths.
    collection = dashboard_manager._dashboards_collection(lovelace)
    item = next(i for i in collection.data.values() if i["url_path"] == "basement-pool")
    assert item["title"] == "Basement Pool"
    assert item["icon"] == "mdi:pool"
    assert item["require_admin"] is True
    assert item["show_in_sidebar"] is False


async def test_create_refuses_a_taken_path(lovelace: HomeAssistant) -> None:
    await _create(lovelace, title="Pool")

    result = await _create(lovelace, title="Pool")

    assert "already uses the URL '/pool'" in result["error"]


async def test_create_refuses_a_bad_icon_before_touching_anything(lovelace: HomeAssistant) -> None:
    result = await _create(lovelace, title="Pool", icon="pool")

    assert "not a usable icon" in result["error"]
    assert "pool" not in lovelace.data[LOVELACE_DATA].dashboards


async def test_a_non_admin_cannot_create_a_dashboard_hidden_from_itself(
    lovelace: HomeAssistant,
) -> None:
    """A write-scoped credential need not be an HA admin. The dashboard would
    be hidden from it on creation, so it could never be seeded or filled."""
    with caller_scope(False, can_write=True):
        result = await mcp_server._tool_create_dashboard(
            lovelace, {"title": "Pool", "require_admin": True}
        )

    assert "Only an administrator" in result["error"]
    assert "pool" not in lovelace.data[LOVELACE_DATA].dashboards


async def test_a_non_admin_can_create_a_dashboard_it_can_fill(lovelace: HomeAssistant) -> None:
    with caller_scope(False, can_write=True):
        result = await mcp_server._tool_create_dashboard(lovelace, {"title": "Pool"})

    assert result["status"] == "created"
    assert "add_dashboard_view" in result["note"]


async def test_an_unseeded_dashboard_is_not_promised_as_fillable(lovelace: HomeAssistant) -> None:
    async def _not_seeded(*_args: Any) -> bool:
        return False

    with patch.object(dashboard_manager, "async_initialize_created_dashboard", _not_seeded):
        result = await _create(lovelace, title="Pool")

    assert result["status"] == "created"
    assert "add_dashboard_view" not in result["note"]
    assert "Take control" in result["note"]


# ── Delete ──────────────────────────────────────────────────────────────────


async def test_mcp_deletes_a_dashboard_and_names_what_went(lovelace: HomeAssistant) -> None:
    await _create(lovelace, title="Pool")
    await (
        lovelace.data[LOVELACE_DATA]
        .dashboards["pool"]
        .async_save({"views": [{"title": "One", "cards": [{"type": "markdown", "content": "x"}]}]})
    )

    result = await _delete(lovelace, "pool")

    assert result["status"] == "deleted"
    assert result["view_count"] == 1
    assert result["card_count"] == 1
    assert "pool" not in lovelace.data[LOVELACE_DATA].dashboards
    collection = dashboard_manager._dashboards_collection(lovelace)
    assert not any(i["url_path"] == "pool" for i in collection.data.values())


async def test_delete_refuses_the_default(lovelace: HomeAssistant) -> None:
    result = await _delete(lovelace, "lovelace")

    assert "cannot be deleted" in result["error"]


async def test_delete_refuses_a_dashboard_that_changed_under_it(lovelace: HomeAssistant) -> None:
    """A dashboard's id is its url_path, so one remade at the same path while
    the proposal was reading would otherwise be deleted in its place."""
    await _create(lovelace, title="Pool")
    real = dashboard_manager.async_propose_dashboard_delete

    async def _stale(hass: HomeAssistant, target: str) -> dict[str, Any]:
        proposal = await real(hass, target)
        proposal["client_action"]["expected"]["title"] = "Something else"
        return proposal

    with patch.object(dashboard_manager, "async_propose_dashboard_delete", _stale):
        result = await _delete(lovelace, "pool")

    assert "changed while it was being read" in result["error"]
    assert "pool" in lovelace.data[LOVELACE_DATA].dashboards


# ── MCP surface ─────────────────────────────────────────────────────────────


def _definition(name: str) -> Any:
    return next(t for t in mcp_server._TOOL_DEFINITIONS if t.name == name)


@pytest.mark.parametrize("name", ["selora_create_dashboard", "selora_delete_dashboard"])
def test_the_mcp_tools_are_admin_gated(name: str) -> None:
    assert name in mcp_server._ADMIN_TOOLS
    assert name in mcp_server._dashboard_write_tools()


def test_the_mcp_descriptions_promise_no_card() -> None:
    """The chat text describes a Create button and a result that comes back
    later; over MCP the handler acts at once."""
    for name in ("selora_create_dashboard", "selora_delete_dashboard"):
        text = _definition(name).description
        assert "button" not in text.lower(), name
        assert "comes back" not in text, name
        assert "panel" not in text.lower(), name
    assert "IMMEDIATELY" in _definition("selora_delete_dashboard").description


def test_the_mcp_create_schema_matches_chat_without_resumption() -> None:
    from custom_components.selora_ai.tool_registry import TOOL_MAP

    chat = set(TOOL_MAP["create_dashboard"].to_anthropic()["input_schema"]["properties"])
    mcp = set(_definition("selora_create_dashboard").inputSchema["properties"])
    assert mcp == chat - {"remaining_intent"}
