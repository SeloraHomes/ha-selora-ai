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

import asyncio
from collections.abc import Callable
import logging
from typing import TYPE_CHECKING, Any
import uuid

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
import voluptuous as vol

if TYPE_CHECKING:
    from .device_manager import DeviceManager

from .const import (
    CONF_ANTHROPIC_API_KEY,
    CONF_ANTHROPIC_MODEL,
    CONF_ENTRY_TYPE,
    CONF_GEMINI_API_KEY,
    CONF_GEMINI_MODEL,
    CONF_LLM_PROVIDER,
    CONF_OLLAMA_HOST,
    CONF_OLLAMA_MODEL,
    CONF_OPENAI_API_KEY,
    CONF_OPENAI_MODEL,
    CONF_OPENROUTER_API_KEY,
    CONF_OPENROUTER_MODEL,
    CONF_SELECTED_DEVICES,
    CONF_SELORA_LOCAL_HOST,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_SELORA_LOCAL_HOST,
    DOMAIN,
    ENTRY_TYPE_DEVICE,
    ENTRY_TYPE_LLM,
    LLM_PROVIDER_ANTHROPIC,
    LLM_PROVIDER_GEMINI,
    LLM_PROVIDER_NONE,
    LLM_PROVIDER_OLLAMA,
    LLM_PROVIDER_OPENAI,
    LLM_PROVIDER_OPENROUTER,
    LLM_PROVIDER_SELORA_CLOUD,
    LLM_PROVIDER_SELORA_LOCAL,
)

_LOGGER = logging.getLogger(__name__)

# Total wall-time budget for accepting all selected device flows
# concurrently.  Individual flows that finish within this window are
# processed normally; those still running are reported as in-progress
# and left for the user to complete via Settings > Devices.
_DEVICE_ACCEPT_TIMEOUT = 90


# ── Deferred area assignment for timed-out flows ─────────────────────


def _make_deferred_area_callback(
    hass: HomeAssistant,
    flow_id: str,
    display_name: str,
    area_id: str | None,
) -> Callable[[asyncio.Task[dict[str, Any]]], None]:
    """Return a task done-callback that assigns *area_id* when the flow completes.

    Also drains the task exception (if any) so asyncio does not log
    "Task exception was never retrieved".
    """

    def _on_done(task: asyncio.Task[dict[str, Any]]) -> None:
        if task.cancelled():
            _LOGGER.debug(
                "Deferred flow %s (%s) was cancelled (likely HA shutdown)",
                display_name,
                flow_id,
            )
            return

        exc = task.exception()
        if exc is not None:
            _LOGGER.warning(
                "Deferred flow %s (%s) failed: %s",
                display_name,
                flow_id,
                exc,
            )
            return

        result = task.result()
        if result.get("type") != "create_entry":
            _LOGGER.info(
                "Deferred flow %s (%s) did not create an entry (type=%s)",
                display_name,
                flow_id,
                result.get("type", ""),
            )
            return

        entry_id = result.get("entry_id")
        if not entry_id or not area_id:
            return

        # HA may already be stopping by the time a slow flow finishes;
        # touching the device registry then races teardown.
        if not hass.is_running:
            return

        from homeassistant.helpers import device_registry as dr

        try:
            dev_reg = dr.async_get(hass)
            for device in dr.async_entries_for_config_entry(dev_reg, entry_id):
                dev_reg.async_update_device(device.id, area_id=area_id)
            _LOGGER.info(
                "Deferred area assignment: %s → area %s",
                display_name,
                area_id,
            )
        except (KeyError, ValueError) as err:
            _LOGGER.warning(
                "Deferred area assignment failed for %s: %s",
                display_name,
                err,
            )

    return _on_done


# ── Validation helpers ────────────────────────────────────────────────


async def _validate_anthropic(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Validate the Anthropic API key works."""
    from .providers import create_provider

    provider = create_provider(
        LLM_PROVIDER_ANTHROPIC,
        hass,
        api_key=data[CONF_ANTHROPIC_API_KEY],
        model=data.get(CONF_ANTHROPIC_MODEL, DEFAULT_ANTHROPIC_MODEL),
    )
    if not await provider.health_check():
        raise ConnectionError("Anthropic API key invalid or unreachable")
    model = data.get(CONF_ANTHROPIC_MODEL, DEFAULT_ANTHROPIC_MODEL)
    return {"title": f"Selora AI (Claude — {model})"}


async def _validate_ollama(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Validate that Ollama is reachable and the model is available."""
    from .providers import create_provider

    provider = create_provider(
        LLM_PROVIDER_OLLAMA,
        hass,
        host=data.get(CONF_OLLAMA_HOST, DEFAULT_OLLAMA_HOST),
        model=data.get(CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL),
    )
    if not await provider.health_check():
        raise ConnectionError("Ollama not reachable or model not found")
    model = data.get(CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL)
    return {"title": f"Selora AI (Ollama — {model})"}


async def _validate_gemini(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Validate that the Gemini API key works."""
    from .providers import create_provider

    provider = create_provider(
        LLM_PROVIDER_GEMINI,
        hass,
        api_key=data[CONF_GEMINI_API_KEY],
        model=data.get(CONF_GEMINI_MODEL, DEFAULT_GEMINI_MODEL),
    )
    if not await provider.health_check():
        raise ConnectionError("Gemini API key invalid or unreachable")
    model = data.get(CONF_GEMINI_MODEL, DEFAULT_GEMINI_MODEL)
    return {"title": f"Selora AI (Gemini — {model})"}


async def _validate_openai(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Validate that the OpenAI API key works."""
    from .providers import create_provider

    provider = create_provider(
        LLM_PROVIDER_OPENAI,
        hass,
        api_key=data[CONF_OPENAI_API_KEY],
        model=data.get(CONF_OPENAI_MODEL, DEFAULT_OPENAI_MODEL),
    )
    if not await provider.health_check():
        raise ConnectionError("OpenAI API key invalid or unreachable")
    model = data.get(CONF_OPENAI_MODEL, DEFAULT_OPENAI_MODEL)
    return {"title": f"Selora AI (OpenAI — {model})"}


async def _validate_selora_local(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Validate that the Selora AI Local add-on is reachable."""
    from .providers import create_provider

    provider = create_provider(
        LLM_PROVIDER_SELORA_LOCAL,
        hass,
        host=data.get(CONF_SELORA_LOCAL_HOST, DEFAULT_SELORA_LOCAL_HOST),
    )
    if not await provider.health_check():
        raise ConnectionError("Selora AI Local add-on not reachable")
    return {"title": "Selora AI (Local)"}


async def _validate_openrouter(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Validate that the OpenRouter API key works."""
    from .providers import create_provider

    provider = create_provider(
        LLM_PROVIDER_OPENROUTER,
        hass,
        api_key=data[CONF_OPENROUTER_API_KEY],
        model=data.get(CONF_OPENROUTER_MODEL, DEFAULT_OPENROUTER_MODEL),
    )
    if not await provider.health_check():
        raise ConnectionError("OpenRouter API key invalid or unreachable")
    model = data.get(CONF_OPENROUTER_MODEL, DEFAULT_OPENROUTER_MODEL)
    return {"title": f"Selora AI (OpenRouter — {model})"}


class SeloraAiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Selora AI.

    Initial setup: LLM config → device discovery → selection → results
    Add Entry:     device discovery → selection → results
    """

    VERSION = 2

    # No async_get_options_flow: background-services settings live in the
    # custom Selora AI panel (Settings tab → Background Services) so the
    # styling matches the rest of the integration. Removing this method
    # hides the "Configure" gear in HA's Devices & Services UI, which
    # otherwise rendered an unstyled modal duplicating the panel controls.

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

    def _is_developer_mode(self) -> bool:
        """Check if developer mode is enabled on the existing LLM entry."""
        for entry in self._async_current_entries():
            if entry.data.get("developer_mode", False):
                return True
        return False

    def _has_llm_entry(self) -> bool:
        """Check if an LLM config entry already exists."""
        for entry in self._async_current_entries():
            if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_LLM:
                return True
            # Backward compat: old entries without entry_type that have llm_provider
            if CONF_LLM_PROVIDER in entry.data and CONF_ENTRY_TYPE not in entry.data:
                return True
        return False

    def _get_device_manager(self) -> DeviceManager | None:
        """Retrieve the DeviceManager from the running LLM entry."""
        for entry_data in self.hass.data.get(DOMAIN, {}).values():
            if isinstance(entry_data, dict) and "device_manager" in entry_data:
                return entry_data["device_manager"]
        return None

    def _get_or_create_device_manager(self) -> DeviceManager | None:
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
    ) -> config_entries.ConfigFlowResult:
        """Entry point — choose LLM provider or route to device discovery."""
        if self._has_llm_entry():
            # Add Entry mode: skip LLM config, go straight to discovery
            return await self.async_step_discover()

        # Initial setup: Choose LLM provider
        if user_input is not None:
            self._provider = user_input[CONF_LLM_PROVIDER]
            if self._provider == LLM_PROVIDER_SELORA_CLOUD:
                return await self.async_step_selora_cloud()
            if self._provider == LLM_PROVIDER_ANTHROPIC:
                return await self.async_step_anthropic()
            if self._provider == LLM_PROVIDER_GEMINI:
                return await self.async_step_gemini()
            if self._provider == LLM_PROVIDER_OPENAI:
                return await self.async_step_openai()
            if self._provider == LLM_PROVIDER_OPENROUTER:
                return await self.async_step_openrouter()
            if self._provider == LLM_PROVIDER_OLLAMA:
                return await self.async_step_ollama()
            if self._provider == LLM_PROVIDER_SELORA_LOCAL:
                return await self.async_step_selora_local()

            # Skip for now
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="Selora AI (Unconfigured)", data={CONF_LLM_PROVIDER: LLM_PROVIDER_NONE}
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
                            LLM_PROVIDER_SELORA_CLOUD: "Selora AI Cloud — Recommended",
                            LLM_PROVIDER_ANTHROPIC: "Anthropic (Claude)",
                            LLM_PROVIDER_GEMINI: "Google Gemini",
                            LLM_PROVIDER_OPENAI: "OpenAI",
                            LLM_PROVIDER_OPENROUTER: "OpenRouter (Multi-Model Aggregator)",
                            LLM_PROVIDER_OLLAMA: "Ollama (Local LLM)",
                            LLM_PROVIDER_SELORA_LOCAL: "Selora AI Local (On-device)",
                            LLM_PROVIDER_NONE: "Skip for now (Configure later)",
                        }
                    ),
                }
            ),
        )

    # ── LLM Configuration (store config, chain to discovery) ──────────

    async def async_step_anthropic(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
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
                # Chain to Selora Connect linking (optional), then discovery
                return await self.async_step_selora_connect()

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

    async def async_step_gemini(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Configure Google Gemini API key, then chain to discovery."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await _validate_gemini(self.hass, user_input)
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Failed to validate Gemini API key")
                errors["base"] = "unknown"
            else:
                self._llm_data = {
                    CONF_ENTRY_TYPE: ENTRY_TYPE_LLM,
                    CONF_LLM_PROVIDER: LLM_PROVIDER_GEMINI,
                    **user_input,
                    "_title": info["title"],
                }
                return await self.async_step_selora_connect()

        return self.async_show_form(
            step_id="gemini",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_GEMINI_API_KEY): str,
                    vol.Required(
                        CONF_GEMINI_MODEL,
                        default=DEFAULT_GEMINI_MODEL,
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_openai(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
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
                return await self.async_step_selora_connect()

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

    async def async_step_openrouter(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Configure OpenRouter API key, then chain to discovery."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await _validate_openrouter(self.hass, user_input)
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Failed to validate OpenRouter API key")
                errors["base"] = "unknown"
            else:
                self._llm_data = {
                    CONF_ENTRY_TYPE: ENTRY_TYPE_LLM,
                    CONF_LLM_PROVIDER: LLM_PROVIDER_OPENROUTER,
                    **user_input,
                    "_title": info["title"],
                }
                return await self.async_step_selora_connect()

        return self.async_show_form(
            step_id="openrouter",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_OPENROUTER_API_KEY): str,
                    vol.Required(
                        CONF_OPENROUTER_MODEL,
                        default=DEFAULT_OPENROUTER_MODEL,
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_selora_local(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Configure the Selora AI Local add-on, then chain to discovery."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await _validate_selora_local(self.hass, user_input)
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Selora Local validation")
                errors["base"] = "unknown"
            else:
                self._llm_data = {
                    CONF_ENTRY_TYPE: ENTRY_TYPE_LLM,
                    CONF_LLM_PROVIDER: LLM_PROVIDER_SELORA_LOCAL,
                    **user_input,
                    "_title": info["title"],
                }
                return await self.async_step_selora_connect()

        return self.async_show_form(
            step_id="selora_local",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SELORA_LOCAL_HOST,
                        default=DEFAULT_SELORA_LOCAL_HOST,
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_ollama(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
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
                # Chain to Selora Connect linking (optional), then discovery
                return await self.async_step_selora_connect()

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

    # ── Selora AI Cloud (OAuth, no credentials at config-flow time) ────
    async def async_step_selora_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm Selora Cloud selection.

        OAuth linking happens post-setup from the panel — there are no
        credentials to enter here. This step just records the chosen
        provider so the panel opens in the linked-state UI on first load.
        """
        if user_input is not None:
            self._llm_data = {
                CONF_ENTRY_TYPE: ENTRY_TYPE_LLM,
                CONF_LLM_PROVIDER: LLM_PROVIDER_SELORA_CLOUD,
                "_title": "Selora AI (Selora Cloud)",
            }
            return await self.async_step_selora_connect()

        return self.async_show_form(
            step_id="selora_cloud",
            data_schema=vol.Schema({}),
        )

    # ── Selora Connect ──────────────────────────────────────────────────
    # Connect linking is handled post-setup via the Selora AI panel's
    # OAuth flow (Settings → Remote Access & MCP Authentication).
    # This step just chains through to device discovery.

    async def async_step_selora_connect(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Skip to device discovery — Connect linking is done via the panel."""
        return await self.async_step_discover()

    # ── Device Discovery & Onboarding ─────────────────────────────────

    def _build_device_label(self, dev: dict[str, Any]) -> str:
        """Build a human-readable label for a discovered device."""
        known = dev.get("known", {})
        name = known.get("name", dev.get("handler", ""))
        ctx = dev.get("context", {})
        title_name = ctx.get("title_placeholders", {}).get("name", "")
        unique_id = ctx.get("unique_id", "")
        flow_id = dev.get("flow_id", "")

        if title_name:
            return f"{title_name} ({name})"
        if unique_id:
            return f"{name} — {unique_id}"
        # Use short flow_id suffix to disambiguate multiple same-type devices
        if flow_id:
            return f"{name} — {flow_id[:4].upper()}"
        return name

    async def async_step_discover(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Run discovery, show per-device area dropdowns."""
        dm = self._get_or_create_device_manager()
        if dm is None:
            return self.async_abort(reason="llm_not_ready")

        # Run discovery, filter out our own domain and system integrations
        from .const import PROTECTED_DOMAINS

        result = await dm.discover_network_devices()
        self._discovered_devices = [
            d for d in result.get("discovered", []) if d.get("handler", "") not in PROTECTED_DOMAINS
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
            area_options.append(SelectOptionDict(value=area.id, label=area.name))

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
        device_list = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(device_lines))

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
    ) -> config_entries.ConfigFlowResult:
        """User submitted area assignments — orchestrate device setup.

        Critical invariant: this step must always make forward progress.
        If the device-accept orchestration explodes, we still want the
        user to land on a results page (or, in initial setup, get an
        LLM entry created) — otherwise they get trapped re-running the
        same flow and hitting the same exception every time, with no
        Selora AI entry to fall back to.
        """
        if user_input is None:
            return await self.async_step_discover()

        try:
            return await self._handle_select_devices(user_input)
        except Exception:
            _LOGGER.exception("async_step_select_devices crashed — degrading to results")
            return await self._fallback_after_select_devices_failure()

    async def _handle_select_devices(
        self, user_input: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        dm = self._get_or_create_device_manager()
        if dm is None:
            return self.async_abort(reason="llm_not_ready")

        # Parse selections: device_0 -> area_id, device_1 -> "skip", etc.
        self._setup_results = []
        selected: list[tuple[dict[str, Any], str]] = []

        for i, dev in enumerate(self._discovered_devices):
            area_choice = user_input.get(f"device_{i}", "skip")
            if area_choice != "skip":
                selected.append((dev, area_choice))

        if not selected:
            if self._llm_data:
                # Initial setup with no devices selected — still create LLM entry
                return await self._create_llm_entry()
            return self.async_abort(reason="no_devices_selected")

        await self._accept_selected_flows(dm, selected)
        return await self.async_step_results()

    async def _fallback_after_select_devices_failure(
        self,
    ) -> config_entries.ConfigFlowResult:
        """Recover from an unexpected crash inside the device-accept path.

        On initial setup we'd rather create an LLM-only entry (devices
        can be onboarded later via "Add Entry") than leave the user with
        no entry at all. On Add-Entry mode, abort cleanly so they aren't
        trapped — they can retry without a half-baked record entry.
        """
        if self._llm_data:
            try:
                return await self._create_llm_entry()
            except Exception:
                _LOGGER.exception("LLM-entry fallback also failed")
        return self.async_abort(reason="select_devices_failed")

    async def _accept_selected_flows(
        self,
        dm: DeviceManager,
        selected: list[tuple[dict[str, Any], str]],
    ) -> None:
        """Accept selected device flows concurrently with a wall-time budget.

        Tasks that don't finish within the wall-time budget are left running
        in the background — they're not cancelled, and a done-callback
        assigns the user-chosen area when they eventually complete.
        """
        tasks = {
            self.hass.async_create_task(
                dm.accept_flow(dev["flow_id"]),
                name=f"selora_ai_accept_{dev['flow_id']}",
            ): (dev, area_choice)
            for dev, area_choice in selected
        }

        done, pending = await asyncio.wait(tasks, timeout=_DEVICE_ACCEPT_TIMEOUT)

        for task in done:
            dev, area_choice = tasks[task]
            flow_id = dev["flow_id"]
            display_name = self._build_device_label(dev)
            area_id = area_choice if area_choice != "no_area" else None

            exc = task.exception()
            if exc is not None:
                _LOGGER.error(
                    "Setup failed for %s (%s): %s",
                    display_name,
                    flow_id,
                    exc,
                    exc_info=exc,
                )
                self._setup_results.append(
                    {
                        "flow_id": flow_id,
                        "name": display_name,
                        "status": "error",
                        "message": str(exc) or exc.__class__.__name__,
                    }
                )
                continue

            result = task.result()
            self._setup_results.append(
                self._build_setup_result(flow_id, display_name, area_id, result)
            )
            if result.get("type") == "create_entry" and area_id and result.get("entry_id"):
                await self._assign_area_to_entry(result["entry_id"], area_id)

        for task in pending:
            dev, area_choice = tasks[task]
            flow_id = dev["flow_id"]
            display_name = self._build_device_label(dev)
            area_id = area_choice if area_choice != "no_area" else None

            task.add_done_callback(
                _make_deferred_area_callback(self.hass, flow_id, display_name, area_id)
            )

            _LOGGER.info(
                "Flow %s (%s) still in progress after %ds — "
                "area will be assigned when the flow completes",
                display_name,
                flow_id,
                _DEVICE_ACCEPT_TIMEOUT,
            )
            self._setup_results.append(
                {
                    "flow_id": flow_id,
                    "name": display_name,
                    "status": "needs_attention",
                    "area_id": area_id,
                    "message": "Still in progress — finish setup via Settings > Devices",
                }
            )

    @staticmethod
    def _build_setup_result(
        flow_id: str,
        display_name: str,
        area_id: str | None,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Turn a normalised flow result into a setup-result dict."""
        result_type = result.get("type", "")

        if result_type == "create_entry":
            return {
                "flow_id": flow_id,
                "name": display_name,
                "status": "success",
                "title": result.get("title", display_name),
                "area_id": area_id,
            }
        if result_type == "form":
            return {
                "flow_id": result.get("flow_id", flow_id),
                "name": display_name,
                "status": "needs_attention",
                "step_id": result.get("step_id", ""),
                "message": (f"Requires manual setup: step '{result.get('step_id', 'unknown')}'"),
            }
        if result.get("errors"):
            return {
                "flow_id": flow_id,
                "name": display_name,
                "status": "error",
                "message": str(result["errors"]),
            }
        return {
            "flow_id": result.get("flow_id", flow_id),
            "name": display_name,
            "status": "needs_attention",
            "step_id": result.get("step_id", ""),
            "message": (f"Requires manual setup: step '{result.get('step_id', 'unknown')}'"),
        }

    async def _assign_area_to_entry(self, entry_id: str, area_id: str) -> None:
        """Assign all devices from a config entry to an area."""
        from homeassistant.helpers import device_registry as dr

        try:
            dev_reg = dr.async_get(self.hass)
            for device in dr.async_entries_for_config_entry(dev_reg, entry_id):
                dev_reg.async_update_device(device.id, area_id=area_id)
        except (KeyError, ValueError) as exc:
            _LOGGER.warning("Could not assign area %s to entry %s: %s", area_id, entry_id, exc)

    async def _create_llm_entry(self) -> config_entries.ConfigFlowResult:
        """Create the LLM config entry (initial setup, no devices found or selected)."""
        await self.async_set_unique_id(f"{DOMAIN}_llm")
        self._abort_if_unique_id_configured()

        title = self._llm_data.pop("_title", "Selora AI")
        return self.async_create_entry(title=title, data=self._llm_data)

    async def async_step_results(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
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


# Background-services options previously rendered by SeloraAiOptionsFlowHandler
# now live in the custom panel's Settings tab. The class was removed alongside
# async_get_options_flow on SeloraAiConfigFlow so HA stops surfacing a
# duplicated, unstyled modal in Devices & Services. update_config /
# get_config WS commands handle the read/write path.
