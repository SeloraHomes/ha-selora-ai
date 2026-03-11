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
    OpenAI API     — GPT models, cloud
    Ollama         — Llama 3.1, local, on-prem fallback
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import voluptuous as vol

from aiohttp.web import Request, Response

from homeassistant.components import conversation, webhook, websocket_api
from homeassistant.components.websocket_api import decorators

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from datetime import datetime, timedelta

from .const import (
    CONF_ANTHROPIC_API_KEY,
    CONF_ANTHROPIC_MODEL,
    CONF_ENTRY_TYPE,
    CONF_LLM_PROVIDER,
    CONF_OLLAMA_HOST,
    CONF_OLLAMA_MODEL,
    CONF_OPENAI_API_KEY,
    CONF_OPENAI_MODEL,
    CONF_RECORDER_LOOKBACK_DAYS,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_RECORDER_LOOKBACK_DAYS,
    DOMAIN,
    ENTRY_TYPE_DEVICE,
    LLM_PROVIDER_ANTHROPIC,
    LLM_PROVIDER_OLLAMA,
    LLM_PROVIDER_OPENAI,
    LLM_PROVIDER_NONE,
    SIGNAL_ACTIVITY_LOG,
    SIGNAL_DEVICES_UPDATED,
    WEBHOOK_DEVICES_ID,
    AUTOMATION_ID_PREFIX,
    PANEL_NAME,
    PANEL_TITLE,
    PANEL_ICON,
    PANEL_PATH,
    CONF_COLLECTOR_ENABLED,
    CONF_COLLECTOR_MODE,
    CONF_COLLECTOR_INTERVAL,
    CONF_COLLECTOR_START_TIME,
    CONF_COLLECTOR_END_TIME,
    CONF_DISCOVERY_ENABLED,
    CONF_DISCOVERY_MODE,
    CONF_DISCOVERY_INTERVAL,
    CONF_DISCOVERY_START_TIME,
    CONF_DISCOVERY_END_TIME,
    MODE_SCHEDULED,
    DEFAULT_DISCOVERY_ENABLED,
    DEFAULT_DISCOVERY_MODE,
    DEFAULT_DISCOVERY_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

WEBHOOK_ID = "selora_ai_command"
PLATFORMS = ["sensor", "button", "conversation"]


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

    from .llm_client import LLMClient
    
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


@websocket_api.async_response
@decorators.websocket_command({
    vol.Required("type"): "selora_ai/chat",
    vol.Required("message"): str,
})
async def _handle_websocket_chat(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Handle chat messages from the side panel."""
    
    # 1. Try Home Assistant's native conversation assistant first.
    # This allows the user to perform immediate actions without LLM reasoning.
    try:
        ha_result = await conversation.async_converse(
            hass=hass,
            text=msg["message"],
            conversation_id=None,
            context=connection.context,
        )
        
        # If HA assistant handled it successfully, return its response.
        if ha_result.response.error_code is None:
            speech = ha_result.response.speech.get("plain", {}).get("speech", "")
            if not speech and hasattr(ha_result.response, "as_dict"):
                # Fallback to dict access if structured get fails
                res_dict = ha_result.response.as_dict()
                speech = res_dict.get("speech", {}).get("plain", {}).get("speech", "")

            if speech:
                connection.send_result(msg["id"], {
                    "response": speech,
                    "automation": None,
                    "automation_yaml": None,
                })
                return
    except Exception as exc:
        _LOGGER.error("Error in HA conversation processing: %s", exc)
        # Continue to Selora LLM if Assist fails or crashes

    # 2. Fall back to Selora LLM for automation reasoning.
    from .llm_client import LLMClient
    
    llm: LLMClient | None = None
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if isinstance(entry_data, dict) and "llm" in entry_data:
            llm = entry_data["llm"]
            break

    if llm is None:
        connection.send_error(msg["id"], "not_initialized", "Selora AI LLM not initialized")
        return

    # Get context: entities, devices, areas
    entities = _collect_entity_states(hass)
    
    # Get existing automations for context
    automations = []
    for state in hass.states.async_all("automation"):
        automations.append({
            "entity_id": state.entity_id,
            "alias": state.attributes.get("friendly_name", state.entity_id),
            "state": state.state,
        })

    # Send message to LLM (enhanced for chat)
    result = await llm.architect_chat(
        msg["message"], 
        entities, 
        existing_automations=automations
    )
    
    if "error" in result:
        connection.send_error(msg["id"], "llm_error", result["error"])
        return
        
    connection.send_result(msg["id"], {
        "response": result.get("response", "I'm not sure how to help with that."),
        "automation": result.get("automation"),
        "automation_yaml": result.get("automation_yaml"),
    })


@websocket_api.async_response
@decorators.websocket_command({
    vol.Required("type"): "selora_ai/create_automation",
    vol.Required("automation"): dict,
})
async def _handle_websocket_create_automation(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create a new automation from the side panel."""
    automation_data = msg["automation"]
    
    # Basic validation
    has_trigger = automation_data.get("trigger") or automation_data.get("triggers")
    has_action = automation_data.get("action") or automation_data.get("actions")
    
    if not automation_data.get("alias") or not has_trigger or not has_action:
        connection.send_error(msg["id"], "invalid_format", "Invalid automation structure (missing alias, trigger, or action)")
        return

    try:
        # We'll use the automation service to create it if available, 
        # but HA usually requires writing to automations.yaml for manual creation.
        # For now, we'll implement a helper to write it.
        from .automation_utils import async_create_automation
        
        success = await async_create_automation(hass, automation_data)
        
        if success:
            connection.send_result(msg["id"], {"status": "success"})
        else:
            connection.send_error(msg["id"], "creation_failed", "Failed to write automation to file")
            
    except Exception as exc:
        _LOGGER.exception("Error creating automation")
        connection.send_error(msg["id"], "error", str(exc))


@websocket_api.async_response
@decorators.websocket_command({
    vol.Required("type"): "selora_ai/get_suggestions",
})
async def _handle_websocket_get_suggestions(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the latest automated suggestions from the background collector."""
    suggestions = hass.data.get(DOMAIN, {}).get("latest_suggestions", [])
    connection.send_result(msg["id"], suggestions)


@websocket_api.async_response
@decorators.websocket_command({
    vol.Required("type"): "selora_ai/get_automations",
})
async def _handle_websocket_get_automations(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return all existing automations and flag Selora-managed ones."""
    try:
        from homeassistant.helpers import entity_registry as er
        
        registry = er.async_get(hass)
        automations = []
        
        for state in hass.states.async_all("automation"):
            entity_id = state.entity_id
            entry = registry.async_get(entity_id)
            
            is_selora = False
            if entry and entry.unique_id:
                is_selora = entry.unique_id.startswith(AUTOMATION_ID_PREFIX)
            
            # Fallback to description check if unique_id doesn't match
            description = state.attributes.get("description", "")
            if not is_selora and description and "[Selora AI]" in description:
                is_selora = True
                
            automations.append({
                "entity_id": entity_id,
                "alias": state.attributes.get("friendly_name", entity_id),
                "description": description,
                "state": state.state,
                "is_selora": is_selora,
                "last_triggered": state.attributes.get("last_triggered"),
            })
            
        connection.send_result(msg["id"], automations)
    except Exception as exc:
        _LOGGER.exception("Error in _handle_websocket_get_automations")
        connection.send_error(msg["id"], "unknown_error", str(exc))


@websocket_api.async_response
@decorators.websocket_command({
    vol.Required("type"): "selora_ai/get_config",
})
async def _handle_websocket_get_config(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the current integration config."""
    # We find the first config entry for our domain
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        connection.send_error(msg["id"], "not_configured", "Selora AI not configured")
        return
    
    entry = entries[0]
    # Merge entry data with options for a complete view
    config_data = {**entry.data, **entry.options}
    
    connection.send_result(msg["id"], {
        "llm_provider": config_data.get(CONF_LLM_PROVIDER),
        "anthropic_api_key": config_data.get(CONF_ANTHROPIC_API_KEY, ""),
        "anthropic_model": config_data.get(CONF_ANTHROPIC_MODEL, DEFAULT_ANTHROPIC_MODEL),
        "openai_api_key": config_data.get(CONF_OPENAI_API_KEY, ""),
        "openai_model": config_data.get(CONF_OPENAI_MODEL, DEFAULT_OPENAI_MODEL),
        "ollama_host": config_data.get(CONF_OLLAMA_HOST, DEFAULT_OLLAMA_HOST),
        "ollama_model": config_data.get(CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL),
        # Background Services
        "collector_enabled": config_data.get(CONF_COLLECTOR_ENABLED, True),
        "collector_mode": config_data.get(CONF_COLLECTOR_MODE, "continuous"),
        "collector_interval": config_data.get(CONF_COLLECTOR_INTERVAL, 3600),
        "collector_start_time": config_data.get(CONF_COLLECTOR_START_TIME, "09:00"),
        "collector_end_time": config_data.get(CONF_COLLECTOR_END_TIME, "17:00"),
        "discovery_enabled": config_data.get(CONF_DISCOVERY_ENABLED, True),
        "discovery_mode": config_data.get(CONF_DISCOVERY_MODE, "continuous"),
        "discovery_interval": config_data.get(CONF_DISCOVERY_INTERVAL, 14400),
        "discovery_start_time": config_data.get(CONF_DISCOVERY_START_TIME, "00:00"),
        "discovery_end_time": config_data.get(CONF_DISCOVERY_END_TIME, "23:59"),
    })


@websocket_api.async_response
@decorators.websocket_command({
    vol.Required("type"): "selora_ai/update_config",
    vol.Required("config"): dict,
})
async def _handle_websocket_update_config(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Update the integration config and re-initialize."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        connection.send_error(msg["id"], "not_configured", "Selora AI not configured")
        return
    
    entry = entries[0]
    new_config = msg["config"]
    
    # Split into data and options
    data_keys = {
        CONF_LLM_PROVIDER,
        CONF_ANTHROPIC_API_KEY,
        CONF_ANTHROPIC_MODEL,
        CONF_OLLAMA_HOST,
        CONF_OLLAMA_MODEL,
        CONF_ENTRY_TYPE,
    }
    
    new_data = {k: v for k, v in new_config.items() if k in data_keys}
    new_options = {k: v for k, v in new_config.items() if k not in data_keys}
    
    # Update the entry
    hass.config_entries.async_update_entry(
        entry, 
        data={**entry.data, **new_data},
        options={**entry.options, **new_options}
    )
    
    # Reload the entry to apply changes
    await hass.config_entries.async_reload(entry.entry_id)
    connection.send_result(msg["id"], {"status": "success"})


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Selora AI component."""
    hass.data.setdefault(DOMAIN, {})

    # Register WebSocket API
    websocket_api.async_register_command(hass, _handle_websocket_chat)
    websocket_api.async_register_command(hass, _handle_websocket_create_automation)
    websocket_api.async_register_command(hass, _handle_websocket_get_suggestions)
    websocket_api.async_register_command(hass, _handle_websocket_get_automations)
    websocket_api.async_register_command(hass, _handle_websocket_get_config)
    websocket_api.async_register_command(hass, _handle_websocket_update_config)

    # Register static path for frontend
    # Modern way to register static paths (2024.7+)
    try:
        from homeassistant.components.http import StaticPathConfig
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    f"/api/{DOMAIN}/panel.js",
                    hass.config.path(f"custom_components/{DOMAIN}/frontend/panel.js"),
                    False,
                ),
                StaticPathConfig(
                    f"/api/{DOMAIN}/logo.png",
                    hass.config.path(f"custom_components/{DOMAIN}/brand/logo.png"),
                    True,
                ),
            ]
        )
    except (ImportError, AttributeError):
        # Fallback for older versions
        hass.http.register_static_path(
            f"/api/{DOMAIN}/panel.js",
            hass.config.path(f"custom_components/{DOMAIN}/frontend/panel.js"),
            False,
        )
        hass.http.register_static_path(
            f"/api/{DOMAIN}/logo.png",
            hass.config.path(f"custom_components/{DOMAIN}/brand/logo.png"),
            True,
        )

    # Register custom side panel in the sidebar
    from homeassistant.components import frontend
    
    # In recent HA, async_register_panel might be deprecated or renamed
    # We try both async_register_panel and async_register_built_in_panel
    if hasattr(frontend, "async_register_panel"):
        frontend.async_register_panel(
            hass,
            frontend_url_path=PANEL_PATH,
            webcomponent_name=PANEL_NAME,
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            module_url=f"/api/{DOMAIN}/panel.js",
            config={"domain": DOMAIN},
            require_admin=False,
        )
    elif hasattr(frontend, "async_register_built_in_panel"):
        try:
            frontend.async_register_built_in_panel(
                hass,
                component_name="custom",
                sidebar_title=PANEL_TITLE,
                sidebar_icon=PANEL_ICON,
                frontend_url_path=PANEL_PATH,
                config={
                    "_panel_custom": {
                        "name": PANEL_NAME,
                        "module_url": f"/api/{DOMAIN}/panel.js",
                    },
                    "domain": DOMAIN,
                },
                require_admin=False,
            )
        except ValueError as err:
            _LOGGER.warning("Panel already registered: %s", err)
    else:
        _LOGGER.warning("Neither async_register_panel nor async_register_built_in_panel found in frontend")

    _LOGGER.info("Selora AI initialized (awaiting entry)")

    return True


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

    from .llm_client import LLMClient
    from .device_manager import DeviceManager

    if provider == LLM_PROVIDER_ANTHROPIC:
        llm = LLMClient(
            hass,
            provider=provider,
            api_key=entry.data.get(CONF_ANTHROPIC_API_KEY, ""),
            model=entry.data.get(CONF_ANTHROPIC_MODEL, DEFAULT_ANTHROPIC_MODEL),
            lookback_days=lookback,
        )
    elif provider == LLM_PROVIDER_OPENAI:
        llm = LLMClient(
            hass,
            provider=provider,
            api_key=entry.data.get(CONF_OPENAI_API_KEY, ""),
            model=entry.data.get(CONF_OPENAI_MODEL, DEFAULT_OPENAI_MODEL),
            lookback_days=lookback,
        )
    elif provider == LLM_PROVIDER_OLLAMA:
        llm = LLMClient(
            hass,
            provider=provider,
            host=entry.data.get(CONF_OLLAMA_HOST, DEFAULT_OLLAMA_HOST),
            model=entry.data.get(CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL),
            lookback_days=lookback,
        )
    else:
        # Provider is NONE (skipped)
        llm = None

    # Verify LLM is healthy on startup
    if llm and not await llm.health_check():
        _LOGGER.warning(
            "%s not reachable — will retry on next collection cycle",
            llm.provider_name,
        )

    from .collector import DataCollector
    collector = DataCollector(hass, llm, lookback_days=lookback, settings=entry.options)
    device_mgr = DeviceManager(
        hass,
        api_key=entry.data.get(CONF_ANTHROPIC_API_KEY, "") if llm else "",
        model=entry.data.get(CONF_ANTHROPIC_MODEL, DEFAULT_ANTHROPIC_MODEL) if llm else DEFAULT_ANTHROPIC_MODEL,
    )

    # Store references for cleanup on unload
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "llm": llm,
        "collector": collector,
        "device_manager": device_mgr,
        "unsub_discovery": None, # Will be set below
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

    # Schedule periodic discovery if enabled
    options = entry.options
    discovery_enabled = options.get(CONF_DISCOVERY_ENABLED, DEFAULT_DISCOVERY_ENABLED)
    
    async def _run_discovery(_now: datetime | None = None) -> None:
        """Run the discovery process and respect settings."""
        # Respect schedule window if not initial startup
        if _now is not None:
            mode = options.get(CONF_DISCOVERY_MODE, DEFAULT_DISCOVERY_MODE)
            if mode == MODE_SCHEDULED:
                start_str = options.get(CONF_DISCOVERY_START_TIME, "00:00")
                end_str = options.get(CONF_DISCOVERY_END_TIME, "23:59")
                
                # Inline _is_within_window logic
                try:
                    now_time = datetime.now().time()
                    start_time = datetime.strptime(start_str, "%H:%M").time()
                    end_time = datetime.strptime(end_str, "%H:%M").time()
                    
                    within = False
                    if start_time <= end_time:
                        within = start_time <= now_time <= end_time
                    else:
                        within = now_time >= start_time or now_time <= end_time
                    
                    if not within:
                        _LOGGER.debug("Outside discovery window (%s - %s), skipping", start_str, end_str)
                        return
                except ValueError:
                    _LOGGER.error("Invalid discovery time format: %s or %s", start_str, end_str)

        try:
            result = await device_mgr.discover_network_devices()
            summary = result.get("summary", {})
            _LOGGER.info(
                "Network discovery: %d discovered, %d configured, %d available",
                summary.get("discovered_count", 0),
                summary.get("configured_count", 0),
                summary.get("available_count", 0),
            )
            # Sync Cast known_hosts (safe)
            cast_result = await device_mgr.sync_cast_known_hosts()
            if cast_result.get("updated"):
                _LOGGER.info("Cast known_hosts updated: %s", cast_result.get("added_hosts"))
            
            # Auto-assign areas (safe)
            area_result = await device_mgr.auto_assign_areas()
            if area_result.get("assigned"):
                _LOGGER.info("Auto-assigned %d devices to areas", len(area_result["assigned"]))
            
            # Generate dashboard
            await device_mgr.generate_dashboard()
            async_dispatcher_send(hass, SIGNAL_DEVICES_UPDATED)
            
            discovered_count = summary.get("discovered_count", 0)
            if discovered_count > 0:
                async_dispatcher_send(
                    hass, SIGNAL_ACTIVITY_LOG,
                    f"Network discovery: {discovered_count} new devices found",
                    "discover",
                )
        except Exception:
            _LOGGER.exception("Discovery task failed")

    # Initial delayed discovery
    async def _delayed_discovery() -> None:
        await asyncio.sleep(30)
        if discovery_enabled:
            await _run_discovery()

    hass.async_create_task(_delayed_discovery())
    
    # Periodic discovery timer
    unsub_discovery = None
    if discovery_enabled:
        interval = options.get(CONF_DISCOVERY_INTERVAL, DEFAULT_DISCOVERY_INTERVAL)
        unsub_discovery = async_track_time_interval(
            hass, _run_discovery, timedelta(seconds=interval)
        )
        _LOGGER.info("Periodic discovery started (interval: %ss)", interval)
        hass.data[DOMAIN][entry.entry_id]["unsub_discovery"] = unsub_discovery

    # Set up entity platforms (sensor + button)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Immediately add Selora AI Hub card to dashboard (entities now exist)
    try:
        await device_mgr.generate_dashboard()
    except Exception:
        _LOGGER.debug("Immediate dashboard generation failed — will retry in delayed discovery")

    # Register webhooks (only once, not per entry)
    if not hass.data[DOMAIN].get("_webhook_registered"):
        from .device_manager import handle_devices_webhook
        webhook.async_register(
            hass, DOMAIN, "Selora AI Command", WEBHOOK_ID, _handle_webhook
        )
        webhook.async_register(
            hass, DOMAIN, "Selora AI Devices", WEBHOOK_DEVICES_ID, handle_devices_webhook
        )
        hass.data[DOMAIN]["_webhook_registered"] = True
        _LOGGER.info("Selora AI webhooks registered: /api/webhook/%s, /api/webhook/%s", WEBHOOK_ID, WEBHOOK_DEVICES_ID)
    
    # Start background collection + analysis
    if llm:
        await collector.async_start()
        _LOGGER.info("Selora AI started (%s)", llm.provider_name)
    else:
        _LOGGER.info("Selora AI started (unconfigured mode)")
    
    # Register update listener for options
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload — stop background tasks, close sessions."""
    # Device onboarding entries have no runtime state to clean up
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_DEVICE:
        return True

    data = hass.data[DOMAIN].pop(entry.entry_id, {})

    collector: DataCollector | None = data.get("collector")
    unsub_discovery = data.get("unsub_discovery")

    if collector:
        await collector.async_stop()
    
    if unsub_discovery:
        unsub_discovery()

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
