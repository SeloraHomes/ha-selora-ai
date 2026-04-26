"""OpenRouter LLM provider — thin subclass of OpenAICompatibleProvider.

OpenRouter speaks the OpenAI chat-completions protocol but routes to many
vendor models behind vendor-prefixed model IDs (e.g. "anthropic/claude-sonnet-4.5").
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from ..const import (
    DEFAULT_OPENROUTER_HOST,
    DEFAULT_OPENROUTER_MODEL,
    OPENROUTER_APP_REFERER,
    OPENROUTER_APP_TITLE,
)
from .openai_compat import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    """OpenRouter API provider (multi-vendor aggregator)."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        api_key: str = "",
        model: str = "",
        host: str = "",
        **_kwargs: Any,
    ) -> None:
        super().__init__(
            hass,
            model=model or DEFAULT_OPENROUTER_MODEL,
            host=host or DEFAULT_OPENROUTER_HOST,
            api_key=api_key,
        )

    @property
    def provider_type(self) -> str:
        return "openrouter"

    @property
    def provider_name(self) -> str:
        return f"OpenRouter ({self._model})"

    def _get_headers(self) -> dict[str, str]:
        headers = super()._get_headers()
        headers["HTTP-Referer"] = OPENROUTER_APP_REFERER
        headers["X-Title"] = OPENROUTER_APP_TITLE
        return headers
