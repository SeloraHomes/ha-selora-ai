"""OpenAI LLM provider — thin subclass of OpenAICompatibleProvider."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from ..const import DEFAULT_OPENAI_HOST, DEFAULT_OPENAI_MODEL
from .openai_compat import OpenAICompatibleProvider

# OpenAI's models API exposes no modality metadata, so vision gating is a
# deny-list: every mainline chat model since gpt-4o is multimodal, while
# these families are text-only and reject image_url content. The model
# field is free-form (users can point at any snapshot), so match by
# family substring; plain "gpt-4" (the 0613-era alias) is matched exactly
# below so "gpt-4o"/"gpt-4.1" stay vision-capable.
_TEXT_ONLY_MODEL_HINTS = (
    "gpt-3.5",
    "gpt-4-0613",
    "gpt-4-32k",
    "o1-mini",
    "o1-preview",
    "o3-mini",
    "-instruct",
    "davinci",
    "babbage",
)


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
    def provider_type(self) -> str:
        return "openai"

    @property
    def provider_name(self) -> str:
        return f"OpenAI ({self._model})"

    @property
    def supports_vision(self) -> bool:
        model = self._model.lower()
        if model == "gpt-4":
            return False
        return not any(hint in model for hint in _TEXT_ONLY_MODEL_HINTS)
