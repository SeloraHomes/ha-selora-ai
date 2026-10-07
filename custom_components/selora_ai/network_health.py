"""The home's radio networks — Zigbee, Z-Wave, Matter — and which devices on
them are offline or barely heard.

"Why is the hallway sensor flaky?" is usually a mesh answer: the device is
offline, asleep, or one hop too far. Home Assistant shows it per integration
(the ZHA device page, the Z-Wave JS network page), and its diagnostics dump is
a page of raw radio state; this is the summary across all of them.

Read without importing any radio library: those are installed only when the
integration is, and their objects change shape between releases. So:

* **ZHA** from its gateway proxy in ``hass.data`` — the same device info its
  device page shows (availability, LQI, RSSI, last seen, role, power source);
* **Z-Wave JS** from the status and statistics sensors it registers per node
  (``node_status``: alive / asleep / dead; ``controller_status``; the
  statistics ``rssi`` and ``last_seen``, which are disabled by default);
* **Matter** from availability alone: a node that drops off takes every
  entity with it;
* **Zigbee2MQTT** from its bridge device and each device's ``linkquality``
  entity, since many homes run Zigbee there rather than in ZHA.

Every network also gets the generic test: a device whose every entity is
unavailable is offline, whatever the integration says.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.util import dt as dt_util

from .helpers import device_entries, sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry
    from homeassistant.helpers.entity_registry import RegistryEntry

PROTOCOLS: Final = ("zigbee", "zwave", "matter")
_RADIO_DOMAINS: Final = {"zha": "zigbee", "zwave_js": "zwave", "matter": "matter"}
_Z2M_MANUFACTURER: Final = "Zigbee2MQTT"

# Thresholds HA's own pages colour as poor: ZHA's network map draws an LQI
# under 80 red, and under -80 dBm a link is at the edge for both radios.
WEAK_LQI: Final = 80
WEAK_RSSI: Final = -80

_MAX_ROWS: Final = 50
_NAME_LEN: Final = 60


def _when(value: Any) -> str | None:
    """A last-seen value as ISO text: ZHA gives epoch seconds, sensors ISO."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        try:
            return dt_util.utc_from_timestamp(float(value)).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, datetime):
        return value.isoformat()
    parsed = dt_util.parse_datetime(str(value))
    return parsed.isoformat() if parsed is not None else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _state_value(hass: HomeAssistant, entity_id: str | None) -> str | None:
    if entity_id is None or (state := hass.states.get(entity_id)) is None:
        return None
    if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    return state.state


class _Registries:
    """The registries, read once per call, with each device's entities."""

    def __init__(self, hass: HomeAssistant) -> None:
        from homeassistant.helpers import (  # noqa: PLC0415
            area_registry as ar,
        )
        from homeassistant.helpers import (
            device_registry as dr,
        )
        from homeassistant.helpers import (
            entity_registry as er,
        )

        self.hass = hass
        self.areas = ar.async_get(hass)
        self.devices = dr.async_get(hass)
        self.all_devices = device_entries(self.devices)
        self.entity_reg = er.async_get(hass)
        self.by_device: dict[str, list[RegistryEntry]] = {}
        for entry in self.entity_reg.entities.values():
            if entry.device_id and entry.disabled_by is None:
                self.by_device.setdefault(entry.device_id, []).append(entry)

    def area(self, device: DeviceEntry) -> str | None:
        if device.area_id and (area := self.areas.async_get_area(device.area_id)):
            return sanitize_untrusted_text(area.name, _NAME_LEN)
        return None

    def all_unavailable(self, device_id: str) -> bool | None:
        """Whether every entity with a state is unavailable; None with none."""
        states = [
            state
            for entry in self.by_device.get(device_id, [])
            if (state := self.hass.states.get(entry.entity_id)) is not None
        ]
        if not states:
            return None
        return all(state.state == STATE_UNAVAILABLE for state in states)


def _row(regs: _Registries, device: DeviceEntry) -> dict[str, Any]:
    row: dict[str, Any] = {
        "device_id": device.id,
        "name": sanitize_untrusted_text(device.name_by_user or device.name or device.id, _NAME_LEN),
    }
    if area := regs.area(device):
        row["area"] = area
    if device.manufacturer or device.model:
        row["model"] = sanitize_untrusted_text(
            " ".join(p for p in (device.manufacturer, device.model) if p), _NAME_LEN
        )
    return row


def _finish(row: dict[str, Any], regs: _Registries) -> dict[str, Any]:
    """Fill ``status`` from the generic test when the integration said nothing,
    and flag a weak link."""
    if "status" not in row:
        everything_down = regs.all_unavailable(row["device_id"])
        row["status"] = (
            "unknown" if everything_down is None else "offline" if everything_down else "online"
        )
    elif row["status"] == "online" and regs.all_unavailable(row["device_id"]):
        row["status"] = "offline"
    lqi, rssi = row.get("lqi"), row.get("rssi")
    if (lqi is not None and lqi < WEAK_LQI) or (rssi is not None and rssi < WEAK_RSSI):
        row["weak_signal"] = True
    return row


def _zha_rows(hass: HomeAssistant, regs: _Registries, entry: ConfigEntry) -> dict[str, Any]:
    proxies: dict[str, Any] = {}
    # ``hass.data["zha"]`` is core's ``HAZHAData``; setup puts the
    # ``ZHAGatewayProxy`` on it (``zha/__init__.py``), which
    # ``get_zha_gateway_proxy`` and the ``zha/devices`` command read too.
    gateway = getattr(hass.data.get("zha"), "gateway_proxy", None)
    for proxy in (getattr(gateway, "device_proxies", None) or {}).values():
        try:
            info = proxy.device_info
            # The registry device's id, which ZHA sets when it registers it.
            device_id = proxy.device_id
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if isinstance(info, dict) and isinstance(device_id, str):
            proxies[device_id] = info

    rows: list[dict[str, Any]] = []
    controller: dict[str, Any] | None = None
    for device in _entry_devices(regs, entry):
        row = _row(regs, device)
        info = proxies.get(device.id)
        if info is not None:
            if (available := info.get("available")) is not None:
                row["status"] = "online" if available else "offline"
            for key in ("lqi", "rssi"):
                if (value := _number(info.get(key))) is not None:
                    row[key] = value
            if seen := _when(info.get("last_seen")):
                row["last_seen"] = seen
            if role := info.get("device_type"):
                row["role"] = str(role)
            if power := info.get("power_source"):
                row["power"] = str(power)
        if row.get("role") == "Coordinator":
            controller = {"name": row["name"], "status": row.get("status", "unknown")}
            continue
        rows.append(_finish(row, regs))
    result: dict[str, Any] = {"devices": rows}
    if controller is not None:
        result["controller"] = controller
    if not proxies and entry.state.value == "loaded":
        result["note"] = (
            "ZHA's device details were not readable; status is from entity availability."
        )
    return result


def _zwave_rows(regs: _Registries, entry: ConfigEntry) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    controller: dict[str, Any] | None = None
    for device in _entry_devices(regs, entry):
        row = _row(regs, device)
        keyed: dict[str, str] = {}
        for ent in regs.by_device.get(device.id, []):
            unique_id = str(ent.unique_id)
            for key in (
                "node_status",
                "controller_status",
                "statistics_rssi",
                "statistics_last_seen",
            ):
                if unique_id.endswith(f".{key}"):
                    keyed[key] = ent.entity_id
        if "controller_status" in keyed:
            raw = regs.hass.states.get(keyed["controller_status"])
            # Unavailable while the entry is loaded: the Z-Wave JS server or
            # the stick is gone, and every node with it.
            status = (
                "offline"
                if raw is not None and raw.state == STATE_UNAVAILABLE
                else _state_value(regs.hass, keyed["controller_status"]) or "unknown"
            )
            controller = {"name": row["name"], "status": status}
            continue
        # alive / awake / asleep / dead; asleep is a battery device's normal.
        node = _state_value(regs.hass, keyed.get("node_status"))
        if node == "dead":
            row["status"] = "offline"
        elif node == "asleep":
            row["status"] = "asleep"
        elif node in ("alive", "awake"):
            row["status"] = "online"
        if (rssi := _number(_state_value(regs.hass, keyed.get("statistics_rssi")))) is not None:
            row["rssi"] = rssi
        if seen := _when(_state_value(regs.hass, keyed.get("statistics_last_seen"))):
            row["last_seen"] = seen
        rows.append(_finish(row, regs))
    if controller is not None and controller["status"] == "offline":
        # One fault — the controller — not one per node behind it.
        for row in rows:
            row["status"] = "unknown"
            row.pop("weak_signal", None)
    result: dict[str, Any] = {"devices": rows}
    if controller is not None:
        result["controller"] = controller
    return result


def _matter_rows(regs: _Registries, entry: ConfigEntry) -> dict[str, Any]:
    return {"devices": [_finish(_row(regs, d), regs) for d in _entry_devices(regs, entry)]}


def _entry_devices(regs: _Registries, entry: ConfigEntry) -> list[DeviceEntry]:
    from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

    return [
        device
        for device in dr.async_entries_for_config_entry(regs.devices, entry.entry_id)
        if device.disabled_by is None and device.entry_type is None
    ]


def _bridge_disconnected(regs: _Registries, bridge_id: str) -> bool:
    """A Zigbee2MQTT bridge reports losing its coordinator through its
    connectivity sensor, which stays available and turns ``off``."""
    for ent in regs.by_device.get(bridge_id, []):
        state = regs.hass.states.get(ent.entity_id)
        if (
            ent.domain == "binary_sensor"
            and state is not None
            and (
                ent.device_class
                or ent.original_device_class
                or state.attributes.get("device_class")
            )
            == "connectivity"
        ):
            return state.state == "off"
    return False


def _z2m_networks(regs: _Registries) -> list[dict[str, Any]]:
    """One network per Zigbee2MQTT bridge: the devices it introduced."""
    bridges = [
        device
        for device in regs.all_devices
        if device.manufacturer == _Z2M_MANUFACTURER and device.disabled_by is None
    ]
    networks: list[dict[str, Any]] = []
    for bridge in bridges:
        rows: list[dict[str, Any]] = []
        for device in regs.all_devices:
            if device.via_device_id != bridge.id or device.disabled_by is not None:
                continue
            row = _row(regs, device)
            for ent in regs.by_device.get(device.id, []):
                if (
                    str(ent.unique_id).endswith("_linkquality_zigbee2mqtt")
                    and (lqi := _number(_state_value(regs.hass, ent.entity_id))) is not None
                ):
                    row["lqi"] = lqi
            rows.append(_finish(row, regs))
        # MQTT itself down takes every bridge entity with it: that is the
        # integration's fault, reported once by check_system.
        mqtt_down = any(
            (cfg := regs.hass.config_entries.async_get_entry(entry_id)) is not None
            and cfg.state.value != "loaded"
            for entry_id in bridge.config_entries
        )
        bridge_down = (
            None
            if mqtt_down
            else _bridge_disconnected(regs, bridge.id) or regs.all_unavailable(bridge.id)
        )
        if bridge_down or mqtt_down:
            # Its devices are unavailable because the bridge is: one fault.
            for row in rows:
                row["status"] = "unknown"
                row.pop("weak_signal", None)
        network: dict[str, Any] = {
            "protocol": "zigbee",
            "integration": "zigbee2mqtt",
            "title": sanitize_untrusted_text(
                bridge.name_by_user or bridge.name or "Zigbee2MQTT", _NAME_LEN
            ),
            "devices": rows,
        }
        if mqtt_down:
            network["note"] = (
                "The MQTT integration is not running, so the bridge can't be "
                "reached; check_system says why."
            )
        else:
            network["controller"] = {
                "name": sanitize_untrusted_text(bridge.name or "Zigbee2MQTT", _NAME_LEN),
                "status": "unknown"
                if bridge_down is None
                else "offline"
                if bridge_down
                else "online",
            }
        networks.append(network)
    return networks


def _summarize(network: dict[str, Any], *, all_devices: bool, offset: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = network.pop("devices")
    offline = [r for r in rows if r["status"] == "offline"]
    weak = [r for r in rows if r.get("weak_signal") and r["status"] != "offline"]
    network["device_count"] = len(rows)
    network["offline_count"] = len(offline)
    network["weak_signal_count"] = len(weak)
    network["asleep_count"] = sum(1 for r in rows if r["status"] == "asleep")
    if not network["asleep_count"]:
        del network["asleep_count"]
    shown = rows[offset:] if all_devices else offline + weak
    network["devices" if all_devices else "attention"] = shown[:_MAX_ROWS]
    if len(shown) > _MAX_ROWS:
        network["omitted"] = len(shown) - _MAX_ROWS
        if all_devices:
            network["next_offset"] = offset + _MAX_ROWS
    return network


def network_health(
    hass: HomeAssistant,
    protocol: str | None = None,
    *,
    all_devices: bool = False,
    offset: int = 0,
) -> dict[str, Any]:
    """Each radio network with its offline and weak-signal devices."""
    if protocol is not None and protocol not in PROTOCOLS:
        return {"error": f"protocol must be one of {', '.join(PROTOCOLS)}."}
    if offset < 0:
        return {"error": "offset must be a whole number of devices, from next_offset."}

    regs = _Registries(hass)
    networks: list[dict[str, Any]] = []
    for domain, proto in _RADIO_DOMAINS.items():
        if protocol not in (None, proto):
            continue
        for entry in hass.config_entries.async_entries(domain):
            network: dict[str, Any] = {
                "protocol": proto,
                "integration": domain,
                "entry_id": entry.entry_id,
                "title": sanitize_untrusted_text(entry.title or domain, _NAME_LEN),
                "state": entry.state.value,
            }
            if domain == "zha":
                network.update(_zha_rows(hass, regs, entry))
            elif domain == "zwave_js":
                network.update(_zwave_rows(regs, entry))
            else:
                network.update(_matter_rows(regs, entry))
            if entry.state.value != "loaded":
                # Its entities are all unavailable because IT is down: one
                # fault, not one per device.
                for row in network["devices"]:
                    row["status"] = "unknown"
                    row.pop("weak_signal", None)
                network.pop("controller", None)
                network["note"] = (
                    "The integration is not running, so none of its devices can be "
                    "reached; check_system says why."
                )
            networks.append(network)
    if protocol in (None, "zigbee"):
        networks.extend(_z2m_networks(regs))

    if not networks:
        wanted = protocol or "Zigbee, Z-Wave or Matter"
        return {
            "networks": [],
            "note": f"No {wanted} network is set up (ZHA, Z-Wave JS, Matter or Zigbee2MQTT).",
        }
    return {
        "networks": [_summarize(n, all_devices=all_devices, offset=offset) for n in networks],
        "weak_signal_below": {"lqi": WEAK_LQI, "rssi_dbm": WEAK_RSSI},
        **(
            {}
            if all_devices
            else {
                "note": "Only devices needing attention are listed; all_devices=true lists every one."
            }
        ),
    }
