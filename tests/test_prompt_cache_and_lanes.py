"""Cloud turns get the whole tool schema, and the cached prefix holds no state.

The lanes traded correctness for ~4k tokens: three regexes decided what the
model could see, and when they were wrong the model reported the capability did
not exist rather than answering worse. A schema that never varies also caches,
which the lanes prevented.

The caching half has one safety property worth more than the saving: the cached
prefix must never contain the home's entity states, or a hit replays a reading
of the house as it was minutes ago.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.selora_ai.llm_client.client import LLMClient
from custom_components.selora_ai.tool_registry import CHAT_TOOLS


def _client(*, holds_schema: bool = True, low_context: bool = False) -> MagicMock:
    """A provider stand-in.

    Every attribute the code reads is set explicitly: a MagicMock attribute is
    truthy and comparable, so a partial stub answers every question "yes", which
    is how the bounded-local case went untested the first time.
    """
    client = MagicMock(spec=LLMClient)
    client._provider = MagicMock(holds_full_tool_schema=holds_schema, is_low_context=low_context)
    client._area_names = lambda: []
    return client


# ── Lanes ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "Create a new office area",
        "turn on the kitchen lights",
        "assign the living room lights to the Living Room",
        "what's on my dashboard",
    ],
)
def test_a_cloud_turn_gets_every_tool(message: str) -> None:
    """No regex decides what a frontier model may see. When one was wrong the
    model reported the capability did not exist."""
    assert (
        LLMClient._cloud_intent_hint(_client(low_context=False), message, None, refining=False)
        is None
    )


def test_a_low_context_turn_still_gets_a_lane() -> None:
    """Selora AI Local has a real context ceiling — that constraint is genuine
    and the lanes are how it is met."""
    hint = LLMClient._cloud_intent_hint(
        _client(holds_schema=False, low_context=True),
        "turn on the kitchen lights",
        None,
        refining=False,
    )
    assert hint == "command"


@pytest.mark.parametrize(
    ("window", "expected"),
    [
        # Ollama serves whatever window the runtime was started with, and the
        # toolset alone is ~9.7K.
        (4096, False),
        (8192, False),
        (16384, False),
        # None means UNKNOWN, and LLMProvider is explicit that unknown keeps the
        # conservative behaviour — reading it as "fits" gets it backwards.
        (None, False),
        (32768, True),
        (131072, True),
    ],
)
def test_an_unknown_window_never_counts_as_room(window: int | None, expected: bool) -> None:
    """The default answer for any provider that has not claimed a catalogue."""
    from custom_components.selora_ai.providers.base import LLMProvider

    provider = MagicMock(context_window=window)
    assert LLMProvider.holds_full_tool_schema.fget(provider) is expected


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        # Named in review: a vendor prefix is not capacity. Both sit under
        # vendors whose flagships are huge and both are smaller than the ~9.7K
        # schema alone.
        ("openai/gpt-4", False),
        ("google/gemma-2-9b-it", False),
        ("openai/gpt-3.5-turbo", False),
        ("openai/gpt-4-turbo", True),
        ("meta-llama/llama-3-8b-instruct", False),
        ("some-vendor/experimental-4k", False),
        ("anthropic/claude-sonnet-4.5", True),
        ("openai/gpt-5.4", True),
        ("openai/gpt-4o-mini", True),
        ("google/gemini-2.5-flash", True),
    ],
)
def test_openrouter_capacity_is_per_model_not_per_vendor(model: str, expected: bool) -> None:
    from custom_components.selora_ai.providers.openrouter import OpenRouterProvider

    provider = OpenRouterProvider.__new__(OpenRouterProvider)
    provider._model = model
    assert OpenRouterProvider.holds_full_tool_schema.fget(provider) is expected


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        # Still selectable, and all smaller than the schema alone.
        ("gpt-4", False),
        ("gpt-4-32k", False),
        ("gpt-4-0613", False),
        ("gpt-3.5-turbo", False),
        # 128K, and the prefix must reach its variants without touching the
        # 8K ids above — one hyphen separates the two families.
        ("gpt-4-turbo", True),
        ("gpt-4-turbo-preview", True),
        ("gpt-4-turbo-2024-04-09", True),
        ("gpt-4o", True),
        ("gpt-4o-mini", True),
        ("gpt-4.1", True),
        ("gpt-5.4", True),
        ("o1-mini", True),
        ("o3-mini", True),
    ],
)
def test_openai_capacity_is_per_model(model: str, expected: bool) -> None:
    """The model field is free-form; a blanket yes made a gpt-4 request fail
    outright rather than fall back to a lane."""
    from custom_components.selora_ai.providers.openai import OpenAIProvider

    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider._model = model
    assert OpenAIProvider.holds_full_tool_schema.fget(provider) is expected


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        # 1.0 Pro served exactly the 32K threshold — no margin for the prompt.
        ("gemini-1.0-pro", False),
        ("gemini-pro", False),
        ("gemini-1.5-pro", True),
        ("gemini-2.5-flash", True),
    ],
)
def test_gemini_capacity_is_per_model(model: str, expected: bool) -> None:
    from custom_components.selora_ai.providers.gemini import GeminiProvider

    provider = GeminiProvider.__new__(GeminiProvider)
    provider._model = model
    assert GeminiProvider.holds_full_tool_schema.fget(provider) is expected


def test_an_unrecognised_model_keeps_its_lane() -> None:
    """The allowlist direction: unknown is never "it fits"."""
    from custom_components.selora_ai.providers.base import model_is_known_large

    assert model_is_known_large("something-new-2026") is False
    assert model_is_known_large("") is False


def test_anthropic_may_answer_by_catalogue() -> None:
    """The one provider where every model ever shipped is ≥100K."""
    from custom_components.selora_ai.providers.anthropic import AnthropicProvider

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider._model = "claude-3-haiku"
    assert AnthropicProvider.holds_full_tool_schema.fget(provider) is True


# ── Caching ─────────────────────────────────────────────────────────────────


def _anthropic_payload(system: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    from custom_components.selora_ai.providers.anthropic import AnthropicProvider

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider._model = "claude-sonnet-4-6"
    return AnthropicProvider.build_payload(provider, system, messages, tools=[{"name": "x"}])


def test_anthropic_marks_the_system_prompt_cacheable() -> None:
    payload = _anthropic_payload("rules", [{"role": "user", "content": "hi"}])

    assert payload["system"] == [
        {"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}}
    ]


def test_the_cached_prefix_never_holds_entity_state() -> None:
    """The safety property. Entity states ride in the current turn's user
    message; a cache hit must replay instructions, never a stale reading of the
    house."""
    system = "You are an assistant. Follow the rules."
    messages = [{"role": "user", "content": "CURRENT STATE: light.kitchen is on at 71%"}]
    payload = _anthropic_payload(system, messages)

    cached = json.dumps(payload["system"]) + json.dumps(payload.get("tools", []))
    assert "light.kitchen" not in cached
    assert "71%" not in cached
    # And it is still there for the model to read, uncached.
    assert "light.kitchen" in json.dumps(payload["messages"])


def _openrouter_payload(model: str) -> dict[str, Any]:
    from custom_components.selora_ai.providers.openrouter import OpenRouterProvider

    provider = OpenRouterProvider.__new__(OpenRouterProvider)
    provider._model = model
    provider._api_key = "k"
    return OpenRouterProvider.build_payload(
        provider, "rules", [{"role": "user", "content": "light.kitchen is on"}]
    )


def test_openrouter_caches_the_system_prompt_on_anthropic_models() -> None:
    payload = _openrouter_payload("anthropic/claude-sonnet-4.5")
    system = payload["messages"][0]

    assert system["role"] == "system"
    assert system["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_openrouter_leaves_other_upstreams_alone() -> None:
    """cache_control is an Anthropic extension; another upstream would be handed
    a key it does not understand."""
    payload = _openrouter_payload("meta-llama/llama-4")

    assert payload["messages"][0]["content"] == "rules"


def test_openrouter_never_caches_the_turn_that_carries_state() -> None:
    payload = _openrouter_payload("anthropic/claude-sonnet-4.5")
    user_turn = payload["messages"][-1]

    assert "light.kitchen" in json.dumps(user_turn)
    assert "cache_control" not in json.dumps(user_turn)
