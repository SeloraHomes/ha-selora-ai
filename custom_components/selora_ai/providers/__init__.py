"""LLM provider abstraction — factory and registry.

Adding a new provider:
  1. Create a new module under providers/ with a class extending LLMProvider
     (or OpenAICompatibleProvider for OpenAI-format APIs).
  2. Register it in PROVIDER_REGISTRY below.
  3. Add config constants in const.py and a config flow step.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .anthropic import AnthropicProvider
from .base import LLMProvider
from .gemini import GeminiProvider
from .ollama import OllamaProvider
from .openai import OpenAIProvider
from .openrouter import OpenRouterProvider
from .selora_cloud import SeloraCloudProvider

__all__ = [
    "AnthropicProvider",
    "GeminiProvider",
    "LLMProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "OpenRouterProvider",
    "SeloraCloudProvider",
    "create_provider",
]

PROVIDER_REGISTRY: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
    "openrouter": OpenRouterProvider,
    "ollama": OllamaProvider,
    "selora_cloud": SeloraCloudProvider,
}


def create_provider(
    provider_name: str,
    hass: HomeAssistant,
    **kwargs: Any,
) -> LLMProvider:
    """Create a provider instance by name.

    Raises ValueError for unknown provider names.
    """
    cls = PROVIDER_REGISTRY.get(provider_name)
    if cls is None:
        raise ValueError(f"Unknown LLM provider: {provider_name!r}")
    return cls(hass, **kwargs)
