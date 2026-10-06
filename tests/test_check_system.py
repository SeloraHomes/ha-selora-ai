"""Tests for the chat's one-call system check and integration reload."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.helper_flow import async_create_flow_helper
from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import definitions as mcp_definitions
from custom_components.selora_ai.mcp_server.names import (
    TOOL_CHECK_SYSTEM,
    TOOL_RELOAD_INTEGRATION,
)
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import TOOL_MAP


def _executor(hass: HomeAssistant, *, admin: bool = True) -> ToolExecutor:
    return ToolExecutor(hass, MagicMock(), is_admin=admin)


async def test_a_quiet_home_says_nothing_needs_attention(hass: HomeAssistant) -> None:
    result = await _executor(hass).execute("check_system", {})

    assert result["summary"] == "Nothing needs attention."
    assert result["repairs"]["count"] == 0
    assert "note" in result["backups"]


async def test_what_needs_attention_is_gathered_in_one_call(hass: HomeAssistant) -> None:
    broken = MockConfigEntry(domain="hue", title="Hue bridge")
    broken.add_to_hass(hass)
    broken.mock_state(hass, ConfigEntryState.SETUP_RETRY, reason="Bridge unreachable")
    ir.async_create_issue(
        hass,
        "homeassistant",
        "yaml_hue",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="config_entry_only",
        translation_placeholders={"domain": "hue"},
    )

    result = await _executor(hass).execute("check_system", {})

    (integration,) = result["integrations_not_working"]["items"]
    assert integration["title"] == "Hue bridge"
    assert integration["reason"] == "Bridge unreachable"
    (repair,) = result["repairs"]["items"]
    assert repair["title"] == "The hue integration does not support YAML configuration"
    assert result["summary"].startswith("2 thing(s) need attention")


async def test_chat_reloads_an_integration(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "template", {})
    await async_create_flow_helper(hass, "template", "sensor", {"name": "A", "state": "{{ 1 }}"})
    (entry,) = hass.config_entries.async_entries("template")

    result = await _executor(hass).execute("reload_integration", {"entry_id": entry.entry_id})

    assert result["status"] == "reloaded", result


async def test_both_are_admin_only_in_chat_and_over_mcp(hass: HomeAssistant) -> None:
    for name in ("check_system", "reload_integration"):
        assert TOOL_MAP[name].requires_admin
        assert TOOL_MAP[name].large_context_only
        assert "requires admin" in (await _executor(hass, admin=False).execute(name, {}))["error"]
    assert TOOL_CHECK_SYSTEM in mcp_access._ADMIN_TOOLS
    assert TOOL_RELOAD_INTEGRATION in mcp_access._ADMIN_TOOLS


def test_the_mcp_definitions_are_derived_from_chat() -> None:
    """One definition each, so chat and MCP cannot drift."""
    names = [t.name for t in mcp_definitions._TOOL_DEFINITIONS]
    assert names.count(TOOL_CHECK_SYSTEM) == 1
    assert names.count(TOOL_RELOAD_INTEGRATION) == 1
    assert mcp_definitions._DERIVED_MCP_TOOLS[TOOL_CHECK_SYSTEM] == "check_system"
    assert mcp_definitions._DERIVED_MCP_TOOLS[TOOL_RELOAD_INTEGRATION] == "reload_integration"


def test_the_schema_cost_stays_small() -> None:
    """Every cloud turn carries every chat tool's schema."""
    import json

    for name in ("check_system", "reload_integration"):
        assert len(json.dumps(TOOL_MAP[name].to_anthropic())) < 1200, name


async def test_a_failing_backup_location_needs_attention(hass: HomeAssistant) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    hass.config.components.add("backup")
    backups = {
        f"b{i}": SimpleNamespace(backup_id=f"b{i}", date=f"2026-10-{i % 28 + 1:02d}T00:00:00+00:00")
        for i in range(60)
    }
    hass.data["backup"] = SimpleNamespace(
        async_get_backups=AsyncMock(
            return_value=(backups, {"cloud.cloud": RuntimeError("quota exceeded")})
        ),
        config=SimpleNamespace(
            data=SimpleNamespace(
                last_attempted_automatic_backup=None,
                last_completed_automatic_backup=None,
                schedule=None,
            )
        ),
    )

    result = await _executor(hass).execute("check_system", {})

    assert result["backups"]["count"] == 60
    assert result["backups"]["location_errors"] == {"cloud.cloud": "quota exceeded"}
    assert result["summary"].startswith("1 thing(s) need attention")
