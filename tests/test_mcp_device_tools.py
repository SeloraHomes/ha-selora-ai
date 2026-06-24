"""Tests for the selora_list_devices and selora_get_device MCP tools."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
import pytest

from custom_components.selora_ai.mcp_server import (
    _tool_get_device,
    _tool_get_device_triggers,
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
async def test_list_devices_excludes_non_collector_domains(hass: HomeAssistant, setup_home) -> None:
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
async def test_list_devices_filter_area_and_domain(hass: HomeAssistant, setup_home) -> None:
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
async def test_list_devices_includes_entity_states(hass: HomeAssistant, setup_home) -> None:
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
async def test_get_device_includes_state_attributes(hass: HomeAssistant, setup_home) -> None:
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


@pytest.mark.asyncio
async def test_get_device_exposes_zha_ieee(hass: HomeAssistant) -> None:
    """A ZHA device surfaces its IEEE address as `zha_ieee` plus raw connections.

    This is what lets the LLM build a `zha_event` button-press trigger without
    asking the user to paste the IEEE by hand.
    """
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain="zha", entry_id="zha_entry")
    entry.add_to_hass(hass)

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    ieee = "00:15:8d:00:02:aa:bb:cc"
    device = dev_reg.async_get_or_create(
        config_entry_id="zha_entry",
        identifiers={("zha", ieee)},
        connections={(dr.CONNECTION_ZIGBEE, ieee)},
        name="Aqara Smart Button",
        manufacturer="Aqara",
        model="WXKG11LM",
    )
    # Needs at least one collector-domain entity to be returned.
    ent_reg.async_get_or_create(
        "sensor",
        "zha",
        "aqara_button_battery",
        device_id=device.id,
        suggested_object_id="aqara_button_battery",
    )
    hass.states.async_set("sensor.aqara_button_battery", "100")

    result = await _tool_get_device(hass, {"device_id": device.id})
    assert result["zha_ieee"] == ieee
    assert [dr.CONNECTION_ZIGBEE, ieee] in result["connections"]
    assert ["zha", ieee] in result["identifiers"]


@pytest.mark.asyncio
async def test_get_device_no_zha_ieee_for_non_zigbee(
    hass: HomeAssistant, setup_home
) -> None:
    """Non-Zigbee devices omit `zha_ieee` (no spurious key)."""
    devices = (await _tool_list_devices(hass, {"domain": "light"}))["devices"]
    device_id = devices[0]["device_id"]

    result = await _tool_get_device(hass, {"device_id": device_id})
    assert "zha_ieee" not in result
    assert "connections" in result
    assert "identifiers" in result


# ── selora_get_device_triggers tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_device_triggers_missing_id(hass: HomeAssistant) -> None:
    """Error when device_id is not provided."""
    result = await _tool_get_device_triggers(hass, {})
    assert "error" in result


@pytest.mark.asyncio
async def test_get_device_triggers_not_found(hass: HomeAssistant) -> None:
    """Error when device_id doesn't exist."""
    result = await _tool_get_device_triggers(hass, {"device_id": "nonexistent_id"})
    assert "error" in result
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_get_device_triggers_returns_list(hass: HomeAssistant, setup_home) -> None:
    """The result is always a device_id + list of trigger blocks + count.

    A light device registers HA's built-in toggle device triggers, so this also
    confirms real triggers come back as ready-to-use `platform: device` blocks.
    """
    devices = (await _tool_list_devices(hass, {"domain": "light"}))["devices"]
    device_id = devices[0]["device_id"]

    result = await _tool_get_device_triggers(hass, {"device_id": device_id})
    assert result["device_id"] == device_id
    assert isinstance(result["triggers"], list)
    assert result["count"] == len(result["triggers"])
    for trigger in result["triggers"]:
        assert trigger["device_id"] == device_id
        assert trigger.get("platform") == "device" or trigger.get("trigger") == "device"


@pytest.mark.asyncio
async def test_get_device_triggers_passthrough(
    hass: HomeAssistant, setup_home, monkeypatch
) -> None:
    """Device-trigger blocks from HA are returned verbatim for the LLM to use."""
    devices = (await _tool_list_devices(hass, {"domain": "light"}))["devices"]
    device_id = devices[0]["device_id"]

    block = {
        "platform": "device",
        "domain": "zha",
        "device_id": device_id,
        "type": "remote_button_short_press",
        "subtype": "turn_on",
    }

    async def _fake_get_automations(_hass, _atype, device_ids):
        return {device_ids[0]: [block]}

    monkeypatch.setattr(
        "homeassistant.components.device_automation.async_get_device_automations",
        _fake_get_automations,
    )

    result = await _tool_get_device_triggers(hass, {"device_id": device_id})
    assert result["count"] == 1
    assert result["triggers"][0] == block


@pytest.mark.asyncio
async def test_get_device_triggers_invalid_config_is_empty(
    hass: HomeAssistant, setup_home, monkeypatch
) -> None:
    """An InvalidDeviceAutomationConfig degrades to an empty list, not a raised error."""
    from homeassistant.components.device_automation import InvalidDeviceAutomationConfig

    devices = (await _tool_list_devices(hass, {"domain": "light"}))["devices"]
    device_id = devices[0]["device_id"]

    async def _raise(_hass, _atype, _device_ids):
        raise InvalidDeviceAutomationConfig("bad config")

    monkeypatch.setattr(
        "homeassistant.components.device_automation.async_get_device_automations",
        _raise,
    )

    result = await _tool_get_device_triggers(hass, {"device_id": device_id})
    assert result["triggers"] == []
    assert result["count"] == 0
