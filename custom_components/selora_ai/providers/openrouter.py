"""OpenRouter LLM provider — thin subclass of OpenAICompatibleProvider.

OpenRouter speaks the OpenAI chat-completions protocol but routes to many
vendor models behind vendor-prefixed model IDs (e.g. "anthropic/claude-sonnet-4.5").
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant

from ..const import (
    DEFAULT_OPENROUTER_HOST,
    DEFAULT_OPENROUTER_MODEL,
    HEALTH_CHECK_TIMEOUT,
    OPENROUTER_APP_REFERER,
    OPENROUTER_APP_TITLE,
)
from .openai_compat import OpenAICompatibleProvider

_LOGGER = logging.getLogger(__name__)


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

    async def health_check(self) -> bool:
        """Validate the OpenRouter API key.

        Override the base ``GET /v1/models`` probe because OpenRouter's
        models endpoint is **public** — it returns 200 even without an
        Authorization header, so the inherited check would accept any
        invalid key. ``GET /api/v1/auth/key`` requires a valid Bearer
        token and returns the key's metadata, so 200 is an authoritative
        signal that the key is good.
        """
        try:
            session = self._get_session()
            async with session.get(
                f"{self._host}/v1/auth/key",
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=HEALTH_CHECK_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    body = (await resp.text())[:200]
                    _LOGGER.error(
                        "OpenRouter health check failed: HTTP %s: %s",
                        resp.status,
                        body,
                    )
                    return False
                return True
        except (aiohttp.ClientError, TimeoutError):
            _LOGGER.exception("OpenRouter health check failed")
            return False
