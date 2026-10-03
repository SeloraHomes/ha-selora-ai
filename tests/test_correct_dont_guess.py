"""A cloud model is corrected from ground truth; only the local model is guessed for.

The prompt-aware repairs in ``parsers`` rewrite a rejected automation from the
user's wording — an unknown entity swapped for one sharing a word with the
prompt, a trigger rebuilt from "for 10 minutes". They were written for the
1.7B local model, which gets no correction round. A model that does is handed
the validation error and the real candidates instead.
"""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai.llm_client import LLMClient
from custom_components.selora_ai.llm_client.parsers import parse_streamed_response
from custom_components.selora_ai.providers import create_provider

_WRONG_DOMAIN = {
    "alias": "Coffee",
    "triggers": [{"trigger": "time", "at": "07:00:00"}],
    "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.coffee_maker"}}],
}


def _text() -> str:
    return "Here it is.\n```automation\n" + json.dumps(_WRONG_DOMAIN) + "\n```"


def _entities() -> list[dict]:
    return [
        {
            "entity_id": "switch.coffee_maker",
            "state": "off",
            "attributes": {"friendly_name": "Coffee Maker"},
        }
    ]


async def test_a_cloud_turn_gets_the_error_back(hass: HomeAssistant) -> None:
    hass.states.async_set("switch.coffee_maker", "off", {"friendly_name": "Coffee Maker"})

    result = parse_streamed_response(
        _text(), hass, _entities(), user_message="make coffee at 7", guess_repairs=False
    )

    assert "unknown entity_id" in result["validation_error"]
    assert (
        result["rejected_automation"]["actions"][0]["target"]["entity_id"] == "light.coffee_maker"
    )


async def test_the_local_model_keeps_its_repair(hass: HomeAssistant) -> None:
    hass.states.async_set("switch.coffee_maker", "off", {"friendly_name": "Coffee Maker"})

    result = parse_streamed_response(
        _text(), hass, _entities(), user_message="make coffee at 7", guess_repairs=True
    )

    assert not result.get("validation_error")
    assert result["automation"]["actions"][0]["target"]["entity_id"] == "switch.coffee_maker"


@pytest.mark.parametrize(
    ("provider", "kwargs", "guesses"),
    [
        ("selora_local", {"host": "http://localhost:8080"}, True),
        ("anthropic", {"api_key": "k"}, False),
        ("openai", {"api_key": "k", "model": "gpt-5.4"}, False),
        ("ollama", {"host": "http://localhost:11434", "model": "llama4"}, False),
    ],
)
def test_only_the_low_context_model_is_guessed_for(
    hass: HomeAssistant, provider: str, kwargs: dict, guesses: bool
) -> None:
    client = LLMClient(hass, create_provider(provider, hass, **kwargs))
    assert client.guesses_repairs is guesses


async def test_the_json_fallback_keeps_the_policy(hass: HomeAssistant) -> None:
    """A streamed reply in the older JSON-only shape falls back to the other
    parser; the cloud policy must go with it, or the guessing comes back."""
    hass.states.async_set("switch.coffee_maker", "off", {"friendly_name": "Coffee Maker"})
    text = json.dumps({"intent": "automation", "response": "Here.", "automation": _WRONG_DOMAIN})

    result = parse_streamed_response(
        text, hass, _entities(), user_message="make coffee at 7", guess_repairs=False
    )

    assert "unknown entity_id" in result.get("validation_error", "")
