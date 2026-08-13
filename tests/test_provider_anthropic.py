"""Response parsing for the Anthropic adapter.

``/v1/messages`` answers with a *list* of content blocks, so reading a
reply means walking that list rather than taking a field. These pin the
cases where the list holds more than the one text block the happy path
produces — thinking blocks and tool_use split the prose around them.
"""

from __future__ import annotations

import pytest

from custom_components.selora_ai.const import ANTHROPIC_API_VERSION
from custom_components.selora_ai.providers.anthropic import AnthropicProvider


@pytest.fixture
def provider(hass) -> AnthropicProvider:
    return AnthropicProvider(hass, api_key="sk-ant-test", model="claude-sonnet-4-6")


class TestAnthropicTextExtraction:
    def test_single_text_block(self, provider: AnthropicProvider) -> None:
        data = {"content": [{"type": "text", "text": "Three lights are on."}]}
        assert provider.extract_text_response(data) == "Three lights are on."

    def test_joins_multiple_text_blocks(self, provider: AnthropicProvider) -> None:
        # Taking only the first block silently truncates the answer.
        data = {
            "content": [
                {"type": "text", "text": "Three lights are on: "},
                {"type": "text", "text": "kitchen, hall, porch."},
            ]
        }
        assert provider.extract_text_response(data) == "Three lights are on: kitchen, hall, porch."

    def test_skips_thinking_and_tool_use_blocks(self, provider: AnthropicProvider) -> None:
        data = {
            "content": [
                {"type": "thinking", "thinking": "counting the lights"},
                {"type": "text", "text": "Three."},
                {"type": "tool_use", "id": "tu_1", "name": "list_entities", "input": {}},
            ]
        }
        assert provider.extract_text_response(data) == "Three."

    def test_no_text_block_returns_none(self, provider: AnthropicProvider) -> None:
        # A tool-only turn has no prose; the caller distinguishes that from
        # an empty string.
        data = {"content": [{"type": "tool_use", "id": "tu_1", "name": "x", "input": {}}]}
        assert provider.extract_text_response(data) is None
        assert provider.extract_text_response({}) is None


class TestAnthropicHeaders:
    def test_sends_key_and_api_version(self, provider: AnthropicProvider) -> None:
        headers = provider._get_headers()
        assert headers["x-api-key"] == "sk-ant-test"
        assert headers["anthropic-version"] == ANTHROPIC_API_VERSION

    def test_omits_auth_headers_without_a_key(self, hass) -> None:
        headers = AnthropicProvider(hass, api_key="")._get_headers()
        assert "x-api-key" not in headers
        assert "anthropic-version" not in headers


class TestAnthropicUsage:
    def test_reads_cache_token_counts(self, provider: AnthropicProvider) -> None:
        data = {
            "usage": {
                "input_tokens": 10,
                "output_tokens": 20,
                "cache_creation_input_tokens": 300,
                "cache_read_input_tokens": 400,
            }
        }
        assert provider.extract_usage(data) == {
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_creation_input_tokens": 300,
            "cache_read_input_tokens": 400,
        }

    def test_no_usage_block_returns_none(self, provider: AnthropicProvider) -> None:
        assert provider.extract_usage({}) is None
