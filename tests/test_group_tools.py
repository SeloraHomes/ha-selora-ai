"""Tests for the group-helper CRUD chat tools.

The create/update/delete tests drive Home Assistant's REAL ``group`` config
flow against a real ``hass``, so a change to HA's helper flow (menu step,
option keys, per-type schema) fails here rather than silently at runtime.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
import voluptuous as vol

from custom_components.selora_ai import group_manager as gm
from custom_components.selora_ai.llm_client.command_policy import (
    _pending_deletes_from_log,
    synthesize_approval_from_tool_log,
)
from custom_components.selora_ai.mcp_server import (
    _preview_delete_group,
    _tool_create_group,
    _tool_delete_group,
    _tool_list_groups,
    _tool_update_group,
)
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import (
    CHAT_TOOLS,
    COMMAND_TOOL_NAMES,
    TOOL_CREATE_GROUP,
    TOOL_DELETE_GROUP,
    TOOL_LIST_GROUPS,
    TOOL_MAP,
    TOOL_UPDATE_GROUP,
)

_GROUP_TOOL_NAMES = ("list_groups", "create_group", "update_group", "delete_group")


def _make_executor(hass: HomeAssistant, *, is_admin: bool = False) -> ToolExecutor:
    return ToolExecutor(hass, MagicMock(), is_admin=is_admin)


@pytest.fixture
async def group_home(hass: HomeAssistant) -> HomeAssistant:
    """A hass with the group integration up and a few member entities."""
    assert await async_setup_component(hass, "group", {})
    await hass.async_block_till_done()
    for entity_id in ("light.lamp", "light.ceiling", "light.desk"):
        hass.states.async_set(entity_id, "off")
    hass.states.async_set("switch.plug", "off")
    hass.states.async_set("sensor.temp_a", "20.0")
    hass.states.async_set("number.setpoint", "21.0")
    await hass.async_block_till_done()
    return hass


async def _create(hass: HomeAssistant, **kwargs: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "Evening Lights",
        "entities": ["light.lamp", "light.ceiling"],
    }
    payload.update(kwargs)
    result = await _tool_create_group(hass, payload)
    await hass.async_block_till_done()
    return result


# ── Registry / schema ────────────────────────────────────────────────


class TestGroupToolRegistry:
    def test_all_four_registered(self) -> None:
        for tool in (TOOL_LIST_GROUPS, TOOL_CREATE_GROUP, TOOL_UPDATE_GROUP, TOOL_DELETE_GROUP):
            assert tool in CHAT_TOOLS
        for name in _GROUP_TOOL_NAMES:
            assert name in TOOL_MAP

    def test_only_writes_require_admin(self) -> None:
        assert TOOL_MAP["list_groups"].requires_admin is False
        for name in ("create_group", "update_group", "delete_group"):
            assert TOOL_MAP[name].requires_admin is True

    def test_in_command_tool_names(self) -> None:
        """_classify_chat_intent falls through to "command" for group phrasings
        ("group my bedroom lights"), which trims the schema to
        COMMAND_TOOL_NAMES — so the group tools must be in that set or they
        become invisible for the requests that need them."""
        for name in _GROUP_TOOL_NAMES:
            assert name in COMMAND_TOOL_NAMES

    def test_array_params_declare_items(self) -> None:
        """Gemini rejects an ARRAY function-declaration parameter with no
        ``items``, so every array param must carry an element type."""
        for name in ("create_group", "update_group"):
            for serialized in (
                TOOL_MAP[name].to_anthropic()["input_schema"]["properties"],
                TOOL_MAP[name].to_openai()["function"]["parameters"]["properties"],
            ):
                arrays = [p for p in serialized.values() if p.get("type") == "array"]
                assert arrays, f"{name} should expose an array param"
                for prop in arrays:
                    assert prop["items"] == {"type": "string"}

    def test_create_group_requires_name_and_entities(self) -> None:
        schema = TOOL_MAP["create_group"].to_anthropic()["input_schema"]
        assert set(schema["required"]) == {"name", "entities"}


class TestExecutorAdminGating:
    @pytest.mark.asyncio
    async def test_writes_refused_for_non_admin(self, hass: HomeAssistant) -> None:
        executor = _make_executor(hass, is_admin=False)
        for name in ("create_group", "update_group", "delete_group"):
            result = await executor.execute(name, {})
            assert "requires admin privileges" in result["error"]

    @pytest.mark.asyncio
    async def test_list_allowed_for_non_admin(self, hass: HomeAssistant) -> None:
        executor = _make_executor(hass, is_admin=False)
        result = await executor.execute("list_groups", {})
        assert result == {"groups": [], "count": 0}

    @pytest.mark.asyncio
    async def test_full_lifecycle_through_executor(self, group_home: HomeAssistant) -> None:
        """The chat path is ToolExecutor, not the _tool_* functions directly —
        exercise create → list → update → delete-preview through it so the
        handler map, admin gate, and result truncation are all covered."""
        executor = _make_executor(group_home, is_admin=True)

        created = await executor.execute(
            "create_group",
            {"name": "Evening Lights", "entities": ["light.lamp", "light.ceiling"]},
        )
        await group_home.async_block_till_done()
        assert created["status"] == "created"
        entity_id = created["entity_id"]

        listed = await executor.execute("list_groups", {})
        assert [g["entity_id"] for g in listed["groups"]] == [entity_id]

        updated = await executor.execute(
            "update_group", {"entity_id": entity_id, "add_entities": ["light.desk"]}
        )
        await group_home.async_block_till_done()
        assert updated["member_count"] == 3

        preview = await executor.execute("delete_group", {"entity_id": entity_id})
        assert preview["requires_approval"] is True
        # Nothing deleted yet — the card gates it.
        assert len(gm.group_entries(group_home)) == 1

        assert [c["tool"] for c in executor.call_log] == [
            "create_group",
            "list_groups",
            "update_group",
            "delete_group",
        ]

    @pytest.mark.asyncio
    async def test_delete_delegates_to_preview_not_real_delete(self, hass: HomeAssistant) -> None:
        """The chat tool must never delete directly — it previews."""
        executor = _make_executor(hass, is_admin=True)
        with patch(
            "custom_components.selora_ai.mcp_server._preview_delete_group",
            new_callable=AsyncMock,
            return_value={"requires_approval": True, "delete": {}},
        ) as preview:
            await executor.execute("delete_group", {"entity_id": "light.g"})
        preview.assert_awaited_once()


# ── Pure helpers ─────────────────────────────────────────────────────


class TestInferGroupType:
    @pytest.mark.parametrize(
        ("members", "expected"),
        [
            (["light.a", "light.b"], "light"),
            (["switch.a"], "switch"),
            (["cover.a", "cover.b"], "cover"),
            # HA's sensor group deliberately spans three numeric domains.
            (["sensor.a", "number.b", "input_number.c"], "sensor"),
            (["number.b"], "sensor"),
        ],
    )
    def test_resolves(self, members: list[str], expected: str) -> None:
        assert gm.infer_group_type(members) == (expected, None)

    def test_mixed_domains_refused_with_guidance(self) -> None:
        group_type, error = gm.infer_group_type(["light.a", "switch.b"])
        assert group_type is None
        assert "one domain only" in error
        assert "light, switch" in error

    def test_ungroupable_domain_refused(self) -> None:
        group_type, error = gm.infer_group_type(["climate.a"])
        assert group_type is None
        assert "climate" in error

    def test_empty_refused(self) -> None:
        assert gm.infer_group_type([])[0] is None


class TestValidateMembers:
    @pytest.mark.asyncio
    async def test_unknown_entity_refused(self, group_home: HomeAssistant) -> None:
        """A hallucinated entity_id would create a permanently-unavailable
        group, so it must be rejected rather than passed to HA."""
        members, error = gm.validate_members(group_home, ["light.lamp", "light.nope"])
        assert members == []
        assert "do not exist" in error
        assert "light.nope" in error

    @pytest.mark.asyncio
    async def test_malformed_refused(self, group_home: HomeAssistant) -> None:
        members, error = gm.validate_members(group_home, ["not-an-entity"])
        assert members == []
        assert "not valid entity_ids" in error.lower()

    @pytest.mark.asyncio
    async def test_dedupes_preserving_order(self, group_home: HomeAssistant) -> None:
        members, error = gm.validate_members(
            group_home, ["light.ceiling", "light.lamp", "light.ceiling"]
        )
        assert error is None
        assert members == ["light.ceiling", "light.lamp"]

    @pytest.mark.asyncio
    async def test_accepts_comma_joined_string(self, group_home: HomeAssistant) -> None:
        """Models sometimes emit a string despite the array schema."""
        members, error = gm.validate_members(group_home, "light.lamp, light.ceiling")
        assert error is None
        assert members == ["light.lamp", "light.ceiling"]

    @pytest.mark.asyncio
    async def test_empty_refused(self, group_home: HomeAssistant) -> None:
        assert gm.validate_members(group_home, [])[1] is not None


# ── Create (real HA flow) ────────────────────────────────────────────


class TestCreateGroup:
    @pytest.mark.asyncio
    async def test_creates_live_light_group(self, group_home: HomeAssistant) -> None:
        result = await _create(group_home)

        assert result["status"] == "created"
        assert result["group_type"] == "light"
        assert result["member_count"] == 2
        entity_id = result["entity_id"]
        # The whole point of the helper route: a domain-typed entity.
        assert entity_id.startswith("light.")
        state = group_home.states.get(entity_id)
        assert state is not None
        assert set(state.attributes["entity_id"]) == {"light.lamp", "light.ceiling"}

    @pytest.mark.asyncio
    async def test_group_state_aggregates_members(self, group_home: HomeAssistant) -> None:
        result = await _create(group_home)
        entity_id = result["entity_id"]
        assert group_home.states.get(entity_id).state == "off"

        group_home.states.async_set("light.lamp", "on")
        await group_home.async_block_till_done()
        # Default mode is "any" — one member on turns the group on.
        assert group_home.states.get(entity_id).state == "on"

    @pytest.mark.asyncio
    async def test_requires_all_members_uses_all_mode(self, group_home: HomeAssistant) -> None:
        result = await _create(group_home, requires_all_members=True)
        entity_id = result["entity_id"]
        group_home.states.async_set("light.lamp", "on")
        await group_home.async_block_till_done()
        # all-mode: one member on is not enough.
        assert group_home.states.get(entity_id).state == "off"

    @pytest.mark.asyncio
    async def test_options_stored_on_entry_not_data(self, group_home: HomeAssistant) -> None:
        """HA's group helper keeps everything in entry.options; entry.data is
        empty. Our update path edits options, so this contract matters."""
        await _create(group_home)
        entry = gm.group_entries(group_home)[0]
        assert entry.data == {}
        assert entry.options["group_type"] == "light"
        assert entry.options["name"] == "Evening Lights"
        assert entry.options["entities"] == ["light.lamp", "light.ceiling"]

    @pytest.mark.asyncio
    async def test_numeric_group_gets_default_statistic(self, group_home: HomeAssistant) -> None:
        """A sensor group's schema REQUIRES ``type``; omitting it would make
        the flow reject the payload."""
        result = await _create(
            group_home,
            name="Indoor Temps",
            entities=["sensor.temp_a", "number.setpoint"],
        )
        assert result["status"] == "created"
        assert result["group_type"] == "sensor"
        entry = gm.group_entries(group_home)[0]
        assert entry.options["type"] == "mean"

    @pytest.mark.asyncio
    async def test_explicit_statistic_honoured(self, group_home: HomeAssistant) -> None:
        await _create(group_home, name="Max Temp", entities=["sensor.temp_a"], statistic="max")
        assert gm.group_entries(group_home)[0].options["type"] == "max"

    @pytest.mark.asyncio
    async def test_bad_statistic_refused(self, group_home: HomeAssistant) -> None:
        result = await _create(
            group_home, name="X", entities=["sensor.temp_a"], statistic="average"
        )
        assert "statistic must be one of" in result["error"]
        assert gm.group_entries(group_home) == []

    @pytest.mark.asyncio
    async def test_mixed_domain_refused_before_flow(self, group_home: HomeAssistant) -> None:
        result = await _create(group_home, entities=["light.lamp", "switch.plug"])
        assert "one domain only" in result["error"]
        assert gm.group_entries(group_home) == []

    @pytest.mark.asyncio
    async def test_duplicate_name_refused(self, group_home: HomeAssistant) -> None:
        """Group helpers carry no unique_id, so HA would create a second
        same-named group and make every resolve-by-name ambiguous."""
        await _create(group_home)
        result = await _create(group_home, entities=["light.desk"])
        assert "already exists" in result["error"]
        assert "update_group" in result["error"]
        assert len(gm.group_entries(group_home)) == 1

    @pytest.mark.asyncio
    async def test_duplicate_name_check_is_case_insensitive(
        self, group_home: HomeAssistant
    ) -> None:
        await _create(group_home)
        result = await _create(group_home, name="evening lights", entities=["light.desk"])
        assert "already exists" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_name_refused(self, group_home: HomeAssistant) -> None:
        assert "name is required" in (await _create(group_home, name="   "))["error"]

    @pytest.mark.asyncio
    async def test_group_type_mismatch_refused(self, group_home: HomeAssistant) -> None:
        result = await _create(group_home, group_type="switch")
        assert "does not match the members" in result["error"]

    @pytest.mark.asyncio
    async def test_hide_members_hides_them(self, group_home: HomeAssistant) -> None:
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(group_home)
        entry = registry.async_get_or_create("light", "test", "lamp-unique")
        group_home.states.async_set(entry.entity_id, "off")
        await group_home.async_block_till_done()

        await _create(group_home, name="Hidden", entities=[entry.entity_id], hide_members=True)
        assert registry.async_get(entry.entity_id).hidden_by is not None

    @pytest.mark.asyncio
    async def test_no_dangling_flow_when_refused_before_flow_starts(
        self, group_home: HomeAssistant
    ) -> None:
        await _create(group_home, entities=["light.lamp", "switch.plug"])
        assert group_home.config_entries.flow.async_progress_by_handler("group") == []

    @pytest.mark.asyncio
    async def test_no_dangling_flow_when_ha_cannot_group_the_type(
        self, group_home: HomeAssistant
    ) -> None:
        """Bails out AFTER async_init, so it exercises the cleanup path a
        pre-flow refusal never reaches. Without the abort, the user is left
        with an orphaned 'Group' flow in Settings to dismiss by hand."""
        with patch.object(
            group_home.config_entries.flow,
            "async_init",
            return_value={
                "type": "menu",
                "flow_id": (
                    await group_home.config_entries.flow.async_init(
                        "group", context={"source": "user"}
                    )
                )["flow_id"],
                # HA claims it cannot group lights.
                "menu_options": ["switch"],
            },
        ):
            result = await _tool_create_group(
                group_home, {"name": "Nope", "entities": ["light.lamp"]}
            )

        assert "cannot group 'light'" in result["error"]
        assert group_home.config_entries.flow.async_progress_by_handler("group") == []
        assert gm.group_entries(group_home) == []

    @pytest.mark.asyncio
    async def test_no_dangling_flow_when_ha_rejects_the_payload(
        self, group_home: HomeAssistant
    ) -> None:
        """A voluptuous rejection at the form step must also clean up."""
        with patch.object(
            group_home.config_entries.flow,
            "async_configure",
            side_effect=vol.Invalid("bad payload"),
        ):
            result = await _tool_create_group(
                group_home, {"name": "Nope", "entities": ["light.lamp"]}
            )

        assert "rejected the group" in result["error"]
        assert group_home.config_entries.flow.async_progress_by_handler("group") == []
        assert gm.group_entries(group_home) == []


# ── List ─────────────────────────────────────────────────────────────


class TestListGroups:
    @pytest.mark.asyncio
    async def test_empty_home(self, group_home: HomeAssistant) -> None:
        assert await _tool_list_groups(group_home, {}) == {"groups": [], "count": 0}

    @pytest.mark.asyncio
    async def test_reports_members_and_ids(self, group_home: HomeAssistant) -> None:
        created = await _create(group_home)
        result = await _tool_list_groups(group_home, {})

        assert result["count"] == 1
        info = result["groups"][0]
        assert info["name"] == "Evening Lights"
        assert info["group_type"] == "light"
        assert info["entity_id"] == created["entity_id"]
        assert info["entry_id"] == created["entry_id"]
        assert info["member_count"] == 2
        assert info["state"] == "off"

    @pytest.mark.asyncio
    async def test_filter_by_group_type(self, group_home: HomeAssistant) -> None:
        await _create(group_home)
        await _create(group_home, name="Plugs", entities=["switch.plug"])

        assert (await _tool_list_groups(group_home, {"group_type": "light"}))["count"] == 1
        assert (await _tool_list_groups(group_home, {"group_type": "switch"}))["count"] == 1
        assert (await _tool_list_groups(group_home, {}))["count"] == 2

    @pytest.mark.asyncio
    async def test_yaml_groups_reported_read_only(self, hass: HomeAssistant) -> None:
        """A legacy YAML group can't be edited via the helper flow. Surfacing
        it read-only stops the model from reporting it missing and creating a
        confusing duplicate."""
        assert await async_setup_component(
            hass, "group", {"group": {"downstairs": {"entities": ["light.lamp"]}}}
        )
        await hass.async_block_till_done()

        result = await _tool_list_groups(hass, {})
        assert result["count"] == 0
        assert "group.downstairs" in result["read_only_yaml_groups"]

    @pytest.mark.asyncio
    async def test_helper_group_not_listed_as_yaml(self, group_home: HomeAssistant) -> None:
        await _create(group_home)
        assert "read_only_yaml_groups" not in await _tool_list_groups(group_home, {})


# ── Update ───────────────────────────────────────────────────────────


class TestUpdateGroup:
    @pytest.mark.asyncio
    async def test_add_entities_delta(self, group_home: HomeAssistant) -> None:
        created = await _create(group_home)
        result = await _tool_update_group(
            group_home,
            {"entity_id": created["entity_id"], "add_entities": ["light.desk"]},
        )
        await group_home.async_block_till_done()

        assert result["status"] == "updated"
        assert result["added"] == ["light.desk"]
        assert result["member_count"] == 3
        # The LIVE entity must track the new member, not just the stored options.
        state = group_home.states.get(created["entity_id"])
        assert set(state.attributes["entity_id"]) == {
            "light.lamp",
            "light.ceiling",
            "light.desk",
        }

    @pytest.mark.asyncio
    async def test_new_member_affects_group_state(self, group_home: HomeAssistant) -> None:
        """Proves the reload actually rebound the tracker: a member added after
        creation must be able to drive the group's state."""
        created = await _create(group_home)
        await _tool_update_group(
            group_home,
            {"entity_id": created["entity_id"], "add_entities": ["light.desk"]},
        )
        await group_home.async_block_till_done()

        group_home.states.async_set("light.desk", "on")
        await group_home.async_block_till_done()
        assert group_home.states.get(created["entity_id"]).state == "on"

    @pytest.mark.asyncio
    async def test_remove_entities_delta(self, group_home: HomeAssistant) -> None:
        created = await _create(group_home)
        result = await _tool_update_group(
            group_home,
            {"entity_id": created["entity_id"], "remove_entities": ["light.lamp"]},
        )
        await group_home.async_block_till_done()

        assert result["removed"] == ["light.lamp"]
        assert result["members"] == ["light.ceiling"]

    @pytest.mark.asyncio
    async def test_replace_member_list(self, group_home: HomeAssistant) -> None:
        created = await _create(group_home)
        result = await _tool_update_group(
            group_home,
            {"entity_id": created["entity_id"], "entities": ["light.desk"]},
        )
        assert result["members"] == ["light.desk"]
        assert result["removed"] == ["light.lamp", "light.ceiling"]

    @pytest.mark.asyncio
    async def test_rename(self, group_home: HomeAssistant) -> None:
        created = await _create(group_home)
        result = await _tool_update_group(
            group_home, {"entity_id": created["entity_id"], "new_name": "Night Lights"}
        )
        assert result["name"] == "Night Lights"
        entry = group_home.config_entries.async_get_entry(created["entry_id"])
        assert entry.options["name"] == "Night Lights"
        assert entry.title == "Night Lights"

    @pytest.mark.asyncio
    async def test_resolve_by_name(self, group_home: HomeAssistant) -> None:
        await _create(group_home)
        result = await _tool_update_group(
            group_home, {"group_name": "evening lights", "add_entities": ["light.desk"]}
        )
        assert result["status"] == "updated"

    @pytest.mark.asyncio
    async def test_ambiguous_name_refused(self, group_home: HomeAssistant) -> None:
        await _create(group_home, name="Upstairs Lights")
        await _create(group_home, name="Downstairs Lights", entities=["light.desk"])
        result = await _tool_update_group(
            group_home, {"group_name": "lights", "add_entities": ["light.desk"]}
        )
        assert "matches several groups" in result["error"]

    @pytest.mark.asyncio
    async def test_adding_other_domain_refused(self, group_home: HomeAssistant) -> None:
        """Adding a switch to a light group makes the resulting set mixed, so
        the mixed-domain guard catches it before the domain-change guard."""
        created = await _create(group_home)
        result = await _tool_update_group(
            group_home,
            {"entity_id": created["entity_id"], "add_entities": ["switch.plug"]},
        )
        assert "one domain only" in result["error"]

    @pytest.mark.asyncio
    async def test_cannot_swap_domain_wholesale(self, group_home: HomeAssistant) -> None:
        """Replacing every light with a switch yields a homogeneous but WRONG
        domain — a group helper's type is fixed at creation."""
        created = await _create(group_home)
        result = await _tool_update_group(
            group_home,
            {"entity_id": created["entity_id"], "entities": ["switch.plug"]},
        )
        assert "cannot change domain" in result["error"]
        # The original membership must survive a rejected update.
        entry = group_home.config_entries.async_get_entry(created["entry_id"])
        assert entry.options["entities"] == ["light.lamp", "light.ceiling"]

    @pytest.mark.asyncio
    async def test_cannot_empty_the_group(self, group_home: HomeAssistant) -> None:
        created = await _create(group_home)
        result = await _tool_update_group(
            group_home,
            {
                "entity_id": created["entity_id"],
                "remove_entities": ["light.lamp", "light.ceiling"],
            },
        )
        assert "at least one member" in result["error"]

    @pytest.mark.asyncio
    async def test_removing_non_member_refused(self, group_home: HomeAssistant) -> None:
        created = await _create(group_home)
        result = await _tool_update_group(
            group_home,
            {"entity_id": created["entity_id"], "remove_entities": ["light.desk"]},
        )
        assert "Not members of this group" in result["error"]

    @pytest.mark.asyncio
    async def test_unknown_new_member_refused(self, group_home: HomeAssistant) -> None:
        created = await _create(group_home)
        result = await _tool_update_group(
            group_home,
            {"entity_id": created["entity_id"], "add_entities": ["light.ghost"]},
        )
        assert "do not exist" in result["error"]

    @pytest.mark.asyncio
    async def test_no_op_refused(self, group_home: HomeAssistant) -> None:
        created = await _create(group_home)
        result = await _tool_update_group(group_home, {"entity_id": created["entity_id"]})
        assert "Nothing to change" in result["error"]

    @pytest.mark.asyncio
    async def test_rename_collision_refused(self, group_home: HomeAssistant) -> None:
        await _create(group_home)
        second = await _create(group_home, name="Plugs", entities=["switch.plug"])
        result = await _tool_update_group(
            group_home, {"entity_id": second["entity_id"], "new_name": "Evening Lights"}
        )
        assert "already named" in result["error"]

    @pytest.mark.asyncio
    async def test_unknown_group_refused(self, group_home: HomeAssistant) -> None:
        await _create(group_home)
        result = await _tool_update_group(
            group_home, {"entity_id": "light.nope", "add_entities": ["light.desk"]}
        )
        assert "No group helper found" in result["error"]

    @pytest.mark.asyncio
    async def test_yaml_group_update_explains_why_not(self, hass: HomeAssistant) -> None:
        assert await async_setup_component(
            hass, "group", {"group": {"downstairs": {"entities": ["light.lamp"]}}}
        )
        await hass.async_block_till_done()
        hass.states.async_set("light.desk", "off")
        await _tool_create_group(hass, {"name": "Helper", "entities": ["light.desk"]})
        await hass.async_block_till_done()

        result = await _tool_update_group(
            hass, {"entity_id": "group.downstairs", "add_entities": ["light.desk"]}
        )
        assert "YAML-defined group" in result["error"]


class TestMembersStoredAsRegistryIds:
    """HA's entity selector validates with ``cv.entity_id_or_uuid``.

    A group built in the UI can therefore persist registry ids in
    ``options["entities"]``, so every membership comparison has to resolve
    them or an ordinary edit fails on a group we did not create.
    """

    async def _uuid_group(self, hass: HomeAssistant) -> tuple[str, list[str], list[str]]:
        """A group whose stored members are registry ids, as the UI writes them."""
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(hass)
        entries = [registry.async_get_or_create("light", "test", f"reg-{n}") for n in ("a", "b")]
        for reg in entries:
            hass.states.async_set(reg.entity_id, "off")
        await hass.async_block_till_done()

        entity_ids = [reg.entity_id for reg in entries]
        created = await _create(hass, name="UI Group", entities=entity_ids)
        entry = gm.group_entries(hass)[0]
        uuids = [reg.id for reg in entries]
        hass.config_entries.async_update_entry(entry, options={**entry.options, "entities": uuids})
        await hass.async_block_till_done()
        return created["entity_id"], entity_ids, uuids

    @pytest.mark.asyncio
    async def test_rename_succeeds(self, group_home: HomeAssistant) -> None:
        """infer_group_type() would otherwise read the uuid as a domain."""
        entity_id, _, _ = await self._uuid_group(group_home)

        result = await _tool_update_group(
            group_home, {"entity_id": entity_id, "new_name": "Renamed"}
        )
        assert result["status"] == "updated"
        assert result["name"] == "Renamed"

    @pytest.mark.asyncio
    async def test_remove_by_entity_id_matches_a_stored_uuid(
        self, group_home: HomeAssistant
    ) -> None:
        entity_id, entity_ids, uuids = await self._uuid_group(group_home)

        result = await _tool_update_group(
            group_home, {"entity_id": entity_id, "remove_entities": [entity_ids[0]]}
        )
        assert result["status"] == "updated"
        assert gm.group_entries(group_home)[0].options["entities"] == [uuids[1]]

    @pytest.mark.asyncio
    async def test_stored_uuid_form_is_preserved(self, group_home: HomeAssistant) -> None:
        """A registry id keeps tracking an entity across an entity_id rename,
        so an edit must not normalise it into the weaker form."""
        entity_id, entity_ids, uuids = await self._uuid_group(group_home)

        await _tool_update_group(group_home, {"entity_id": entity_id, "new_name": "Renamed"})
        await group_home.async_block_till_done()

        assert gm.group_entries(group_home)[0].options["entities"] == uuids

    @pytest.mark.asyncio
    async def test_replacing_with_the_same_members_changes_nothing(
        self, group_home: HomeAssistant
    ) -> None:
        """Replacement names entity_ids while storage holds uuids. Compared
        raw, every member reads as both removed and added — and the removals
        unhide members the group still holds."""
        from homeassistant.helpers import entity_registry as er

        entity_id, entity_ids, uuids = await self._uuid_group(group_home)
        registry = er.async_get(group_home)
        for member in entity_ids:
            registry.async_update_entity(member, hidden_by=er.RegistryEntryHider.INTEGRATION)

        result = await _tool_update_group(
            group_home, {"entity_id": entity_id, "entities": entity_ids}
        )
        await group_home.async_block_till_done()

        # Identical membership is a real no-op once the forms line up, exactly
        # as it already was for an entity_id-stored group.
        assert "Nothing to change" in result["error"]
        assert all(registry.async_get(m).hidden_by is not None for m in entity_ids)

    @pytest.mark.asyncio
    async def test_list_groups_reports_resolved_members(self, group_home: HomeAssistant) -> None:
        """A uuid is not something the LLM can quote or feed to another tool."""
        _, entity_ids, _ = await self._uuid_group(group_home)

        listed = await _tool_list_groups(group_home, {})

        assert listed["groups"][0]["members"] == entity_ids

    @pytest.mark.asyncio
    async def test_adding_an_existing_member_by_entity_id_does_not_duplicate(
        self, group_home: HomeAssistant
    ) -> None:
        """Raw-compared, the entity_id does not match its stored uuid and the
        same entity is appended a second time."""
        entity_id, entity_ids, uuids = await self._uuid_group(group_home)

        await _tool_update_group(
            group_home, {"entity_id": entity_id, "add_entities": [entity_ids[0]]}
        )
        await group_home.async_block_till_done()

        assert gm.group_entries(group_home)[0].options["entities"] == uuids

    @pytest.mark.asyncio
    async def test_adding_a_new_member_keeps_existing_uuids(
        self, group_home: HomeAssistant
    ) -> None:
        entity_id, _, uuids = await self._uuid_group(group_home)

        result = await _tool_update_group(
            group_home, {"entity_id": entity_id, "add_entities": ["light.desk"]}
        )
        await group_home.async_block_till_done()

        assert result["member_count"] == 3
        assert gm.group_entries(group_home)[0].options["entities"] == [*uuids, "light.desk"]

    @pytest.mark.asyncio
    async def test_replacement_keeps_the_stored_form_of_retained_members(
        self, group_home: HomeAssistant
    ) -> None:
        """Replacing names entity_ids; rewriting a retained member into that
        form would trade a rename-proof reference for a fragile one."""
        entity_id, entity_ids, uuids = await self._uuid_group(group_home)

        await _tool_update_group(
            group_home, {"entity_id": entity_id, "entities": [entity_ids[0], "light.desk"]}
        )
        await group_home.async_block_till_done()

        # Retained member keeps its uuid; the genuinely new one is stored as given.
        assert gm.group_entries(group_home)[0].options["entities"] == [uuids[0], "light.desk"]

    async def _group_with_a_stale_member(self, hass: HomeAssistant) -> tuple[str, list[str], str]:
        """A uuid-stored group, one of whose entities has since been deleted."""
        from homeassistant.helpers import entity_registry as er

        entity_id, entity_ids, uuids = await self._uuid_group(hass)
        er.async_get(hass).async_remove(entity_ids[1])
        await hass.async_block_till_done()
        return entity_id, entity_ids, uuids[1]

    @pytest.mark.asyncio
    async def test_a_stale_stored_id_blocks_a_rename(self, group_home: HomeAssistant) -> None:
        """The saved list is re-validated on setup, so writing a stale id back
        leaves the group entity unavailable — refuse rather than brick it."""
        entity_id, _, stale = await self._group_with_a_stale_member(group_home)

        result = await _tool_update_group(
            group_home, {"entity_id": entity_id, "new_name": "Renamed"}
        )

        assert "no longer exists" in result["error"]
        # The exact string to pass to remove_entities — it has no entity_id left.
        assert stale in result["error"]

    @pytest.mark.asyncio
    async def test_the_group_entity_survives_a_refused_update(
        self, group_home: HomeAssistant
    ) -> None:
        """The reload is what breaks it, so refusing must leave it alone —
        note the config entry stays LOADED either way."""
        entity_id, _, _ = await self._group_with_a_stale_member(group_home)
        before = group_home.states.get(entity_id).state

        await _tool_update_group(group_home, {"entity_id": entity_id, "new_name": "Renamed"})
        await group_home.async_block_till_done()

        assert group_home.states.get(entity_id).state == before != "unavailable"

    @pytest.mark.asyncio
    async def test_removing_the_stale_id_unblocks_the_group(
        self, group_home: HomeAssistant
    ) -> None:
        """The escape hatch the error points at has to actually work."""
        entity_id, _, stale = await self._group_with_a_stale_member(group_home)

        removed = await _tool_update_group(
            group_home, {"entity_id": entity_id, "remove_entities": [stale]}
        )
        await group_home.async_block_till_done()
        renamed = await _tool_update_group(
            group_home, {"entity_id": entity_id, "new_name": "Renamed"}
        )
        await group_home.async_block_till_done()

        assert removed["status"] == "updated"
        assert renamed["status"] == "updated"
        assert group_home.states.get(entity_id).state != "unavailable"

    @pytest.mark.asyncio
    async def test_replacing_the_list_without_the_stale_id_also_works(
        self, group_home: HomeAssistant
    ) -> None:
        entity_id, entity_ids, stale = await self._group_with_a_stale_member(group_home)

        result = await _tool_update_group(
            group_home, {"entity_id": entity_id, "entities": [entity_ids[0]]}
        )
        await group_home.async_block_till_done()

        assert result["status"] == "updated"
        assert stale not in gm.group_entries(group_home)[0].options["entities"]

    @pytest.mark.asyncio
    async def test_result_reports_resolved_entity_ids(self, group_home: HomeAssistant) -> None:
        """The caller is an LLM that quotes these back — a uuid means nothing."""
        entity_id, entity_ids, _ = await self._uuid_group(group_home)

        result = await _tool_update_group(
            group_home, {"entity_id": entity_id, "remove_entities": [entity_ids[0]]}
        )
        assert result["members"] == [entity_ids[1]]
        assert result["removed"] == [entity_ids[0]]


class TestLargeGroupListing:
    """A big group must survive the executor's result truncation.

    ToolExecutor trims by popping items off the longest list it can find, and
    ``_find_longest_list`` does not descend into a list of dicts — so an
    oversized ``groups[0]`` gets dropped whole rather than having its members
    shortened, leaving the caller a bare ``count``.
    """

    @staticmethod
    async def _big_group(hass: HomeAssistant, count: int) -> list[str]:
        members = [f"light.living_room_downlight_number_{i:03d}" for i in range(count)]
        for member in members:
            hass.states.async_set(member, "off")
        await hass.async_block_till_done()
        await _create(hass, name="All Downlights", entities=members)
        await hass.async_block_till_done()
        return members

    @pytest.mark.asyncio
    async def test_group_record_survives_the_executor(self, group_home: HomeAssistant) -> None:
        await self._big_group(group_home, 600)

        result = await _make_executor(group_home, is_admin=True).execute("list_groups", {})

        assert result["groups"], "the whole group record was dropped"
        group = result["groups"][0]
        assert group["name"] == "All Downlights"
        assert group["entity_id"] == "light.all_downlights"

    @pytest.mark.asyncio
    async def test_member_count_stays_exact_and_omissions_are_reported(
        self, group_home: HomeAssistant
    ) -> None:
        members = await self._big_group(group_home, 600)

        result = await _make_executor(group_home, is_admin=True).execute("list_groups", {})
        group = result["groups"][0]

        assert group["member_count"] == len(members)
        assert len(group["members"]) == gm._MAX_LISTED_MEMBERS
        assert group["members_omitted"] == len(members) - gm._MAX_LISTED_MEMBERS

    @pytest.mark.asyncio
    async def test_a_small_group_is_reported_whole(self, group_home: HomeAssistant) -> None:
        await _create(group_home)

        result = await _tool_list_groups(group_home, {})
        group = result["groups"][0]

        assert group["members"] == ["light.lamp", "light.ceiling"]
        assert "members_omitted" not in group


class TestNonNumericSensorMembers:
    """A sensor group aggregates numbers; a text member cannot take part.

    HA builds these with ``ignore_non_numeric`` False, which does not refuse —
    ``SensorGroup`` drops the unparseable member and logs a warning, so the
    group reports success with a member that contributes nothing, and an
    all-text group publishes ``unknown``.
    """

    @pytest.mark.asyncio
    async def test_create_refuses_a_text_member(self, group_home: HomeAssistant) -> None:
        group_home.states.async_set("sensor.washer_status", "running")
        await group_home.async_block_till_done()

        result = await _create(
            group_home, name="Mixed", entities=["sensor.temp_a", "sensor.washer_status"]
        )

        assert "report text" in result["error"]
        assert "sensor.washer_status" in result["error"]
        assert gm.group_entries(group_home) == []

    @pytest.mark.asyncio
    async def test_create_refuses_an_all_text_group(self, group_home: HomeAssistant) -> None:
        group_home.states.async_set("sensor.washer_status", "running")
        group_home.states.async_set("sensor.dryer_status", "idle")
        await group_home.async_block_till_done()

        result = await _create(
            group_home,
            name="Appliances",
            entities=["sensor.washer_status", "sensor.dryer_status"],
        )

        assert "report text" in result["error"]
        assert gm.group_entries(group_home) == []

    @pytest.mark.asyncio
    async def test_update_refuses_adding_a_text_member(self, group_home: HomeAssistant) -> None:
        group_home.states.async_set("sensor.washer_status", "running")
        await group_home.async_block_till_done()
        created = await _create(group_home, name="Temps", entities=["sensor.temp_a"])

        result = await _tool_update_group(
            group_home,
            {"entity_id": created["entity_id"], "add_entities": ["sensor.washer_status"]},
        )

        assert "report text" in result["error"]
        assert gm.group_entries(group_home)[0].options["entities"] == ["sensor.temp_a"]

    @pytest.mark.asyncio
    async def test_an_offline_numeric_sensor_is_still_groupable(
        self, group_home: HomeAssistant
    ) -> None:
        """A numeric sensor reads unavailable/unknown whenever its device is
        offline — grouping must not depend on a flat battery."""
        group_home.states.async_set("sensor.temp_c", "unavailable")
        group_home.states.async_set("sensor.temp_d", "unknown")
        await group_home.async_block_till_done()

        result = await _create(
            group_home, name="Temps", entities=["sensor.temp_c", "sensor.temp_d"]
        )

        assert result["status"] == "created"


class TestManualHideProvenance:
    """A hide the user applied by hand is theirs, not the integration's.

    Overwriting it with an integration hide looks like a no-op — the entity
    stays hidden — but it transfers ownership, so a later removal or delete
    releases a hide the user set for their own reasons.
    """

    async def _hidden_group_and_user_hidden_light(self, hass: HomeAssistant) -> tuple[str, str]:
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(hass)
        member = registry.async_get_or_create("light", "test", "prov-a")
        outsider = registry.async_get_or_create("light", "test", "prov-b")
        for reg in (member, outsider):
            hass.states.async_set(reg.entity_id, "off")
        await hass.async_block_till_done()

        created = await _create(hass, name="Hidden", entities=[member.entity_id], hide_members=True)
        registry.async_update_entity(outsider.entity_id, hidden_by=er.RegistryEntryHider.USER)
        return created["entity_id"], outsider.entity_id

    @pytest.mark.asyncio
    async def test_creating_a_hidden_group_keeps_the_user_hide(
        self, group_home: HomeAssistant
    ) -> None:
        """Creation runs HA's real flow, whose hide hook overwrites hidden_by
        unconditionally — provenance has to be put back afterwards."""
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(group_home)
        member = registry.async_get_or_create("light", "test", "create-prov")
        group_home.states.async_set(member.entity_id, "off")
        await group_home.async_block_till_done()
        registry.async_update_entity(member.entity_id, hidden_by=er.RegistryEntryHider.USER)

        await _create(
            group_home,
            name="Hidden",
            entities=[member.entity_id, "light.desk"],
            hide_members=True,
        )
        await group_home.async_block_till_done()

        assert registry.async_get(member.entity_id).hidden_by == er.RegistryEntryHider.USER

    @pytest.mark.asyncio
    async def test_a_user_hide_survives_the_whole_create_remove_cycle(
        self, group_home: HomeAssistant
    ) -> None:
        """The loss only becomes visible at removal: once stored as
        INTEGRATION, the unhide takes it away entirely."""
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(group_home)
        member = registry.async_get_or_create("light", "test", "cycle-prov")
        group_home.states.async_set(member.entity_id, "off")
        await group_home.async_block_till_done()
        registry.async_update_entity(member.entity_id, hidden_by=er.RegistryEntryHider.USER)

        created = await _create(
            group_home,
            name="Hidden",
            entities=[member.entity_id, "light.desk"],
            hide_members=True,
        )
        await _tool_update_group(
            group_home,
            {"entity_id": created["entity_id"], "remove_entities": [member.entity_id]},
        )
        await group_home.async_block_till_done()

        assert registry.async_get(member.entity_id).hidden_by == er.RegistryEntryHider.USER

    @pytest.mark.asyncio
    async def test_adding_to_a_hidden_group_keeps_the_user_hide(
        self, group_home: HomeAssistant
    ) -> None:
        from homeassistant.helpers import entity_registry as er

        group, outsider = await self._hidden_group_and_user_hidden_light(group_home)

        await _tool_update_group(group_home, {"entity_id": group, "add_entities": [outsider]})
        await group_home.async_block_till_done()

        registry = er.async_get(group_home)
        assert registry.async_get(outsider).hidden_by == er.RegistryEntryHider.USER

    @pytest.mark.asyncio
    async def test_removal_does_not_release_a_user_hide(self, group_home: HomeAssistant) -> None:
        """The downgrade only shows up here: once stored as INTEGRATION, the
        removal unhide takes it away entirely."""
        from homeassistant.helpers import entity_registry as er

        group, outsider = await self._hidden_group_and_user_hidden_light(group_home)
        await _tool_update_group(group_home, {"entity_id": group, "add_entities": [outsider]})
        await group_home.async_block_till_done()

        await _tool_update_group(group_home, {"entity_id": group, "remove_entities": [outsider]})
        await group_home.async_block_till_done()

        registry = er.async_get(group_home)
        assert registry.async_get(outsider).hidden_by == er.RegistryEntryHider.USER


class TestHiddenMemberOverlap:
    """Hiding is a property of the entity, not of one membership.

    An entity can belong to several hidden groups, so dropping it from one
    must not reveal it while another still lists it.
    """

    @staticmethod
    async def _light(hass: HomeAssistant, slug: str) -> str:
        from homeassistant.helpers import entity_registry as er

        entry = er.async_get(hass).async_get_or_create("light", "test", f"{slug}-unique")
        hass.states.async_set(entry.entity_id, "off")
        await hass.async_block_till_done()
        return entry.entity_id

    @staticmethod
    def _hidden_by(hass: HomeAssistant, entity_id: str) -> Any:
        from homeassistant.helpers import entity_registry as er

        return er.async_get(hass).async_get(entity_id).hidden_by

    async def _two_groups(
        self, hass: HomeAssistant, *, second_hides: bool
    ) -> tuple[dict[str, Any], str]:
        """Two groups sharing one member; only the first is guaranteed hidden."""
        shared = await self._light(hass, "shared")
        other = await self._light(hass, "other")
        first = await _create(hass, name="Group A", entities=[shared, other], hide_members=True)
        await _create(hass, name="Group B", entities=[shared], hide_members=second_hides)
        await hass.async_block_till_done()
        return first, shared

    @pytest.mark.asyncio
    async def test_stays_hidden_when_another_hidden_group_claims_it(
        self, group_home: HomeAssistant
    ) -> None:
        first, shared = await self._two_groups(group_home, second_hides=True)

        await _tool_update_group(
            group_home, {"entity_id": first["entity_id"], "remove_entities": [shared]}
        )
        await group_home.async_block_till_done()

        assert self._hidden_by(group_home, shared) is not None

    @pytest.mark.asyncio
    async def test_unhidden_when_the_other_group_does_not_hide(
        self, group_home: HomeAssistant
    ) -> None:
        first, shared = await self._two_groups(group_home, second_hides=False)

        await _tool_update_group(
            group_home, {"entity_id": first["entity_id"], "remove_entities": [shared]}
        )
        await group_home.async_block_till_done()

        assert self._hidden_by(group_home, shared) is None

    @pytest.mark.asyncio
    async def test_delete_keeps_hide_claimed_by_a_surviving_group(
        self, group_home: HomeAssistant
    ) -> None:
        """group.async_remove_entry unhides members with no regard for other
        groups, so deleting one of two overlapping hidden groups would leave
        the survivor with a visible member."""
        first, shared = await self._two_groups(group_home, second_hides=True)
        entry = next(e for e in gm.group_entries(group_home) if e.options.get("name") == "Group A")

        await _tool_delete_group(group_home, {"entry_id": entry.entry_id, "confirmed": True})
        await group_home.async_block_till_done()

        assert self._hidden_by(group_home, shared) is not None

    @pytest.mark.asyncio
    async def test_delete_unhides_when_no_group_survives(self, group_home: HomeAssistant) -> None:
        first, shared = await self._two_groups(group_home, second_hides=False)
        entry = next(e for e in gm.group_entries(group_home) if e.options.get("name") == "Group A")

        await _tool_delete_group(group_home, {"entry_id": entry.entry_id, "confirmed": True})
        await group_home.async_block_till_done()

        assert self._hidden_by(group_home, shared) is None

    @pytest.mark.asyncio
    async def test_delete_does_not_downgrade_a_manual_hide(self, group_home: HomeAssistant) -> None:
        """HA leaves a user-hidden member alone, so restoring an INTEGRATION
        hide over it would turn a manual hide into one a later removal clears."""
        from homeassistant.helpers import entity_registry as er

        first, shared = await self._two_groups(group_home, second_hides=True)
        registry = er.async_get(group_home)
        registry.async_update_entity(shared, hidden_by=er.RegistryEntryHider.USER)
        entry = next(e for e in gm.group_entries(group_home) if e.options.get("name") == "Group A")

        await _tool_delete_group(group_home, {"entry_id": entry.entry_id, "confirmed": True})
        await group_home.async_block_till_done()

        assert self._hidden_by(group_home, shared) == er.RegistryEntryHider.USER

    @pytest.mark.asyncio
    async def test_unhidden_when_no_other_group_claims_it(self, group_home: HomeAssistant) -> None:
        shared = await self._light(group_home, "solo")
        other = await self._light(group_home, "solo-two")
        created = await _create(
            group_home, name="Only Group", entities=[shared, other], hide_members=True
        )

        await _tool_update_group(
            group_home, {"entity_id": created["entity_id"], "remove_entities": [shared]}
        )
        await group_home.async_block_till_done()

        assert self._hidden_by(group_home, shared) is None


class TestStatisticOnNonNumericGroup:
    """statistic is ignored off the sensor type, not refused.

    Only a sensor group holds a number, so "mean of two lights" is not a
    request a user can make and there is no intent to discard. Models
    volunteer the option from the enum, and refusing it turned an ordinary
    "group my two lights" into a dead end.
    """

    @pytest.mark.asyncio
    async def test_statistic_ignored_for_light_group(self, group_home: HomeAssistant) -> None:
        result = await _create(group_home, statistic="max")
        assert result["status"] == "created"
        assert "type" not in gm.group_entries(group_home)[0].options

    @pytest.mark.asyncio
    async def test_statistic_ignored_for_switch_group(self, group_home: HomeAssistant) -> None:
        result = await _create(group_home, name="Plugs", entities=["switch.plug"], statistic="mean")
        assert result["status"] == "created"
        assert "type" not in gm.group_entries(group_home)[0].options

    @pytest.mark.asyncio
    async def test_unknown_statistic_ignored_for_light_group(
        self, group_home: HomeAssistant
    ) -> None:
        """The value check runs after the drop, so an ignored field cannot error."""
        result = await _create(group_home, statistic="average")
        assert result["status"] == "created"
        assert "type" not in gm.group_entries(group_home)[0].options

    @pytest.mark.asyncio
    async def test_statistic_still_allowed_for_numeric_group(
        self, group_home: HomeAssistant
    ) -> None:
        result = await _create(
            group_home, name="Temps", entities=["sensor.temp_a"], statistic="max"
        )
        assert result["status"] == "created"
        assert gm.group_entries(group_home)[0].options["type"] == "max"


class TestSelfReferentialMembership:
    """A group listing its own entity tracks its own state and can loop.

    HA's options flow makes this unrepresentable via
    entity_selector_without_own_entities; we bypass that flow, so the guard
    lives in async_update_group.
    """

    @pytest.mark.asyncio
    async def test_add_self_rejected(self, group_home: HomeAssistant) -> None:
        created = await _create(group_home)
        result = await _tool_update_group(
            group_home,
            {"entity_id": created["entity_id"], "add_entities": [created["entity_id"]]},
        )
        assert "cannot contain itself" in result["error"]
        entry = group_home.config_entries.async_get_entry(created["entry_id"])
        assert created["entity_id"] not in entry.options["entities"]

    @pytest.mark.asyncio
    async def test_replace_with_self_rejected(self, group_home: HomeAssistant) -> None:
        created = await _create(group_home)
        result = await _tool_update_group(
            group_home,
            {
                "entity_id": created["entity_id"],
                "entities": ["light.desk", created["entity_id"]],
            },
        )
        assert "cannot contain itself" in result["error"]
        entry = group_home.config_entries.async_get_entry(created["entry_id"])
        assert entry.options["entities"] == ["light.lamp", "light.ceiling"]

    @pytest.mark.asyncio
    async def test_nesting_another_group_still_allowed(self, group_home: HomeAssistant) -> None:
        """Only SELF-reference is refused — nesting a different group is a
        supported HA pattern and must keep working."""
        inner = await _create(group_home, name="Inner", entities=["light.lamp"])
        outer = await _create(group_home, name="Outer", entities=["light.ceiling"])

        result = await _tool_update_group(
            group_home,
            {"entity_id": outer["entity_id"], "add_entities": [inner["entity_id"]]},
        )
        await group_home.async_block_till_done()
        assert result["status"] == "updated"
        assert inner["entity_id"] in result["members"]


class TestConflictingMembershipModes:
    @pytest.mark.asyncio
    async def test_entities_with_add_rejected(self, group_home: HomeAssistant) -> None:
        """Replacement used to win and the delta was dropped silently, while
        the call still reported success."""
        created = await _create(group_home)
        result = await _tool_update_group(
            group_home,
            {
                "entity_id": created["entity_id"],
                "entities": ["light.lamp"],
                "add_entities": ["light.desk"],
            },
        )
        assert "not both" in result["error"]
        # Nothing applied — neither the replacement nor the ignored delta.
        entry = group_home.config_entries.async_get_entry(created["entry_id"])
        assert entry.options["entities"] == ["light.lamp", "light.ceiling"]

    @pytest.mark.asyncio
    async def test_entities_with_remove_rejected(self, group_home: HomeAssistant) -> None:
        created = await _create(group_home)
        result = await _tool_update_group(
            group_home,
            {
                "entity_id": created["entity_id"],
                "entities": ["light.desk"],
                "remove_entities": ["light.lamp"],
            },
        )
        assert "not both" in result["error"]

    @pytest.mark.asyncio
    async def test_add_and_remove_together_still_allowed(self, group_home: HomeAssistant) -> None:
        """add + remove are the same mode (a delta) and compose fine."""
        created = await _create(group_home)
        result = await _tool_update_group(
            group_home,
            {
                "entity_id": created["entity_id"],
                "add_entities": ["light.desk"],
                "remove_entities": ["light.lamp"],
            },
        )
        await group_home.async_block_till_done()
        assert result["status"] == "updated"
        assert result["members"] == ["light.ceiling", "light.desk"]


class TestYamlOnlyHome:
    """A home whose ONLY groups are YAML-defined.

    The read-only explanation has to survive the no-helpers shortcut, or the
    model reports a group the user can plainly see as nonexistent and offers to
    create a duplicate of it.
    """

    @pytest.fixture
    async def yaml_only(self, hass: HomeAssistant) -> HomeAssistant:
        assert await async_setup_component(
            hass,
            "group",
            {"group": {"downstairs": {"name": "Downstairs", "entities": ["light.lamp"]}}},
        )
        await hass.async_block_till_done()
        hass.states.async_set("light.desk", "off")
        await hass.async_block_till_done()
        assert gm.group_entries(hass) == [], "fixture must have no helper groups"
        return hass

    @pytest.mark.asyncio
    async def test_update_by_entity_id_explains_yaml(self, yaml_only: HomeAssistant) -> None:
        result = await _tool_update_group(
            yaml_only, {"entity_id": "group.downstairs", "add_entities": ["light.desk"]}
        )
        assert "YAML-defined group" in result["error"]
        assert "do NOT create a duplicate" in result["error"]

    @pytest.mark.asyncio
    async def test_update_by_name_explains_yaml(self, yaml_only: HomeAssistant) -> None:
        """The model often has only a name ("add the lamp to the Downstairs
        group"), so the name path needs the same explanation."""
        result = await _tool_update_group(
            yaml_only, {"group_name": "Downstairs", "add_entities": ["light.desk"]}
        )
        assert "YAML-defined group" in result["error"]

    @pytest.mark.asyncio
    async def test_update_by_object_id_name_explains_yaml(self, yaml_only: HomeAssistant) -> None:
        result = await _tool_update_group(
            yaml_only, {"group_name": "downstairs", "add_entities": ["light.desk"]}
        )
        assert "YAML-defined group" in result["error"]

    @pytest.mark.asyncio
    async def test_delete_by_entity_id_explains_yaml(self, yaml_only: HomeAssistant) -> None:
        result = await _preview_delete_group(yaml_only, {"entity_id": "group.downstairs"})
        assert "YAML-defined group" in result["error"]
        assert "requires_approval" not in result

    @pytest.mark.asyncio
    async def test_genuinely_missing_group_still_says_no_helpers(
        self, yaml_only: HomeAssistant
    ) -> None:
        """The no-helpers message must still be reachable — the YAML branch
        must not swallow a real not-found."""
        result = await _tool_update_group(
            yaml_only, {"entity_id": "light.nonexistent", "add_entities": ["light.desk"]}
        )
        assert "no group helpers yet" in result["error"]

    @pytest.mark.asyncio
    async def test_unknown_name_says_no_helpers(self, yaml_only: HomeAssistant) -> None:
        result = await _tool_update_group(
            yaml_only, {"group_name": "Nowhere", "add_entities": ["light.desk"]}
        )
        assert "no group helpers yet" in result["error"]


class TestAllMembersOnUnsupportedType:
    """``all`` mode exists only for binary_sensor/light/switch groups.

    The other config schemas are vol.PREVENT_EXTRA, so the flag can only ever
    be dropped for them — which must be an error, not a silent success.
    """

    @pytest.mark.asyncio
    async def test_create_rejects_it(self, group_home: HomeAssistant) -> None:
        group_home.states.async_set("cover.blind", "open")
        await group_home.async_block_till_done()
        result = await _tool_create_group(
            group_home,
            {"name": "Blinds", "entities": ["cover.blind"], "requires_all_members": True},
        )
        assert "no all-members mode" in result["error"]
        assert "binary_sensor, light, switch" in result["error"]
        assert gm.group_entries(group_home) == []

    @pytest.mark.asyncio
    async def test_create_still_allows_it_for_lights(self, group_home: HomeAssistant) -> None:
        result = await _create(group_home, requires_all_members=True)
        assert result["status"] == "created"
        assert gm.group_entries(group_home)[0].options["all"] is True

    @pytest.mark.asyncio
    async def test_update_rejects_it_instead_of_reporting_success(
        self, group_home: HomeAssistant
    ) -> None:
        """Was the bug: the no-op guard counted the flag as a change, then the
        write silently dropped it and returned status "updated"."""
        group_home.states.async_set("cover.blind", "open")
        await group_home.async_block_till_done()
        created = await _tool_create_group(
            group_home, {"name": "Blinds", "entities": ["cover.blind"]}
        )
        await group_home.async_block_till_done()

        result = await _tool_update_group(
            group_home, {"entity_id": created["entity_id"], "requires_all_members": True}
        )
        assert "status" not in result
        assert "no all-members mode" in result["error"]
        entry = group_home.config_entries.async_get_entry(created["entry_id"])
        assert "all" not in entry.options

    @pytest.mark.asyncio
    async def test_update_rejects_it_alongside_a_valid_change(
        self, group_home: HomeAssistant
    ) -> None:
        """Rejected before anything is written, so the valid part of the
        request must not be half-applied either."""
        group_home.states.async_set("cover.blind", "open")
        group_home.states.async_set("cover.shade", "open")
        await group_home.async_block_till_done()
        created = await _tool_create_group(
            group_home, {"name": "Blinds", "entities": ["cover.blind"]}
        )
        await group_home.async_block_till_done()

        result = await _tool_update_group(
            group_home,
            {
                "entity_id": created["entity_id"],
                "add_entities": ["cover.shade"],
                "requires_all_members": False,
            },
        )
        assert "no all-members mode" in result["error"]
        entry = group_home.config_entries.async_get_entry(created["entry_id"])
        assert entry.options["entities"] == ["cover.blind"]

    @pytest.mark.asyncio
    async def test_update_applies_it_for_switches(self, group_home: HomeAssistant) -> None:
        created = await _tool_create_group(
            group_home, {"name": "Plugs", "entities": ["switch.plug"]}
        )
        await group_home.async_block_till_done()
        result = await _tool_update_group(
            group_home, {"entity_id": created["entity_id"], "requires_all_members": True}
        )
        await group_home.async_block_till_done()
        assert result["status"] == "updated"
        entry = group_home.config_entries.async_get_entry(created["entry_id"])
        assert entry.options["all"] is True


# ── Delete + confirmation card ───────────────────────────────────────


class TestPreviewDeleteGroup:
    @pytest.mark.asyncio
    async def test_returns_approval_descriptor_keyed_on_entry_id(
        self, group_home: HomeAssistant
    ) -> None:
        created = await _create(group_home)
        preview = await _preview_delete_group(group_home, {"entity_id": created["entity_id"]})

        assert preview["requires_approval"] is True
        descriptor = preview["delete"]
        assert descriptor["kind"] == "group"
        # entry_id is immutable, unlike the entity_id the card also shows.
        assert descriptor["target_id"] == created["entry_id"]
        assert descriptor["entity_id"] == created["entity_id"]
        assert descriptor["label"] == "Evening Lights"

    @pytest.mark.asyncio
    async def test_preview_does_not_delete(self, group_home: HomeAssistant) -> None:
        created = await _create(group_home)
        await _preview_delete_group(group_home, {"entity_id": created["entity_id"]})
        assert len(gm.group_entries(group_home)) == 1

    @pytest.mark.asyncio
    async def test_label_warns_when_automations_depend_on_it(
        self, group_home: HomeAssistant
    ) -> None:
        """Deleting a referenced group breaks automations silently, so the
        blast radius has to reach the card the user actually reads."""
        created = await _create(group_home)
        with patch(
            "custom_components.selora_ai.group_manager.group_dependents",
            return_value={
                "automations": ["automation.evening"],
                "scripts": [],
                "scenes": [],
                "groups": [],
            },
        ):
            preview = await _preview_delete_group(group_home, {"entity_id": created["entity_id"]})
        assert "used by 1 automation/script" in preview["delete"]["label"]

    @pytest.mark.asyncio
    async def test_unknown_group_errors(self, group_home: HomeAssistant) -> None:
        result = await _preview_delete_group(group_home, {"entity_id": "light.nope"})
        assert "error" in result
        assert "requires_approval" not in result

    @pytest.mark.asyncio
    async def test_label_warns_when_another_group_contains_it(
        self, group_home: HomeAssistant
    ) -> None:
        """Nesting is supported, and HA's own groups_with_entity does not see
        helper groups — so nothing else would tell the user the parent is
        about to lose these devices."""
        inner = await _create(group_home, name="Inner")
        await _create(group_home, name="Outer", entities=[inner["entity_id"], "light.desk"])
        await group_home.async_block_till_done()

        preview = await _preview_delete_group(group_home, {"entity_id": inner["entity_id"]})

        assert "used by 1 group" in preview["delete"]["label"]

    @pytest.mark.asyncio
    async def test_label_warns_when_a_scene_sets_it(self, group_home: HomeAssistant) -> None:
        """`scene` ships no scenes_with_entity helper, so without reading the
        state attribute nothing would surface a scene that targets the group."""
        created = await _create(group_home)
        assert await async_setup_component(
            group_home,
            "scene",
            {"scene": [{"name": "Movie", "entities": {created["entity_id"]: "on"}}]},
        )
        await group_home.async_block_till_done()

        preview = await _preview_delete_group(group_home, {"entity_id": created["entity_id"]})

        assert "used by 1 scene" in preview["delete"]["label"]

    @pytest.mark.asyncio
    async def test_parent_groups_are_reported_as_dependents(
        self, group_home: HomeAssistant
    ) -> None:
        inner = await _create(group_home, name="Inner")
        outer = await _create(group_home, name="Outer", entities=[inner["entity_id"], "light.desk"])
        await group_home.async_block_till_done()

        dependents = gm.group_dependents(group_home, inner["entity_id"])

        assert dependents["groups"] == [outer["entity_id"]]
        # The parent itself has no parent.
        assert gm.group_dependents(group_home, outer["entity_id"])["groups"] == []


class TestDeleteGroup:
    @pytest.mark.asyncio
    async def test_removes_entry_and_entity(self, group_home: HomeAssistant) -> None:
        created = await _create(group_home)
        result = await _tool_delete_group(group_home, {"entry_id": created["entry_id"]})
        await group_home.async_block_till_done()

        assert result["status"] == "deleted"
        assert gm.group_entries(group_home) == []
        assert group_home.states.get(created["entity_id"]) is None

    @pytest.mark.asyncio
    async def test_members_survive(self, group_home: HomeAssistant) -> None:
        created = await _create(group_home)
        await _tool_delete_group(group_home, {"entry_id": created["entry_id"]})
        await group_home.async_block_till_done()
        assert group_home.states.get("light.lamp") is not None

    @pytest.mark.asyncio
    async def test_refuses_non_group_config_entry(self, group_home: HomeAssistant) -> None:
        """Guards the confirm path: a stale or spoofed entry_id must never let
        a group deletion remove an unrelated integration."""
        from pytest_homeassistant_custom_component.common import MockConfigEntry

        other = MockConfigEntry(domain="hue", title="Hue Bridge")
        other.add_to_hass(group_home)

        result = await _tool_delete_group(group_home, {"entry_id": other.entry_id})
        assert "is not a group helper" in result["error"]
        assert group_home.config_entries.async_get_entry(other.entry_id) is not None

    @pytest.mark.asyncio
    async def test_unknown_entry_id_errors(self, group_home: HomeAssistant) -> None:
        result = await _tool_delete_group(group_home, {"entry_id": "does-not-exist"})
        assert "error" in result


class TestDeleteApprovalCard:
    def test_descriptor_survives_synthesis(self) -> None:
        tool_log = [
            {
                "tool": "delete_group",
                "arguments": {"entity_id": "light.evening"},
                "result": {
                    "requires_approval": True,
                    "delete": {
                        "kind": "group",
                        "target_id": "entry123",
                        "entity_id": "light.evening",
                        "name": "Evening Lights",
                        "label": "Evening Lights",
                    },
                },
            }
        ]
        deletes = _pending_deletes_from_log(tool_log)
        assert len(deletes) == 1
        assert deletes[0]["kind"] == "group"
        assert deletes[0]["target_id"] == "entry123"

        result = synthesize_approval_from_tool_log(
            {"intent": "answer", "response": "ok"}, tool_log, None, language="en"
        )
        assert result["intent"] == "command_approval"
        approval = result["command_approval"]
        assert approval["approval_kind"] == "delete"
        assert approval["deletes"][0]["kind"] == "group"
        scopes = [a["value"].split(":")[1] for a in result["quick_actions"]]
        assert "delete" in scopes
        assert "cancel" in scopes

    def test_unknown_kind_dropped(self) -> None:
        """A card must never offer a Delete button the confirm handler can't
        resolve."""
        deletes = _pending_deletes_from_log(
            [
                {
                    "tool": "delete_group",
                    "arguments": {},
                    "result": {
                        "requires_approval": True,
                        "delete": {"kind": "helper", "target_id": "x", "entity_id": "y"},
                    },
                }
            ]
        )
        assert deletes == []


class TestResolveDeleteApproval:
    @pytest.mark.asyncio
    async def test_confirm_deletes_by_entry_id_only(self, hass: HomeAssistant) -> None:
        """entity_id could have been remapped between render and click, so the
        confirm must resolve purely by the immutable entry_id."""
        from custom_components.selora_ai import _resolve_delete_approval

        store = MagicMock()
        store.set_approval_status = AsyncMock()
        store.append_message = AsyncMock(return_value={"role": "assistant"})
        connection = MagicMock()
        approval = {
            "approval_kind": "delete",
            "deletes": [
                {
                    "kind": "group",
                    "target_id": "entry123",
                    "entity_id": "light.evening",
                    "label": "Evening Lights",
                }
            ],
        }
        with patch(
            "custom_components.selora_ai.mcp_server._tool_delete_group",
            new_callable=AsyncMock,
            return_value={"status": "deleted"},
        ) as mock_del:
            await _resolve_delete_approval(
                hass,
                connection,
                {"id": 1},
                store,
                "sess",
                0,
                approval,
                "delete",
                language="en",
            )

        mock_del.assert_awaited_once_with(hass, {"entry_id": "entry123"})
        store.set_approval_status.assert_awaited_once_with("sess", 0, "approved")

    @pytest.mark.asyncio
    async def test_cancel_deletes_nothing(self, group_home: HomeAssistant) -> None:
        from custom_components.selora_ai import _resolve_delete_approval

        created = await _create(group_home)
        store = MagicMock()
        store.set_approval_status = AsyncMock()
        store.append_message = AsyncMock(return_value={"role": "assistant"})
        approval = {
            "approval_kind": "delete",
            "deletes": [
                {"kind": "group", "target_id": created["entry_id"], "label": "Evening Lights"}
            ],
        }
        await _resolve_delete_approval(
            group_home,
            MagicMock(),
            {"id": 1},
            store,
            "sess",
            0,
            approval,
            "cancel",
            language="en",
        )
        assert len(gm.group_entries(group_home)) == 1

    @pytest.mark.asyncio
    async def test_end_to_end_confirm_removes_real_group(self, group_home: HomeAssistant) -> None:
        """Full path: preview → card synthesis → confirm → gone."""
        from custom_components.selora_ai import _resolve_delete_approval

        created = await _create(group_home)
        preview = await _preview_delete_group(group_home, {"entity_id": created["entity_id"]})
        synthesized = synthesize_approval_from_tool_log(
            {"intent": "answer", "response": "ok"},
            [{"tool": "delete_group", "arguments": {}, "result": preview}],
            None,
            language="en",
        )

        store = MagicMock()
        store.set_approval_status = AsyncMock()
        store.append_message = AsyncMock(return_value={"role": "assistant"})
        connection = MagicMock()
        await _resolve_delete_approval(
            group_home,
            connection,
            {"id": 1},
            store,
            "sess",
            0,
            synthesized["command_approval"],
            "delete",
            language="en",
        )
        await group_home.async_block_till_done()

        connection.send_error.assert_not_called()
        assert gm.group_entries(group_home) == []
        assert group_home.states.get(created["entity_id"]) is None
