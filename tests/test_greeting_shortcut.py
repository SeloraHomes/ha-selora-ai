"""Pure-greeting short-circuit in the chat handlers.

The model consistently ignored the small-talk rule in the system prompt
and replied to "hello" with a status dump of automations. The chat path
now intercepts a narrow class of greeting/thanks-only messages and
returns a canned reply without calling the provider — these tests pin
that behaviour: greetings must short-circuit, real requests must not.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.selora_ai.llm_client import LLMClient
from custom_components.selora_ai.llm_client.intent import (
    _classify_chat_intent,
    _is_identity_question,
    _is_pure_greeting,
)
from custom_components.selora_ai.providers import create_provider


@pytest.fixture(autouse=True)
def _enable_custom_component(enable_custom_integrations):
    """Auto-enable custom integrations so our domain is discoverable."""


def _make_client(hass) -> LLMClient:
    provider = create_provider("anthropic", hass, api_key="test-key")
    return LLMClient(hass, provider)


class TestIsPureGreeting:
    """Detector boundary cases — must not eat real requests."""

    @pytest.mark.parametrize(
        "message",
        [
            "hi",
            "hello",
            "hey",
            "yo",
            "sup",
            "thanks",
            "thank you",
            "thx",
            "cheers",
            "good morning",
            "good evening",
            "good night",
            "good afternoon",
            "Hello",  # case-insensitive
            "HELLO",
            "hi!",
            "hello!!!",
            "thanks!",
            "thank you :)",  # punctuation tail
            "   hello   ",  # whitespace tail
            "thanks 🙏",  # emoji tail
            "good morning ☀️",
            # Vocative — addressing the assistant by name. Without this
            # the LLM hallucinates an automation update from prior turns.
            "Hello Selora AI",
            "hi selora",
            "hey selora ai",
            "thanks selora",
            "thank you AI",
            "good morning selora!",
            "hello assistant",
        ],
    )
    def test_recognises_pure_greetings(self, message: str) -> None:
        assert _is_pure_greeting(message) is True

    @pytest.mark.parametrize(
        "message",
        [
            "",
            "   ",
            "hi there",  # trailing word — route to LLM
            "hello, can you turn off the lights?",
            "thanks for the help, can you also disable arrival reminder?",
            "turn on the kitchen light",
            "what's the temperature in the living room?",
            "list my automations",
            "good morning, please open the blinds",
            "hellomate",  # no word boundary
            "hithere",
            # Long messages are never short-circuited even if they start
            # with a greeting word — keep the LLM in the loop for anything
            # that might carry an actual request.
            "hello this is a long message asking you to do something specific now",
        ],
    )
    def test_rejects_non_pure_greetings(self, message: str) -> None:
        assert _is_pure_greeting(message) is False


class TestIsIdentityQuestion:
    """Identity/capability questions route to the answer specialist.

    "what are you?" etc. were classified as clarification and sent to the
    command/automation LoRA, which recited its role and (with
    repeat_penalty pinned at 1.0 to keep JSON in-distribution) looped on
    it. Detecting them lets the classifier route to the answer
    specialist instead — short-format, streamed — so any residual
    repetition is visible rather than stalling the JSON path. Must not
    eat real requests that merely contain "you".
    """

    @pytest.mark.parametrize(
        "message",
        [
            "what are you?",
            "what are you",
            "who are you?",
            "WHAT ARE YOU",
            "what can you do?",
            "what do you do",
            "what's your name",
            "what is selora",
            "tell me about yourself",
            "introduce yourself",
            "describe selora",
            "what are your capabilities",
            "who r u",
            "so what are you?",
            "what does selora do",
            "how does selora work?",
            "are you selora?",
        ],
    )
    def test_recognises_identity_questions(self, message: str) -> None:
        assert _is_identity_question(message) is True

    @pytest.mark.parametrize(
        "message",
        [
            "",
            "   ",
            "turn off the kitchen light",
            "can you turn off the kitchen light?",
            "what's the outdoor temperature?",
            "create an automation to turn on the porch light at sunset",
            "turn off the light",
            "what are you going to do about the porch light?",
            "what lights are on?",
            "is the front door locked?",
        ],
    )
    def test_rejects_non_identity_questions(self, message: str) -> None:
        assert _is_identity_question(message) is False


class TestArchitectChatShortcut:
    """``architect_chat`` returns the canned reply without provider calls."""

    async def test_pure_greeting_skips_provider(self, hass) -> None:
        client = _make_client(hass)
        client._provider.send_request = AsyncMock(
            side_effect=AssertionError("provider must not be called for pure greetings")
        )
        result = await client.architect_chat("hello", entities=[])
        assert result["intent"] == "answer"
        assert "help" in result["response"].lower()
        client._provider.send_request.assert_not_called()

    async def test_substantive_message_calls_provider(self, hass) -> None:
        client = _make_client(hass)
        client._provider.send_request = AsyncMock(
            return_value=('{"intent": "answer", "response": "ok"}', None)
        )
        await client.architect_chat("turn on the kitchen light", entities=[])
        client._provider.send_request.assert_called_once()

    async def test_thanks_skips_provider(self, hass) -> None:
        client = _make_client(hass)
        client._provider.send_request = AsyncMock(
            side_effect=AssertionError("provider must not be called for thanks")
        )
        result = await client.architect_chat("thanks!", entities=[])
        assert result["intent"] == "answer"
        client._provider.send_request.assert_not_called()

    async def test_identity_question_calls_provider(self, hass) -> None:
        # Identity questions now route to the answer specialist; the model
        # owns the reply (no canned short-circuit), so the provider runs.
        client = _make_client(hass)
        client._provider.send_request = AsyncMock(
            return_value=('{"intent": "answer", "response": "ok"}', None)
        )
        await client.architect_chat("what are you?", entities=[])
        client._provider.send_request.assert_called_once()


class TestArchitectChatStreamShortcut:
    """``architect_chat_stream`` yields the canned reply without provider calls."""

    async def test_pure_greeting_yields_canned_reply(self, hass) -> None:
        client = _make_client(hass)
        client._provider.send_request_stream = MagicMock(
            side_effect=AssertionError("provider must not stream for pure greetings")
        )
        client._provider.raw_request_stream = MagicMock(
            side_effect=AssertionError("provider must not stream for pure greetings")
        )

        chunks: list[str] = []
        async for chunk in client.architect_chat_stream("hi", entities=[]):
            chunks.append(chunk)

        assert chunks == ["Hi! What can I help with?"]

    async def test_substantive_message_streams_from_provider(self, hass) -> None:
        client = _make_client(hass)

        async def _fake_stream(*_a, **_kw):
            yield "real "
            yield "answer"

        client._provider.send_request_stream = _fake_stream

        # Disable tool path by forcing tool_executor=None and is_low_context=True
        # so the stream skips the architect path's heavier setup.
        type(client._provider).is_low_context = property(lambda self: True)
        try:
            chunks: list[str] = []
            async for chunk in client.architect_chat_stream(
                "turn on the kitchen light", entities=[]
            ):
                chunks.append(chunk)
            assert "".join(chunks) == "real answer"
        finally:
            # Restore default attribute access on the class.
            del type(client._provider).is_low_context



class TestClassifyChatIntent:
    """Low-context intent routing for the LoRA specialists."""

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("what are you?", "answer"),
            ("what can you do?", "answer"),
            ("who are you", "answer"),
            ("can you turn off the kitchen light", "command"),
            ("could you please open the garage", "command"),
            ("would you set the thermostat to 21", "command"),
            # "should I/you/we …" stays on the answer path: advisory
            # questions ("should I turn off the heater?") must not
            # trigger device actions via the command specialist.
            ("should I turn off the heater?", "answer"),
            ("should we lock the front door?", "answer"),
            ("hey can you turn off the bedroom light", "command"),
            ("turn off the kitchen light", "command"),
            ("turn on the porch light at sunset", "command"),
            ("what lights can I turn on", "answer"),
            ("what lights are on?", "answer"),
            ("is the garage door open?", "answer"),
            ("every day at sunset turn on the porch light", "automation"),
            ("when the garage door opens, turn on the kitchen light", "automation"),
            ("remind me to turn off the coffee maker at 9pm", "automation"),
            ("hello", "answer"),
            ("thanks", "answer"),
        ],
    )
    def test_routes_to_expected_specialist(self, message: str, expected: str) -> None:
        assert _classify_chat_intent(message) == expected
