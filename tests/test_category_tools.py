"""Tests for the category registry chat/MCP tools.

Categories are the registry sibling of labels with one structural difference:
they are SCOPED, and an entity holds at most one per scope. These drive HA's
real category and entity registries.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import category_registry as cr, entity_registry as er
import pytest

from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import (
    COMMAND_TOOL_NAMES,
    CONFIG_TOOL_NAMES,
    TOOL_MAP,
)

_CATEGORY_TOOLS = (
    "list_categories",
    "create_category",
    "assign_category",
    "delete_category",
)


def _executor(hass: HomeAssistant, *, is_admin: bool = True) -> ToolExecutor:
    return ToolExecutor(hass, MagicMock(), is_admin=is_admin)


@pytest.fixture
def filed(hass: HomeAssistant) -> HomeAssistant:
    """Two automation categories and one script category, with entities filed."""
    registry = cr.async_get(hass)
    lights = registry.async_create(scope="automation", name="Lights")
    registry.async_create(scope="automation", name="Security")
    registry.async_create(scope="script", name="Lights")

    entities = er.async_get(hass)
    entry = entities.async_get_or_create("automation", "test", "one")
    entities.async_update_entity(entry.entity_id, categories={"automation": lights.category_id})
    entities.async_get_or_create("automation", "test", "two")
    return hass


# ── Registration ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", _CATEGORY_TOOLS)
def test_every_category_tool_is_registered_and_lane_reachable(name: str) -> None:
    assert name in TOOL_MAP
    assert name in CONFIG_TOOL_NAMES
    assert TOOL_MAP[name].large_context_only is True


def test_get_rid_of_a_category_is_reachable_from_the_command_lane() -> None:
    assert "delete_category" in COMMAND_TOOL_NAMES


def test_the_scope_param_offers_the_ui_lists() -> None:
    """A scope no Home Assistant page reads gives a category that exists but
    appears nowhere."""
    scope = next(p for p in TOOL_MAP["create_category"].params if p.name == "scope")
    assert scope.enum == ("automation", "script", "scene", "helper")
    assert scope.required is True


# ── Read ────────────────────────────────────────────────────────────────────


async def test_categories_are_listed_across_scopes(filed: HomeAssistant) -> None:
    result = await _executor(filed).execute("list_categories", {})

    assert result["count"] == 3
    assert sorted(result["scopes_in_use"]) == ["automation", "script"]
    assert {(c["scope"], c["name"]) for c in result["categories"]} == {
        ("automation", "Lights"),
        ("automation", "Security"),
        ("script", "Lights"),
    }


async def test_a_scope_narrows_the_listing(filed: HomeAssistant) -> None:
    result = await _executor(filed).execute("list_categories", {"scope": "script"})

    assert [c["name"] for c in result["categories"]] == ["Lights"]


async def test_the_listing_counts_filed_entities(filed: HomeAssistant) -> None:
    result = await _executor(filed).execute("list_categories", {"scope": "automation"})

    counts = {c["name"]: c["entity_count"] for c in result["categories"]}
    assert counts == {"Lights": 1, "Security": 0}


# ── Create ──────────────────────────────────────────────────────────────────


async def test_a_category_is_created(hass: HomeAssistant) -> None:
    result = await _executor(hass).execute(
        "create_category", {"scope": "automation", "name": "Heating", "icon": "mdi:radiator"}
    )
    assert result["status"] == "created"

    entry = cr.async_get(hass).async_get_category(
        scope="automation", category_id=result["category_id"]
    )
    assert entry is not None
    assert entry.name == "Heating"
    assert entry.icon == "mdi:radiator"


async def test_a_duplicate_within_a_scope_is_reported_not_created(filed: HomeAssistant) -> None:
    result = await _executor(filed).execute(
        "create_category", {"scope": "automation", "name": "Lights"}
    )
    assert result["status"] == "exists"
    assert len(list(cr.async_get(filed).async_list_categories(scope="automation"))) == 2


async def test_the_same_name_under_another_scope_is_a_different_category(
    filed: HomeAssistant,
) -> None:
    """Names are unique WITHIN a scope, which is why the scope is required
    rather than searched across."""
    result = await _executor(filed).execute("create_category", {"scope": "scene", "name": "Lights"})
    assert result["status"] == "created"


# ── Assign ──────────────────────────────────────────────────────────────────


async def test_entities_are_filed_under_a_category(filed: HomeAssistant) -> None:
    result = await _executor(filed).execute(
        "assign_category",
        {"entity_ids": ["automation.test_two"], "scope": "automation", "category": "Security"},
    )
    assert result["status"] == "assigned"

    entry = er.async_get(filed).async_get("automation.test_two")
    security = next(
        c
        for c in cr.async_get(filed).async_list_categories(scope="automation")
        if c.name == "Security"
    )
    assert entry.categories["automation"] == security.category_id


async def test_assigning_one_scope_leaves_the_others_alone(filed: HomeAssistant) -> None:
    """`categories` is a per-scope mapping several unrelated concerns write to;
    replacing it wholesale would drop whatever another page had filed."""
    entities = er.async_get(filed)
    script_cat = next(c for c in cr.async_get(filed).async_list_categories(scope="script"))
    entities.async_update_entity(
        "automation.test_one",
        categories={
            **entities.async_get("automation.test_one").categories,
            "script": script_cat.category_id,
        },
    )

    await _executor(filed).execute(
        "assign_category",
        {"entity_ids": ["automation.test_one"], "scope": "automation", "category": "Security"},
    )

    assert entities.async_get("automation.test_one").categories["script"] == script_cat.category_id


async def test_omitting_the_category_clears_that_scope(filed: HomeAssistant) -> None:
    """The only way to undo an assignment — an empty name is not a category a
    user can name."""
    result = await _executor(filed).execute(
        "assign_category", {"entity_ids": ["automation.test_one"], "scope": "automation"}
    )
    assert result["status"] == "cleared"
    assert "automation" not in er.async_get(filed).async_get("automation.test_one").categories


async def test_an_unknown_entity_is_reported_not_silently_skipped(filed: HomeAssistant) -> None:
    result = await _executor(filed).execute(
        "assign_category",
        {
            "entity_ids": ["automation.test_two", "automation.nope"],
            "scope": "automation",
            "category": "Security",
        },
    )
    assert result["entities_updated"] == ["automation.test_two"]
    assert result["not_found"] == ["automation.nope"]


async def test_an_unknown_category_is_refused(filed: HomeAssistant) -> None:
    result = await _executor(filed).execute(
        "assign_category",
        {"entity_ids": ["automation.test_two"], "scope": "automation", "category": "Nope"},
    )
    assert "error" in result


# ── Delete ──────────────────────────────────────────────────────────────────


async def test_deleting_a_category_asks_first(filed: HomeAssistant) -> None:
    result = await _executor(filed).execute(
        "delete_category", {"scope": "automation", "category": "Lights"}
    )

    assert result["requires_approval"] is True
    assert result["delete"]["kind"] == "category"
    assert list(cr.async_get(filed).async_list_categories(scope="automation"))


async def test_the_card_carries_the_scope_in_its_target(filed: HomeAssistant) -> None:
    """The same name under two scopes is two categories; a bare id would not say
    which one the card meant."""
    result = await _executor(filed).execute(
        "delete_category", {"scope": "script", "category": "Lights"}
    )
    assert result["delete"]["target_id"].startswith("script#")


async def test_the_card_counts_what_stops_being_categorised(filed: HomeAssistant) -> None:
    result = await _executor(filed).execute(
        "delete_category", {"scope": "automation", "category": "Lights"}
    )
    assert "1 item" in result["delete"]["label"]


async def test_deleting_a_category_leaves_its_entities_alone(filed: HomeAssistant) -> None:
    from custom_components.selora_ai.category_manager import async_delete_category

    lights = next(
        c
        for c in cr.async_get(filed).async_list_categories(scope="automation")
        if c.name == "Lights"
    )
    result = async_delete_category(filed, "automation", lights.category_id)

    assert result["entities_uncategorised"] == 1
    assert er.async_get(filed).async_get("automation.test_one") is not None


async def test_a_confirmed_delete_removes_the_category(filed: HomeAssistant) -> None:
    from custom_components.selora_ai import _resolve_delete_approval

    preview = await _executor(filed).execute(
        "delete_category", {"scope": "automation", "category": "Lights"}
    )
    store = MagicMock()
    store.set_approval_status = AsyncMock()
    store.append_message = AsyncMock(return_value={"role": "assistant"})
    connection = MagicMock()

    await _resolve_delete_approval(
        filed,
        connection,
        {"id": 1},
        store,
        "sess",
        0,
        {"approval_kind": "delete", "deletes": [preview["delete"]]},
        "delete",
        language="en",
    )

    connection.send_error.assert_not_called()
    names = [c.name for c in cr.async_get(filed).async_list_categories(scope="automation")]
    assert names == ["Security"]
    # The identically-named script category is untouched.
    assert [c.name for c in cr.async_get(filed).async_list_categories(scope="script")] == ["Lights"]


def test_the_delete_kind_is_executable() -> None:
    from custom_components.selora_ai.llm_client.command_policy import (
        _DELETE_KINDS,
        _DELETE_TOOLS,
    )

    assert "delete_category" in _DELETE_TOOLS
    assert "category" in _DELETE_KINDS


# ── MCP ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", _CATEGORY_TOOLS)
def test_every_category_tool_reaches_mcp(name: str) -> None:
    from custom_components.selora_ai import mcp_server

    mcp_name = f"selora_{name}"
    assert any(t.name == mcp_name for t in mcp_server._TOOL_DEFINITIONS)
    assert mcp_name in mcp_server._get_tool_handlers()
    assert mcp_server._DERIVED_MCP_TOOLS[mcp_name] == name


def test_mcp_category_access_matches_the_chat_definitions() -> None:
    from custom_components.selora_ai import mcp_server

    for name in _CATEGORY_TOOLS:
        mcp_name = f"selora_{name}"
        if TOOL_MAP[name].requires_admin:
            assert mcp_name in mcp_server._ADMIN_TOOLS, mcp_name
        else:
            assert mcp_name in mcp_server._READ_ONLY_TOOLS, mcp_name


async def test_mcp_deletes_the_category_on_the_spot(filed: HomeAssistant) -> None:
    from custom_components.selora_ai.mcp_server import _tool_delete_category

    result = await _tool_delete_category(filed, {"scope": "automation", "category": "Lights"})

    assert result["status"] == "deleted"
    names = [c.name for c in cr.async_get(filed).async_list_categories(scope="automation")]
    assert names == ["Security"]


async def test_a_confirmed_delete_answers_with_the_approval_scope(
    filed: HomeAssistant,
) -> None:
    """Parsing the category's scope out of target_id shadowed the resolver's own
    approval scope, so the success response reported "automation" where every
    other delete kind reports "delete"."""
    from custom_components.selora_ai import _resolve_delete_approval

    preview = await _executor(filed).execute(
        "delete_category", {"scope": "automation", "category": "Lights"}
    )
    store = MagicMock()
    store.set_approval_status = AsyncMock()
    store.append_message = AsyncMock(return_value={"role": "assistant"})
    connection = MagicMock()

    await _resolve_delete_approval(
        filed,
        connection,
        {"id": 1},
        store,
        "sess",
        0,
        {"approval_kind": "delete", "deletes": [preview["delete"]]},
        "delete",
        language="en",
    )

    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once()
    payload = connection.send_result.call_args[0][1]
    assert payload["scope"] == "delete"


async def test_an_entity_the_page_never_lists_is_refused(filed: HomeAssistant) -> None:
    """A category is a filing on ONE page. Writing the mapping anyway leaves an
    assignment the user sees nowhere and inflates the category's count."""
    entities = er.async_get(filed)
    entities.async_get_or_create("light", "test", "kitchen")

    result = await _executor(filed).execute(
        "assign_category",
        {
            "entity_ids": ["light.test_kitchen", "automation.test_two"],
            "scope": "automation",
            "category": "Security",
        },
    )

    assert result["entities_updated"] == ["automation.test_two"]
    assert result["wrong_scope"] == ["light.test_kitchen"]
    assert "never lists" in result["message"]
    assert "automation" not in (entities.async_get("light.test_kitchen").categories or {})


async def test_a_helper_entity_is_accepted_under_the_helper_scope(
    filed: HomeAssistant,
) -> None:
    """The helper page lists a family of domains rather than one."""
    entities = er.async_get(filed)
    entities.async_get_or_create("input_boolean", "test", "guest")
    cr.async_get(filed).async_create(scope="helper", name="Guests")

    result = await _executor(filed).execute(
        "assign_category",
        {"entity_ids": ["input_boolean.test_guest"], "scope": "helper", "category": "Guests"},
    )
    assert result["entities_updated"] == ["input_boolean.test_guest"]
    assert "wrong_scope" not in result


async def test_an_unknown_scope_polices_nothing(filed: HomeAssistant) -> None:
    """A scope HA may have added, or the user invented, has page contents we do
    not know — refusing every entity under it is worse than the case it guards."""
    entities = er.async_get(filed)
    entities.async_get_or_create("light", "test", "kitchen")
    cr.async_get(filed).async_create(scope="zone", name="Outside")

    result = await _executor(filed).execute(
        "assign_category",
        {"entity_ids": ["light.test_kitchen"], "scope": "zone", "category": "Outside"},
    )
    assert result["entities_updated"] == ["light.test_kitchen"]


async def test_a_config_entry_helper_is_accepted_under_the_helper_scope(
    filed: HomeAssistant,
) -> None:
    """A template, utility-meter, derivative or threshold helper is an ordinary
    sensor.* that the Helpers page nonetheless lists — a domain allowlist
    rejects every one of them."""
    from unittest.mock import patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain="utility_meter", title="Daily energy")
    entry.add_to_hass(filed)

    entities = er.async_get(filed)
    created = entities.async_get_or_create("sensor", "utility_meter", "daily", config_entry=entry)
    cr.async_get(filed).async_create(scope="helper", name="Energy")

    with patch(
        "custom_components.selora_ai.registry_manager._config_entry_helper_domains",
        return_value={"utility_meter"},
    ):
        result = await _executor(filed).execute(
            "assign_category",
            {"entity_ids": [created.entity_id], "scope": "helper", "category": "Energy"},
        )

    assert result["entities_updated"] == [created.entity_id]
    assert "wrong_scope" not in result


async def test_a_plain_sensor_is_still_refused_under_the_helper_scope(
    filed: HomeAssistant,
) -> None:
    """The guard must not become a pass-through for anything sensor-shaped."""
    entities = er.async_get(filed)
    created = entities.async_get_or_create("sensor", "test", "outdoor_temp")
    cr.async_get(filed).async_create(scope="helper", name="Energy")

    result = await _executor(filed).execute(
        "assign_category",
        {"entity_ids": [created.entity_id], "scope": "helper", "category": "Energy"},
    )
    assert result["wrong_scope"] == [created.entity_id]


async def test_names_differing_only_in_spacing_are_distinct(filed: HomeAssistant) -> None:
    """HA's uniqueness check is name.casefold() and nothing more, so both names
    are categories a user can genuinely have."""
    from custom_components.selora_ai.category_manager import resolve_category

    registry = cr.async_get(filed)
    single = registry.async_create(scope="automation", name="Outdoor Lights")
    double = registry.async_create(scope="automation", name="Outdoor  Lights")
    assert single.category_id != double.category_id

    resolved, error = resolve_category(filed, "automation", "Outdoor  Lights")
    assert error is None
    assert resolved.category_id == double.category_id

    resolved, error = resolve_category(filed, "automation", "Outdoor Lights")
    assert error is None
    assert resolved.category_id == single.category_id


async def test_a_loose_spacing_match_still_resolves_when_unambiguous(
    filed: HomeAssistant,
) -> None:
    """What a caller typing a name from memory needs."""
    from custom_components.selora_ai.category_manager import resolve_category

    cr.async_get(filed).async_create(scope="automation", name="Outdoor  Lights")

    resolved, error = resolve_category(filed, "automation", "Outdoor Lights")
    assert error is None
    assert resolved.name == "Outdoor  Lights"


async def test_an_ambiguous_spacing_match_is_refused(filed: HomeAssistant) -> None:
    """Picking the first of several is the silent mis-targeting the exact match
    exists to prevent."""
    from custom_components.selora_ai.category_manager import resolve_category

    registry = cr.async_get(filed)
    registry.async_create(scope="automation", name="Outdoor  Lights")
    registry.async_create(scope="automation", name="Outdoor   Lights")

    resolved, error = resolve_category(filed, "automation", "Outdoor Lights")
    assert resolved is None
    assert "differ only in spacing" in error


async def test_a_name_differing_only_in_spacing_is_created(filed: HomeAssistant) -> None:
    """HA allows both, so treating the loose match as a duplicate refuses a
    creation it would happily accept."""
    cr.async_get(filed).async_create(scope="automation", name="Outdoor  Lights")

    result = await _executor(filed).execute(
        "create_category", {"scope": "automation", "name": "Outdoor Lights"}
    )
    assert result["status"] == "created"

    names = {c.name for c in cr.async_get(filed).async_list_categories(scope="automation")}
    assert {"Outdoor  Lights", "Outdoor Lights"} <= names


async def test_a_genuine_duplicate_is_still_reported(filed: HomeAssistant) -> None:
    """Case-insensitively identical is what HA calls a duplicate."""
    result = await _executor(filed).execute(
        "create_category", {"scope": "automation", "name": "lights"}
    )
    assert result["status"] == "exists"


async def test_an_out_of_scope_assignment_can_still_be_cleared(
    filed: HomeAssistant,
) -> None:
    """Clearing removes a mapping that already exists, and an entity is often
    out of scope precisely because something wrote a stale one — refusing makes
    the bad state unfixable through the tool that caused it."""
    entities = er.async_get(filed)
    created = entities.async_get_or_create("light", "test", "kitchen")
    lights = next(
        c
        for c in cr.async_get(filed).async_list_categories(scope="automation")
        if c.name == "Lights"
    )
    # Written directly: exactly the stale mapping the scope guard now prevents.
    entities.async_update_entity(created.entity_id, categories={"automation": lights.category_id})

    result = await _executor(filed).execute(
        "assign_category", {"entity_ids": [created.entity_id], "scope": "automation"}
    )

    assert result["status"] == "cleared"
    assert result["entities_updated"] == [created.entity_id]
    assert "wrong_scope" not in result
    assert "automation" not in (entities.async_get(created.entity_id).categories or {})


async def test_a_padded_scope_still_deletes_on_mcp(filed: HomeAssistant) -> None:
    """resolve_category strips internally, so a padded scope RESOLVED and then
    failed at the delete — found, then reported missing."""
    from custom_components.selora_ai.mcp_server import _tool_delete_category

    result = await _tool_delete_category(filed, {"scope": "  automation  ", "category": "Lights"})

    assert result["status"] == "deleted"
    names = [c.name for c in cr.async_get(filed).async_list_categories(scope="automation")]
    assert names == ["Security"]


async def test_a_padded_scope_makes_a_card_that_can_confirm(filed: HomeAssistant) -> None:
    """On the preview path the raw scope was baked into target_id, so the card
    described a category its own confirm could not find."""
    from custom_components.selora_ai import _resolve_delete_approval

    preview = await _executor(filed).execute(
        "delete_category", {"scope": "  automation  ", "category": "Lights"}
    )
    assert preview["delete"]["target_id"].startswith("automation#")

    store = MagicMock()
    store.set_approval_status = AsyncMock()
    store.append_message = AsyncMock(return_value={"role": "assistant"})
    connection = MagicMock()
    await _resolve_delete_approval(
        filed,
        connection,
        {"id": 1},
        store,
        "sess",
        0,
        {"approval_kind": "delete", "deletes": [preview["delete"]]},
        "delete",
        language="en",
    )

    connection.send_error.assert_not_called()
    names = [c.name for c in cr.async_get(filed).async_list_categories(scope="automation")]
    assert names == ["Security"]


async def test_the_manager_normalizes_its_own_scope(filed: HomeAssistant) -> None:
    """Defence in depth: a future caller that resolves and passes its raw scope
    on must not reintroduce this."""
    from custom_components.selora_ai.category_manager import async_delete_category

    lights = next(
        c
        for c in cr.async_get(filed).async_list_categories(scope="automation")
        if c.name == "Lights"
    )
    result = async_delete_category(filed, " automation ", lights.category_id)

    assert result["status"] == "deleted"
