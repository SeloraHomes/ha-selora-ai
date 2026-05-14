"""Tests for the get_entity_state, find_entities_by_area, and validate_action tools."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

from custom_components.selora_ai.mcp_server import (
    _tool_find_entities_by_area,
    _tool_get_entity_state,
    _tool_validate_action,
)


@pytest.fixture
async def setup_entities(hass: HomeAssistant):
    """Set up areas, a device, and entities with mixed area assignments."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)

    entry = MockConfigEntry(domain="test", entry_id="mock_entry_entities")
    entry.add_to_hass(hass)

    kitchen = area_reg.async_create("Kitchen")
    living = area_reg.async_create("Living Room")

    # Entity assigned directly via entity registry (kitchen)
    ent_reg.async_get_or_create(
        "light",
        "test",
        "kitchen_light_uid",
        suggested_object_id="kitchen_light",
    )
    ent_reg.async_update_entity("light.kitchen_light", area_id=kitchen.id)
    hass.states.async_set(
        "light.kitchen_light",
        "on",
        {"friendly_name": "Kitchen Light", "brightness": 200},
    )

    # Entity inherits area from its device (living room)
    device = dev_reg.async_get_or_create(
        config_entry_id="mock_entry_entities",
        identifiers={("test", "living_lamp")},
        name="Living Lamp",
    )
    dev_reg.async_update_device(device.id, area_id=living.id)
    ent_reg.async_get_or_create(
        "light",
        "test",
        "living_lamp_uid",
        device_id=device.id,
        suggested_object_id="living_lamp",
    )
    hass.states.async_set("light.living_lamp", "off", {"friendly_name": "Living Lamp"})

    # Climate entity in living room (different domain, same area as living_lamp)
    climate_device = dev_reg.async_get_or_create(
        config_entry_id="mock_entry_entities",
        identifiers={("test", "thermostat")},
        name="Thermostat",
    )
    dev_reg.async_update_device(climate_device.id, area_id=living.id)
    ent_reg.async_get_or_create(
        "climate",
        "test",
        "thermostat_uid",
        device_id=climate_device.id,
        suggested_object_id="thermostat",
    )
    hass.states.async_set(
        "climate.thermostat",
        "heat",
        {
            "friendly_name": "Living Thermostat",
            "temperature": 72,
            "current_temperature": 68,
            "hvac_action": "heating",
        },
    )

    # Unassigned entity (no area)
    ent_reg.async_get_or_create(
        "switch",
        "test",
        "unassigned_uid",
        suggested_object_id="unassigned",
    )
    hass.states.async_set("switch.unassigned", "off")

    return {"kitchen_id": kitchen.id, "living_id": living.id}


# ── get_entity_state ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_entity_state_returns_state(hass: HomeAssistant, setup_entities) -> None:
    result = await _tool_get_entity_state(hass, {"entity_id": "light.kitchen_light"})
    assert result["entity_id"] == "light.kitchen_light"
    assert result["state"] == "on"
    assert result["domain"] == "light"
    assert result["area"] == "Kitchen"
    assert result["friendly_name"] == "Kitchen Light"
    assert result["attributes"]["brightness"] == 200


@pytest.mark.asyncio
async def test_get_entity_state_climate_attrs(hass: HomeAssistant, setup_entities) -> None:
    result = await _tool_get_entity_state(hass, {"entity_id": "climate.thermostat"})
    assert result["state"] == "heat"
    assert result["attributes"]["temperature"] == 72
    assert result["attributes"]["current_temperature"] == 68
    assert result["attributes"]["hvac_action"] == "heating"


@pytest.mark.asyncio
async def test_get_entity_state_missing_id(hass: HomeAssistant) -> None:
    result = await _tool_get_entity_state(hass, {})
    assert "error" in result


@pytest.mark.asyncio
async def test_get_entity_state_unknown_entity(hass: HomeAssistant) -> None:
    result = await _tool_get_entity_state(hass, {"entity_id": "light.does_not_exist"})
    assert "error" in result
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_get_entity_state_malformed_id(hass: HomeAssistant) -> None:
    result = await _tool_get_entity_state(hass, {"entity_id": "no_dot"})
    assert "error" in result
    assert "malformed" in result["error"]


@pytest.mark.asyncio
async def test_get_entity_state_resolves_area_via_device(
    hass: HomeAssistant, setup_entities
) -> None:
    """Regression: entities assigned to an area only through their device
    must still report the room name. Without the device-area fallback this
    returned area: null and the LLM lost room context.
    """
    result = await _tool_get_entity_state(hass, {"entity_id": "light.living_lamp"})
    assert result["entity_id"] == "light.living_lamp"
    assert result["area"] == "Living Room"


@pytest.mark.asyncio
async def test_get_entity_state_no_area_when_neither_assigned(
    hass: HomeAssistant, setup_entities
) -> None:
    """Entities with neither entity- nor device-level area still return None."""
    result = await _tool_get_entity_state(hass, {"entity_id": "switch.unassigned"})
    assert result["entity_id"] == "switch.unassigned"
    assert result["area"] is None


# ── find_entities_by_area ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_entities_by_area_basic(hass: HomeAssistant, setup_entities) -> None:
    result = await _tool_find_entities_by_area(hass, {"area": "Kitchen"})
    assert result["count"] == 1
    assert result["entities"][0]["entity_id"] == "light.kitchen_light"
    assert result["area_matches"] == ["Kitchen"]


@pytest.mark.asyncio
async def test_find_entities_by_area_resolves_via_device(
    hass: HomeAssistant, setup_entities
) -> None:
    """Entity with no entity-level area should inherit its device's area."""
    result = await _tool_find_entities_by_area(hass, {"area": "Living Room"})
    entity_ids = {e["entity_id"] for e in result["entities"]}
    assert "light.living_lamp" in entity_ids
    assert "climate.thermostat" in entity_ids


@pytest.mark.asyncio
async def test_find_entities_by_area_case_insensitive(
    hass: HomeAssistant, setup_entities
) -> None:
    result = await _tool_find_entities_by_area(hass, {"area": "kitchen"})
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_find_entities_by_area_domain_filter(
    hass: HomeAssistant, setup_entities
) -> None:
    result = await _tool_find_entities_by_area(
        hass, {"area": "Living Room", "domain": "light"}
    )
    entity_ids = {e["entity_id"] for e in result["entities"]}
    assert entity_ids == {"light.living_lamp"}


@pytest.mark.asyncio
async def test_find_entities_by_area_no_match(hass: HomeAssistant, setup_entities) -> None:
    result = await _tool_find_entities_by_area(hass, {"area": "Basement"})
    assert result["count"] == 0
    assert result["entities"] == []
    assert result["area_matches"] == []


@pytest.mark.asyncio
async def test_find_entities_by_area_missing_area(hass: HomeAssistant) -> None:
    result = await _tool_find_entities_by_area(hass, {})
    assert "error" in result


# ── validate_action ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_action_valid_light_turn_on(
    hass: HomeAssistant, setup_entities
) -> None:
    result = await _tool_validate_action(
        hass,
        {
            "service": "light.turn_on",
            "entity_id": "light.kitchen_light",
            "data": {"brightness_pct": 80},
        },
    )
    assert result["valid"] is True
    assert result["errors"] == []
    assert result["domain"] == "light"
    assert "brightness_pct" in result["allowed_data_keys"]


@pytest.mark.asyncio
async def test_validate_action_rejects_unknown_domain(hass: HomeAssistant) -> None:
    result = await _tool_validate_action(
        hass,
        {"service": "lock.unlock", "entity_id": "lock.front_door"},
    )
    assert result["valid"] is False
    assert any("allowlist" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_action_rejects_unknown_verb(hass: HomeAssistant) -> None:
    result = await _tool_validate_action(
        hass,
        {"service": "light.explode", "entity_id": "light.kitchen_light"},
    )
    assert result["valid"] is False
    assert any("not a valid light service" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_action_rejects_unknown_entity(
    hass: HomeAssistant, setup_entities
) -> None:
    result = await _tool_validate_action(
        hass,
        {"service": "light.turn_on", "entity_id": "light.ghost"},
    )
    assert result["valid"] is False
    assert any("not known to Home Assistant" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_action_rejects_domain_mismatch(
    hass: HomeAssistant, setup_entities
) -> None:
    result = await _tool_validate_action(
        hass,
        {"service": "light.turn_on", "entity_id": "switch.unassigned"},
    )
    assert result["valid"] is False
    assert any("switch domain, not light" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_action_rejects_unsupported_param(
    hass: HomeAssistant, setup_entities
) -> None:
    result = await _tool_validate_action(
        hass,
        {
            "service": "light.turn_on",
            "entity_id": "light.kitchen_light",
            "data": {"volume_level": 0.5},
        },
    )
    assert result["valid"] is False
    assert any("unsupported parameters" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_action_malformed_service(hass: HomeAssistant) -> None:
    result = await _tool_validate_action(
        hass, {"service": "no_dot", "entity_id": "light.x"}
    )
    assert result["valid"] is False
    assert any("<domain>.<verb>" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_action_data_must_be_object(hass: HomeAssistant) -> None:
    result = await _tool_validate_action(
        hass,
        {
            "service": "light.turn_on",
            "entity_id": "light.kitchen_light",
            "data": "not-an-object",
        },
    )
    assert result["valid"] is False
    assert any("data must be an object" in e for e in result["errors"])
