"""Matter node ids: read from the device registry, found in what users paste.

The Matter server, its dashboard and Thread network views name a device by its
node id ("Node 31", "@1:1f", "0x1F"), while Home Assistant shows the device's
name and nowhere offers the reverse lookup. Asked "which device is Node 31?",
a search on names matched an unrelated tracker and the chat sent the user to
Settings to compare addresses by hand.

The id is already in the registry: the Matter integration registers every
device as ``deviceid_<FABRIC>-<NODE>-MatterNodeDevice`` (16 hex digits each), or
with the endpoint number in place of ``MatterNodeDevice`` for a device behind
a bridge (``matter/helpers.get_device_id``). That format is persisted in every
hub's registry, so core cannot change it without a migration — and reading it
needs neither the Matter server nor its client library, which a manifest
dependency would drag in.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import TYPE_CHECKING, Any, Final

from homeassistant.config_entries import ConfigEntryState

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry

    from .types import MatterNode

MATTER_DOMAIN: Final = "matter"

_IDENTIFIER: Final = re.compile(
    r"deviceid_([0-9A-F]{16})-([0-9A-F]{16})-(MatterNodeDevice|\d+)", re.IGNORECASE
)

# How a node is written where users copy it from. Decimal only after the word
# "node": a bare number is a name fragment far more often than an id.
_REFERENCES: Final[tuple[tuple[re.Pattern[str], int], ...]] = (
    (re.compile(r"\bnode\s*(?:id\s*)?#?\s*(\d{1,20})\b", re.IGNORECASE), 10),
    (re.compile(r"@\s*\d{1,3}\s*:\s*([0-9a-f]{1,16})\b", re.IGNORECASE), 16),
    # Sixteen hex digits are a Thread extended address, not a node id.
    (re.compile(r"\b0x([0-9a-f]{1,15})\b", re.IGNORECASE), 16),
    (re.compile(r"\b[0-9a-f]{16}-([0-9a-f]{16})\b", re.IGNORECASE), 16),
)


def matter_node(device: DeviceEntry) -> MatterNode | None:
    """The device's Matter node, or None for a device Matter did not register."""
    for ident in device.identifiers:
        if len(ident) < 2 or ident[0] != MATTER_DOMAIN:
            continue
        found = _IDENTIFIER.fullmatch(str(ident[1]))
        if found is None:
            continue
        fabric, node_hex, suffix = found.groups()
        node_id = int(node_hex, 16)
        node: MatterNode = {
            "node_id": node_id,
            "node_hex": f"0x{node_id:X}",
            "fabric": fabric.upper(),
        }
        if suffix.isdigit():
            node["bridged_endpoint"] = int(suffix)
        return node
    return None


def node_ids_in(text: str) -> set[int]:
    """Every Matter node id written in ``text``; empty when it names none.

    "Node 31 @1:1f" names node 31 twice, once in each base, and yields {31}.
    """
    ids: set[int] = set()
    for pattern, base in _REFERENCES:
        for found in pattern.finditer(text):
            ids.add(int(found.group(1), base))
    return ids


def devices_on_nodes(
    devices: Iterable[DeviceEntry], node_ids: set[int]
) -> list[tuple[DeviceEntry, MatterNode]]:
    """The devices on those nodes, each node's own device before its bridged ones."""
    found = [
        (device, node)
        for device in devices
        if (node := matter_node(device)) is not None and node["node_id"] in node_ids
    ]
    found.sort(key=lambda pair: (pair[1]["node_id"], pair[1].get("bridged_endpoint", -1)))
    return found


def entry_matter_client(hass: HomeAssistant, entry: ConfigEntry) -> Any:
    """A loaded Matter entry's server client, or None.

    The adapter is the entry's ``runtime_data`` on current cores and lives in
    ``hass.data["matter"][entry_id]`` on older ones.
    """
    if entry.state is not ConfigEntryState.LOADED:
        return None
    data = getattr(entry, "runtime_data", None)
    legacy = hass.data.get(MATTER_DOMAIN)
    if data is None and isinstance(legacy, Mapping):
        data = legacy.get(entry.entry_id)
    return getattr(getattr(data, "adapter", None), "matter_client", None)
