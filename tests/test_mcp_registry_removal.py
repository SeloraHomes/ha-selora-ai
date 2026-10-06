"""Tests for removing a device or an entity over MCP, after a confirmation."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_integration,
)

from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import TOOL_REMOVE_DEVICE, TOOL_REMOVE_ENTITY


def _speaker(
    hass: HomeAssistant, *, releases: bool | None = True, domain: str = "sonos"
) -> tuple[dr.DeviceEntry, str]:
    """A one-entity device; ``releases`` is what its integration's removal hook
    answers, None for an integration without one."""
    entry = MockConfigEntry(domain=domain, title=domain.title())
    entry.add_to_hass(hass)
    entry.supports_remove_device = releases is not None
    mock_integration(
        hass,
        MockModule(
            domain,
            async_remove_config_entry_device=(
                AsyncMock(return_value=releases) if releases is not None else None
            ),
        ),
    )
    # Running: only a running integration can say an entity is gone.
    entry.mock_state(hass, ConfigEntryState.LOADED)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(domain, "bedroom")}, name="Bedroom"
    )
    entity = er.async_get(hass).async_get_or_create(
        "media_player", domain, "bedroom", device_id=device.id, config_entry=entry
    )
    return device, entity.entity_id


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[tool](hass, arguments)


async def test_a_device_is_removed_only_once_confirmed(hass: HomeAssistant) -> None:
    device, entity_id = _speaker(hass)

    asked = await _mcp(hass, TOOL_REMOVE_DEVICE, device_id=device.id)

    assert asked["requires_confirmation"] is True
    assert asked["entities"] == [entity_id]
    assert dr.async_get(hass).async_get(device.id) is not None

    removed = await _mcp(hass, TOOL_REMOVE_DEVICE, device_id=device.id, confirmed=True)

    assert removed["status"] == "removed", removed
    assert dr.async_get(hass).async_get(device.id) is None
    assert er.async_get(hass).async_get(entity_id) is None


async def test_an_integration_that_keeps_the_device_wins(hass: HomeAssistant) -> None:
    device, _ = _speaker(hass, releases=False)

    result = await _mcp(hass, TOOL_REMOVE_DEVICE, device_id=device.id, confirmed=True)

    assert "was not removed" in result["error"]
    assert dr.async_get(hass).async_get(device.id) is not None


async def test_an_integration_without_device_removal_is_refused_up_front(
    hass: HomeAssistant,
) -> None:
    device, _ = _speaker(hass, releases=None)

    result = await _mcp(hass, TOOL_REMOVE_DEVICE, device_id=device.id)

    assert "cannot be removed from here" in result["error"]


async def test_a_restored_entity_is_removed_once_confirmed(hass: HomeAssistant) -> None:
    _, entity_id = _speaker(hass)
    hass.states.async_set(entity_id, "unavailable", {"restored": True})

    asked = await _mcp(hass, TOOL_REMOVE_ENTITY, entity_id=entity_id)
    assert asked["requires_confirmation"] is True
    removed = await _mcp(
        hass,
        TOOL_REMOVE_ENTITY,
        entity_id=entity_id,
        confirmed=True,
        registry_id=asked["registry_id"],
    )

    assert removed["status"] == "removed", removed
    assert er.async_get(hass).async_get(entity_id) is None


async def test_a_missing_state_counts_only_while_its_integration_runs(
    hass: HomeAssistant,
) -> None:
    _, entity_id = _speaker(hass)
    owner = hass.config_entries.async_entries("sonos")[0]

    owner.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    starting = await _mcp(hass, TOOL_REMOVE_ENTITY, entity_id=entity_id, confirmed=True)
    # What Home Assistant writes for every entity of an integration not up yet.
    hass.states.async_set(entity_id, "unavailable", {"restored": True})
    restored_while_retrying = await _mcp(
        hass, TOOL_REMOVE_ENTITY, entity_id=entity_id, confirmed=True
    )
    hass.states.async_remove(entity_id)
    owner.mock_state(hass, ConfigEntryState.LOADED)
    er.async_get(hass).async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    disabled = await _mcp(hass, TOOL_REMOVE_ENTITY, entity_id=entity_id, confirmed=True)
    er.async_get(hass).async_update_entity(entity_id, disabled_by=None)
    registry_id = er.async_get(hass).async_get(entity_id).id
    orphaned = await _mcp(
        hass, TOOL_REMOVE_ENTITY, entity_id=entity_id, confirmed=True, registry_id=registry_id
    )

    assert "still provided" in starting["error"]
    assert "still provided" in restored_while_retrying["error"]
    assert "still provided" in disabled["error"]
    assert orphaned["status"] == "removed", orphaned


async def test_an_entity_still_provided_is_refused(hass: HomeAssistant) -> None:
    _, entity_id = _speaker(hass)
    hass.states.async_set(entity_id, "playing")

    result = await _mcp(hass, TOOL_REMOVE_ENTITY, entity_id=entity_id, confirmed=True)

    assert "still provided" in result["error"]
    assert er.async_get(hass).async_get(entity_id) is not None


async def test_selora_never_removes_its_own(hass: HomeAssistant) -> None:
    device, entity_id = _speaker(hass, domain=DOMAIN)
    sensor = er.async_get(hass).async_get_or_create("sensor", DOMAIN, "status")

    device_result = await _mcp(hass, TOOL_REMOVE_DEVICE, device_id=device.id, confirmed=True)
    entity_result = await _mcp(hass, TOOL_REMOVE_ENTITY, entity_id=sensor.entity_id, confirmed=True)

    assert "own device" in device_result["error"]
    assert "own entities" in entity_result["error"]


async def test_removing_needs_admin() -> None:
    assert TOOL_REMOVE_DEVICE in mcp_access._ADMIN_TOOLS
    assert TOOL_REMOVE_ENTITY in mcp_access._ADMIN_TOOLS


async def test_only_the_entity_shown_is_removed(hass: HomeAssistant) -> None:
    """A rename frees the entity_id for another entity; the registry_id from
    the preview is what pins the one the user agreed to."""
    _, entity_id = _speaker(hass)
    hass.states.async_set(entity_id, "unavailable", {"restored": True})
    asked = await _mcp(hass, TOOL_REMOVE_ENTITY, entity_id=entity_id)
    registry = er.async_get(hass)
    registry.async_update_entity(entity_id, new_entity_id="media_player.moved")
    other = registry.async_get_or_create(
        "media_player",
        "sonos",
        "kitchen",
        suggested_object_id=entity_id.split(".")[1],
        config_entry=hass.config_entries.async_entries("sonos")[0],
    )
    assert other.entity_id == entity_id
    hass.states.async_set(entity_id, "unavailable", {"restored": True})

    result = await _mcp(
        hass,
        TOOL_REMOVE_ENTITY,
        entity_id=entity_id,
        confirmed=True,
        registry_id=asked["registry_id"],
    )
    unpinned = await _mcp(hass, TOOL_REMOVE_ENTITY, entity_id=entity_id, confirmed=True)

    assert "not the entity that was shown" in result["error"]
    assert "not the entity that was shown" in unpinned["error"]
    assert registry.async_get(entity_id) is not None


def _with_part(hass: HomeAssistant) -> tuple[dr.DeviceEntry, Any, str]:
    if not hasattr(dr.DeviceRegistry, "async_get_or_create_child"):
        pytest.skip("child devices are new in Home Assistant 2026.9")
    device, _ = _speaker(hass)
    registry = dr.async_get(hass)
    part = registry.async_get_or_create_child(
        config_entry_id=hass.config_entries.async_entries("sonos")[0].entry_id,
        identifiers={("sonos", "bedroom-sub")},
        name="Subwoofer",
        parent_device_id=device.id,
    )
    entity = er.async_get(hass).async_get_or_create(
        "sensor", "sonos", "bedroom-sub-level", device_id=part.id
    )
    return device, part, entity.entity_id


async def test_a_parent_device_names_its_parts(hass: HomeAssistant) -> None:
    device, part, part_entity = _with_part(hass)

    asked = await _mcp(hass, TOOL_REMOVE_DEVICE, device_id=device.id)

    assert asked["parts"] == [{"id": part.id, "name": "Subwoofer"}]
    assert part_entity in asked["entities"]
    assert "1 part" in asked["hint"]


async def test_a_part_is_removed_with_its_parent_not_alone(hass: HomeAssistant) -> None:
    _device, part, _ = _with_part(hass)

    result = await _mcp(hass, TOOL_REMOVE_DEVICE, device_id=part.id, confirmed=True)

    assert "is part of Bedroom" in result["error"]
    assert dr.async_get(hass).async_get(part.id) is not None


async def test_what_uses_a_device_is_counted_across_its_entities(hass: HomeAssistant) -> None:
    from unittest.mock import patch

    from custom_components.selora_ai import registry_removal

    device, entity_id = _speaker(hass)
    second = er.async_get(hass).async_get_or_create(
        "sensor", "sonos", "bedroom-volume", device_id=device.id
    )
    refs = {
        entity_id: {"automations": ["automation.a", "automation.shared"]},
        second.entity_id: {"automations": ["automation.b", "automation.shared"]},
    }
    with (
        patch(
            "custom_components.selora_ai.group_manager.group_dependents",
            side_effect=lambda _h, eid: refs.get(eid, {}),
        ),
        patch(
            "custom_components.selora_ai.recipes.dashboard.async_dashboards_with_entity",
            AsyncMock(return_value=([], [])),
        ),
    ):
        used = await registry_removal._used_by(hass, [entity_id, second.entity_id])

    assert used == ["3 automations"]


async def test_an_automation_using_the_device_itself_is_counted(hass: HomeAssistant) -> None:
    """A device trigger names the device, not an entity."""
    from unittest.mock import patch

    from custom_components.selora_ai import registry_removal

    device, _ = _speaker(hass)
    with patch.object(
        registry_removal,
        "_device_finders",
        return_value=[
            ("automations", lambda _h, did: ["automation.doorbell"] if did == device.id else [])
        ],
    ):
        asked = await _mcp(hass, TOOL_REMOVE_DEVICE, device_id=device.id)

    assert "1 automation" in asked["hint"]
