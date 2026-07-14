"""Selora AI websocket handlers: linking.

Extracted from __init__.py. Handlers reach shared integration
helpers via ``from .. import`` (safe: this module is imported
lazily at registration time, after the package has loaded).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import decorators
from homeassistant.core import HomeAssistant
import voluptuous as vol

from .. import (
    _aigateway_view,
    _decode_jwt_claims,
    _ensure_exclude_label,
    _find_llm,
    _mask_api_key,
    _require_admin,
    _resolve_llm_entry,
    _resolve_llm_provider,
)
from ..const import (
    AUTOMATION_STALE_DAYS,
    CONF_AIGATEWAY_ACCESS_TOKEN,
    CONF_AIGATEWAY_CLIENT_ID,
    CONF_AIGATEWAY_EXPIRES_AT,
    CONF_AIGATEWAY_REFRESH_TOKEN,
    CONF_AIGATEWAY_USER_EMAIL,
    CONF_AIGATEWAY_USER_ID,
    CONF_ANTHROPIC_API_KEY,
    CONF_ANTHROPIC_MODEL,
    CONF_AUTO_PURGE_STALE,
    CONF_COLLECTOR_ENABLED,
    CONF_COLLECTOR_END_TIME,
    CONF_COLLECTOR_INTERVAL,
    CONF_COLLECTOR_MODE,
    CONF_COLLECTOR_START_TIME,
    CONF_DISCOVERY_ENABLED,
    CONF_DISCOVERY_END_TIME,
    CONF_DISCOVERY_INTERVAL,
    CONF_DISCOVERY_MODE,
    CONF_DISCOVERY_START_TIME,
    CONF_ENTRY_TYPE,
    CONF_GEMINI_API_KEY,
    CONF_GEMINI_MODEL,
    CONF_INSIGHTS_ENABLED,
    CONF_INSIGHTS_INTERVAL,
    CONF_LLM_PRICING_OVERRIDES,
    CONF_LLM_PROVIDER,
    CONF_OLLAMA_HOST,
    CONF_OLLAMA_MODEL,
    CONF_OPENAI_API_KEY,
    CONF_OPENAI_MODEL,
    CONF_OPENROUTER_API_KEY,
    CONF_OPENROUTER_MODEL,
    CONF_PATTERN_ENABLED,
    CONF_SELORA_CONNECT_ENABLED,
    CONF_SELORA_CONNECT_URL,
    CONF_SELORA_INSTALLATION_ID,
    CONF_SELORA_JWT_KEY,
    CONF_SELORA_LOCAL_HOST,
    CONF_SELORA_MCP_URL,
    CONF_TELEMETRY_ENABLED,
    CONF_TELEMETRY_PROMPT_SEEN,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_AUTO_PURGE_STALE,
    DEFAULT_COLLECTOR_ENABLED,
    DEFAULT_COLLECTOR_END_TIME,
    DEFAULT_COLLECTOR_INTERVAL,
    DEFAULT_COLLECTOR_MODE,
    DEFAULT_COLLECTOR_START_TIME,
    DEFAULT_DISCOVERY_ENABLED,
    DEFAULT_DISCOVERY_END_TIME,
    DEFAULT_DISCOVERY_INTERVAL,
    DEFAULT_DISCOVERY_MODE,
    DEFAULT_DISCOVERY_START_TIME,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_INSIGHTS_INTERVAL,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_OPENROUTER_HOST,
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_SELORA_CONNECT_URL,
    DEFAULT_SELORA_LOCAL_HOST,
    DEFAULT_TELEMETRY_ENABLED,
    DEFAULT_TELEMETRY_PROMPT_SEEN,
    DOMAIN,
    LLM_PROVIDER_ANTHROPIC,
    LLM_PROVIDER_GEMINI,
    LLM_PROVIDER_OLLAMA,
    LLM_PROVIDER_OPENAI,
    LLM_PROVIDER_OPENROUTER,
    LLM_PROVIDER_SELORA_CLOUD,
    LLM_PROVIDER_SELORA_LOCAL,
    SELORA_EXCLUDE_LABEL_ID,
    SELORA_EXCLUDE_LABEL_NAME,
)

_LOGGER = logging.getLogger(__name__)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_config",
    }
)
async def _handle_websocket_get_config(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the current integration config."""
    if not _require_admin(connection, msg):
        return

    entry = _resolve_llm_entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_configured", "Selora AI not configured")
        return

    from ..entity_filter import resolve_label_tagged_items

    # Merge entry data with options for a complete view
    config_data = {**entry.data, **entry.options}
    aigw = _aigateway_view(config_data)

    from ..providers import discover_selora_local_host

    _selora_local_discovered_host = await discover_selora_local_host(
        hass, config_data.get(CONF_SELORA_LOCAL_HOST)
    )
    _selora_local_available = _selora_local_discovered_host is not None

    # Installed integration version — the frontend keys its recipe-catalog
    # cache on this so an upgrade never reuses a catalog filtered by the
    # old version's min-version gate. Blocking read, cached after first.
    from ..recipes.version_gate import integration_version

    _integration_version = await hass.async_add_executor_job(integration_version)

    connection.send_result(
        msg["id"],
        {
            "llm_provider": _resolve_llm_provider(config_data),
            "integration_version": _integration_version,
            # Never send the raw key to the frontend — only a safe display hint.
            "anthropic_api_key_hint": _mask_api_key(config_data.get(CONF_ANTHROPIC_API_KEY, "")),
            "anthropic_api_key_set": bool(config_data.get(CONF_ANTHROPIC_API_KEY)),
            "anthropic_model": config_data.get(CONF_ANTHROPIC_MODEL, DEFAULT_ANTHROPIC_MODEL),
            "gemini_api_key_hint": _mask_api_key(config_data.get(CONF_GEMINI_API_KEY, "")),
            "gemini_api_key_set": bool(config_data.get(CONF_GEMINI_API_KEY)),
            "gemini_model": config_data.get(CONF_GEMINI_MODEL, DEFAULT_GEMINI_MODEL),
            "openai_api_key_hint": _mask_api_key(config_data.get(CONF_OPENAI_API_KEY, "")),
            "openai_api_key_set": bool(config_data.get(CONF_OPENAI_API_KEY)),
            "openai_model": config_data.get(CONF_OPENAI_MODEL, DEFAULT_OPENAI_MODEL),
            "openrouter_api_key_hint": _mask_api_key(config_data.get(CONF_OPENROUTER_API_KEY, "")),
            "openrouter_api_key_set": bool(config_data.get(CONF_OPENROUTER_API_KEY)),
            "openrouter_model": config_data.get(CONF_OPENROUTER_MODEL, DEFAULT_OPENROUTER_MODEL),
            "ollama_host": config_data.get(CONF_OLLAMA_HOST, DEFAULT_OLLAMA_HOST),
            "ollama_model": config_data.get(CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL),
            "selora_local_host": config_data.get(CONF_SELORA_LOCAL_HOST, DEFAULT_SELORA_LOCAL_HOST),
            "selora_local_available": _selora_local_available,
            "selora_local_discovered_host": _selora_local_discovered_host,
            # Background Services
            "collector_enabled": config_data.get(CONF_COLLECTOR_ENABLED, DEFAULT_COLLECTOR_ENABLED),
            "collector_mode": config_data.get(CONF_COLLECTOR_MODE, DEFAULT_COLLECTOR_MODE),
            "collector_interval": config_data.get(
                CONF_COLLECTOR_INTERVAL, DEFAULT_COLLECTOR_INTERVAL
            ),
            "collector_start_time": config_data.get(
                CONF_COLLECTOR_START_TIME, DEFAULT_COLLECTOR_START_TIME
            ),
            "collector_end_time": config_data.get(
                CONF_COLLECTOR_END_TIME, DEFAULT_COLLECTOR_END_TIME
            ),
            "auto_purge_stale": config_data.get(CONF_AUTO_PURGE_STALE, DEFAULT_AUTO_PURGE_STALE),
            "stale_days": AUTOMATION_STALE_DAYS,
            "discovery_enabled": config_data.get(CONF_DISCOVERY_ENABLED, DEFAULT_DISCOVERY_ENABLED),
            "discovery_mode": config_data.get(CONF_DISCOVERY_MODE, DEFAULT_DISCOVERY_MODE),
            "discovery_interval": config_data.get(
                CONF_DISCOVERY_INTERVAL, DEFAULT_DISCOVERY_INTERVAL
            ),
            "discovery_start_time": config_data.get(
                CONF_DISCOVERY_START_TIME, DEFAULT_DISCOVERY_START_TIME
            ),
            "discovery_end_time": config_data.get(
                CONF_DISCOVERY_END_TIME, DEFAULT_DISCOVERY_END_TIME
            ),
            "pattern_detection_enabled": config_data.get(CONF_PATTERN_ENABLED, True),
            "insights_enabled": config_data.get(CONF_INSIGHTS_ENABLED, True),
            "insights_interval": config_data.get(CONF_INSIGHTS_INTERVAL, DEFAULT_INSIGHTS_INTERVAL),
            "exclude_label_id": SELORA_EXCLUDE_LABEL_ID,
            "exclude_label_name": SELORA_EXCLUDE_LABEL_NAME,
            "label_tagged": resolve_label_tagged_items(hass),
            # Anonymous telemetry (opt-in, off by default)
            "telemetry_enabled": config_data.get(CONF_TELEMETRY_ENABLED, DEFAULT_TELEMETRY_ENABLED),
            "telemetry_prompt_seen": config_data.get(
                CONF_TELEMETRY_PROMPT_SEEN, DEFAULT_TELEMETRY_PROMPT_SEEN
            ),
            # Developer settings
            "developer_mode": config_data.get("developer_mode", False),
            # Selora Connect
            "selora_connect_enabled": config_data.get(CONF_SELORA_CONNECT_ENABLED, False),
            "selora_connect_url": config_data.get(
                CONF_SELORA_CONNECT_URL, DEFAULT_SELORA_CONNECT_URL
            ),
            "selora_installation_id": config_data.get(CONF_SELORA_INSTALLATION_ID, ""),
            "selora_mcp_url": config_data.get(CONF_SELORA_MCP_URL, ""),
            # Selora Cloud (AI Gateway OAuth)
            "aigateway_linked": bool(aigw["refresh_token"]),
            "aigateway_user_email": aigw["user_email"],
            # LLM pricing overrides — shape: {provider: {model: [in_per_mtok, out_per_mtok]}}
            "llm_pricing_overrides": config_data.get(CONF_LLM_PRICING_OVERRIDES, {}),
        },
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/update_config",
        vol.Required("config"): dict,
    }
)
async def _handle_websocket_update_config(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Update the integration config and re-initialize."""
    if not _require_admin(connection, msg):
        return

    entry = _resolve_llm_entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_configured", "Selora AI not configured")
        return

    new_config = msg["config"]

    # Split into data and options
    data_keys = {
        CONF_LLM_PROVIDER,
        CONF_ANTHROPIC_API_KEY,
        CONF_ANTHROPIC_MODEL,
        CONF_GEMINI_API_KEY,
        CONF_GEMINI_MODEL,
        CONF_OPENAI_API_KEY,
        CONF_OPENAI_MODEL,
        CONF_OPENROUTER_API_KEY,
        CONF_OPENROUTER_MODEL,
        CONF_OLLAMA_HOST,
        CONF_OLLAMA_MODEL,
        CONF_SELORA_LOCAL_HOST,
        CONF_ENTRY_TYPE,
        CONF_SELORA_CONNECT_ENABLED,
        CONF_SELORA_CONNECT_URL,
        CONF_SELORA_INSTALLATION_ID,
        CONF_SELORA_JWT_KEY,
    }

    new_data = {k: v for k, v in new_config.items() if k in data_keys}
    new_options = {k: v for k, v in new_config.items() if k not in data_keys}

    # Only persist KNOWN settable options. Without this any key a client sends
    # lands in entry.options verbatim — a crafted (admin) payload could bloat
    # the entry with junk keys or flip a future safety-gating option that isn't
    # meant to be user-settable. Data-keys (credentials/JWT) are already handled
    # above; this allowlist mirrors the option surface get_config exposes.
    allowed_option_keys = {
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
        CONF_AUTO_PURGE_STALE,
        CONF_INSIGHTS_ENABLED,
        CONF_INSIGHTS_INTERVAL,
        CONF_TELEMETRY_ENABLED,
        CONF_TELEMETRY_PROMPT_SEEN,
        CONF_LLM_PRICING_OVERRIDES,
        "pattern_detection_enabled",  # frontend key (see get_config)
        "developer_mode",
    }
    unknown_keys = [k for k in new_options if k not in allowed_option_keys]
    if unknown_keys:
        _LOGGER.warning(
            "Ignoring unrecognized config option key(s): %s", ", ".join(sorted(unknown_keys))
        )
        for k in unknown_keys:
            new_options.pop(k, None)

    # Never store a null/empty provider — fall back to the existing value.
    if CONF_LLM_PROVIDER in new_data and not new_data[CONF_LLM_PROVIDER]:
        new_data[CONF_LLM_PROVIDER] = entry.data.get(CONF_LLM_PROVIDER) or DEFAULT_LLM_PROVIDER

    # Only overwrite the stored API keys if the frontend sent a new non-empty value.
    # The frontend sends an empty string when the user hasn't touched the key field,
    # so we must not clobber the existing key in that case.
    for key in (
        CONF_ANTHROPIC_API_KEY,
        CONF_GEMINI_API_KEY,
        CONF_OPENAI_API_KEY,
        CONF_OPENROUTER_API_KEY,
    ):
        if key in new_data and not new_data[key]:
            new_data.pop(key, None)

    # Keys that only affect the frontend — no reload needed. The telemetry
    # consent flag only gates the one-time banner; it changes no backend
    # behaviour, so persisting it must never trigger a reload.
    frontend_only_keys = {"developer_mode", CONF_TELEMETRY_PROMPT_SEEN}
    # Keys whose change can be applied live to the running LLMClient
    # without rebuilding it. Pricing overrides only impact cost reporting
    # for subsequent calls, so a hot update is enough. The telemetry
    # toggle is read live by ``TelemetryClient`` on every emit, so flipping
    # it needs no reload either.
    hot_option_keys = {CONF_LLM_PRICING_OVERRIDES, CONF_TELEMETRY_ENABLED}

    # Check if any backend-relevant keys actually changed
    old_data = {**entry.data}
    old_options = {**entry.options}
    needs_reload = False
    for k, v in new_data.items():
        if k not in frontend_only_keys and old_data.get(k) != v:
            needs_reload = True
            break
    if not needs_reload:
        for k, v in new_options.items():
            if k not in frontend_only_keys and k not in hot_option_keys and old_options.get(k) != v:
                needs_reload = True
                break

    # Update the entry
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, **new_data}, options={**entry.options, **new_options}
    )

    # Apply hot-reloadable option changes directly to the running client.
    if CONF_LLM_PRICING_OVERRIDES in new_options:
        llm = _find_llm(hass)
        if llm is not None and hasattr(llm, "set_pricing_overrides"):
            llm.set_pricing_overrides(new_options[CONF_LLM_PRICING_OVERRIDES] or {})

    # Send result BEFORE reload so the frontend gets a response
    connection.send_result(msg["id"], {"status": "success"})

    if needs_reload:
        # Schedule the reload as a background task so the WS response arrives first
        async def _reload() -> None:
            try:
                await hass.config_entries.async_reload(entry.entry_id)
            except Exception:
                _LOGGER.exception("Failed to reload entry after config update")

        hass.async_create_task(_reload())


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/apply_exclude_label",
        vol.Optional("entity_id"): str,
        vol.Optional("device_id"): str,
        vol.Optional("area_id"): str,
    }
)
async def _handle_websocket_apply_exclude_label(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Tag an entity / device / area with the Selora exclude label."""
    if not _require_admin(connection, msg):
        return

    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from ..entity_filter import resolve_label_tagged_items

    label_id = _ensure_exclude_label(hass)
    if label_id is None:
        connection.send_error(msg["id"], "label_unavailable", "Could not access label registry")
        return

    entity_id = msg.get("entity_id")
    device_id = msg.get("device_id")
    area_id = msg.get("area_id")
    if not (entity_id or device_id or area_id):
        connection.send_error(
            msg["id"], "missing_target", "entity_id, device_id, or area_id required"
        )
        return

    try:
        if entity_id:
            ent_reg = er.async_get(hass)
            ent = ent_reg.async_get(entity_id)
            if ent is None:
                connection.send_error(msg["id"], "not_found", f"Unknown entity {entity_id}")
                return
            ent_reg.async_update_entity(entity_id, labels=set(ent.labels or ()) | {label_id})
        if device_id:
            dev_reg = dr.async_get(hass)
            dev = dev_reg.async_get(device_id)
            if dev is None:
                connection.send_error(msg["id"], "not_found", f"Unknown device {device_id}")
                return
            dev_reg.async_update_device(device_id, labels=set(dev.labels or ()) | {label_id})
        if area_id:
            area_reg = ar.async_get(hass)
            area = area_reg.async_get_area(area_id)
            if area is None:
                connection.send_error(msg["id"], "not_found", f"Unknown area {area_id}")
                return
            area_reg.async_update(area_id, labels=set(area.labels or ()) | {label_id})
    except Exception as exc:  # noqa: BLE001
        _LOGGER.exception("Failed to apply exclude label")
        connection.send_error(msg["id"], "apply_failed", str(exc))
        return

    connection.send_result(msg["id"], {"label_tagged": resolve_label_tagged_items(hass)})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/remove_exclude_label",
        vol.Optional("entity_id"): str,
        vol.Optional("device_id"): str,
        vol.Optional("area_id"): str,
    }
)
async def _handle_websocket_remove_exclude_label(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Remove the Selora exclude label from an entity / device / area."""
    if not _require_admin(connection, msg):
        return

    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from ..entity_filter import resolve_exclude_label_id, resolve_label_tagged_items

    label_id = resolve_exclude_label_id(hass)
    if label_id is None:
        # No label means nothing to untag — return current (empty) state.
        connection.send_result(msg["id"], {"label_tagged": resolve_label_tagged_items(hass)})
        return

    entity_id = msg.get("entity_id")
    device_id = msg.get("device_id")
    area_id = msg.get("area_id")

    try:
        if entity_id:
            ent_reg = er.async_get(hass)
            ent = ent_reg.async_get(entity_id)
            if ent is not None and label_id in (ent.labels or ()):
                ent_reg.async_update_entity(entity_id, labels=set(ent.labels) - {label_id})
        if device_id:
            dev_reg = dr.async_get(hass)
            dev = dev_reg.async_get(device_id)
            if dev is not None and label_id in (dev.labels or ()):
                dev_reg.async_update_device(device_id, labels=set(dev.labels) - {label_id})
        if area_id:
            area_reg = ar.async_get(hass)
            area = area_reg.async_get_area(area_id)
            if area is not None and label_id in (area.labels or ()):
                area_reg.async_update(area_id, labels=set(area.labels) - {label_id})
    except Exception as exc:  # noqa: BLE001
        _LOGGER.exception("Failed to remove exclude label")
        connection.send_error(msg["id"], "remove_failed", str(exc))
        return

    connection.send_result(msg["id"], {"label_tagged": resolve_label_tagged_items(hass)})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/validate_llm_key",
        vol.Required("provider"): str,
        vol.Optional("api_key"): str,
        vol.Optional("model"): str,
        vol.Optional("host"): str,
    }
)
async def _handle_websocket_validate_llm_key(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Validate an LLM provider key/connection without saving."""
    if not _require_admin(connection, msg):
        return

    from ..providers import create_provider

    provider = msg["provider"]
    api_key = msg.get("api_key", "")
    model = msg.get("model", "")
    host = msg.get("host", "")

    # Apply defaults for missing model/host
    if provider == LLM_PROVIDER_ANTHROPIC:
        model = model or DEFAULT_ANTHROPIC_MODEL
    elif provider == LLM_PROVIDER_GEMINI:
        model = model or DEFAULT_GEMINI_MODEL
    elif provider == LLM_PROVIDER_OPENAI:
        model = model or DEFAULT_OPENAI_MODEL
    elif provider == LLM_PROVIDER_OPENROUTER:
        model = model or DEFAULT_OPENROUTER_MODEL
        host = host or DEFAULT_OPENROUTER_HOST
    elif provider == LLM_PROVIDER_OLLAMA:
        model = model or DEFAULT_OLLAMA_MODEL
        host = host or DEFAULT_OLLAMA_HOST
    elif provider == LLM_PROVIDER_SELORA_LOCAL:
        host = host or DEFAULT_SELORA_LOCAL_HOST

    try:
        llm_provider = create_provider(
            provider,
            hass,
            api_key=api_key,
            model=model,
            host=host,
        )
        valid = await llm_provider.health_check()
        if valid:
            connection.send_result(msg["id"], {"valid": True})
        else:
            connection.send_result(
                msg["id"],
                {"valid": False, "error": "API key invalid or provider unreachable."},
            )
    except Exception as exc:
        connection.send_result(
            msg["id"],
            {"valid": False, "error": str(exc) or "Validation failed."},
        )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/exchange_connect_code",
        vol.Required("code"): str,
        vol.Required("code_verifier"): str,
        vol.Required("redirect_uri"): str,
        vol.Optional("connect_url", default=""): str,
    }
)
async def _handle_websocket_exchange_connect_code(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Exchange an OAuth authorization code for Connect installation credentials."""
    if not _require_admin(connection, msg):
        return

    entry = _resolve_llm_entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_configured", "Selora AI not configured")
        return

    connect_url = (
        msg["connect_url"] or entry.data.get(CONF_SELORA_CONNECT_URL, DEFAULT_SELORA_CONNECT_URL)
    ).rstrip("/")

    import aiohttp

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession() as session:
        # Step 1: Exchange authorization code for an access token
        try:
            async with session.post(
                f"{connect_url}/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": msg["code"],
                    "code_verifier": msg["code_verifier"],
                    "client_id": msg["redirect_uri"],
                    "redirect_uri": msg["redirect_uri"],
                },
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    _LOGGER.warning("Connect token exchange failed (%s): %s", resp.status, body)
                    connection.send_error(
                        msg["id"],
                        "token_exchange_failed",
                        f"Connect returned HTTP {resp.status}",
                    )
                    return
                token_data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            connection.send_error(msg["id"], "connect_unreachable", f"Cannot reach Connect: {err}")
            return

        access_token = token_data.get("access_token")
        if not access_token:
            connection.send_error(msg["id"], "token_exchange_failed", "No access_token in response")
            return

        # Step 2: Register this HA instance as an MCP device
        try:
            async with session.post(
                f"{connect_url}/api/v1/mcp/devices/register",
                json={"device_name": hass.config.location_name or "Home Assistant"},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    _LOGGER.warning(
                        "Connect device registration failed (%s): %s",
                        resp.status,
                        body,
                    )
                    connection.send_error(
                        msg["id"],
                        "registration_failed",
                        f"Device registration returned HTTP {resp.status}",
                    )
                    return
                device_data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            connection.send_error(
                msg["id"],
                "connect_unreachable",
                f"Cannot reach Connect for device registration: {err}",
            )
            return

        device_id = device_data.get("device_id")
        installation_id = device_data.get("installation_id")
        scope_id_from_device = device_data.get("scope_id")
        if not device_id:
            connection.send_error(
                msg["id"],
                "invalid_response",
                "Connect response missing device_id",
            )
            return

        # Step 3: Fetch installation MCP auth config (installation-scoped JWT key)
        # Claude's OAuth flow issues tokens signed with the installation key,
        # not the per-device key from registration.
        jwt_key = None
        scope_id = None
        if installation_id:
            try:
                async with session.get(
                    f"{connect_url}/api/v1/installations/{installation_id}/mcp-auth-config",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=timeout,
                ) as resp:
                    if resp.status == 200:
                        auth_config = await resp.json()
                        jwt_key = auth_config.get("jwt_key")
                        scope_id = auth_config.get("scope_id")
                    else:
                        _LOGGER.warning(
                            "Failed to fetch MCP auth config (%s), using device key",
                            resp.status,
                        )
            except (aiohttp.ClientError, TimeoutError) as err:
                _LOGGER.warning("Could not reach Connect for MCP auth config: %s", err)

    # Fall back to device key only when there is no installation
    if not jwt_key:
        jwt_key = device_data.get("jwt_key")

    if not jwt_key:
        connection.send_error(
            msg["id"],
            "invalid_response",
            "Connect response missing jwt_key",
        )
        return

    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_SELORA_CONNECT_ENABLED: True,
            CONF_SELORA_CONNECT_URL: connect_url,
            CONF_SELORA_INSTALLATION_ID: scope_id
            or scope_id_from_device
            or installation_id
            or device_id,
            CONF_SELORA_JWT_KEY: jwt_key,
        },
    )

    connection.send_result(msg["id"], {"status": "linked", "device_id": device_id})

    # Reload so the JWT validator picks up the new credentials
    async def _reload() -> None:
        try:
            await hass.config_entries.async_reload(entry.entry_id)
        except Exception:
            _LOGGER.exception("Failed to reload entry after Connect linking")

    hass.async_create_task(_reload())


@websocket_api.async_response
@decorators.websocket_command({vol.Required("type"): "selora_ai/unlink_connect"})
async def _handle_websocket_unlink_connect(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Remove Connect credentials from the config entry."""
    if not _require_admin(connection, msg):
        return

    entry = _resolve_llm_entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_configured", "Selora AI not configured")
        return

    new_data = {**entry.data}
    new_data.pop(CONF_SELORA_CONNECT_ENABLED, None)
    new_data.pop(CONF_SELORA_INSTALLATION_ID, None)
    new_data.pop(CONF_SELORA_JWT_KEY, None)
    # Keep CONF_SELORA_CONNECT_URL so the user doesn't have to re-enter it

    hass.config_entries.async_update_entry(entry, data=new_data)

    # Immediately clear the in-memory validator so Selora JWTs are rejected
    # right away, even if the scheduled reload below fails.
    hass.data.get(DOMAIN, {}).pop("selora_jwt_validator", None)

    connection.send_result(msg["id"], {"status": "unlinked"})

    async def _reload() -> None:
        try:
            await hass.config_entries.async_reload(entry.entry_id)
        except Exception:
            _LOGGER.exception("Failed to reload entry after Connect unlinking")

    hass.async_create_task(_reload())


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/exchange_aigateway_code",
        vol.Required("code"): str,
        vol.Required("code_verifier"): str,
        vol.Required("redirect_uri"): str,
        vol.Required("client_id"): str,
        vol.Optional("connect_url", default=""): str,
    }
)
async def _handle_websocket_exchange_aigateway_code(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Exchange an AI Gateway OAuth code for access + refresh tokens."""
    if not _require_admin(connection, msg):
        return

    entry = _resolve_llm_entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_configured", "Selora AI not configured")
        return

    connect_url = (msg["connect_url"] or _aigateway_view(entry.data)["connect_url"]).rstrip("/")

    import time as _time

    import aiohttp

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                f"{connect_url}/oauth/aigw/token",
                data={
                    "grant_type": "authorization_code",
                    "code": msg["code"],
                    "code_verifier": msg["code_verifier"],
                    "client_id": msg["client_id"],
                    "redirect_uri": msg["redirect_uri"],
                },
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    _LOGGER.warning("AI Gateway token exchange failed (%s): %s", resp.status, body)
                    connection.send_error(
                        msg["id"],
                        "token_exchange_failed",
                        f"AI Gateway returned HTTP {resp.status}",
                    )
                    return
                token_data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            connection.send_error(msg["id"], "connect_unreachable", f"Cannot reach Connect: {err}")
            return

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = int(token_data.get("expires_in") or 0)
    if not access_token or not refresh_token:
        connection.send_error(
            msg["id"], "token_exchange_failed", "Missing access_token or refresh_token"
        )
        return

    claims = _decode_jwt_claims(access_token)
    user_email = claims.get("email") or ""
    user_id = str(claims.get("sub") or "")
    expires_at = _time.time() + expires_in if expires_in > 0 else 0.0

    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_LLM_PROVIDER: LLM_PROVIDER_SELORA_CLOUD,
            CONF_AIGATEWAY_ACCESS_TOKEN: access_token,
            CONF_AIGATEWAY_REFRESH_TOKEN: refresh_token,
            CONF_AIGATEWAY_EXPIRES_AT: expires_at,
            CONF_AIGATEWAY_USER_EMAIL: user_email,
            CONF_AIGATEWAY_USER_ID: user_id,
            CONF_AIGATEWAY_CLIENT_ID: msg["client_id"],
            CONF_SELORA_CONNECT_URL: connect_url,
        },
    )

    connection.send_result(
        msg["id"],
        {"status": "linked", "user_email": user_email},
    )

    async def _reload() -> None:
        try:
            await hass.config_entries.async_reload(entry.entry_id)
        except Exception:
            _LOGGER.exception("Failed to reload entry after AI Gateway linking")

    hass.async_create_task(_reload())


@websocket_api.async_response
@decorators.websocket_command({vol.Required("type"): "selora_ai/unlink_aigateway"})
async def _handle_websocket_unlink_aigateway(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Remove AI Gateway OAuth credentials from the config entry.

    Falls back to the default LLM provider so the integration keeps
    working after an unlink.
    """
    if not _require_admin(connection, msg):
        return

    entry = _resolve_llm_entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_configured", "Selora AI not configured")
        return

    new_data = {**entry.data}
    for key in (
        CONF_AIGATEWAY_ACCESS_TOKEN,
        CONF_AIGATEWAY_REFRESH_TOKEN,
        CONF_AIGATEWAY_EXPIRES_AT,
        CONF_AIGATEWAY_USER_EMAIL,
        CONF_AIGATEWAY_USER_ID,
        CONF_AIGATEWAY_CLIENT_ID,
    ):
        new_data.pop(key, None)
    # The hub auto-provisioner stores the same credentials under a nested
    # "selora_ai_gateway" object; drop it (and its legacy alias) too so
    # the user really is unlinked rather than silently re-linking on
    # next reload.
    new_data.pop("selora_ai_gateway", None)
    new_data.pop("ai_gateway", None)
    # Keep llm_provider on selora_cloud so the user stays in the same UI
    # state (set URL override, re-link). The provider will fail health
    # checks until re-linking, which is the expected mid-flow behaviour.

    hass.config_entries.async_update_entry(entry, data=new_data)
    connection.send_result(msg["id"], {"status": "unlinked"})

    async def _reload() -> None:
        try:
            await hass.config_entries.async_reload(entry.entry_id)
        except Exception:
            _LOGGER.exception("Failed to reload entry after AI Gateway unlinking")

    hass.async_create_task(_reload())


def async_register(hass: HomeAssistant) -> None:
    """Register the linking websocket commands."""
    from homeassistant.components import websocket_api

    websocket_api.async_register_command(hass, _handle_websocket_get_config)
    websocket_api.async_register_command(hass, _handle_websocket_update_config)
    websocket_api.async_register_command(hass, _handle_websocket_apply_exclude_label)
    websocket_api.async_register_command(hass, _handle_websocket_remove_exclude_label)
    websocket_api.async_register_command(hass, _handle_websocket_validate_llm_key)
    websocket_api.async_register_command(hass, _handle_websocket_exchange_connect_code)
    websocket_api.async_register_command(hass, _handle_websocket_unlink_connect)
    websocket_api.async_register_command(hass, _handle_websocket_exchange_aigateway_code)
    websocket_api.async_register_command(hass, _handle_websocket_unlink_aigateway)
