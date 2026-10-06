"""Tests for installing an update from chat, behind a confirmation card."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai import _apply_destructive_actions
from custom_components.selora_ai.llm_client.command_policy import _pending_destructive_from_log
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import COMMAND_TOOL_NAMES, CONFIG_TOOL_NAMES


class _Firmware(UpdateEntity):
    def __init__(self, features: UpdateEntityFeature) -> None:
        self.entity_id = "update.lamp_firmware"
        self._attr_name = "Lamp firmware"
        self._attr_installed_version = "1.0"
        self._attr_latest_version = "1.1"
        self._attr_supported_features = features
        self.installs: list[tuple[str | None, bool]] = []

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        self.installs.append((version, backup))


@pytest.fixture
async def firmware(hass: HomeAssistant) -> _Firmware:
    assert await async_setup_component(hass, "update", {})
    entity = _Firmware(
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.BACKUP
        | UpdateEntityFeature.SPECIFIC_VERSION
    )
    await hass.data["update"].async_add_entities([entity])
    await hass.async_block_till_done()
    return entity


async def _card(hass: HomeAssistant) -> tuple[dict[str, Any], dict[str, Any]]:
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    result = await executor.execute("install_update", {"entity_id": "update.lamp_firmware"})
    assert result.get("requires_approval"), result
    (action,) = _pending_destructive_from_log(executor.call_log)
    return result, {**action, "fingerprint": result["destructive"]["fingerprint"]}


async def test_an_update_is_carded_then_installed_with_a_backup(
    hass: HomeAssistant, firmware: _Firmware
) -> None:
    result, action = await _card(hass)

    assert "1.0 → 1.1" in result["destructive"]["label"]
    assert "after a backup" in result["destructive"]["label"]
    assert firmware.installs == []

    applied, errors = await _apply_destructive_actions(hass, [action])
    await hass.async_block_till_done()

    assert errors == [] and applied
    assert firmware.installs == [("1.1", True)]


async def test_a_newer_version_than_the_card_named_is_not_installed(
    hass: HomeAssistant, firmware: _Firmware
) -> None:
    _result, action = await _card(hass)
    firmware._attr_latest_version = "1.2"
    firmware.async_write_ha_state()

    _applied, errors = await _apply_destructive_actions(hass, [action])
    await hass.async_block_till_done()

    assert "different version" in errors[0]
    assert firmware.installs == []


async def test_nothing_to_install_is_refused(hass: HomeAssistant, firmware: _Firmware) -> None:
    firmware._attr_installed_version = "1.1"
    firmware.async_write_ha_state()

    result = await ToolExecutor(hass, MagicMock(), is_admin=True).execute(
        "install_update", {"entity_id": "update.lamp_firmware"}
    )

    assert "no update waiting" in result["error"]


def test_install_update_is_in_both_lanes() -> None:
    assert "install_update" in COMMAND_TOOL_NAMES
    assert "install_update" in CONFIG_TOOL_NAMES


async def test_a_backup_that_can_no_longer_be_made_stops_the_install(
    hass: HomeAssistant, firmware: _Firmware
) -> None:
    _result, action = await _card(hass)
    firmware._attr_supported_features = UpdateEntityFeature.INSTALL
    firmware.async_write_ha_state()

    _applied, errors = await _apply_destructive_actions(hass, [action])
    await hass.async_block_till_done()

    assert "backup the card promised" in errors[0]
    assert firmware.installs == []


async def test_another_update_under_the_same_entity_id_is_not_installed(
    hass: HomeAssistant, firmware: _Firmware
) -> None:
    """The card names a registry entry, not just an entity_id: one removed and
    replaced while the card is open answers to the same id."""
    import json

    _result, action = await _card(hass)
    promise = json.loads(action["fingerprint"])
    action["fingerprint"] = json.dumps({**promise, "id": "a-registry-entry-since-removed"})

    _applied, errors = await _apply_destructive_actions(hass, [action])
    await hass.async_block_till_done()

    assert "not the update that was shown" in errors[0]
    assert firmware.installs == []
