"""Ollama LLM provider — local, OpenAI-compatible, no API key needed."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant

from ..const import (
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    HEALTH_CHECK_TIMEOUT,
)
from .openai_compat import OpenAICompatibleProvider

_LOGGER = logging.getLogger(__name__)

# Substrings identifying multimodal model families in Ollama's catalog.
# Vision is per-model there — a text-only model silently ignores (or
# errors on) image_url blocks, so the capability flag has to look at the
# configured model name rather than the provider.
_VISION_MODEL_HINTS = (
    "llava",
    "llama4",
    "llama3.2-vision",
    "qwen2.5vl",
    "qwen3-vl",
    "qwen-vl",
    "gemma3",
    "minicpm-v",
    "moondream",
    "granite3.2-vision",
    "mistral-small3",
    "pixtral",
    "vision",
)


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

    @property
    def is_local(self) -> bool:
        return True

    @property
    def supports_vision(self) -> bool:
        model = self._model.lower()
        return any(hint in model for hint in _VISION_MODEL_HINTS)

    async def health_check(self) -> bool:
        """Check Ollama is reachable and the model is pulled."""
        try:
            session = self._get_session()
            async with session.get(
                f"{self._host}/api/tags",
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=HEALTH_CHECK_TIMEOUT),
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
