"""A streamed reply the backend stopped mid-block.

The stream closes cleanly when a backend hits its output cap, so nothing in
the text says the turn is unfinished — and an unterminated ```automation
matches neither the fenced pattern nor the bare-block salvage, so it used to
fall through to the prose path. The user got the model's own "Updates **Eco
Away** so the heat pump uses 15°…" above half a JSON object: a confirmation
of an automation that was never written.
"""

from __future__ import annotations

import pytest

from custom_components.selora_ai.llm_client.client import LLMClient

# The reply from the bug report, cut where the stream ended, with the entity
# tile marker the synthesizer appends after it.
CUT_OFF_REFINEMENT = (
    "Updates **Eco Away** so the heat pump uses 15° when Philippe is away "
    "and 21° when he is home.\n\n"
    "```automation\n"
    "{\n"
    '  "refine_automation_id": "selora_ai_e99e4d0f",\n'
    '  "alias": "Eco Away",\n'
    '  "id": "selora_ai_e99e4d0f",\n'
    '  "mode": "single",\n'
    '  "triggers": [\n'
    "    {\n"
    '      "entity_id": "person.philippe",\n'
    '      "from": "home",\n'
    '      "platform": "state",\n'
    '      "to": "not_home"\n'
    "    },\n"
    "    {\n"
    '      "entity_id'
    "\n\n[[entities:automation.eco_away,climate.heatpump,person.philippe]]"
)


@pytest.fixture
def client(hass):
    from custom_components.selora_ai.providers import create_provider

    provider = create_provider("anthropic", hass, api_key="test-key")
    return LLMClient(hass, provider=provider)


def test_cut_off_proposal_is_not_reported_as_an_answer(hass, client) -> None:
    result = client.parse_streamed_response(
        CUT_OFF_REFINEMENT,
        user_message="Set temp to 15 when I'm away, and 21 when I'm home",
        refining=True,
    )
    assert result["validation_error"] == "truncated_response"
    # Not "automation": the retry loop corrects a REJECTED payload, and this
    # turn has no payload to correct.
    assert result["validation_target"] == "response"
    assert result.get("automation") is None


def test_the_optimistic_prose_does_not_survive(hass, client) -> None:
    """The prose already described the automation as written. Kept, it would
    both claim and deny the same thing in one bubble."""
    result = client.parse_streamed_response(CUT_OFF_REFINEMENT, refining=True)
    assert "15°" not in result["response"]
    assert "cut off" in result["response"]


def test_no_json_or_marker_leaks_into_the_bubble(hass, client) -> None:
    result = client.parse_streamed_response(CUT_OFF_REFINEMENT, refining=True)
    assert "refine_automation_id" not in result["response"]
    assert "```" not in result["response"]
    assert "[[entities:" not in result["response"]


def test_the_notice_follows_the_turn_language(hass, client) -> None:
    result = client.parse_streamed_response(CUT_OFF_REFINEMENT, language="fr")
    assert "coupée" in result["response"]
    # An unknown locale falls back to English rather than an empty bubble.
    fallback = client.parse_streamed_response(CUT_OFF_REFINEMENT, language="xx")
    assert "cut off" in fallback["response"]


def _reason_for(client, finish_reason: str | None) -> str:
    """Parse the cut-off reply with the provider reporting *finish_reason*.

    Set on the provider rather than passed in, so the test covers the wiring
    that actually carries it: the round records it, and the parse reads it
    back off the same provider.
    """
    client._provider._note_finish_reason(finish_reason)
    return client.parse_streamed_response(CUT_OFF_REFINEMENT)["truncation_reason"]


def test_the_reason_is_carried_to_the_panel(hass, client) -> None:
    """"It was cut off" describes what the user just watched. Which of the two
    causes it was is the part they cannot see, and only this side knows it."""
    # OpenAI's spelling, then Anthropic's and Gemini's for the same thing.
    assert _reason_for(client, "length") == "output_cap"
    assert _reason_for(client, "max_tokens") == "output_cap"
    assert _reason_for(client, "MAX_TOKENS") == "output_cap"


def test_a_backend_that_said_nothing_is_reported_as_such(hass, client) -> None:
    """Not a missing answer — a stream that ends with no terminal event is
    what a relay dropping the tail looks like from here, and that is a
    different problem from the model running out of room."""
    assert _reason_for(client, None) == "unreported"
    assert _reason_for(client, "stop") == "unreported"


def test_a_complete_proposal_is_untouched(hass, client) -> None:
    hass.states.async_set("binary_sensor.leak", "off")
    hass.states.async_set("light.kitchen", "off")
    text = (
        "Here you go.\n\n"
        "```automation\n"
        "{\n"
        '  "alias": "Leak Alert",\n'
        '  "triggers": [{"platform": "state", "entity_id": "binary_sensor.leak", '
        '"to": "on"}],\n'
        '  "conditions": [],\n'
        '  "actions": [{"service": "light.turn_on", '
        '"target": {"entity_id": "light.kitchen"}}]\n'
        "}\n"
        "```"
    )
    result = client.parse_streamed_response(text, refining=True)
    assert result["intent"] == "automation"
    assert result.get("validation_error") is None


def test_a_stray_closing_fence_is_not_truncation(hass, client) -> None:
    """The bare-block salvage's own shape: the OPENING fence is missing, so
    the single fence in the reply is the closer of a body that did arrive."""
    hass.states.async_set("binary_sensor.leak", "off")
    hass.states.async_set("light.kitchen", "off")
    text = (
        "Here you go.\n\n"
        "automation\n"
        "{\n"
        '  "alias": "Leak Alert",\n'
        '  "triggers": [{"platform": "state", "entity_id": "binary_sensor.leak", '
        '"to": "on"}],\n'
        '  "actions": [{"service": "light.turn_on", '
        '"target": {"entity_id": "light.kitchen"}}]\n'
        "}\n"
        "```\n\n"
        "Adjust the entity as you like."
    )
    result = client.parse_streamed_response(
        text, user_message="Create an automation for the leak sensor"
    )
    assert result["intent"] == "automation"


def test_prose_with_no_blocks_is_untouched(hass, client) -> None:
    result = client.parse_streamed_response("The kitchen light is on.")
    assert result["intent"] == "answer"
    assert result["response"] == "The kitchen light is on."
    assert result.get("validation_error") is None
