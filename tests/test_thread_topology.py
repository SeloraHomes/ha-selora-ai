"""The Thread mesh, read from what each Matter node reports about itself.

The Matter server client is stood in by its shape: ``get_nodes()`` returning
nodes whose ``node_data.attributes`` hold the raw attribute cache, structs
keyed by field id and octet strings in base64, as the server sends them.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
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

from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.entities import _tool_search_entities
from custom_components.selora_ai.mcp_server.names import TOOL_GET_NETWORK_HEALTH
from custom_components.selora_ai.thread_topology import (
    ext_address,
    ext_addresses_in,
    read_thread_nodes,
)

_FABRIC = "8A1C2B3D4E5F6071"
_PLUG = "1A2B3C4D5E6F7081"  # a router
_SENSOR = "2B3C4D5E6F708192"  # a sleepy end device under the plug
_BORDER = "9F8E7D6C5B4A3921"  # heard by both, owned by no node of ours


def _b64(address: str) -> str:
    return base64.b64encode(bytes.fromhex(address)).decode()


def _neighbor(address: str, rssi: int, *, child: bool = False, rx_on: bool = True) -> dict:
    return {
        "0": int(address, 16),
        "2": 0x400,
        "5": 3,
        "6": rssi,
        "7": rssi - 2,
        "10": rx_on,
        "13": child,
    }


def _node(node_id: int, attributes: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(node_id=node_id, node_data=SimpleNamespace(attributes=attributes))


def _thread_attrs(address: str, role: int, neighbors: list[dict] | None) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "0/51/0": [{"0": "MyThread", "4": _b64(address), "7": 4}],
        "0/53/0": 15,
        "0/53/1": role,
        "0/53/2": "MyHome",
    }
    if neighbors is not None:
        attrs["0/53/7"] = neighbors
    return attrs


def _client() -> SimpleNamespace:
    nodes = [
        _node(
            31,
            _thread_attrs(
                _PLUG,
                5,
                [_neighbor(_SENSOR, -88, child=True, rx_on=False), _neighbor(_BORDER, -60)],
            ),
        ),
        # Sleepy: no neighbour table of its own.
        _node(32, _thread_attrs(_SENSOR, 2, None)),
        # On Wi-Fi: no Thread attributes at all.
        _node(33, {"0/51/0": [{"0": "wlan0", "4": _b64("AABBCCDDEEFF0011"), "7": 1}]}),
    ]
    return SimpleNamespace(get_nodes=lambda: nodes)


@pytest.fixture
def home(hass: HomeAssistant) -> dict[str, dr.DeviceEntry]:
    mock_integration(hass, MockModule("matter"))
    entry = MockConfigEntry(domain="matter", title="Matter")
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = SimpleNamespace(adapter=SimpleNamespace(matter_client=_client()))
    devices: dict[str, dr.DeviceEntry] = {}
    for node_id, name, entity_id in (
        (31, "Kitchen plug", "switch.kitchen_plug"),
        (32, "Door sensor", "binary_sensor.door"),
    ):
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={("matter", f"deviceid_{_FABRIC}-{node_id:016X}-MatterNodeDevice")},
            name=name,
        )
        domain, object_id = entity_id.split(".", 1)
        er.async_get(hass).async_get_or_create(
            domain,
            "matter",
            name,
            suggested_object_id=object_id,
            config_entry=entry,
            device_id=device.id,
        )
        hass.states.async_set(entity_id, "on", {"friendly_name": name})
        devices[name] = device
    return devices


async def _matter_network(hass: HomeAssistant) -> dict[str, Any]:
    handler = mcp_dispatch._get_tool_handlers()[TOOL_GET_NETWORK_HEALTH]
    (network,) = (await handler(hass, {"protocol": "matter", "all_devices": True}))["networks"]
    return network


# ── Reading the attribute cache ────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [int(_PLUG, 16), _PLUG, f"0x{_PLUG.lower()}", _b64(_PLUG), bytes.fromhex(_PLUG)],
)
def test_an_extended_address_reads_the_same_from_every_form(value: Any) -> None:
    assert ext_address(value) == _PLUG


def test_a_real_node_dump_reads() -> None:
    # Verbatim from core's tests/components/matter/fixtures/nodes/silabs_fan.json.
    attributes = {
        "0/51/0": [
            {"0": "MyHome", "1": True, "2": None, "3": None, "4": "Hh33j1J0OU4=", "5": [], "7": 4}
        ],
        "0/53/1": 5,
        "0/53/7": [
            {
                "0": 7061919368606463488,
                "1": 17,
                "2": 3072,
                "3": 1729386,
                "4": 111166,
                "5": 3,
                "6": -41,
                "7": -42,
                "8": 0,
                "9": 0,
                "10": True,
                "11": True,
                "12": True,
                "13": False,
            }
        ],
    }

    node = read_thread_nodes(SimpleNamespace(get_nodes=lambda: [_node(1, attributes)]))[1]

    assert node["ext_address"] == "1E1DF78F5274394E"
    assert node["links"] == [
        {
            "ext_address": f"{7061919368606463488:016X}",
            "rloc16": 3072,
            "lqi": 3,
            "avg_rssi": -41,
            "last_rssi": -42,
            "rx_on_when_idle": True,
            "is_child": False,
        }
    ]


def test_a_value_that_is_not_an_address_is_none() -> None:
    assert ext_address("not an address") is None
    assert ext_address(True) is None
    assert ext_address(_b64("AABB")) is None


def test_thread_nodes_carry_role_address_and_links_and_skip_wifi() -> None:
    nodes = read_thread_nodes(_client())

    assert set(nodes) == {31, 32}
    assert nodes[31]["role"] == "router"
    assert nodes[31]["ext_address"] == _PLUG
    assert nodes[31]["network_name"] == "MyHome"
    assert [link["ext_address"] for link in nodes[31]["links"]] == [_SENSOR, _BORDER]
    assert nodes[31]["links"][0]["is_child"] is True
    assert nodes[32]["links"] == []


def test_camel_case_struct_keys_are_read_too() -> None:
    client = SimpleNamespace(
        get_nodes=lambda: [
            _node(5, {"0/53/1": 3, "0/53/7": [{"extAddress": int(_PLUG, 16), "averageRssi": -70}]})
        ]
    )

    (link,) = read_thread_nodes(client)[5]["links"]

    assert link == {"ext_address": _PLUG, "avg_rssi": -70}


def test_a_client_of_another_shape_reads_as_no_thread() -> None:
    assert read_thread_nodes(None) == {}
    assert read_thread_nodes(SimpleNamespace()) == {}


def test_addresses_are_found_in_text_but_not_inside_an_operational_id() -> None:
    assert ext_addresses_in(f"what is {_PLUG.lower()}?") == {_PLUG}
    assert ext_addresses_in(f"0x{_PLUG}") == {_PLUG}
    assert ext_addresses_in(f"{_FABRIC}-000000000000001F") == set()


# ── Network health ─────────────────────────────────────────────────────────


async def test_each_node_names_its_links_strongest_first(
    hass: HomeAssistant, home: dict[str, dr.DeviceEntry]
) -> None:
    network = await _matter_network(hass)
    rows = {row["name"]: row for row in network["devices"]}

    plug = rows["Kitchen plug"]["thread"]
    assert plug["role"] == "router"
    assert [(link["to"], link["relation"]) for link in plug["links"]] == [
        (f"external {_BORDER}", "neighbour"),
        ("Door sensor", "child"),
    ]
    assert rows["Kitchen plug"]["rssi"] == -60
    assert "weak_signal" not in rows["Kitchen plug"]


async def test_a_node_without_a_table_is_described_by_who_hears_it(
    hass: HomeAssistant, home: dict[str, dr.DeviceEntry]
) -> None:
    network = await _matter_network(hass)
    sensor = next(row for row in network["devices"] if row["name"] == "Door sensor")

    assert sensor["thread"]["links_reported_by_neighbours"] is True
    assert sensor["thread"]["links"] == [
        {"to": "Kitchen plug", "relation": "heard_by", "rssi": -88, "lqi": 3}
    ]
    # Heard at -88 dBm: a weak link, so the device needs attention.
    assert sensor["weak_signal"] is True


async def test_the_mesh_lists_what_no_node_of_ours_owns(
    hass: HomeAssistant, home: dict[str, dr.DeviceEntry]
) -> None:
    mesh = (await _matter_network(hass))["thread"]

    assert mesh["network_name"] == "MyHome"
    assert mesh["channel"] == 15
    assert mesh["roles"] == {"router": 1, "sleepy_end_device": 1}
    assert mesh["lqi_scale"].startswith("0-3")
    (external,) = mesh["external"]
    assert external == {
        "ext_address": _BORDER,
        "seen_by": ["Kitchen plug"],
        "always_on": True,
        "best_rssi": -60,
    }


# ── Search ─────────────────────────────────────────────────────────────────


async def test_search_resolves_an_extended_address_to_its_device(
    hass: HomeAssistant, home: dict[str, dr.DeviceEntry]
) -> None:
    result = await _tool_search_entities(hass, {"query": _SENSOR.lower()})

    assert [m["entity_id"] for m in result["matches"]] == ["binary_sensor.door"]
    (node,) = result["matter_nodes"]
    assert node["ext_address"] == _SENSOR
    assert "thread_note" not in result
    assert "matter_note" not in result


async def test_an_address_no_device_owns_says_what_it_may_be(
    hass: HomeAssistant, home: dict[str, dr.DeviceEntry]
) -> None:
    result = await _tool_search_entities(hass, {"query": _BORDER})

    assert "matter_nodes" not in result
    assert _BORDER in result["thread_note"]
    assert "border router" in result["thread_note"]


async def test_a_zigbee_ieee_address_finds_its_device_not_a_thread_note(
    hass: HomeAssistant, home: dict[str, dr.DeviceEntry]
) -> None:
    zha = MockConfigEntry(domain="zha")
    zha.add_to_hass(hass)
    bulb = dr.async_get(hass).async_get_or_create(
        config_entry_id=zha.entry_id,
        connections={("zigbee", "00:15:8d:00:01:23:45:67")},
        name="Hall bulb",
    )
    er.async_get(hass).async_get_or_create(
        "light", "zha", "hall", suggested_object_id="hall", config_entry=zha, device_id=bulb.id
    )
    hass.states.async_set("light.hall", "on", {"friendly_name": "Hall bulb"})

    result = await _tool_search_entities(hass, {"query": "0x00158d0001234567"})

    assert [m["entity_id"] for m in result["matches"]] == ["light.hall"]
    assert result["hardware_matches"][0]["connection"] == "zigbee"
    assert "thread_note" not in result


async def test_a_node_disabled_in_home_assistant_is_ours_not_external(
    hass: HomeAssistant, home: dict[str, dr.DeviceEntry]
) -> None:
    dr.async_get(hass).async_update_device(
        home["Door sensor"].id, disabled_by=dr.DeviceEntryDisabler.USER
    )

    network = await _matter_network(hass)

    assert [e["ext_address"] for e in network["thread"]["external"]] == [_BORDER]
    plug = next(row for row in network["devices"] if row["name"] == "Kitchen plug")
    assert ("Node 32", "child") in [
        (link["to"], link["relation"]) for link in plug["thread"]["links"]
    ]
