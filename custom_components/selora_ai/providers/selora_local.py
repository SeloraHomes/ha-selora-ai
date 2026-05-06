"""Selora AI Local — local-inference HA add-on (libselora / Phi-3.5 INT8).

Speaks the OpenAI chat-completions protocol over HTTP. The add-on listens on
:5310 by default and exposes four LoRA specialists as model ids
(``selora-v1-{commands,automations,answers,clarifications}``).

The user never picks a specialist directly — the integration knows what the
current call is for (executing an action vs. building an automation vs.
answering a question vs. asking a clarification) and routes via
``set_call_kind`` set by ``LLMClient._usage_scope``.
"""

from __future__ import annotations

from contextvars import ContextVar
import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant

from ..const import DEFAULT_LLM_TIMEOUT, DEFAULT_SELORA_LOCAL_HOST
from .openai_compat import OpenAICompatibleProvider

_LOGGER = logging.getLogger(__name__)

# LoRA specialist per LLMClient call ``kind``. Anything not listed falls back
# to ``commands`` — the README's documented default.
_KIND_TO_LORA: dict[str, str] = {
    "suggestions": "selora-v1-automations",  # pattern → automation generation
    "command": "selora-v1-commands",  # explicit "do X" command path
    "chat": "selora-v1-commands",  # architect classify-and-respond
    "chat_tool_round": "selora-v1-commands",  # continuation of chat with tools
    "session_title": "selora-v1-answers",  # short generative summary
    "health_check": "selora-v1-commands",  # any LoRA works
    "raw": "selora-v1-commands",  # send_request fallback
}
_DEFAULT_LORA = "selora-v1-commands"


class SeloraLocalProvider(OpenAICompatibleProvider):
    """Selora AI Local provider (HA add-on, OpenAI-compatible)."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        host: str = "",
        **_kwargs: Any,
    ) -> None:
        super().__init__(
            hass,
            model=_DEFAULT_LORA,
            host=host or DEFAULT_SELORA_LOCAL_HOST,
            api_key="",
        )
        # Per-task LoRA selection. ContextVar so concurrent calls (e.g. a
        # background analysis cycle overlapping a panel chat request) don't
        # trample each other.
        self._call_kind: ContextVar[str | None] = ContextVar("selora_local_call_kind", default=None)

    @property
    def provider_type(self) -> str:
        return "selora_local"

    @property
    def provider_name(self) -> str:
        return "Selora AI Local"

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def is_low_context(self) -> bool:
        # libselora ships with max_seq=1024 — anything larger gets truncated
        # by the engine before the model sees it.
        return True

    def set_call_kind(self, kind: str | None) -> None:
        self._call_kind.set(kind)

    def _resolve_model(self) -> str:
        return _KIND_TO_LORA.get(self._call_kind.get(), _DEFAULT_LORA)

    def build_payload(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        # Swap the model into self._model so the parent payload + the usage
        # callback (which reports against self._model) both see the right
        # specialist for this call.
        self._model = self._resolve_model()
        return super().build_payload(
            system,
            messages,
            tools=tools,
            stream=stream,
            max_tokens=max_tokens,
        )

    async def health_check(self) -> bool:
        """Check the add-on is reachable on /health."""
        try:
            session = self._get_session()
            async with session.get(
                f"{self._host}/health",
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_LLM_TIMEOUT),
            ) as resp:
                return resp.status == 200
        except Exception:
            _LOGGER.exception("Selora Local health check failed")
            return False
