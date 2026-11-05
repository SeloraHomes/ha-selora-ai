"""The panel's toggle switches every automation, not only automations.yaml ones.

An automation from a package, or without an ``id:``, has no entry to write
``initial_state`` into; it is switched at runtime only. One with an entry also
gets ``initial_state`` saved, so a restart keeps the choice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import async_mock_service
import yaml

from custom_components.selora_ai.websocket.automations import (
    _handle_websocket_toggle_automation,
)

_handler = _handle_websocket_toggle_automation.__wrapped__
_schema = _handle_websocket_toggle_automation._ws_schema


async def _toggle(hass: Any, **fields: Any) -> MagicMock:
    msg = _schema({"id": 1, "type": "selora_ai/toggle_automation", **fields})
    connection = MagicMock()
    with patch(
        "custom_components.selora_ai.websocket.automations._require_admin",
        return_value=True,
    ):
        await _handler(hass, connection, msg)
    return connection


def _write_yaml(hass: Any, entries: list[dict[str, Any]]) -> Path:
    path = Path(hass.config.config_dir) / "automations.yaml"
    path.write_text(yaml.safe_dump(entries))
    return path


@pytest.mark.asyncio
async def test_switches_an_automation_without_an_id(hass: Any) -> None:
    hass.states.async_set("automation.porch", "on")
    calls = async_mock_service(hass, "automation", "turn_off")

    connection = await _toggle(hass, entity_id="automation.porch", enabled=False)

    connection.send_error.assert_not_called()
    assert connection.send_result.call_args.args[1]["persisted"] is False
    assert [c.data["entity_id"] for c in calls] == ["automation.porch"]


@pytest.mark.asyncio
async def test_switches_a_package_automation_its_id_is_not_in_yaml(hass: Any) -> None:
    _write_yaml(hass, [{"id": "other", "alias": "Other"}])
    hass.states.async_set("automation.porch", "off")
    calls = async_mock_service(hass, "automation", "turn_on")

    connection = await _toggle(
        hass, automation_id="from_package", entity_id="automation.porch", enabled=True
    )

    connection.send_error.assert_not_called()
    assert connection.send_result.call_args.args[1]["persisted"] is False
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_saves_initial_state_for_an_automations_yaml_entry(hass: Any) -> None:
    path = _write_yaml(hass, [{"id": "porch", "alias": "Porch"}])
    hass.states.async_set("automation.porch", "on")
    async_mock_service(hass, "automation", "turn_off")

    connection = await _toggle(
        hass, automation_id="porch", entity_id="automation.porch", enabled=False
    )

    connection.send_error.assert_not_called()
    assert connection.send_result.call_args.args[1]["persisted"] is True
    assert yaml.safe_load(path.read_text())[0]["initial_state"] is False


@pytest.mark.asyncio
async def test_refuses_an_entity_that_is_not_an_automation(hass: Any) -> None:
    hass.states.async_set("light.porch", "on")

    connection = await _toggle(hass, entity_id="light.porch", enabled=False)

    assert connection.send_error.call_args.args[1] == "not_found"


@pytest.mark.asyncio
async def test_says_when_its_own_initial_state_will_undo_the_switch(hass: Any) -> None:
    hass.states.async_set("automation.porch", "on")
    async_mock_service(hass, "automation", "turn_off")

    with patch(
        "custom_components.selora_ai.config_inspect.loaded_automation_config",
        return_value={"id": "pkg", "initial_state": True},
    ):
        connection = await _toggle(
            hass, automation_id="pkg", entity_id="automation.porch", enabled=False
        )

    assert connection.send_result.call_args.args[1]["reverts_on_restart"] is True


@pytest.mark.asyncio
async def test_no_warning_when_nothing_overrides_the_switch(hass: Any) -> None:
    hass.states.async_set("automation.porch", "on")
    async_mock_service(hass, "automation", "turn_off")

    with patch(
        "custom_components.selora_ai.config_inspect.loaded_automation_config",
        return_value={"initial_state": False},
    ):
        connection = await _toggle(hass, entity_id="automation.porch", enabled=False)

    assert connection.send_result.call_args.args[1]["reverts_on_restart"] is False


@pytest.mark.asyncio
async def test_refuses_when_automations_yaml_cannot_be_read(hass: Any) -> None:
    (Path(hass.config.config_dir) / "automations.yaml").write_text("- id: [porch\n")
    hass.states.async_set("automation.porch", "on")
    calls = async_mock_service(hass, "automation", "turn_off")

    connection = await _toggle(
        hass, automation_id="porch", entity_id="automation.porch", enabled=False
    )

    connection.send_result.assert_not_called()
    connection.send_error.assert_called_once()
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial", "enabled", "reverts"), [("off", True, True), ("off", False, False)]
)
async def test_reads_a_quoted_initial_state_as_ha_does(
    hass: Any, initial: str, enabled: bool, reverts: bool
) -> None:
    hass.states.async_set("automation.porch", "on" if not enabled else "off")
    async_mock_service(hass, "automation", "turn_on" if enabled else "turn_off")

    with patch(
        "custom_components.selora_ai.config_inspect.loaded_automation_config",
        return_value={"initial_state": initial},
    ):
        connection = await _toggle(hass, entity_id="automation.porch", enabled=enabled)

    assert connection.send_result.call_args.args[1]["reverts_on_restart"] is reverts
