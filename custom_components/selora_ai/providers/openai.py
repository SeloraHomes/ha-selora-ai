"""OpenAI LLM provider — thin subclass of OpenAICompatibleProvider."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from ..const import DEFAULT_OPENAI_HOST, DEFAULT_OPENAI_MODEL
from .openai_compat import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    """OpenAI API provider (GPT models)."""

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
            model=model or DEFAULT_OPENAI_MODEL,
            host=host or DEFAULT_OPENAI_HOST,
            api_key=api_key,
        )

    @property
    def provider_name(self) -> str:
        return f"OpenAI ({self._model})"
