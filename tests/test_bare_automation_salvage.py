"""Backend salvage for fence-less automation / scene blocks.

Some models drop the opening ``` fence and emit a bare
``automation\n{...}`` block. Without the salvage, the backend
extractor never matches, no proposal card builds, and the raw JSON
leaks into the chat bubble. These tests pin the extractor's recovery
path so the proposal still builds.
"""

from __future__ import annotations

import pytest

from custom_components.selora_ai.llm_client.client import LLMClient


@pytest.fixture
def client(hass):
    from custom_components.selora_ai.providers import create_provider

    provider = create_provider("anthropic", hass, api_key="test-key")
    return LLMClient(hass, provider=provider)


def test_bare_automation_block_without_opening_fence(hass, client) -> None:
    """Model dropped the ```automation opener — body has a closing ``` only."""
    hass.states.async_set("binary_sensor.leak", "off")
    hass.states.async_set("light.kitchen", "off")
    text = (
        "Setting up the alert.\n\n"
        "automation\n"
        "{\n"
        '  "alias": "Leak Alert",\n'
        '  "description": "",\n'
        '  "triggers": [{"platform": "state", "entity_id": "binary_sensor.leak", "to": "on"}],\n'
        '  "conditions": [],\n'
        '  "actions": [{"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}}]\n'
        "}\n"
        "```"
    )
    result = client.parse_streamed_response(text)
    assert result["intent"] == "automation"
    assert result["automation"]["alias"] == "Leak Alert"
    # Raw JSON must not survive in the prose.
    assert '"alias"' not in result["response"]
    assert "automation\n{" not in result["response"]
    # Leading prose must survive.
    assert "Setting up the alert" in result["response"]


def test_bare_automation_block_without_any_fence(hass, client) -> None:
    """Model dropped both fences — pure prose + bare JSON block at end."""
    hass.states.async_set("binary_sensor.leak", "off")
    hass.states.async_set("light.kitchen", "off")
    text = (
        "Here you go.\n\n"
        "automation\n"
        "{\n"
        '  "alias": "Leak",\n'
        '  "triggers": [{"platform": "state", "entity_id": "binary_sensor.leak", "to": "on"}],\n'
        '  "conditions": [],\n'
        '  "actions": [{"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}}]\n'
        "}"
    )
    result = client.parse_streamed_response(text)
    assert result["intent"] == "automation"
    assert result["automation"]["alias"] == "Leak"
    assert '"alias"' not in result["response"]


def test_bare_automation_block_with_trailing_prose(hass, client) -> None:
    """Model emits a trailing summary after the block — card must still build.

    Real-world failure: the architect dropped the opening ```, emitted the
    bare ``automation\n{...}\n``` `` block, then a prose summary
    ("This automation monitors all 5 leak detectors…"). The old ``\\Z``
    anchor missed it, so the proposal card never built and the raw JSON
    leaked into the streamed bubble.
    """
    hass.states.async_set("binary_sensor.leak", "off")
    hass.states.async_set("light.kitchen", "off")
    text = (
        "I found your sensors.\n\n"
        "automation\n"
        "{\n"
        '  "alias": "Water Leak Alert",\n'
        '  "triggers": [{"platform": "state", "entity_id": "binary_sensor.leak", "to": "on"}],\n'
        '  "conditions": [],\n'
        '  "actions": [{"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}}]\n'
        "}\n"
        "```\n\n"
        "This automation monitors all your leak detectors and notifies you."
    )
    result = client.parse_streamed_response(
        text,
        user_message="Create an automation that notifies me when any water leak sensor turns on",
    )
    assert result["intent"] == "automation"
    assert result["automation"]["alias"] == "Water Leak Alert"
    assert '"alias"' not in result["response"]
    # Both the leading and trailing prose survive in the bubble.
    assert "I found your sensors" in result["response"]
    assert "This automation monitors" in result["response"]


def test_fenced_automation_block_with_trailing_prose(hass, client) -> None:
    """Same, but with a proper ```automation opener and trailing prose."""
    hass.states.async_set("binary_sensor.leak", "off")
    hass.states.async_set("light.kitchen", "off")
    text = (
        "Here it is.\n\n"
        "```automation\n"
        "{\n"
        '  "alias": "Leak Alert",\n'
        '  "triggers": [{"platform": "state", "entity_id": "binary_sensor.leak", "to": "on"}],\n'
        '  "conditions": [],\n'
        '  "actions": [{"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}}]\n'
        "}\n"
        "```\n\n"
        "Let me know if you want changes."
    )
    result = client.parse_streamed_response(
        text,
        user_message="Create an automation that notifies me when the leak sensor turns on",
    )
    assert result["intent"] == "automation"
    assert result["automation"]["alias"] == "Leak Alert"
    assert '"alias"' not in result["response"]
    assert "Let me know if you want changes" in result["response"]


def test_bare_automation_trailing_prose_with_braces(hass, client) -> None:
    """Trailing prose containing braces must not extend the JSON capture.

    A greedy ``\\{[\\s\\S]*\\}`` would run from the automation object's
    opening ``{`` to the LAST ``}`` in the whole text — here the Jinja
    ``{{ … }}`` and ``{}`` in the follow-up sentence — making json.loads
    fail and dropping the proposal. The balanced decode stops at the
    automation object.
    """
    hass.states.async_set("binary_sensor.leak", "off")
    hass.states.async_set("light.kitchen", "off")
    text = (
        "Here you go.\n\n"
        "automation\n"
        "{\n"
        '  "alias": "Leak Alert",\n'
        '  "triggers": [{"platform": "state", "entity_id": "binary_sensor.leak", "to": "on"}],\n'
        '  "conditions": [],\n'
        '  "actions": [{"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}}]\n'
        "}\n"
        "```\n\n"
        "You can template the name with {{ trigger.to_state.name }} or pass {} for none."
    )
    result = client.parse_streamed_response(
        text,
        user_message="Create an automation that notifies me when the leak sensor turns on",
    )
    assert result["intent"] == "automation"
    assert result["automation"]["alias"] == "Leak Alert"
    assert '"alias"' not in result["response"]
    # Trailing prose (with its braces) survives in the bubble.
    assert "template the name" in result["response"]
    assert "{{ trigger.to_state.name }}" in result["response"]


def test_generic_fenced_example_not_salvaged_as_proposal(hass, client) -> None:
    """A `automation\\n{...}` inside a generic ``` fence is illustrative
    code, not a proposal. The bare-block fallback must skip it so no
    Accept card is surfaced for content the model only showed as code.
    """
    text = (
        "Here's the shape an automation takes:\n\n"
        "```\n"
        "automation\n"
        "{\n"
        '  "alias": "Example",\n'
        '  "triggers": [{"platform": "state", "entity_id": "binary_sensor.x", "to": "on"}]\n'
        "}\n"
        "```\n\n"
        "Want me to build one for real?"
    )
    result = client.parse_streamed_response(text)
    # No real proposal: not an automation intent, no proposal payload.
    assert result["intent"] != "automation"
    assert "automation" not in result
    assert "automation_yaml" not in result


def test_fenced_automation_example_in_howto_not_proposal(hass, client) -> None:
    """A ```automation example in a how-to answer is not a proposal.

    The block is followed by a SHORT explanation ("Adjust the entity IDs
    as needed") — length alone can't tell that from a real summary. The
    discriminator is intent: the user asked *how*, not to create, so
    ``_is_definite_automation`` is False and the example renders as code
    with no Accept card.
    """
    hass.states.async_set("binary_sensor.leak", "off")
    text = (
        "Here's how a leak automation looks:\n\n"
        "```automation\n"
        "{\n"
        '  "alias": "Example Leak Alert",\n'
        '  "triggers": [{"platform": "state", "entity_id": "binary_sensor.leak", "to": "on"}],\n'
        '  "conditions": [],\n'
        '  "actions": [{"service": "notify.mobile_app", "data": {"message": "Leak!"}}]\n'
        "}\n"
        "```\n\n"
        "Adjust the entity IDs as needed."
    )
    result = client.parse_streamed_response(
        text,
        user_message="How do I write an automation to alert me about leaks?",
    )
    assert result["intent"] != "automation"
    assert "automation" not in result
    assert "automation_yaml" not in result


def test_terminal_fenced_block_is_proposal_without_create_intent(hass, client) -> None:
    """A block at the very END (no trailing prose) is still a proposal
    even when intent classification is ambiguous — the terminal position
    is the architect's proposal convention. Only the trailing-prose case
    is gated on create-intent.
    """
    hass.states.async_set("binary_sensor.leak", "off")
    hass.states.async_set("light.kitchen", "off")
    text = (
        "Done.\n\n"
        "```automation\n"
        "{\n"
        '  "alias": "Leak Alert",\n'
        '  "triggers": [{"platform": "state", "entity_id": "binary_sensor.leak", "to": "on"}],\n'
        '  "conditions": [],\n'
        '  "actions": [{"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}}]\n'
        "}\n"
        "```"
    )
    result = client.parse_streamed_response(text)
    assert result["intent"] == "automation"
    assert result["automation"]["alias"] == "Leak Alert"


def test_prose_mention_of_automation_does_not_trigger_salvage(hass, client) -> None:
    """Conservative: the word must be alone on a line followed by ``{``."""
    text = "I built an automation for you. Want me to refine it?"
    result = client.parse_streamed_response(text)
    # Falls through to JSON parser → answer intent.
    assert result["intent"] != "automation"
    assert "automation" in result["response"]


def test_bare_scene_block_without_opening_fence(hass, client) -> None:
    hass.states.async_set("light.living_room", "on")
    text = (
        "Here is the scene.\n\n"
        "scene\n"
        '{"name": "Movie Time", "entities": {"light.living_room": {"state": "on"}}}\n'
        "```"
    )
    result = client.parse_streamed_response(text)
    assert result["intent"] == "scene"
    assert result["scene"]["name"] == "Movie Time"
    assert "Movie Time" not in result["response"]
    assert "Here is the scene" in result["response"]
