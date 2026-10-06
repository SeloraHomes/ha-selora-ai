"""Tests for listing, ignoring and fixing Home Assistant repairs over MCP."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import (
    MockModule,
    mock_integration,
    mock_platform,
)
import voluptuous as vol

from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import (
    TOOL_FIX_REPAIR,
    TOOL_IGNORE_REPAIR,
    TOOL_LIST_REPAIRS,
)


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[tool](hass, arguments)


@pytest.fixture
async def repairs(hass: HomeAssistant) -> None:
    """A YAML warning from core's own translations, and a fixable issue from
    an integration whose fix is Home Assistant's confirmation flow."""
    mock_integration(hass, MockModule("fixer"))
    mock_platform(
        hass,
        "fixer.repairs",
        Mock(async_create_fix_flow=AsyncMock(return_value=ConfirmRepairFlow())),
    )
    assert await async_setup_component(hass, "fixer", {})
    assert await async_setup_component(hass, "repairs", {})
    await hass.async_block_till_done()
    ir.async_create_issue(
        hass,
        "homeassistant",
        "yaml_hue",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="config_entry_only",
        translation_placeholders={"domain": "hue"},
    )
    ir.async_create_issue(
        hass,
        "fixer",
        "stale_token",
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="stale_token",
    )


async def test_repairs_are_listed_with_their_text(hass: HomeAssistant, repairs: None) -> None:
    listed = await _mcp(hass, TOOL_LIST_REPAIRS)

    first, second = listed["repairs"]
    assert first["issue_id"] == "stale_token"
    assert first["fixable"] is True
    assert second["title"] == "The hue integration does not support YAML configuration"
    assert second["description"].startswith("The hue integration does not support")


async def test_an_ignored_repair_is_hidden_until_shown_again(
    hass: HomeAssistant, repairs: None
) -> None:
    ignored = await _mcp(hass, TOOL_IGNORE_REPAIR, domain="homeassistant", issue_id="yaml_hue")
    hidden = await _mcp(hass, TOOL_LIST_REPAIRS)
    with_ignored = await _mcp(hass, TOOL_LIST_REPAIRS, include_ignored=True)
    await _mcp(hass, TOOL_IGNORE_REPAIR, domain="homeassistant", issue_id="yaml_hue", ignore=False)
    shown = await _mcp(hass, TOOL_LIST_REPAIRS)

    assert ignored["status"] == "ignored"
    assert [r["issue_id"] for r in hidden["repairs"]] == ["stale_token"]
    assert any(r.get("ignored") for r in with_ignored["repairs"])
    assert len(shown["repairs"]) == 2


async def test_a_fix_is_described_then_run_once_confirmed(
    hass: HomeAssistant, repairs: None
) -> None:
    described = await _mcp(hass, TOOL_FIX_REPAIR, domain="fixer", issue_id="stale_token")

    assert described["status"] == "needs_confirmation", described
    assert ir.async_get(hass).async_get_issue("fixer", "stale_token") is not None

    fixed = await _mcp(
        hass,
        TOOL_FIX_REPAIR,
        domain="fixer",
        issue_id="stale_token",
        flow_id=described["flow_id"],
        fields={},
    )

    assert fixed["status"] == "fixed", fixed
    assert ir.async_get(hass).async_get_issue("fixer", "stale_token") is None


async def test_a_repair_without_a_fix_says_so(hass: HomeAssistant, repairs: None) -> None:
    result = await _mcp(hass, TOOL_FIX_REPAIR, domain="homeassistant", issue_id="yaml_hue")

    assert "no automatic fix" in result["error"]


async def test_an_unknown_repair_points_at_the_list(hass: HomeAssistant, repairs: None) -> None:
    result = await _mcp(hass, TOOL_IGNORE_REPAIR, domain="x", issue_id="y")

    assert "list_repairs" in result["error"]


async def test_listing_is_open_and_acting_needs_admin() -> None:
    assert TOOL_LIST_REPAIRS in mcp_access._READ_ONLY_TOOLS
    assert TOOL_IGNORE_REPAIR in mcp_access._ADMIN_TOOLS
    assert TOOL_FIX_REPAIR in mcp_access._ADMIN_TOOLS


class _MenuFix(RepairsFlow):
    """A fix that starts with a choice, then confirms it."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
        return self.async_show_menu(menu_options=["keep", "remove"])

    async def async_step_remove(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            return self.async_show_form(step_id="remove", data_schema=vol.Schema({}))
        return self.async_create_entry(data={})

    async def async_step_keep(self, user_input: dict[str, Any] | None = None) -> Any:
        return self.async_abort(reason="kept")


async def test_a_fix_with_a_menu_is_walked_step_by_step(hass: HomeAssistant) -> None:
    mock_integration(hass, MockModule("chooser"))
    mock_platform(
        hass,
        "chooser.repairs",
        Mock(async_create_fix_flow=AsyncMock(return_value=_MenuFix())),
    )
    assert await async_setup_component(hass, "chooser", {})
    assert await async_setup_component(hass, "repairs", {})
    ir.async_create_issue(
        hass, "chooser", "orphan", is_fixable=True, severity="warning", translation_key="orphan"
    )

    menu = await _mcp(hass, TOOL_FIX_REPAIR, domain="chooser", issue_id="orphan")
    assert menu["status"] == "needs_choice", menu
    assert menu["choices"] == ["keep", "remove"]

    confirm = await _mcp(
        hass,
        TOOL_FIX_REPAIR,
        domain="chooser",
        issue_id="orphan",
        flow_id=menu["flow_id"],
        choice="remove",
    )
    assert confirm["status"] == "needs_confirmation", confirm
    assert confirm["step"] == "remove"

    done = await _mcp(
        hass,
        TOOL_FIX_REPAIR,
        domain="chooser",
        issue_id="orphan",
        flow_id=menu["flow_id"],
        fields={},
    )
    assert done["status"] == "fixed", done


async def test_only_a_fix_started_here_is_continued(hass: HomeAssistant, repairs: None) -> None:
    result = await _mcp(
        hass,
        TOOL_FIX_REPAIR,
        domain="fixer",
        issue_id="stale_token",
        flow_id="not-ours",
        fields={},
    )

    assert "not one in progress" in result["error"]


async def test_an_issue_about_another_integration_reads_its_creators_text(
    hass: HomeAssistant,
) -> None:
    """``issue_domain`` names who the issue is about; the text is the creator's."""
    ir.async_create_issue(
        hass,
        "homeassistant",
        "about_hue",
        issue_domain="hue",
        is_fixable=False,
        severity="warning",
        translation_key="config_entry_only",
        translation_placeholders={"domain": "hue"},
    )

    (row,) = (await _mcp(hass, TOOL_LIST_REPAIRS))["repairs"]

    assert row["title"] == "The hue integration does not support YAML configuration"


async def test_a_fix_continues_only_the_repair_it_started_for(
    hass: HomeAssistant, repairs: None
) -> None:
    ir.async_create_issue(
        hass, "fixer", "other", is_fixable=True, severity="warning", translation_key="other"
    )
    started = await _mcp(hass, TOOL_FIX_REPAIR, domain="fixer", issue_id="stale_token")

    result = await _mcp(
        hass,
        TOOL_FIX_REPAIR,
        domain="fixer",
        issue_id="other",
        flow_id=started["flow_id"],
        fields={},
    )

    assert "not this repair" in result["error"]
    assert ir.async_get(hass).async_get_issue("fixer", "stale_token") is not None
