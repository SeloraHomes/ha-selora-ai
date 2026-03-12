"""Config flow for Selora AI integration.

Single continuous flow:
  1. Choose LLM provider (Anthropic / Ollama)
  2. Configure LLM credentials
  3. Discover devices on the network
  4. Select devices + assign areas
  5. Show results → create entry

"Add Entry" later runs steps 3-5 only (device onboarding).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_ANTHROPIC_API_KEY,
    CONF_ANTHROPIC_MODEL,
    CONF_ENTRY_TYPE,
    CONF_LLM_PROVIDER,
    CONF_OLLAMA_HOST,
    CONF_OLLAMA_MODEL,
    CONF_OPENAI_API_KEY,
    CONF_OPENAI_MODEL,
    CONF_SELECTED_DEVICES,
    CONF_COLLECTOR_ENABLED,
    CONF_COLLECTOR_MODE,
    CONF_COLLECTOR_START_TIME,
    CONF_COLLECTOR_END_TIME,
    CONF_COLLECTOR_INTERVAL,
    CONF_DISCOVERY_ENABLED,
    CONF_DISCOVERY_MODE,
    CONF_DISCOVERY_START_TIME,
    CONF_DISCOVERY_END_TIME,
    CONF_DISCOVERY_INTERVAL,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_COLLECTOR_ENABLED,
    DEFAULT_COLLECTOR_MODE,
    DEFAULT_COLLECTOR_INTERVAL,
    DEFAULT_COLLECTOR_START_TIME,
    DEFAULT_COLLECTOR_END_TIME,
    DEFAULT_DISCOVERY_ENABLED,
    DEFAULT_DISCOVERY_MODE,
    DEFAULT_DISCOVERY_INTERVAL,
    DEFAULT_DISCOVERY_START_TIME,
    DEFAULT_DISCOVERY_END_TIME,
    DEFAULT_OPENAI_MODEL,
    DOMAIN,
    ENTRY_TYPE_DEVICE,
    ENTRY_TYPE_LLM,
    LLM_PROVIDER_ANTHROPIC,
    LLM_PROVIDER_OLLAMA,
    LLM_PROVIDER_OPENAI,
    LLM_PROVIDER_NONE,
    MODE_CONTINUOUS,
    MODE_SCHEDULED,
)

_LOGGER = logging.getLogger(__name__)


# ── Validation helpers ────────────────────────────────────────────────


async def _validate_anthropic(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Validate the Anthropic API key works."""
    from .llm_client import LLMClient
    client = LLMClient(
        hass,
        provider=LLM_PROVIDER_ANTHROPIC,
        api_key=data[CONF_ANTHROPIC_API_KEY],
        model=data.get(CONF_ANTHROPIC_MODEL, DEFAULT_ANTHROPIC_MODEL),
    )
    if not await client.health_check():
        raise ConnectionError("Anthropic API key invalid or unreachable")
    model = data.get(CONF_ANTHROPIC_MODEL, DEFAULT_ANTHROPIC_MODEL)
    return {"title": f"Selora AI (Claude — {model})"}


async def _validate_ollama(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Validate that Ollama is reachable and the model is available."""
    from .llm_client import LLMClient
    client = LLMClient(
        hass,
        provider=LLM_PROVIDER_OLLAMA,
        host=data.get(CONF_OLLAMA_HOST, DEFAULT_OLLAMA_HOST),
        model=data.get(CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL),
    )
    if not await client.health_check():
        raise ConnectionError("Ollama not reachable or model not found")
    model = data.get(CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL)
    return {"title": f"Selora AI (Ollama — {model})"}


async def _validate_openai(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Validate that the OpenAI API key works."""
    from .llm_client import LLMClient
    client = LLMClient(
        hass,
        provider=LLM_PROVIDER_OPENAI,
        api_key=data[CONF_OPENAI_API_KEY],
        model=data.get(CONF_OPENAI_MODEL, DEFAULT_OPENAI_MODEL),
    )
    if not await client.health_check():
        raise ConnectionError("OpenAI API key invalid or unreachable")
    model = data.get(CONF_OPENAI_MODEL, DEFAULT_OPENAI_MODEL)
    return {"title": f"Selora AI (OpenAI — {model})"}


class SeloraAiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Selora AI.

    Initial setup: LLM config → device discovery → selection → results
    Add Entry:     device discovery → selection → results
    """

    VERSION = 1

    def async_get_options_flow(self, config_entry: config_entries.ConfigEntry) -> SeloraAiOptionsFlowHandler:
        """Get the options flow for this handler."""
        return SeloraAiOptionsFlowHandler(config_entry)

    def __init__(self) -> None:
        """Initialize the config flow."""
        super().__init__()
        self._provider: str = DEFAULT_LLM_PROVIDER
        # LLM config stored between steps (initial setup only)
        self._llm_data: dict[str, Any] | None = None
        # Device discovery state
        self._discovered_devices: list[dict[str, Any]] = []
        self._setup_results: list[dict[str, Any]] = []

    # ── Helpers ────────────────────────────────────────────────────────

    def _has_llm_entry(self) -> bool:
        """Check if an LLM config entry already exists."""
        for entry in self._async_current_entries():
            if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_LLM:
                return True
            # Backward compat: old entries without entry_type that have llm_provider
            if CONF_LLM_PROVIDER in entry.data and CONF_ENTRY_TYPE not in entry.data:
                return True
        return False

    def _get_device_manager(self):
        """Retrieve the DeviceManager from the running LLM entry."""
        for entry_data in self.hass.data.get(DOMAIN, {}).values():
            if isinstance(entry_data, dict) and "device_manager" in entry_data:
                return entry_data["device_manager"]
        return None

    def _get_or_create_device_manager(self):
        """Get existing DeviceManager or create a temporary one for initial setup."""
        dm = self._get_device_manager()
        if dm is not None:
            return dm
        # During initial setup, no LLM entry exists yet — create a temp DeviceManager
        if self._llm_data:
            from .device_manager import DeviceManager
            return DeviceManager(
                self.hass,
                api_key=self._llm_data.get(CONF_ANTHROPIC_API_KEY, ""),
                model=self._llm_data.get(CONF_ANTHROPIC_MODEL, DEFAULT_ANTHROPIC_MODEL),
            )
        return None

    # ── Entry Point ───────────────────────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Entry point — choose LLM provider or route to device discovery."""
        if self._has_llm_entry():
            # Add Entry mode: skip LLM config, go straight to discovery
            return await self.async_step_discover()

        # Initial setup: Choose LLM provider
        if user_input is not None:
            self._provider = user_input[CONF_LLM_PROVIDER]
            if self._provider == LLM_PROVIDER_ANTHROPIC:
                return await self.async_step_anthropic()
            if self._provider == LLM_PROVIDER_OPENAI:
                return await self.async_step_openai()
            if self._provider == LLM_PROVIDER_OLLAMA:
                return await self.async_step_ollama()

            # Skip for now
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="Selora AI (Unconfigured)", 
                data={CONF_LLM_PROVIDER: LLM_PROVIDER_NONE}
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_LLM_PROVIDER,
                        default=DEFAULT_LLM_PROVIDER,
                    ): vol.In(
                        {
                            LLM_PROVIDER_ANTHROPIC: "Anthropic (Claude) — Recommended",
                            LLM_PROVIDER_OPENAI: "OpenAI",
                            LLM_PROVIDER_OLLAMA: "Ollama (Local LLM)",
                            LLM_PROVIDER_NONE: "Skip for now (Configure later)",
                        }
                    ),
                }
            ),
        )

    # ── LLM Configuration (store config, chain to discovery) ──────────

    async def async_step_anthropic(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure Anthropic API key, then chain to discovery."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await _validate_anthropic(self.hass, user_input)
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Failed to validate Anthropic API key")
                errors["base"] = "unknown"
            else:
                # Store LLM config — entry will be created at the end of the flow
                self._llm_data = {
                    CONF_ENTRY_TYPE: ENTRY_TYPE_LLM,
                    CONF_LLM_PROVIDER: LLM_PROVIDER_ANTHROPIC,
                    **user_input,
                    "_title": info["title"],
                }
                # Chain to device discovery
                return await self.async_step_discover()

        return self.async_show_form(
            step_id="anthropic",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ANTHROPIC_API_KEY): str,
                    vol.Required(
                        CONF_ANTHROPIC_MODEL,
                        default=DEFAULT_ANTHROPIC_MODEL,
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_openai(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure OpenAI API key, then chain to discovery."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await _validate_openai(self.hass, user_input)
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Failed to validate OpenAI API key")
                errors["base"] = "unknown"
            else:
                self._llm_data = {
                    CONF_ENTRY_TYPE: ENTRY_TYPE_LLM,
                    CONF_LLM_PROVIDER: LLM_PROVIDER_OPENAI,
                    **user_input,
                    "_title": info["title"],
                }
                return await self.async_step_discover()

        return self.async_show_form(
            step_id="openai",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_OPENAI_API_KEY): str,
                    vol.Required(
                        CONF_OPENAI_MODEL,
                        default=DEFAULT_OPENAI_MODEL,
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_ollama(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure local Ollama, then chain to discovery."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await _validate_ollama(self.hass, user_input)
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Ollama validation")
                errors["base"] = "unknown"
            else:
                # Store LLM config — entry will be created at the end
                self._llm_data = {
                    CONF_ENTRY_TYPE: ENTRY_TYPE_LLM,
                    CONF_LLM_PROVIDER: LLM_PROVIDER_OLLAMA,
                    **user_input,
                    "_title": info["title"],
                }
                # Chain to device discovery
                return await self.async_step_discover()

        return self.async_show_form(
            step_id="ollama",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_OLLAMA_HOST,
                        default=DEFAULT_OLLAMA_HOST,
                    ): str,
                    vol.Required(
                        CONF_OLLAMA_MODEL,
                        default=DEFAULT_OLLAMA_MODEL,
                    ): str,
                }
            ),
            errors=errors,
        )

    # ── Device Discovery & Onboarding ─────────────────────────────────

    def _build_device_label(self, dev: dict[str, Any]) -> str:
        """Build a human-readable label for a discovered device."""
        handler = dev.get("handler", "unknown")
        known = dev.get("known", {})
        name = known.get("name", handler)
        ctx = dev.get("context", {})
        title_name = ctx.get("title_placeholders", {}).get("name", "")
        unique_id = ctx.get("unique_id", "")

        # Try to get IP from the flow handler
        host = ""
        try:
            flow_id = dev["flow_id"]
            flow_obj = self.hass.config_entries.flow._progress.get(flow_id)
            if flow_obj and hasattr(flow_obj, "host"):
                host = flow_obj.host or ""
        except Exception:
            pass

        if title_name:
            return f"{title_name} ({name})"
        if host:
            return f"{name} — {host}"
        if unique_id:
            return f"{name} — {unique_id}"
        return name

    async def async_step_discover(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Run discovery, show per-device area dropdowns."""
        dm = self._get_or_create_device_manager()
        if dm is None:
            return self.async_abort(reason="llm_not_ready")

        # Run discovery, filter out our own domain and system integrations
        from .const import PROTECTED_DOMAINS
        result = await dm.discover_network_devices()
        self._discovered_devices = [
            d for d in result.get("discovered", [])
            if d.get("handler", "") not in PROTECTED_DOMAINS
        ]

        if not self._discovered_devices:
            if self._llm_data:
                # Initial setup with no devices — still create LLM entry
                return await self._create_llm_entry()
            return self.async_abort(reason="no_devices_found")

        # Get available areas from HA
        from homeassistant.helpers import area_registry as ar
        area_reg = ar.async_get(self.hass)
        areas = area_reg.async_list_areas()

        area_options: list[SelectOptionDict] = [
            SelectOptionDict(value="skip", label="— Skip —"),
            SelectOptionDict(value="no_area", label="Add (no area)"),
        ]
        for area in sorted(areas, key=lambda a: a.name):
            area_options.append(
                SelectOptionDict(value=area.id, label=area.name)
            )

        # Build one dropdown per discovered device
        schema_dict: dict = {}
        device_lines: list[str] = []
        for i, dev in enumerate(self._discovered_devices):
            label = self._build_device_label(dev)
            field_key = f"device_{i}"
            schema_dict[vol.Optional(field_key, default="skip")] = SelectSelector(
                SelectSelectorConfig(
                    options=area_options,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            device_lines.append(f"**{label}**")

        # Include device names in description so user knows what device_N is
        device_list = "\n".join(
            f"{i + 1}. {line}" for i, line in enumerate(device_lines)
        )

        return self.async_show_form(
            step_id="select_devices",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "count": str(len(self._discovered_devices)),
                "device_list": device_list,
            },
        )

    async def async_step_select_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """User submitted area assignments — orchestrate device setup."""
        if user_input is None:
            return await self.async_step_discover()

        dm = self._get_or_create_device_manager()
        if dm is None:
            return self.async_abort(reason="llm_not_ready")

        # Parse selections: device_0 -> area_id, device_1 -> "skip", etc.
        self._setup_results = []
        any_selected = False

        for i, dev in enumerate(self._discovered_devices):
            area_choice = user_input.get(f"device_{i}", "skip")
            if area_choice == "skip":
                continue

            any_selected = True
            flow_id = dev["flow_id"]
            handler = dev.get("handler", "")
            display_name = self._build_device_label(dev)
            area_id = area_choice if area_choice != "no_area" else None

            try:
                result = await dm.accept_flow(flow_id)

                if result.get("type") == "create_entry":
                    # Assign area to the newly created device
                    if area_id:
                        entry_id = result.get("entry_id")
                        if entry_id:
                            await self._assign_area_to_entry(entry_id, area_id)

                    self._setup_results.append({
                        "flow_id": flow_id,
                        "name": display_name,
                        "status": "success",
                        "title": result.get("title", display_name),
                        "area_id": area_id,
                    })
                elif result.get("error"):
                    self._setup_results.append({
                        "flow_id": flow_id,
                        "name": display_name,
                        "status": "error",
                        "message": result["error"],
                    })
                else:
                    self._setup_results.append({
                        "flow_id": result.get("flow_id", flow_id),
                        "name": display_name,
                        "status": "needs_attention",
                        "step_id": result.get("step_id", ""),
                        "message": f"Requires manual setup: step '{result.get('step_id', 'unknown')}'",
                    })
            except Exception as exc:
                _LOGGER.error("Setup failed for %s (%s): %s", display_name, flow_id, exc)
                self._setup_results.append({
                    "flow_id": flow_id,
                    "name": display_name,
                    "status": "error",
                    "message": str(exc),
                })

        if not any_selected:
            if self._llm_data:
                # Initial setup with no devices selected — still create LLM entry
                return await self._create_llm_entry()
            return self.async_abort(reason="no_devices_selected")

        # Post-setup: generate dashboard
        try:
            await dm.generate_dashboard()
        except Exception:
            _LOGGER.exception("Post-setup tasks failed")

        return await self.async_step_results()

    async def _assign_area_to_entry(self, entry_id: str, area_id: str) -> None:
        """Assign all devices from a config entry to an area."""
        from homeassistant.helpers import device_registry as dr
        dev_reg = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(dev_reg, entry_id):
            dev_reg.async_update_device(device.id, area_id=area_id)

    async def _create_llm_entry(self) -> FlowResult:
        """Create the LLM config entry (initial setup, no devices found or selected)."""
        await self.async_set_unique_id(f"{DOMAIN}_llm")
        self._abort_if_unique_id_configured()

        title = self._llm_data.pop("_title", "Selora AI")
        return self.async_create_entry(title=title, data=self._llm_data)

    async def async_step_results(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show setup results and create the appropriate config entry."""
        if user_input is not None:
            if self._llm_data:
                # Initial setup — create LLM entry (includes device results)
                await self.async_set_unique_id(f"{DOMAIN}_llm")
                self._abort_if_unique_id_configured()

                title = self._llm_data.pop("_title", "Selora AI")
                return self.async_create_entry(title=title, data=self._llm_data)
            else:
                # Add Entry mode — create device onboarding record
                succeeded = [r for r in self._setup_results if r["status"] == "success"]
                device_names = [r.get("title", r.get("name", "?")) for r in succeeded]

                title = (
                    f"Devices: {', '.join(device_names)}"
                    if device_names
                    else "Device Onboarding (no devices configured)"
                )

                unique_suffix = uuid.uuid4().hex[:8]
                await self.async_set_unique_id(f"{DOMAIN}_devices_{unique_suffix}")

                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_ENTRY_TYPE: ENTRY_TYPE_DEVICE,
                        CONF_SELECTED_DEVICES: [
                            {
                                "flow_id": r.get("flow_id"),
                                "name": r.get("name"),
                                "status": r["status"],
                                "title": r.get("title", ""),
                            }
                            for r in self._setup_results
                        ],
                    },
                )

        # Build summary for the results page
        succeeded = sum(1 for r in self._setup_results if r["status"] == "success")
        failed = sum(1 for r in self._setup_results if r["status"] == "error")
        needs_attention = sum(1 for r in self._setup_results if r["status"] == "needs_attention")

        details = "\n".join(
            f"- {r.get('name', '?')}: {r['status']}"
            + (f" ({r.get('message', '')})" if r.get("message") else "")
            for r in self._setup_results
        )

        return self.async_show_form(
            step_id="results",
            data_schema=vol.Schema({}),
            description_placeholders={
                "succeeded": str(succeeded),
                "failed": str(failed),
                "needs_attention": str(needs_attention),
                "details": details,
            },
        )


class SeloraAiOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Selora AI options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the background services options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    # Data Collector
                    vol.Required(
                        CONF_COLLECTOR_ENABLED,
                        default=options.get(CONF_COLLECTOR_ENABLED, DEFAULT_COLLECTOR_ENABLED),
                    ): bool,
                    vol.Required(
                        CONF_COLLECTOR_MODE,
                        default=options.get(CONF_COLLECTOR_MODE, DEFAULT_COLLECTOR_MODE),
                    ): vol.In(
                        {
                            MODE_CONTINUOUS: "Continuous",
                            MODE_SCHEDULED: "Scheduled Window",
                        }
                    ),
                    vol.Required(
                        CONF_COLLECTOR_INTERVAL,
                        default=options.get(CONF_COLLECTOR_INTERVAL, DEFAULT_COLLECTOR_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=60)),
                    vol.Optional(
                        CONF_COLLECTOR_START_TIME,
                        default=options.get(CONF_COLLECTOR_START_TIME, DEFAULT_COLLECTOR_START_TIME),
                    ): str,
                    vol.Optional(
                        CONF_COLLECTOR_END_TIME,
                        default=options.get(CONF_COLLECTOR_END_TIME, DEFAULT_COLLECTOR_END_TIME),
                    ): str,
                    
                    # Network Discovery
                    vol.Required(
                        CONF_DISCOVERY_ENABLED,
                        default=options.get(CONF_DISCOVERY_ENABLED, DEFAULT_DISCOVERY_ENABLED),
                    ): bool,
                    vol.Required(
                        CONF_DISCOVERY_MODE,
                        default=options.get(CONF_DISCOVERY_MODE, DEFAULT_DISCOVERY_MODE),
                    ): vol.In(
                        {
                            MODE_CONTINUOUS: "Continuous",
                            MODE_SCHEDULED: "Scheduled Window",
                        }
                    ),
                    vol.Required(
                        CONF_DISCOVERY_INTERVAL,
                        default=options.get(CONF_DISCOVERY_INTERVAL, DEFAULT_DISCOVERY_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=60)),
                    vol.Optional(
                        CONF_DISCOVERY_START_TIME,
                        default=options.get(CONF_DISCOVERY_START_TIME, DEFAULT_DISCOVERY_START_TIME),
                    ): str,
                    vol.Optional(
                        CONF_DISCOVERY_END_TIME,
                        default=options.get(CONF_DISCOVERY_END_TIME, DEFAULT_DISCOVERY_END_TIME),
                    ): str,
                }
            ),
        )
