"""Tests for the execute_command prompt guidance and the duplicate-execution guard."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.selora_ai.llm_client import (
    LLMClient,
    _build_command_confirmation,
    _executed_service_calls_from_log,
    _read_prompt_files,
    _suppress_duplicate_command_after_tool,
    _tool_failure_response,
)
from custom_components.selora_ai.llm_client import prompts as _prompts_mod
from custom_components.selora_ai.llm_client.command_policy import apply_command_policy
from custom_components.selora_ai.llm_client.parsers import parse_architect_response
from custom_components.selora_ai.llm_client.prompts import (
    build_architect_stream_system_prompt,
    build_architect_system_prompt,
)
from custom_components.selora_ai.providers import create_provider


@pytest.fixture(autouse=True)
def _enable_custom_component(enable_custom_integrations):
    """Auto-enable custom integrations so our domain is discoverable."""


@pytest.fixture(autouse=True)
def _preload_prompts():
    """Load prompt files so system prompts include tool-policy text."""
    _prompts_mod._TOOL_POLICY_TEXT, _prompts_mod._DEVICE_KNOWLEDGE_TEXT = _read_prompt_files()


def _make_client(hass) -> LLMClient:
    provider = create_provider("anthropic", hass, api_key="test-key")
    return LLMClient(hass, provider)


# ── Prompt content (JSON architect) ──────────────────────────────────────────


def test_json_prompt_mentions_execute_command_when_tools_available(hass) -> None:
    prompt = build_architect_system_prompt(tools_available=True)
    assert "execute_command" in prompt
    assert "ALREADY run" in prompt
    assert "second time" in prompt


def test_json_prompt_omits_execute_command_without_tools(hass) -> None:
    prompt = build_architect_system_prompt(tools_available=False)
    # The tool-mode paragraph must not be present when tools are unavailable.
    assert "execute_command" not in prompt


# ── Prompt content (streaming architect) ────────────────────────────────────


def test_stream_prompt_mentions_execute_command_when_tools_available(hass) -> None:
    prompt = build_architect_stream_system_prompt(tools_available=True)
    assert "execute_command" in prompt
    assert "ALREADY run" in prompt


def test_stream_prompt_omits_execute_command_without_tools(hass) -> None:
    prompt = build_architect_stream_system_prompt(tools_available=False)
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


def _requires_approval(service: str, entity_id: str, risk_level: str = "high") -> dict:
    """A tool-log entry where execute_command returned requires_approval."""
    return {
        "tool": "execute_command",
        "arguments": {"service": service, "entity_id": entity_id},
        "result": {
            "valid": False,
            "requires_approval": True,
            "service": service,
            "risk_level": risk_level,
            "approval_reason": "test reason",
        },
    }


def test_synthesize_approval_from_tool_log_upgrades_answer() -> None:
    """Regression: when execute_command returns requires_approval and the
    LLM narrates the result instead of emitting a command JSON block,
    the integration must auto-synthesize a command_approval proposal so
    the user still gets the approval card."""
    from custom_components.selora_ai.llm_client import (
        synthesize_approval_from_tool_log,
    )

    parsed = {
        "intent": "answer",
        "response": "I can't unlock the door directly — it requires approval.",
    }
    tool_log = [_requires_approval("lock.unlock", "lock.front")]
    result = synthesize_approval_from_tool_log(parsed, tool_log)
    assert result["intent"] == "command_approval"
    assert result.get("calls") == []
    approval = result["command_approval"]
    assert approval["risk_level"] == "high"
    assert len(approval["calls"]) == 1
    assert approval["calls"][0]["service"] == "lock.unlock"
    assert approval["calls"][0]["target"] == {"entity_id": ["lock.front"]}
    assert result["quick_actions"]
    assert all(qa["value"].startswith("approve:") for qa in result["quick_actions"])


def test_synthesize_approval_skips_when_command_intent_present() -> None:
    """If the LLM properly emitted a command intent, the explicit JSON
    path takes precedence over synthesis from the tool log."""
    from custom_components.selora_ai.llm_client import (
        synthesize_approval_from_tool_log,
    )

    parsed = {
        "intent": "command",
        "response": "Unlocking.",
        "calls": [{"service": "lock.unlock", "target": {"entity_id": "lock.front"}}],
    }
    tool_log = [_requires_approval("lock.unlock", "lock.front")]
    result = synthesize_approval_from_tool_log(parsed, tool_log)
    assert result is parsed  # untouched


def test_synthesize_approval_preserves_executed_action() -> None:
    """P2: when one tool round both EXECUTES a write (light off) and holds
    another for approval (unlock), the executed action must be acknowledged
    in the synthesized response — not dropped behind the approval card."""
    from custom_components.selora_ai.llm_client import (
        synthesize_approval_from_tool_log,
    )

    parsed = {"intent": "answer", "response": "Editorialised narration."}
    tool_log = [
        _executed("light.turn_off", "light.kitchen"),
        _requires_approval("lock.unlock", "lock.front"),
    ]
    result = synthesize_approval_from_tool_log(parsed, tool_log)
    assert result["intent"] == "command_approval"
    # Executed light is confirmed; approval hint still present.
    assert "Turned off" in result["response"]
    assert "light.kitchen" in result["response"]
    assert "approval" in result["response"].lower()
    # The approval card holds ONLY the unlock — the executed light is not
    # re-proposed for approval.
    calls = result["command_approval"]["calls"]
    assert len(calls) == 1
    assert calls[0]["service"] == "lock.unlock"


def test_synthesize_approval_normalizes_executed_scene() -> None:
    """P3: an activated scene in the same round as a held call must surface
    as 'Activated …' (scene.turn_on normalization), not be silently dropped
    because the raw scene result carries no ``service`` field."""
    from custom_components.selora_ai.llm_client import (
        synthesize_approval_from_tool_log,
    )

    parsed = {"intent": "answer", "response": "narration"}
    tool_log = [
        _scene_activated("scene.movie_night"),
        _requires_approval("lock.unlock", "lock.front"),
    ]
    result = synthesize_approval_from_tool_log(parsed, tool_log)
    assert result["intent"] == "command_approval"
    assert "Activated" in result["response"]
    assert "scene.movie_night" in result["response"]


def test_synthesize_approval_hint_only_when_nothing_executed() -> None:
    """No executed write in the round → bare approval hint, no fabricated
    confirmation prefix."""
    from custom_components.selora_ai.llm_client import (
        synthesize_approval_from_tool_log,
    )

    parsed = {"intent": "answer", "response": "narration"}
    tool_log = [_requires_approval("lock.unlock", "lock.front")]
    result = synthesize_approval_from_tool_log(parsed, tool_log)
    assert result["response"] == "This request needs your approval before I run it."


def test_normalized_write_result_maps_scene_to_turn_on() -> None:
    """P3 (short-circuit path): activate_scene results carry no ``service``,
    so they're mapped to scene.turn_on so build_executed_confirmation can
    render them. execute_command results pass through unchanged."""
    from custom_components.selora_ai.llm_client.client import (
        _normalized_write_result,
    )

    scene = _normalized_write_result(
        "activate_scene", {"entity_id": "scene.movie_night", "status": "activated"}
    )
    assert scene == {"service": "scene.turn_on", "entity_ids": ["scene.movie_night"]}
    cmd = {"executed": True, "service": "light.turn_off", "entity_ids": ["light.kitchen"]}
    assert _normalized_write_result("execute_command", cmd) is cmd
    # A scene without entity_id can't be confirmed → None.
    assert _normalized_write_result("activate_scene", {"status": "activated"}) is None


def test_build_executed_confirmation_excludes_already_shown_marker() -> None:
    """Regression: the model narrates 'Locking the Front Door' with an
    [[entity:lock.front_door]] tile in its pre-tool prose, then the
    short-circuit synthesizes 'Locked Front Door.' If the confirmation
    re-emits a marker for the same entity, the chat renders two identical
    cards. Excluding already-shown ids drops the duplicate marker while
    keeping the sentence.
    """
    from custom_components.selora_ai.llm_client.command_policy import (
        build_executed_confirmation,
    )

    calls = [{"service": "lock.lock", "entity_ids": ["lock.front_door"]}]
    # No exclusion → marker present.
    full = build_executed_confirmation(calls)
    assert "[[entities:lock.front_door]]" in full
    # Entity already shown upstream → marker dropped, sentence kept.
    deduped = build_executed_confirmation(calls, exclude_marker_ids={"lock.front_door"})
    assert "[[entities:" not in deduped
    assert "lock.front_door" in full and "Locked" in deduped


def test_build_executed_confirmation_keeps_unshown_marker_ids() -> None:
    """Only entities already shown are dropped; a second target still
    renders its tile."""
    from custom_components.selora_ai.llm_client.command_policy import (
        build_executed_confirmation,
    )

    calls = [{"service": "lock.lock", "entity_ids": ["lock.front_door", "lock.back_door"]}]
    out = build_executed_confirmation(calls, exclude_marker_ids={"lock.front_door"})
    assert "[[entities:lock.back_door]]" in out
    assert "lock.front_door" not in out.split("[[entities:")[1]


async def test_architect_chat_approval_short_circuit_yields_card(hass, monkeypatch) -> None:
    """Regression: the non-stream tool loop returns a non-empty sentinel
    on an approval short-circuit. ``architect_chat`` treats a falsy
    ``result_text`` as an LLM failure, so returning "" turned "unlock the
    front door" into the generic LLM error instead of the approval card."""
    from unittest.mock import AsyncMock

    from custom_components.selora_ai.llm_client.command_policy import (
        APPROVAL_PENDING_HINT,
    )

    client = _make_client(hass)
    tool_log = [_requires_approval("lock.unlock", "lock.front")]
    monkeypatch.setattr(
        client,
        "_send_request_with_tools",
        AsyncMock(return_value=(APPROVAL_PENDING_HINT, None, tool_log)),
    )

    result = await client.architect_chat(
        "unlock the front door",
        entities=[_ent("lock.front")],
        tool_executor=MagicMock(),
    )

    assert result["intent"] == "command_approval"
    assert "error" not in result
    assert result["command_approval"]["calls"][0]["service"] == "lock.unlock"


async def test_architect_chat_nontool_approval_escalates_cover_risk(hass, monkeypatch) -> None:
    """P2: the non-tool chat path must pass ``hass`` to
    ``synthesize_approval_from_tool_log`` so device-class elevation applies.
    A non-tool provider that emits an explicit ``command_approval`` for
    ``cover.open_cover`` on a garage cover would otherwise show LOW risk
    while approving executes a high-risk physical-access action.
    """
    import json
    from unittest.mock import AsyncMock

    hass.states.async_set("cover.garage", "closed", {"device_class": "garage"})
    client = _make_client(hass)
    card_json = json.dumps(
        {
            "intent": "command_approval",
            "response": "needs approval",
            "command_approval": {
                "risk_level": "low",
                "calls": [
                    {"service": "cover.open_cover", "target": {"entity_id": ["cover.garage"]}},
                ],
            },
        }
    )
    monkeypatch.setattr(
        client._provider,
        "send_request",
        AsyncMock(return_value=(card_json, None)),
    )

    result = await client.architect_chat(
        "open the garage",
        entities=[_ent("cover.garage")],
    )

    assert result["intent"] == "command_approval"
    assert result["command_approval"]["risk_level"] == "high"


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


def _scene_activated(entity_id: str) -> dict:
    """A tool-log entry for a successful activate_scene call."""
    return {
        "tool": "activate_scene",
        "arguments": {"entity_id": entity_id},
        "result": {"entity_id": entity_id, "status": "activated"},
    }


def _scene_failed(entity_id: str) -> dict:
    """A tool-log entry for a failed activate_scene call."""
    return {
        "tool": "activate_scene",
        "arguments": {"entity_id": entity_id},
        "result": {"error": "Activation failed: device offline"},
    }


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
    with another REVIEW-bucket call (e.g. ``lock.unlock``) must route the
    surviving call to ``command_approval`` so the user explicitly authorizes
    it — never smuggled past the gate.
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

    # Policy must route lock.unlock through the approval gate — it's in
    # the REVIEW bucket with HIGH risk, not the SAFE allowlist.
    final = apply_command_policy(
        suppressed,
        [
            {"entity_id": "light.kitchen", "state": "on", "attributes": {}},
            {"entity_id": "lock.front_door", "state": "locked", "attributes": {}},
        ],
    )
    assert final["intent"] == "command_approval"
    assert final.get("calls") == []  # nothing executes until user approves
    approval = final["command_approval"]
    assert approval["risk_level"] == "high"
    assert len(approval["calls"]) == 1
    assert approval["calls"][0]["service"] == "lock.unlock"


def test_streamed_command_approval_keeps_policy_quick_actions(hass) -> None:
    """P2: a streamed response carrying both a ``command`` block (which the
    policy converts to ``command_approval``) and a model-supplied
    ``quick_actions`` block must keep the policy-generated
    ``approve:<scope>:<proposal_id>`` / ``deny`` sentinels. Overwriting them
    with the model's quick_actions leaves an unresolvable card.
    """
    client = _make_client(hass)
    text = (
        "I can unlock the front door, but it needs your OK.\n\n"
        "```command\n"
        '{"calls": [{"service": "lock.unlock", "target": {"entity_id": "lock.front_door"}}]}\n'
        "```\n\n"
        "```quick_actions\n"
        '[{"label": "Sure", "value": "noop:yes"}, {"label": "Nope", "value": "noop:no"}]\n'
        "```"
    )
    parsed = client.parse_streamed_response(text, entities=[_ent("lock.front_door")], tool_log=None)
    assert parsed["intent"] == "command_approval"
    pid = parsed["command_approval"]["proposal_id"]
    values = [qa["value"] for qa in parsed["quick_actions"]]
    assert f"approve:once:{pid}" in values
    assert f"approve:deny:{pid}" in values
    # Model's bogus actions did not leak through.
    assert not any(v.startswith("noop:") for v in values)


def _executed_targetless(service: str, data: dict) -> dict:
    """Tool-log entry for a successful targetless write (notify/script/
    shell_command) — execute_command returns an empty ``entity_ids``."""
    return {
        "tool": "execute_command",
        "arguments": {"service": service, "data": data},
        "result": {"executed": True, "service": service, "entity_ids": [], "states": []},
    }


def test_targetless_write_echo_is_suppressed(hass) -> None:
    """P2: an approved targetless REVIEW write (notify.mobile_app_*) ran via
    execute_command. The model echoes the same call in a final ``command``
    block. Because executed signatures used to ignore empty entity_ids, the
    echo slipped past the duplicate guard and fired a second notification.
    The echo with a matching data payload must be stripped.
    """
    parsed = {
        "intent": "command",
        "response": "Sent the alert.",
        "calls": [
            {"service": "notify.mobile_app_phone", "data": {"message": "Garage left open"}},
        ],
    }
    tool_log = [_executed_targetless("notify.mobile_app_phone", {"message": "Garage left open"})]
    suppressed = _suppress_duplicate_command_after_tool(parsed, tool_log)
    assert not suppressed.get("calls")


def test_targetless_write_distinct_payload_survives(hass) -> None:
    """A second targetless write with a different message is NOT a duplicate
    — the data payload keeps the signatures distinct, so it must survive.
    """
    parsed = {
        "intent": "command",
        "response": "Sending both.",
        "calls": [
            {"service": "notify.mobile_app_phone", "data": {"message": "second alert"}},
        ],
    }
    tool_log = [_executed_targetless("notify.mobile_app_phone", {"message": "first alert"})]
    suppressed = _suppress_duplicate_command_after_tool(parsed, tool_log)
    assert len(suppressed["calls"]) == 1
    assert suppressed["calls"][0]["data"]["message"] == "second alert"


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
    final = apply_command_policy(
        suppressed,
        [
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
    """Regression: tool-loop exhaustion synthesizes prose via
    _build_command_confirmation; the streaming parser must mark it
    suppressed so the policy doesn't rewrite it to 'I didn't run any
    action'. Same logic covers the JSON path via architect_chat.
    """
    client = _make_client(hass)
    tool_log = [_executed("light.turn_off", "light.kitchen")]
    executed_calls = _executed_service_calls_from_log(tool_log)
    # Build the exact prose the synthesizer (_tool_failure_response)
    # would produce. Anchoring to the helper output ensures the test
    # mirrors the real path that _stream_request_with_tools takes on
    # exhaustion.
    text = (
        _build_command_confirmation(executed_calls)
        + " Then I ran out of tool rounds before finishing — try a more "
        "specific request only if there's more to do."
    )
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


def test_streaming_no_flag_for_unbacked_claim_after_tool(hass) -> None:
    """Regression: tool ran for kitchen, but the model's free-form prose
    claims an action on a *different* device that never ran. The flag
    must NOT be set — the policy's unbacked-action guard must still kick
    in so the user isn't told a bedroom action happened when it didn't.
    """
    client = _make_client(hass)
    # Prose claims bedroom action with no command block. "Turning off"
    # triggers _looks_like_unbacked_action, and the prose does NOT match
    # the synthesized "Done — light turn off (kitchen)." prefix that
    # _build_command_confirmation would produce for the executed call.
    text = "Turning off the bedroom light."
    tool_log = [_executed("light.turn_off", "light.kitchen")]
    parsed = client.parse_streamed_response(
        text,
        entities=[_ent("light.kitchen"), _ent("light.bedroom")],
        tool_log=tool_log,
    )
    assert parsed.get("suppressed_duplicate_command") is not True
    # Policy stomps the unbacked action prose with the clarification.
    assert "didn't run" in parsed["response"].lower() or "rephrase" in parsed["response"].lower()


def test_streaming_generic_ack_survives_policy_after_tool(hass) -> None:
    """Regression: model returns a generic 'Done.' after the tool fired.
    The policy's unbacked-action regex matches 'done', and without the
    suppression flag the prose would be rewritten to 'I didn't run any
    action'. Generic acks must be trusted when a tool ran.
    """
    client = _make_client(hass)
    tool_log = [_executed("light.turn_off", "light.kitchen")]
    # Try each generic-ack variant.
    for ack in ("Done.", "Done!", "All set.", "Got it.", "OK, done.", "Sure."):
        parsed = client.parse_streamed_response(
            ack, entities=[_ent("light.kitchen")], tool_log=tool_log
        )
        assert parsed.get("suppressed_duplicate_command") is True, (
            f"generic ack {ack!r} should be trusted after successful tool"
        )
        assert parsed["response"] == ack
        assert "didn't run" not in parsed["response"].lower()


def test_streaming_generic_ack_without_tool_not_trusted(hass) -> None:
    """A bare 'Done.' without any tool execution is still an unbacked
    claim — policy stomps. The generic-ack trust only applies when a
    successful execute_command is in the log.
    """
    client = _make_client(hass)
    parsed = client.parse_streamed_response(
        "Done.", entities=[_ent("light.kitchen")], tool_log=None
    )
    assert parsed.get("suppressed_duplicate_command") is not True


def test_streaming_same_action_natural_prose_survives_policy(hass) -> None:
    """Regression: tool ran light.kitchen, model returns natural-prose
    confirmation that names the same entity ('Turning off the kitchen
    light'). Must trust — not a hallucination, just a non-synthesized
    confirmation of the actual action.
    """
    client = _make_client(hass)
    tool_log = [_executed("light.turn_off", "light.kitchen")]
    parsed = client.parse_streamed_response(
        "Turning off the kitchen light.",
        entities=[_ent("light.kitchen")],
        tool_log=tool_log,
    )
    assert parsed.get("suppressed_duplicate_command") is True
    assert parsed["response"] == "Turning off the kitchen light."
    assert "didn't run" not in parsed["response"].lower()


def test_streaming_opposite_action_prose_not_trusted(hass) -> None:
    """Regression: tool ran light.turn_on for kitchen, but prose says
    'Turning off'. Same entity, opposite action — must NOT trust, so
    policy stomps the contradictory claim instead of confirming an
    action that didn't happen.
    """
    client = _make_client(hass)
    tool_log = [_executed("light.turn_on", "light.kitchen")]
    parsed = client.parse_streamed_response(
        "Turning off the kitchen light.",
        entities=[_ent("light.kitchen")],
        tool_log=tool_log,
    )
    assert parsed.get("suppressed_duplicate_command") is not True
    assert "didn't run" in parsed["response"].lower() or "rephrase" in parsed["response"].lower()


def test_streaming_multi_token_entity_match(hass) -> None:
    """Multi-token entity_id (kitchen_island) — prose matching any non-
    stopword token still trusts."""
    client = _make_client(hass)
    tool_log = [_executed("light.turn_on", "light.kitchen_island")]
    parsed = client.parse_streamed_response(
        "Turning on the island lamp at 60%.",
        entities=[_ent("light.kitchen_island")],
        tool_log=tool_log,
    )
    assert parsed.get("suppressed_duplicate_command") is True


def test_guard_suppresses_duplicate_scene_turn_on_after_activate_scene() -> None:
    """Regression: activate_scene successfully ran scene.movie_night; the
    model also echoes a scene.turn_on command block. Without
    activate_scene in the tracked-tools list, the duplicate guard would
    miss this and the scene would fire a second time via
    _execute_command_calls.
    """
    parsed = {
        "intent": "command",
        "response": "Activating Movie Night.",
        "calls": [
            {"service": "scene.turn_on", "target": {"entity_id": "scene.movie_night"}},
        ],
    }
    tool_log = [_scene_activated("scene.movie_night")]
    result = _suppress_duplicate_command_after_tool(parsed, tool_log)
    assert result["intent"] == "answer"
    assert result["suppressed_duplicate_command"] is True
    assert "calls" not in result


def test_guard_does_not_suppress_after_failed_activate_scene() -> None:
    """Failed scene activation must NOT count as executed — fallback
    command block must be allowed through to the policy."""
    parsed = {
        "intent": "command",
        "response": "Activating Movie Night.",
        "calls": [
            {"service": "scene.turn_on", "target": {"entity_id": "scene.movie_night"}},
        ],
    }
    tool_log = [_scene_failed("scene.movie_night")]
    result = _suppress_duplicate_command_after_tool(parsed, tool_log)
    assert result is parsed


def test_executed_service_calls_includes_activate_scene() -> None:
    """Failure-path synthesis must surface scene activations so users
    aren't told nothing happened after the scene fired."""
    log = [
        _executed("light.turn_off", "light.kitchen"),
        _scene_activated("scene.movie_night"),
        _scene_failed("scene.bedtime"),
    ]
    calls = _executed_service_calls_from_log(log)
    services = [c["service"] for c in calls]
    targets = [c["target"]["entity_id"] for c in calls]
    assert services == ["light.turn_off", "scene.turn_on"]
    assert ["light.kitchen"] in targets
    assert ["scene.movie_night"] in targets
    # Failed scene activation is NOT counted.
    assert ["scene.bedtime"] not in targets


def test_streaming_block_strip_with_mismatched_prose_rejected(hass) -> None:
    """Regression: the duplicate block is stripped because the
    model echoed light.kitchen — which matches the executed tool call —
    but the prose talks about light.bedroom. The block-strip path used
    to set the trust flag unconditionally, letting the false bedroom
    confirmation slip through. Now the Stage-1 unbacked-entity check
    catches it and the policy stomps the response.
    """
    client = _make_client(hass)
    text = (
        "Turning off the bedroom light.\n\n"
        "```command\n"
        '{"calls": [{"service": "light.turn_off", "target": {"entity_id": "light.kitchen"}}]}\n'
        "```"
    )
    tool_log = [_executed("light.turn_off", "light.kitchen")]
    parsed = client.parse_streamed_response(
        text,
        entities=[_ent("light.kitchen"), _ent("light.bedroom")],
        tool_log=tool_log,
    )
    assert parsed.get("suppressed_duplicate_command") is not True
    assert "didn't run" in parsed["response"].lower() or "rephrase" in parsed["response"].lower()


def test_streaming_block_strip_with_matching_prose_trusted(hass) -> None:
    """Positive case: the block-strip path still trusts prose that
    describes the executed action (or makes no contradictory claim).
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
        text,
        entities=[_ent("light.kitchen"), _ent("light.bedroom")],
        tool_log=tool_log,
    )
    assert parsed.get("suppressed_duplicate_command") is True
    assert parsed["response"].startswith("Turning off the kitchen light")


def test_streaming_mixed_inverse_actions_trusted(hass) -> None:
    """Regression: model runs turn_off(kitchen) and turn_on(porch) via
    tools, then says 'Turned off the kitchen and turned on the porch.'
    Per-mention verb proximity must trust this: kitchen pairs with
    'turned off' (consistent), porch pairs with 'turned on' (consistent).
    """
    client = _make_client(hass)
    tool_log = [
        _executed("light.turn_off", "light.kitchen"),
        _executed("light.turn_on", "light.porch"),
    ]
    parsed = client.parse_streamed_response(
        "Turned off the kitchen and turned on the porch.",
        entities=[_ent("light.kitchen"), _ent("light.porch")],
        tool_log=tool_log,
    )
    assert parsed.get("suppressed_duplicate_command") is True
    assert "didn't run" not in parsed["response"].lower()


def test_streaming_unbacked_extra_entity_rejected(hass) -> None:
    """Regression: tool runs turn_off(kitchen) only, but prose says
    'Turning off the kitchen and bedroom lights.' The bedroom claim is
    unbacked — stage 1 must veto because bedroom is a known entity in
    the snapshot that wasn't executed. Without the veto, the model's
    mention of bedroom would be silently confirmed.
    """
    client = _make_client(hass)
    tool_log = [_executed("light.turn_off", "light.kitchen")]
    parsed = client.parse_streamed_response(
        "Turning off the kitchen and bedroom lights.",
        entities=[_ent("light.kitchen"), _ent("light.bedroom")],
        tool_log=tool_log,
    )
    assert parsed.get("suppressed_duplicate_command") is not True
    # Policy stomps because the flag isn't set and "Turning off…" matches
    # the unbacked-action regex.
    assert "didn't run" in parsed["response"].lower() or "rephrase" in parsed["response"].lower()


def test_streaming_mixed_inverse_executed_entity_rejected(hass) -> None:
    """Regression: tool ran turn_off on BOTH kitchen and bedroom, but the
    prose says 'Turned off kitchen and turned on bedroom.' The bedroom
    token IS executed (so stage 1 doesn't veto), but the verb attached
    to its mention is the inverse of the executed service. The matcher
    must require every mention of an executed entity to be verb-
    consistent — not return True on the first consistent mention.
    Without this, the user is shown a false confirmation that bedroom
    was turned on when in fact it was turned off.
    """
    client = _make_client(hass)
    tool_log = [
        _executed("light.turn_off", "light.kitchen"),
        _executed("light.turn_off", "light.bedroom"),
    ]
    parsed = client.parse_streamed_response(
        "Turned off the kitchen and turned on the bedroom.",
        entities=[_ent("light.kitchen"), _ent("light.bedroom")],
        tool_log=tool_log,
    )
    assert parsed.get("suppressed_duplicate_command") is not True


def test_response_describes_executed_call_helper() -> None:
    """Direct unit test for the entity-token + action-verb matcher."""
    from custom_components.selora_ai.llm_client import (
        _response_describes_executed_call,
    )

    executed_off = [{"service": "light.turn_off", "target": {"entity_id": ["light.kitchen"]}}]
    # Positive: matching action + matching entity.
    assert _response_describes_executed_call("Turning off the kitchen light.", executed_off)
    assert _response_describes_executed_call(
        "Done with the Kitchen Lights, anything else?", executed_off
    )
    # Negative: different entity.
    assert not _response_describes_executed_call("Turning off the bedroom light.", executed_off)
    # Negative: only domain mentioned (stopword-filtered).
    assert not _response_describes_executed_call("Turning off the light.", executed_off)
    # Negative: opposite action even with matching entity.
    assert not _response_describes_executed_call("Turning on the kitchen light.", executed_off)
    # Word-boundary: substring of a longer word doesn't match.
    assert not _response_describes_executed_call("I checked them all.", executed_off)

    # turn_on executed; "Turning off the kitchen light" must NOT trust.
    executed_on = [{"service": "light.turn_on", "target": {"entity_id": ["light.kitchen"]}}]
    assert not _response_describes_executed_call("Turning off the kitchen light.", executed_on)
    assert _response_describes_executed_call("Turning on the kitchen light.", executed_on)

    # Cover open vs close.
    executed_open = [{"service": "cover.open_cover", "target": {"entity_id": ["cover.garage"]}}]
    assert _response_describes_executed_call("Opening the garage door.", executed_open)
    assert not _response_describes_executed_call("Closing the garage door.", executed_open)

    # Media play vs pause.
    executed_play = [
        {"service": "media_player.media_play", "target": {"entity_id": ["media_player.kitchen_tv"]}}
    ]
    assert _response_describes_executed_call("Playing the kitchen TV.", executed_play)
    assert not _response_describes_executed_call("Pausing the kitchen TV.", executed_play)

    # Multi-entity executed — match if ANY entity is named.
    multi = [
        {"service": "light.turn_off", "target": {"entity_id": ["light.kitchen"]}},
        {"service": "light.turn_off", "target": {"entity_id": ["light.bedroom"]}},
    ]
    assert _response_describes_executed_call("Bedroom is now off.", multi)

    # Empty inputs.
    assert not _response_describes_executed_call("", executed_off)
    assert not _response_describes_executed_call("Done.", [])

    # Per-mention proximity: mixed inverse actions, both backed.
    mixed_inverse = [
        {"service": "light.turn_off", "target": {"entity_id": ["light.kitchen"]}},
        {"service": "light.turn_on", "target": {"entity_id": ["light.porch"]}},
    ]
    assert _response_describes_executed_call(
        "Turned off the kitchen and turned on the porch.", mixed_inverse
    )
    # Each entity's own nearest verb is the correct one, even though both
    # opposite verbs appear globally in the prose.

    # Per-mention proximity: opposite verb directly adjacent to a single
    # entity still wins even if the right verb appears farther away.
    assert not _response_describes_executed_call(
        "Turned on the kitchen.",
        executed_off,  # turn_off executed
    )

    # Unbacked-entity veto needs entities snapshot.
    entities = [
        {"entity_id": "light.kitchen", "state": "off", "attributes": {}},
        {"entity_id": "light.bedroom", "state": "off", "attributes": {}},
    ]
    # Without entities snapshot — old behavior, just entity-token match.
    assert _response_describes_executed_call("Turned off the kitchen and bedroom.", executed_off)
    # With entities snapshot — bedroom is known but not executed → veto.
    assert not _response_describes_executed_call(
        "Turned off the kitchen and bedroom.", executed_off, entities
    )

    # Shared-token entity isn't unbacked: sensor.kitchen_temp shares
    # "kitchen" with executed light.kitchen, so the mention is fine.
    entities_with_shared = [
        {"entity_id": "light.kitchen", "state": "off", "attributes": {}},
        {"entity_id": "sensor.kitchen_temp", "state": "20", "attributes": {}},
    ]
    assert _response_describes_executed_call(
        "Turned off the kitchen.", executed_off, entities_with_shared
    )


def test_is_generic_acknowledgement_classifier() -> None:
    """Unit test the classifier directly — what it accepts and rejects."""
    from custom_components.selora_ai.llm_client import _is_generic_acknowledgement

    # Accept pure acks
    for text in (
        "Done",
        "Done.",
        "Done!",
        "  done. ",
        "All set",
        "All set.",
        "Got it.",
        "OK, done.",
        "ok done",
        "Sure!",
    ):
        assert _is_generic_acknowledgement(text), f"should accept: {text!r}"

    # Reject anything specific
    for text in (
        "Done — light turn off (kitchen).",
        "Done turning off the kitchen.",
        "Turning off the kitchen light.",
        "I turned off the kitchen light.",
        "",
        "Sure thing — opening the door now.",
    ):
        assert not _is_generic_acknowledgement(text), f"should reject: {text!r}"


def test_response_is_synthesized_confirmation_matches_prefix() -> None:
    """The helper recognizes the literal _build_command_confirmation output."""
    from custom_components.selora_ai.llm_client import (
        _build_command_confirmation,
        _response_is_synthesized_confirmation,
    )

    executed = [{"service": "light.turn_off", "target": {"entity_id": ["light.kitchen"]}}]
    prefix = _build_command_confirmation(executed)
    assert _response_is_synthesized_confirmation(prefix, executed)
    assert _response_is_synthesized_confirmation(prefix + " Then I lost the connection.", executed)
    # Mismatched prose — different entity, doesn't match prefix.
    assert not _response_is_synthesized_confirmation("Now turning off the bedroom light.", executed)
    # Empty inputs — no false positives.
    assert not _response_is_synthesized_confirmation("", executed)
    assert not _response_is_synthesized_confirmation(prefix, [])


def test_parser_strips_model_supplied_suppressed_flag(hass) -> None:
    """SAFETY regression: a model-supplied suppressed_duplicate_command flag
    must NOT survive parsing. Otherwise a prompt could induce JSON that
    bypasses _apply_command_policy and runs arbitrary services.
    """
    malicious = (
        '{"intent":"command",'
        '"response":"sure",'
        '"calls":[{"service":"lock.unlock","target":{"entity_id":"lock.front"}}],'
        '"suppressed_duplicate_command":true}'
    )
    parsed = parse_architect_response(malicious, hass)
    assert "suppressed_duplicate_command" not in parsed
    # Policy MUST run normally. lock.unlock is in the REVIEW bucket so
    # the call is routed to command_approval — never executed silently.
    final = apply_command_policy(
        parsed,
        [{"entity_id": "lock.front", "state": "locked", "attributes": {}}],
    )
    assert final["intent"] == "command_approval"
    assert final.get("calls") == []
    assert final["command_approval"]["risk_level"] == "high"


def test_policy_rejects_bypass_flag_with_command_intent(hass) -> None:
    """Belt+suspenders: even if the flag somehow survives parsing (or is
    set internally on a malformed result), the policy must only honor it
    when the result is intent=answer with no calls. Any other shape
    falls through to full validation.
    """
    # Simulate the worst case: a result with the flag AND a command
    # intent AND calls targeting a forbidden domain.
    spoofed = {
        "intent": "command",
        "response": "Unlocking.",
        "calls": [{"service": "lock.unlock", "target": {"entity_id": "lock.front"}}],
        "suppressed_duplicate_command": True,
    }
    final = apply_command_policy(
        spoofed,
        [{"entity_id": "lock.front", "state": "locked", "attributes": {}}],
    )
    # The early-return must NOT trigger — policy validates the call and
    # routes lock.unlock through the approval gate (REVIEW bucket).
    assert final["intent"] == "command_approval"
    assert final.get("calls") == []
    assert final["command_approval"]["risk_level"] == "high"


def test_policy_preserves_confirmation_after_suppression(hass) -> None:
    """When the duplicate guard has set suppressed_duplicate_command on an
    intent=answer/no-calls result, _apply_command_policy must short-circuit
    so the confirmation prose isn't stomped by the unbacked-action guard.
    This is the only shape the policy honors the flag in — see
    test_policy_rejects_bypass_flag_with_command_intent for the negative.
    """
    # Simulate what _suppress_duplicate_command_after_tool produces.
    suppressed = {
        "intent": "answer",
        "response": "Turning off the kitchen light.",
        "suppressed_duplicate_command": True,
    }
    result = apply_command_policy(suppressed, [])
    assert result["response"] == "Turning off the kitchen light."
    assert "validation_error" not in result


def test_unbacked_action_with_known_entity_marker_recovers_command(hass) -> None:
    """Regression: model narrates 'Locking the front door' with an
    [[entity:lock.front_door]] marker but emits NO calls. The entity is
    known and the verb ('Locking') is unambiguous, so the policy rebuilds
    the lock.lock call and routes it to the approval card instead of
    nagging the user to retry.
    """
    result = {
        "intent": "command",
        "response": "Locking the front door now.\n\n[[entity:lock.front_door|Front Door]]",
        "calls": [],
    }
    final = apply_command_policy(
        result,
        [{"entity_id": "lock.front_door", "state": "unlocked", "attributes": {}}],
    )
    # lock.lock is REVIEW → recovered call routes to an approval card.
    assert final["intent"] == "command_approval"
    assert final["command_approval"]["calls"][0]["service"] == "lock.lock"
    target_ids = final["command_approval"]["calls"][0]["target"]["entity_id"]
    assert "lock.front_door" in (target_ids if isinstance(target_ids, list) else [target_ids])


def test_unbacked_action_recovers_unlock_verb(hass) -> None:
    """The verb table must prefer 'unlock' over 'lock' — 'unlocking'
    contains the substring 'lock'."""
    result = {
        "intent": "command",
        "response": "Unlocking the front door now.\n\n[[entity:lock.front_door|Front Door]]",
        "calls": [],
    }
    final = apply_command_policy(
        result,
        [{"entity_id": "lock.front_door", "state": "locked", "attributes": {}}],
    )
    assert final["intent"] == "command_approval"
    assert final["command_approval"]["calls"][0]["service"] == "lock.unlock"


def test_unbacked_action_without_known_entity_keeps_no_match_message(hass) -> None:
    """Unbacked action prose with no marker (or a marker for an unknown
    entity) keeps the original 'no entity matched' wording — recovery
    needs a known entity to rebuild the call.
    """
    result = {
        "intent": "command",
        "response": "Locking the front door now.",
        "calls": [],
    }
    final = apply_command_policy(
        result,
        [{"entity_id": "light.kitchen", "state": "on", "attributes": {}}],
    )
    assert final["intent"] == "answer"
    assert final["validation_error"] == "no_matching_entity_for_command"
    assert "no entity clearly matched" in final["response"].lower()


def test_unbacked_action_uninferable_verb_keeps_retry_message(hass) -> None:
    """When the entity is known but the verb can't be inferred (the domain
    has no verb-hint table), recovery is skipped and the accurate
    command_not_emitted retry message is returned.
    """
    result = {
        "intent": "command",
        # 'vacuum' has no entry in _SERVICE_REPAIR_HINTS → no recovery.
        "response": "Starting the vacuum now.\n\n[[entity:vacuum.roomba|Roomba]]",
        "calls": [],
    }
    final = apply_command_policy(
        result,
        [{"entity_id": "vacuum.roomba", "state": "docked", "attributes": {}}],
    )
    assert final["intent"] == "answer"
    assert final["validation_error"] == "command_not_emitted"
    assert "again" in final["response"].lower()


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
    parsed = client.parse_streamed_response(text, entities=[_ent("fan.bedroom")], tool_log=tool_log)
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
    duplicate call dropped (no double-execution). The trust flag is only
    set when the accompanying prose matches a trusted shape — see
    test_guard_full_strip_flag_requires_prose_trust below.
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
    # No calls remain — _execute_command_calls won't double-fire.
    assert "calls" not in result or result["calls"] == []


def test_guard_full_strip_flag_requires_prose_trust() -> None:
    """When the duplicate stripper downgrades to answer, every full-strip
    result has intent=answer with no remaining calls (so
    _execute_command_calls won't double-fire). The trust flag on top of
    that is set ONLY when the prose actually describes the executed
    action — generic 'Done.' or specific prose naming the executed
    entity. Prose naming an unexecuted entity is rejected.
    """
    tool_log = [_executed("light.turn_on", "light.kitchen", {"brightness_pct": 60})]
    entities = [
        {"entity_id": "light.kitchen", "state": "off", "attributes": {}},
        {"entity_id": "light.bedroom", "state": "off", "attributes": {}},
    ]

    # (1) Generic ack — flag set; calls removed; intent downgraded.
    parsed_ack = {
        "intent": "command",
        "response": "Done.",
        "calls": [
            {
                "service": "light.turn_on",
                "target": {"entity_id": "light.kitchen"},
                "data": {"brightness_pct": 60},
            }
        ],
    }
    result = _suppress_duplicate_command_after_tool(parsed_ack, tool_log, entities)
    assert result["intent"] == "answer"
    assert "calls" not in result
    assert result["suppressed_duplicate_command"] is True
    # Prose preserved verbatim.
    assert result["response"] == "Done."

    # (2) Specific prose naming executed entity — flag set; prose preserved.
    parsed_named = dict(parsed_ack, response="Setting the kitchen light to 60%.")
    result = _suppress_duplicate_command_after_tool(parsed_named, tool_log, entities)
    assert result["intent"] == "answer"
    assert "calls" not in result
    assert result["suppressed_duplicate_command"] is True
    assert "kitchen light" in result["response"].lower()

    # (3) Specific prose naming an UNEXECUTED entity — flag NOT set,
    # so _apply_command_policy still runs the unbacked-action guard.
    # Calls still removed and intent downgraded — duplicate dispatch
    # is prevented regardless of the trust outcome.
    parsed_bad = dict(parsed_ack, response="Turning off the bedroom light.")
    result = _suppress_duplicate_command_after_tool(parsed_bad, tool_log, entities)
    assert result["intent"] == "answer"
    assert "calls" not in result
    assert "suppressed_duplicate_command" not in result


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


# ── Parametric-call repairs (command_policy._normalize_parametric_calls) ──────


def _climate_ent(entity_id: str, state: str = "heat") -> dict:
    return {"entity_id": entity_id, "state": state, "attributes": {}}


def _light_ent(entity_id: str, state: str = "off") -> dict:
    return {"entity_id": entity_id, "state": state, "attributes": {}}


def test_unknown_climate_is_not_retargeted_to_sole_entity() -> None:
    """An unknown climate id (even a generic-looking 'climate.main') is NOT
    silently retargeted to the home's sole climate entity. Without the user's
    message the helper can't tell a placeholder from an explicit reference to
    a device that doesn't exist, so it must stay blocked → clarification."""
    parsed = {
        "intent": "command",
        "response": "Setting the thermostat to 21 degrees.",
        "calls": [
            {
                "service": "climate.set_temperature",
                "target": {"entity_id": "climate.main"},
                "data": {"temperature": 21},
            }
        ],
    }
    final = apply_command_policy(parsed, [_climate_ent("climate.living_room")])
    assert final["intent"] != "command"
    assert not final.get("calls")


def test_repair_does_not_swap_explicit_unknown_climate() -> None:
    """An explicit named thermostat the home doesn't have (climate.basement)
    must NOT be silently retargeted to the sole climate entity — it is
    rejected so the user gets a clarification."""
    parsed = {
        "intent": "command",
        "response": "Setting the basement thermostat to 21 degrees.",
        "calls": [
            {
                "service": "climate.set_temperature",
                "target": {"entity_id": "climate.basement"},
                "data": {"temperature": 21},
            }
        ],
    }
    final = apply_command_policy(parsed, [_climate_ent("climate.living_room")])
    assert final["intent"] != "command"
    assert not final.get("calls")


def test_repair_dim_without_level_does_not_go_full_bright() -> None:
    """light.dim with no numeric level and no number in prose must NOT default
    to 100% (the opposite action). The bogus verb is left unrepaired and
    rejected rather than firing full brightness."""
    parsed = {
        "intent": "command",
        "response": "Dimming the kitchen light.",
        "calls": [{"service": "light.dim", "target": {"entity_id": "light.kitchen"}, "data": {}}],
    }
    final = apply_command_policy(parsed, [_light_ent("light.kitchen")])
    # Not turned into a full-brightness turn_on.
    for call in final.get("calls", []):
        assert not (
            call.get("service") == "light.turn_on"
            and call.get("data", {}).get("brightness_pct") == 100
        )
    assert final["intent"] != "command"


def test_repair_set_brightness_with_explicit_level() -> None:
    """light.set_brightness with an explicit brightness_pct is repaired to
    light.turn_on carrying that level."""
    parsed = {
        "intent": "command",
        "response": "Setting kitchen light to 40 percent.",
        "calls": [
            {
                "service": "light.set_brightness",
                "target": {"entity_id": "light.kitchen"},
                "data": {"brightness_pct": 40},
            }
        ],
    }
    final = apply_command_policy(parsed, [_light_ent("light.kitchen")])
    assert final["intent"] == "command"
    assert final["calls"][0]["service"] == "light.turn_on"
    assert final["calls"][0]["data"]["brightness_pct"] == 40


def test_repair_temperature_requires_context_not_first_number() -> None:
    """climate.set_temperature missing the temperature key pulls the setpoint
    from temperature context in the prose, not the first stray integer.
    'Zone 2 thermostat to 21 degrees' → 21, never 2."""
    parsed = {
        "intent": "command",
        "response": "Setting Zone 2 thermostat to 21 degrees.",
        "calls": [
            {
                "service": "climate.set_temperature",
                "target": {"entity_id": "climate.zone_2"},
                "data": {},
            }
        ],
    }
    final = apply_command_policy(parsed, [_climate_ent("climate.zone_2")])
    assert final["intent"] == "command"
    assert final["calls"][0]["data"]["temperature"] == 21


def test_wildcard_not_expanded_on_immediate_command_path() -> None:
    """A wildcard (light.*) is NOT expanded into a bulk action on the
    immediate command path — nothing here verifies the user asked for "all"
    devices, so a hallucinated wildcard must be rejected, not fanned out."""
    parsed = {
        "intent": "command",
        "response": "Turning off all the lights.",
        "calls": [{"service": "light.turn_off", "target": {"entity_id": "light.*"}, "data": {}}],
    }
    final = apply_command_policy(
        parsed,
        [_light_ent("light.kitchen", "on"), _light_ent("light.hall", "on")],
    )
    assert final["intent"] != "command"
    assert not final.get("calls")


# ── _execute_command_calls: failed calls kept out of the success list ─────────


async def test_execute_command_calls_separates_failed_from_executed(hass) -> None:
    """A service that raises is recorded in the separate ``failed`` list, never
    in ``executed`` — so an all-failed command does not read as a success."""
    from custom_components.selora_ai import _execute_command_calls

    async def _boom(call) -> None:
        raise ValueError("value out of range")

    hass.services.async_register("climate", "set_temperature", _boom)

    executed, failed, error_suffix = await _execute_command_calls(
        hass,
        [
            {
                "service": "climate.set_temperature",
                "target": {"entity_id": "climate.living_room"},
                "data": {"temperature": 999},
            }
        ],
    )
    assert executed == []
    assert len(failed) == 1
    assert failed[0]["error"]
    assert failed[0]["data"] == {"temperature": 999}
    assert "Failed" in error_suffix


def test_turn_off_with_stray_brightness_is_not_flipped_to_on() -> None:
    """light.turn_off carrying a residual brightness payload must NOT be
    rewritten to light.turn_on — that performs the opposite of the explicit
    'turn off' intent. The unsupported payload is rejected instead."""
    parsed = {
        "intent": "command",
        "response": "Turning off the kitchen light.",
        "calls": [
            {
                "service": "light.turn_off",
                "target": {"entity_id": "light.kitchen"},
                "data": {"brightness": 50},
            }
        ],
    }
    final = apply_command_policy(parsed, [_light_ent("light.kitchen", "on")])
    # Never flipped to turn_on.
    for call in final.get("calls", []):
        assert call.get("service") != "light.turn_on"
    assert final["intent"] != "command"


def test_repair_brightness_from_percent_sign_prose() -> None:
    """light.set_brightness with the level only in prose as '50%' (no data
    field) is repaired — the percent-sign form must be recognised, not just
    'percent' / 'to 50'."""
    parsed = {
        "intent": "command",
        "response": "Brightness 50%.",
        "calls": [
            {
                "service": "light.set_brightness",
                "target": {"entity_id": "light.kitchen"},
                "data": {},
            }
        ],
    }
    final = apply_command_policy(parsed, [_light_ent("light.kitchen")])
    assert final["intent"] == "command"
    assert final["calls"][0]["service"] == "light.turn_on"
    assert final["calls"][0]["data"]["brightness_pct"] == 50


def test_repair_temperature_ignores_schedule_time() -> None:
    """A schedule time in the prose must not be parsed as the setpoint.
    'Set the thermostat at 7 PM to 21 degrees' → 21, never 7."""
    parsed = {
        "intent": "command",
        "response": "Set the thermostat at 7 PM to 21 degrees.",
        "calls": [
            {
                "service": "climate.set_temperature",
                "target": {"entity_id": "climate.living_room"},
                "data": {},
            }
        ],
    }
    final = apply_command_policy(parsed, [_climate_ent("climate.living_room")])
    assert final["intent"] == "command"
    assert final["calls"][0]["data"]["temperature"] == 21


def test_repair_temperature_preserves_decimal() -> None:
    """A decimal setpoint in prose ('21.5 degrees') is parsed in full, not
    truncated to the trailing digits ('5')."""
    parsed = {
        "intent": "command",
        "response": "Setting the thermostat to 21.5 degrees.",
        "calls": [
            {
                "service": "climate.set_temperature",
                "target": {"entity_id": "climate.lr"},
                "data": {},
            }
        ],
    }
    final = apply_command_policy(parsed, [_climate_ent("climate.lr")])
    assert final["intent"] == "command"
    assert final["calls"][0]["data"]["temperature"] == 21.5


def test_repair_temperature_preserves_negative() -> None:
    """A negative setpoint ('-5 degrees') is parsed in full as -5, never
    truncated to '5' nor dropped — climate entities can support it."""
    parsed = {
        "intent": "command",
        "response": "Setting the thermostat to -5 degrees.",
        "calls": [
            {
                "service": "climate.set_temperature",
                "target": {"entity_id": "climate.lr"},
                "data": {},
            }
        ],
    }
    final = apply_command_policy(parsed, [_climate_ent("climate.lr")])
    assert final["intent"] == "command"
    assert final["calls"][0]["data"]["temperature"] == -5


def test_repair_temperature_preserves_zero() -> None:
    """An explicit '0 degrees' setpoint is preserved, not discarded by a
    lower-bound range check."""
    parsed = {
        "intent": "command",
        "response": "Setting the thermostat to 0 degrees.",
        "calls": [
            {
                "service": "climate.set_temperature",
                "target": {"entity_id": "climate.lr"},
                "data": {},
            }
        ],
    }
    final = apply_command_policy(parsed, [_climate_ent("climate.lr")])
    assert final["intent"] == "command"
    assert final["calls"][0]["data"]["temperature"] == 0


def test_repair_zero_brightness_becomes_turn_off() -> None:
    """light.set_brightness with an explicit 0 is honoured as light.turn_off,
    not floored to a 1% turn_on."""
    parsed = {
        "intent": "command",
        "response": "Setting kitchen light to 0%.",
        "calls": [
            {
                "service": "light.set_brightness",
                "target": {"entity_id": "light.kitchen"},
                "data": {"brightness_pct": 0},
            }
        ],
    }
    final = apply_command_policy(parsed, [_light_ent("light.kitchen", "on")])
    assert final["intent"] == "command"
    assert final["calls"][0]["service"] == "light.turn_off"
    assert "brightness_pct" not in final["calls"][0].get("data", {})


def test_executed_service_calls_from_log_preserves_data() -> None:
    """The failure-path synthesis keeps each action's data (brightness,
    temperature) instead of emitting an empty {}."""
    from custom_components.selora_ai import _executed_record_from_call
    from custom_components.selora_ai.llm_client.command_policy import (
        _executed_service_calls_from_log,
    )

    tool_log = [
        {
            "tool": "execute_command",
            "arguments": {
                "service": "light.turn_on",
                "entity_id": "light.kitchen",
                "data": {"brightness_pct": 60},
            },
            "result": {
                "executed": True,
                "service": "light.turn_on",
                "entity_ids": ["light.kitchen"],
            },
        }
    ]
    calls = _executed_service_calls_from_log(tool_log)
    assert calls[0]["data"] == {"brightness_pct": 60}
    record = _executed_record_from_call(calls[0])
    assert record["data"] == {"brightness_pct": 60}


def test_fallback_execution_records_redact_generic_targets(hass) -> None:
    """The post-tool failure path must redact generic-target entity_ids from
    its wire records, exactly like the normal success path. A 'set the
    thermostat to 21' command whose resolved climate entity is not named in
    the prompt must not leak the entity_id."""
    from custom_components.selora_ai import (
        _executed_record_from_call,
        _redact_executed_entity_ids_for_generic_references,
    )

    hass.states.async_set(
        "climate.living_room", "heat", {"friendly_name": "Living Room Thermostat"}
    )
    executed_calls = [
        {
            "service": "climate.set_temperature",
            "target": {"entity_id": ["climate.living_room"]},
            "data": {"temperature": 21},
        }
    ]
    records = [_executed_record_from_call(c) for c in executed_calls]
    _redact_executed_entity_ids_for_generic_references(hass, records, "set the thermostat to 21")
    # Entity_id redacted (friendly_name absent from prompt); data preserved.
    assert records[0]["entity_ids"] == []
    assert records[0]["data"] == {"temperature": 21}


def test_redaction_preserves_explicit_entity_id_target(hass) -> None:
    """An explicit entity_id named in the prompt ('turn on light.kitchen') is
    preserved in the execution record, not redacted as generic."""
    from custom_components.selora_ai import (
        _redact_executed_entity_ids_for_generic_references,
    )

    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen Light"})
    records = [
        {"domain": "light", "action": "turn_on", "entity_ids": ["light.kitchen"], "data": {}}
    ]
    _redact_executed_entity_ids_for_generic_references(hass, records, "turn on light.kitchen")
    assert records[0]["entity_ids"] == ["light.kitchen"]


def test_redaction_preserves_object_id_phrase_target(hass) -> None:
    """A reference by area/object name ('turn on the kitchen') is preserved
    even when the full friendly_name ('Kitchen Light') isn't in the prompt."""
    from custom_components.selora_ai import (
        _redact_executed_entity_ids_for_generic_references,
    )

    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen Light"})
    records = [
        {"domain": "light", "action": "turn_on", "entity_ids": ["light.kitchen"], "data": {}}
    ]
    _redact_executed_entity_ids_for_generic_references(hass, records, "turn on the kitchen")
    assert records[0]["entity_ids"] == ["light.kitchen"]
