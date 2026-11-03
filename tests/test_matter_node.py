"""Matter node ids: what the Matter server and Thread views call a device
("Node 31", "@1:1f"), resolved to the device Home Assistant names.

Asked "which device is Node 31 @1:1f?", a name search matched an unrelated
tracker and the chat sent the user to Settings to compare addresses by hand.
The id is in the device registry all along, so every read that names a device
must accept it and every read that describes one must carry it.
"""

from __future__ import annotations

from typing import Any

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

from custom_components.selora_ai.matter_node import matter_node, node_ids_in
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.entities import (
    _tool_get_device,
    _tool_search_entities,
)
from custom_components.selora_ai.mcp_server.names import TOOL_GET_NETWORK_HEALTH
from custom_components.selora_ai.registry_manager import resolve_device

_FABRIC = "8A1C2B3D4E5F6071"


def _identifier(node_id: int, suffix: str = "MatterNodeDevice") -> tuple[str, str]:
    # matter/helpers.get_device_id, as matter/adapter.py registers it.
    return ("matter", f"deviceid_{_FABRIC}-{node_id:016X}-{suffix}")


def _entry(hass: HomeAssistant, domain: str) -> MockConfigEntry:
    # The real component imports its server client, which is not installed.
    mock_integration(hass, MockModule(domain))
    entry = MockConfigEntry(domain=domain, title=domain)
    entry.add_to_hass(hass)
    return entry


def _device(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    name: str,
    identifier: tuple[str, str],
    entity_id: str,
    state: str = "on",
) -> dr.DeviceEntry:
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={identifier},
        name=name,
        manufacturer="Eve",
        model="Energy",
    )
    domain, object_id = entity_id.split(".", 1)
    er.async_get(hass).async_get_or_create(
        domain,
        entry.domain,
        f"{name}-{entity_id}",
        suggested_object_id=object_id,
        config_entry=entry,
        device_id=device.id,
    )
    hass.states.async_set(entity_id, state, {"friendly_name": name})
    return device


@pytest.fixture
def home(hass: HomeAssistant) -> dict[str, dr.DeviceEntry]:
    matter = _entry(hass, "matter")
    other = _entry(hass, "ibeacon")
    return {
        "plug": _device(hass, matter, "Kitchen plug", _identifier(31), "switch.kitchen_plug"),
        "bridge": _device(hass, matter, "Hue bridge", _identifier(7), "button.hue_bridge_identify"),
        "bridged": _device(
            hass, matter, "Hall motion", _identifier(7, "12"), "binary_sensor.hall_motion"
        ),
        # The tracker the name search returned for "Node 31 @1:1f".
        "beacon": _device(
            hass,
            other,
            "Se1735ca0b1686310C2B1D",
            ("ibeacon", "se1735"),
            "device_tracker.se1735ca0b1686310c2b1d",
            "not_home",
        ),
    }


# ── Reading the registry and the reference ─────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Node 31 @1:1f", {31}),
        ("which device is node 31", {31}),
        ("node id #31", {31}),
        ("thread node @2:1F", {31}),
        ("0x1f", {31}),
        (f"{_FABRIC}-000000000000001F", {31}),
        ("Node 31 and node 7", {31, 7}),
        ("kitchen plug", set()),
        ("31 degrees", set()),
        ("Node-RED", set()),
    ],
)
def test_node_ids_are_read_from_how_users_write_them(text: str, expected: set[int]) -> None:
    assert node_ids_in(text) == expected


def test_a_bridged_device_carries_its_bridge_node_and_endpoint(
    home: dict[str, dr.DeviceEntry],
) -> None:
    assert matter_node(home["plug"]) == {"node_id": 31, "node_hex": "0x1F", "fabric": _FABRIC}
    assert matter_node(home["bridged"]) == {
        "node_id": 7,
        "node_hex": "0x7",
        "fabric": _FABRIC,
        "bridged_endpoint": 12,
    }
    assert matter_node(home["beacon"]) is None


# ── The reads ──────────────────────────────────────────────────────────────


async def test_search_resolves_a_node_reference_to_that_device_only(
    hass: HomeAssistant, home: dict[str, dr.DeviceEntry]
) -> None:
    result = await _tool_search_entities(hass, {"query": "Node 31 @1:1f"})

    assert [m["entity_id"] for m in result["matches"]] == ["switch.kitchen_plug"]
    (node,) = result["matter_nodes"]
    assert node["device_id"] == home["plug"].id
    assert node["name"] == "Kitchen plug"
    assert node["node_id"] == 31
    assert "hint" not in result


async def test_search_for_a_bridge_node_lists_the_devices_behind_it(
    hass: HomeAssistant, home: dict[str, dr.DeviceEntry]
) -> None:
    result = await _tool_search_entities(hass, {"query": "node 7"})

    assert [n["name"] for n in result["matter_nodes"]] == ["Hue bridge", "Hall motion"]
    assert {m["entity_id"] for m in result["matches"]} == {
        "button.hue_bridge_identify",
        "binary_sensor.hall_motion",
    }


async def test_a_node_no_device_is_on_says_so_beside_the_name_matches(
    hass: HomeAssistant, home: dict[str, dr.DeviceEntry]
) -> None:
    result = await _tool_search_entities(hass, {"query": "Node 99"})

    assert "matter_nodes" not in result
    assert "99 (0x63)" in result["matter_note"]
    assert "not that node" in result["matter_note"]


async def test_get_device_carries_the_node_and_accepts_it_as_the_reference(
    hass: HomeAssistant, home: dict[str, dr.DeviceEntry]
) -> None:
    result = await _tool_get_device(hass, {"device_id": "Node 31"})

    assert result["device_id"] == home["plug"].id
    assert result["matter"]["node_hex"] == "0x1F"


async def test_a_node_names_its_own_device_not_the_ones_bridged_behind_it(
    hass: HomeAssistant, home: dict[str, dr.DeviceEntry]
) -> None:
    device, error = resolve_device(hass, "node 7")

    assert error is None
    assert device is not None and device.id == home["bridge"].id


async def test_a_device_named_like_a_node_is_still_found_by_its_name(
    hass: HomeAssistant, home: dict[str, dr.DeviceEntry]
) -> None:
    named = _device(
        hass, _entry(hass, "zha"), "Node 31", ("zha", "aa:bb"), "sensor.node_31_temperature"
    )

    device, _ = resolve_device(hass, "Node 31")

    assert device is not None and device.id == named.id


async def test_network_health_names_each_matter_device_by_its_node(
    hass: HomeAssistant, home: dict[str, dr.DeviceEntry]
) -> None:
    hass.config_entries.async_entries("matter")[0].mock_state(hass, ConfigEntryState.LOADED)
    handler = mcp_dispatch._get_tool_handlers()[TOOL_GET_NETWORK_HEALTH]

    (network,) = (await handler(hass, {"protocol": "matter", "all_devices": True}))["networks"]

    rows: dict[str, Any] = {row["name"]: row for row in network["devices"]}
    assert rows["Kitchen plug"]["node_id"] == 31
    assert rows["Kitchen plug"]["node_hex"] == "0x1F"
    assert rows["Hall motion"]["bridged_endpoint"] == 12


async def test_a_missing_node_is_named_beside_one_that_resolved(
    hass: HomeAssistant, home: dict[str, dr.DeviceEntry]
) -> None:
    result = await _tool_search_entities(hass, {"query": "Node 31 and node 99"})

    assert [n["node_id"] for n in result["matter_nodes"]] == [31]
    assert "99 (0x63)" in result["matter_note"]
    assert "31 (0x1F)" not in result["matter_note"]
