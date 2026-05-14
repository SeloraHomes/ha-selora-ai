"""Tests for the execute_command prompt guidance and the duplicate-execution guard."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import custom_components.selora_ai.llm_client as _llm_mod
from custom_components.selora_ai.llm_client import (
    LLMClient,
    _build_command_confirmation,
    _executed_service_calls_from_log,
    _read_prompt_files,
    _suppress_duplicate_command_after_tool,
    _tool_failure_response,
)
from custom_components.selora_ai.providers import create_provider


@pytest.fixture(autouse=True)
def _enable_custom_component(enable_custom_integrations):
    """Auto-enable custom integrations so our domain is discoverable."""


@pytest.fixture(autouse=True)
def _preload_prompts():
    """Load prompt files so system prompts include tool-policy text."""
    _llm_mod._TOOL_POLICY_TEXT, _llm_mod._DEVICE_KNOWLEDGE_TEXT = _read_prompt_files()


def _make_client(hass) -> LLMClient:
    provider = create_provider("anthropic", hass, api_key="test-key")
    return LLMClient(hass, provider)


# ── Prompt content (JSON architect) ──────────────────────────────────────────


def test_json_prompt_mentions_execute_command_when_tools_available(hass) -> None:
    prompt = _make_client(hass)._build_architect_system_prompt(tools_available=True)
    assert "execute_command" in prompt
    assert "ALREADY run" in prompt
    assert "second time" in prompt


def test_json_prompt_omits_execute_command_without_tools(hass) -> None:
    prompt = _make_client(hass)._build_architect_system_prompt(tools_available=False)
    # The tool-mode paragraph must not be present when tools are unavailable.
    assert "execute_command" not in prompt


# ── Prompt content (streaming architect) ────────────────────────────────────


def test_stream_prompt_mentions_execute_command_when_tools_available(hass) -> None:
    prompt = _make_client(hass)._build_architect_stream_system_prompt(tools_available=True)
    assert "execute_command" in prompt
    assert "ALREADY run" in prompt


def test_stream_prompt_omits_execute_command_without_tools(hass) -> None:
    prompt = _make_client(hass)._build_architect_stream_system_prompt(tools_available=False)
    assert "execute_command" not in prompt


# ── _suppress_duplicate_command_after_tool ───────────────────────────────────


def _executed(service: str, entity_id: str, data: dict | None = None) -> dict:
    """A tool-log entry that ran a service successfully (executed=True)."""
    args: dict = {"service": service, "entity_id": entity_id}
    if data is not None:
        args["data"] = data
    return {
        "tool": "execute_command",
        "arguments": args,
        "result": {
            "executed": True,
            "service": service,
            "entity_ids": [entity_id],
            "states": [],
        },
    }


def _failed_validation(service: str, entity_id: str) -> dict:
    """A tool-log entry whose validation failed (no service ever ran)."""
    return {
        "tool": "execute_command",
        "arguments": {"service": service, "entity_id": entity_id},
        "result": {"valid": False, "errors": ["unknown entity"]},
    }


def _failed_runtime(service: str, entity_id: str) -> dict:
    """A tool-log entry whose hass.services raised."""
    return {
        "tool": "execute_command",
        "arguments": {"service": service, "entity_id": entity_id},
        "result": {
            "executed": False,
            "service": service,
            "entity_ids": [entity_id],
            "error": "device offline",
        },
    }


def test_guard_drops_duplicate_command_call_after_execute_command() -> None:
    """All calls in parsed are duplicates → downgrade to answer with confirmation."""
    parsed = {
        "intent": "command",
        "response": "Turning off the kitchen light.",
        "calls": [
            {
                "service": "light.turn_off",
                "target": {"entity_id": "light.kitchen"},
                "data": {},
            }
        ],
    }
    tool_log = [_executed("light.turn_off", "light.kitchen")]
    result = _suppress_duplicate_command_after_tool(parsed, tool_log)
    assert result["intent"] == "answer"
    assert result["suppressed_duplicate_command"] is True
    assert "calls" not in result
    # Confirmation text is preserved — Fix #1: not stomped by command policy
    assert "kitchen light" in result["response"].lower()


def test_guard_never_suppresses_delayed_command() -> None:
    """Delayed actions are future-scheduled — never duplicates of immediate ones."""
    parsed = {
        "intent": "delayed_command",
        "response": "Turning the fan off in 10 minutes.",
        "calls": [
            {"service": "fan.turn_off", "target": {"entity_id": "fan.bedroom"}},
        ],
        "delay_seconds": 600,
    }
    tool_log = [_executed("fan.turn_on", "fan.bedroom")]
    result = _suppress_duplicate_command_after_tool(parsed, tool_log)
    assert result is parsed
    assert result["intent"] == "delayed_command"
    assert result["delay_seconds"] == 600


def test_guard_keeps_non_duplicate_calls() -> None:
    """Mixed turn: drop the call that was tool-executed, keep new ones.

    The surviving call must NOT carry suppressed_duplicate_command — the
    flag would short-circuit _apply_command_policy and skip allowlist /
    entity / data validation for the surviving call.
    """
    parsed = {
        "intent": "command",
        "response": "Doing both.",
        "calls": [
            {"service": "light.turn_off", "target": {"entity_id": "light.kitchen"}},
            {"service": "light.turn_on", "target": {"entity_id": "light.bedroom"}},
        ],
    }
    tool_log = [_executed("light.turn_off", "light.kitchen")]
    result = _suppress_duplicate_command_after_tool(parsed, tool_log)
    assert result["intent"] == "command"
    assert len(result["calls"]) == 1
    assert result["calls"][0]["target"]["entity_id"] == "light.bedroom"
    # Flag must NOT be set — surviving calls need to go through the policy.
    assert "suppressed_duplicate_command" not in result


def test_guard_different_service_not_duplicate() -> None:
    """Same entity, different service → not a duplicate."""
    parsed = {
        "intent": "command",
        "response": "Now turning it off.",
        "calls": [
            {"service": "light.turn_off", "target": {"entity_id": "light.kitchen"}},
        ],
    }
    tool_log = [_executed("light.turn_on", "light.kitchen")]
    result = _suppress_duplicate_command_after_tool(parsed, tool_log)
    assert result["intent"] == "command"
    assert len(result["calls"]) == 1


def test_guard_does_not_suppress_when_tool_validation_failed() -> None:
    """Fix #3: validation-failed tool call must not suppress the fallback command."""
    parsed = {
        "intent": "command",
        "response": "Turning off the kitchen light.",
        "calls": [
            {"service": "light.turn_off", "target": {"entity_id": "light.kitchen"}},
        ],
    }
    tool_log = [_failed_validation("light.turn_off", "light.kitchen")]
    result = _suppress_duplicate_command_after_tool(parsed, tool_log)
    assert result is parsed
    assert result["intent"] == "command"


def test_guard_does_not_suppress_when_service_raised() -> None:
    """Fix #3: runtime-failed tool call must not suppress the fallback command."""
    parsed = {
        "intent": "command",
        "response": "Turning off the kitchen light.",
        "calls": [
            {"service": "light.turn_off", "target": {"entity_id": "light.kitchen"}},
        ],
    }
    tool_log = [_failed_runtime("light.turn_off", "light.kitchen")]
    result = _suppress_duplicate_command_after_tool(parsed, tool_log)
    assert result is parsed
    assert result["intent"] == "command"


def test_guard_no_change_when_no_execute_command_in_log() -> None:
    parsed = {
        "intent": "command",
        "response": "x",
        "calls": [
            {"service": "light.turn_off", "target": {"entity_id": "light.kitchen"}},
        ],
    }
    tool_log = [{"tool": "search_entities", "arguments": {"query": "kitchen"}}]
    result = _suppress_duplicate_command_after_tool(parsed, tool_log)
    assert result is parsed


def test_guard_no_change_when_log_empty() -> None:
    parsed = {"intent": "command", "response": "x", "calls": []}
    assert _suppress_duplicate_command_after_tool(parsed, []) is parsed


def test_guard_no_change_for_answer_intent() -> None:
    parsed = {"intent": "answer", "response": "Done."}
    tool_log = [_executed("light.turn_off", "light.kitchen")]
    result = _suppress_duplicate_command_after_tool(parsed, tool_log)
    assert result is parsed


def test_policy_validates_partial_strip_survivors(hass) -> None:
    """Regression: after a partial strip, the surviving call must still run
    through the safety policy. A model that mixes one tool-executed call
    with another unsafe call (e.g. ``lock.unlock`` — outside the safe
    domain allowlist) must have the unsafe call rejected, not smuggled
    through.
    """
    client = _make_client(hass)
    parsed = {
        "intent": "command",
        "response": "Doing both.",
        "calls": [
            {"service": "light.turn_off", "target": {"entity_id": "light.kitchen"}},
            {"service": "lock.unlock", "target": {"entity_id": "lock.front_door"}},
        ],
    }
    tool_log = [_executed("light.turn_off", "light.kitchen")]
    suppressed = _suppress_duplicate_command_after_tool(parsed, tool_log)
    # Only the lock.unlock call survived the duplicate strip.
    assert len(suppressed["calls"]) == 1
    assert suppressed["calls"][0]["service"] == "lock.unlock"

    # Policy must reject lock.unlock — it's outside the safe allowlist.
    final = client._apply_command_policy(
        suppressed,
        entities=[
            {"entity_id": "light.kitchen", "state": "on", "attributes": {}},
            {"entity_id": "lock.front_door", "state": "locked", "attributes": {}},
        ],
    )
    assert final["intent"] == "answer"
    assert final.get("calls") == []
    assert "validation_error" in final
    assert "lock" in final["validation_error"]


def test_policy_validates_partial_strip_survivors_data_keys(hass) -> None:
    """Surviving call with unsupported data params must still be rejected."""
    client = _make_client(hass)
    parsed = {
        "intent": "command",
        "response": "Doing both.",
        "calls": [
            {"service": "light.turn_off", "target": {"entity_id": "light.kitchen"}},
            {
                "service": "light.turn_on",
                "target": {"entity_id": "light.bedroom"},
                "data": {"bogus_param": 42},
            },
        ],
    }
    tool_log = [_executed("light.turn_off", "light.kitchen")]
    suppressed = _suppress_duplicate_command_after_tool(parsed, tool_log)
    final = client._apply_command_policy(
        suppressed,
        entities=[
            {"entity_id": "light.kitchen", "state": "on", "attributes": {}},
            {"entity_id": "light.bedroom", "state": "off", "attributes": {}},
        ],
    )
    # Unsupported data key is caught by the policy — entire turn blocked.
    assert final["intent"] == "answer"
    assert final.get("calls") == []
    assert "validation_error" in final
    assert "bogus_param" in final["validation_error"]


def test_executed_calls_from_log_only_successful() -> None:
    """Validation failures and service exceptions are not surfaced as executed."""
    log = [
        _executed("light.turn_off", "light.kitchen"),
        _failed_validation("light.turn_on", "light.bedroom"),
        _failed_runtime("fan.turn_on", "fan.bedroom"),
        {"tool": "search_entities", "arguments": {"query": "x"}, "result": {}},
    ]
    calls = _executed_service_calls_from_log(log)
    assert len(calls) == 1
    assert calls[0]["service"] == "light.turn_off"
    assert calls[0]["target"]["entity_id"] == ["light.kitchen"]


def test_executed_calls_from_log_empty_log() -> None:
    assert _executed_service_calls_from_log([]) == []
    assert _executed_service_calls_from_log(None) == []


def test_tool_failure_response_with_executed_call() -> None:
    """The synthesized message mentions what already ran."""
    log = [_executed("light.turn_off", "light.kitchen")]
    msg = _tool_failure_response(log, suffix="Then the LLM dropped.")
    assert "light" in msg.lower()
    assert "Then the LLM dropped." in msg
    # Must start with the confirmation prose, not the suffix
    assert msg.index("light") < msg.index("Then the LLM")


def test_tool_failure_response_no_executed_call() -> None:
    """With no successful tool calls, only the suffix is returned."""
    msg = _tool_failure_response(
        [_failed_runtime("light.turn_on", "light.kitchen")],
        suffix="Generic failure message.",
    )
    assert msg == "Generic failure message."


def test_streaming_exhaustion_confirmation_survives_policy(hass) -> None:
    """Regression: tool-loop exhaustion synthesizes 'Done — ...' prose; the
    streaming parser must mark it suppressed so the policy doesn't rewrite
    it to 'I didn't run any action'. Same logic covers the JSON path via
    architect_chat — tested here through parse_streamed_response.
    """
    client = _make_client(hass)
    # Pure prose, no fenced blocks — what _stream_request_with_tools yields
    # on exhaustion after execute_command already fired.
    text = (
        "Done — light turn_off (kitchen). Then I ran out of tool rounds "
        "before finishing — try a more specific request only if there's "
        "more to do."
    )
    tool_log = [_executed("light.turn_off", "light.kitchen")]
    parsed = client.parse_streamed_response(
        text, entities=[_ent("light.kitchen")], tool_log=tool_log
    )
    assert parsed["intent"] == "answer"
    assert parsed["response"].startswith("Done —")
    assert "didn't run" not in parsed["response"].lower()
    assert parsed.get("suppressed_duplicate_command") is True


def test_streaming_no_flag_when_no_tool_executed(hass) -> None:
    """Mirror: same prose shape but no tool ran → no suppression flag.
    The policy is free to apply its normal unbacked-action handling
    (in fact this prose wouldn't even reach this path in practice — it
    only matters that we don't blanket-set the flag).
    """
    client = _make_client(hass)
    text = "Done — something happened."
    tool_log = [_failed_validation("light.turn_off", "light.kitchen")]
    parsed = client.parse_streamed_response(
        text, entities=[_ent("light.kitchen")], tool_log=tool_log
    )
    # Failed validation in tool_log → no executed signature → no flag.
    assert parsed.get("suppressed_duplicate_command") is not True


def test_policy_preserves_confirmation_after_suppression(hass) -> None:
    """Fix #1: _apply_command_policy must not stomp the confirmation text
    when the duplicate guard has already converted the turn to 'answer'.
    """
    client = _make_client(hass)
    # Simulate what _suppress_duplicate_command_after_tool produces.
    suppressed = {
        "intent": "answer",
        "response": "Turning off the kitchen light.",
        "suppressed_duplicate_command": True,
    }
    result = client._apply_command_policy(suppressed, entities=[])
    assert result["response"] == "Turning off the kitchen light."
    assert "validation_error" not in result
    # Old bug: result["response"] would become "I'm not sure which device…"


# ── parse_streamed_response with tool_log ───────────────────────────────────


def _ent(entity_id: str) -> dict:
    """Minimal EntitySnapshot for tests that pass through _apply_command_policy."""
    return {"entity_id": entity_id, "state": "on", "attributes": {}}


def test_parse_streamed_response_strips_duplicate_command_block(hass) -> None:
    """Trailing ```command``` echoing the tool-fired call is stripped, and
    the confirmation prose is preserved (not stomped by the policy's
    unbacked-action check).
    """
    client = _make_client(hass)
    text = (
        "Turning off the kitchen light.\n\n"
        "[[entity:light.kitchen|Kitchen]]\n\n"
        "```command\n"
        '{"calls": [{"service": "light.turn_off", "target": {"entity_id": "light.kitchen"}}]}\n'
        "```"
    )
    tool_log = [_executed("light.turn_off", "light.kitchen")]
    parsed = client.parse_streamed_response(
        text, entities=[_ent("light.kitchen")], tool_log=tool_log
    )
    # The ```command``` block was stripped before parsing — the result is
    # a plain prose answer, not a command intent.
    assert parsed.get("intent") != "command"
    assert not parsed.get("calls")
    # Confirmation prose is preserved — policy did not stomp it with the
    # "I didn't run any action" clarification.
    assert "turning off" in parsed["response"].lower()
    assert "didn't run" not in parsed["response"].lower()
    assert parsed.get("suppressed_duplicate_command") is True


def test_parse_streamed_response_fully_stripped_block_preserves_marker_prose(
    hass,
) -> None:
    """Regression: action-like prose with an entity marker survives when
    the entire ```command``` block is stripped. Previously
    _apply_command_policy would rewrite the response into the "no matching
    entity" fallback because the marker text matches the action-confirmation
    pattern.
    """
    client = _make_client(hass)
    text = (
        "Turning off the kitchen light.\n\n"
        "```command\n"
        '{"calls": [{"service": "light.turn_off", "target": {"entity_id": "light.kitchen"}}]}\n'
        "```"
    )
    tool_log = [_executed("light.turn_off", "light.kitchen")]
    parsed = client.parse_streamed_response(
        text, entities=[_ent("light.kitchen")], tool_log=tool_log
    )
    # Crucially, the response text starts with "Turning off" — the policy's
    # _looks_like_unbacked_action matcher would catch this. The suppressed
    # flag must be present and the policy must short-circuit.
    assert parsed["response"].startswith("Turning off")
    assert "validation_error" not in parsed


def test_parse_streamed_response_preserves_delayed_followup(hass) -> None:
    """Regression: 'turn fan on now and off in 10 minutes' must keep the delayed block."""
    client = _make_client(hass)
    text = (
        "Turning the bedroom fan on and scheduling it off in 10 minutes.\n\n"
        "[[entity:fan.bedroom|Bedroom Fan]]\n\n"
        "```delayed_command\n"
        '{"calls": [{"service": "fan.turn_off", "target": {"entity_id": "fan.bedroom"}}], '
        '"delay_seconds": 600}\n'
        "```"
    )
    tool_log = [_executed("fan.turn_on", "fan.bedroom")]
    parsed = client.parse_streamed_response(
        text, entities=[_ent("fan.bedroom")], tool_log=tool_log
    )
    assert parsed["intent"] == "delayed_command"
    assert parsed.get("delay_seconds") == 600
    assert len(parsed["calls"]) == 1
    assert parsed["calls"][0]["service"] == "fan.turn_off"


def test_parse_streamed_response_keeps_non_duplicate_command_block(hass) -> None:
    """Command block targeting a different entity must be preserved."""
    client = _make_client(hass)
    text = (
        "Turning the kitchen light off and the bedroom on.\n\n"
        "```command\n"
        '{"calls": [{"service": "light.turn_on", "target": {"entity_id": "light.bedroom"}}]}\n'
        "```"
    )
    tool_log = [_executed("light.turn_off", "light.kitchen")]
    parsed = client.parse_streamed_response(
        text, entities=[_ent("light.bedroom"), _ent("light.kitchen")], tool_log=tool_log
    )
    assert parsed["intent"] == "command"
    assert len(parsed["calls"]) == 1
    assert parsed["calls"][0]["target"]["entity_id"] == "light.bedroom"


def test_parse_streamed_response_filters_mixed_block(hass) -> None:
    """Fix #2: a ```command``` block with both a duplicate AND a new call
    must keep only the new call. Otherwise the duplicate (e.g. toggle)
    re-runs and undoes the user's action.
    """
    client = _make_client(hass)
    text = (
        "Doing both.\n\n"
        "```command\n"
        '{"calls": ['
        '{"service": "light.toggle", "target": {"entity_id": "light.kitchen"}},'
        '{"service": "light.turn_on", "target": {"entity_id": "light.bedroom"}}'
        "]}\n"
        "```"
    )
    tool_log = [_executed("light.toggle", "light.kitchen")]
    parsed = client.parse_streamed_response(
        text,
        entities=[_ent("light.kitchen"), _ent("light.bedroom")],
        tool_log=tool_log,
    )
    assert parsed["intent"] == "command"
    services = [c["service"] for c in parsed["calls"]]
    targets = [c["target"]["entity_id"] for c in parsed["calls"]]
    # The toggle on light.kitchen was already executed — must not re-fire.
    assert "light.toggle" not in services
    assert "light.kitchen" not in targets
    # The new call on light.bedroom survives.
    assert "light.turn_on" in services
    assert "light.bedroom" in targets


def test_parse_streamed_response_does_not_strip_after_failed_tool(hass) -> None:
    """Fix #3 (streaming): a fallback ```command``` block after a failed
    tool call must not be stripped (the service never ran)."""
    client = _make_client(hass)
    text = (
        "Falling back to the JSON path.\n\n"
        "```command\n"
        '{"calls": [{"service": "light.turn_off", "target": {"entity_id": "light.kitchen"}}]}\n'
        "```"
    )
    tool_log = [_failed_runtime("light.turn_off", "light.kitchen")]
    parsed = client.parse_streamed_response(
        text, entities=[_ent("light.kitchen")], tool_log=tool_log
    )
    assert parsed["intent"] == "command"
    assert len(parsed["calls"]) == 1
    assert parsed["calls"][0]["target"]["entity_id"] == "light.kitchen"


def test_parse_streamed_response_strips_command_block_before_quick_actions(
    hass,
) -> None:
    """Regression: when the model appends ```quick_actions``` after the
    duplicate ```command``` block, the command must still be stripped.
    Otherwise quick_actions extraction shifts the command into terminal
    position and it gets executed a second time.
    """
    client = _make_client(hass)
    text = (
        "Turning off the kitchen light.\n\n"
        "```command\n"
        '{"calls": [{"service": "light.turn_off", "target": {"entity_id": "light.kitchen"}}]}\n'
        "```\n\n"
        "```quick_actions\n"
        '[{"label": "Also turn off bedroom", "value": "turn off bedroom light", "mode": "suggestion"}]\n'
        "```"
    )
    tool_log = [_executed("light.turn_off", "light.kitchen")]
    parsed = client.parse_streamed_response(
        text, entities=[_ent("light.kitchen")], tool_log=tool_log
    )
    assert parsed.get("intent") != "command"
    assert not parsed.get("calls")
    # quick_actions still extracted and attached
    assert parsed.get("quick_actions")
    assert parsed["quick_actions"][0]["label"] == "Also turn off bedroom"


def test_guard_does_not_suppress_parameterized_variant() -> None:
    """Fix (data in signature): tool ran light.turn_on without data; the
    final call adds brightness_pct → not a duplicate, must survive.
    """
    parsed = {
        "intent": "command",
        "response": "Setting the kitchen light to 60%.",
        "calls": [
            {
                "service": "light.turn_on",
                "target": {"entity_id": "light.kitchen"},
                "data": {"brightness_pct": 60},
            }
        ],
    }
    tool_log = [_executed("light.turn_on", "light.kitchen")]  # no data
    result = _suppress_duplicate_command_after_tool(parsed, tool_log)
    assert result is parsed
    assert result["intent"] == "command"
    assert result["calls"][0]["data"]["brightness_pct"] == 60


def test_guard_suppresses_exact_match_including_data() -> None:
    """Tool ran with brightness_pct=60 and final block echoes the same →
    duplicate, suppressed.
    """
    parsed = {
        "intent": "command",
        "response": "Setting it to 60%.",
        "calls": [
            {
                "service": "light.turn_on",
                "target": {"entity_id": "light.kitchen"},
                "data": {"brightness_pct": 60},
            }
        ],
    }
    tool_log = [_executed("light.turn_on", "light.kitchen", {"brightness_pct": 60})]
    result = _suppress_duplicate_command_after_tool(parsed, tool_log)
    assert result["intent"] == "answer"
    assert result["suppressed_duplicate_command"] is True


def test_guard_keeps_different_data_value() -> None:
    """Tool ran with brightness_pct=60; final block has brightness_pct=80 →
    different request, must survive.
    """
    parsed = {
        "intent": "command",
        "response": "Now bumping it to 80%.",
        "calls": [
            {
                "service": "light.turn_on",
                "target": {"entity_id": "light.kitchen"},
                "data": {"brightness_pct": 80},
            }
        ],
    }
    tool_log = [_executed("light.turn_on", "light.kitchen", {"brightness_pct": 60})]
    result = _suppress_duplicate_command_after_tool(parsed, tool_log)
    assert result is parsed
    assert result["calls"][0]["data"]["brightness_pct"] == 80


def test_guard_suppresses_malformed_service_echo_after_repair() -> None:
    """Regression: model used execute_command(cover.open_cover, cover.garage_door),
    then echoes a JSON call with service='cover.garage_door' (entity_id stuffed
    into the service field). Without service repair, the signatures wouldn't
    match and _apply_command_policy would repair-and-re-execute. The guard
    must repair the service before comparing.
    """
    parsed = {
        "intent": "command",
        "response": "Opening the garage door.",
        "calls": [
            {
                "service": "cover.garage_door",  # malformed — entity_id in service field
                "target": {"entity_id": "cover.garage_door"},
            }
        ],
    }
    tool_log = [_executed("cover.open_cover", "cover.garage_door")]
    result = _suppress_duplicate_command_after_tool(parsed, tool_log)
    assert result["intent"] == "answer"
    assert result["suppressed_duplicate_command"] is True
    assert "calls" not in result


def test_parse_streamed_response_strips_malformed_service_echo(hass) -> None:
    """Streaming counterpart: ```command``` block with malformed service
    after a successful execute_command. Prose before the block ("Opening
    the garage door") is what the repair uses to infer the intended verb.
    """
    client = _make_client(hass)
    text = (
        "Opening the garage door.\n\n"
        "```command\n"
        '{"calls": [{"service": "cover.garage_door", '
        '"target": {"entity_id": "cover.garage_door"}}]}\n'
        "```"
    )
    tool_log = [_executed("cover.open_cover", "cover.garage_door")]
    parsed = client.parse_streamed_response(
        text, entities=[_ent("cover.garage_door")], tool_log=tool_log
    )
    assert parsed.get("intent") != "command"
    assert not parsed.get("calls")


def test_parse_streamed_response_preserves_parameterized_followup(hass) -> None:
    """Streaming counterpart: tool fired without data, block adds
    brightness_pct → block must survive (set brightness still needs to run).
    """
    client = _make_client(hass)
    text = (
        "Turning on the kitchen light at 60%.\n\n"
        "```command\n"
        '{"calls": [{"service": "light.turn_on", '
        '"target": {"entity_id": "light.kitchen"}, '
        '"data": {"brightness_pct": 60}}]}\n'
        "```"
    )
    tool_log = [_executed("light.turn_on", "light.kitchen")]
    parsed = client.parse_streamed_response(
        text, entities=[_ent("light.kitchen")], tool_log=tool_log
    )
    assert parsed["intent"] == "command"
    assert len(parsed["calls"]) == 1
    assert parsed["calls"][0]["data"]["brightness_pct"] == 60


def test_parse_streamed_response_keeps_command_block_without_tool_log(hass) -> None:
    """Without tool_log, the ```command``` block parses normally."""
    client = _make_client(hass)
    text = (
        "Turning off the kitchen light.\n\n"
        "```command\n"
        '{"calls": [{"service": "light.turn_off", "target": {"entity_id": "light.kitchen"}}]}\n'
        "```"
    )
    parsed = client.parse_streamed_response(text)
    assert parsed["intent"] == "command"
    assert len(parsed["calls"]) == 1


# ── ToolExecutor.call_log ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_executor_appends_to_call_log(hass) -> None:
    from custom_components.selora_ai.tool_executor import ToolExecutor

    dm = MagicMock()
    executor = ToolExecutor(hass, dm, is_admin=True)
    assert executor.call_log == []

    # Use validate_action — pure function, no service dispatch needed.
    await executor.execute(
        "validate_action",
        {"service": "light.turn_on", "entity_id": "light.somewhere"},
    )
    assert len(executor.call_log) == 1
    entry = executor.call_log[0]
    assert entry["tool"] == "validate_action"
    assert entry["arguments"] == {
        "service": "light.turn_on",
        "entity_id": "light.somewhere",
    }
    # The handler's return value is captured so the duplicate-execution
    # guard can distinguish actual service execution from validation
    # failure (validate_action never executes — result has 'valid' field).
    assert "result" in entry
    assert "valid" in entry["result"]


@pytest.mark.asyncio
async def test_tool_executor_records_result_on_handler_exception(hass) -> None:
    """Even when a handler raises, the failure is recorded in the log."""
    from custom_components.selora_ai.tool_executor import ToolExecutor

    dm = MagicMock()
    executor = ToolExecutor(hass, dm, is_admin=True)
    # eval_template with an unparseable template — handler returns error dict.
    result = await executor.execute("eval_template", {"template": "{{ states("})
    assert "error" in result
    assert len(executor.call_log) == 1
    assert executor.call_log[0]["result"] == result
    # Crucially: this entry would NOT cause the duplicate guard to flag
    # a subsequent execute_command (different tool name anyway, but the
    # principle is that failed dispatches are recorded with their error).


@pytest.mark.asyncio
async def test_tool_executor_does_not_log_unknown_tool(hass) -> None:
    from custom_components.selora_ai.tool_executor import ToolExecutor

    dm = MagicMock()
    executor = ToolExecutor(hass, dm, is_admin=True)
    result = await executor.execute("nope_does_not_exist", {})
    assert "error" in result
    assert executor.call_log == []
