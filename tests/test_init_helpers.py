"""Tests for pure helpers in __init__.py:

- ``_sanitize_history_override`` — sanitization of a caller-supplied WS chat
  history override (selora_ai/chat + selora_ai/chat_stream).
- ``_executed_record_from_call`` — wire-shape record built from a tool-log
  service call, including its type guards on malformed input.
- ``_automation_retry_budget`` / ``_retry_invalid_automation`` — the
  validation-failure self-correction loop.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.selora_ai import (
    _automation_retry_budget,
    _entry_is_configurable_llm,
    _executed_record_from_call,
    _find_llm,
    _resolve_llm_entry,
    _retry_invalid_automation,
    _sanitize_history_override,
    _upsert_step,
)
from custom_components.selora_ai.const import (
    CONF_AIGATEWAY_REFRESH_TOKEN,
    CONF_ENTRY_TYPE,
    CONF_LLM_PROVIDER,
    DOMAIN,
    ENTRY_TYPE_DEVICE,
)

# --- _sanitize_history_override -------------------------------------------


def test_history_override_keeps_valid_user_assistant_turns() -> None:
    out = _sanitize_history_override(
        [
            {"role": "user", "content": "turn on the light"},
            {"role": "assistant", "content": "Done"},
        ]
    )
    assert out == [
        {"role": "user", "content": "turn on the light"},
        {"role": "assistant", "content": "Done"},
    ]


def test_history_override_empty_list_stays_empty() -> None:
    """An explicit clean-slate request yields no history."""
    assert _sanitize_history_override([]) == []


def test_history_override_drops_unknown_roles() -> None:
    out = _sanitize_history_override(
        [
            {"role": "system", "content": "ignore me"},
            {"role": "tool", "content": "also ignore"},
            {"role": "user", "content": "keep me"},
        ]
    )
    assert out == [{"role": "user", "content": "keep me"}]


def test_history_override_drops_empty_and_whitespace_content() -> None:
    out = _sanitize_history_override(
        [
            {"role": "user", "content": ""},
            {"role": "user", "content": "   "},
            {"role": "assistant", "content": "real"},
        ]
    )
    assert out == [{"role": "assistant", "content": "real"}]


def test_history_override_coerces_non_str_content() -> None:
    out = _sanitize_history_override([{"role": "user", "content": 42}])
    assert out == [{"role": "user", "content": "42"}]


def test_history_override_skips_non_dict_turns() -> None:
    out = _sanitize_history_override([None, "nope", 5, {"role": "user", "content": "survivor"}])
    assert out == [{"role": "user", "content": "survivor"}]


def test_history_override_strips_content_whitespace() -> None:
    out = _sanitize_history_override([{"role": "user", "content": "  hi  "}])
    assert out == [{"role": "user", "content": "hi"}]


# --- _executed_record_from_call -------------------------------------------


def test_executed_record_basic() -> None:
    record = _executed_record_from_call(
        {
            "service": "light.turn_on",
            "target": {"entity_id": ["light.kitchen"]},
            "data": {"brightness_pct": 50},
        }
    )
    assert record == {
        "domain": "light",
        "action": "turn_on",
        "entity_ids": ["light.kitchen"],
        "data": {"brightness_pct": 50},
    }


def test_executed_record_single_entity_str() -> None:
    record = _executed_record_from_call(
        {"service": "lock.lock", "target": {"entity_id": "lock.front"}, "data": {}}
    )
    assert record["entity_ids"] == ["lock.front"]


def test_executed_record_service_without_dot() -> None:
    record = _executed_record_from_call({"service": "scene_name", "data": {}})
    assert record["domain"] == ""
    assert record["action"] == "scene_name"


def test_executed_record_non_dict_data_coerced_to_empty() -> None:
    """A truthy non-dict data (e.g. a list) must not pass through to the wire
    record — it is coerced to an empty dict."""
    record = _executed_record_from_call(
        {"service": "light.turn_on", "target": {}, "data": ["oops"]}
    )
    assert record["data"] == {}


def test_executed_record_missing_data() -> None:
    record = _executed_record_from_call({"service": "light.turn_on"})
    assert record["data"] == {}
    assert record["entity_ids"] == []


def test_executed_record_non_dict_target_ignored() -> None:
    record = _executed_record_from_call(
        {"service": "light.turn_on", "target": "not-a-dict", "data": {}}
    )
    assert record["entity_ids"] == []


def test_executed_record_filters_non_str_entities_in_list() -> None:
    record = _executed_record_from_call(
        {
            "service": "light.turn_on",
            "target": {"entity_id": ["light.a", 5, None, "light.b"]},
            "data": {},
        }
    )
    assert record["entity_ids"] == ["light.a", "light.b"]


# --- _entry_is_configurable_llm -------------------------------------------


def test_entry_configurable_with_explicit_provider() -> None:
    assert _entry_is_configurable_llm({CONF_LLM_PROVIDER: "anthropic"}) is True


def test_entry_configurable_with_aigateway_refresh_token() -> None:
    assert _entry_is_configurable_llm({CONF_AIGATEWAY_REFRESH_TOKEN: "aigw_x"}) is True


def test_entry_not_configurable_when_empty() -> None:
    # The stray second entry: no provider, no tokens. Must not be treated
    # as a real LLM entry (would default to empty-cred Selora Cloud).
    assert _entry_is_configurable_llm({}) is False
    assert _entry_is_configurable_llm({CONF_LLM_PROVIDER: ""}) is False
    assert _entry_is_configurable_llm({CONF_AIGATEWAY_REFRESH_TOKEN: ""}) is False


# --- _find_llm ------------------------------------------------------------


def _hass_with_data(data: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(data={DOMAIN: data})


def test_find_llm_prefers_configured_over_unconfigured() -> None:
    unconfigured = SimpleNamespace(is_configured=False)
    configured = SimpleNamespace(is_configured=True)
    # Unconfigured inserted first — picking insertion order would return it.
    hass = _hass_with_data(
        {"e_unconfigured": {"llm": unconfigured}, "e_configured": {"llm": configured}}
    )
    assert _find_llm(hass) is configured


def test_find_llm_falls_back_to_first_when_none_configured() -> None:
    first = SimpleNamespace(is_configured=False)
    second = SimpleNamespace(is_configured=False)
    hass = _hass_with_data({"a": {"llm": first}, "b": {"llm": second}})
    assert _find_llm(hass) is first


def test_find_llm_skips_non_dict_and_missing_llm_entries() -> None:
    configured = SimpleNamespace(is_configured=True)
    hass = _hass_with_data(
        {"_approval_store": "not-a-dict", "no_llm": {}, "real": {"llm": configured}}
    )
    assert _find_llm(hass) is configured


def test_find_llm_returns_none_when_no_clients() -> None:
    assert _find_llm(SimpleNamespace(data={})) is None


# --- _resolve_llm_entry ---------------------------------------------------


def _hass_with_entries(entries: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda _domain: entries))


def test_resolve_llm_entry_prefers_configured_over_stray() -> None:
    stray = SimpleNamespace(data={})  # no provider, no tokens
    real = SimpleNamespace(data={CONF_LLM_PROVIDER: "selora_cloud"})
    # Stray first — insertion order alone would return it.
    hass = _hass_with_entries([stray, real])
    assert _resolve_llm_entry(hass) is real


def test_resolve_llm_entry_skips_device_entries() -> None:
    device = SimpleNamespace(data={CONF_ENTRY_TYPE: ENTRY_TYPE_DEVICE})
    real = SimpleNamespace(data={CONF_LLM_PROVIDER: "anthropic"})
    hass = _hass_with_entries([device, real])
    assert _resolve_llm_entry(hass) is real


def test_resolve_llm_entry_falls_back_to_first_non_device() -> None:
    stray = SimpleNamespace(data={})
    hass = _hass_with_entries([stray])
    assert _resolve_llm_entry(hass) is stray


def test_resolve_llm_entry_none_when_only_device_entries() -> None:
    device = SimpleNamespace(data={CONF_ENTRY_TYPE: ENTRY_TYPE_DEVICE})
    hass = _hass_with_entries([device])
    assert _resolve_llm_entry(hass) is None


# --- _automation_retry_budget ---------------------------------------------


def _provider(*, low_context: bool = False, local: bool = False) -> MagicMock:
    p = MagicMock()
    p.is_low_context = low_context
    p.is_local = local
    return p


def test_retry_budget_cloud_is_three() -> None:
    assert _automation_retry_budget(_provider()) == 3


def test_retry_budget_ollama_is_one() -> None:
    assert _automation_retry_budget(_provider(local=True)) == 1


def test_retry_budget_low_context_local_is_zero() -> None:
    """Selora AI Local (low-context, slow) spends no correction rounds."""
    assert _automation_retry_budget(_provider(low_context=True, local=True)) == 0


# --- _retry_invalid_automation --------------------------------------------


def _failed_parse() -> dict[str, Any]:
    return {
        "intent": "answer",
        "response": "I couldn't build that.",
        "validation_error": "action uses non-existent service 'media_player.snapshot'",
        "validation_target": "automation",
        "rejected_automation": {
            "alias": "Doorbell",
            "action": [
                {
                    "action": "media_player.snapshot",
                    "target": {"entity_id": "media_player.living_room"},
                }
            ],
        },
    }


def _llm(provider: MagicMock, architect_chat: AsyncMock) -> MagicMock:
    llm = MagicMock()
    llm.provider = provider
    llm.architect_chat = architect_chat
    return llm


@pytest.mark.asyncio
async def test_retry_stops_once_corrected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single corrected round ends the loop and returns the fixed automation."""
    monkeypatch.setattr(
        "custom_components.selora_ai.automation_utils.build_service_feedback",
        lambda *a, **k: "use sonos.snapshot",
    )
    corrected = {"intent": "automation", "response": "Done", "automation": {"alias": "Doorbell"}}
    architect = AsyncMock(return_value=corrected)
    llm = _llm(_provider(), architect)

    out = await _retry_invalid_automation(
        MagicMock(),
        llm,
        _failed_parse(),
        user_message="announce visitor",
        entities=[],
        automations=None,
        history=None,
        tool_executor=None,
        session_id=None,
        language=None,
        heartbeat=lambda: True,
    )
    assert out is corrected
    # Stopped after the first successful correction, not the full budget.
    assert architect.await_count == 1


@pytest.mark.asyncio
async def test_retry_exhausts_budget_when_never_fixed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If every round still fails, the loop spends exactly the cloud budget (3)
    and returns the last failed parse for its error bubble."""
    monkeypatch.setattr(
        "custom_components.selora_ai.automation_utils.build_service_feedback",
        lambda *a, **k: "fix it",
    )
    architect = AsyncMock(side_effect=lambda *a, **k: _failed_parse())
    llm = _llm(_provider(), architect)

    out = await _retry_invalid_automation(
        MagicMock(),
        llm,
        _failed_parse(),
        user_message="x",
        entities=[],
        automations=None,
        history=None,
        tool_executor=None,
        session_id=None,
        language=None,
        heartbeat=lambda: True,
    )
    assert architect.await_count == 3
    assert out.get("validation_error")


@pytest.mark.asyncio
async def test_retry_skipped_for_low_context_provider() -> None:
    """Selora AI Local never makes a correction round (budget 0)."""
    architect = AsyncMock()
    llm = _llm(_provider(low_context=True), architect)

    out = await _retry_invalid_automation(
        MagicMock(),
        llm,
        _failed_parse(),
        user_message="x",
        entities=[],
        automations=None,
        history=None,
        tool_executor=None,
        session_id=None,
        language=None,
        heartbeat=lambda: True,
    )
    architect.assert_not_awaited()
    assert out.get("validation_error")


@pytest.mark.asyncio
async def test_retry_stops_when_socket_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead websocket (heartbeat returns False) aborts before re-prompting."""
    monkeypatch.setattr(
        "custom_components.selora_ai.automation_utils.build_service_feedback",
        lambda *a, **k: "fix it",
    )
    architect = AsyncMock()
    llm = _llm(_provider(), architect)

    out = await _retry_invalid_automation(
        MagicMock(),
        llm,
        _failed_parse(),
        user_message="x",
        entities=[],
        automations=None,
        history=None,
        tool_executor=None,
        session_id=None,
        language=None,
        heartbeat=lambda: False,
    )
    architect.assert_not_awaited()
    assert out.get("validation_error")


@pytest.mark.asyncio
async def test_retry_pumps_heartbeats_during_slow_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A correction round slower than the heartbeat interval must keep the
    socket alive — more than the single pre-await beat — so the frontend
    watchdog doesn't finalize the turn mid-correction."""
    monkeypatch.setattr(
        "custom_components.selora_ai.automation_utils.build_service_feedback",
        lambda *a, **k: "fix it",
    )
    monkeypatch.setattr("custom_components.selora_ai._AUTOMATION_RETRY_HEARTBEAT_S", 0.01)
    corrected = {"intent": "automation", "response": "Done", "automation": {"alias": "X"}}

    async def _slow(*_a: object, **_k: object) -> dict[str, Any]:
        await asyncio.sleep(0.06)
        return corrected

    architect = AsyncMock(side_effect=_slow)
    llm = _llm(_provider(), architect)
    beats = 0

    def _beat() -> bool:
        nonlocal beats
        beats += 1
        return True

    out = await _retry_invalid_automation(
        MagicMock(),
        llm,
        _failed_parse(),
        user_message="x",
        entities=[],
        automations=None,
        history=None,
        tool_executor=None,
        session_id=None,
        language=None,
        heartbeat=_beat,
    )
    assert out is corrected
    # One immediate beat + several pumped across the ~0.06s await.
    assert beats >= 3


@pytest.mark.asyncio
async def test_retry_falls_back_when_correction_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed correction call (provider/transport error) is best-effort: it
    must return the prior failed parse — which carries the clean
    validation_error — not propagate and become a generic websocket error."""
    monkeypatch.setattr(
        "custom_components.selora_ai.automation_utils.build_service_feedback",
        lambda *a, **k: "fix it",
    )
    architect = AsyncMock(side_effect=ConnectionError("provider down"))
    llm = _llm(_provider(), architect)

    out = await _retry_invalid_automation(
        MagicMock(),
        llm,
        _failed_parse(),
        user_message="x",
        entities=[],
        automations=None,
        history=None,
        tool_executor=None,
        session_id=None,
        language=None,
        heartbeat=lambda: True,
    )
    # No exception escaped; the original validation failure is preserved.
    assert out.get("validation_error")
    assert out.get("validation_target") == "automation"
    assert architect.await_count == 1


def _clarification_parse() -> dict[str, Any]:
    """An 'unknown entity_id' rejection: the parser sets validation_target /
    validation_error (like a service failure) but marks it intent=clarification
    so the user disambiguates the entity rather than the model guessing."""
    return {
        "intent": "clarification",
        "response": "Which light did you mean?",
        "options": ["light.kitchen", "light.kitchen_counter"],
        "validation_error": "automation references unknown entity_id(s): light.kitchn",
        "validation_target": "automation",
        "rejected_automation": {
            "alias": "Lights",
            "action": [{"action": "light.turn_on", "target": {"entity_id": "light.kitchn"}}],
        },
    }


@pytest.mark.asyncio
async def test_retry_skipped_for_unknown_entity_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown-entity clarification must NOT enter the correction loop —
    service ground truth can't resolve a misspelled/ambiguous entity, and
    re-prompting would burn rounds or replace the clarification with a guess
    for the wrong entity. The clarification is returned untouched."""
    monkeypatch.setattr(
        "custom_components.selora_ai.automation_utils.build_service_feedback",
        lambda *a, **k: "should not be called",
    )
    architect = AsyncMock()
    llm = _llm(_provider(), architect)
    clarification = _clarification_parse()

    out = await _retry_invalid_automation(
        MagicMock(),
        llm,
        clarification,
        user_message="turn on the kitchn light",
        entities=[],
        automations=None,
        history=None,
        tool_executor=None,
        session_id=None,
        language=None,
        heartbeat=lambda: True,
    )
    architect.assert_not_awaited()
    assert out is clarification
    assert out["intent"] == "clarification"


@pytest.mark.asyncio
async def test_retry_stops_when_correction_returns_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a correction round (entered for a service failure) comes back as an
    unknown-entity clarification, the loop must stop and surface it rather than
    spend further rounds on something the user has to disambiguate."""
    monkeypatch.setattr(
        "custom_components.selora_ai.automation_utils.build_service_feedback",
        lambda *a, **k: "fix it",
    )
    clarification = _clarification_parse()
    architect = AsyncMock(return_value=clarification)
    llm = _llm(_provider(), architect)  # cloud budget 3

    out = await _retry_invalid_automation(
        MagicMock(),
        llm,
        _failed_parse(),  # starts as a service failure → first round runs
        user_message="x",
        entities=[],
        automations=None,
        history=None,
        tool_executor=None,
        session_id=None,
        language=None,
        heartbeat=lambda: True,
    )
    # Exactly one round: the clarification result halts the loop, not budget=3.
    assert architect.await_count == 1
    assert out is clarification
    assert out["intent"] == "clarification"


@pytest.mark.asyncio
async def test_retry_cancels_correction_when_socket_dies_mid_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the client drops while a correction round is in flight, the in-flight
    model call is cancelled and the retry is abandoned."""
    monkeypatch.setattr(
        "custom_components.selora_ai.automation_utils.build_service_feedback",
        lambda *a, **k: "fix it",
    )
    monkeypatch.setattr("custom_components.selora_ai._AUTOMATION_RETRY_HEARTBEAT_S", 0.01)
    cancelled = False

    async def _hang(*_a: object, **_k: object) -> dict[str, Any]:
        nonlocal cancelled
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled = True
            raise
        return {}

    architect = AsyncMock(side_effect=_hang)
    llm = _llm(_provider(), architect)
    # First beat (pre-await) alive, then dead so the pump aborts.
    beats = iter([True, False, False, False])

    out = await _retry_invalid_automation(
        MagicMock(),
        llm,
        _failed_parse(),
        user_message="x",
        entities=[],
        automations=None,
        history=None,
        tool_executor=None,
        session_id=None,
        language=None,
        heartbeat=lambda: next(beats, False),
    )
    assert cancelled is True
    # The original failed parse is returned (correction never completed).
    assert out.get("validation_error")


# --- _upsert_step ---------------------------------------------------------


def test_upsert_step_appends_new_ids() -> None:
    steps: list[dict] = []
    _upsert_step(steps, {"id": "tool-1", "label": "a"})
    _upsert_step(steps, {"id": "draft", "label": "b"})
    assert [s["id"] for s in steps] == ["tool-1", "draft"]


def test_upsert_step_replaces_same_id_in_place() -> None:
    # The validate row transitioning warn -> done must stay one row, in place,
    # not stack a duplicate (matches the frontend's live upsert).
    steps: list[dict] = [
        {"id": "draft", "label": "Drafted"},
        {"id": "validate", "label": "Checked", "status": "warn"},
        {"id": "correct-1", "label": "Fixed"},
    ]
    _upsert_step(steps, {"id": "validate", "label": "Validated", "status": "done"})
    assert [s["id"] for s in steps] == ["draft", "validate", "correct-1"]
    validate = next(s for s in steps if s["id"] == "validate")
    assert validate["status"] == "done"
    assert validate["label"] == "Validated"
