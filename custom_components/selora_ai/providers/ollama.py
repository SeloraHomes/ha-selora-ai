"""Ollama LLM provider — local, OpenAI-compatible, no API key needed."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant

from ..const import DEFAULT_LLM_TIMEOUT, DEFAULT_OLLAMA_HOST, DEFAULT_OLLAMA_MODEL
from .openai_compat import OpenAICompatibleProvider

_LOGGER = logging.getLogger(__name__)


class OllamaProvider(OpenAICompatibleProvider):
    """Ollama local LLM provider."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        model: str = "",
        host: str = "",
        **_kwargs: Any,
    ) -> None:
        super().__init__(
            hass,
            model=model or DEFAULT_OLLAMA_MODEL,
            host=host or DEFAULT_OLLAMA_HOST,
            api_key="",
        )

    @property
    def provider_type(self) -> str:
        return "ollama"

    @property
    def provider_name(self) -> str:
        return f"Ollama ({self._model})"

    @property
    def requires_api_key(self) -> bool:
        return False

    async def health_check(self) -> bool:
        """Check Ollama is reachable and the model is pulled."""
        try:
            session = self._get_session()
            async with session.get(
                f"{self._host}/api/tags",
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_LLM_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
                models = [m.get("name", "") for m in data.get("models", [])]
                if not any(self._model in m for m in models):
                    _LOGGER.warning(
                        "Model '%s' not found in Ollama. Available: %s",
                        self._model,
                        models,
                    )
                    return False
                return True
        except Exception:
            _LOGGER.exception("Ollama health check failed")
            return False
