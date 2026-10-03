"""Every reply shape the trained automation prompt asks for must parse.

The prompt offers the model three outputs: a fenced YAML BLUEPRINT, the
CONCRETE automation object, and the clarification shape. Each is checked
on both runtimes, through both the non-streaming conversion
(``extract_text_response``) and the streaming one (``convert_response_text``),
and then through ``parse_architect_response``, which is what the chat
handlers read.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.selora_ai.const import (
    SELORA_LOCAL_BACKEND_LLAMA,
    SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED,
)
from custom_components.selora_ai.llm_client.parsers import parse_architect_response
from custom_components.selora_ai.providers.selora_local import SeloraLocalProvider
from custom_components.selora_ai.providers.selora_local.runtime.slim_parser import (
    selora_local_blueprint_reply,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

BACKENDS = [SELORA_LOCAL_BACKEND_LLAMA, SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED]

_BLUEPRINT = """```yaml
blueprint:
  name: Motion light
  description: Turn a light on while motion is detected.
  domain: automation
  input:
    motion_sensor:
      name: Motion sensor
      selector:
        entity:
          domain: binary_sensor
    light_target:
      name: Light
      selector:
        target:
          entity:
            domain: light
mode: single
max_exceeded: silent
triggers:
  - platform: state
    entity_id: !input motion_sensor
    to: "on"
actions:
  - service: light.turn_on
    target: !input light_target
%s
```"""

# Each of these holds text the JSON crop reads as an object, or a template
# whose braces send it down the repair path and strip the fence.
_BLUEPRINT_BODIES = {
    "plain": "",
    "empty_mapping": "    data: {}",
    "flow_mapping": '    data: {"brightness_pct": 50}',
    "template": (
        "  - service: notify.notify\n"
        "    data:\n"
        '      message: "{{ trigger.entity_id }} detected motion"'
    ),
}


def _provider(hass: HomeAssistant, backend: str) -> SeloraLocalProvider:
    provider = SeloraLocalProvider(
        hass, host="http://hub.invalid:8080", selora_local_backend=backend
    )
    provider.set_call_kind("chat_automation")
    return provider


def _converted(provider: SeloraLocalProvider, raw: str, *, streamed: bool) -> str:
    provider.set_call_kind("chat_automation")
    if streamed:
        provider._raw_response_buffer.set(raw)
        return provider.convert_response_text(raw)
    return (
        provider.extract_text_response(
            {"choices": [{"message": {"role": "assistant", "content": raw}}]}
        )
        or ""
    )


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("streamed", [False, True], ids=["whole", "streamed"])
@pytest.mark.parametrize("body", list(_BLUEPRINT_BODIES), ids=list(_BLUEPRINT_BODIES))
async def test_a_blueprint_reaches_the_user_verbatim(
    hass: HomeAssistant, backend: str, streamed: bool, body: str
) -> None:
    """Nothing here can save a blueprint, so the reply is the code block the
    model wrote — fence included, so the panel renders it with a Copy button —
    and never an automation envelope with no automation in it."""
    raw = _BLUEPRINT % _BLUEPRINT_BODIES[body]
    parsed = parse_architect_response(
        _converted(_provider(hass, backend), raw, streamed=streamed), hass
    )
    assert parsed == {"intent": "answer", "response": raw}


def test_a_commented_blueprint_key_is_still_a_blueprint() -> None:
    """The trained prompt allows inline comments in the block."""
    raw = "```yaml\nblueprint:  # metadata\n  name: x\n  domain: automation\n```"
    assert selora_local_blueprint_reply(raw) == raw


def test_a_fenced_block_without_a_blueprint_key_is_not_a_blueprint() -> None:
    assert selora_local_blueprint_reply("```yaml\nalias: Night lights\n```") is None


def test_an_unfenced_blueprint_key_is_not_a_blueprint() -> None:
    """The fence is part of the trained format; prose that merely says the
    word is an answer like any other."""
    assert selora_local_blueprint_reply("blueprint:\n  name: x\n") is None


def test_a_nested_blueprint_key_is_not_a_blueprint() -> None:
    assert selora_local_blueprint_reply("```yaml\nfoo:\n  blueprint:\n    name: x\n```") is None


_CONCRETE: dict[str, Any] = {
    "intent": "automation",
    "response": "Turns on the kitchen lights at sunset.",
    "description": "Turns on Kitchen Lights every day at sunset.",
    "automation": {
        "alias": "Kitchen Lights Sunset",
        "description": "Kitchen lights on at sunset.",
        "triggers": [{"platform": "sun", "event": "sunset"}],
        "conditions": [],
        "actions": [{"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}}],
    },
}


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("streamed", [False, True], ids=["whole", "streamed"])
async def test_the_concrete_automation_object_becomes_an_automation(
    hass: HomeAssistant, backend: str, streamed: bool
) -> None:
    """The full object is what both the automation specialist and the fused
    model's published prompt ask for."""
    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen Lights"})
    parsed = parse_architect_response(
        _converted(_provider(hass, backend), json.dumps(_CONCRETE), streamed=streamed), hass
    )
    assert parsed["intent"] == "automation"
    assert parsed["automation"]["alias"] == "Kitchen Lights Sunset"
    assert parsed["automation"]["actions"][0]["target"] == {"entity_id": ["light.kitchen"]}


@pytest.mark.parametrize("backend", BACKENDS)
async def test_the_clarification_shape_stays_a_clarification(
    hass: HomeAssistant, backend: str
) -> None:
    raw = json.dumps({"intent": "clarification", "response": "Which light: Kitchen or Hallway?"})
    parsed = parse_architect_response(
        _converted(_provider(hass, backend), raw, streamed=False), hass
    )
    assert parsed["intent"] == "clarification"
    assert parsed["response"] == "Which light: Kitchen or Hallway?"


@pytest.mark.parametrize("backend", BACKENDS)
async def test_a_blueprint_wins_over_a_sentence_driven_override(
    hass: HomeAssistant, backend: str
) -> None:
    """The deterministic automation overrides build a concrete automation from
    the user's sentence without reading the reply. When the model answered
    with a blueprint, the request was for one, and a concrete automation in
    its place is not what was asked for."""
    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen Light"})
    provider = _provider(hass, backend)
    provider.set_chat_context(
        user_message="create a blueprint that turns the kitchen light on at sunset",
        entities=[{"entity_id": "light.kitchen", "state": "off", "attributes": {}}],
        existing_automations=[],
        history=[],
    )
    raw = _BLUEPRINT % ""
    assert parse_architect_response(_converted(provider, raw, streamed=False), hass) == {
        "intent": "answer",
        "response": raw,
    }
