"""Device discovery & integration orchestration for Selora AI.

Wraps HA's config_entries.flow API so Selora AI can list discovered
devices, accept/pair them (including PIN entry), and complete integration
— all via a single webhook endpoint.

All devices go through the same generic flow:
  1. discover_network_devices() finds pending config flows
  2. accept_flow() confirms discovery with empty user input
  3. submit_pin() handles any PIN prompts
  4. configure_step() handles arbitrary multi-step flows
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp
from aiohttp.web import Request, Response

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

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

        from .const import DOMAIN
        hub_id = (DOMAIN, "selora_ai_hub")

        for device in dev_reg.devices.values():
            if device.area_id:
                continue  # already assigned
            # Skip the Selora AI Hub — it's whole-home, not room-specific
            if hub_id in device.identifiers:
                continue
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

    async def generate_dashboard(self) -> dict[str, Any]:
        """Auto-generate a Lovelace dashboard showing all useful entities.

        Builds a comprehensive overview: weather, people, controllable devices
        grouped by area, sensors, and the Selora AI hub card.
        """
        from homeassistant.helpers import area_registry as ar, entity_registry as ent_r

        dev_reg = dr.async_get(self.hass)
        ent_reg = ent_r.async_get(self.hass)
        area_reg = ar.async_get(self.hass)

        # Domains we want on the dashboard
        controllable = {
            "media_player", "light", "switch", "climate", "cover",
            "fan", "lock", "vacuum", "humidifier", "water_heater",
        }
        sensor_domains = {"sensor", "binary_sensor"}
        all_dashboard_domains = controllable | sensor_domains | {
            "camera", "weather", "person", "input_boolean",
            "input_number", "input_select", "automation", "scene",
        }

        # Skip internal/noisy entities
        skip_platforms = {"selora_ai", "backup"}
        skip_prefixes = (
            "sensor.backup_", "event.backup_", "sensor.sun_solar_",
        )

        # Build area_id → area_name lookup
        area_names = {a.id: a.name for a in area_reg.async_list_areas()}

        # Collect all visible entities grouped by purpose
        area_controllable: dict[str, list[str]] = {}
        area_sensors: dict[str, list[str]] = {}
        unassigned_controllable: list[str] = []
        unassigned_sensors: list[str] = []
        weather_entities: list[str] = []
        person_entities: list[str] = []
        camera_entities: list[str] = []
        scene_entities: list[str] = []
        sun_entities: list[str] = []

        for entity in ent_reg.entities.values():
            eid = entity.entity_id
            domain = eid.split(".")[0]

            if domain not in all_dashboard_domains:
                continue
            if entity.disabled_by or entity.hidden_by:
                continue
            if entity.platform in skip_platforms:
                continue
            if any(eid.startswith(p) for p in skip_prefixes):
                continue

            # Special-purpose entities go to dedicated sections
            if domain == "weather":
                weather_entities.append(eid)
                continue
            if domain == "person":
                person_entities.append(eid)
                continue
            if domain == "camera":
                camera_entities.append(eid)
                continue
            if domain == "scene":
                scene_entities.append(eid)
                continue
            if eid in ("sensor.sun_next_rising", "sensor.sun_next_setting", "binary_sensor.sun_solar_rising"):
                sun_entities.append(eid)
                continue
            # Skip other sun sensors
            if entity.platform == "sun" and eid not in sun_entities:
                continue

            # Determine area
            area_id = entity.area_id
            if not area_id and entity.device_id:
                device = dev_reg.async_get(entity.device_id)
                if device:
                    area_id = device.area_id

            area_name = area_names.get(area_id) if area_id else None

            if domain in controllable:
                if area_name:
                    area_controllable.setdefault(area_name, []).append(eid)
                else:
                    unassigned_controllable.append(eid)
            elif domain in sensor_domains:
                if area_name:
                    area_sensors.setdefault(area_name, []).append(eid)
                else:
                    unassigned_sensors.append(eid)

        # ── Build Lovelace cards ──
        cards: list[dict[str, Any]] = []

        # Weather card (prominent, top of dashboard)
        for weid in weather_entities:
            cards.append({"type": "weather-forecast", "entity": weid, "show_forecast": True})

        # Person tracking
        if person_entities:
            cards.append({
                "type": "glance",
                "title": "People",
                "entities": [{"entity": eid, "tap_action": {"action": "more-info"}} for eid in person_entities],
            })

        # Sun info
        if sun_entities:
            cards.append({
                "type": "glance",
                "title": "Sun",
                "entities": [{"entity": eid} for eid in sorted(sun_entities)],
            })

        # Selora AI Hub
        cards.append({
            "type": "entities",
            "title": "Selora AI Hub",
            "entities": [
                "sensor.selora_ai_hub_status",
                "sensor.selora_ai_hub_devices",
                "sensor.selora_ai_hub_discovery",
                "sensor.selora_ai_hub_last_activity",
                "button.selora_ai_hub_discover_devices",
            ],
        })

        # Area sections — controllable devices
        for area_name in sorted(set(list(area_controllable.keys()) + list(area_sensors.keys()))):
            area_cards: list[dict[str, Any]] = []

            for eid in sorted(area_controllable.get(area_name, [])):
                domain = eid.split(".")[0]
                if domain == "media_player":
                    area_cards.append({"type": "media-control", "entity": eid})
                elif domain in ("light", "switch", "fan", "cover"):
                    area_cards.append({"type": "button", "entity": eid, "tap_action": {"action": "toggle"}})
                elif domain == "climate":
                    area_cards.append({"type": "thermostat", "entity": eid})
                elif domain == "camera":
                    area_cards.append({"type": "picture-entity", "entity": eid})
                else:
                    area_cards.append({"type": "entity", "entity": eid})

            # Sensors for this area as a glance card
            area_sensor_list = area_sensors.get(area_name, [])
            if area_sensor_list:
                area_cards.append({
                    "type": "glance",
                    "title": "Sensors",
                    "entities": [{"entity": eid} for eid in sorted(area_sensor_list)[:8]],
                })

            if area_cards:
                cards.append({
                    "type": "vertical-stack",
                    "title": area_name,
                    "cards": area_cards,
                })

        # Cameras
        for ceid in camera_entities:
            cards.append({"type": "picture-entity", "entity": ceid})

        # Scenes
        if scene_entities:
            cards.append({
                "type": "glance",
                "title": "Scenes",
                "entities": [{"entity": eid, "tap_action": {"action": "call-service", "service": "scene.turn_on", "service_data": {"entity_id": eid}}} for eid in scene_entities[:8]],
            })

        # Unassigned controllable devices
        if unassigned_controllable:
            un_cards: list[dict[str, Any]] = []
            for eid in sorted(unassigned_controllable):
                domain = eid.split(".")[0]
                if domain == "media_player":
                    un_cards.append({"type": "media-control", "entity": eid})
                elif domain in ("light", "switch", "fan", "cover"):
                    un_cards.append({"type": "button", "entity": eid, "tap_action": {"action": "toggle"}})
                elif domain == "climate":
                    un_cards.append({"type": "thermostat", "entity": eid})
                else:
                    un_cards.append({"type": "entity", "entity": eid})
            cards.append({
                "type": "vertical-stack",
                "title": "Other Devices",
                "cards": un_cards,
            })

        # Unassigned sensors
        if unassigned_sensors:
            cards.append({
                "type": "glance",
                "title": "Sensors",
                "entities": [{"entity": eid} for eid in sorted(unassigned_sensors)[:12]],
            })

        # The dashboard config to use if creating from scratch
        config = {
            "title": "Home",
            "views": [
                {
                    "path": "default_view",
                    "title": "Overview",
                    "cards": cards,
                }
            ],
        }

        selora_card = {
            "type": "entities",
            "title": "Selora AI Hub",
            "entities": [
                "sensor.selora_ai_hub_devices",
                "sensor.selora_ai_hub_status",
                "sensor.selora_ai_hub_discovery",
            ],
        }

        # Use HA's Lovelace API — updates cache + fires events for immediate effect
        saved = False
        try:
            lovelace_data = self.hass.data.get("lovelace")
            if lovelace_data and hasattr(lovelace_data, "dashboards"):
                for url_path, dashboard_obj in lovelace_data.dashboards.items():
                    if not hasattr(dashboard_obj, "async_save"):
                        continue
                    try:
                        current_config = await dashboard_obj.async_load(force=False)
                    except Exception:
                        # No config yet — save our full config
                        await dashboard_obj.async_save(config)
                        _LOGGER.info(
                            "Generated dashboard '%s' with %d cards",
                            url_path, len(cards),
                        )
                        saved = True
                        continue

                    # Dashboard has config — ensure Selora AI Hub card is present
                    views = current_config.get("views", [])
                    if not views:
                        current_config["views"] = [{"path": "default_view", "title": "Overview", "cards": []}]
                        views = current_config["views"]
                    view_cards = views[0].get("cards", [])
                    has_selora = any(
                        c.get("title") == "Selora AI Hub" for c in view_cards
                    )
                    if not has_selora:
                        view_cards.insert(0, selora_card)
                        views[0]["cards"] = view_cards
                        await dashboard_obj.async_save(current_config)
                        _LOGGER.info(
                            "Added Selora AI Hub card to dashboard '%s'",
                            url_path,
                        )
                    saved = True
        except Exception:
            _LOGGER.debug("Lovelace API save failed", exc_info=True)

        if saved:
            return {"generated": True, "cards": len(cards)}

        # Fallback: direct file write (takes effect after HA restart)
        import json, os
        storage_path = os.path.join(self.hass.config.path(), ".storage", "lovelace")
        if not os.path.exists(storage_path):
            dashboard_data = {
                "version": 1,
                "minor_version": 1,
                "key": "lovelace",
                "data": {"config": config},
            }
            await self.hass.async_add_executor_job(
                self._write_dashboard, storage_path, dashboard_data
            )
            _LOGGER.info("Generated dashboard (file fallback) with %d cards", len(cards))
        return {"generated": True, "cards": len(cards)}

    @staticmethod
    def _read_dashboard(path: str) -> dict:
        import json
        with open(path, "r") as f:
            return json.load(f)

    @staticmethod
    def _write_dashboard(path: str, data: dict) -> None:
        import json
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

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
        """Remove Selora AI mirror devices + orphaned entities.

        Keeps only the Hub device and its core entities (status sensor + 4 action buttons).
        Removes everything else: stale accept/reject buttons, Turn On/Off, Kitchen Status, etc.
        """
        from .const import DOMAIN

        dev_reg = dr.async_get(self.hass)
        ent_reg = er.async_get(self.hass)
        hub_id = (DOMAIN, "selora_ai_hub")

        # Unique IDs of entities we want to KEEP on the Hub
        _KEEP_UNIQUE_IDS = {
            "selora_ai_hub_status",            # status sensor
            "selora_ai_hub_device_list",       # device list sensor
            "selora_ai_hub_last_activity",     # last activity sensor
            "selora_ai_hub_discovery",         # discovery sensor
            f"{DOMAIN}_discover",              # discover button
            f"{DOMAIN}_auto_setup",            # auto setup button
            f"{DOMAIN}_cleanup",               # cleanup button
            f"{DOMAIN}_reset",                 # reset button
        }

        removed_devices: list[str] = []
        removed_entities: list[str] = []

        # 1. Remove non-Hub devices owned by our integration
        for device in list(dev_reg.devices.values()):
            if not any(ident[0] == DOMAIN for ident in device.identifiers):
                continue
            if hub_id in device.identifiers:
                continue

            for entity in er.async_entries_for_device(ent_reg, device.id, include_disabled_entities=True):
                ent_reg.async_remove(entity.entity_id)
                removed_entities.append(entity.entity_id)

            dev_reg.async_remove_device(device.id)
            removed_devices.append(device.name or device.id)

        # 2. Remove orphaned entities on the Hub that aren't core
        for entity in list(ent_reg.entities.values()):
            if entity.platform != DOMAIN:
                continue
            if entity.unique_id in _KEEP_UNIQUE_IDS:
                continue
            ent_reg.async_remove(entity.entity_id)
            removed_entities.append(entity.entity_id)

        if removed_devices or removed_entities:
            _LOGGER.info(
                "Cleaned up %d mirror devices, %d stale entities",
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


def _json(data: Any, status: int = 200) -> Response:
    return Response(
        text=json.dumps(data),
        content_type="application/json",
        status=status,
    )


def _get_flow_handler(hass: HomeAssistant, flow_id: str) -> str:
    """Look up the handler (domain) for a given flow_id."""
    for flow in hass.config_entries.flow.async_progress():
        if flow["flow_id"] == flow_id:
            return flow.get("handler", "")
    return ""


async def handle_devices_webhook(
    hass: HomeAssistant, webhook_id: str, request: Request
) -> Response:
    """Route device-management webhook requests by action."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return _json({"error": "Invalid JSON"}, 400)

    action = body.get("action", "").strip()
    if not action:
        return _json({"error": "Missing 'action' field"}, 400)

    from .const import DOMAIN  # local import to avoid circular ref

    # Find the DeviceManager stored during setup
    dm: DeviceManager | None = None
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if isinstance(entry_data, dict) and "device_manager" in entry_data:
            dm = entry_data["device_manager"]
            break

    if dm is None:
        return _json({"error": "DeviceManager not initialised"}, 503)

    try:
        if action == "list":
            devices = await dm.list_discovered()
            return _json({"discovered": devices})

        if action == "add":
            flow_id = body.get("flow_id", "")
            if not flow_id:
                return _json({"error": "Missing 'flow_id'"}, 400)
            result = await dm.accept_flow(flow_id)
            return _json(result)

        if action == "pair":
            flow_id = body.get("flow_id", "")
            pin = body.get("pin", "")
            if not flow_id or not pin:
                return _json({"error": "Missing 'flow_id' or 'pin'"}, 400)
            result = await dm.submit_pin(flow_id, pin)
            return _json(result)

        if action == "discover":
            domain = body.get("domain", "")
            host = body.get("host", "")
            # With domain+host: manual flow start (existing behavior)
            if domain and host:
                result = await dm.start_device_flow(domain, host)
                return _json(result)
            # No params: comprehensive network status
            result = await dm.discover_network_devices()
            return _json(result)

        if action == "auto_setup":
            result = await dm.auto_setup_discovered()
            return _json(result)

        if action == "configure":
            flow_id = body.get("flow_id", "")
            user_input = body.get("user_input", {})
            if not flow_id:
                return _json({"error": "Missing 'flow_id'"}, 400)
            result = await dm.configure_step(flow_id, user_input)
            return _json(result)

        if action == "reset":
            reset_result = await dm.reset_integrations()
            cleanup_result = await dm.cleanup_mirror_devices()
            # Wait for HA's SSDP/mDNS to re-discover devices on the network
            await asyncio.sleep(15)
            auto_result = await dm.auto_setup_discovered()
            return _json({**reset_result, **cleanup_result, "auto_setup": auto_result})

        if action == "cleanup":
            result = await dm.cleanup_mirror_devices()
            return _json(result)

        return _json({"error": f"Unknown action: {action}"}, 400)

    except Exception as exc:
        _LOGGER.error("Device webhook error (%s): %s", action, exc)
        return _json({"error": str(exc)}, 500)
