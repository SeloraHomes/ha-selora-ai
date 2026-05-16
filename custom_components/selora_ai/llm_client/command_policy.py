"""Command safety policy: validates and repairs LLM-proposed service calls.

Owns the allowlist of safe domains/services/parameters and the heuristics
that recover from common LLM mistakes (bogus service name, fake confirmation
without backing calls). Public entry point is ``apply_command_policy``.
"""

from __future__ import annotations

import json
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


def validate_command_action(
    service: str,
    entity_id: str | list[str],
    data: dict[str, Any] | None = None,
    *,
    known_entity_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Validate a single service call against the safe-command policy.

    Pure function exposed for the ``validate_action`` MCP/chat tool so callers
    (notably small LLMs) can self-check before emitting a command. Mirrors the
    per-call checks in ``apply_command_policy`` but does not mutate state and
    does not perform verb auto-repair — the goal is to surface explicit errors
    the model can correct, not silently rewrite the call.

    ``known_entity_ids`` is optional; when provided, each target entity_id is
    checked for membership (so the tool can flag typos). When omitted, only
    shape and domain rules are enforced.
    """
    errors: list[str] = []
    service = (service or "").strip()
    if "." not in service:
        return {
            "valid": False,
            "errors": ["service must be in '<domain>.<verb>' form"],
            "service": service,
            "domain": None,
            "allowed_data_keys": [],
        }

    domain, service_name = service.split(".", 1)
    if domain not in _ALLOWED_COMMAND_SERVICES:
        return {
            "valid": False,
            "errors": [
                f"domain '{domain}' is outside the safe command allowlist ({_SAFE_COMMAND_DOMAINS})"
            ],
            "service": service,
            "domain": domain,
            "allowed_data_keys": [],
            "allowed_services": sorted(_ALLOWED_COMMAND_SERVICES),
        }

    allowed_services = sorted(_ALLOWED_COMMAND_SERVICES[domain])
    if service_name not in _ALLOWED_COMMAND_SERVICES[domain]:
        errors.append(
            f"'{service}' is not a valid {domain} service; expected one of "
            f"{', '.join(allowed_services)}"
        )

    if isinstance(entity_id, str):
        target_ids = [entity_id] if entity_id else []
    elif isinstance(entity_id, list) and all(isinstance(eid, str) for eid in entity_id):
        target_ids = entity_id
    else:
        errors.append("entity_id must be a string or list of strings")
        target_ids = []

    if not target_ids:
        errors.append("at least one entity_id is required")
    elif len(target_ids) > _MAX_TARGET_ENTITIES:
        errors.append(f"too many entity_ids targeted at once (max {_MAX_TARGET_ENTITIES})")

    for eid in target_ids:
        if "." not in eid:
            errors.append(f"entity_id '{eid}' is not in '<domain>.<object_id>' form")
            continue
        ent_domain = eid.split(".", 1)[0]
        if ent_domain != domain:
            errors.append(f"entity_id '{eid}' is in the {ent_domain} domain, not {domain}")
        if known_entity_ids is not None and eid not in known_entity_ids:
            errors.append(f"entity_id '{eid}' is not known to Home Assistant")

    allowed_data_keys = sorted(_COMMAND_SERVICE_POLICIES.get(domain, {}).get(service_name, set()))
    if data is not None and not isinstance(data, dict):
        errors.append("data must be an object")
    elif isinstance(data, dict) and data:
        extra = sorted(set(data) - set(allowed_data_keys))
        if extra:
            errors.append(
                f"unsupported parameters for {service}: {', '.join(extra)} "
                f"(allowed: {', '.join(allowed_data_keys) or 'none'})"
            )

    return {
        "valid": not errors,
        "errors": errors,
        "service": service,
        "domain": domain,
        "allowed_services": allowed_services,
        "allowed_data_keys": allowed_data_keys,
    }


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


_CallSignature = tuple[str, frozenset[str], str]


def _data_signature(data: Any) -> str:
    """Canonical JSON for the service-data payload, excluding entity_id.

    Two calls are only considered duplicates when their data payloads
    match — otherwise ``light.turn_on(light.kitchen, brightness_pct=60)``
    looks identical to ``light.turn_on(light.kitchen)`` and the requested
    brightness would be silently dropped. ``entity_id`` is excluded here
    because it's already part of the signature's entity-id frozenset.
    """
    if not isinstance(data, dict) or not data:
        return ""
    cleaned = {k: v for k, v in data.items() if k != "entity_id"}
    if not cleaned:
        return ""
    try:
        return json.dumps(cleaned, sort_keys=True, default=str)
    except TypeError, ValueError:
        return str(sorted(cleaned.items()))


def _iter_executed_write_actions(
    tool_log: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Yield ``{service, entity_ids, data}`` records for every successful
    side-effecting tool call in *tool_log*.

    Recognises:

    * ``execute_command`` — counts when ``result.executed is True``. Uses the
      executor-returned ``service``/``entity_ids`` when present (they
      reflect any handler-side normalisation) and falls back to the raw
      arguments otherwise. ``data`` is preserved so parameterized
      variants (e.g. different brightness) stay distinct downstream.
    * ``activate_scene`` — counts when ``result.status == "activated"``.
      The executor's result carries the resolved ``entity_id``; we
      represent this as a synthetic ``scene.turn_on`` call so duplicate
      suppression and failure-path synthesis treat it uniformly with
      other write tools.

    Tools that didn't actually fire (validation failures, runtime errors,
    read-only handlers) are skipped — that's what lets the duplicate
    guard distinguish a legitimate fallback ```command``` block from a
    re-echo of an already-executed action.
    """
    actions: list[dict[str, Any]] = []
    for entry in tool_log or []:
        tool_name = entry.get("tool")
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        args = entry.get("arguments") or {}

        if tool_name == "execute_command":
            if result.get("executed") is not True:
                continue
            service = str(result.get("service") or "").strip()
            target_ids = result.get("entity_ids")
            if not service or not isinstance(target_ids, list):
                service = service or str(args.get("service", "")).strip()
                raw = args.get("entity_id")
                if isinstance(raw, str):
                    target_ids = [raw.strip()] if raw.strip() else []
                elif isinstance(raw, list):
                    target_ids = [str(e).strip() for e in raw if str(e).strip()]
                else:
                    target_ids = []
            if not service:
                continue
            ids = [str(e).strip() for e in target_ids if str(e).strip()]
            if not ids:
                continue
            data = args.get("data") if isinstance(args.get("data"), dict) else {}
            actions.append({"service": service, "entity_ids": ids, "data": data})
        elif tool_name == "activate_scene":
            if result.get("status") != "activated":
                continue
            # The handler resolves scene_id → entity_id and returns the
            # entity_id it actually called scene.turn_on on. That's the
            # signature surface a duplicate command block would echo.
            entity_id = result.get("entity_id")
            if not isinstance(entity_id, str) or not entity_id:
                continue
            actions.append(
                {
                    "service": "scene.turn_on",
                    "entity_ids": [entity_id],
                    "data": {},
                }
            )
    return actions


def _executed_call_signatures(
    tool_log: list[dict[str, Any]],
) -> set[_CallSignature]:
    """Return (service, frozenset(entity_ids), data_sig) signatures for
    every successful side-effecting tool call (execute_command +
    activate_scene). See ``_iter_executed_write_actions`` for the
    success criteria.

    ``data_sig`` is the canonical JSON of the service-data payload (minus
    ``entity_id``) so parameterized variants with different
    ``brightness_pct``, ``temperature``, etc. do not collapse into the
    same signature.
    """
    sigs: set[_CallSignature] = set()
    for action in _iter_executed_write_actions(tool_log):
        ids = frozenset(action["entity_ids"])
        if not ids:
            continue
        sigs.add((action["service"], ids, _data_signature(action["data"])))
    return sigs


def _call_signature(
    call: dict[str, Any],
    response_text: str = "",
) -> _CallSignature | None:
    """Return (service, frozenset(entity_ids), data_sig) for a parsed ServiceCallDict.

    Mirrors the service-name auto-repair performed by ``apply_command_policy``:
    when the raw service is malformed for its allowed domain (e.g. the model
    stuffed the entity_id into the service field — ``cover.garage_door`` instead
    of ``cover.open_cover``), and ``response_text`` makes the intended verb
    unambiguous ("Opening the garage door"), the signature is computed against
    the repaired service. Without this, a malformed echo would slip past the
    duplicate guard and then be repaired+executed a second time by the policy.
    """
    service = str(call.get("service", "")).strip()
    if not service:
        return None
    if "." in service:
        domain = service.split(".", 1)[0]
        if domain in _ALLOWED_COMMAND_SERVICES:
            verb = service.split(".", 1)[1]
            if verb not in _ALLOWED_COMMAND_SERVICES[domain] and response_text:
                repaired = _repair_service_name(service, response_text)
                if repaired and repaired.split(".", 1)[1] in _ALLOWED_COMMAND_SERVICES[domain]:
                    service = repaired
    target = call.get("target") or {}
    raw = target.get("entity_id") if isinstance(target, dict) else None
    data = call.get("data") if isinstance(call.get("data"), dict) else None
    if raw is None and data is not None:
        raw = data.get("entity_id")
    if isinstance(raw, str):
        ids = frozenset([raw.strip()] if raw.strip() else [])
    elif isinstance(raw, list):
        ids = frozenset(str(e).strip() for e in raw if str(e).strip())
    else:
        ids = frozenset()
    if not ids:
        return None
    return service, ids, _data_signature(data)


def _suppress_duplicate_command_after_tool(
    parsed: dict[str, Any],
    tool_log: list[dict[str, Any]],
    entities: list[EntitySnapshot] | None = None,
) -> dict[str, Any]:
    """Drop ``command`` calls that duplicate already-executed ``execute_command``.

    The downstream command dispatcher runs every entry in ``parsed["calls"]``.
    If the model both called the ``execute_command`` tool AND echoed the same
    call in its final JSON, the service would fire twice.

    Only ``intent == "command"`` is affected. ``delayed_command`` is *never*
    suppressed: a scheduled future action is by definition NOT a duplicate of
    an immediate action that already fired.

    Calls that target entities/services NOT executed via the tool are left
    intact — the model may legitimately mix a tool-fired call with another
    immediate call in the same turn.

    When *every* call is a duplicate, the result is downgraded to an
    ``answer``. The ``suppressed_duplicate_command`` flag is only set when
    the accompanying prose actually describes one of the executed actions
    (per ``_prose_is_trusted_after_tool``) — otherwise the policy's
    unbacked-action guard must still run, since the model may have written
    about a different device alongside its duplicate echo.
    """
    if not tool_log:
        return parsed
    if parsed.get("intent") != "command":
        return parsed
    calls = parsed.get("calls")
    if not isinstance(calls, list) or not calls:
        return parsed

    executed = _executed_call_signatures(tool_log)
    if not executed:
        return parsed

    response_text = str(parsed.get("response", ""))
    surviving: list[dict[str, Any]] = []
    dropped = 0
    for call in calls:
        if not isinstance(call, dict):
            surviving.append(call)
            continue
        sig = _call_signature(call, response_text)
        if sig is not None and sig in executed:
            dropped += 1
            continue
        surviving.append(call)

    if dropped == 0:
        return parsed

    _LOGGER.info("Suppressed %d duplicate command call(s) already executed via tool", dropped)

    if not surviving:
        # Every call in the final JSON was a duplicate — downgrade to
        # answer so the dispatcher has nothing to run.
        # The suppressed_duplicate_command flag only bypasses the
        # unbacked-action policy guard when the prose itself describes
        # the executed action. If the model wrote about a different
        # device alongside its duplicate block (e.g. tool ran
        # light.kitchen but response says "Turning off the bedroom"),
        # we must let the policy run so the false claim is corrected.
        downgraded: dict[str, Any] = {
            "intent": "answer",
            "response": parsed.get("response", "Done."),
        }
        executed_calls = _executed_service_calls_from_log(tool_log)
        if executed_calls and _prose_is_trusted_after_tool(
            downgraded["response"], executed_calls, entities
        ):
            downgraded["suppressed_duplicate_command"] = True
        return downgraded

    # Partial strip: surviving calls stay as ``intent: "command"`` and
    # MUST run through apply_command_policy so service allowlist, entity
    # registry, and data-key validation still apply. The flag is NOT set
    # here — its sole role is to skip the unbacked-action stomp when no
    # calls remain to validate. Setting it on a command intent with calls
    # would smuggle the surviving call past the safety policy.
    new = dict(parsed)
    new["calls"] = surviving
    return new


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


def _executed_service_calls_from_log(
    tool_log: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Return ServiceCallDict-shaped entries for every successful
    side-effecting tool call (execute_command + activate_scene).

    Used to synthesize confirmations when the tool loop fails *after*
    one or more services already fired — the user must not be told
    nothing happened. Mirrors the set of tools tracked by the duplicate
    guard so both paths agree on what counts as executed.
    """
    return [
        {"service": a["service"], "target": {"entity_id": list(a["entity_ids"])}}
        for a in _iter_executed_write_actions(tool_log)
    ]


def _response_is_synthesized_confirmation(
    response: str,
    executed_calls: list[dict[str, Any]],
) -> bool:
    """True iff *response* starts with the exact confirmation prefix that
    ``_build_command_confirmation(executed_calls)`` would produce.

    Used to decide whether to skip the policy's unbacked-action stomp.
    Matching the literal synthesizer output prevents a broader-flag bug
    where any free-form action prose written after a successful tool
    call would be trusted — e.g. the model uses execute_command for
    light.kitchen and then prose-confirms light.bedroom (which never
    ran). Such mismatched prose fails this check and remains subject
    to the policy's safety correction.
    """
    if not executed_calls or not response:
        return False
    expected_prefix = _build_command_confirmation(executed_calls)
    return response.strip().startswith(expected_prefix)


# Pure-acknowledgement phrases the LLM commonly produces after a successful
# tool call. They don't name a specific action or device, so they can't
# falsely claim something that didn't happen — they're safe to trust as
# "the tool ran, and the model is just nodding." Anything more specific
# (e.g. "Turning off the bedroom light") must go through the synthesized-
# prefix check so a hallucinated entity is still caught by the policy.
_GENERIC_ACK_RE = re.compile(
    r"^\s*(?:"
    r"(?:ok[,.\s]+)?done"
    r"|all\s+set"
    r"|got\s+it"
    r"|sure"
    r")\s*[.!?]*\s*$",
    re.IGNORECASE,
)


def _is_generic_acknowledgement(response: str) -> bool:
    """True for short, non-specific confirmations like 'Done.' or 'All set.'.

    Used alongside :func:`_response_is_synthesized_confirmation` to decide
    whether prose following a successful tool execution is trustworthy.
    Generic acks make no claim about a specific entity so they're safe
    even if the model hasn't echoed the executed call verbatim.
    """
    if not isinstance(response, str):
        return False
    return bool(_GENERIC_ACK_RE.match(response.strip()))


# Tokens too generic to anchor a prose-vs-entity match. ``light`` and
# ``switch`` appear in nearly every entity_id and would otherwise match
# any sentence about lights/switches. ``ai`` etc. are sub-3-char and
# already filtered, but the noise list captures the longer common-domain
# words.
_PROSE_MATCH_STOPWORDS = frozenset(
    {
        "light",
        "lights",
        "switch",
        "switches",
        "sensor",
        "sensors",
        "binary",
        "media",
        "player",
        "input",
        "boolean",
        "climate",
        "cover",
        "covers",
        "scene",
        "scenes",
        "automation",
        "the",
        "and",
        "for",
        "with",
    }
)


# Verb patterns that describe the *opposite* of a given service. Used by
# the per-mention proximity check below: when a verb of this shape sits
# closer than _VERB_PROXIMITY_CHARS to an entity mention in the prose, the
# mention can't be confirming THIS service — the model is describing the
# inverse action there. Only paired services with a real inverse are
# listed; services like set_temperature or volume_set have no opposite
# verb in natural language.
_OPPOSITE_VERB_PATTERNS: dict[str, re.Pattern[str]] = {
    "turn_on": re.compile(
        r"\bturn(?:ing|ed)?\s+off\b|\bswitch(?:ing|ed)?\s+off\b|\bshut(?:ting)?\s+off\b",
        re.IGNORECASE,
    ),
    "turn_off": re.compile(
        r"\bturn(?:ing|ed)?\s+on\b|\bswitch(?:ing|ed)?\s+on\b",
        re.IGNORECASE,
    ),
    "open_cover": re.compile(
        r"\bclos(?:e|ing|ed)\b|\bshut(?:ting)?\b",
        re.IGNORECASE,
    ),
    "close_cover": re.compile(
        r"\bopen(?:ing|ed)?\b",
        re.IGNORECASE,
    ),
    "media_play": re.compile(
        r"\bpaus(?:e|ing|ed)\b|\bstopp(?:ing|ed)?\b",
        re.IGNORECASE,
    ),
    "media_pause": re.compile(
        r"\bplay(?:ing|ed)?\b|\bresum(?:e|ing|ed)\b",
        re.IGNORECASE,
    ),
    "media_stop": re.compile(
        r"\bplay(?:ing|ed)?\b|\bresum(?:e|ing|ed)\b",
        re.IGNORECASE,
    ),
}


# Verb patterns used to locate "the action just performed" relative to an
# entity mention. We take the closest match ENDING BEFORE the entity
# position and compare it against the executed service's opposite
# pattern — this gives per-mention granularity, so a multi-action turn
# like "Turned off the kitchen and on the porch" can verify each entity
# pairs with its own correct verb rather than rejecting both calls
# because both opposite verbs appear somewhere in the response.
_ACTION_VERB_RE = re.compile(
    r"\bturn(?:ing|ed)?\s+(?:on|off)\b"
    r"|\bswitch(?:ing|ed)?\s+(?:on|off)\b"
    r"|\bshut(?:ting)?\s+off\b"
    r"|\bopen(?:ing|ed)?\b"
    r"|\bclos(?:e|ing|ed)\b"
    r"|\bshut(?:ting)?\b"
    r"|\bplay(?:ing|ed)?\b"
    r"|\bpaus(?:e|ing|ed)\b"
    r"|\bstopp(?:ing|ed)?\b"
    r"|\bresum(?:e|ing|ed)\b",
    re.IGNORECASE,
)

# Maximum char distance between a verb's end and an entity mention's
# start for the verb to be considered "describing" that entity. Tuned
# to cover natural phrasings like "turning off the kitchen light" (~22
# chars) without bleeding into a previous clause.
_VERB_PROXIMITY_CHARS = 40


def _tokens_from_entity_id(entity_id: str) -> list[str]:
    """Return distinctive object_id tokens, stopword-filtered."""
    if not isinstance(entity_id, str) or "." not in entity_id:
        return []
    object_id = entity_id.split(".", 1)[-1].lower()
    return [
        t for t in re.split(r"[._\s]+", object_id) if len(t) > 2 and t not in _PROSE_MATCH_STOPWORDS
    ]


def _entity_ids_from_call(call: dict[str, Any]) -> list[str]:
    """Return the entity_id list for a ServiceCallDict-shaped call."""
    target = call.get("target") or {}
    raw = target.get("entity_id") if isinstance(target, dict) else None
    if isinstance(raw, str):
        return [raw] if raw else []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, str)]
    return []


def _nearest_verb_before(
    haystack: str, position: int, *, max_distance: int = _VERB_PROXIMITY_CHARS
) -> str | None:
    """Return the closest action-verb match that ends before *position*.

    Limited to ``max_distance`` chars so a verb in a previous clause
    doesn't get tied to an unrelated entity mention. Returns None when
    no verb is within range.
    """
    best: str | None = None
    best_dist = max_distance + 1
    for m in _ACTION_VERB_RE.finditer(haystack[:position]):
        dist = position - m.end()
        if dist < best_dist:
            best_dist = dist
            best = m.group(0)
    return best


def _response_describes_executed_call(
    response: str,
    executed_calls: list[dict[str, Any]],
    entities: list[EntitySnapshot] | None = None,
) -> bool:
    """True iff every entity-naming claim in *response* is backed by an
    actually executed call (and verb-consistent with it).

    Two stages:

    1. **Unbacked-entity veto.** Using the ``entities`` snapshot (the
       same filtered set the JSON command path uses), if the prose
       contains a distinctive token of a *known* entity that was NOT
       executed — e.g. ``bedroom`` after only ``light.kitchen`` ran —
       return False. This catches "Turned off the kitchen and bedroom
       lights" where the bedroom claim is hallucinated. Tokens shared
       with an executed entity are NOT treated as unbacked.

    2. **Per-mention action consistency.** For each executed call, find
       its entity tokens in the prose. For each occurrence, look at the
       nearest action verb within ``_VERB_PROXIMITY_CHARS`` *before*
       the entity. If that verb is the opposite of the executed
       service, this particular mention doesn't back the call (skip
       it). Otherwise count the mention as backing. The proximity
       scope is what makes a mixed-inverse turn — e.g. "Turned off the
       kitchen and on the porch" with both ``turn_off light.kitchen``
       and ``turn_on light.porch`` executed — match correctly, because
       each entity pairs with its own verb rather than the union of all
       verbs.

    Returns True iff stage 1 passes AND at least one executed call has
    a backed mention from stage 2. When ``entities`` is None, stage 1
    is skipped (no snapshot to cross-check against).
    """
    if not response or not executed_calls:
        return False
    haystack = response.lower()

    executed_ids: set[str] = set()
    for call in executed_calls:
        executed_ids.update(_entity_ids_from_call(call))
    all_executed_tokens: set[str] = set()
    for eid in executed_ids:
        all_executed_tokens.update(_tokens_from_entity_id(eid))

    # Stage 1: unbacked-entity veto. A token is "unbacked" only when no
    # executed entity shares it — so a token like "kitchen" appearing
    # both in executed light.kitchen and non-executed sensor.kitchen_temp
    # is fine (the prose mention could be referring to the executed one).
    if entities:
        for e in entities:
            eid = e.get("entity_id", "") if isinstance(e, dict) else ""
            if not eid or eid in executed_ids:
                continue
            for token in _tokens_from_entity_id(eid):
                if token in all_executed_tokens:
                    continue
                if re.search(rf"\b{re.escape(token)}\b", haystack):
                    return False

    # Stage 2: per-mention action consistency. Build a token → owning
    # calls map so each mention is judged only against the executed
    # calls that could plausibly explain it. EVERY mention of an
    # executed entity must be consistent with at least one of its
    # owning calls — otherwise a turn like ``turn_off(kitchen)`` +
    # ``turn_off(bedroom)`` with prose "Turned off kitchen and turned
    # on bedroom" would slip past (the bedroom token IS executed, so
    # stage 1 doesn't veto, and the kitchen mention alone would have
    # been enough under the old "any backed wins" rule).
    token_to_calls: dict[str, list[dict[str, Any]]] = {}
    for call in executed_calls:
        for eid in _entity_ids_from_call(call):
            for token in _tokens_from_entity_id(eid):
                token_to_calls.setdefault(token, []).append(call)

    if not token_to_calls:
        return False

    found_backed = False
    for token, owning_calls in token_to_calls.items():
        for m in re.finditer(rf"\b{re.escape(token)}\b", haystack):
            verb = _nearest_verb_before(haystack, m.start())
            mention_backed = False
            for call in owning_calls:
                service = str(call.get("service", "")).strip()
                if "." not in service:
                    continue
                service_name = service.split(".", 1)[1]
                opposite = _OPPOSITE_VERB_PATTERNS.get(service_name)
                if opposite is None:
                    # No opposite verb defined for this service —
                    # any mention is acceptable for this call.
                    mention_backed = True
                    break
                if verb is None:
                    # No verb within proximity — benefit of the doubt
                    # ("Done. Kitchen light." style).
                    mention_backed = True
                    break
                if not opposite.search(verb):
                    mention_backed = True
                    break
            if not mention_backed:
                # This mention contradicts every executed call that
                # could explain it — the prose makes a false claim
                # about an executed entity.
                return False
            found_backed = True
    return found_backed


def _response_names_unbacked_entity(
    response: str,
    executed_calls: list[dict[str, Any]],
    entities: list[EntitySnapshot] | None,
) -> bool:
    """True if the prose mentions a distinctive token of a known entity
    that was *not* executed (Stage 1 of
    ``_response_describes_executed_call``, extracted for reuse).

    Used as the safety check for the block-strip path: when the model
    authored a JSON command block matching the executed action AND its
    prose doesn't actively claim a different device, the prose is
    anchored to the executed action even if it doesn't otherwise
    name the executed entity. Without an entities snapshot we can't
    cross-check, so this returns False (no contradiction observable).
    """
    if not response or not executed_calls or not entities:
        return False
    haystack = response.lower()
    executed_ids: set[str] = set()
    for call in executed_calls:
        executed_ids.update(_entity_ids_from_call(call))
    all_executed_tokens: set[str] = set()
    for eid in executed_ids:
        all_executed_tokens.update(_tokens_from_entity_id(eid))
    for e in entities:
        eid = e.get("entity_id", "") if isinstance(e, dict) else ""
        if not eid or eid in executed_ids:
            continue
        for token in _tokens_from_entity_id(eid):
            if token in all_executed_tokens:
                continue
            if re.search(rf"\b{re.escape(token)}\b", haystack):
                return True
    return False


def _prose_is_trusted_after_tool(
    response: str,
    executed_calls: list[dict[str, Any]],
    entities: list[EntitySnapshot] | None,
) -> bool:
    """True iff *response* is safe to bypass the unbacked-action policy
    after one or more tool calls executed.

    Single decision point used by every code path that needs to set
    ``suppressed_duplicate_command``. Trust requires the prose match one
    of three shapes:

    1. The exact synthesized confirmation prefix from
       ``_build_command_confirmation`` (exhaustion-path output).
    2. A generic acknowledgement with no specific claim
       (``Done.``, ``All set.``, ``Got it.``, ``Sure.``).
    3. A natural-language description that names an executed entity AND
       uses an action verb consistent with the executed service AND does
       not name any non-executed known entity (see
       ``_response_describes_executed_call``).

    Any other prose — including the duplicate-stripper case where the
    model wrote about a different device alongside its echoed command
    block — fails the check, so the policy's unbacked-action stomp
    runs as a safety guard.
    """
    if not executed_calls:
        return False
    return (
        _response_is_synthesized_confirmation(response, executed_calls)
        or _is_generic_acknowledgement(response)
        or _response_describes_executed_call(response, executed_calls, entities)
    )


def _tool_failure_response(
    tool_log: list[dict[str, Any]] | None,
    *,
    suffix: str,
) -> str:
    """Compose a user-facing message when the tool loop bailed out.

    If any ``execute_command`` already succeeded this turn, the result is
    something like ``"Done — light turn_off (kitchen). " + suffix`` so the
    user sees what already happened and is not tempted to retry and run
    the same service a second time. Otherwise just ``suffix`` is returned.
    """
    executed = _executed_service_calls_from_log(tool_log)
    if not executed:
        return suffix
    return _build_command_confirmation(executed) + " " + suffix


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

    # When the duplicate-execution guard has already converted a turn to
    # ``intent: "answer"`` because ``execute_command`` actually fired,
    # action-like response prose ("Turning off the kitchen light") is a
    # legitimate confirmation, not an unbacked claim. Skip the
    # unbacked-action stomping below so we don't tell the user we
    # didn't do something the tool did.
    #
    # SAFETY: the flag is internal and must only be honored when the
    # result shape matches what the internal setters produce —
    # ``intent == "answer"`` with no ``calls``. Otherwise this would
    # be a model-supplied bypass: a prompt could induce
    # ``{"intent":"command","calls":[...],
    # "suppressed_duplicate_command":true}`` and skip the entire
    # safe-command policy before reaching the dispatcher.
    # parse_architect_response also strips the flag from model JSON
    # as a first line of defense; this check is the belt+suspenders.
    if (
        result.get("suppressed_duplicate_command")
        and result.get("intent") == "answer"
        and not result.get("calls")
    ):
        return result

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
