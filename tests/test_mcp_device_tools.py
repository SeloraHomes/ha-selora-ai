"""Tests for the selora_list_devices and selora_get_device MCP tools."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

from custom_components.selora_ai.mcp_server import (
    _tool_get_device,
    _tool_list_devices,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _register_device(
    dev_reg: dr.DeviceRegistry,
    ent_reg: er.EntityRegistry,
    hass: HomeAssistant,
    *,
    name: str,
    manufacturer: str = "TestMfg",
    model: str = "TestModel",
    area_id: str | None = None,
    entities: list[tuple[str, str, str]] | None = None,
) -> dr.DeviceEntry:
    """Register a device and its entities.

    entities: list of (domain, object_id, state) tuples.
    """
    device = dev_reg.async_get_or_create(
        config_entry_id="mock_config_entry",
        identifiers={("test", name)},
        name=name,
        manufacturer=manufacturer,
        model=model,
    )
    if area_id:
        dev_reg.async_update_device(device.id, area_id=area_id)
        device = dev_reg.async_get(device.id)

    for domain, object_id, state in entities or []:
        entity_id = f"{domain}.{object_id}"
        ent_reg.async_get_or_create(
            domain,
            "test",
            f"{name}_{object_id}",
            device_id=device.id,
            suggested_object_id=object_id,
        )
        hass.states.async_set(entity_id, state)

    return device


@pytest.fixture
async def setup_home(hass: HomeAssistant):
    """Set up a home with areas, devices, and entities for testing."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)

    entry = MockConfigEntry(domain="test", entry_id="mock_config_entry")
    entry.add_to_hass(hass)

    # Create areas
    living_room = area_reg.async_create("Living Room")
    kitchen = area_reg.async_create("Kitchen")

    # Device 1: light in Living Room
    _register_device(
        dev_reg,
        ent_reg,
        hass,
        name="Hue Light",
        manufacturer="Philips",
        model="Hue White",
        area_id=living_room.id,
        entities=[("light", "living_room_light", "on")],
    )

    # Device 2: thermostat in Living Room
    _register_device(
        dev_reg,
        ent_reg,
        hass,
        name="Ecobee Thermostat",
        manufacturer="Ecobee",
        model="SmartThermostat",
        area_id=living_room.id,
        entities=[("climate", "living_room_thermostat", "heat")],
    )

    # Device 3: lock in Kitchen
    _register_device(
        dev_reg,
        ent_reg,
        hass,
        name="Front Door Lock",
        manufacturer="Schlage",
        model="Encode",
        area_id=kitchen.id,
        entities=[("lock", "front_door", "locked")],
    )

    # Device 4: device with no collector-domain entities (should be excluded)
    _register_device(
        dev_reg,
        ent_reg,
        hass,
        name="Weather Service",
        manufacturer="NWS",
        model="Forecast",
        entities=[("weather", "home", "sunny")],
    )

    return {
        "living_room_id": living_room.id,
        "kitchen_id": kitchen.id,
    }


# ── selora_list_devices tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_devices_returns_all(hass: HomeAssistant, setup_home) -> None:
    """All devices with collector-domain entities are listed."""
    result = await _tool_list_devices(hass, {})
    assert result["count"] == 3
    names = {d["name"] for d in result["devices"]}
    assert names == {"Hue Light", "Ecobee Thermostat", "Front Door Lock"}


@pytest.mark.asyncio
async def test_list_devices_excludes_non_collector_domains(
    hass: HomeAssistant, setup_home
) -> None:
    """Devices with only non-collector-domain entities are excluded."""
    result = await _tool_list_devices(hass, {})
    names = {d["name"] for d in result["devices"]}
    assert "Weather Service" not in names


@pytest.mark.asyncio
async def test_list_devices_filter_by_area(hass: HomeAssistant, setup_home) -> None:
    """Area filter returns only matching devices."""
    result = await _tool_list_devices(hass, {"area": "Kitchen"})
    assert result["count"] == 1
    assert result["devices"][0]["name"] == "Front Door Lock"


@pytest.mark.asyncio
async def test_list_devices_filter_by_area_case_insensitive(
    hass: HomeAssistant, setup_home
) -> None:
    """Area filter is case-insensitive."""
    result = await _tool_list_devices(hass, {"area": "living room"})
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_list_devices_filter_by_domain(hass: HomeAssistant, setup_home) -> None:
    """Domain filter returns only devices with matching entities."""
    result = await _tool_list_devices(hass, {"domain": "climate"})
    assert result["count"] == 1
    assert result["devices"][0]["name"] == "Ecobee Thermostat"


@pytest.mark.asyncio
async def test_list_devices_filter_area_and_domain(
    hass: HomeAssistant, setup_home
) -> None:
    """Combined area + domain filter."""
    result = await _tool_list_devices(hass, {"area": "Living Room", "domain": "light"})
    assert result["count"] == 1
    assert result["devices"][0]["name"] == "Hue Light"


@pytest.mark.asyncio
async def test_list_devices_no_match(hass: HomeAssistant, setup_home) -> None:
    """Returns empty list when no devices match filters."""
    result = await _tool_list_devices(hass, {"area": "Basement"})
    assert result["count"] == 0
    assert result["devices"] == []


@pytest.mark.asyncio
async def test_list_devices_includes_metadata(hass: HomeAssistant, setup_home) -> None:
    """Each device entry includes expected fields."""
    result = await _tool_list_devices(hass, {"area": "Kitchen"})
    device = result["devices"][0]
    assert device["manufacturer"] == "Schlage"
    assert device["model"] == "Encode"
    assert "lock" in device["domains"]
    entity_ids = [e["entity_id"] for e in device["entities"]]
    assert "lock.front_door" in entity_ids


@pytest.mark.asyncio
async def test_list_devices_includes_entity_states(
    hass: HomeAssistant, setup_home
) -> None:
    """list_devices returns entity states for frontend card rendering."""
    result = await _tool_list_devices(hass, {"area": "Kitchen"})
    device = result["devices"][0]
    assert len(device["entities"]) == 1
    assert device["entities"][0]["entity_id"] == "lock.front_door"
    assert device["entities"][0]["state"] == "locked"


# ── selora_get_device tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_device_returns_detail(hass: HomeAssistant, setup_home) -> None:
    """Full device detail is returned for a valid device_id."""
    # First list to get a device_id
    devices = (await _tool_list_devices(hass, {"area": "Kitchen"}))["devices"]
    device_id = devices[0]["device_id"]

    result = await _tool_get_device(hass, {"device_id": device_id})
    assert result["name"] == "Front Door Lock"
    assert result["manufacturer"] == "Schlage"
    assert result["model"] == "Encode"
    assert result["area"] == "Kitchen"
    assert len(result["entities"]) == 1
    assert result["entities"][0]["entity_id"] == "lock.front_door"
    assert result["entities"][0]["state"] == "locked"


@pytest.mark.asyncio
async def test_get_device_missing_id(hass: HomeAssistant) -> None:
    """Error when device_id is not provided."""
    result = await _tool_get_device(hass, {})
    assert "error" in result


@pytest.mark.asyncio
async def test_get_device_not_found(hass: HomeAssistant) -> None:
    """Error when device_id doesn't exist."""
    result = await _tool_get_device(hass, {"device_id": "nonexistent_id"})
    assert "error" in result
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_get_device_includes_state_attributes(
    hass: HomeAssistant, setup_home
) -> None:
    """Climate device includes domain-specific attributes."""
    # Set climate state with attributes
    hass.states.async_set(
        "climate.living_room_thermostat",
        "heat",
        {
            "friendly_name": "Ecobee Thermostat",
            "temperature": 72,
            "current_temperature": 68,
            "hvac_action": "heating",
        },
    )

    devices = (await _tool_list_devices(hass, {"domain": "climate"}))["devices"]
    device_id = devices[0]["device_id"]

    result = await _tool_get_device(hass, {"device_id": device_id})
    entity = result["entities"][0]
    assert entity["attributes"]["temperature"] == 72
    assert entity["attributes"]["current_temperature"] == 68
    assert entity["attributes"]["hvac_action"] == "heating"
