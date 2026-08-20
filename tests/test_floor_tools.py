"""Tests for the floor registry chat/MCP tools.

Floors were reachable only as a side effect of placing an area — `_ensure_floor`
created one and nothing could list, rename or remove it. These drive HA's real
floor and area registries so a change in either fails here rather than at
runtime.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, floor_registry as fr
import pytest

from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import (
    COMMAND_TOOL_NAMES,
    CONFIG_TOOL_NAMES,
    TOOL_MAP,
)

_FLOOR_TOOLS = ("list_floors", "create_floor", "update_floor", "delete_floor")


def _executor(hass: HomeAssistant, *, is_admin: bool = True) -> ToolExecutor:
    return ToolExecutor(hass, MagicMock(), is_admin=is_admin)


@pytest.fixture
def home(hass: HomeAssistant) -> HomeAssistant:
    """Two floors with areas, plus an area on no floor."""
    floors = fr.async_get(hass)
    areas = ar.async_get(hass)
    ground = floors.async_create("Ground", level=0)
    upstairs = floors.async_create("Upstairs", level=1)
    areas.async_create("Kitchen", floor_id=ground.floor_id)
    areas.async_create("Hall", floor_id=ground.floor_id)
    areas.async_create("Bedroom", floor_id=upstairs.floor_id)
    areas.async_create("Garden")
    return hass


# ── Registration ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", _FLOOR_TOOLS)
def test_every_floor_tool_is_registered_and_lane_reachable(name: str) -> None:
    """A config request falls through to the command lane, which would trim the
    schema to device control and hide exactly these."""
    assert name in TOOL_MAP
    assert name in CONFIG_TOOL_NAMES


def test_get_rid_of_a_floor_is_reachable_from_the_command_lane() -> None:
    """ "Get rid of the upstairs floor" classifies as a command, like delete_area."""
    assert "delete_floor" in COMMAND_TOOL_NAMES


@pytest.mark.parametrize("name", _FLOOR_TOOLS)
def test_floor_tools_are_hidden_from_the_low_context_model(name: str) -> None:
    assert TOOL_MAP[name].large_context_only is True


# ── Read ────────────────────────────────────────────────────────────────────


async def test_floors_are_listed_with_their_areas(home: HomeAssistant) -> None:
    result = await _executor(home).execute("list_floors", {})

    assert result["count"] == 2
    assert [f["name"] for f in result["floors"]] == ["Ground", "Upstairs"]
    assert result["floors"][0]["areas"] == ["Hall", "Kitchen"]
    assert result["floors"][1]["areas"] == ["Bedroom"]


async def test_areas_with_no_floor_are_named_not_counted(home: HomeAssistant) -> None:
    """It is the question that follows "list the floors"; a count sends the
    caller back for a second round trip against the area list."""
    result = await _executor(home).execute("list_floors", {})
    assert result["areas_without_a_floor"] == ["Garden"]


async def test_floors_order_by_level_not_name(hass: HomeAssistant) -> None:
    """Level is the only field carrying the storeys' real relationship."""
    floors = fr.async_get(hass)
    floors.async_create("Attic", level=2)
    floors.async_create("Basement", level=-1)
    floors.async_create("Ground", level=0)

    result = await _executor(hass).execute("list_floors", {})
    assert [f["name"] for f in result["floors"]] == ["Basement", "Ground", "Attic"]


async def test_a_floor_with_no_level_sorts_last(hass: HomeAssistant) -> None:
    """Unset is not the ground floor."""
    floors = fr.async_get(hass)
    floors.async_create("Attic")
    floors.async_create("Ground", level=0)

    result = await _executor(hass).execute("list_floors", {})
    assert [f["name"] for f in result["floors"]] == ["Ground", "Attic"]


# ── Create ──────────────────────────────────────────────────────────────────


async def test_a_floor_is_created(hass: HomeAssistant) -> None:
    result = await _executor(hass).execute(
        "create_floor", {"name": "Loft", "level": 3, "icon": "mdi:home-roof"}
    )
    assert result["status"] == "created"

    entry = fr.async_get(hass).async_get_floor(result["floor_id"])
    assert entry is not None
    assert entry.name == "Loft"
    assert entry.level == 3
    assert entry.icon == "mdi:home-roof"


async def test_level_zero_survives_the_adapter(hass: HomeAssistant) -> None:
    """The blank-is-absent rule the other adapters use would read the ground
    floor as "not set" and leave it unordered."""
    result = await _executor(hass).execute("create_floor", {"name": "Ground", "level": 0})
    assert fr.async_get(hass).async_get_floor(result["floor_id"]).level == 0


async def test_a_duplicate_floor_is_reported_not_created(home: HomeAssistant) -> None:
    """HA's async_create raises on a duplicate name, which reads to the model as
    a failure worth retrying differently rather than "it is already there"."""
    result = await _executor(home).execute("create_floor", {"name": "Ground"})

    assert result["status"] == "exists"
    assert len(list(fr.async_get(home).async_list_floors())) == 2


# ── Update ──────────────────────────────────────────────────────────────────


async def test_renaming_a_floor_keeps_its_areas(home: HomeAssistant) -> None:
    """floor_id is derived at creation and never rewritten, so no area moves."""
    result = await _executor(home).execute(
        "update_floor", {"floor": "Ground", "new_name": "Downstairs"}
    )
    assert result["status"] == "updated"

    listed = await _executor(home).execute("list_floors", {})
    downstairs = next(f for f in listed["floors"] if f["name"] == "Downstairs")
    assert downstairs["areas"] == ["Hall", "Kitchen"]


async def test_a_rename_onto_an_existing_floor_is_refused(home: HomeAssistant) -> None:
    result = await _executor(home).execute(
        "update_floor", {"floor": "Ground", "new_name": "Upstairs"}
    )
    assert "already exists" in result["error"]
    assert fr.async_get(home).async_get_floor_by_name("Ground") is not None


async def test_updating_nothing_reports_unchanged(home: HomeAssistant) -> None:
    result = await _executor(home).execute("update_floor", {"floor": "Ground"})
    assert result["status"] == "unchanged"


async def test_an_unknown_floor_is_refused(home: HomeAssistant) -> None:
    result = await _executor(home).execute("update_floor", {"floor": "Mezzanine"})
    assert "error" in result


# ── Delete ──────────────────────────────────────────────────────────────────


async def test_deleting_a_floor_asks_first(home: HomeAssistant) -> None:
    """Nothing is removed until the user taps the card."""
    result = await _executor(home).execute("delete_floor", {"floor": "Ground"})

    assert result["requires_approval"] is True
    assert result["delete"]["kind"] == "floor"
    assert fr.async_get(home).async_get_floor_by_name("Ground") is not None


async def test_the_card_names_the_areas_that_lose_their_floor(home: HomeAssistant) -> None:
    """HA clears each area's floor_id silently; "and 4 areas" would not tell the
    user whether the one they care about is among them."""
    result = await _executor(home).execute("delete_floor", {"floor": "Ground"})

    label = result["delete"]["label"]
    assert "Hall" in label and "Kitchen" in label


async def test_the_card_carries_a_creation_fingerprint(home: HomeAssistant) -> None:
    """floor_id is name-derived and reusable once the floor is gone."""
    result = await _executor(home).execute("delete_floor", {"floor": "Ground"})
    assert result["delete"]["fingerprint"]


async def test_deleting_a_floor_leaves_its_areas_alone(home: HomeAssistant) -> None:
    from custom_components.selora_ai.registry_manager import async_delete_floor

    entry = fr.async_get(home).async_get_floor_by_name("Ground")
    result = await async_delete_floor(home, entry.floor_id)

    assert result["areas_unassigned"] == ["Hall", "Kitchen"]
    areas = ar.async_get(home)
    assert areas.async_get_area_by_name("Kitchen") is not None
    assert areas.async_get_area_by_name("Kitchen").floor_id is None


async def test_deleting_an_unknown_floor_is_refused(home: HomeAssistant) -> None:
    from custom_components.selora_ai.registry_manager import async_delete_floor

    assert "error" in await async_delete_floor(home, "floor.nope")


# ── MCP ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", _FLOOR_TOOLS)
def test_every_floor_tool_reaches_mcp(name: str) -> None:
    from custom_components.selora_ai import mcp_server

    mcp_name = f"selora_{name}"
    assert any(t.name == mcp_name for t in mcp_server._TOOL_DEFINITIONS)
    assert mcp_name in mcp_server._get_tool_handlers()
    assert mcp_server._DERIVED_MCP_TOOLS[mcp_name] == name


def test_mcp_floor_access_matches_the_chat_definitions() -> None:
    """A read tool in the admin set is unreachable for a read-only credential;
    a write tool missing from it is reachable by one."""
    from custom_components.selora_ai import mcp_server

    for name in _FLOOR_TOOLS:
        mcp_name = f"selora_{name}"
        if TOOL_MAP[name].requires_admin:
            assert mcp_name in mcp_server._ADMIN_TOOLS, mcp_name
        else:
            assert mcp_name in mcp_server._READ_ONLY_TOOLS, mcp_name


async def test_mcp_deletes_the_floor_on_the_spot(home: HomeAssistant) -> None:
    """MCP clients run their own confirmation — there is no card."""
    from custom_components.selora_ai.mcp_server import _tool_delete_floor

    result = await _tool_delete_floor(home, {"floor": "Ground"})

    assert result["status"] == "deleted"
    assert fr.async_get(home).async_get_floor_by_name("Ground") is None


def test_mcp_says_the_floor_delete_is_immediate() -> None:
    """The shared description promises a confirmation card MCP cannot deliver."""
    from custom_components.selora_ai import mcp_server

    tool = next(t for t in mcp_server._TOOL_DEFINITIONS if t.name == "selora_delete_floor")
    assert "runs IMMEDIATELY" in tool.description


# ── Confirmation ────────────────────────────────────────────────────────────


def test_the_delete_kind_is_executable() -> None:
    """Missing either allowlist fails silently in the worst shape available: the
    tool returns requires_approval, the loop discards the model's prose, and the
    synthesizer drops the descriptor — an empty reply and no card."""
    from custom_components.selora_ai.llm_client.command_policy import (
        _DELETE_KINDS,
        _DELETE_TOOLS,
    )

    assert "delete_floor" in _DELETE_TOOLS
    assert "floor" in _DELETE_KINDS


async def _confirm(hass: HomeAssistant, descriptor: dict[str, Any]) -> MagicMock:
    """Drive the real websocket delete resolver over one descriptor."""
    from custom_components.selora_ai import _resolve_delete_approval

    store = MagicMock()
    store.set_approval_status = AsyncMock()
    store.append_message = AsyncMock(return_value={"role": "assistant"})
    connection = MagicMock()
    await _resolve_delete_approval(
        hass,
        connection,
        {"id": 1},
        store,
        "sess",
        0,
        {"approval_kind": "delete", "deletes": [descriptor]},
        "delete",
        language="en",
    )
    return connection


async def test_a_confirmed_delete_removes_the_floor(home: HomeAssistant) -> None:
    entry = fr.async_get(home).async_get_floor_by_name("Ground")
    preview = await _executor(home).execute("delete_floor", {"floor": "Ground"})

    connection = await _confirm(home, preview["delete"])

    connection.send_error.assert_not_called()
    assert fr.async_get(home).async_get_floor(entry.floor_id) is None


async def test_a_recreated_floor_is_not_deleted_by_a_stale_card(home: HomeAssistant) -> None:
    """floor_id comes from the name, so deleting "Ground" and making a new
    "Ground" while the card sits open yields the same id."""
    preview = await _executor(home).execute("delete_floor", {"floor": "Ground"})
    descriptor = preview["delete"]

    floors = fr.async_get(home)
    floors.async_delete(descriptor["target_id"])
    recreated = floors.async_create("Ground", level=0)
    assert recreated.floor_id == descriptor["target_id"]

    connection = await _confirm(home, descriptor)

    connection.send_error.assert_called_once()
    assert floors.async_get_floor(recreated.floor_id) is not None


async def test_a_floor_icon_can_be_cleared(home: HomeAssistant) -> None:
    """_opt_str collapses "" to None, so the manager's clear path was
    unreachable from either surface."""
    await _executor(home).execute("update_floor", {"floor": "Ground", "icon": "mdi:home"})

    result = await _executor(home).execute("update_floor", {"floor": "Ground", "clear": ["icon"]})
    assert result["status"] == "updated"
    assert fr.async_get(home).async_get_floor_by_name("Ground").icon is None


async def test_an_empty_icon_string_still_does_not_clear(home: HomeAssistant) -> None:
    """The convention _opt_str exists to protect: models pad unused params."""
    await _executor(home).execute("update_floor", {"floor": "Ground", "icon": "mdi:home"})
    await _executor(home).execute(
        "update_floor", {"floor": "Ground", "new_name": "Lower", "icon": ""}
    )

    assert fr.async_get(home).async_get_floor_by_name("Lower").icon == "mdi:home"


async def test_clearing_an_unknown_floor_field_is_refused(home: HomeAssistant) -> None:
    result = await _executor(home).execute("update_floor", {"floor": "Ground", "clear": ["name"]})
    assert "clear accepts" in result["error"]


async def test_a_name_matching_another_floors_alias_is_created(hass: HomeAssistant) -> None:
    """HA enforces uniqueness on the NAME alone, so a floor whose name equals
    another's alias is one a user can legitimately create — resolve_floor also
    matches aliases and reported it as already existing."""
    floors = fr.async_get(hass)
    floors.async_create("Ground", aliases={"Downstairs"})

    result = await _executor(hass).execute("create_floor", {"name": "Downstairs"})

    assert result["status"] == "created"
    assert {f.name for f in floors.async_list_floors()} == {"Ground", "Downstairs"}


async def test_a_genuine_name_duplicate_is_still_reported(home: HomeAssistant) -> None:
    """The alias fix must not turn duplicate detection off."""
    result = await _executor(home).execute("create_floor", {"name": "ground"})

    assert result["status"] == "exists"
    assert len(list(fr.async_get(home).async_list_floors())) == 2


async def test_finding_a_floor_by_alias_still_works(hass: HomeAssistant) -> None:
    """The forgiving resolver stays forgiving — it is only the duplicate CHECK
    that must ask a narrower question."""
    from custom_components.selora_ai.registry_manager import resolve_floor

    floors = fr.async_get(hass)
    floors.async_create("Ground", aliases={"Downstairs"})

    entry, error = resolve_floor(hass, "Downstairs")
    assert error is None
    assert entry.name == "Ground"


async def test_a_rename_onto_another_floors_alias_is_allowed(hass: HomeAssistant) -> None:
    """create_floor already permits a name matching another floor's alias, so
    refusing the same name on rename was an inconsistency, not a stricter rule."""
    floors = fr.async_get(hass)
    floors.async_create("Ground", aliases={"Downstairs"})
    floors.async_create("Attic")

    result = await _executor(hass).execute(
        "update_floor", {"floor": "Attic", "new_name": "Downstairs"}
    )

    assert result["status"] == "updated"
    assert {f.name for f in floors.async_list_floors()} == {"Ground", "Downstairs"}


async def test_a_rename_onto_a_real_floor_name_is_still_refused(
    home: HomeAssistant,
) -> None:
    """The alias fix must not turn the conflict check off."""
    result = await _executor(home).execute(
        "update_floor", {"floor": "Ground", "new_name": "Upstairs"}
    )
    assert "already exists" in result["error"]


async def test_renaming_a_floor_to_its_own_name_is_not_a_clash(
    home: HomeAssistant,
) -> None:
    result = await _executor(home).execute(
        "update_floor", {"floor": "Ground", "new_name": "Ground"}
    )
    assert "error" not in result
