"""Tests for the OpenRouter LLM provider."""

from __future__ import annotations

import pytest

from custom_components.selora_ai.const import (
    DEFAULT_OPENROUTER_HOST,
    DEFAULT_OPENROUTER_MODEL,
    OPENROUTER_APP_REFERER,
    OPENROUTER_APP_TITLE,
)
from custom_components.selora_ai.providers import (
    OpenRouterProvider,
    create_provider,
)


@pytest.fixture
def provider(hass):
    """OpenRouterProvider with explicit credentials."""
    return OpenRouterProvider(
        hass,
        api_key="sk-or-test-key",
        model="anthropic/claude-sonnet-4.5",
    )


class TestOpenRouterIdentity:
    def test_provider_type(self, provider) -> None:
        assert provider.provider_type == "openrouter"

    def test_provider_name(self, provider) -> None:
        assert provider.provider_name == "OpenRouter (anthropic/claude-sonnet-4.5)"

    def test_requires_api_key(self, provider) -> None:
        assert provider.requires_api_key is True

    def test_has_api_key(self, provider) -> None:
        assert provider.has_api_key is True

    def test_model(self, provider) -> None:
        assert provider.model == "anthropic/claude-sonnet-4.5"


class TestOpenRouterDefaults:
    def test_default_host(self, hass) -> None:
        prov = OpenRouterProvider(hass, api_key="k")
        assert prov._host == DEFAULT_OPENROUTER_HOST

    def test_default_model(self, hass) -> None:
        prov = OpenRouterProvider(hass, api_key="k")
        assert prov.model == DEFAULT_OPENROUTER_MODEL

    def test_endpoint_uses_v1_chat_completions(self, provider) -> None:
        assert provider._endpoint == f"{DEFAULT_OPENROUTER_HOST}/v1/chat/completions"


class TestOpenRouterHeaders:
    def test_authorization_header(self, provider) -> None:
        headers = provider._get_headers()
        assert headers["Authorization"] == "Bearer sk-or-test-key"
        assert headers["Content-Type"] == "application/json"

    def test_attribution_headers(self, provider) -> None:
        headers = provider._get_headers()
        assert headers["HTTP-Referer"] == OPENROUTER_APP_REFERER
        assert headers["X-Title"] == OPENROUTER_APP_TITLE


class TestOpenRouterRegistry:
    def test_create_via_factory(self, hass) -> None:
        prov = create_provider(
            "openrouter",
            hass,
            api_key="k",
            model="openai/gpt-5",
        )
        assert isinstance(prov, OpenRouterProvider)
        assert prov.model == "openai/gpt-5"
