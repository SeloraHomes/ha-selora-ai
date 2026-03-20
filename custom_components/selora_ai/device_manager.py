"""Device discovery & integration orchestration for Selora AI.

Wraps HA's config_entries.flow API so Selora AI can list discovered
devices, accept/pair them (including PIN entry), and complete integration
through authenticated Home Assistant flows.

All devices go through the same generic flow:
  1. discover_network_devices() finds pending config flows
  2. accept_flow() confirms discovery with empty user input
  3. submit_pin() handles any PIN prompts
  4. configure_step() handles arbitrary multi-step flows
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

_LOGGER = logging.getLogger(__name__)


class DeviceManager:
    """Orchestrate HA config-entry flows for device discovery & pairing."""

    def __init__(self, hass: HomeAssistant, api_key: str = "", model: str = "") -> None:
        self.hass = hass
        self._api_key = api_key
        self._model = model

    async def list_discovered(self) -> list[dict[str, Any]]:
        """Return all pending discovery / config flows."""
        progress = self.hass.config_entries.flow.async_progress()
        results: list[dict[str, Any]] = []
        for flow in progress:
            results.append({
                "flow_id": flow["flow_id"],
                "handler": flow.get("handler", ""),
                "step_id": flow.get("step_id", ""),
                "context": {
                    k: v
                    for k, v in flow.get("context", {}).items()
                    if k in ("source", "unique_id", "title_placeholders")
                },
            })
        return results

    async def accept_flow(self, flow_id: str) -> dict[str, Any]:
        """Confirm a discovered flow with empty user input (single-step)."""
        result = await self.hass.config_entries.flow.async_configure(
            flow_id, user_input={}
        )
        return self._normalise_result(result)

    async def start_device_flow(
        self, domain: str, host: str
    ) -> dict[str, Any]:
        """Manually kick off a config flow by domain + host IP."""
        result = await self.hass.config_entries.flow.async_init(
            domain,
            context={"source": "user"},
            data={"host": host},
        )
        return self._normalise_result(result)

    async def submit_pin(self, flow_id: str, pin: str) -> dict[str, Any]:
        """Submit a PIN for a pairing step (e.g. Android TV)."""
        result = await self.hass.config_entries.flow.async_configure(
            flow_id, user_input={"pin": pin}
        )
        return self._normalise_result(result)

    async def configure_step(
        self, flow_id: str, user_input: dict[str, Any]
    ) -> dict[str, Any]:
        """Generic escape-hatch: progress any flow step with arbitrary input."""
        result = await self.hass.config_entries.flow.async_configure(
            flow_id, user_input=user_input
        )
        return self._normalise_result(result)

    # ── Network discovery & auto-setup ────────────────────────────

    async def _trigger_active_discovery(self) -> list[dict[str, Any]]:
        """Placeholder for active discovery logic.

        Previously checked for related integration pairs (e.g. Cast ↔ Android TV).
        Currently returns empty — passive mDNS/SSDP discovery handles all devices.
        """
        return []

    async def discover_network_devices(self) -> dict[str, Any]:
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
        from .const import KNOWN_INTEGRATIONS, PROTECTED_DOMAINS, DiscoveryMethod

        # Active discovery — start flows for devices we know about but aren't fully configured
        active_initiated = await self._trigger_active_discovery()

        # ── Discovered (pending config flows from SSDP/mDNS + active) ──
        progress = self.hass.config_entries.flow.async_progress()
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
            available.append({
                "domain": domain,
                "name": info.name,
                "category": info.category.value,
                "discovery": info.discovery.value,
                "source": info.source.value,
                "brands": info.brands,
                "notes": info.notes,
            })

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

    def _get_existing_config_data(self, domain: str) -> dict[str, Any] | None:
        """Return config data from an existing entry for the same domain (config cloning)."""
        for ce in self.hass.config_entries.async_entries(domain):
            if ce.data:
                return dict(ce.data)
        return None

    async def auto_setup_discovered(self) -> dict[str, Any]:
        """Auto-accept ALL pending discovery flows — aggressive mode.

        Strategy:
          1. All flows → try accept_flow() with empty input
          2. If multi-step, try config cloning from an already-configured entry
             of the same domain (same device type = same config)
          3. If still can't complete, skip for user action

        Returns: {accepted: [...], skipped: [...], failed: [...]}
        """
        from .const import KNOWN_INTEGRATIONS, PROTECTED_DOMAINS

        progress = self.hass.config_entries.flow.async_progress()

        accepted: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []

        for flow in progress:
            handler = flow.get("handler", "")
            flow_id = flow["flow_id"]
            step_id = flow.get("step_id", "")

            # Never auto-accept flows for protected/system integrations
            if handler in PROTECTED_DOMAINS:
                continue

            try:
                # Try standard single-step accept (works for most auto-discovered devices)
                _LOGGER.info("Auto-setup: trying to accept %s flow %s", handler, flow_id)
                result = await self.accept_flow(flow_id)

                if result.get("type") == "create_entry":
                    accepted.append({"handler": handler, "flow_id": flow_id, "title": result.get("title", handler)})
                    continue

                # Multi-step flow — try config cloning from a similar device
                if result.get("step_id"):
                    clone_data = self._get_existing_config_data(handler)
                    if clone_data:
                        _LOGGER.info(
                            "Auto-setup: cloning config from existing %s entry for flow %s",
                            handler, result.get("flow_id", flow_id),
                        )
                        try:
                            clone_result = await self.configure_step(
                                result.get("flow_id", flow_id), clone_data
                            )
                            if clone_result.get("type") == "create_entry":
                                accepted.append({
                                    "handler": handler,
                                    "flow_id": flow_id,
                                    "title": clone_result.get("title", handler),
                                    "cloned": True,
                                })
                                continue
                        except Exception as clone_exc:
                            _LOGGER.debug("Config clone failed for %s: %s", handler, clone_exc)

                    skipped.append({
                        "handler": handler,
                        "flow_id": result.get("flow_id", flow_id),
                        "reason": f"requires step: {result['step_id']}",
                    })
                else:
                    failed.append({"handler": handler, "flow_id": flow_id, "error": str(result)})

            except Exception as exc:
                _LOGGER.error("Auto-setup failed for %s: %s", handler, exc)
                failed.append({"handler": handler, "flow_id": flow_id, "error": str(exc)})

        _LOGGER.info(
            "Auto-setup complete: %d accepted, %d skipped, %d failed",
            len(accepted), len(skipped), len(failed),
        )

        return {"accepted": accepted, "skipped": skipped, "failed": failed}

    # ── Area auto-assignment ────────────────────────────────────

    async def auto_assign_areas(self) -> dict[str, Any]:
        """Match device names to existing HA areas and assign them.

        Uses case-insensitive substring matching:
          - Device named "basement" → area "basement"
          - Device named "Kitchen" → area "Kitchen"
          - Device named "Living Room Sonos" → area "Living Room"
        Only assigns devices that don't already have an area.
        """
        from homeassistant.helpers import area_registry as ar

        dev_reg = dr.async_get(self.hass)
        area_reg = ar.async_get(self.hass)

        # Build area lookup: lowercase name → area_id
        areas = {area.name.lower(): area.id for area in area_reg.async_list_areas()}

        assigned: list[dict[str, str]] = []

        for device in dev_reg.devices.values():
            if device.area_id:
                continue  # already assigned
            name = (device.name or "").lower()
            if not name:
                continue

            # Try exact match first, then substring match
            matched_area_id = None
            matched_area_name = None
            for area_name, area_id in areas.items():
                if area_name == name or area_name in name or name in area_name:
                    matched_area_id = area_id
                    matched_area_name = area_name
                    break

            if matched_area_id:
                dev_reg.async_update_device(device.id, area_id=matched_area_id)
                assigned.append({
                    "device": device.name or "",
                    "area": matched_area_name or "",
                })
                _LOGGER.info("Auto-assigned device '%s' to area '%s'", device.name, matched_area_name)

        return {"assigned": assigned}

    # ── Reset & cleanup ──────────────────────────────────────────

    async def reset_integrations(self) -> dict[str, Any]:
        """Remove all config entries not in PROTECTED_DOMAINS."""
        from .const import PROTECTED_DOMAINS

        removed: list[str] = []
        entries = list(self.hass.config_entries.async_entries())
        for entry in entries:
            if entry.domain not in PROTECTED_DOMAINS:
                try:
                    await self.hass.config_entries.async_remove(entry.entry_id)
                    removed.append(f"{entry.domain}:{entry.title}")
                    _LOGGER.info("Reset removed integration: %s (%s)", entry.domain, entry.title)
                except Exception as exc:
                    _LOGGER.error("Failed to remove %s: %s", entry.domain, exc)

        return {"removed_integrations": removed}

    async def cleanup_mirror_devices(self) -> dict[str, Any]:
        """Remove all Selora AI-owned devices and orphaned entities."""
        from .const import DOMAIN

        dev_reg = dr.async_get(self.hass)
        ent_reg = er.async_get(self.hass)

        removed_devices: list[str] = []
        removed_entities: list[str] = []

        for device in list(dev_reg.devices.values()):
            if not any(ident[0] == DOMAIN for ident in device.identifiers):
                continue

            for entity in er.async_entries_for_device(ent_reg, device.id, include_disabled_entities=True):
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
                len(removed_devices), len(removed_entities),
            )

        return {
            "removed_devices": removed_devices,
            "removed_entities": removed_entities,
        }

    @staticmethod
    def _normalise_result(result: dict[str, Any]) -> dict[str, Any]:
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
