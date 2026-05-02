"""Tests for LLM 429 (quota) handling."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai.const import EVENT_LLM_QUOTA_EXCEEDED
from custom_components.selora_ai.providers.anthropic import AnthropicProvider
from custom_components.selora_ai.providers.base import (
    RateLimitError,
    _parse_retry_after,
)


class TestParseRetryAfter:
    def test_missing_returns_none(self) -> None:
        assert _parse_retry_after(None) is None

    def test_empty_returns_none(self) -> None:
        assert _parse_retry_after("") is None

    def test_integer_seconds(self) -> None:
        assert _parse_retry_after("30") == 30

    def test_whitespace_tolerated(self) -> None:
        assert _parse_retry_after("  120  ") == 120

    def test_negative_returns_none(self) -> None:
        # Servers shouldn't send negative values; treat them as missing.
        assert _parse_retry_after("-5") is None

    def test_non_integer_returns_none(self) -> None:
        # We don't parse the HTTP-date form — too rare to be worth it.
        assert _parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None

    def test_caps_at_one_hour(self) -> None:
        # Protect the alert window from absurd backoff values.
        assert _parse_retry_after("99999") == 3600


class TestRateLimitError:
    def test_carries_provider_and_retry(self) -> None:
        err = RateLimitError("anthropic", "Rate limit hit", retry_after=42)
        assert err.provider == "anthropic"
        assert err.retry_after == 42
        assert err.message == "Rate limit hit"
        assert str(err) == "Rate limit hit"

    def test_is_connection_error(self) -> None:
        # LLMClient's tool-calling loops only catch ConnectionError —
        # RateLimitError must remain compatible so 429s don't escape
        # those handlers as uncaught exceptions.
        err = RateLimitError("anthropic", "rate limit", retry_after=10)
        assert isinstance(err, ConnectionError)


class TestEmitQuotaExceeded:
    """The provider helper that fires the panel-facing HA event."""

    @pytest.fixture
    def provider(self, hass: HomeAssistant) -> AnthropicProvider:
        return AnthropicProvider(hass, api_key="test-key", model="claude-sonnet-4-6")

    async def test_fires_event_with_payload(
        self, hass: HomeAssistant, provider: AnthropicProvider
    ) -> None:
        events = []
        hass.bus.async_listen(EVENT_LLM_QUOTA_EXCEEDED, lambda evt: events.append(evt.data))

        provider._emit_quota_exceeded(retry_after=45, body="quota exceeded")
        await hass.async_block_till_done()

        assert len(events) == 1
        assert events[0]["provider"] == "anthropic"
        assert events[0]["model"] == "claude-sonnet-4-6"
        assert events[0]["retry_after"] == 45
        assert events[0]["message"] == "quota exceeded"

    async def test_falls_back_to_default_backoff(
        self, hass: HomeAssistant, provider: AnthropicProvider
    ) -> None:
        events = []
        hass.bus.async_listen(EVENT_LLM_QUOTA_EXCEEDED, lambda evt: events.append(evt.data))

        provider._emit_quota_exceeded(retry_after=None, body="no header")
        await hass.async_block_till_done()

        # Default lives in const.DEFAULT_QUOTA_BACKOFF_SECONDS (60).
        assert events[0]["retry_after"] == 60

    async def test_truncates_long_body(
        self, hass: HomeAssistant, provider: AnthropicProvider
    ) -> None:
        events = []
        hass.bus.async_listen(EVENT_LLM_QUOTA_EXCEEDED, lambda evt: events.append(evt.data))

        provider._emit_quota_exceeded(retry_after=10, body="x" * 1000)
        await hass.async_block_till_done()

        # 200 char cap protects the dispatcher and the panel banner from
        # multi-kilobyte upstream errors.
        assert len(events[0]["message"]) == 200
