"""Button platform — Hub action buttons for Selora AI.

Four buttons on the Selora AI Hub device:
  - Discover Devices: scan network status
  - Auto Setup: accept all auto-discoverable flows
  - Cleanup Mirrors: remove stale mirror devices/entities
  - Reset Everything: wipe + re-discover + auto-setup

Automation review happens via persistent notifications + Settings > Automations.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.button import ButtonEntity

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_ACTIVITY_LOG, SIGNAL_DEVICES_UPDATED

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register Hub action buttons."""
    async_add_entities([
        DiscoverButton(hass, entry),
        AutoSetupButton(hass, entry),
        CleanupButton(hass, entry),
        ResetButton(hass, entry),
    ], update_before_add=True)


# ── Base ──────────────────────────────────────────────────────────────


class _HubActionButton(ButtonEntity):
    """Base class for Selora AI Hub action buttons."""

    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "selora_ai_hub")},
        )

    def _get_device_manager(self):
        data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
        return data.get("device_manager")


# ── Discover ──────────────────────────────────────────────────────────


class DiscoverButton(_HubActionButton):
    """Scan the network and report discovered / configured / available devices."""

    _attr_unique_id = f"{DOMAIN}_discover"
    _attr_name = "Discover Devices"
    _attr_icon = "mdi:magnify-scan"

    async def async_press(self) -> None:
        dm = self._get_device_manager()
        if not dm:
            _LOGGER.error("DeviceManager not available")
            return
        result = await dm.discover_network_devices()
        summary = result.get("summary", {})
        discovered = result.get("discovered", [])
        configured = result.get("configured", [])
        active = result.get("active_initiated", [])

        _LOGGER.info("Discover: %s", summary)
        async_dispatcher_send(self.hass, SIGNAL_DEVICES_UPDATED)
        async_dispatcher_send(
            self.hass, SIGNAL_ACTIVITY_LOG,
            f"Discovered {summary.get('discovered_count', 0)} pending, "
            f"{summary.get('configured_count', 0)} configured",
            "discover",
        )


# ── Auto Setup ────────────────────────────────────────────────────────


class AutoSetupButton(_HubActionButton):
    """Auto-accept all pending auto-discoverable device flows."""

    _attr_unique_id = f"{DOMAIN}_auto_setup"
    _attr_name = "Auto Setup"
    _attr_icon = "mdi:auto-fix"

    async def async_press(self) -> None:
        dm = self._get_device_manager()
        if not dm:
            _LOGGER.error("DeviceManager not available")
            return
        result = await dm.auto_setup_discovered()
        await dm.auto_assign_areas()
        accepted = result.get("accepted", [])
        skipped = result.get("skipped", [])
        failed = result.get("failed", [])

        _LOGGER.info("Auto-setup: %d accepted, %d skipped, %d failed", len(accepted), len(skipped), len(failed))
        async_dispatcher_send(self.hass, SIGNAL_DEVICES_UPDATED)
        names = [a.get("title", a["handler"]) for a in accepted] if accepted else ["none"]
        async_dispatcher_send(
            self.hass, SIGNAL_ACTIVITY_LOG,
            f"Auto-setup: {len(accepted)} accepted — {', '.join(names)}",
            "auto_setup",
        )


# ── Cleanup ───────────────────────────────────────────────────────────


class CleanupButton(_HubActionButton):
    """Remove stale mirror devices and orphaned entities."""

    _attr_unique_id = f"{DOMAIN}_cleanup"
    _attr_name = "Cleanup"
    _attr_icon = "mdi:broom"

    async def async_press(self) -> None:
        dm = self._get_device_manager()
        if not dm:
            _LOGGER.error("DeviceManager not available")
            return
        result = await dm.cleanup_mirror_devices()
        removed_devices = result.get("removed_devices", [])
        removed_entities = result.get("removed_entities", [])

        _LOGGER.info("Cleanup: removed %d devices, %d entities", len(removed_devices), len(removed_entities))
        async_dispatcher_send(self.hass, SIGNAL_DEVICES_UPDATED)
        async_dispatcher_send(
            self.hass, SIGNAL_ACTIVITY_LOG,
            f"Cleanup: removed {len(removed_devices)} devices, {len(removed_entities)} entities",
            "cleanup",
        )


# ── Reset ─────────────────────────────────────────────────────────────


class ResetButton(_HubActionButton):
    """Nuclear reset — wipe all non-protected integrations, clean up, re-discover."""

    _attr_unique_id = f"{DOMAIN}_reset"
    _attr_name = "Reset Everything"
    _attr_icon = "mdi:nuke"

    async def async_press(self) -> None:
        dm = self._get_device_manager()
        if not dm:
            _LOGGER.error("DeviceManager not available")
            return
        reset_result = await dm.reset_integrations()
        cleanup_result = await dm.cleanup_mirror_devices()
        # Wait for HA's SSDP/mDNS to re-discover devices on the network
        await asyncio.sleep(15)
        auto_result = await dm.auto_setup_discovered()

        removed = reset_result.get("removed_integrations", [])
        accepted = auto_result.get("accepted", [])
        _LOGGER.info("Reset: removed %d, auto-setup accepted %d", len(removed), len(accepted))
        async_dispatcher_send(self.hass, SIGNAL_DEVICES_UPDATED)
        async_dispatcher_send(
            self.hass, SIGNAL_ACTIVITY_LOG,
            f"Reset: removed {len(removed)}, re-setup accepted {len(accepted)}",
            "reset",
        )
