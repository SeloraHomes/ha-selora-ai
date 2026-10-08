"""A script's inputs (fields), variables and run limits, set and removed.

``set_script`` kept these when it found them but could not set one, so "make a
script that takes a duration" produced a script with the duration hard-coded,
and nothing it had could be removed. Fields are what callers pass, so changing
them names the callers, and on the chat card says so before it happens.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import Event, HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
import yaml

from custom_components.selora_ai.mcp_server.scripts_helpers import (
    _tool_delete_script,
    _tool_set_script,
)
from custom_components.selora_ai.tool_executor import ToolExecutor

_GREET = [{"event": "greeted", "event_data": {"who": "{{ who }}", "greeting": "{{ greeting }}"}}]
_FIELDS = {"who": {"required": True, "selector": {"text": {}}}}


@pytest.fixture
async def scripts(hass: HomeAssistant) -> HomeAssistant:
    Path(hass.config.path("scripts.yaml")).write_text("{}\n", encoding="utf-8")
    Path(hass.config.path("configuration.yaml")).write_text(
        "script: !include scripts.yaml\n", encoding="utf-8"
    )
    assert await async_setup_component(hass, "script", {"script": {}})
    await hass.async_block_till_done()
    return hass


def _stored(hass: HomeAssistant) -> dict[str, Any]:
    return yaml.safe_load(Path(hass.config.path("scripts.yaml")).read_text())


async def _greeter(hass: HomeAssistant, **extra: Any) -> dict[str, Any]:
    return await _tool_set_script(
        hass,
        {
            "object_id": "greeter",
            "alias": "Greeter",
            "sequence": _GREET,
            "fields": _FIELDS,
            "variables": {"greeting": "hello"},
            **extra,
        },
    )


async def test_a_script_takes_inputs_and_runs_with_them(scripts: HomeAssistant) -> None:
    result = await _greeter(scripts, mode="queued", max=4, max_exceeded="silent")
    seen: list[Event] = []
    scripts.bus.async_listen("greeted", seen.append)

    await scripts.services.async_call("script", "greeter", {"who": "Ann"}, blocking=True)
    await scripts.async_block_till_done()

    assert result["status"] == "created", result
    assert result["fields"] == ["who"]
    stored = _stored(scripts)["greeter"]
    assert stored["fields"] == _FIELDS
    assert stored["max"] == 4
    assert stored["max_exceeded"] == "silent"
    assert seen[0].data == {"who": "Ann", "greeting": "hello"}


async def test_what_home_assistant_refuses_is_not_written(scripts: HomeAssistant) -> None:
    bad = await _greeter(scripts, fields={"who": {"selector": {"no_such_selector": {}}}})
    too_few = await _greeter(scripts, mode="queued", max=0)

    assert "validation failed" in bad["error"]
    assert "error" in too_few
    assert _stored(scripts) == {}


async def test_settings_are_removed_with_clear(scripts: HomeAssistant) -> None:
    await _greeter(scripts, icon="mdi:hand-wave")

    result = await _tool_set_script(
        scripts,
        {
            "object_id": "greeter",
            "alias": "Greeter",
            "sequence": [{"event": "greeted"}],
            "clear": ["fields", "variables", "icon"],
        },
    )
    unknown = await _tool_set_script(
        scripts,
        {"object_id": "greeter", "alias": "Greeter", "sequence": _GREET, "clear": ["alias"]},
    )

    assert result["status"] == "updated", result
    assert set(_stored(scripts)["greeter"]) == {"alias", "sequence"}
    assert "clear takes" in unknown["error"]

    for name, value in (("mode", "queued"), ("icon", "mdi:x"), ("fields", _FIELDS)):
        both = await _tool_set_script(
            scripts,
            {
                "object_id": "greeter",
                "alias": "Greeter",
                "sequence": _GREET,
                name: value,
                "clear": [name],
            },
        )
        assert "set and cleared" in both["error"], name


async def test_changing_inputs_names_the_callers(scripts: HomeAssistant) -> None:
    await _greeter(scripts)
    assert await async_setup_component(
        scripts,
        "automation",
        {
            "automation": {
                "id": "morning",
                "alias": "Morning hello",
                "triggers": [{"trigger": "event", "event_type": "wake"}],
                "actions": [{"action": "script.greeter", "data": {"who": "Ann"}}],
            }
        },
    )
    await scripts.async_block_till_done()

    unchanged = await _greeter(scripts)
    changed = await _greeter(scripts, fields={"name": {"required": True}})

    assert "check_callers" not in unchanged
    # Called as `action: script.greeter`, which Home Assistant's own reference
    # tracking does not count as a reference.
    assert changed["check_callers"]["automations"] == ["automation.morning_hello"]


async def test_the_chat_card_says_the_inputs_change(scripts: HomeAssistant) -> None:
    await _greeter(scripts)

    result = await ToolExecutor(scripts, MagicMock(), is_admin=True).execute(
        "set_script",
        {
            "object_id": "greeter",
            "alias": "Greeter",
            "sequence": _GREET,
            "fields": {"name": {"required": True}},
        },
    )

    assert result.get("requires_approval"), result
    assert "its inputs become name" in result["destructive"]["label"]


async def test_deleting_a_script_counts_its_direct_callers(scripts: HomeAssistant) -> None:
    """The delete card's warning reads the same callers."""
    await _greeter(scripts)
    assert await async_setup_component(
        scripts,
        "automation",
        {
            "automation": {
                "alias": "Hello",
                "triggers": [{"trigger": "event", "event_type": "wake"}],
                "actions": [
                    {"if": [], "then": [{"action": "script.greeter", "data": {"who": "Ann"}}]}
                ],
            }
        },
    )
    await scripts.async_block_till_done()

    result = await ToolExecutor(scripts, MagicMock(), is_admin=True).execute(
        "delete_script", {"script": "greeter"}
    )

    assert "called by 1 automation" in result["delete"]["label"]


async def test_max_on_a_script_whose_runs_never_overlap_is_refused(scripts: HomeAssistant) -> None:
    """Home Assistant saves it in any mode and enforces it only for queued/parallel."""
    result = await _greeter(scripts, max=3)

    assert "queued or parallel" in result["error"]


async def test_max_with_the_mode_cleared_is_refused(scripts: HomeAssistant) -> None:
    await _greeter(scripts, mode="queued", max=3)

    result = await _greeter(scripts, max=4, clear=["mode"])

    assert "queued or parallel" in result["error"]


async def test_a_replaced_script_comes_back_in_the_result(scripts: HomeAssistant) -> None:
    """Fed back to set_script, the old script is what the file holds again."""
    await _greeter(scripts, mode="queued", max=4)
    before = _stored(scripts)["greeter"]

    result = await _tool_set_script(
        scripts, {"object_id": "greeter", "alias": "Greeter", "sequence": _GREET[:1]}
    )
    assert result["previous"] == {"object_id": "greeter", **before}

    await _tool_set_script(scripts, result["previous"])
    assert _stored(scripts)["greeter"] == before


async def test_a_deleted_script_comes_back_in_the_result(scripts: HomeAssistant) -> None:
    await _greeter(scripts)
    before = _stored(scripts)["greeter"]

    result = await _tool_delete_script(scripts, {"script": "script.greeter"})

    assert result["status"] == "deleted", result
    assert "greeter" not in _stored(scripts)

    # Under its own object_id, so what called script.greeter finds it again.
    await _tool_set_script(scripts, result["previous"])
    assert _stored(scripts)["greeter"] == before


async def test_a_script_without_an_alias_comes_back_named_by_its_id(
    scripts: HomeAssistant,
) -> None:
    """set_script requires an alias; a hand-written script may have none."""
    Path(scripts.config.path("scripts.yaml")).write_text(
        yaml.safe_dump({"bare": {"sequence": _GREET, "trace": {"stored_traces": 3}}})
    )

    result = await _tool_delete_script(scripts, {"script": "bare"})

    assert result["previous"]["alias"] == "bare"
    assert "trace" not in result["previous"]
    assert "trace" in result["note"]


async def test_a_setting_the_replacement_added_is_cleared_on_the_way_back(
    scripts: HomeAssistant,
) -> None:
    await _tool_set_script(
        scripts, {"object_id": "greeter", "alias": "Greeter", "sequence": _GREET}
    )
    before = _stored(scripts)["greeter"]

    result = await _tool_set_script(
        scripts,
        {"object_id": "greeter", "alias": "Greeter", "sequence": _GREET, "icon": "mdi:hand-wave"},
    )
    assert result["previous"]["clear"] == ["icon"]

    await _tool_set_script(scripts, result["previous"])
    assert _stored(scripts)["greeter"] == before
