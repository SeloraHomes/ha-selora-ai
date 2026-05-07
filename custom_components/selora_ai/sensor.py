"""Sensor platform — Hub dashboard sensors for Selora AI.

Four sensors on the Selora AI Hub device:
  - Status:        "5 devices managed"          (aggregate overview)
  - Devices:       "3 TVs, 1 speaker, 1 light"  (categorised inventory)
  - Last Activity: "Auto-paired basement TV"     (recent action log)
  - Discovery:     "2 pending" / "All configured" (flow status)

Update mechanism: 60s poll + immediate dispatcher signal updates.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_DOLLAR
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    AUTOMATION_ID_PREFIX,
    DOMAIN,
    KNOWN_INTEGRATIONS,
    SIGNAL_ACTIVITY_LOG,
    SIGNAL_DEVICES_UPDATED,
    SIGNAL_LLM_USAGE,
)

_LOGGER = logging.getLogger(__name__)

_UPDATE_INTERVAL = timedelta(seconds=60)

# Entity domains Selora AI considers "managed"
_MANAGED_DOMAINS = {
    "media_player",
    "light",
    "switch",
    "climate",
    "cover",
    "fan",
    "lock",
    "vacuum",
}

# Integrations to skip when building the device list (system / infra)
_SKIP_DOMAINS = {
    DOMAIN,
    "homeassistant",
    "automation",
    "frontend",
    "backup",
    "sun",
    "persistent_notification",
    "recorder",
    "logger",
    "system_log",
    "default_config",
    "config",
    "person",
    "zone",
    "script",
    "scene",
    "group",
    "template",
    "webhook",
    "conversation",
    "assist_pipeline",
    "cloud",
    "mobile_app",
    "tag",
    "blueprint",
    "ffmpeg",
    "met",
    "bluetooth",
    "dhcp",
    "ssdp",
    "zeroconf",
    "usb",
    "network",
    "shopping_list",
    "google_translate",
    "radio_browser",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Selora AI Hub sensors."""
    sensors: list[SensorEntity] = [
        SmartButlerStatusSensor(hass, entry),
        DeviceListSensor(hass, entry),
        LastActivitySensor(hass, entry),
        DiscoverySensor(hass, entry),
        LLMTokensInSensor(hass, entry),
        LLMTokensOutSensor(hass, entry),
        LLMCallsSensor(hass, entry),
        LLMCostSensor(hass, entry),
    ]
    async_add_entities(sensors, update_before_add=True)
    _LOGGER.info("Selora AI Hub: %d sensors registered", len(sensors))


# ── Helper ──────────────────────────────────────────────────────────


def _hub_device_info() -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, "selora_ai_hub")},
        name="Selora AI Hub",
    )


# ── Status Sensor (enhanced) ────────────────────────────────────────


class SmartButlerStatusSensor(SensorEntity):
    """Aggregate status sensor — device count + pending automations."""

    _attr_has_entity_name = True
    _attr_unique_id = "selora_ai_hub_status"
    _attr_name = "Status"
    _attr_icon = "mdi:robot"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._unsub_timer: Callable[[], None] | None = None
        self._unsub_signal: Callable[[], None] | None = None
        self._device_count: int = 0
        self._pending_automations: int = 0
        self._devices_by_category: dict[str, list[dict[str, Any]]] = {}
        self._attr_device_info = _hub_device_info()

    @property
    def native_value(self) -> str:
        parts = [f"{self._device_count} devices managed"]
        if self._pending_automations:
            parts.append(f"{self._pending_automations} automations pending review")
        return ", ".join(parts)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "device_count": self._device_count,
            "pending_automations": self._pending_automations,
            "devices": self._devices_by_category,
        }

    def _refresh(self) -> None:
        device_count = 0
        for state in self.hass.states.async_all():
            domain = state.entity_id.split(".")[0]
            if domain in _MANAGED_DOMAINS:
                device_count += 1
        self._device_count = device_count

        pending = 0
        for state in self.hass.states.async_all("automation"):
            aid = state.attributes.get("id", "")
            if aid.startswith(AUTOMATION_ID_PREFIX) and state.state == "off":
                pending += 1
        self._pending_automations = pending

        # Build category map from device registry
        self._devices_by_category = _build_device_categories(self.hass)

    async def async_added_to_hass(self) -> None:
        self._refresh()

        @callback
        def _tick(_now: datetime) -> None:
            self._refresh()
            self.async_write_ha_state()

        @callback
        def _on_signal() -> None:
            self._refresh()
            self.async_write_ha_state()

        self._unsub_timer = async_track_time_interval(self.hass, _tick, _UPDATE_INTERVAL)
        self._unsub_signal = async_dispatcher_connect(self.hass, SIGNAL_DEVICES_UPDATED, _on_signal)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_timer:
            self._unsub_timer()
        if self._unsub_signal:
            self._unsub_signal()


# ── Device List Sensor ──────────────────────────────────────────────


def _build_device_categories(hass: HomeAssistant) -> dict[str, list[dict[str, Any]]]:
    """Walk device registry, match against KNOWN_INTEGRATIONS, return categorised inventory.

    Each device entry includes: name, integration, area, state, and primary entity_id
    so users can find and control devices from the sensor attributes.
    """
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)
    categories: dict[str, list[dict[str, Any]]] = {}

    for device in dev_reg.devices.values():
        for ident in device.identifiers:
            # Most integrations register `(domain, unique_id)` 2-tuples,
            # but some (e.g. zha legacy entries) emit longer tuples — only
            # the leading domain is meaningful for our category mapping.
            if not ident:
                continue
            ident_domain = ident[0]
            if ident_domain in _SKIP_DOMAINS:
                continue
            info = KNOWN_INTEGRATIONS.get(ident_domain)
            category = info.category.value if info else "other"
            integration_name = info.name if info else ident_domain

            # Resolve area name
            area_name = None
            if device.area_id:
                area_entry = area_reg.async_get_area(device.area_id)
                if area_entry:
                    area_name = area_entry.name

            # Find the primary entity (media_player, light, switch, etc.) and its state
            primary_entity_id = None
            device_state = None
            for entity in er.async_entries_for_device(ent_reg, device.id):
                entity_domain = entity.entity_id.split(".")[0]
                if entity_domain in _MANAGED_DOMAINS:
                    primary_entity_id = entity.entity_id
                    state_obj = hass.states.get(entity.entity_id)
                    if state_obj:
                        device_state = state_obj.state
                    break

            categories.setdefault(category, [])
            categories[category].append(
                {
                    "name": device.name or "Unknown",
                    "integration": integration_name,
                    "area": area_name,
                    "state": device_state,
                    "entity_id": primary_entity_id,
                }
            )
            break  # one category per device

    return categories


def _summarise_categories(cats: dict[str, list[dict[str, Any]]]) -> str:
    """Build a human-readable summary like '3 TVs, 1 speaker, 1 light'."""
    if not cats:
        return "No devices"
    parts = []
    for cat, devices in sorted(cats.items(), key=lambda x: -len(x[1])):
        n = len(devices)
        label = cat if n == 1 else f"{cat}s"
        parts.append(f"{n} {label}")
    return ", ".join(parts)


class DeviceListSensor(SensorEntity):
    """Categorised device inventory sensor."""

    _attr_has_entity_name = True
    _attr_unique_id = "selora_ai_hub_device_list"
    _attr_name = "Devices"
    _attr_icon = "mdi:devices"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._unsub_timer: Callable[[], None] | None = None
        self._unsub_signal: Callable[[], None] | None = None
        self._categories: dict[str, list[dict[str, Any]]] = {}
        self._attr_device_info = _hub_device_info()

    @property
    def native_value(self) -> str:
        return _summarise_categories(self._categories)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"inventory": self._categories}

    def _refresh(self) -> None:
        self._categories = _build_device_categories(self.hass)

    async def async_added_to_hass(self) -> None:
        self._refresh()

        @callback
        def _tick(_now: datetime) -> None:
            self._refresh()
            self.async_write_ha_state()

        @callback
        def _on_signal() -> None:
            self._refresh()
            self.async_write_ha_state()

        self._unsub_timer = async_track_time_interval(self.hass, _tick, _UPDATE_INTERVAL)
        self._unsub_signal = async_dispatcher_connect(self.hass, SIGNAL_DEVICES_UPDATED, _on_signal)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_timer:
            self._unsub_timer()
        if self._unsub_signal:
            self._unsub_signal()


# ── Last Activity Sensor ────────────────────────────────────────────


class LastActivitySensor(SensorEntity):
    """Tracks recent Selora AI activity — keeps a deque of 20 entries."""

    _attr_has_entity_name = True
    _attr_unique_id = "selora_ai_hub_last_activity"
    _attr_name = "Last Activity"
    _attr_icon = "mdi:history"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._unsub_signal: Callable[[], None] | None = None
        self._log: deque[dict[str, str]] = deque(maxlen=20)
        self._attr_device_info = _hub_device_info()

    @property
    def native_value(self) -> str:
        if not self._log:
            return "No activity yet"
        return self._log[-1].get("message", "")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "timestamp": self._log[-1]["timestamp"] if self._log else None,
            "action_type": self._log[-1].get("action_type", "") if self._log else None,
            "recent_log": list(self._log),
        }

    async def async_added_to_hass(self) -> None:
        @callback
        def _on_activity(message: str, action_type: str = "info") -> None:
            self._log.append(
                {
                    "message": message,
                    "action_type": action_type,
                    "timestamp": datetime.now().isoformat(),
                }
            )
            self.async_write_ha_state()

        self._unsub_signal = async_dispatcher_connect(self.hass, SIGNAL_ACTIVITY_LOG, _on_activity)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_signal:
            self._unsub_signal()


# ── Discovery Sensor ────────────────────────────────────────────────


class DiscoverySensor(SensorEntity):
    """Shows pending discovery flows and configured integration count."""

    _attr_has_entity_name = True
    _attr_unique_id = "selora_ai_hub_discovery"
    _attr_name = "Discovery"
    _attr_icon = "mdi:radar"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._unsub_timer: Callable[[], None] | None = None
        self._unsub_signal: Callable[[], None] | None = None
        self._pending_count: int = 0
        self._configured_count: int = 0
        self._pending_flows: list[dict[str, str]] = []
        self._last_scan: str | None = None
        self._attr_device_info = _hub_device_info()

    @property
    def native_value(self) -> str:
        if self._pending_count:
            return f"{self._pending_count} pending"
        return "All configured"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "pending_flows": self._pending_flows,
            "configured_count": self._configured_count,
            "last_scan": self._last_scan,
        }

    def _refresh(self) -> None:
        from .const import PROTECTED_DOMAINS

        # Pending flows
        progress = self.hass.config_entries.flow.async_progress()
        self._pending_flows = []
        for flow in progress:
            handler = flow.get("handler", "")
            if handler in _SKIP_DOMAINS:
                continue
            info = KNOWN_INTEGRATIONS.get(handler)
            name = info.name if info else handler
            self._pending_flows.append(
                {
                    "handler": handler,
                    "name": name,
                    "flow_id": flow["flow_id"],
                }
            )
        self._pending_count = len(self._pending_flows)

        # Configured count (non-system integrations)
        count = 0
        for ce in self.hass.config_entries.async_entries():
            if ce.domain not in PROTECTED_DOMAINS and ce.domain not in _SKIP_DOMAINS:
                count += 1
        self._configured_count = count
        self._last_scan = datetime.now().isoformat()

    async def async_added_to_hass(self) -> None:
        self._refresh()

        @callback
        def _tick(_now: datetime) -> None:
            self._refresh()
            self.async_write_ha_state()

        @callback
        def _on_signal() -> None:
            self._refresh()
            self.async_write_ha_state()

        self._unsub_timer = async_track_time_interval(self.hass, _tick, _UPDATE_INTERVAL)
        self._unsub_signal = async_dispatcher_connect(self.hass, SIGNAL_DEVICES_UPDATED, _on_signal)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_timer:
            self._unsub_timer()
        if self._unsub_signal:
            self._unsub_signal()


# ── LLM Usage Sensors ───────────────────────────────────────────────
#
# Cumulative counters fed by SIGNAL_LLM_USAGE. Persist across restarts via
# RestoreSensor. Use total_increasing so HA's long-term Statistics engine
# computes hourly/daily rollups for free — the panel and HA history graphs
# read from those statistics directly.


class _LLMUsageBaseSensor(RestoreSensor):
    """Base for LLM usage counters: restore last value, increment on signal."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._unsub_signal: Callable[[], None] | None = None
        self._value: float = 0.0
        self._last_provider: str | None = None
        self._last_model: str | None = None
        self._attr_device_info = _hub_device_info()

    @property
    def native_value(self) -> float:
        return self._value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "last_provider": self._last_provider,
            "last_model": self._last_model,
        }

    def _delta_for(self, payload: dict[str, Any]) -> float:
        """Return the increment to add to this counter for one usage event."""
        raise NotImplementedError

    async def async_added_to_hass(self) -> None:
        last = await self.async_get_last_sensor_data()
        if last is not None and last.native_value is not None:
            try:
                self._value = float(last.native_value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                self._value = 0.0

        @callback
        def _on_usage(payload: dict[str, Any]) -> None:
            delta = self._delta_for(payload)
            if delta <= 0:
                return
            self._value += delta
            self._last_provider = payload.get("provider")
            self._last_model = payload.get("model")
            self.async_write_ha_state()

        self._unsub_signal = async_dispatcher_connect(self.hass, SIGNAL_LLM_USAGE, _on_usage)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_signal:
            self._unsub_signal()


class LLMTokensInSensor(_LLMUsageBaseSensor):
    """Cumulative input (prompt) tokens sent to the LLM."""

    _attr_unique_id = "selora_ai_hub_llm_tokens_in"
    _attr_name = "LLM Tokens In"
    _attr_icon = "mdi:upload"
    _attr_native_unit_of_measurement = "tokens"

    def _delta_for(self, payload: dict[str, Any]) -> float:
        return float(payload.get("input_tokens", 0) or 0)


class LLMTokensOutSensor(_LLMUsageBaseSensor):
    """Cumulative output (completion) tokens received from the LLM."""

    _attr_unique_id = "selora_ai_hub_llm_tokens_out"
    _attr_name = "LLM Tokens Out"
    _attr_icon = "mdi:download"
    _attr_native_unit_of_measurement = "tokens"

    def _delta_for(self, payload: dict[str, Any]) -> float:
        return float(payload.get("output_tokens", 0) or 0)


class LLMCallsSensor(_LLMUsageBaseSensor):
    """Cumulative count of LLM calls."""

    _attr_unique_id = "selora_ai_hub_llm_calls"
    _attr_name = "LLM Calls"
    _attr_icon = "mdi:counter"
    _attr_native_unit_of_measurement = "calls"

    def _delta_for(self, payload: dict[str, Any]) -> float:
        # Count any usage event with at least one token reported.
        if (payload.get("input_tokens") or 0) or (payload.get("output_tokens") or 0):
            return 1.0
        return 0.0


class LLMCostSensor(_LLMUsageBaseSensor):
    """Estimated cumulative LLM cost in USD (best-effort, uses static pricing).

    Not using ``SensorDeviceClass.MONETARY`` because that device class
    requires ``state_class: TOTAL`` (with explicit resets), and we want
    the simpler ``TOTAL_INCREASING`` semantics. The unit is still USD so
    dashboards render correctly.
    """

    _attr_unique_id = "selora_ai_hub_llm_cost"
    _attr_name = "LLM Cost (estimate)"
    _attr_icon = "mdi:cash"
    _attr_native_unit_of_measurement = CURRENCY_DOLLAR
    _attr_suggested_display_precision = 4

    def _delta_for(self, payload: dict[str, Any]) -> float:
        return float(payload.get("cost_usd", 0.0) or 0.0)
