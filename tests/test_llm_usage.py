"""Tests for LLM token-usage capture.

Covers:
- ``estimate_llm_cost_usd`` math
- Each provider's ``extract_usage`` and ``parse_stream_usage``
- The provider->callback wiring used by ``LLMClient``
"""

from __future__ import annotations

from collections import deque
import json

import pytest

from custom_components.selora_ai.const import estimate_llm_cost_usd
from custom_components.selora_ai.providers.anthropic import AnthropicProvider
from custom_components.selora_ai.providers.gemini import GeminiProvider
from custom_components.selora_ai.providers.openai import OpenAIProvider


# ── Pricing helper ────────────────────────────────────────────────────


class TestEstimateCost:
    def test_known_anthropic_model(self) -> None:
        # claude-sonnet-4-6: $3/1M in, $15/1M out
        cost = estimate_llm_cost_usd("anthropic", "claude-sonnet-4-6", 1_000_000, 1_000_000)
        assert cost == pytest.approx(18.0)

    def test_partial_tokens(self) -> None:
        cost = estimate_llm_cost_usd("anthropic", "claude-sonnet-4-6", 1000, 500)
        # 1000 * 3 / 1M + 500 * 15 / 1M = 0.003 + 0.0075 = 0.0105
        assert cost == pytest.approx(0.0105)

    def test_unknown_model_returns_zero(self) -> None:
        assert estimate_llm_cost_usd("anthropic", "nonexistent-model", 100, 100) == 0.0

    def test_unknown_provider_returns_zero(self) -> None:
        assert estimate_llm_cost_usd("madeup", "anything", 100, 100) == 0.0

    def test_ollama_is_free(self) -> None:
        assert estimate_llm_cost_usd("ollama", "llama4", 1_000_000, 1_000_000) == 0.0

    def test_anthropic_opus_4_7_official_pricing(self) -> None:
        # Anthropic-published rate: $5/$25 per MTok for Opus 4.5+.
        cost = estimate_llm_cost_usd("anthropic", "claude-opus-4-7", 1_000_000, 1_000_000)
        assert cost == pytest.approx(30.0)

    def test_override_takes_precedence(self) -> None:
        overrides = {"anthropic": {"claude-sonnet-4-6": [1.5, 7.5]}}
        cost = estimate_llm_cost_usd(
            "anthropic", "claude-sonnet-4-6", 1_000_000, 1_000_000, overrides=overrides
        )
        # 1.5 + 7.5 = $9.0 vs default $18.0
        assert cost == pytest.approx(9.0)

    def test_override_supports_unknown_models(self) -> None:
        overrides = {"openai": {"gpt-future": [10.0, 40.0]}}
        cost = estimate_llm_cost_usd(
            "openai", "gpt-future", 1_000_000, 1_000_000, overrides=overrides
        )
        assert cost == pytest.approx(50.0)

    def test_override_falls_back_for_unconfigured_model(self) -> None:
        overrides = {"anthropic": {"claude-opus-4-7": [1.0, 1.0]}}
        # Model not present in overrides → must use built-in default.
        cost = estimate_llm_cost_usd(
            "anthropic", "claude-sonnet-4-6", 1_000_000, 1_000_000, overrides=overrides
        )
        assert cost == pytest.approx(18.0)

    def test_override_accepts_tuple_too(self) -> None:
        overrides = {"anthropic": {"claude-sonnet-4-6": (2.0, 8.0)}}
        cost = estimate_llm_cost_usd(
            "anthropic", "claude-sonnet-4-6", 1_000_000, 1_000_000, overrides=overrides
        )
        assert cost == pytest.approx(10.0)

    def test_empty_overrides_uses_defaults(self) -> None:
        cost = estimate_llm_cost_usd(
            "anthropic", "claude-sonnet-4-6", 1_000_000, 1_000_000, overrides={}
        )
        assert cost == pytest.approx(18.0)

    def test_none_overrides_uses_defaults(self) -> None:
        cost = estimate_llm_cost_usd(
            "anthropic", "claude-sonnet-4-6", 1_000_000, 1_000_000, overrides=None
        )
        assert cost == pytest.approx(18.0)


# ── Provider extract_usage ────────────────────────────────────────────


class TestAnthropicUsage:
    @pytest.fixture
    def provider(self, hass):
        return AnthropicProvider(hass, api_key="test-key", model="claude-sonnet-4-6")

    def test_extract_usage_full(self, provider) -> None:
        response = {
            "content": [{"type": "text", "text": "ok"}],
            "usage": {
                "input_tokens": 120,
                "output_tokens": 45,
                "cache_creation_input_tokens": 200,
                "cache_read_input_tokens": 50,
            },
        }
        usage = provider.extract_usage(response)
        assert usage == {
            "input_tokens": 120,
            "output_tokens": 45,
            "cache_creation_input_tokens": 200,
            "cache_read_input_tokens": 50,
        }

    def test_extract_usage_missing_returns_none(self, provider) -> None:
        assert provider.extract_usage({"content": []}) is None

    def test_parse_stream_usage_message_start(self, provider) -> None:
        line = "data: " + json.dumps(
            {"type": "message_start", "message": {"usage": {"input_tokens": 99}}}
        )
        assert provider.parse_stream_usage(line) == {"input_tokens": 99}

    def test_parse_stream_usage_message_delta(self, provider) -> None:
        line = "data: " + json.dumps({"type": "message_delta", "usage": {"output_tokens": 17}})
        assert provider.parse_stream_usage(line) == {"output_tokens": 17}

    def test_parse_stream_usage_other_event(self, provider) -> None:
        line = "data: " + json.dumps({"type": "content_block_delta", "delta": {"text": "hi"}})
        assert provider.parse_stream_usage(line) is None

    def test_parse_stream_usage_invalid_json(self, provider) -> None:
        assert provider.parse_stream_usage("data: not-json") is None


class TestOpenAIUsage:
    @pytest.fixture
    def provider(self, hass):
        return OpenAIProvider(hass, api_key="test-key", model="gpt-5.4")

    def test_extract_usage_translates_field_names(self, provider) -> None:
        response = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 220, "completion_tokens": 80},
        }
        assert provider.extract_usage(response) == {
            "input_tokens": 220,
            "output_tokens": 80,
        }

    def test_extract_usage_missing_returns_none(self, provider) -> None:
        assert provider.extract_usage({"choices": []}) is None

    def test_payload_requests_stream_usage(self, provider) -> None:
        payload = provider.build_payload(
            system="s",
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
        )
        assert payload["stream"] is True
        assert payload["stream_options"] == {"include_usage": True}

    def test_payload_no_stream_options_when_not_streaming(self, provider) -> None:
        payload = provider.build_payload(
            system="s",
            messages=[{"role": "user", "content": "hi"}],
        )
        assert "stream_options" not in payload

    def test_parse_stream_usage_final_chunk(self, provider) -> None:
        line = "data: " + json.dumps(
            {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 25}}
        )
        assert provider.parse_stream_usage(line) == {
            "input_tokens": 50,
            "output_tokens": 25,
        }

    def test_parse_stream_usage_done_marker(self, provider) -> None:
        assert provider.parse_stream_usage("data: [DONE]") is None

    def test_parse_stream_usage_text_chunk(self, provider) -> None:
        line = "data: " + json.dumps({"choices": [{"delta": {"content": "hi"}}]})
        assert provider.parse_stream_usage(line) is None


class TestGeminiUsage:
    @pytest.fixture
    def provider(self, hass):
        return GeminiProvider(hass, api_key="test-key", model="gemini-2.5-flash")

    def test_extract_usage(self, provider) -> None:
        response = {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
            "usageMetadata": {
                "promptTokenCount": 300,
                "candidatesTokenCount": 100,
            },
        }
        assert provider.extract_usage(response) == {
            "input_tokens": 300,
            "output_tokens": 100,
        }

    def test_extract_usage_missing_metadata(self, provider) -> None:
        assert provider.extract_usage({"candidates": []}) is None

    def test_parse_stream_usage(self, provider) -> None:
        line = "data: " + json.dumps(
            {
                "candidates": [{"content": {"parts": [{"text": "hi"}]}}],
                "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 3},
            }
        )
        assert provider.parse_stream_usage(line) == {
            "input_tokens": 12,
            "output_tokens": 3,
        }


# ── Callback wiring ───────────────────────────────────────────────────


class TestUsageCallbackWiring:
    @pytest.fixture
    def provider(self, hass):
        return AnthropicProvider(hass, api_key="test-key", model="claude-sonnet-4-6")

    def test_callback_invoked_on_report(self, provider) -> None:
        seen: list[tuple[str, str, dict]] = []

        def cb(provider_type: str, model: str, usage: dict) -> None:
            seen.append((provider_type, model, dict(usage)))

        provider.set_usage_callback(cb)
        provider._report_usage({"input_tokens": 10, "output_tokens": 5})

        assert seen == [
            ("anthropic", "claude-sonnet-4-6", {"input_tokens": 10, "output_tokens": 5}),
        ]

    def test_no_callback_when_unset(self, provider) -> None:
        # Should not raise
        provider._report_usage({"input_tokens": 1, "output_tokens": 1})

    def test_zero_usage_skipped(self, provider) -> None:
        seen: list[tuple[str, str, dict]] = []
        provider.set_usage_callback(lambda p, m, u: seen.append((p, m, dict(u))))
        provider._report_usage({"input_tokens": 0, "output_tokens": 0})
        provider._report_usage(None)
        assert seen == []

    def test_callback_exception_swallowed(self, provider) -> None:
        def boom(*_args, **_kwargs):
            raise RuntimeError("nope")

        provider.set_usage_callback(boom)
        # Must not propagate — telemetry never breaks the request path
        provider._report_usage({"input_tokens": 1})


# ── LLMClient flush + ring buffer ─────────────────────────────────────


class TestLLMClientFlushUsage:
    """Provider reports buffered usage; LLMClient flushes with kind/intent.

    Tests the buffering contract: usage events sit pending until a public
    method calls ``_flush_usage`` (so chat can attach the parsed intent),
    and each flushed event lands in both the dispatcher signal and the
    ring buffer.
    """

    @pytest.fixture
    def llm_client(self, hass):
        from custom_components.selora_ai.llm_client import LLMClient

        provider = AnthropicProvider(
            hass, api_key="test-key", model="claude-sonnet-4-6"
        )
        return LLMClient(hass, provider)

    def test_provider_callback_buffers_instead_of_emitting(self, hass, llm_client) -> None:
        from custom_components.selora_ai.const import DOMAIN

        with llm_client._usage_scope():
            # Simulate the provider reporting usage mid-call.
            llm_client._provider._report_usage(
                {"input_tokens": 10, "output_tokens": 5}
            )

            # Buffered, not yet flushed → ring buffer empty.
            assert hass.data[DOMAIN]["llm_usage_events"] == deque(maxlen=500)
            assert len(llm_client._pending_usage.get()) == 1

    async def test_flush_emits_to_ring_buffer_and_signal(
        self, hass, llm_client
    ) -> None:
        from homeassistant.helpers.dispatcher import async_dispatcher_connect

        from custom_components.selora_ai.const import DOMAIN, SIGNAL_LLM_USAGE

        seen: list[dict] = []
        async_dispatcher_connect(
            hass, SIGNAL_LLM_USAGE, lambda payload: seen.append(payload)
        )

        with llm_client._usage_scope():
            llm_client._provider._report_usage({"input_tokens": 100, "output_tokens": 50})
            llm_client._flush_usage("chat", intent="answer")
        await hass.async_block_till_done()

        events = list(hass.data[DOMAIN]["llm_usage_events"])
        assert len(events) == 1
        evt = events[0]
        assert evt["kind"] == "chat"
        assert evt["intent"] == "answer"
        assert evt["provider"] == "anthropic"
        assert evt["model"] == "claude-sonnet-4-6"
        assert evt["input_tokens"] == 100
        assert evt["output_tokens"] == 50
        # claude-sonnet-4-6: $3 in + $15 out per 1M = 100*3/1M + 50*15/1M = $0.00105
        assert evt["cost_usd"] == pytest.approx(0.00105)
        assert "timestamp" in evt

        assert len(seen) == 1
        assert seen[0]["kind"] == "chat"
        assert seen[0]["intent"] == "answer"

    def test_flush_omits_intent_when_unset(self, hass, llm_client) -> None:
        with llm_client._usage_scope():
            llm_client._provider._report_usage({"input_tokens": 10, "output_tokens": 5})
            llm_client._flush_usage("suggestions")

        from custom_components.selora_ai.const import DOMAIN

        evt = list(hass.data[DOMAIN]["llm_usage_events"])[0]
        assert evt["kind"] == "suggestions"
        assert "intent" not in evt

    def test_flush_handles_multiple_buffered_events(self, hass, llm_client) -> None:
        # Two provider reports between flushes — e.g. cache + main response.
        with llm_client._usage_scope():
            llm_client._provider._report_usage({"input_tokens": 10, "output_tokens": 0})
            llm_client._provider._report_usage({"input_tokens": 0, "output_tokens": 30})
            llm_client._flush_usage("chat_tool_round")

        from custom_components.selora_ai.const import DOMAIN

        events = list(hass.data[DOMAIN]["llm_usage_events"])
        assert len(events) == 2
        assert all(e["kind"] == "chat_tool_round" for e in events)

    def test_flush_clears_pending(self, hass, llm_client) -> None:
        with llm_client._usage_scope():
            llm_client._provider._report_usage({"input_tokens": 10, "output_tokens": 5})
            llm_client._flush_usage("chat")
            # Subsequent flush with no new usage is a no-op.
            llm_client._flush_usage("suggestions")

        from custom_components.selora_ai.const import DOMAIN

        events = list(hass.data[DOMAIN]["llm_usage_events"])
        assert len(events) == 1
        assert events[0]["kind"] == "chat"

    def test_drop_pending_clears_without_emitting(self, hass, llm_client) -> None:
        with llm_client._usage_scope():
            llm_client._provider._report_usage({"input_tokens": 10, "output_tokens": 5})
            llm_client._drop_pending_usage()
            llm_client._flush_usage("chat")

        from custom_components.selora_ai.const import DOMAIN

        assert len(hass.data[DOMAIN]["llm_usage_events"]) == 0

    def test_pricing_override_applied_to_flushed_event(self, hass) -> None:
        from custom_components.selora_ai.const import DOMAIN
        from custom_components.selora_ai.llm_client import LLMClient

        provider = AnthropicProvider(
            hass, api_key="test-key", model="claude-sonnet-4-6"
        )
        # Half off the default rate.
        client = LLMClient(
            hass,
            provider,
            pricing_overrides={"anthropic": {"claude-sonnet-4-6": [1.5, 7.5]}},
        )

        with client._usage_scope():
            client._provider._report_usage(
                {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
            )
            client._flush_usage("chat")

        evt = list(hass.data[DOMAIN]["llm_usage_events"])[0]
        # Default would be $18 — override drops it to $9.
        assert evt["cost_usd"] == pytest.approx(9.0)

    def test_set_pricing_overrides_takes_effect_immediately(self, hass) -> None:
        from custom_components.selora_ai.const import DOMAIN
        from custom_components.selora_ai.llm_client import LLMClient

        provider = AnthropicProvider(
            hass, api_key="test-key", model="claude-sonnet-4-6"
        )
        client = LLMClient(hass, provider)
        client.set_pricing_overrides(
            {"anthropic": {"claude-sonnet-4-6": [0.0, 0.0]}}
        )

        with client._usage_scope():
            client._provider._report_usage(
                {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
            )
            client._flush_usage("chat")

        evt = list(hass.data[DOMAIN]["llm_usage_events"])[0]
        assert evt["cost_usd"] == pytest.approx(0.0)

    def test_ring_buffer_caps_size(self, hass, llm_client) -> None:
        from custom_components.selora_ai.const import DOMAIN
        from custom_components.selora_ai.llm_client import LLM_USAGE_BUFFER_SIZE

        # Fill past the cap; oldest entries should be dropped.
        with llm_client._usage_scope():
            for i in range(LLM_USAGE_BUFFER_SIZE + 25):
                llm_client._provider._report_usage(
                    {"input_tokens": 1, "output_tokens": i + 1}
                )
                llm_client._flush_usage("chat")

        events = list(hass.data[DOMAIN]["llm_usage_events"])
        assert len(events) == LLM_USAGE_BUFFER_SIZE
        # The oldest (output_tokens=1..25) should have been dropped.
        assert events[0]["output_tokens"] == 26
        assert events[-1]["output_tokens"] == LLM_USAGE_BUFFER_SIZE + 25
