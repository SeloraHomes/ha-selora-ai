"""Response parsing — JSON-mode, fenced-block streaming, and suggestions.

The architect endpoint accepts two response shapes:
1. JSON-mode (non-streaming): the whole reply is one JSON object.
2. Streaming: conversational text followed by ``` ``` fenced blocks
   (``automation``, ``scene``, ``command``, ``delayed_command``, ``cancel``,
   ``quick_actions``) that the renderer parses incrementally.

This module owns parsing for both shapes plus the suggestions array, and
applies scene/automation validation. Command-policy validation is layered
on top by the caller (see ``command_policy.apply_command_policy``).
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

import yaml

from ..automation_utils import assess_automation_risk, validate_automation_payload
from ..types import ArchitectResponse, EntitySnapshot
from .command_policy import (
    _call_signature,
    _executed_call_signatures,
    _executed_service_calls_from_log,
    _prose_is_trusted_after_tool,
    _response_names_unbacked_entity,
    apply_command_policy,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def parse_architect_response(text: str, hass: HomeAssistant) -> ArchitectResponse:
    """Parse the JSON response from the architect LLM.

    Normalises the result to always include 'intent' and 'response'.
    For 'automation' intent, generates automation_yaml server-side.

    Strips ``suppressed_duplicate_command`` — that's an internal trust
    flag set after duplicate-suppression validates a confirmed-safe
    path. If we let the model include it in its JSON, a prompt could
    induce ``{"intent":"command","calls":[...],
    "suppressed_duplicate_command":true}`` and bypass
    ``apply_command_policy`` entirely, executing arbitrary services.
    Internal callers re-add the flag after parsing when appropriate.
    """
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            return {"intent": "answer", "response": text}

        data: dict[str, Any] = json.loads(text[start : end + 1])
        data.pop("suppressed_duplicate_command", None)

        # Ensure intent is always present
        if "intent" not in data:
            # Legacy single-key response without intent — infer from content
            if "automation" in data:
                data["intent"] = "automation"
            elif "scene" in data:
                data["intent"] = "scene"
            elif "calls" in data:
                data["intent"] = "command"
            else:
                data["intent"] = "answer"

        if "scene" in data:
            from ..scene_utils import validate_scene_payload

            is_valid, reason, normalized = validate_scene_payload(data["scene"], hass)
            if not is_valid or normalized is None:
                _LOGGER.warning("Discarding invalid scene payload: %s", reason)
                data.pop("scene", None)
                data.pop("scene_yaml", None)
                data["validation_error"] = reason
                data["validation_target"] = "scene"
                data["response"] = (
                    "I couldn't create a valid scene from that request: "
                    f"{reason}. Please refine the request and try again."
                )
                if data.get("intent") == "scene":
                    data["intent"] = "answer"
            else:
                data["scene"] = normalized
                data["scene_yaml"] = yaml.dump(
                    normalized, default_flow_style=False, allow_unicode=True
                )

        if data.get("automation"):
            is_valid, reason, normalized = validate_automation_payload(data["automation"], hass)
            if not is_valid or normalized is None:
                _LOGGER.warning("Discarding invalid architect automation payload: %s", reason)
                data.pop("automation", None)
                data.pop("automation_yaml", None)
                data["validation_error"] = reason
                data["validation_target"] = "automation"
                data["response"] = (
                    "I couldn't create a valid automation from that request: "
                    f"{reason}. Please refine the request and try again."
                )
                if data.get("intent") == "automation":
                    data["intent"] = "answer"
            else:
                data["automation"] = normalized
                data["automation_yaml"] = yaml.dump(
                    normalized, default_flow_style=False, allow_unicode=True
                )
                data["risk_assessment"] = assess_automation_risk(normalized)

        return data

    except json.JSONDecodeError, KeyError, ValueError:
        _LOGGER.error("Failed to parse architect response: %s", text[:500])
        return {"intent": "answer", "response": text}


def parse_suggestions(text: str, provider_name: str) -> list[dict[str, Any]]:
    """Parse the LLM response into automation configs."""
    try:
        _LOGGER.debug("Raw %s response: %s", provider_name, text[:500])

        # Strip markdown fences if present
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        # Find JSON array in response
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            _LOGGER.warning("No JSON array found in %s response", provider_name)
            return []
        text = text[start : end + 1]

        suggestions = json.loads(text)
        if not isinstance(suggestions, list):
            return []

        valid = []
        for s in suggestions:
            if not isinstance(s, dict):
                continue
            has_name = "alias" in s or "description" in s
            has_behavior = any(k in s for k in ("actions", "action", "triggers", "trigger"))
            if has_name and has_behavior:
                valid.append(s)
        if len(valid) < len(suggestions):
            _LOGGER.debug(
                "Filtered %d/%d suggestions (missing required keys)",
                len(suggestions) - len(valid),
                len(suggestions),
            )
        return valid

    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        _LOGGER.warning("Failed to parse %s response: %s", provider_name, exc)
        return []


def parse_command_response_text(text: str) -> ArchitectResponse:
    """Parse LLM response text into service calls."""
    try:
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            return {"calls": [], "response": "Could not parse LLM response"}

        result = json.loads(text[start : end + 1])
        if not isinstance(result, dict):
            return {"calls": [], "response": "Invalid response format"}

        return {
            "calls": result.get("calls", []),
            "response": result.get("response", "Command processed"),
        }

    except (json.JSONDecodeError, KeyError) as exc:
        _LOGGER.warning("Failed to parse command response: %s", exc)
        return {"calls": [], "response": "Failed to parse LLM response"}


def parse_streamed_response(
    text: str,
    hass: HomeAssistant,
    entities: list[EntitySnapshot] | None = None,
    tool_log: list[dict[str, Any]] | None = None,
) -> ArchitectResponse:
    """Parse completed streamed text.

    Looks for a ```automation ... ``` fenced block.  Text before it is the
    conversational response; the block contents are parsed as automation JSON.
    Falls back to parse_architect_response for pure-JSON responses.

    When *entities* is provided, command-intent results are validated
    through ``apply_command_policy`` so that unsafe calls are blocked
    even on the streaming path.

    When *tool_log* is provided and includes an ``execute_command``
    invocation, command JSON that duplicates an already-executed tool
    call is suppressed to prevent double execution.
    """
    # Extract quick_actions block first — it's supplementary and can
    # appear alongside any other block type. Removing it now also lets
    # the duplicate-strip below see a terminal ```command``` block when
    # the model emits the order: prose → command → quick_actions.
    quick_actions: list[dict[str, Any]] | None = None
    qa_match = re.search(r"```quick_actions\s*\n?([\s\S]*?)```", text)
    if qa_match:
        try:
            parsed_qa = json.loads(qa_match.group(1).strip())
            if isinstance(parsed_qa, list) and parsed_qa:
                quick_actions = parsed_qa
        except json.JSONDecodeError, ValueError:
            _LOGGER.warning("Failed to parse quick_actions block")
        text = text[: qa_match.start()] + text[qa_match.end() :]

    def _attach_qa(r: ArchitectResponse) -> ArchitectResponse:
        if quick_actions:
            r["quick_actions"] = quick_actions
        return r

    # If execute_command ran during the tool loop, the user has already
    # seen that action take effect. A trailing ```command``` block that
    # echoes a *duplicate* (service, entity_id, data) signature would
    # re-execute, so we filter those individual calls out of the block.
    # Calls that don't match an executed signature stay (the model may
    # legitimately mix a tool-fired call with another immediate call in
    # one turn — and for ``toggle`` services in particular, re-running
    # would undo the user's request).
    # NEVER strip ```delayed_command``` — a scheduled future action is
    # by definition not a duplicate of an already-fired immediate one,
    # and dropping it would silently lose the user's request (e.g.
    # "turn the fan on now and off in 10 minutes").
    executed_sigs = _executed_call_signatures(tool_log) if tool_log else set()
    # When the entire command block is removed because every call inside
    # was a duplicate of an executed tool call, the model's prose
    # confirmation ("Turning off the kitchen light.") still ran via the
    # tool. We track this so the fallback path can mark the result as
    # suppressed and the policy preserves the prose instead of stomping
    # it with the "I didn't run any action" clarification.
    command_block_fully_stripped = False
    if executed_sigs:
        # Allow optional trailing whitespace, not just end-of-string,
        # because the quick_actions extraction above may leave behind
        # blank lines between the command block and the (now-removed)
        # quick_actions slot.
        cmd_match = re.search(r"```command\s*\n?([\s\S]*?)```\s*\Z", text.rstrip())
        if cmd_match:
            stripped_text = text.rstrip()
            # Prose before the block is what the policy's auto-repair
            # uses to infer the intended verb ("Opening the garage door"
            # → cover.open_cover). Pass it to _call_signature so a
            # malformed echo collapses onto the repaired signature.
            prose_before_block = stripped_text[: cmd_match.start()]
            try:
                block_data = json.loads(cmd_match.group(1).strip())
                block_calls = block_data.get("calls", [])
                if isinstance(block_calls, list) and block_calls:
                    surviving: list[dict[str, Any]] = []
                    for c in block_calls:
                        sig = (
                            _call_signature(c, prose_before_block) if isinstance(c, dict) else None
                        )
                        if sig is not None and sig in executed_sigs:
                            continue
                        surviving.append(c)
                    if len(surviving) < len(block_calls):
                        if surviving:
                            rewritten_data = dict(block_data, calls=surviving)
                            new_block = (
                                "```command\n"
                                + json.dumps(rewritten_data, ensure_ascii=False)
                                + "\n```"
                            )
                            text = stripped_text[: cmd_match.start()] + new_block
                        else:
                            text = stripped_text[: cmd_match.start()]
                            command_block_fully_stripped = True
            except json.JSONDecodeError, ValueError:
                # Malformed block — leave it; downstream parser logs the warning.
                pass

    # Check for immediate command fenced block — anchored to end.
    command_match = re.search(r"```command\s*\n?([\s\S]*?)```\s*$", text)
    if command_match:
        response_text = text[: command_match.start()].strip()
        json_text = command_match.group(1).strip()
        try:
            data = json.loads(json_text)
            result: ArchitectResponse = {
                "intent": "command",
                "response": response_text or "Done.",
                "calls": data.get("calls", []),
            }
            if entities is not None:
                result = apply_command_policy(result, entities)
            return _attach_qa(result)
        except json.JSONDecodeError, ValueError:
            _LOGGER.warning("Failed to parse command block: %s", json_text[:200])

    cancel_match = re.search(r"```cancel\s*\n?([\s\S]*?)```\s*$", text)
    if cancel_match:
        response_text = text[: cancel_match.start()].strip()
        try:
            data = json.loads(cancel_match.group(1).strip())
            return _attach_qa(
                {
                    "intent": "cancel",
                    "response": data.get("response", response_text or "Cancelled."),
                }
            )
        except json.JSONDecodeError, ValueError:
            # Malformed cancel block — fall through to avoid destructive action
            _LOGGER.warning("Failed to parse cancel block, ignoring")

    # Check for delayed_command fenced block — anchored to end so
    # informational examples don't trigger real scheduling.
    delay_match = re.search(r"```delayed_command\s*\n?([\s\S]*?)```\s*$", text)
    if delay_match:
        response_text = text[: delay_match.start()].strip()
        json_text = delay_match.group(1).strip()
        try:
            data = json.loads(json_text)
            result: ArchitectResponse = {
                "intent": "delayed_command",
                "response": response_text or "Scheduling that action.",
                "calls": data.get("calls", []),
            }
            if "delay_seconds" in data:
                result["delay_seconds"] = data["delay_seconds"]
            if "scheduled_time" in data:
                result["scheduled_time"] = data["scheduled_time"]
            if entities is not None:
                result = apply_command_policy(result, entities)
            return _attach_qa(result)
        except json.JSONDecodeError, ValueError:
            _LOGGER.warning("Failed to parse delayed_command block: %s", json_text[:200])

    # Check for scene fenced block — must be the terminal block in the
    # response (anchored to end) so informational examples don't trigger
    # real scene creation.
    scene_match = re.search(r"```scene\s*\n?([\s\S]*?)```\s*$", text)
    if scene_match:
        from ..scene_utils import validate_scene_payload

        response_text = text[: scene_match.start()].strip()
        json_text = scene_match.group(1).strip()
        try:
            scene_data = json.loads(json_text)
            is_valid, reason, normalized = validate_scene_payload(scene_data, hass)
            if not is_valid or normalized is None:
                return _attach_qa(
                    {
                        "intent": "answer",
                        "response": response_text or "I couldn't create a valid scene",
                        "validation_error": reason,
                        "validation_target": "scene",
                    }
                )
            scene_result: dict[str, Any] = {
                "intent": "scene",
                "response": response_text or "Scene created.",
                "scene": normalized,
                "scene_yaml": yaml.dump(normalized, default_flow_style=False, allow_unicode=True),
            }
            # Preserve refine_scene_id so the streaming handler can
            # update the existing scene instead of creating a new one.
            if scene_data.get("refine_scene_id"):
                scene_result["refine_scene_id"] = scene_data["refine_scene_id"]
            return _attach_qa(scene_result)
        except json.JSONDecodeError, ValueError:
            _LOGGER.warning("Failed to parse scene block: %s", json_text[:200])

    match = re.search(r"```automation\s*\n?([\s\S]*?)```", text)
    if match:
        response_text = text[: match.start()].strip()
        json_text = match.group(1).strip()
        try:
            automation = json.loads(json_text)
            is_valid, reason, normalized = validate_automation_payload(automation, hass)
            if not is_valid or normalized is None:
                _LOGGER.warning("Discarding invalid streamed automation payload: %s", reason)
                return _attach_qa(
                    {
                        "intent": "answer",
                        "response": (
                            response_text
                            or "I couldn't create a valid automation from that request"
                        )
                        + f": {reason}. Please refine the request and try again.",
                        "validation_error": reason,
                        "validation_target": "automation",
                    }
                )
            automation_yaml = yaml.dump(normalized, default_flow_style=False, allow_unicode=True)
            return _attach_qa(
                {
                    "intent": "automation",
                    "response": response_text or "Here's the automation I've created.",
                    "automation": normalized,
                    "automation_yaml": automation_yaml,
                    "description": normalized.get("description", ""),
                    "risk_assessment": assess_automation_risk(normalized),
                }
            )
        except json.JSONDecodeError, ValueError:
            _LOGGER.warning("Failed to parse automation block: %s", json_text[:200])

    # No fenced block — try the old JSON-only parser
    result = parse_architect_response(text, hass)

    # The prose we're returning is trustworthy and should bypass
    # the policy's unbacked-action guard in three cases:
    # (a) The model wrote the prose alongside a ```command``` block
    #     that fully matched an executed tool signature (the
    #     command_block_fully_stripped branch). The prose is
    #     anchored to actions that really ran AND it doesn't claim
    #     a different known entity. The latter check is the safety
    #     correction — previously this branch trusted any prose,
    #     letting a mismatched "Turning off the bedroom" claim
    #     through after stripping a kitchen-targeted block.
    # (b)/(c) A tool call already fired AND the prose matches one
    #     of the trusted shapes from _prose_is_trusted_after_tool
    #     (synthesized prefix, generic ack, or executed-entity
    #     describe).
    if result.get("intent") == "answer" and tool_log:
        executed_calls = _executed_service_calls_from_log(tool_log)
        response_text = result.get("response", "")
        if _prose_is_trusted_after_tool(response_text, executed_calls, entities) or (
            command_block_fully_stripped
            and executed_calls
            and not _response_names_unbacked_entity(response_text, executed_calls, entities)
        ):
            result["suppressed_duplicate_command"] = True

    # Apply command safety policy if entities are available.
    # Always run the policy — even when calls is empty — so that
    # command intents with no calls get downgraded to "answer".
    if entities is not None:
        result = apply_command_policy(result, entities)

    return _attach_qa(result)
