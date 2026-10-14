"""The automations list describes automations defined outside automations.yaml.

A recipe installs its automations as an HA package, so automations.yaml never
holds them. The row still has to say what the automation does — description
and flow — from the config the loaded entity was built from, while staying
out of the YAML editor, which only saves to automations.yaml.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.selora_ai.websocket.automations import (
    _handle_websocket_get_automations,
)

_handler = _handle_websocket_get_automations.__wrapped__

_PACKAGE_AUTOMATION = {
    "id": "water_leak_alert_leak_detected",
    "alias": "Water Leak Alert — leak detected",
    "description": "Notify when a leak sensor detects moisture.",
    "triggers": [{"trigger": "state", "entity_id": "binary_sensor.laundry_leak", "to": "on"}],
    "actions": [{"action": "persistent_notification.create", "data": {"message": "Leak!"}}],
}


async def _list(hass: HomeAssistant) -> list[dict[str, Any]]:
    connection = MagicMock()
    with patch(
        "custom_components.selora_ai.websocket.automations._require_admin",
        return_value=True,
    ):
        await _handler(hass, connection, {"id": 1, "type": "selora_ai/get_automations"})
    connection.send_error.assert_not_called()
    result: list[dict[str, Any]] = connection.send_result.call_args.args[1]
    return result


async def test_package_automation_carries_its_description_and_flow(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "automation", {"automation": [_PACKAGE_AUTOMATION]})
    await hass.async_block_till_done()

    (row,) = await _list(hass)

    assert row["automation_id"] == ""
    assert row["description"] == "Notify when a leak sensor detects moisture."
    assert row["triggers"][0]["entity_id"] == "binary_sensor.laundry_leak"
    assert row["actions"][0]["action"] == "persistent_notification.create"
    assert row["yaml_text"] == ""
