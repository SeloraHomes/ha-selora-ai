"""`create_helper` drives a config-entry helper's own setup flow.

One tool for every helper HA ships: storage helpers are proposed for the panel
(`test_create_helper.py`), and everything else — template entities of every
type, utility meters, thresholds … — runs its integration's config flow here,
with the flow's form as the schema.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai.tool_executor import ToolExecutor

_NO_OP = [{"delay": {"seconds": 0}}]


async def _create(hass: HomeAssistant, **arguments: Any) -> dict[str, Any]:
    return await ToolExecutor(hass, MagicMock(), is_admin=True).execute("create_helper", arguments)


@pytest.fixture
async def template(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "template", {})
    await hass.async_block_till_done()


async def test_a_bare_call_lists_the_types(hass: HomeAssistant, template: None) -> None:
    result = await _create(hass, domain="template")
    assert result["status"] == "needs_type"
    assert "alarm_control_panel" in result["types"]
    assert "sensor" in result["types"]


async def test_a_typed_call_describes_the_form(hass: HomeAssistant, template: None) -> None:
    """The flow is the schema — the model learns the fields from it."""
    result = await _create(hass, domain="template", type="alarm_control_panel")
    assert result["status"] == "needs_options"
    names = {f["name"] for f in result["fields"]}
    assert {"name", "arm_away", "arm_home", "trigger", "code_arm_required"} <= names
    # Nothing was left half-done in Settings.
    assert not hass.config_entries.flow.async_progress()


async def test_an_alarm_panel_works_with_no_helper_behind_it(
    hass: HomeAssistant, template: None
) -> None:
    result = await _create(
        hass,
        domain="template",
        type="alarm_control_panel",
        fields={
            "name": "Home Alarm",
            "arm_home": _NO_OP,
            "arm_away": _NO_OP,
            "disarm": _NO_OP,
            "trigger": _NO_OP,
            "code_arm_required": False,
            "code_format": "no_code",
        },
    )

    assert result["status"] == "created", result
    (entity_id,) = result["entity_ids"]
    for service, expected in (
        ("alarm_arm_away", "armed_away"),
        ("alarm_trigger", "triggered"),
        ("alarm_disarm", "disarmed"),
    ):
        await hass.services.async_call(
            "alarm_control_panel", service, {"entity_id": entity_id}, blocking=True
        )
        assert hass.states.get(entity_id).state == expected, service


async def test_a_template_sensor(hass: HomeAssistant, template: None) -> None:
    result = await _create(
        hass,
        domain="template",
        type="sensor",
        fields={"name": "Answer", "state": "{{ 40 + 2 }}"},
    )
    assert result["status"] == "created", result
    assert hass.states.get(result["entity_ids"][0]).state == "42"


async def test_rejected_fields_come_back_with_the_form(hass: HomeAssistant, template: None) -> None:
    result = await _create(hass, domain="template", type="sensor", fields={"state": "{{ 1 }}"})
    assert "error" in result
    assert result["fields"]
    assert not hass.config_entries.flow.async_progress()


@pytest.mark.parametrize("domain", ["hue", "nonexistent"])
async def test_only_helper_integrations_are_driven(hass: HomeAssistant, domain: str) -> None:
    """A device or cloud-account flow is not one a chat should complete."""
    result = await _create(hass, domain=domain)
    assert "not a helper integration" in result["error"]


async def test_groups_keep_their_own_tool(hass: HomeAssistant) -> None:
    result = await _create(hass, domain="group")
    assert "create_group" in result["error"]


def test_there_is_no_tool_per_helper_kind() -> None:
    from custom_components.selora_ai.tool_registry import TOOL_MAP

    assert "create_alarm_panel" not in TOOL_MAP


def test_a_missing_non_device_is_named_without_a_device_dump() -> None:
    """A missing alarm panel is not answered by a list of lights and a
    question about when the automation should run."""
    from custom_components.selora_ai.llm_client.parsers import _humanise_unknown_entity_error

    out = _humanise_unknown_entity_error(
        "automation references unknown entity_id(s): alarm_control_panel.home_alarm",
        [{"entity_id": "light.kitchen", "attributes": {"friendly_name": "Kitchen Light"}}],
    )

    assert "alarm_control_panel.home_alarm" in out
    assert "Kitchen Light" not in out
    assert "which device" not in out.lower()


# ── Over MCP ────────────────────────────────────────────────────────────────


def test_mcp_offers_the_setup_flow_half_only() -> None:
    """MCP has no panel to show a Create button, so it is told only about the
    half that works there — with the chat tool's own parameters."""
    from custom_components.selora_ai import mcp_server

    (tool,) = [t for t in mcp_server._TOOL_DEFINITIONS if t.name == "selora_create_helper"]
    assert set(tool.inputSchema["properties"]) == {"domain", "type", "fields"}
    assert "Create button" not in tool.description
    assert "cannot be created from here" in tool.description
    assert "selora_create_helper" in mcp_server._ADMIN_TOOLS


async def test_mcp_creates_a_template_helper(hass: HomeAssistant, template: None) -> None:
    from custom_components.selora_ai.mcp_server import _tool_create_helper

    result = await _tool_create_helper(
        hass,
        {"domain": "template", "type": "sensor", "fields": {"name": "Answer", "state": "{{ 42 }}"}},
    )
    assert result["status"] == "created", result


async def test_mcp_refuses_a_storage_helper(hass: HomeAssistant) -> None:
    from custom_components.selora_ai.mcp_server import _tool_create_helper

    result = await _tool_create_helper(hass, {"domain": "input_boolean", "name": "Guest"})
    assert "cannot be created over MCP" in result["error"]
