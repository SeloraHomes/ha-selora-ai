"""Tests for replacing an automation Selora did not create, over MCP.

It is validated by Home Assistant's own automation validator and written
exactly as given — the proposal validator would reshape it — while keeping the
enabled state and the risk gate every replacement gets.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
import pytest
import yaml

from custom_components.selora_ai.mcp_server import _tool_create_automation

USER_ENTRY = {
    "id": "morning_routine",
    "alias": "Morning Routine",
    "trigger": [{"platform": "time", "at": "06:30:00"}],
    "action": [{"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}}],
}


def _write(hass: HomeAssistant, entries: list[dict[str, Any]]) -> Path:
    path = Path(hass.config.config_dir) / "automations.yaml"
    path.write_text(yaml.safe_dump(entries), encoding="utf-8")
    return path


def _read(hass: HomeAssistant) -> list[dict[str, Any]]:
    path = Path(hass.config.config_dir) / "automations.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def home(hass: HomeAssistant) -> None:
    hass.states.async_set("light.kitchen", "off")
    hass.services.async_register("automation", "reload", lambda call: None)
    hass.services.async_register("shell_command", "wipe", lambda call: None)


async def _replace(hass: HomeAssistant, text: str) -> dict[str, Any]:
    return await _tool_create_automation(
        hass, {"yaml": text, "automation_id": "morning_routine"}
    )


async def test_a_user_automation_is_written_exactly_as_given(hass: HomeAssistant) -> None:
    """Legacy singular keys and `service:` stay as the user wrote them — the
    proposal validator would have moved them to their plural forms."""
    _write(hass, [USER_ENTRY])

    result = await _replace(
        hass,
        "alias: Morning Routine\n"
        "trigger:\n- platform: time\n  at: '07:15:00'\n"
        "action:\n- service: light.turn_on\n  target:\n    entity_id: light.kitchen\n",
    )

    assert result["status"] == "updated", result
    (entry,) = _read(hass)
    assert entry["id"] == "morning_routine"
    assert entry["trigger"] == [{"platform": "time", "at": "07:15:00"}]
    assert entry["action"][0]["service"] == "light.turn_on"
    assert "triggers" not in entry
    assert "actions" not in entry


async def test_the_id_in_the_yaml_cannot_move_it(hass: HomeAssistant) -> None:
    _write(hass, [USER_ENTRY])

    await _replace(
        hass,
        "id: something_else\nalias: Morning Routine\n"
        "trigger:\n- platform: time\n  at: '07:15:00'\n"
        "action:\n- service: light.turn_on\n  target:\n    entity_id: light.kitchen\n",
    )

    assert [e["id"] for e in _read(hass)] == ["morning_routine"]


async def test_what_home_assistant_rejects_is_refused_and_nothing_written(
    hass: HomeAssistant,
) -> None:
    _write(hass, [USER_ENTRY])

    result = await _replace(
        hass,
        "alias: Morning Routine\n"
        "trigger:\n- platform: no_such_trigger\n"
        "action:\n- service: light.turn_on\n",
    )

    assert "Home Assistant rejected the automation" in result["error"]
    assert _read(hass) == [USER_ENTRY]


async def test_the_boot_override_is_kept(hass: HomeAssistant) -> None:
    """A content edit does not change whether the automation starts enabled."""
    _write(hass, [{**USER_ENTRY, "initial_state": False}])

    await _replace(
        hass,
        "alias: Morning Routine\n"
        "trigger:\n- platform: time\n  at: '07:15:00'\n"
        "action:\n- service: light.turn_on\n  target:\n    entity_id: light.kitchen\n",
    )

    assert _read(hass)[0]["initial_state"] is False


async def test_newly_adding_a_shell_command_turns_it_off(hass: HomeAssistant) -> None:
    """The risk gate is not Selora's alone: editing a user's automation into a
    shell_command one must not leave it running unreviewed."""
    _write(hass, [{**USER_ENTRY, "initial_state": True}])

    result = await _replace(
        hass,
        "alias: Morning Routine\n"
        "trigger:\n- platform: time\n  at: '07:15:00'\n"
        "action:\n- service: shell_command.wipe\n",
    )

    assert result["forced_disabled"] is True
    assert _read(hass)[0]["initial_state"] is False


async def test_no_version_record_is_made(hass: HomeAssistant) -> None:
    """The version history belongs to Selora's automations."""
    from custom_components.selora_ai.automation_utils import _get_automation_store

    _write(hass, [USER_ENTRY])

    await _replace(
        hass,
        "alias: Morning Routine\n"
        "trigger:\n- platform: time\n  at: '07:15:00'\n"
        "action:\n- service: light.turn_on\n  target:\n    entity_id: light.kitchen\n",
    )

    store = _get_automation_store(hass)
    assert await store.get_record("morning_routine") is None
