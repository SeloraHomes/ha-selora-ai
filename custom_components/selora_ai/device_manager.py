"""Device discovery & integration orchestration for Selora AI.

Wraps HA's config_entries.flow API for Selora AI's own onboarding (its
config flow's device step and the hub buttons):
  1. discover_network_devices() finds pending discovery flows
  2. accept_flow() confirms one with empty user input
  3. configure_step() completes a multi-step one from a cloned entry

A chat or MCP setup goes through ``integration_manager`` instead, one step
per call.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .helpers import device_entries
from .telemetry import record_activity

if TYPE_CHECKING:
    from .types import (
        CleanupResult,
        FlowResult,
        NetworkDiscoveryResult,
    )

_LOGGER = logging.getLogger(__name__)


class DeviceManager:
    """Orchestrate HA config-entry flows for device discovery & pairing."""

    def __init__(self, hass: HomeAssistant, api_key: str = "", model: str = "") -> None:
        self.hass = hass
        self._api_key = api_key
        self._model = model

    def _count_if_paired(self, result: FlowResult) -> FlowResult:
        """Record a pairing when a flow step completed with a created entry.

        Every onboarding path (single-step accept, PIN, generic step,
        manual init) funnels its normalised result through here, so a
        device is counted exactly once regardless of how many steps it
        took. Returns the result unchanged for call-site convenience.
        """
        if result.get("type") == "create_entry":
            record_activity(self.hass, "devices_paired")
        return result

    async def accept_flow(self, flow_id: str) -> FlowResult:
        """Confirm a discovered flow with empty user input (single-step)."""
        result = await self.hass.config_entries.flow.async_configure(flow_id, user_input={})
        return self._count_if_paired(self._normalise_result(result))

    async def configure_step(self, flow_id: str, user_input: dict[str, Any]) -> FlowResult:
        """Generic escape-hatch: progress any flow step with arbitrary input."""
        result = await self.hass.config_entries.flow.async_configure(flow_id, user_input=user_input)
        return self._count_if_paired(self._normalise_result(result))

    # ── Network discovery & auto-setup ────────────────────────────

    async def _trigger_active_discovery(self) -> list[dict[str, Any]]:
        """Placeholder for active discovery logic.

        Previously checked for related integration pairs (e.g. Cast ↔ Android TV).
        Currently returns empty — passive mDNS/SSDP discovery handles all devices.
        """
        return []

    async def discover_network_devices(self) -> NetworkDiscoveryResult:
        """Full network status: discovered, configured, and available integrations.

        First triggers active discovery to find devices that passive mDNS/SSDP
        might miss (especially in Docker), then reports full status.

        Returns:
            discovered: Pending HA config flows annotated with KNOWN_INTEGRATIONS metadata
            configured: Already-set-up integrations matched against KNOWN_INTEGRATIONS
            available: Known integrations not yet found (cloud/manual ones user could add)
            active_initiated: Flows started by active discovery
            summary: counts
        """
        from .const import KNOWN_INTEGRATIONS, PROTECTED_DOMAINS

        record_activity(self.hass, "discoveries_run")

        # Active discovery — start flows for devices we know about but aren't fully configured
        active_initiated = await self._trigger_active_discovery()

        # ── Discovered (pending config flows from SSDP/mDNS + active) ──
        # Only what Home Assistant found itself: a flow the user (or a Selora
        # tool) started by hand, or a re-login, is not a discovery.
        from .integration_manager import discovery_flows  # noqa: PLC0415

        progress = discovery_flows(self.hass)
        discovered: list[dict[str, Any]] = []
        discovered_domains: set[str] = set()
        for flow in progress:
            handler = flow.get("handler", "")
            discovered_domains.add(handler)
            entry: dict[str, Any] = {
                "flow_id": flow["flow_id"],
                "handler": handler,
                "step_id": flow.get("step_id", ""),
                "context": {
                    k: v
                    for k, v in flow.get("context", {}).items()
                    if k in ("source", "unique_id", "title_placeholders")
                },
            }
            # Annotate with known integration metadata
            info = KNOWN_INTEGRATIONS.get(handler)
            if info:
                entry["known"] = {
                    "name": info.name,
                    "category": info.category.value,
                    "discovery": info.discovery.value,
                    "source": info.source.value,
                    "brands": info.brands,
                }
            discovered.append(entry)

        # ── Configured (existing config entries matched against registry) ──
        configured: list[dict[str, Any]] = []
        configured_domains: set[str] = set()
        for ce in self.hass.config_entries.async_entries():
            if ce.domain in PROTECTED_DOMAINS:
                continue
            configured_domains.add(ce.domain)
            item: dict[str, Any] = {
                "domain": ce.domain,
                "title": ce.title,
                "entry_id": ce.entry_id,
            }
            info = KNOWN_INTEGRATIONS.get(ce.domain)
            if info:
                item["known"] = {
                    "name": info.name,
                    "category": info.category.value,
                    "discovery": info.discovery.value,
                    "source": info.source.value,
                    "brands": info.brands,
                }
            configured.append(item)

        # ── Available (known integrations not yet discovered or configured) ──
        available: list[dict[str, Any]] = []
        for domain, info in KNOWN_INTEGRATIONS.items():
            if domain in discovered_domains or domain in configured_domains:
                continue
            available.append(
                {
                    "domain": domain,
                    "name": info.name,
                    "category": info.category.value,
                    "discovery": info.discovery.value,
                    "source": info.source.value,
                    "brands": info.brands,
                    "notes": info.notes,
                }
            )

        return {
            "discovered": discovered,
            "configured": configured,
            "available": available,
            "active_initiated": active_initiated,
            "summary": {
                "discovered_count": len(discovered),
                "configured_count": len(configured),
                "available_count": len(available),
                "active_initiated_count": len(active_initiated),
            },
        }

    # ── Reset & cleanup ──────────────────────────────────────────

    async def cleanup_mirror_devices(self) -> CleanupResult:
        """Remove all Selora AI-owned devices and orphaned entities."""
        from .const import DOMAIN

        dev_reg = dr.async_get(self.hass)
        ent_reg = er.async_get(self.hass)

        removed_devices: list[str] = []
        removed_entities: list[str] = []

        for device in device_entries(dev_reg):
            if not any(ident[0] == DOMAIN for ident in device.identifiers):
                continue

            for entity in er.async_entries_for_device(
                ent_reg, device.id, include_disabled_entities=True
            ):
                ent_reg.async_remove(entity.entity_id)
                removed_entities.append(entity.entity_id)

            dev_reg.async_remove_device(device.id)
            removed_devices.append(device.name or device.id)

        for entity in list(ent_reg.entities.values()):
            if entity.platform != DOMAIN:
                continue
            ent_reg.async_remove(entity.entity_id)
            removed_entities.append(entity.entity_id)

        if removed_devices or removed_entities:
            _LOGGER.info(
                "Cleaned up %d devices, %d entities",
                len(removed_devices),
                len(removed_entities),
            )

        return {
            "removed_devices": removed_devices,
            "removed_entities": removed_entities,
        }

    @staticmethod
    def _normalise_result(result: dict[str, Any]) -> FlowResult:
        """Trim a FlowResult to JSON-safe fields we care about."""
        out: dict[str, Any] = {
            "type": result.get("type", ""),
            "flow_id": result.get("flow_id", ""),
        }
        if result.get("step_id"):
            out["step_id"] = result["step_id"]
        if result.get("title"):
            out["title"] = result["title"]
        if result.get("description_placeholders"):
            out["description_placeholders"] = result["description_placeholders"]
        if result.get("errors"):
            out["errors"] = result["errors"]
        if result.get("result"):
            entry = result["result"]
            if hasattr(entry, "entry_id"):
                out["entry_id"] = entry.entry_id
                out["title"] = entry.title
        return out
