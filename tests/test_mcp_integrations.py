"""Tests for listing, reloading, enabling, removing and reconfiguring integrations
over MCP.

The integration under test is a real one: a template sensor, created through
its config flow, whose entry has an options flow of its own.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.helper_flow import async_create_flow_helper
from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import (
    TOOL_LIST_INTEGRATIONS,
    TOOL_RELOAD_INTEGRATION,
    TOOL_REMOVE_INTEGRATION,
    TOOL_SET_INTEGRATION_ENABLED,
    TOOL_SET_INTEGRATION_OPTIONS,
)


@pytest.fixture
async def sensor_entry(hass: HomeAssistant) -> str:
    """A template sensor's entry_id."""
    assert await async_setup_component(hass, "template", {})
    await hass.async_block_till_done()
    created = await async_create_flow_helper(
        hass, "template", "sensor", {"name": "Answer", "state": "{{ 42 }}"}
    )
    assert created["status"] == "created", created
    (entry,) = hass.config_entries.async_entries("template")
    return entry.entry_id


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[tool](hass, arguments)


async def test_integrations_are_listed_with_their_state(
    hass: HomeAssistant, sensor_entry: str
) -> None:
    listed = await _mcp(hass, TOOL_LIST_INTEGRATIONS, domain="template")

    (row,) = listed["integrations"]
    assert row["entry_id"] == sensor_entry
    assert row["state"] == "loaded"
    assert row["entities"] == 1
    assert row["supports_options"] is True
    assert listed["not_working"] == 0


async def test_problems_only_lists_what_is_not_working(
    hass: HomeAssistant, sensor_entry: str
) -> None:
    broken = MockConfigEntry(domain="broken", title="Broken", state=ConfigEntryState.SETUP_RETRY)
    broken.add_to_hass(hass)

    listed = await _mcp(hass, TOOL_LIST_INTEGRATIONS, problems_only=True)

    assert [r["domain"] for r in listed["integrations"]] == ["broken"]
    assert listed["not_working"] == 1


async def test_an_integration_is_reloaded(hass: HomeAssistant, sensor_entry: str) -> None:
    result = await _mcp(hass, TOOL_RELOAD_INTEGRATION, entry_id=sensor_entry)

    assert result["status"] == "reloaded", result
    assert result["state"] == "loaded"


async def test_an_integration_is_disabled_and_enabled(
    hass: HomeAssistant, sensor_entry: str
) -> None:
    disabled = await _mcp(hass, TOOL_SET_INTEGRATION_ENABLED, entry_id=sensor_entry, enabled=False)
    assert disabled["status"] == "disabled", disabled
    assert disabled["disabled_by"] == "user"
    assert disabled["state"] == "not_loaded"

    enabled = await _mcp(hass, TOOL_SET_INTEGRATION_ENABLED, entry_id=sensor_entry, enabled=True)
    assert enabled["state"] == "loaded"
    assert "disabled_by" not in enabled


async def test_removing_asks_first(hass: HomeAssistant, sensor_entry: str) -> None:
    asked = await _mcp(hass, TOOL_REMOVE_INTEGRATION, entry_id=sensor_entry)

    assert asked["requires_confirmation"] is True
    assert "1 entit" in asked["hint"]
    assert hass.config_entries.async_get_entry(sensor_entry) is not None

    removed = await _mcp(hass, TOOL_REMOVE_INTEGRATION, entry_id=sensor_entry, confirmed=True)

    assert removed["status"] == "removed", removed
    assert hass.config_entries.async_get_entry(sensor_entry) is None


async def test_options_are_described_then_saved(hass: HomeAssistant, sensor_entry: str) -> None:
    described = await _mcp(hass, TOOL_SET_INTEGRATION_OPTIONS, entry_id=sensor_entry)

    assert described["status"] == "needs_options", described
    state_field = next(f for f in described["fields"] if f["name"] == "state")
    assert state_field["current"] == "{{ 42 }}"

    saved = await _mcp(
        hass, TOOL_SET_INTEGRATION_OPTIONS, entry_id=sensor_entry, options={"state": "{{ 7 }}"}
    )

    assert saved["status"] == "saved", saved
    assert hass.config_entries.async_get_entry(sensor_entry).options["state"] == "{{ 7 }}"
    assert hass.states.get("sensor.answer").state == "7"


async def test_selora_never_changes_itself(hass: HomeAssistant) -> None:
    own = MockConfigEntry(domain=DOMAIN, title="Selora AI")
    own.add_to_hass(hass)

    for tool, extra in (
        (TOOL_RELOAD_INTEGRATION, {}),
        (TOOL_SET_INTEGRATION_ENABLED, {"enabled": False}),
        (TOOL_REMOVE_INTEGRATION, {"confirmed": True}),
        (TOOL_SET_INTEGRATION_OPTIONS, {}),
    ):
        result = await _mcp(hass, tool, entry_id=own.entry_id, **extra)
        assert "does not" in result["error"], (tool, result)


async def test_an_unknown_entry_points_at_the_list(hass: HomeAssistant) -> None:
    result = await _mcp(hass, TOOL_RELOAD_INTEGRATION, entry_id="nope")

    assert "list_integrations" in result["error"]


async def test_listing_is_open_and_changing_needs_admin() -> None:
    assert TOOL_LIST_INTEGRATIONS in mcp_access._READ_ONLY_TOOLS
    for tool in (
        TOOL_RELOAD_INTEGRATION,
        TOOL_SET_INTEGRATION_ENABLED,
        TOOL_REMOVE_INTEGRATION,
        TOOL_SET_INTEGRATION_OPTIONS,
    ):
        assert tool in mcp_access._ADMIN_TOOLS


def test_a_stored_credential_is_never_shown() -> None:
    from custom_components.selora_ai.helper_flow import _describe_serialized

    fields = _describe_serialized(
        [
            {
                "name": "password",
                "selector": {"text": {"type": "password"}},
                "description": {"suggested_value": "hunter2"},
            },
            {"name": "api_key", "default": "abc123"},
            {"name": "host", "description": {"suggested_value": "10.0.0.2"}},
        ]
    )

    assert fields == [
        {"name": "password", "selector": "text", "is_set": True},
        {"name": "api_key", "is_set": True},
        {"name": "host", "current": "10.0.0.2"},
    ]


def test_a_sections_fields_are_described() -> None:
    from custom_components.selora_ai.helper_flow import _describe_serialized

    (section,) = _describe_serialized(
        [
            {
                "name": "auth",
                "type": "expandable",
                "schema": [
                    {"name": "username", "description": {"suggested_value": "me"}},
                    {"name": "password", "description": {"suggested_value": "x"}},
                ],
            }
        ]
    )

    assert section["fields"] == [
        {"name": "username", "current": "me"},
        {"name": "password", "is_set": True},
    ]


async def test_an_empty_options_object_is_submitted(hass: HomeAssistant, sensor_entry: str) -> None:
    result = await _mcp(hass, TOOL_SET_INTEGRATION_OPTIONS, entry_id=sensor_entry, options={})

    assert result.get("status") != "needs_options", result


async def test_a_disable_that_needs_a_restart_says_so(
    hass: HomeAssistant, sensor_entry: str
) -> None:
    with patch.object(hass.config_entries, "async_set_disabled_by", AsyncMock(return_value=False)):
        result = await _mcp(
            hass, TOOL_SET_INTEGRATION_ENABLED, entry_id=sensor_entry, enabled=False
        )

    assert result["require_restart"] is True
