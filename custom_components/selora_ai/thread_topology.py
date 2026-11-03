"""The Thread mesh as the Matter devices on it report it.

Home Assistant's Thread page lists border routers and datasets; nothing in it
says which device a node hangs off, how well it hears its neighbours, or what
a node no Matter controller of ours knows is. Each Matter node over Thread
answers that itself through two clusters the Matter server keeps in its node
cache, so reading them costs no radio traffic:

* **Thread Network Diagnostics** (cluster 53): routing role (1), channel (0),
  network name (2) and the neighbour table (7) — per neighbour its extended
  address, RLOC16, LQI, average and last RSSI, whether it is this node's
  child and whether its radio stays on;
* **General Diagnostics** (cluster 51) network interfaces (0): a Thread
  interface's hardware address IS the node's extended address, which is how
  one node's neighbour entry is matched to another node.

A neighbour no node of ours owns is a device on the same mesh another
controller commissioned, a border router, or a Thread device that is not
Matter; whether its radio stays on says which kind of role it plays.

Read from the client's raw attribute cache (``node_data.attributes``, keyed
``endpoint/cluster/attribute``), never through the client's cluster objects:
those need the CHIP cluster library, which a manifest dependency would drag
in. Structs arrive keyed by field id ("0", "1", …) and octet strings as
base64; camelCase keys are read too, as some server builds send them.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
import re
from typing import TYPE_CHECKING, Any, Final

from .matter_node import MATTER_DOMAIN, entry_matter_client

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .types import ThreadLink, ThreadNode

_ROLES: Final = {
    0: "unspecified",
    1: "unassigned",
    2: "sleepy_end_device",
    3: "end_device",
    4: "router_eligible_end_device",
    5: "router",
    6: "leader",
}
_THREAD_INTERFACE: Final = 4  # General Diagnostics InterfaceTypeEnum.kThread

_CHANNEL: Final = "0/53/0"
_ROLE: Final = "0/53/1"
_NETWORK_NAME: Final = "0/53/2"
_NEIGHBORS: Final = "0/53/7"
_INTERFACES: Final = "0/51/0"


def _field(struct: Any, number: int, name: str) -> Any:
    if not isinstance(struct, Mapping):
        return None
    for key in (str(number), number, name):
        if key in struct:
            return struct[key]
    return None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def ext_address(value: Any) -> str | None:
    """An extended address as 16 upper-case hex digits, from any form it arrives in."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return f"{value:016X}" if 0 <= value < 1 << 64 else None
    if isinstance(value, bytes | bytearray):
        return value.hex().upper() if len(value) == 8 else None
    if isinstance(value, str):
        text = value.strip().removeprefix("0x").removeprefix("0X")
        if len(text) == 16:
            try:
                int(text, 16)
            except ValueError:
                pass
            else:
                return text.upper()
        try:
            raw = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            return None
        return raw.hex().upper() if len(raw) == 8 else None
    return None


def _own_address(attributes: Mapping[str, Any]) -> str | None:
    interfaces = attributes.get(_INTERFACES)
    if not isinstance(interfaces, list):
        return None
    for interface in interfaces:
        if _field(interface, 7, "type") == _THREAD_INTERFACE:
            return ext_address(_field(interface, 4, "hardwareAddress"))
    return None


def _link(entry: Any) -> ThreadLink | None:
    address = ext_address(_field(entry, 0, "extAddress"))
    if address is None:
        return None
    link: ThreadLink = {"ext_address": address}
    if (rloc16 := _int(_field(entry, 2, "rloc16"))) is not None:
        link["rloc16"] = rloc16
    if (lqi := _int(_field(entry, 5, "lqi"))) is not None:
        link["lqi"] = lqi
    if (avg_rssi := _int(_field(entry, 6, "averageRssi"))) is not None:
        link["avg_rssi"] = avg_rssi
    if (last_rssi := _int(_field(entry, 7, "lastRssi"))) is not None:
        link["last_rssi"] = last_rssi
    if isinstance(rx_on := _field(entry, 10, "rxOnWhenIdle"), bool):
        link["rx_on_when_idle"] = rx_on
    if isinstance(is_child := _field(entry, 13, "isChild"), bool):
        link["is_child"] = is_child
    return link


def read_thread_nodes(client: Any) -> dict[int, ThreadNode]:
    """Each Thread node's role and links, keyed by node id; {} without a client.

    A node on Wi-Fi or Ethernet, or one not interviewed yet, has no Thread
    attributes and is left out.
    """
    if client is None:
        return {}
    try:
        nodes = list(client.get_nodes())
    except (AttributeError, TypeError):
        return {}
    found: dict[int, ThreadNode] = {}
    for node in nodes:
        node_id = _int(getattr(node, "node_id", None))
        attributes = getattr(getattr(node, "node_data", None), "attributes", None)
        if node_id is None or not isinstance(attributes, Mapping):
            continue
        role = _int(attributes.get(_ROLE))
        neighbors = attributes.get(_NEIGHBORS)
        own = _own_address(attributes)
        if role is None and own is None and not isinstance(neighbors, list):
            continue
        thread: ThreadNode = {"links": []}
        if role is not None:
            thread["role"] = _ROLES.get(role, "unspecified")
        if own is not None:
            thread["ext_address"] = own
        if (channel := _int(attributes.get(_CHANNEL))) is not None:
            thread["channel"] = channel
        if isinstance(name := attributes.get(_NETWORK_NAME), str) and name:
            thread["network_name"] = name
        if isinstance(neighbors, list):
            thread["links"] = [link for entry in neighbors if (link := _link(entry))]
        found[node_id] = thread
    return found


# Bare or 0x-prefixed, alone: inside "<fabric>-<node>" it is an operational id.
_ADDRESS_IN_TEXT: Final = re.compile(
    r"(?<![0-9a-f-])(?:0x)?([0-9a-f]{16})(?![0-9a-f-])", re.IGNORECASE
)


def ext_addresses_in(text: str) -> set[str]:
    """Every Thread extended address written in ``text``, upper-case."""
    return {found.group(1).upper() for found in _ADDRESS_IN_TEXT.finditer(text)}


def nodes_with_addresses(hass: HomeAssistant, addresses: set[str]) -> dict[int, str]:
    """The Matter node ids whose Thread interface has one of these addresses."""
    found: dict[int, str] = {}
    if not addresses:
        return found
    for entry in hass.config_entries.async_entries(MATTER_DOMAIN):
        for node_id, info in read_thread_nodes(entry_matter_client(hass, entry)).items():
            if (address := info.get("ext_address")) in addresses:
                found[node_id] = address
    return found
