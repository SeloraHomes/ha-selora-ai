"""Selora AI — Home Assistant Integration.

Self-contained HA custom integration.

    HA entity registry / state machine / recorder (SQLite)
        |
        v
    DataCollector  ──snapshot──>  LLMClient (Anthropic API or local Ollama)
        |                              |
        |                         suggestions
        |                              v
        v                    automations.yaml (disabled)
    logging + sensors              + reload

LLM Backends:
    Anthropic API  — Claude, cloud, recommended
    Ollama         — Llama 3.1, local, on-prem fallback
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiohttp.web import Request, Response

from homeassistant.components import webhook

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_ANTHROPIC_API_KEY,
    CONF_ANTHROPIC_MODEL,
    CONF_ENTRY_TYPE,
    CONF_LLM_PROVIDER,
    CONF_OLLAMA_HOST,
    CONF_OLLAMA_MODEL,
    CONF_RECORDER_LOOKBACK_DAYS,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_RECORDER_LOOKBACK_DAYS,
    DOMAIN,
    ENTRY_TYPE_DEVICE,
    LLM_PROVIDER_ANTHROPIC,
    SIGNAL_ACTIVITY_LOG,
    SIGNAL_DEVICES_UPDATED,
    WEBHOOK_DEVICES_ID,
)
from .collector import DataCollector
from .device_manager import DeviceManager, handle_devices_webhook
from .llm_client import LLMClient

_LOGGER = logging.getLogger(__name__)

WEBHOOK_ID = "selora_ai_command"
PLATFORMS = ["sensor", "button"]


def _collect_entity_states(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Get current states of all entities for the LLM."""
    states = []
    for state in hass.states.async_all():
        states.append(
            {
                "entity_id": state.entity_id,
                "state": state.state,
                "attributes": {
                    "friendly_name": state.attributes.get("friendly_name", ""),
                },
            }
        )
    return states


async def _handle_webhook(
    hass: HomeAssistant, webhook_id: str, request: Request
) -> Response:
    """Handle incoming Selora AI commands via webhook."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return Response(
            text=json.dumps({"error": "Invalid JSON"}),
            content_type="application/json",
            status=400,
        )

    command = body.get("command", "").strip()
    if not command:
        return Response(
            text=json.dumps({"error": "Missing 'command' field"}),
            content_type="application/json",
            status=400,
        )

    _LOGGER.info("Selora AI received command: %s", command)

    # Find the first available LLM client
    llm: LLMClient | None = None
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if isinstance(entry_data, dict) and "llm" in entry_data:
            llm = entry_data["llm"]
            break

    if llm is None:
        return Response(
            text=json.dumps({"error": "No LLM configured"}),
            content_type="application/json",
            status=503,
        )

    # Get current entity states dynamically
    entities = _collect_entity_states(hass)

    # Ask the LLM to translate the command
    result = await llm.execute_command(command, entities)

    calls = result.get("calls", [])
    response_text = result.get("response", "No response")

    # Execute the service calls
    executed = []
    for call in calls:
        service = call.get("service", "")
        if not service or "." not in service:
            continue

        domain, service_name = service.split(".", 1)
        target = call.get("target", {})
        data = call.get("data", {})

        try:
            await hass.services.async_call(
                domain, service_name, {**data, **target}, blocking=True
            )
            executed.append(service)
            _LOGGER.info("Executed: %s → %s", service, target)
        except Exception as exc:
            _LOGGER.error("Failed to execute %s: %s", service, exc)
            response_text += f" (Failed: {service}: {exc})"

    return Response(
        text=json.dumps({
            "command": command,
            "response": response_text,
            "executed": executed,
        }),
        content_type="application/json",
        status=200,
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Selora AI from a config entry."""
    # Device onboarding entries are records only — no runtime setup needed
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_DEVICE:
        _LOGGER.info("Selora AI device onboarding entry loaded: %s", entry.title)
        return True

    provider = entry.data.get(CONF_LLM_PROVIDER, DEFAULT_LLM_PROVIDER)

    lookback = entry.data.get(
        CONF_RECORDER_LOOKBACK_DAYS, DEFAULT_RECORDER_LOOKBACK_DAYS
    )

    if provider == LLM_PROVIDER_ANTHROPIC:
        llm = LLMClient(
            hass,
            provider=provider,
            api_key=entry.data.get(CONF_ANTHROPIC_API_KEY, ""),
            model=entry.data.get(CONF_ANTHROPIC_MODEL, DEFAULT_ANTHROPIC_MODEL),
            lookback_days=lookback,
        )
    else:
        llm = LLMClient(
            hass,
            provider=provider,
            host=entry.data.get(CONF_OLLAMA_HOST, DEFAULT_OLLAMA_HOST),
            model=entry.data.get(CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL),
            lookback_days=lookback,
        )

    # Verify LLM is healthy on startup
    if not await llm.health_check():
        _LOGGER.warning(
            "%s not reachable — will retry on next collection cycle",
            llm.provider_name,
        )

    collector = DataCollector(hass, llm, lookback_days=lookback)
    device_mgr = DeviceManager(
        hass,
        api_key=entry.data.get(CONF_ANTHROPIC_API_KEY, ""),
        model=entry.data.get(CONF_ANTHROPIC_MODEL, DEFAULT_ANTHROPIC_MODEL),
    )

    # Store references for cleanup on unload
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "llm": llm,
        "collector": collector,
        "device_manager": device_mgr,
    }

    # Register a hub device for Selora AI (service type — not a physical device)
    from homeassistant.helpers import device_registry as dr
    dev_reg = dr.async_get(hass)
    hub_device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "selora_ai_hub")},
        name="Selora AI Hub",
        manufacturer="Selora Homes",
        model="Selora AI",
        sw_version="0.1.0",
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    # Always clear area — Hub is whole-home, not room-specific
    if hub_device.area_id:
        dev_reg.async_update_device(hub_device.id, area_id=None)

    # Clean up stale mirror devices from previous versions
    cleanup = await device_mgr.cleanup_mirror_devices()
    if cleanup["removed_devices"]:
        _LOGGER.info("Removed %d mirror devices on startup", len(cleanup["removed_devices"]))

    # Schedule delayed discovery (30s) so HA's SSDP/mDNS has time to find devices
    # Discovery only — users onboard devices via "Add Entry" in the UI
    async def _delayed_discovery() -> None:
        await asyncio.sleep(30)
        try:
            result = await device_mgr.discover_network_devices()
            summary = result.get("summary", {})
            _LOGGER.info(
                "Startup discovery: %d discovered, %d configured, %d available",
                summary.get("discovered_count", 0),
                summary.get("configured_count", 0),
                summary.get("available_count", 0),
            )
            # Sync Cast known_hosts (safe, does not require user input)
            cast_result = await device_mgr.sync_cast_known_hosts()
            if cast_result.get("updated"):
                _LOGGER.info("Cast known_hosts updated: %s", cast_result.get("added_hosts"))
                await asyncio.sleep(10)
            # Auto-assign areas (safe, does not create new integrations)
            area_result = await device_mgr.auto_assign_areas()
            area_assigned = area_result.get("assigned", [])
            if area_assigned:
                _LOGGER.info("Auto-assigned %d devices to areas", len(area_assigned))
            # Generate dashboard
            await device_mgr.generate_dashboard()
            async_dispatcher_send(hass, SIGNAL_DEVICES_UPDATED)
            discovered_count = summary.get("discovered_count", 0)
            if discovered_count > 0:
                async_dispatcher_send(
                    hass, SIGNAL_ACTIVITY_LOG,
                    f"Startup discovery: {discovered_count} devices found — "
                    f"use 'Add Entry' to onboard them",
                    "discover",
                )
        except Exception:
            _LOGGER.exception("Delayed discovery failed")

    hass.async_create_task(_delayed_discovery())

    # Set up entity platforms (sensor + button)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Immediately add Selora AI Hub card to dashboard (entities now exist)
    try:
        await device_mgr.generate_dashboard()
    except Exception:
        _LOGGER.debug("Immediate dashboard generation failed — will retry in delayed discovery")

    # Register webhooks (only once, not per entry)
    if not hass.data[DOMAIN].get("_webhook_registered"):
        webhook.async_register(
            hass, DOMAIN, "Selora AI Command", WEBHOOK_ID, _handle_webhook
        )
        webhook.async_register(
            hass, DOMAIN, "Selora AI Devices", WEBHOOK_DEVICES_ID, handle_devices_webhook
        )
        hass.data[DOMAIN]["_webhook_registered"] = True
        _LOGGER.info("Selora AI webhooks registered: /api/webhook/%s, /api/webhook/%s", WEBHOOK_ID, WEBHOOK_DEVICES_ID)

    # Start background collection + analysis
    await collector.async_start()

    _LOGGER.info("Selora AI started (%s)", llm.provider_name)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload — stop background tasks, close sessions."""
    # Device onboarding entries have no runtime state to clean up
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_DEVICE:
        return True

    data = hass.data[DOMAIN].pop(entry.entry_id, {})

    collector: DataCollector | None = data.get("collector")

    if collector:
        await collector.async_stop()

    # Unload entity platforms
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Unregister webhooks if no more entries
    remaining = {k: v for k, v in hass.data.get(DOMAIN, {}).items() if k != "_webhook_registered"}
    if not remaining and hass.data.get(DOMAIN, {}).get("_webhook_registered"):
        webhook.async_unregister(hass, WEBHOOK_ID)
        webhook.async_unregister(hass, WEBHOOK_DEVICES_ID)
        hass.data[DOMAIN]["_webhook_registered"] = False

    _LOGGER.info("Selora AI stopped")
    return True
