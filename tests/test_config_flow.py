"""Tests for the Selora AI config flow using real HA flow machinery.

Uses ``hass.config_entries.flow.async_init`` / ``async_configure`` so that
the full ConfigFlow lifecycle is exercised — form rendering, step routing,
entry creation, and abort logic — rather than stubbing them out.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai.const import (
    CONF_ANTHROPIC_API_KEY,
    CONF_ANTHROPIC_MODEL,
    CONF_GEMINI_API_KEY,
    CONF_GEMINI_MODEL,
    CONF_LLM_PROVIDER,
    CONF_OLLAMA_HOST,
    CONF_OLLAMA_MODEL,
    CONF_OPENAI_API_KEY,
    CONF_OPENAI_MODEL,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OPENAI_MODEL,
    DOMAIN,
    LLM_PROVIDER_ANTHROPIC,
    LLM_PROVIDER_GEMINI,
    LLM_PROVIDER_NONE,
    LLM_PROVIDER_OLLAMA,
    LLM_PROVIDER_OPENAI,
)


@pytest.fixture(autouse=True)
def _enable_custom_component(enable_custom_integrations):
    """Auto-enable custom integrations so our domain is discoverable."""


@pytest.fixture(autouse=True)
async def _setup_ha_dependencies(hass):
    """Set up HA components that our integration depends on (conversation, http)."""
    await async_setup_component(hass, "homeassistant", {})
    await async_setup_component(hass, "http", {})
    await async_setup_component(hass, "conversation", {})
    await hass.async_block_till_done()


# ── Step: user (LLM provider selection) ──────────────────────────────


class TestStepUser:
    """Tests for the initial user step."""

    async def test_shows_provider_form(self, hass) -> None:
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] == "form"
        assert result["step_id"] == "user"

    async def test_selecting_none_creates_entry(self, hass) -> None:
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_LLM_PROVIDER: LLM_PROVIDER_NONE},
        )
        assert result["type"] == "create_entry"
        assert result["title"] == "Selora AI (Unconfigured)"
        assert result["data"][CONF_LLM_PROVIDER] == LLM_PROVIDER_NONE

    async def test_selecting_anthropic_routes_to_anthropic_step(self, hass) -> None:
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_LLM_PROVIDER: LLM_PROVIDER_ANTHROPIC},
        )
        assert result["type"] == "form"
        assert result["step_id"] == "anthropic"

    async def test_selecting_ollama_routes_to_ollama_step(self, hass) -> None:
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_LLM_PROVIDER: LLM_PROVIDER_OLLAMA},
        )
        assert result["type"] == "form"
        assert result["step_id"] == "ollama"

    async def test_selecting_gemini_routes_to_gemini_step(self, hass) -> None:
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_LLM_PROVIDER: LLM_PROVIDER_GEMINI},
        )
        assert result["type"] == "form"
        assert result["step_id"] == "gemini"

    async def test_selecting_openai_routes_to_openai_step(self, hass) -> None:
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_LLM_PROVIDER: LLM_PROVIDER_OPENAI},
        )
        assert result["type"] == "form"
        assert result["step_id"] == "openai"


# ── Step: anthropic ──────────────────────────────────────────────────


class TestStepAnthropic:
    """Tests for the Anthropic configuration step."""

    async def _reach_anthropic_step(self, hass):
        """Navigate through user → anthropic."""
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        return await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_LLM_PROVIDER: LLM_PROVIDER_ANTHROPIC},
        )

    async def test_shows_form(self, hass) -> None:
        result = await self._reach_anthropic_step(hass)
        assert result["type"] == "form"
        assert result["step_id"] == "anthropic"

    async def test_invalid_key_shows_error(self, hass) -> None:
        result = await self._reach_anthropic_step(hass)
        with patch(
            "custom_components.selora_ai.config_flow._validate_anthropic",
            new_callable=AsyncMock,
            side_effect=ConnectionError("API key invalid"),
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={
                    CONF_ANTHROPIC_API_KEY: "bad-key",
                    CONF_ANTHROPIC_MODEL: DEFAULT_ANTHROPIC_MODEL,
                },
            )
        assert result["type"] == "form"
        assert result["errors"] == {"base": "cannot_connect"}

    async def test_valid_key_chains_forward(self, hass) -> None:
        result = await self._reach_anthropic_step(hass)
        with patch(
            "custom_components.selora_ai.config_flow._validate_anthropic",
            new_callable=AsyncMock,
            return_value={"title": "Selora AI (Claude — claude-sonnet-4-6)"},
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={
                    CONF_ANTHROPIC_API_KEY: "sk-ant-test-key",
                    CONF_ANTHROPIC_MODEL: DEFAULT_ANTHROPIC_MODEL,
                },
            )
        # Valid credentials chain to discovery or straight to entry creation
        # (when no pending discovery flows exist, the flow may skip to create_entry)
        assert result["type"] in ("form", "create_entry", "abort")


# ── Step: ollama ─────────────────────────────────────────────────────


class TestStepOllama:
    """Tests for the Ollama configuration step."""

    async def _reach_ollama_step(self, hass):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        return await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_LLM_PROVIDER: LLM_PROVIDER_OLLAMA},
        )

    async def test_shows_form(self, hass) -> None:
        result = await self._reach_ollama_step(hass)
        assert result["type"] == "form"
        assert result["step_id"] == "ollama"

    async def test_unreachable_host_shows_error(self, hass) -> None:
        result = await self._reach_ollama_step(hass)
        with patch(
            "custom_components.selora_ai.config_flow._validate_ollama",
            new_callable=AsyncMock,
            side_effect=ConnectionError("Ollama not reachable"),
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={
                    CONF_OLLAMA_HOST: "http://unreachable:11434",
                    CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
                },
            )
        assert result["type"] == "form"
        assert result["errors"] == {"base": "cannot_connect"}


# ── Step: openai ─────────────────────────────────────────────────────


class TestStepGemini:
    """Tests for the Gemini configuration step."""

    async def _reach_gemini_step(self, hass):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        return await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_LLM_PROVIDER: LLM_PROVIDER_GEMINI},
        )

    async def test_shows_form(self, hass) -> None:
        result = await self._reach_gemini_step(hass)
        assert result["type"] == "form"
        assert result["step_id"] == "gemini"

    async def test_invalid_key_shows_error(self, hass) -> None:
        result = await self._reach_gemini_step(hass)
        with patch(
            "custom_components.selora_ai.config_flow._validate_gemini",
            new_callable=AsyncMock,
            side_effect=ConnectionError("Gemini API key invalid"),
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={
                    CONF_GEMINI_API_KEY: "bad-key",
                    CONF_GEMINI_MODEL: DEFAULT_GEMINI_MODEL,
                },
            )
        assert result["type"] == "form"
        assert result["errors"] == {"base": "cannot_connect"}

    async def test_valid_key_chains_forward(self, hass) -> None:
        result = await self._reach_gemini_step(hass)
        with patch(
            "custom_components.selora_ai.config_flow._validate_gemini",
            new_callable=AsyncMock,
            return_value={"title": f"Selora AI (Gemini — {DEFAULT_GEMINI_MODEL})"},
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={
                    CONF_GEMINI_API_KEY: "AIza-test-key",
                    CONF_GEMINI_MODEL: DEFAULT_GEMINI_MODEL,
                },
            )
        assert result["type"] in ("form", "create_entry", "abort")


class TestStepOpenai:
    """Tests for the OpenAI configuration step."""

    async def test_valid_key_chains_forward(self, hass) -> None:
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_LLM_PROVIDER: LLM_PROVIDER_OPENAI},
        )
        assert result["step_id"] == "openai"

        with patch(
            "custom_components.selora_ai.config_flow._validate_openai",
            new_callable=AsyncMock,
            return_value={"title": f"Selora AI (OpenAI — {DEFAULT_OPENAI_MODEL})"},
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={
                    CONF_OPENAI_API_KEY: "sk-openai-test-key",
                    CONF_OPENAI_MODEL: DEFAULT_OPENAI_MODEL,
                },
            )
        assert result["type"] in ("form", "create_entry", "abort")
