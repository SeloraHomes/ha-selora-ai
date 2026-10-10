"""The predicted next message the panel shows in the empty composer.

The prediction runs after a real turn, over the session the turn persisted, so
these drive the chat handler first and only stub the provider's reply to the
prediction request.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.const import (
    CONF_LLM_PROVIDER,
    CONF_NEXT_PROMPT_ENABLED,
    DOMAIN,
    LLM_PROVIDER_ANTHROPIC,
    LLM_PROVIDER_OLLAMA,
    LLM_PROVIDER_SELORA_CLOUD,
    LLM_PROVIDER_SELORA_LOCAL,
)
from custom_components.selora_ai.next_prompt import (
    SYSTEM_PROMPT,
    build_transcript,
    next_prompt_enabled,
    parse_prediction,
)
from custom_components.selora_ai.websocket.sessions import (
    _handle_websocket_predict_next_prompt,
)

from .chat_harness import ChatHarness, FakeConnection

_PREDICT = _handle_websocket_predict_next_prompt.__wrapped__


def _proposal() -> dict[str, Any]:
    automation = {
        "alias": "Doorbell Announcement",
        "description": "Announces a visitor when the doorbell or a person is detected",
        "triggers": [
            {"trigger": "state", "entity_id": "binary_sensor.front_door_visitor", "to": "on"},
            {"trigger": "state", "entity_id": "binary_sensor.front_door_person", "to": "on"},
        ],
        "conditions": [],
        "actions": [{"action": "tts.speak", "data": {"message": "Someone is at the door"}}],
        "mode": "single",
    }
    return {
        "intent": "automation",
        "response": "I'll also announce when the camera detects a person.",
        "automation": automation,
        "automation_yaml": "alias: Doorbell Announcement\ntriggers: []\n",
    }


def _reply(prompt: str, confidence: float) -> tuple[str, None]:
    return json.dumps({"confidence": confidence, "prompt": prompt}), None


@pytest.fixture
async def harness(hass: HomeAssistant) -> ChatHarness:
    return await ChatHarness.create(hass)


def _entry(hass: HomeAssistant, provider: str = LLM_PROVIDER_ANTHROPIC, **options: Any) -> None:
    MockConfigEntry(domain=DOMAIN, data={CONF_LLM_PROVIDER: provider}, options=options).add_to_hass(
        hass
    )


async def _predict(
    harness: ChatHarness, reply: tuple[str | None, str | None], language: str | None = None
) -> tuple[str | None, AsyncMock]:
    send = AsyncMock(return_value=reply)
    connection = FakeConnection()
    msg: dict[str, Any] = {"id": 1, "type": "selora_ai/predict_next_prompt"}
    msg["session_id"] = harness.session_id
    if language:
        msg["language"] = language
    with patch.object(harness.llm.provider, "send_request", send):
        await _PREDICT(harness.hass, connection, msg)
    assert not connection.errors
    return connection.results[-1]["prompt"], send


async def _saved_turn(harness: ChatHarness, message: str) -> None:
    turn = await harness.chat(message, reply=_proposal())
    await harness.save_proposal(turn.done["automation_message_index"], "selora_ai_aaa")


async def test_a_confident_prediction_reaches_the_panel(harness: ChatHarness) -> None:
    _entry(harness.hass)
    await _saved_turn(harness, "also announce when a person is at the door")

    prompt, send = await _predict(harness, _reply("Rename it to match", 0.9))

    assert prompt == "Rename it to match"
    kwargs = send.await_args.kwargs
    assert kwargs["system"].endswith(SYSTEM_PROMPT)
    transcript = kwargs["messages"][0]["content"]
    # The model sees what was asked, what was changed and in what state.
    assert "USER: also announce when a person is at the door" in transcript
    assert '[Automation card "Doorbell Announcement", accepted and saved]' in transcript
    assert "alias: Doorbell Announcement" in transcript


async def test_a_proposal_awaiting_accept_is_not_predicted(harness: ChatHarness) -> None:
    """The next move is the Accept click. Accepting asks again, from the
    saved state."""
    _entry(harness.hass)
    await harness.chat("also announce when a person is at the door", reply=_proposal())
    prompt, send = await _predict(harness, _reply("Rename it to match", 0.9))
    assert prompt is None
    send.assert_not_awaited()


async def test_an_unsure_prediction_shows_nothing(harness: ChatHarness) -> None:
    _entry(harness.hass)
    await _saved_turn(harness, "also announce when a person is at the door")
    prompt, _send = await _predict(harness, _reply("Make it louder", 0.4))
    assert prompt is None


async def test_turned_off_costs_no_call(harness: ChatHarness) -> None:
    _entry(harness.hass, **{CONF_NEXT_PROMPT_ENABLED: False})
    await _saved_turn(harness, "also announce when a person is at the door")
    prompt, send = await _predict(harness, _reply("Rename it to match", 0.9))
    assert prompt is None
    send.assert_not_awaited()


async def test_the_prediction_is_written_in_the_users_language(harness: ChatHarness) -> None:
    _entry(harness.hass)
    await _saved_turn(harness, "annonce aussi quand une personne est à la porte")
    _prompt, send = await _predict(harness, _reply("Renomme-la", 0.9), language="en")
    assert "Respond in French" in send.await_args.kwargs["system"]


async def test_a_turn_waiting_on_a_button_is_not_predicted(harness: ChatHarness) -> None:
    _entry(harness.hass)
    await harness.chat(
        "lock the front door",
        reply={
            "intent": "answer",
            "response": "Which one?",
            "quick_actions": [{"label": "Front", "value": "the front one"}],
        },
    )
    prompt, send = await _predict(harness, _reply("The front one", 0.95))
    assert prompt is None
    send.assert_not_awaited()


@pytest.mark.parametrize(
    ("provider", "stored", "expected"),
    [
        (LLM_PROVIDER_ANTHROPIC, None, True),
        (LLM_PROVIDER_SELORA_CLOUD, None, True),
        (LLM_PROVIDER_ANTHROPIC, False, False),
        (LLM_PROVIDER_OLLAMA, None, False),
        (LLM_PROVIDER_OLLAMA, True, True),
        (LLM_PROVIDER_SELORA_LOCAL, True, False),
    ],
)
def test_the_default_follows_the_provider(
    provider: str, stored: bool | None, expected: bool
) -> None:
    config = {} if stored is None else {CONF_NEXT_PROMPT_ENABLED: stored}
    assert next_prompt_enabled(config, provider) is expected


def test_a_session_not_ending_on_a_reply_is_skipped() -> None:
    assert build_transcript([]) is None
    assert build_transcript([{"role": "user", "content": "hi"}]) is None
    pending = {"role": "assistant", "content": "Allow?", "approval_status": "pending"}
    assert build_transcript([{"role": "user", "content": "unlock"}, pending]) is None


def test_markers_never_reach_the_transcript() -> None:
    transcript = build_transcript(
        [
            {"role": "user", "content": "which lights are on?"},
            {"role": "assistant", "content": "Two are on. [[entities:light.a,light.b]]"},
        ]
    )
    assert transcript is not None
    assert "[[" not in transcript


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('```json\n{"confidence": 0.8, "prompt": "Rename it"}\n```', "Rename it"),
        ('{"confidence": 0.8, "prompt": "  Rename\\n it  "}', "Rename it"),
        ('{"confidence": 0, "prompt": ""}', None),
        ('{"confidence": "high", "prompt": "Rename it"}', None),
        ('{"confidence": 0.9, "prompt": "Turn on [[entity:light.a]]"}', None),
        ('{"confidence": 0.9, "prompt": "' + "word " * 40 + '"}', None),
        ("Rename it", None),
        (None, None),
    ],
)
def test_only_a_clean_confident_answer_is_kept(raw: str | None, expected: str | None) -> None:
    assert parse_prediction(raw) == expected
