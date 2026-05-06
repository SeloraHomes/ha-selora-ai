"""Health-check coverage for the LLM providers.

Config-flow validation hangs on these calls; before this batch every key
check did a real chat completion with DEFAULT_LLM_TIMEOUT (120 s). The
new health checks hit a cheap models-list endpoint with HEALTH_CHECK_TIMEOUT
(15 s). These tests pin the new contract:

* OpenAI / OpenRouter / Anthropic call GET /v1/models or /v1/auth/key —
  authenticated, fast, no inference.
* The configured timeout is HEALTH_CHECK_TIMEOUT, not DEFAULT_LLM_TIMEOUT.
* Bad keys (HTTP 401) return False; transport errors return False.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.selora_ai.const import HEALTH_CHECK_TIMEOUT


def _mock_session(status: int, body: str = "") -> tuple[MagicMock, MagicMock]:
    """Return (session, captured_request) where captured_request stores
    the args passed to session.get so tests can assert URL / timeout."""
    captured: dict[str, Any] = {}

    response = MagicMock()
    response.status = status
    response.text = AsyncMock(return_value=body)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=False)

    def _get(*args: Any, **kwargs: Any) -> MagicMock:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return cm

    session = MagicMock()
    session.get = _get
    return session, captured


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


class TestOpenAIHealthCheck:
    @pytest.mark.asyncio
    async def test_returns_true_on_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from custom_components.selora_ai.providers.openai import OpenAIProvider

        session, captured = _mock_session(200, '{"data": []}')
        provider = OpenAIProvider(MagicMock(), api_key="sk-test", model="gpt-4o-mini")
        monkeypatch.setattr(provider, "_get_session", lambda: session)

        assert await provider.health_check() is True
        # Must hit /v1/models, not /v1/chat/completions, and use the
        # short HEALTH_CHECK_TIMEOUT.
        url = captured["args"][0]
        assert url.endswith("/v1/models")
        assert captured["kwargs"]["headers"]["Authorization"] == "Bearer sk-test"
        timeout = captured["kwargs"]["timeout"]
        assert isinstance(timeout, aiohttp.ClientTimeout)
        assert timeout.total == HEALTH_CHECK_TIMEOUT

    @pytest.mark.asyncio
    async def test_returns_false_on_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from custom_components.selora_ai.providers.openai import OpenAIProvider

        session, _ = _mock_session(401, '{"error":{"message":"invalid"}}')
        provider = OpenAIProvider(MagicMock(), api_key="sk-bad", model="gpt-4o-mini")
        monkeypatch.setattr(provider, "_get_session", lambda: session)

        assert await provider.health_check() is False

    @pytest.mark.asyncio
    async def test_returns_false_on_transport_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from custom_components.selora_ai.providers.openai import OpenAIProvider

        session = MagicMock()
        session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
        provider = OpenAIProvider(MagicMock(), api_key="sk-test", model="gpt-4o-mini")
        monkeypatch.setattr(provider, "_get_session", lambda: session)

        assert await provider.health_check() is False


# ---------------------------------------------------------------------------
# OpenRouter — /v1/models is public, must use /v1/auth/key
# ---------------------------------------------------------------------------


class TestOpenRouterHealthCheck:
    @pytest.mark.asyncio
    async def test_uses_auth_key_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from custom_components.selora_ai.providers.openrouter import OpenRouterProvider

        session, captured = _mock_session(200, '{"data": {"label": "k"}}')
        provider = OpenRouterProvider(
            MagicMock(), api_key="or-test", model="anthropic/claude-sonnet-4.5"
        )
        monkeypatch.setattr(provider, "_get_session", lambda: session)

        assert await provider.health_check() is True
        url = captured["args"][0]
        # Critical: must NOT use /v1/models (public endpoint), must use /v1/auth/key.
        assert "/v1/auth/key" in url
        assert "/v1/models" not in url
        assert captured["kwargs"]["headers"]["Authorization"] == "Bearer or-test"
        assert captured["kwargs"]["timeout"].total == HEALTH_CHECK_TIMEOUT

    @pytest.mark.asyncio
    async def test_returns_false_on_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from custom_components.selora_ai.providers.openrouter import OpenRouterProvider

        session, _ = _mock_session(401, "unauthorized")
        provider = OpenRouterProvider(
            MagicMock(), api_key="or-bad", model="anthropic/claude-sonnet-4.5"
        )
        monkeypatch.setattr(provider, "_get_session", lambda: session)

        assert await provider.health_check() is False


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


class TestAnthropicHealthCheck:
    @pytest.mark.asyncio
    async def test_returns_true_on_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from custom_components.selora_ai.providers.anthropic import AnthropicProvider

        session, captured = _mock_session(200, '{"data": []}')
        provider = AnthropicProvider(
            MagicMock(), api_key="sk-ant-test", model="claude-sonnet-4-6"
        )
        monkeypatch.setattr(provider, "_get_session", lambda: session)

        assert await provider.health_check() is True
        url = captured["args"][0]
        assert url.endswith("/v1/models")
        # Anthropic uses x-api-key, not Authorization.
        headers = captured["kwargs"]["headers"]
        assert headers["x-api-key"] == "sk-ant-test"
        assert "anthropic-version" in headers
        assert "Authorization" not in headers
        assert captured["kwargs"]["timeout"].total == HEALTH_CHECK_TIMEOUT

    @pytest.mark.asyncio
    async def test_returns_false_on_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from custom_components.selora_ai.providers.anthropic import AnthropicProvider

        session, _ = _mock_session(401, '{"error":{"type":"authentication_error"}}')
        provider = AnthropicProvider(
            MagicMock(), api_key="sk-ant-bad", model="claude-sonnet-4-6"
        )
        monkeypatch.setattr(provider, "_get_session", lambda: session)

        assert await provider.health_check() is False


# ---------------------------------------------------------------------------
# Timeout regression — the whole point of this batch
# ---------------------------------------------------------------------------


class TestNoLongTimeout:
    """Regression guard: HEALTH_CHECK_TIMEOUT must stay well under
    DEFAULT_LLM_TIMEOUT. A 2-minute spinner inside an interactive
    config-flow form was the bug we set out to fix."""

    def test_health_check_timeout_is_short(self) -> None:
        from custom_components.selora_ai.const import (
            DEFAULT_LLM_TIMEOUT,
            HEALTH_CHECK_TIMEOUT,
        )

        assert HEALTH_CHECK_TIMEOUT <= 30
        assert HEALTH_CHECK_TIMEOUT < DEFAULT_LLM_TIMEOUT
