"""Tests for the Google Gemini LLM provider."""

from __future__ import annotations

import json

import pytest

from custom_components.selora_ai.const import DEFAULT_GEMINI_HOST
from custom_components.selora_ai.providers.gemini import GeminiProvider


@pytest.fixture
def provider(hass):
    """Create a GeminiProvider with test credentials."""
    return GeminiProvider(hass, api_key="test-key", model="gemini-2.5-flash")


class TestGeminiIdentity:
    def test_provider_name(self, provider) -> None:
        assert provider.provider_name == "Google Gemini (gemini-2.5-flash)"

    def test_requires_api_key(self, provider) -> None:
        assert provider.requires_api_key is True

    def test_has_api_key(self, provider) -> None:
        assert provider.has_api_key is True

    def test_model(self, provider) -> None:
        assert provider.model == "gemini-2.5-flash"


class TestGeminiEndpoints:
    def test_endpoint_includes_model_and_key(self, provider) -> None:
        assert "/models/gemini-2.5-flash:generateContent" in provider._endpoint
        assert "key=test-key" in provider._endpoint

    def test_stream_endpoint(self, provider) -> None:
        assert "/models/gemini-2.5-flash:streamGenerateContent" in provider._stream_endpoint
        assert "key=test-key" in provider._stream_endpoint
        assert "alt=sse" in provider._stream_endpoint

    def test_endpoints_use_native_api(self, provider) -> None:
        assert DEFAULT_GEMINI_HOST in provider._endpoint
        assert "/openai/" not in provider._endpoint


class TestGeminiHeaders:
    def test_no_authorization_header(self, provider) -> None:
        headers = provider._get_headers()
        assert "Authorization" not in headers
        assert headers["Content-Type"] == "application/json"


class TestGeminiPayload:
    def test_basic_payload(self, provider) -> None:
        payload = provider.build_payload(
            system="Be helpful.",
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert payload["systemInstruction"] == {"parts": [{"text": "Be helpful."}]}
        assert len(payload["contents"]) == 1
        assert payload["contents"][0]["role"] == "user"
        assert payload["contents"][0]["parts"] == [{"text": "Hello"}]

    def test_no_system_instruction_when_empty(self, provider) -> None:
        payload = provider.build_payload(
            system="",
            messages=[{"role": "user", "content": "Hi"}],
        )
        assert "systemInstruction" not in payload

    def test_assistant_role_mapped_to_model(self, provider) -> None:
        payload = provider.build_payload(
            system="",
            messages=[
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello!"},
                {"role": "user", "content": "How are you?"},
            ],
        )
        assert payload["contents"][0]["role"] == "user"
        assert payload["contents"][1]["role"] == "model"
        assert payload["contents"][2]["role"] == "user"

    def test_tools_wrapped_in_function_declarations(self, provider) -> None:
        tools = [
            {"name": "get_time", "description": "Get current time", "parameters": {}},
            {"name": "get_weather", "description": "Get weather", "parameters": {}},
        ]
        payload = provider.build_payload(
            system="",
            messages=[{"role": "user", "content": "Hi"}],
            tools=tools,
        )
        assert len(payload["tools"]) == 1
        assert payload["tools"][0]["functionDeclarations"] == tools


class TestGeminiResponseParsing:
    def test_extract_text_response(self, provider) -> None:
        data = {
            "candidates": [{
                "content": {
                    "parts": [{"text": "Hello there!"}],
                    "role": "model",
                }
            }]
        }
        assert provider.extract_text_response(data) == "Hello there!"

    def test_extract_text_multi_part(self, provider) -> None:
        data = {
            "candidates": [{
                "content": {
                    "parts": [{"text": "Part 1"}, {"text": " Part 2"}],
                    "role": "model",
                }
            }]
        }
        assert provider.extract_text_response(data) == "Part 1 Part 2"

    def test_extract_text_empty_candidates(self, provider) -> None:
        assert provider.extract_text_response({"candidates": []}) is None
        assert provider.extract_text_response({}) is None

    def test_extract_tool_calls(self, provider) -> None:
        data = {
            "candidates": [{
                "content": {
                    "parts": [{
                        "functionCall": {
                            "name": "get_time",
                            "args": {"timezone": "UTC"},
                        }
                    }],
                    "role": "model",
                }
            }]
        }
        calls = provider.extract_tool_calls(data)
        assert len(calls) == 1
        assert calls[0]["name"] == "get_time"
        assert calls[0]["arguments"] == {"timezone": "UTC"}
        assert calls[0]["id"] == "call_0"

    def test_extract_tool_calls_empty(self, provider) -> None:
        data = {
            "candidates": [{
                "content": {
                    "parts": [{"text": "No tools needed."}],
                    "role": "model",
                }
            }]
        }
        assert provider.extract_tool_calls(data) == []


class TestGeminiStreamParsing:
    def test_parse_stream_line_text(self, provider) -> None:
        event = {
            "candidates": [{
                "content": {
                    "parts": [{"text": "streaming text"}],
                    "role": "model",
                }
            }]
        }
        line = f"data: {json.dumps(event)}"
        assert provider.parse_stream_line(line) == "streaming text"

    def test_parse_stream_line_non_data(self, provider) -> None:
        assert provider.parse_stream_line("event: message") is None

    def test_parse_stream_line_no_text(self, provider) -> None:
        event = {
            "candidates": [{
                "content": {
                    "parts": [{"functionCall": {"name": "test", "args": {}}}],
                    "role": "model",
                }
            }]
        }
        line = f"data: {json.dumps(event)}"
        assert provider.parse_stream_line(line) is None

    def test_parse_stream_line_invalid_json(self, provider) -> None:
        assert provider.parse_stream_line("data: not-json") is None


class TestGeminiToolFormatting:
    def test_format_tool(self, provider) -> None:
        from custom_components.selora_ai.tool_registry import ToolDef, ToolParam

        tool = ToolDef(
            name="test_tool",
            description="A test tool",
            params=(
                ToolParam(name="arg1", type="string", description="First arg", required=True),
            ),
        )
        formatted = provider.format_tool(tool)
        assert formatted["name"] == "test_tool"
        assert formatted["description"] == "A test tool"
        assert "arg1" in formatted["parameters"]["properties"]


class TestGeminiMessageConversion:
    def test_tool_responses_in_messages(self, provider) -> None:
        messages = [
            {"role": "user", "content": "What time is it?"},
            {
                "role": "user",
                "_tool_responses": [{"name": "get_time", "result": {"time": "12:00"}}],
            },
        ]
        _sys, contents = GeminiProvider._to_gemini_messages("", messages)
        # Second message should have a functionResponse part
        assert any(
            "functionResponse" in p
            for p in contents[1]["parts"]
        )

    def test_tool_calls_in_messages(self, provider) -> None:
        messages = [
            {
                "role": "assistant",
                "_tool_calls": [{"name": "get_time", "arguments": {}}],
            },
        ]
        _sys, contents = GeminiProvider._to_gemini_messages("", messages)
        assert contents[0]["role"] == "model"
        assert any(
            "functionCall" in p
            for p in contents[0]["parts"]
        )

    def test_append_tool_result(self, provider) -> None:
        messages = []
        response_data = {
            "candidates": [{
                "content": {
                    "parts": [{"functionCall": {"name": "get_time", "args": {}}}],
                    "role": "model",
                }
            }]
        }
        tool_call = {"id": "call_0", "name": "get_time", "arguments": {}}
        result = {"time": "12:00"}
        provider.append_tool_result(messages, response_data, tool_call, result)
        assert len(messages) == 2
        assert messages[0]["role"] == "assistant"
        assert messages[1]["role"] == "user"
        assert messages[1]["_tool_responses"][0]["name"] == "get_time"

    def test_append_streaming_tool_results(self, provider) -> None:
        messages = []
        tool_calls = [
            {"id": "call_0", "name": "get_time", "arguments": {}},
        ]
        results = [{"time": "12:00"}]
        provider.append_streaming_tool_results(messages, [], tool_calls, results)
        assert len(messages) == 2
        assert messages[0]["_tool_calls"][0]["name"] == "get_time"
        assert messages[1]["_tool_responses"][0]["result"] == {"time": "12:00"}
