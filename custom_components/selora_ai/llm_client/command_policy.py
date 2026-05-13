"""Command safety policy: validates and repairs LLM-proposed service calls.

Owns the allowlist of safe domains/services/parameters and the heuristics
that recover from common LLM mistakes (bogus service name, fake confirmation
without backing calls). Public entry point is ``apply_command_policy``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..types import ArchitectResponse, EntitySnapshot

_LOGGER = logging.getLogger(__name__)

_MAX_COMMAND_CALLS = 5
_MAX_TARGET_ENTITIES = 3

_COMMAND_SERVICE_POLICIES: dict[str, dict[str, set[str]]] = {
    "light": {
        # Both `brightness` (0-255, HA's storage form used by scenes) and
        # `brightness_pct` (0-100, friendlier for prompts) are accepted
        # so scene activations and direct user requests both work.
        "turn_on": {
            "brightness",
            "brightness_pct",
            "color_temp",
            "kelvin",
            "rgb_color",
            "rgbw_color",
            "rgbww_color",
            "hs_color",
            "xy_color",
            "color_name",
            "effect",
            "transition",
        },
        "turn_off": {"transition"},
        "toggle": {"transition"},
    },
    "switch": {
        "turn_on": set(),
        "turn_off": set(),
        "toggle": set(),
    },
    "fan": {
        "turn_on": {"percentage", "preset_mode"},
        "turn_off": set(),
        "toggle": set(),
        "set_percentage": {"percentage"},
        "oscillate": {"oscillating"},
    },
    "media_player": {
        "turn_on": set(),
        "turn_off": set(),
        "media_play": set(),
        "media_pause": set(),
        "media_stop": set(),
        "volume_set": {"volume_level"},
        "volume_mute": {"is_volume_muted"},
    },
    "climate": {
        "turn_on": set(),
        "turn_off": set(),
        "set_temperature": {"temperature", "hvac_mode"},
        "set_hvac_mode": {"hvac_mode"},
    },
    "input_boolean": {
        "turn_on": set(),
        "turn_off": set(),
        "toggle": set(),
    },
    # Scenes are atomic snapshots — `scene.turn_on` takes the scene as the
    # target and applies all of its stored device states server-side.
    # Listing it here means the LLM activates a named scene with one call
    # instead of expanding it into individual light/media_player calls
    # (which then trip the per-domain parameter whitelist).
    "scene": {
        "turn_on": {"transition"},
    },
    # Covers (garage doors, blinds, awnings, gates). Open/close/stop are
    # the standard verbs the LLM needs; `set_cover_position` lets the
    # user say "open the blinds to 50%". Lock and alarm domains are NOT
    # included here — those are higher-risk and gated behind a separate
    # config option (TODO when added).
    "cover": {
        "open_cover": set(),
        "close_cover": set(),
        "stop_cover": set(),
        "toggle": set(),
        "set_cover_position": {"position"},
    },
}
_ALLOWED_COMMAND_SERVICES: dict[str, set[str]] = {
    domain: set(services.keys()) for domain, services in _COMMAND_SERVICE_POLICIES.items()
}
_SAFE_COMMAND_DOMAINS = ", ".join(sorted(_ALLOWED_COMMAND_SERVICES))


# Action-verb → canonical service map per domain. Used to auto-repair
# the LLM's most common command-shape mistake: emitting a bogus
# service field like `cover.cover` or `cover.garage_door` (the domain
# repeated, or the entity_id stuffed in) while writing a coherent
# confirmation sentence ("Opening the garage door"). Patterns are
# scanned in order and the first match wins. Only fires when the
# domain itself is allowed and the parsed service name is NOT already
# a valid service — so a correctly-formed call passes through
# unchanged.
_SERVICE_REPAIR_HINTS: dict[str, list[tuple[re.Pattern[str], str]]] = {
    "cover": [
        (re.compile(r"\b(?:re-?)?open(?:ing|ed)?\b", re.I), "open_cover"),
        (re.compile(r"\bclos(?:e|ing|ed)\b", re.I), "close_cover"),
        (re.compile(r"\bshut(?:ting)?\b", re.I), "close_cover"),
        (re.compile(r"\bstop(?:ping|ped)?\b", re.I), "stop_cover"),
        (re.compile(r"\btoggl", re.I), "toggle"),
    ],
    "light": [
        (re.compile(r"\bturn(?:ing|ed)?\s+on\b", re.I), "turn_on"),
        (re.compile(r"\bturn(?:ing|ed)?\s+off\b", re.I), "turn_off"),
        (re.compile(r"\btoggl", re.I), "toggle"),
    ],
    "switch": [
        (re.compile(r"\bturn(?:ing|ed)?\s+on\b", re.I), "turn_on"),
        (re.compile(r"\bturn(?:ing|ed)?\s+off\b", re.I), "turn_off"),
        (re.compile(r"\btoggl", re.I), "toggle"),
    ],
    "input_boolean": [
        (re.compile(r"\bturn(?:ing|ed)?\s+on\b", re.I), "turn_on"),
        (re.compile(r"\bturn(?:ing|ed)?\s+off\b", re.I), "turn_off"),
        (re.compile(r"\btoggl", re.I), "toggle"),
    ],
    "fan": [
        (re.compile(r"\bturn(?:ing|ed)?\s+on\b", re.I), "turn_on"),
        (re.compile(r"\bturn(?:ing|ed)?\s+off\b", re.I), "turn_off"),
        (re.compile(r"\btoggl", re.I), "toggle"),
    ],
    "media_player": [
        (re.compile(r"\bturn(?:ing|ed)?\s+on\b", re.I), "turn_on"),
        (re.compile(r"\bturn(?:ing|ed)?\s+off\b", re.I), "turn_off"),
        (re.compile(r"\bplay(?:ing|ed)?\b", re.I), "media_play"),
        (re.compile(r"\bpaus(?:e|ing|ed)\b", re.I), "media_pause"),
        (re.compile(r"\bstop(?:ping|ped)?\b", re.I), "media_stop"),
    ],
}


def _repair_service_name(
    service: str,
    response_text: str,
) -> str | None:
    """Infer a canonical `<domain>.<verb>` from the confirmation prose
    when the LLM emitted a bogus service for an allowed domain.

    Returns the repaired service string if a verb can be inferred,
    otherwise None. Callers must verify the domain itself is in
    `_ALLOWED_COMMAND_SERVICES` first — this helper trusts that
    precondition and only handles the verb fix-up.
    """
    if "." not in service:
        return None
    domain = service.split(".", 1)[0]
    hints = _SERVICE_REPAIR_HINTS.get(domain)
    if not hints or not response_text:
        return None
    for pattern, verb in hints:
        if pattern.search(response_text):
            return f"{domain}.{verb}"
    return None


# Matches prose the LLM uses when narrating a device action. Detects the
# failure mode where the model writes a confirmation like "Turning off the
# ceiling lights" but never emits the corresponding command block / calls
# array — the user sees a fake success while nothing actually executes.
_ACTION_CONFIRMATION_RE = re.compile(
    r"^\s*(?:"
    r"turning\s+(?:on|off|up|down)|"
    r"setting\s+|dimming\s+|brightening\s+|"
    r"starting\s+|stopping\s+|pausing\s+|resuming\s+|"
    r"playing\s+|muting\s+|unmuting\s+|"
    r"locking\s+|unlocking\s+|opening\s+|closing\s+|"
    r"i['’]?ve\s+(?:turned|set|dimmed|started|stopped|paused|locked|unlocked|opened|closed)|"
    r"i\s+(?:turned|set|dimmed|started|stopped|paused|locked|unlocked|opened|closed)|"
    r"done\b|all set\b|ok[,.\s]+done"
    r")",
    re.IGNORECASE,
)

# Phrases that strongly indicate explanatory prose rather than a confirmation.
# Used to avoid replacing a short help answer like "Opening a garage door in
# Home Assistant requires a cover entity…" that legitimately starts with an
# action verb but is informational, not a fake confirmation.
_EXPLANATORY_MARKERS_RE = re.compile(
    r"\b(?:requires|because|when\s+you|happens\s+when|works\s+by|"
    r"means\s+that|in\s+home\s+assistant|involves|depends\s+on|"
    r"you\s+(?:need|can|must|should|have\s+to)|"
    r"to\s+(?:do|achieve|enable|set\s+up))\b",
    re.IGNORECASE,
)


def _looks_like_unbacked_action(response: str, *, strict: bool = False) -> bool:
    """Return True when *response* reads as a short device-action confirmation.

    Used to catch the failure mode where the LLM narrates an action
    ("Turning off ceiling lights") without producing service calls — e.g.
    when a typo prevents entity matching. The caller is responsible for
    confirming there are no calls before treating this as a fake confirmation.

    When ``strict`` is True (used when we have NO upstream signal that the
    LLM intended a command), also require the response to be free of
    explanatory markers so a short help answer is not misclassified.
    """
    if not isinstance(response, str):
        return False
    stripped = response.strip()
    if len(stripped) > 240:
        return False
    if not _ACTION_CONFIRMATION_RE.match(stripped):
        return False
    return not (strict and _EXPLANATORY_MARKERS_RE.search(stripped))


_UNBACKED_ACTION_RESPONSE = (
    "I'm not sure which device you meant — could you rephrase? "
    "I didn't run any action because no entity clearly matched."
)


def _build_command_confirmation(calls: list[dict[str, Any]]) -> str:
    """Build a human-readable confirmation from a list of validated service calls.

    Only called after ``apply_command_policy`` has validated the calls,
    so types are guaranteed.  Used as fallback when the LLM returns a
    command intent without a ``response`` field (#94).
    """
    if not isinstance(calls, list) or not calls:
        return "Done."
    parts: list[str] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        service = str(call.get("service", ""))
        target = call.get("target")
        if not isinstance(target, dict):
            target = {}
        entity_ids = target.get("entity_id", [])
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        elif not isinstance(entity_ids, list):
            entity_ids = []
        # Pretty-print entity IDs: "light.kitchen" → "kitchen"
        names = [str(eid).split(".", 1)[-1].replace("_", " ") for eid in entity_ids]
        action = service.replace(".", " ").replace("_", " ")
        if names:
            parts.append(f"{action} ({', '.join(names)})")
        elif action:
            parts.append(action)
    if not parts:
        return "Done."
    return "Done — " + "; ".join(parts) + "."


def _blocked_command_result(
    reason: str,
    result: ArchitectResponse | None = None,
) -> ArchitectResponse:
    """Return a safe response when a command proposal is rejected."""
    _LOGGER.warning("Blocked unsafe LLM command proposal: %s", reason)
    response = (
        "I couldn't safely execute that request because "
        f"{reason}. Immediate commands are currently limited to "
        f"{_SAFE_COMMAND_DOMAINS} devices with explicit entity targets."
    )
    blocked_result = dict(result or {})
    blocked_result["intent"] = "answer"
    blocked_result["calls"] = []
    blocked_result["response"] = response
    blocked_result["validation_error"] = reason
    return blocked_result


def apply_command_policy(
    result: ArchitectResponse,
    entities: list[EntitySnapshot],
) -> ArchitectResponse:
    """Reject unsafe immediate commands before any caller can execute them."""
    if not isinstance(result, dict):
        return {"intent": "answer", "response": "Invalid command response"}

    calls = result.get("calls")
    if not calls:
        # If the LLM classified as "command" or "delayed_command" but
        # provided no calls, downgrade to "answer". When the response
        # text reads as a confirmation ("Turning off …"), replace it
        # so the user isn't told an action ran when none did.
        if result.get("intent") in ("command", "delayed_command"):
            result = dict(result, intent="answer")
            if _looks_like_unbacked_action(result.get("response", "")):
                result["response"] = _UNBACKED_ACTION_RESPONSE
                result["validation_error"] = "no_matching_entity_for_command"
        elif result.get("intent") == "answer" and _looks_like_unbacked_action(
            result.get("response", ""), strict=True
        ):
            # No upstream signal that the LLM intended a command, so use
            # the strict matcher to avoid replacing a short help answer
            # that happens to start with an action verb.
            result = dict(result, response=_UNBACKED_ACTION_RESPONSE)
            result["validation_error"] = "no_matching_entity_for_command"
        return result

    allowed_entities = {e.get("entity_id", "") for e in entities if e.get("entity_id")}
    if not isinstance(calls, list):
        return _blocked_command_result(
            "the model returned an invalid command format",
            result,
        )
    if len(calls) > _MAX_COMMAND_CALLS:
        return _blocked_command_result(
            f"the request tried to perform too many actions at once (max {_MAX_COMMAND_CALLS})",
            result,
        )

    validated_calls: list[dict[str, Any]] = []
    for call in calls:
        if not isinstance(call, dict):
            return _blocked_command_result(
                "one of the proposed commands was not a valid object",
                result,
            )

        service = str(call.get("service", "")).strip()
        if "." not in service:
            return _blocked_command_result(
                "one of the proposed commands was missing a valid service name",
                result,
            )

        domain, service_name = service.split(".", 1)
        if domain not in _ALLOWED_COMMAND_SERVICES:
            return _blocked_command_result(
                f"the {domain} domain is outside the current safe command allowlist",
                result,
            )
        if service_name not in _ALLOWED_COMMAND_SERVICES[domain]:
            # Try to repair common LLM mistakes — `cover.cover`,
            # `cover.garage_door`, etc. — by reading the verb out of
            # the confirmation prose ("Opening the garage door" →
            # `cover.open_cover`). Only kicks in for domains we
            # have a verb-hint table for and only when the
            # response text gives us an unambiguous match.
            repaired = _repair_service_name(service, str(result.get("response", "")))
            if repaired and repaired.split(".", 1)[1] in _ALLOWED_COMMAND_SERVICES[domain]:
                _LOGGER.info(
                    "Auto-repaired malformed service %s -> %s (verb inferred from response prose)",
                    service,
                    repaired,
                )
                service = repaired
                domain, service_name = service.split(".", 1)
                call["service"] = service
            else:
                allowed = sorted(_ALLOWED_COMMAND_SERVICES[domain])
                return _blocked_command_result(
                    f"`{service}` is not a valid {domain} service; expected one of "
                    f"{', '.join(allowed)}",
                    result,
                )

        target = call.get("target", {})
        if not isinstance(target, dict):
            return _blocked_command_result(
                f"{service} had an invalid target payload",
                result,
            )

        entity_ids = target.get("entity_id")
        if isinstance(entity_ids, str):
            target_ids = [entity_ids]
        elif isinstance(entity_ids, list) and all(isinstance(eid, str) for eid in entity_ids):
            target_ids = entity_ids
        else:
            return _blocked_command_result(
                f"{service} did not target explicit entity_ids",
                result,
            )

        if not target_ids:
            return _blocked_command_result(
                f"{service} did not include any target entities",
                result,
            )
        if len(target_ids) > _MAX_TARGET_ENTITIES:
            return _blocked_command_result(
                f"{service} targeted too many entities at once (max {_MAX_TARGET_ENTITIES})",
                result,
            )

        for entity_id in target_ids:
            if entity_id not in allowed_entities:
                return _blocked_command_result(
                    f"{service} referenced an unknown entity_id ({entity_id})",
                    result,
                )
            entity_domain = entity_id.split(".", 1)[0]
            if entity_domain != domain:
                return _blocked_command_result(
                    f"{service} targeted {entity_id}, which is outside the {domain} domain",
                    result,
                )

        data = call.get("data", {})
        if data is not None and not isinstance(data, dict):
            return _blocked_command_result(
                f"{service} included an invalid data payload",
                result,
            )
        data = data or {}

        allowed_data_keys = _COMMAND_SERVICE_POLICIES[domain][service_name]
        extra_keys = sorted(set(data) - allowed_data_keys)
        if extra_keys:
            return _blocked_command_result(
                f"{service} included unsupported parameters: {', '.join(extra_keys)}",
                result,
            )

        validated_calls.append(
            {
                "service": service,
                "target": target,
                "data": data,
            }
        )

    result["calls"] = validated_calls
    # Generate a human-readable fallback so callers never show raw JSON (#94).
    # Only set after policy validation confirms the calls are safe.
    if "response" not in result:
        result["response"] = _build_command_confirmation(validated_calls)
    return result
