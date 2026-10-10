"""Tests for ``rename_automation``: a name or description changed in place.

Neither field can change what an automation does, so the tool writes them
straight to automations.yaml — no regenerated YAML, no proposal card — and
every other key has to come through exactly as it was.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
import yaml

from custom_components.selora_ai.automation_utils import _get_automation_store
from custom_components.selora_ai.mcp_server.automations import (
    RENAME_MAX_NAME_CHARS,
    RENAME_VERSION_MESSAGE,
    _tool_rename_automation,
)
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import (
    COMMAND_TOOL_NAMES,
    CONFIG_TOOL_NAMES,
    TOOL_MAP,
)
from tests.chat_harness import ChatHarness

SELORA_ENTRY: dict[str, Any] = {
    "id": "selora_ai_kitchen",
    "alias": "Kitchen Lights at Sunset",
    "description": "Turns the kitchen lights on at sunset",
    "triggers": [{"trigger": "sun", "event": "sunset", "offset": "-00:15:00"}],
    "conditions": [{"condition": "state", "entity_id": "binary_sensor.home", "state": "on"}],
    "actions": [
        {"action": "light.turn_on", "target": {"entity_id": ["light.kitchen", "light.office"]}}
    ],
    "mode": "queued",
    "max": 3,
    "variables": {"brightness": 80},
    "initial_state": False,
}

# Written by hand, in the legacy singular keys the proposal validator would
# move to their plural forms.
USER_ENTRY: dict[str, Any] = {
    "id": "porch_light",
    "alias": "Porch Light",
    "trigger": [{"platform": "time", "at": "18:00:00"}],
    "action": [{"service": "light.turn_on", "target": {"entity_id": "light.porch"}}],
}


def _write(hass: HomeAssistant, entries: list[dict[str, Any]]) -> None:
    path = Path(hass.config.config_dir) / "automations.yaml"
    path.write_text(yaml.safe_dump(entries, sort_keys=False), encoding="utf-8")


def _read(hass: HomeAssistant) -> list[dict[str, Any]]:
    path = Path(hass.config.config_dir) / "automations.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _by_id(hass: HomeAssistant, automation_id: str) -> dict[str, Any]:
    return next(e for e in _read(hass) if e.get("id") == automation_id)


@pytest.fixture
def home(hass: HomeAssistant) -> HomeAssistant:
    """automations.yaml on disk, with ``automation.reload`` a no-op."""
    hass.services.async_register("automation", "reload", lambda call: None)
    _write(hass, [SELORA_ENTRY, USER_ENTRY])
    return hass


async def _rename(hass: HomeAssistant, **arguments: Any) -> dict[str, Any]:
    return await _tool_rename_automation(hass, arguments)


# ── What it writes ──────────────────────────────────────────────────────────


async def test_a_rename_changes_only_the_alias(home: HomeAssistant) -> None:
    result = await _rename(
        home, automation_id="selora_ai_kitchen", new_name="Kitchen and Office Lights"
    )

    assert result["status"] == "updated", result
    assert result["changed"] == ["name"]
    assert result["name"] == "Kitchen and Office Lights"
    assert _by_id(home, "selora_ai_kitchen") == {
        **SELORA_ENTRY,
        "alias": "Kitchen and Office Lights",
    }


async def test_a_description_only_change_keeps_the_name(home: HomeAssistant) -> None:
    result = await _rename(
        home, automation_id="selora_ai_kitchen", description="Lights the kitchen and office."
    )

    assert result["changed"] == ["description"]
    assert result["previous"] == {"description": SELORA_ENTRY["description"]}
    assert _by_id(home, "selora_ai_kitchen") == {
        **SELORA_ENTRY,
        "description": "Lights the kitchen and office.",
    }


async def test_name_and_description_together(home: HomeAssistant) -> None:
    result = await _rename(
        home,
        automation_id="selora_ai_kitchen",
        new_name="  Kitchen   and Office Lights ",
        description="  Lights both rooms at sunset.  ",
    )

    assert result["changed"] == ["name", "description"]
    assert result["previous"] == {
        "new_name": SELORA_ENTRY["alias"],
        "description": SELORA_ENTRY["description"],
    }
    entry = _by_id(home, "selora_ai_kitchen")
    assert entry["alias"] == "Kitchen and Office Lights"
    assert entry["description"] == "Lights both rooms at sunset."


async def test_every_other_field_is_untouched(home: HomeAssistant) -> None:
    """Triggers, conditions, actions, mode, max, variables and the boot
    override — and a hand-written entry's legacy keys, which the proposal
    validator would have rewritten."""
    await _rename(home, automation_id="selora_ai_kitchen", new_name="Evening Kitchen")
    await _rename(home, automation_id="porch_light", new_name="Porch at Six")

    kitchen = _by_id(home, "selora_ai_kitchen")
    for key in ("triggers", "conditions", "actions", "mode", "max", "variables"):
        assert kitchen[key] == SELORA_ENTRY[key], key
    assert kitchen["initial_state"] is False

    porch = _by_id(home, "porch_light")
    assert porch == {**USER_ENTRY, "alias": "Porch at Six"}
    assert "triggers" not in porch
    assert "actions" not in porch


async def test_an_enabled_boot_override_stays_enabled(home: HomeAssistant) -> None:
    _write(home, [{**SELORA_ENTRY, "initial_state": True}])

    await _rename(home, automation_id="selora_ai_kitchen", new_name="Evening Kitchen")

    assert _by_id(home, "selora_ai_kitchen")["initial_state"] is True


async def test_an_absent_boot_override_stays_absent(home: HomeAssistant) -> None:
    _write(home, [{k: v for k, v in SELORA_ENTRY.items() if k != "initial_state"}])

    await _rename(home, automation_id="selora_ai_kitchen", new_name="Evening Kitchen")

    assert "initial_state" not in _by_id(home, "selora_ai_kitchen")


async def test_the_same_name_writes_nothing(home: HomeAssistant) -> None:
    result = await _rename(home, automation_id="selora_ai_kitchen", new_name=SELORA_ENTRY["alias"])

    assert result["status"] == "unchanged"
    assert "previous" not in result
    assert await _get_automation_store(home).get_record("selora_ai_kitchen") is None


# ── previous: the undo, in the tool's own terms ─────────────────────────────


async def test_previous_replays_the_rename(home: HomeAssistant) -> None:
    result = await _rename(home, automation_id="selora_ai_kitchen", new_name="Evening Kitchen")
    assert result["previous"] == {"new_name": "Kitchen Lights at Sunset"}

    await _rename(home, automation_id="selora_ai_kitchen", **result["previous"])

    assert _by_id(home, "selora_ai_kitchen") == SELORA_ENTRY


async def test_an_added_description_is_undone_with_clear(home: HomeAssistant) -> None:
    """Passing the old value back cannot remove a field that was not there."""
    result = await _rename(home, automation_id="porch_light", description="At six.")
    assert result["previous"] == {"clear": ["description"]}

    await _rename(home, automation_id="porch_light", **result["previous"])

    assert _by_id(home, "porch_light") == USER_ENTRY


async def test_clearing_a_description_hands_it_back(home: HomeAssistant) -> None:
    result = await _rename(home, automation_id="selora_ai_kitchen", clear=["description"])

    assert result["previous"] == {"description": SELORA_ENTRY["description"]}
    assert "description" not in _by_id(home, "selora_ai_kitchen")


# ── Version history ─────────────────────────────────────────────────────────


async def test_a_selora_automation_gets_a_version(home: HomeAssistant) -> None:
    await _tool_rename_automation(
        home,
        {"automation_id": "selora_ai_kitchen", "new_name": "Evening Kitchen"},
        session_id="session-1",
    )

    versions = await _get_automation_store(home).get_versions("selora_ai_kitchen")
    assert [v["message"] for v in versions] == [RENAME_VERSION_MESSAGE]
    assert versions[-1]["data"]["alias"] == "Evening Kitchen"
    assert versions[-1]["session_id"] == "session-1"


async def test_a_user_automation_gets_none(home: HomeAssistant) -> None:
    """The version history belongs to Selora's automations."""
    await _rename(home, automation_id="porch_light", new_name="Porch at Six")

    assert await _get_automation_store(home).get_record("porch_light") is None


# ── Refusals ────────────────────────────────────────────────────────────────


async def test_an_unknown_automation_is_not_found(home: HomeAssistant) -> None:
    result = await _rename(home, automation_id="nope", new_name="X")
    assert "not found" in result["error"]
    result = await _rename(home, entity_id="automation.nope", new_name="X")
    assert "not found" in result["error"]


async def test_an_automation_outside_automations_yaml_is_refused(home: HomeAssistant) -> None:
    home.states.async_set(
        "automation.package_one", "on", {"id": "package_one", "friendly_name": "Package One"}
    )

    result = await _rename(home, entity_id="automation.package_one", new_name="Renamed")

    assert "not in automations.yaml" in result["error"]


async def test_an_entry_without_an_id_is_refused(home: HomeAssistant) -> None:
    _write(home, [{k: v for k, v in USER_ENTRY.items() if k != "id"}])
    home.states.async_set("automation.porch_light", "on", {"friendly_name": "Porch Light"})

    result = await _rename(home, entity_id="automation.porch_light", new_name="Porch at Six")

    assert "no 'id'" in result["error"]
    assert _read(home)[0]["alias"] == "Porch Light"


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "Pass new_name"),
        ({"new_name": "   "}, "cannot be blank"),
        ({"new_name": "x" * (RENAME_MAX_NAME_CHARS + 1)}, "longer than"),
        ({"description": " "}, "cannot be blank"),
        ({"description": "x", "clear": ["description"]}, "not both"),
        ({"clear": ["alias"]}, "clear takes only"),
        ({"new_name": 3}, "must be text"),
    ],
)
async def test_bad_arguments_are_refused(
    home: HomeAssistant, arguments: dict[str, Any], message: str
) -> None:
    result = await _rename(home, automation_id="selora_ai_kitchen", **arguments)

    assert message in result["error"]
    assert _by_id(home, "selora_ai_kitchen") == SELORA_ENTRY


async def test_another_automations_name_is_refused(home: HomeAssistant) -> None:
    """Compared as the user reads it: case and spacing do not make it different."""
    result = await _rename(home, automation_id="selora_ai_kitchen", new_name="porch   LIGHT")

    assert "already called 'Porch Light'" in result["error"]
    assert _by_id(home, "selora_ai_kitchen") == SELORA_ENTRY


async def test_a_loaded_automations_name_is_refused_too(home: HomeAssistant) -> None:
    """One defined in a package shows in the same list as the rest."""
    home.states.async_set(
        "automation.package_one", "on", {"id": "package_one", "friendly_name": "Garage Door"}
    )

    result = await _rename(home, automation_id="selora_ai_kitchen", new_name="Garage door")

    assert "already called" in result["error"]


async def test_recasing_its_own_name_is_allowed(home: HomeAssistant) -> None:
    home.states.async_set(
        "automation.kitchen_lights_at_sunset",
        "on",
        {"id": "selora_ai_kitchen", "friendly_name": "Kitchen Lights at Sunset"},
    )

    result = await _rename(
        home, entity_id="automation.kitchen_lights_at_sunset", new_name="kitchen lights at sunset"
    )

    assert result["status"] == "updated", result
    assert _by_id(home, "selora_ai_kitchen")["alias"] == "kitchen lights at sunset"


async def test_what_home_assistant_rejects_is_not_written(home: HomeAssistant) -> None:
    broken = {**USER_ENTRY, "trigger": [{"platform": "no_such_trigger"}]}
    _write(home, [broken])

    result = await _rename(home, automation_id="porch_light", new_name="Porch at Six")

    assert "Home Assistant rejected the automation" in result["error"]
    assert _read(home) == [broken]


# ── Against Home Assistant's real automation component ──────────────────────


async def test_the_loaded_automation_takes_the_new_name(hass: HomeAssistant) -> None:
    """The reload is what the panel and HA's own list read; the entity_id stays."""
    Path(hass.config.config_dir, "configuration.yaml").write_text(
        "automation: !include automations.yaml\n", encoding="utf-8"
    )
    # An event trigger: a sun or time one leaves a timer armed past the test.
    entry = {
        **{k: v for k, v in SELORA_ENTRY.items() if k not in ("initial_state", "variables")},
        "triggers": [{"trigger": "event", "event_type": "kitchen_test"}],
    }
    _write(hass, [entry])
    assert await async_setup_component(hass, "automation", {"automation": [entry]})
    await hass.async_block_till_done()
    assert hass.states.get("automation.kitchen_lights_at_sunset") is not None

    result = await ToolExecutor(hass, MagicMock(), is_admin=True).execute(
        "rename_automation",
        {"entity_id": "automation.kitchen_lights_at_sunset", "new_name": "Evening Kitchen"},
    )
    await hass.async_block_till_done()

    assert result["status"] == "updated", result
    state = hass.states.get("automation.kitchen_lights_at_sunset")
    assert state is not None
    assert state.name == "Evening Kitchen"


# ── Registration ────────────────────────────────────────────────────────────


def test_it_is_a_direct_write_for_admins_on_large_models() -> None:
    from custom_components.selora_ai.llm_client.command_policy import (
        _DELETE_TOOLS,
        _DESTRUCTIVE_TOOLS,
    )

    tool = TOOL_MAP["rename_automation"]
    assert tool.requires_admin is True
    assert tool.large_context_only is True
    assert "rename_automation" not in _DELETE_TOOLS
    assert "rename_automation" not in _DESTRUCTIVE_TOOLS
    assert "rename_automation" in COMMAND_TOOL_NAMES
    assert "rename_automation" in CONFIG_TOOL_NAMES


async def test_a_non_admin_cannot_call_it(home: HomeAssistant) -> None:
    result = await ToolExecutor(home, MagicMock(), is_admin=False).execute(
        "rename_automation", {"automation_id": "selora_ai_kitchen", "new_name": "X"}
    )

    assert "requires admin" in result["error"]
    assert _by_id(home, "selora_ai_kitchen") == SELORA_ENTRY


def test_the_mcp_definition_is_derived() -> None:
    from custom_components.selora_ai.mcp_server import definitions
    from custom_components.selora_ai.mcp_server.access import _ADMIN_TOOLS
    from custom_components.selora_ai.mcp_server.dispatch import _get_tool_handlers

    name = "selora_rename_automation"
    assert definitions._DERIVED_MCP_TOOLS[name] == "rename_automation"
    assert name in definitions._RETURNS_PREVIOUS
    assert name in _ADMIN_TOOLS
    assert _get_tool_handlers()[name] is _tool_rename_automation
    (tool,) = [t for t in definitions._TOOL_DEFINITIONS if t.name == name]
    assert tool.inputSchema == TOOL_MAP["rename_automation"].to_anthropic()["input_schema"]
    assert tool.description.startswith(TOOL_MAP["rename_automation"].description)


def test_both_prompt_builders_route_a_rename_to_the_tool() -> None:
    from custom_components.selora_ai.llm_client.prompts import (
        build_architect_stream_system_prompt,
        build_architect_system_prompt,
    )

    for prompt in (build_architect_system_prompt(), build_architect_stream_system_prompt()):
        assert "rename_automation" in prompt


# ── A whole chat turn ───────────────────────────────────────────────────────


async def test_a_rename_during_refinement_is_applied_without_a_card(
    hass: HomeAssistant,
) -> None:
    """The user opened the automation to refine it and only asked for a new
    name: the tool writes it, the turn ends with no proposal to accept, and the
    next turn is shown the new name — the refinement marker's copy still holds
    the old one, and a proposal built from it would put it back on accept."""
    hass.services.async_register("automation", "reload", lambda call: None)
    harness = await ChatHarness.create(hass, automations=[SELORA_ENTRY])
    session = await harness.store.create_session()
    harness.session_id = session["id"]
    await harness.store.append_message(
        session["id"],
        "assistant",
        "Describe the changes",
        intent="automation",
        automation=SELORA_ENTRY,
        automation_yaml=yaml.dump(SELORA_ENTRY),
        automation_status="refining",
        automation_id="selora_ai_kitchen",
    )

    turn = await harness.chat(
        "Rename the automation to reflect both Kitchen and Office lights",
        reply={"intent": "answer", "response": "Renamed it to Kitchen and Office Lights."},
        run_tools=[
            (
                "rename_automation",
                {"automation_id": "selora_ai_kitchen", "new_name": "Kitchen and Office Lights"},
            )
        ],
    )

    assert not turn.errors
    assert turn.tool_results[0]["status"] == "updated", turn.tool_results
    assert turn.asked["refining_context"] is not None
    assert turn.done.get("automation") is None
    stored = await harness.messages()
    assert stored[-1]["role"] == "assistant"
    assert not stored[-1].get("automation")
    assert "automation_status" not in stored[-1]
    assert _by_id(hass, "selora_ai_kitchen")["alias"] == "Kitchen and Office Lights"

    follow_up = await harness.chat(
        "thanks", reply={"intent": "answer", "response": "You're welcome."}
    )

    assert follow_up.asked["refining_context"][0] == "Kitchen and Office Lights"
    assert "Kitchen and Office Lights" in follow_up.asked["refining_context"][1]
    aliases = [alias for _, alias, _ in follow_up.asked["automation_context"]]
    assert aliases == ["Kitchen and Office Lights"]


# ── Legacy ownership and concurrent edits ──────────────────────────────────


@pytest.mark.parametrize(
    ("entry", "arguments", "expected"),
    [
        (
            {"id": "legacy_1", "alias": "[Selora AI] Porch", "actions": []},
            {"new_name": "Porch at Dusk"},
            {"alias": "[Selora AI] Porch at Dusk"},
        ),
        (
            {"id": "legacy_2", "alias": "Porch", "description": "Old [Selora AI]", "actions": []},
            {"clear": ["description"]},
            {"description": "[Selora AI]"},
        ),
    ],
)
async def test_a_legacy_selora_automation_keeps_its_marker(
    home: HomeAssistant, entry: dict[str, Any], arguments: dict[str, Any], expected: dict[str, Any]
) -> None:
    """Before the label, the marker alone made an automation Selora's; renaming
    it away would drop it from Selora's views and its history."""
    _write(home, [{**entry, "triggers": [{"trigger": "time", "at": "18:00:00"}]}])
    result = await _rename(home, automation_id=entry["id"], **arguments)
    assert result["status"] == "updated", result
    written = _by_id(home, entry["id"])
    for key, value in expected.items():
        assert written[key] == value


async def test_an_edit_landing_mid_rename_is_not_overwritten(
    home: HomeAssistant,
) -> None:
    """The rename rebuilds the entry from its own read; a content edit made
    after that read must survive, and the rename is redone on top of it."""
    from custom_components.selora_ai.mcp_server import automations as module

    real_read = module._read_yaml_automations
    reads = 0

    async def stale_then_real(hass: HomeAssistant) -> list[dict[str, Any]]:
        nonlocal reads
        reads += 1
        entries = await real_read(hass)
        if reads == 1:
            # What the file held before someone else changed the mode.
            return [
                {**e, "mode": "single"} if e.get("id") == "selora_ai_kitchen" else e
                for e in entries
            ]
        return entries

    with patch.object(module, "_read_yaml_automations", stale_then_real):
        result = await _rename(home, automation_id="selora_ai_kitchen", new_name="Kitchen Glow")

    assert result["status"] == "updated", result
    assert reads == 2
    written = _by_id(home, "selora_ai_kitchen")
    assert written["alias"] == "Kitchen Glow"
    assert written["mode"] == "queued"


async def test_an_automation_that_keeps_changing_is_refused(home: HomeAssistant) -> None:
    from custom_components.selora_ai.mcp_server import automations as module

    real_read = module._read_yaml_automations

    async def always_stale(hass: HomeAssistant) -> list[dict[str, Any]]:
        return [{**e, "mode": "single"} for e in await real_read(hass)]

    with patch.object(module, "_read_yaml_automations", always_stale):
        result = await _rename(home, automation_id="selora_ai_kitchen", new_name="Kitchen Glow")

    assert "error" in result
    assert _by_id(home, "selora_ai_kitchen") == SELORA_ENTRY
