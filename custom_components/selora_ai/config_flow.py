"""Config flow for Selora AI integration.

Two-step flow:
  1. Choose LLM provider (Anthropic API or local Ollama)
  2. Configure provider-specific settings (API key / host + model)
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ANTHROPIC_API_KEY,
    CONF_ANTHROPIC_MODEL,
    CONF_LLM_PROVIDER,
    CONF_OLLAMA_HOST,
    CONF_OLLAMA_MODEL,
    DEFAULT_ANTHROPIC_API_KEY,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    DOMAIN,
    LLM_PROVIDER_ANTHROPIC,
    LLM_PROVIDER_OLLAMA,
)
from .llm_client import LLMClient

_LOGGER = logging.getLogger(__name__)


async def _validate_anthropic(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Validate the Anthropic API key works."""
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


class SeloraAIConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Selora AI.

    Settings > Devices & Services > Add Integration > Selora AI

    Step 1: Choose provider (Anthropic or Ollama)
    Step 2: Provider-specific config (API key or host/model)
    """

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._provider: str = DEFAULT_LLM_PROVIDER

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1 — choose LLM provider."""
        if user_input is not None:
            self._provider = user_input[CONF_LLM_PROVIDER]
            if self._provider == LLM_PROVIDER_ANTHROPIC:
                return await self.async_step_anthropic()
            return await self.async_step_ollama()

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
                            LLM_PROVIDER_OLLAMA: "Ollama (Local LLM)",
                        }
                    ),
                }
            ),
        )

    async def async_step_anthropic(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2a — configure Anthropic API key and model."""
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()

            try:
                info = await _validate_anthropic(self.hass, user_input)
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Anthropic validation")
                errors["base"] = "unknown"
            else:
                data = {CONF_LLM_PROVIDER: LLM_PROVIDER_ANTHROPIC, **user_input}
                return self.async_create_entry(title=info["title"], data=data)

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

    async def async_step_ollama(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2b — configure local Ollama."""
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()

            try:
                info = await _validate_ollama(self.hass, user_input)
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Ollama validation")
                errors["base"] = "unknown"
            else:
                data = {CONF_LLM_PROVIDER: LLM_PROVIDER_OLLAMA, **user_input}
                return self.async_create_entry(title=info["title"], data=data)

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
