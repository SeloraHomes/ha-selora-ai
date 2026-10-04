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


def test_mcp_offers_both_kinds_with_the_chat_parameters() -> None:
    """Both kinds are created on the spot over MCP, so it is told about both —
    with the chat tool's own parameters, minus the resumption trigger."""
    from custom_components.selora_ai import mcp_server
    from custom_components.selora_ai.tool_registry import TOOL_CREATE_HELPER

    (tool,) = [t for t in mcp_server._TOOL_DEFINITIONS if t.name == "selora_create_helper"]
    chat = {p.name for p in TOOL_CREATE_HELPER.params}
    assert set(tool.inputSchema["properties"]) == chat - {"remaining_intent"}
    assert "Create button" not in tool.description
    assert "input_boolean" in tool.description
    assert "selora_create_helper" in mcp_server._ADMIN_TOOLS


async def test_mcp_creates_a_template_helper(hass: HomeAssistant, template: None) -> None:
    from custom_components.selora_ai.mcp_server import _tool_create_helper

    result = await _tool_create_helper(
        hass,
        {"domain": "template", "type": "sensor", "fields": {"name": "Answer", "state": "{{ 42 }}"}},
    )
    assert result["status"] == "created", result


# ── Serializing the form across Home Assistant releases ─────────────────────


def test_the_form_is_serialized_with_the_library_cv_uses(monkeypatch: pytest.MonkeyPatch) -> None:
    """2026.9 serializes forms with probatio and answers "unsupported" with
    probatio's sentinel, which voluptuous_serialize returns in place of the
    field list. Whatever serializer `cv` carries is the one to use."""
    import voluptuous as vol

    from custom_components.selora_ai import helper_flow

    seen: list[Any] = []

    def _to_field_list(schema: Any, *, custom_serializer: Any) -> list[dict[str, Any]]:
        seen.append(custom_serializer)
        return [{"name": "state", "required": True}]

    monkeypatch.setattr(helper_flow.cv, "to_field_list", _to_field_list, raising=False)

    fields = helper_flow._describe_fields(vol.Schema({vol.Required("state"): str}))

    assert fields == [{"name": "state", "required": True}]
    assert seen == [helper_flow.cv.custom_serializer]


def test_a_serializer_that_returns_no_list_describes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mismatched sentinel came back as the whole result and crashed the
    tool on iteration; it describes no fields instead."""
    import voluptuous as vol

    from custom_components.selora_ai import helper_flow

    monkeypatch.setattr(helper_flow.cv, "to_field_list", lambda *_a, **_k: object(), raising=False)

    assert helper_flow._describe_fields(vol.Schema({vol.Required("state"): str})) == []
