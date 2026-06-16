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

import copy
import json
import logging
import re
from typing import TYPE_CHECKING, Any

import yaml

from ..automation_utils import (
    _collect_referenced_entity_ids,
    _find_unknown_entity_ids,
    assess_automation_risk,
    validate_automation_payload,
)
from ..types import ArchitectResponse, EntitySnapshot
from .command_policy import (
    _call_signature,
    _executed_call_signatures,
    _executed_service_calls_from_log,
    _prose_describes_attempted_call,
    _prose_is_trusted_after_tool,
    _response_names_unbacked_entity,
    apply_command_policy,
    synthesize_approval_from_tool_log,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Inline entity tile markers (`[[entity:…]]` / `[[entities:…]]`) belong
# in answer-style replies where the user wants to see live device
# state. Automation and scene proposals describe a *rule*, not the
# current state of a device — embedding a live tile in those bubbles
# (as some models, notably deepseek, like to do regardless of the
# prompt) shoves an irrelevant brightness slider into the proposal
# card. Strip the markers from the prose before we hand it off.
_ENTITY_MARKER_RE = re.compile(r"\s*\[\[(?:entity|entities|areas):[^\]]+\]\]")


def _strip_entity_markers(text: str) -> str:
    """Remove `[[entity:…]]` / `[[entities:…]]` markers from response prose."""
    if not isinstance(text, str) or not text:
        return text
    cleaned = _ENTITY_MARKER_RE.sub("", text)
    # Collapse runs of trailing blank lines the marker removal left
    # behind so the response doesn't end with a stretch of empty
    # whitespace on the rendered card.
    return re.sub(r"\n{3,}", "\n\n", cleaned).rstrip()


def _strip_markers_for_proposal_intents(data: dict[str, Any]) -> None:
    """Strip entity tile markers from automation/scene proposal prose.

    Mutates ``data["response"]`` in place. Called for any reply whose
    *original* intent indicated a creation flow — i.e. the model said
    intent: automation / scene, OR a payload (`automation`/`scene`)
    was present even if the JSON didn't carry the intent explicitly.
    The post-validation downgrade to ``answer`` (when the payload is
    invalid) doesn't matter — by that point we've already overwritten
    the response with a validation-error message that has no markers.
    """
    if "response" not in data:
        return
    intent = data.get("intent")
    if intent in ("automation", "scene") or data.get("automation") or data.get("scene"):
        data["response"] = _strip_entity_markers(data["response"])
        data["response"] = _strip_trigger_action_recap(data["response"])


# Lines like "Trigger: …", "Condition: …", "Action: …" (and their
# plural variants) restate the YAML in prose — pure duplication of
# the proposal card rendered just below the bubble. The streaming
# prompt explicitly forbids these but models occasionally slip them
# in anyway, especially deepseek-style models that like to summarise
# their own JSON output back as text. Strip the lines server-side so
# the bubble stays focused on the human framing of what the rule
# does.
_RECAP_LINE_RE = re.compile(
    r"^\s*(?:[-*•]\s*|\d+[.)]\s*)?\**\s*"
    r"(?:Triggers?|Conditions?|Actions?|Trigger\s+condition|Trigger\s+state)"
    r"\s*\**\s*:\s.*$",
    re.IGNORECASE | re.MULTILINE,
)


def _strip_trigger_action_recap(text: str) -> str:
    """Remove ``Trigger:`` / ``Condition:`` / ``Action:`` recap lines."""
    if not isinstance(text, str) or not text:
        return text
    cleaned = _RECAP_LINE_RE.sub("", text)
    # Collapse the blank stretch the line removal leaves behind.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.rstrip()


# Map each service action to a semantic verb so an entity swap across
# domains (lock.lock → cover.close_cover) preserves user intent.
_SERVICE_ACTION_VERB: dict[str, str] = {
    "turn_on": "on",
    "open_cover": "on",
    "open": "on",
    "unlock": "on",
    "start": "on",
    "activate": "on",
    "enable": "on",
    "turn_off": "off",
    "close_cover": "off",
    "close": "off",
    "lock": "off",
    "stop": "off",
    "deactivate": "off",
    "disable": "off",
    "pause": "off",
}

_DOMAIN_VERB_TO_ACTION: dict[str, dict[str, str]] = {
    "light": {"on": "turn_on", "off": "turn_off"},
    "switch": {"on": "turn_on", "off": "turn_off"},
    "fan": {"on": "turn_on", "off": "turn_off"},
    "climate": {"on": "turn_on", "off": "turn_off"},
    "media_player": {"on": "turn_on", "off": "turn_off"},
    "vacuum": {"on": "start", "off": "stop"},
    "scene": {"on": "turn_on", "off": "turn_on"},
    "input_boolean": {"on": "turn_on", "off": "turn_off"},
    "cover": {"on": "open_cover", "off": "close_cover"},
    "lock": {"on": "unlock", "off": "lock"},
}


def _service_verb_for_domain(verb: str, domain: str) -> str:
    """Pick the right service action for ``domain`` given an "on"/"off" verb."""
    table = _DOMAIN_VERB_TO_ACTION.get(domain)
    if table is None:
        return f"turn_{verb}"
    return table.get(verb, f"turn_{verb}")


# Controllable-device domains. Auto-correct only rewrites a bad
# entity_id when both the bad and the candidate sit in this set, so
# helper-class entities (input_boolean, sensor mirrors, etc.) never
# get silently substituted for a real device — or vice versa.
_REAL_DEVICE_DOMAINS: frozenset[str] = frozenset(
    {
        "light",
        "switch",
        "cover",
        "lock",
        "climate",
        "fan",
        "media_player",
        "vacuum",
        "scene",
        "alarm_control_panel",
        "water_heater",
    }
)


# Keys whose subtrees are integration-defined payload, NOT HA entity
# references. An ``entity_id`` inside ``event_data`` (event trigger
# filter), ``event_data_template``, ``payload`` (MQTT trigger), or
# ``payload_template`` is application data the user defines for matching
# events — it must not be substituted or collected as an HA entity ref.
# The matching authoritative collector in automation_utils ignores them.
_NON_ENTITY_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {
        "event_data",
        "event_data_template",
        "payload",
        "payload_template",
        "variables",
        "response_variable",
        "metadata",
    }
)


def _resolve_unknown_entity_ids(
    reason: str,  # noqa: ARG001 -- callers gate on this; signature is the contract
    automation: dict[str, Any],
    entities: list[EntitySnapshot] | None,
    hass: HomeAssistant | None = None,
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Auto-correct an "unknown entity_id" rejection by looking up each
    bad entity_id by slug + friendly_name across the snapshot. Only
    rewrites controllable-device domains and only when slug/fname maps
    to exactly one real device — partial matches fall through (silent
    half-fixes are worse than a clear ask). Returns
    ``(patched_or_None, substitutions)``."""
    if not entities:
        return None, {}

    by_slug: dict[str, list[str]] = {}
    by_fname: dict[str, list[str]] = {}
    real_ids: set[str] = set()
    for e in entities:
        eid = e.get("entity_id", "")
        if "." not in eid:
            continue
        real_ids.add(eid)
        slug = eid.split(".", 1)[1]
        by_slug.setdefault(slug, []).append(eid)
        fname = ((e.get("attributes") or {}).get("friendly_name") or "").lower().strip()
        if fname:
            by_fname.setdefault(fname, []).append(eid)

    # Walk the automation for the FULL set of referenced entity_ids
    # rather than parsing the validator reason — the validator truncates
    # its preview to the first three (with a ``(+N more)`` suffix), so a
    # regex over the reason string would miss the rest and a single
    # retry would fail on them.
    # Use the validator's own context-aware walker (state-machine
    # contexts only: trigger / condition / action target / data /
    # entity_id). Integration payloads like ``event_data: {entity_id:
    # ...}`` are skipped — those are user-defined match payloads, not
    # HA entity references.
    referenced = _collect_referenced_entity_ids(automation)
    # Ground truth for "unknown": delegate to the same lookup the
    # validator uses (``_find_unknown_entity_ids``) — state machine
    # AND entity registry. The snapshot filters out unavailable /
    # ignored / non-actionable entities; the registry covers
    # disabled-but-real entities that have no state but exist. Using
    # only ``hass.states`` would treat registered-but-stateless entities
    # as unknown and may silently retarget them. Fall back to
    # snapshot-only comparison when no ``hass`` is passed (unit tests).
    if hass is not None:
        bad_ids = _find_unknown_entity_ids(hass, set(referenced))
    else:
        bad_ids = sorted(referenced - real_ids)
    if not bad_ids:
        return None, {}

    substitutions: dict[str, str] = {}
    for bad in bad_ids:
        if bad in real_ids:
            continue  # validator complained about something else, not domain
        bad_domain = bad.split(".", 1)[0]
        # Compatibility gate: only auto-correct when the bad entity_id
        # itself names a controllable-device domain. A
        # ``person.garage_door`` trigger that shares its slug with
        # ``cover.garage_door`` must NOT be silently rewritten — the
        # automation would then fire on the cover's state changes
        # instead of the person arriving, which is a semantic change,
        # not a typo fix. Same for zone/device_tracker/sensor triggers.
        if bad_domain not in _REAL_DEVICE_DOMAINS:
            return None, {}
        bad_slug = bad.split(".", 1)[1]
        candidates: set[str] = set(by_slug.get(bad_slug, []))
        candidates |= set(by_fname.get(bad_slug.replace("_", " ").lower(), []))
        # Candidate side of the gate: substitution target must also be a
        # controllable-device domain, so we never retarget e.g.
        # ``light.x`` onto an ``input_boolean.x`` helper that only
        # mirrors state.
        candidates = {c for c in candidates if c.split(".", 1)[0] in _REAL_DEVICE_DOMAINS}
        if len(candidates) == 1:
            substitutions[bad] = next(iter(candidates))
            continue
        # Zero candidates, or more than one — refuse rather than guess.
        # Retargeting to "the one device the prompt names elsewhere"
        # would silently rewrite an unrelated trigger onto an unrelated
        # device and is intentionally not attempted.
        return None, {}

    if not substitutions:
        return None, {}

    # Refuse cross-domain substitutions that would leave a state trigger
    # or condition pinned to a value the new domain never reports. A
    # ``lock.front_door`` trigger with ``to: locked`` resolves to a
    # ``cover.front_door`` — covers never emit ``locked``, so the
    # automation would silently never fire. The validator accepts
    # free-form state strings here, so this gate must live at our level.
    cross_domain_subs = {
        bad: new
        for bad, new in substitutions.items()
        if bad.split(".", 1)[0] != new.split(".", 1)[0]
    }
    if cross_domain_subs and _bad_has_pinned_state_semantics(automation, set(cross_domain_subs)):
        return None, {}

    # Refuse a substitution that would leave an action's ``target``
    # entity_id list straddling multiple domains. Such a list survives
    # validation but the action's service-domain can only address one
    # domain at runtime — the off-domain entries silently no-op or fail.
    if _substitution_yields_mixed_domain_target(automation, substitutions):
        return None, {}

    # Refuse a substitution that would invert the user's intent. A
    # ``light.turn_off`` action targeting ``light.movie`` resolved to
    # ``scene.movie`` would otherwise become ``scene.turn_on`` because
    # scenes have no "off" verb — silently activating the scene for an
    # off request. Other off-verbs paired with a scene swap are
    # equivalent.
    if _substitution_inverts_intent(automation, substitutions):
        return None, {}

    # Refuse a cross-domain swap when the action carries domain-specific
    # service data (e.g. ``brightness`` for a light) that the target
    # domain's service doesn't accept. The validator checks service
    # existence but not its data schema — the patched automation would
    # be accepted and fail at runtime.
    if _substitution_drops_required_service_data(automation, substitutions):
        return None, {}

    patched: dict[str, Any] = copy.deepcopy(automation)
    _apply_entity_substitutions(patched, substitutions)
    return patched, substitutions


# Service-call data fields that only make sense for entities in a
# specific domain. When auto-correct moves an entity across domains,
# any of these keys present in the action's data refuse the swap — the
# new domain's service won't accept them, so the automation would pass
# validation but fail at runtime.
_DOMAIN_SPECIFIC_SERVICE_DATA: dict[str, frozenset[str]] = {
    "light": frozenset(
        {
            "brightness",
            "brightness_pct",
            "brightness_step",
            "brightness_step_pct",
            "color_name",
            "color_temp",
            "color_temp_kelvin",
            "effect",
            "flash",
            "hs_color",
            "kelvin",
            "profile",
            "rgb_color",
            "rgbw_color",
            "rgbww_color",
            "transition",
            "white",
            "xy_color",
        }
    ),
    "cover": frozenset({"position", "tilt_position"}),
    "climate": frozenset(
        {
            "fan_mode",
            "humidity",
            "hvac_mode",
            "preset_mode",
            "swing_mode",
            "target_temp_high",
            "target_temp_low",
            "temperature",
        }
    ),
    "fan": frozenset({"direction", "oscillating", "percentage", "preset_mode"}),
    "media_player": frozenset(
        {
            "media_content_id",
            "media_content_type",
            "shuffle",
            "sound_mode",
            "source",
            "volume_level",
        }
    ),
    "lock": frozenset({"code"}),
    "vacuum": frozenset({"params"}),
}


def _substitution_drops_required_service_data(node: Any, substitutions: dict[str, str]) -> bool:
    """Return True if applying ``substitutions`` would leave an action
    carrying service-data keys that only make sense for its previous
    (source) domain. Validator accepts unknown extra keys; runtime
    rejects them. Refusing the substitution surfaces the issue back to
    the user instead of producing a silently broken automation."""
    if isinstance(node, dict):
        eids: list[str] = []
        eids.extend(_ids_from_entity_field(node.get("entity_id")))
        tgt = node.get("target")
        if isinstance(tgt, dict):
            eids.extend(_ids_from_entity_field(tgt.get("entity_id")))
        data = node.get("data")
        if isinstance(data, dict):
            eids.extend(_ids_from_entity_field(data.get("entity_id")))
        for e in eids:
            new = substitutions.get(e)
            if new is None:
                continue
            src_domain = e.split(".", 1)[0]
            new_domain = new.split(".", 1)[0]
            if src_domain == new_domain:
                continue
            src_keys = _DOMAIN_SPECIFIC_SERVICE_DATA.get(src_domain)
            if not src_keys:
                continue
            tgt_keys = _DOMAIN_SPECIFIC_SERVICE_DATA.get(new_domain, frozenset())
            # Any source-only key the new domain doesn't share → refuse.
            offending = src_keys - tgt_keys
            if isinstance(data, dict) and any(k in offending for k in data):
                return True
            # HA also accepts service-data flat on the action node.
            if any(k in offending for k in node if k not in {"service", "action"}):
                return True
        for k, v in node.items():
            if k in _NON_ENTITY_PAYLOAD_KEYS:
                continue
            if _substitution_drops_required_service_data(v, substitutions):
                return True
    elif isinstance(node, list):
        for item in node:
            if _substitution_drops_required_service_data(item, substitutions):
                return True
    return False


def _ids_from_entity_field(value: Any) -> list[str]:
    """Extract entity_ids from a string (single or comma-separated),
    list, or anything else (→ empty)."""
    if isinstance(value, str):
        if "," in value:
            return [p.strip() for p in value.split(",") if p.strip()]
        return [value] if value else []
    if isinstance(value, list):
        return [x for x in value if isinstance(x, str)]
    return []


def _substitution_inverts_intent(node: Any, substitutions: dict[str, str]) -> bool:
    """Return True if applying ``substitutions`` would invert an
    action's semantic. The only case so far is an ``off``-verb action
    whose target moves to the ``scene`` domain — scenes only have an
    "activate" verb, so an off request would silently fire the scene."""
    if isinstance(node, dict):
        eids: list[str] = []
        eids.extend(_ids_from_entity_field(node.get("entity_id")))
        tgt = node.get("target")
        if isinstance(tgt, dict):
            eids.extend(_ids_from_entity_field(tgt.get("entity_id")))
        data = node.get("data")
        if isinstance(data, dict):
            eids.extend(_ids_from_entity_field(data.get("entity_id")))
        svc = node.get("service") or node.get("action")
        if isinstance(svc, str) and "." in svc:
            action = svc.split(".", 1)[1]
            verb = _SERVICE_ACTION_VERB.get(action)
            if verb == "off":
                for e in eids:
                    new = substitutions.get(e)
                    if new and new.split(".", 1)[0] == "scene":
                        return True
        for k, v in node.items():
            if k in _NON_ENTITY_PAYLOAD_KEYS:
                continue
            if _substitution_inverts_intent(v, substitutions):
                return True
    elif isinstance(node, list):
        for item in node:
            if _substitution_inverts_intent(item, substitutions):
                return True
    return False


def _bad_has_pinned_state_semantics(node: Any, bad_ids: set[str]) -> bool:
    """Return True if any node references one of ``bad_ids`` alongside
    a ``to:``, ``from:`` or ``state:`` key — i.e. the trigger/condition
    pins specific state values that don't carry across a domain swap."""
    if isinstance(node, dict):
        eids = _ids_from_entity_field(node.get("entity_id"))
        if any(e in bad_ids for e in eids) and any(k in node for k in ("to", "from", "state")):
            return True
        for k, v in node.items():
            if k in _NON_ENTITY_PAYLOAD_KEYS:
                continue
            if _bad_has_pinned_state_semantics(v, bad_ids):
                return True
    elif isinstance(node, list):
        for item in node:
            if _bad_has_pinned_state_semantics(item, bad_ids):
                return True
    return False


def _substitution_yields_mixed_domain_target(node: Any, substitutions: dict[str, str]) -> bool:
    """Return True if applying ``substitutions`` would produce an
    entity_id list (whether under ``target.entity_id``, the legacy
    ``data.entity_id``, or the flat ``entity_id`` at the action node)
    that spans more than one domain. The service-domain can only serve
    one domain at runtime, so the off-domain entries would silently
    fail."""

    def _list_is_mixed(value: Any) -> bool:
        # Normalise to a list of entity_ids — accept the canonical list
        # form AND HA's comma-separated string shorthand.
        ids = _ids_from_entity_field(value)
        if len(ids) < 2:
            return False
        if not any(x in substitutions for x in ids):
            return False
        patched = [substitutions.get(x, x) for x in ids]
        domains = {p.split(".", 1)[0] for p in patched if "." in p}
        return len(domains) > 1

    if isinstance(node, dict):
        if _list_is_mixed(node.get("entity_id")):
            return True
        tgt = node.get("target")
        if isinstance(tgt, dict) and _list_is_mixed(tgt.get("entity_id")):
            return True
        data = node.get("data")
        if isinstance(data, dict) and _list_is_mixed(data.get("entity_id")):
            return True
        for k, v in node.items():
            if k in _NON_ENTITY_PAYLOAD_KEYS:
                continue
            if _substitution_yields_mixed_domain_target(v, substitutions):
                return True
    elif isinstance(node, list):
        for item in node:
            if _substitution_yields_mixed_domain_target(item, substitutions):
                return True
    return False


def _apply_entity_substitutions(node: Any, substitutions: dict[str, str]) -> None:
    """Walk the automation tree applying entity_id substitutions; also swap
    the action service-domain prefix when the substituted target moves
    domain (so ``light.turn_on`` becomes ``switch.turn_on`` after the
    entity moves). Service swap is gated on an entity in this node
    actually being substituted — otherwise unrelated multi-target
    actions like ``homeassistant.update_entity`` targeting several
    sensors would be wrongly rewritten to ``sensor.update_entity``."""
    if isinstance(node, dict):
        node_substituted = False

        def _apply_field(container: dict[str, Any], key: str) -> tuple[bool, list[str]]:
            """Substitute ``container[key]``; handles string, comma-split
            string, and list shapes. Returns (changed, final_ids)."""
            value = container.get(key)
            if isinstance(value, str):
                if "," in value:
                    parts = [p.strip() for p in value.split(",")]
                    new_parts = [substitutions.get(p, p) for p in parts]
                    if new_parts != parts:
                        container[key] = ", ".join(new_parts)
                        return True, [p for p in new_parts if "." in p]
                    return False, [p for p in parts if "." in p]
                if value in substitutions:
                    container[key] = substitutions[value]
                    return True, [container[key]]
                return False, [value] if "." in value else []
            if isinstance(value, list):
                new_list = [substitutions.get(x, x) for x in value]
                if new_list != value:
                    container[key] = new_list
                    return True, [x for x in new_list if isinstance(x, str) and "." in x]
                return False, [x for x in value if isinstance(x, str) and "." in x]
            return False, []

        flat_changed, flat_ids = _apply_field(node, "entity_id")
        if flat_changed:
            node_substituted = True

        new_domain: str | None = None
        tgt = node.get("target")
        if isinstance(tgt, dict):
            tgt_changed, tgt_ids = _apply_field(tgt, "entity_id")
            if tgt_changed:
                node_substituted = True
                domains = {x.split(".", 1)[0] for x in tgt_ids}
                if len(domains) == 1:
                    new_domain = next(iter(domains))

        # Legacy ``data.entity_id`` shape — service call carries the
        # entity in ``data`` instead of ``target``. Validator still
        # accepts it, so we have to swap the service domain here too.
        data = node.get("data")
        if isinstance(data, dict):
            data_changed, data_ids = _apply_field(data, "entity_id")
            if data_changed:
                node_substituted = True
                if new_domain is None:
                    domains = {x.split(".", 1)[0] for x in data_ids}
                    if len(domains) == 1:
                        new_domain = next(iter(domains))

        if new_domain is None and node_substituted and flat_ids:
            domains = {x.split(".", 1)[0] for x in flat_ids}
            if len(domains) == 1:
                new_domain = next(iter(domains))

        svc = node.get("service") or node.get("action")
        svc_key = "service" if node.get("service") else "action"
        if isinstance(svc, str) and "." in svc and new_domain is not None and node_substituted:
            cur_domain, action = svc.split(".", 1)
            if cur_domain != new_domain:
                # Map by VERB across domains so e.g. ``lock.lock`` →
                # ``cover.close_cover`` (both express "off/close" intent)
                # instead of the invalid literal swap ``cover.lock``.
                verb = _SERVICE_ACTION_VERB.get(action)
                if verb is not None:
                    node[svc_key] = f"{new_domain}.{_service_verb_for_domain(verb, new_domain)}"
                else:
                    node[svc_key] = f"{new_domain}.{action}"

        for k, v in node.items():
            if k in _NON_ENTITY_PAYLOAD_KEYS:
                continue
            _apply_entity_substitutions(v, substitutions)
    elif isinstance(node, list):
        for item in node:
            _apply_entity_substitutions(item, substitutions)


def _humanise_unknown_entity_error(
    reason: str,
    entities: list[EntitySnapshot] | None,
) -> str:
    """Replace an "unknown entity_id" validator reason with a clarification
    that lists the user's actual devices grouped by domain (cap 3/domain so
    the chat bubble stays readable)."""
    if not entities or "unknown entity_id" not in reason:
        return (
            f"I couldn't create a valid automation from that request: {reason}. "
            "Please refine the request and try again."
        )

    by_domain: dict[str, list[str]] = {}
    # Domain → English plural label. Naive "+s" mishandles "switch" and
    # uncountable nouns like "climate", so map explicitly.
    domain_labels: dict[str, str] = {
        "light": "lights",
        "switch": "switches",
        "cover": "covers",
        "lock": "locks",
        "climate": "climate",
        "fan": "fans",
        "media_player": "media players",
        "vacuum": "vacuums",
        "scene": "scenes",
    }
    controllable_domains = tuple(domain_labels)
    for e in entities:
        eid = e.get("entity_id", "")
        if "." not in eid:
            continue
        domain = eid.split(".", 1)[0]
        if domain not in controllable_domains:
            continue
        fname = (e.get("attributes") or {}).get("friendly_name") or eid
        by_domain.setdefault(domain, []).append(str(fname))

    parts: list[str] = []
    for domain in controllable_domains:
        names = by_domain.get(domain)
        if not names:
            continue
        sample = ", ".join(names[:3])
        more = f" (+{len(names) - 3} more)" if len(names) > 3 else ""
        parts.append(f"{domain_labels[domain]}: {sample}{more}")

    if not parts:
        return (
            f"I couldn't create that automation — {reason}. "
            "Tell me which of your devices you'd like to use and when it should run."
        )

    summary = "; ".join(parts)
    return (
        f"I couldn't build that — {reason}. "
        f"Here's what I can actually control in your home — {summary}. "
        "Which device should the automation use, and when should it run?"
    )


# Coerce a state trigger to numeric_state when the prompt phrases a
# numeric comparator ("drops below 18", "warmer than 26"). Only state
# triggers on entities whose domain produces numeric measurements are
# eligible — coercing e.g. a binary_sensor.motion trigger would silently
# rewrite an unrelated trigger into a meaningless numeric threshold.
_NUMERIC_ELIGIBLE_DOMAINS: frozenset[str] = frozenset(
    {"sensor", "climate", "number", "input_number", "weather"}
)

_NUMERIC_THRESHOLD_RE = re.compile(
    r"\b(?:drops?|falls?|rises?|gets?|goes?)\s+"
    r"(?P<word>below|above|under|over|to)\s+(?P<n1>-?\d+(?:\.\d+)?)\b"
    r"|\b(?:warmer|hotter|higher)\s+than\s+(?P<n2>-?\d+(?:\.\d+)?)\b"
    r"|\b(?:cooler|colder|lower)\s+than\s+(?P<n3>-?\d+(?:\.\d+)?)\b"
    r"|\b(?P<word2>below|above|under|over)\s+(?P<n4>-?\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)


# Delimiters that end a trigger clause: commas, semicolons, the
# conjunction ``then``, or an unambiguous action-clause verb. The
# anchor-based clause extraction below restricts the imperative-verb
# split to the segment AFTER the trigger anchor (when/if/...), so
# leading-imperative prompts like "Turn on the heater when temperature
# drops below 18" still match ("below 18" sits inside the anchored
# tail "when temperature drops below 18"). Only verbs that are
# unambiguous action language in this position are listed — broad ones
# like "open"/"close"/"start"/"stop" double as state nouns and are
# omitted.
_TRIGGER_CLAUSE_SPLIT_RE = re.compile(
    r"[,;]|\bthen\b|\b(?:set|turn|send|notify|alert|activate)\b",
    re.IGNORECASE,
)

# Explicit trigger markers — when present, the trigger clause begins at
# the marker and extends to the next action-clause delimiter. This
# anchors the comparator search so "Set the thermostat below 18 when
# the outdoor temperature changes" can't attribute the action's
# "below 18" to the trigger.
_TRIGGER_ANCHOR_RE = re.compile(
    r"\b(?:when|if|once|while|whenever|as\s+soon\s+as)\b",
    re.IGNORECASE,
)


def _trigger_clause_of(user_message: str) -> str:
    """Return the substring of ``user_message`` that describes the
    trigger. When the prompt contains an explicit trigger marker
    (``when``, ``if``, ``once``, ``while``, ``whenever``, ``as soon as``)
    the clause spans from that marker to the next action-clause
    delimiter (comma/semicolon/``then``) or end of string. Otherwise
    the clause is the head of the message up to the first delimiter."""
    if not user_message:
        return ""
    anchor = _TRIGGER_ANCHOR_RE.search(user_message)
    if anchor is not None:
        tail = user_message[anchor.start() :]
        m = _TRIGGER_CLAUSE_SPLIT_RE.search(tail)
        return tail[: m.start()] if m else tail
    m = _TRIGGER_CLAUSE_SPLIT_RE.search(user_message)
    return user_message[: m.start()] if m else user_message


def _extract_numeric_threshold(user_message: str) -> tuple[str, float] | None:
    """Return ``("below"|"above", value)`` extracted from a comparator prompt, or None."""
    if not user_message:
        return None
    m = _NUMERIC_THRESHOLD_RE.search(user_message)
    if not m:
        return None
    n_str = m.group("n1") or m.group("n2") or m.group("n3") or m.group("n4")
    if n_str is None:
        return None
    try:
        n = float(n_str)
    except ValueError:
        return None
    word = (m.group("word") or m.group("word2") or "").lower()
    if m.group("n2") is not None:
        direction = "above"
    elif m.group("n3") is not None or word in ("below", "under"):
        direction = "below"
    elif word in ("above", "over"):
        direction = "above"
    elif word == "to":
        # "drops/falls to N" implies the value is falling toward N (below);
        # "rises to N" implies rising toward N (above). "goes/gets to N"
        # is ambiguous (reaching N from either side) — refuse to coerce.
        verb = m.group(0).split(None, 1)[0].lower()
        if verb in ("drops", "drop", "falls", "fall"):
            direction = "below"
        elif verb in ("rises", "rise"):
            direction = "above"
        else:
            return None
    else:
        direction = "below"
    return direction, n


def _read_triggers(automation: dict[str, Any]) -> list[Any]:
    """Return the automation's trigger payload as a list without
    mutating ``automation``. Singular ``trigger`` key wins over the
    plural ``triggers`` to match the validator's precedence — modifying
    the plural list when the validator reads the singular one would
    silently change which trigger is active after ``_commit_triggers``
    promotes the list and drops the singular key."""
    single_val = automation.get("trigger")
    if isinstance(single_val, list):
        return single_val
    if isinstance(single_val, dict):
        return [single_val]
    triggers_val = automation.get("triggers")
    if isinstance(triggers_val, list):
        return triggers_val
    if isinstance(triggers_val, dict):
        return [triggers_val]
    return []


def _commit_triggers(automation: dict[str, Any], triggers: list[Any]) -> None:
    """Write ``triggers`` back as the canonical ``triggers`` list and
    drop the legacy singular ``trigger`` key. The validator prioritizes
    the singular key when both are present, so mutating only the list
    would leave the LoRA's original (rejected) singular trigger in
    force despite a successful coercion."""
    automation["triggers"] = triggers
    automation.pop("trigger", None)


def _coerce_numeric_state_triggers(automation: dict[str, Any], user_message: str) -> bool:
    """Rewrite a state trigger to numeric_state when the prompt carries a
    numeric comparator. Only rewrites state-platform triggers whose
    entity_id domain produces numeric measurements (sensor/climate/...).
    Unrelated triggers (sun, time, binary_sensor state, …) are left
    untouched so a multi-trigger automation keeps its original semantics.

    Refuses to coerce when the prompt is ambiguous — i.e. when the prompt
    contains multiple distinct numeric comparators (each could belong to
    a different trigger), or when one comparator could plausibly target
    several eligible triggers. In those cases the validator's rejection
    bubbles up so the user can clarify.

    Mutates in place; returns True if anything changed."""
    # Restrict comparator search to the trigger clause — the head of the
    # prompt up to the first action-clause delimiter. Otherwise a
    # numeric value that lives in the action ("when the outdoor temp
    # changes, set the thermostat below 18") gets attached to the
    # trigger, changing when the automation fires.
    trigger_clause = _trigger_clause_of(user_message)
    extracted = _extract_numeric_threshold(trigger_clause)
    if extracted is None:
        return False
    # Bail out if the trigger clause names more than one distinct numeric
    # threshold — we can't know which trigger each one targets without
    # phrase-level alignment, and applying the first to every trigger
    # silently changes "temperature above 25 OR humidity below 40" into
    # "above 25 OR above 25".
    all_matches = list(_NUMERIC_THRESHOLD_RE.finditer(trigger_clause))
    distinct = {m.group(0).lower() for m in all_matches}
    if len(distinct) > 1:
        return False
    direction, value = extracted
    triggers = _read_triggers(automation)
    # If an existing numeric_state trigger already carries this exact
    # comparator (same direction + value), the prompt's threshold is
    # already attached to a trigger — applying it again to some other
    # state trigger would silently change its semantics. Refuse.
    for tr in triggers:
        if not isinstance(tr, dict):
            continue
        platform = tr.get("trigger") or tr.get("platform")
        if platform == "numeric_state" and tr.get(direction) == value:
            return False
    eligible: list[dict[str, Any]] = []
    for tr in triggers:
        if not isinstance(tr, dict):
            continue
        platform = tr.get("trigger") or tr.get("platform")
        if platform != "state":
            continue
        eid = tr.get("entity_id")
        if not isinstance(eid, str) or "." not in eid:
            continue
        domain = eid.split(".", 1)[0]
        if domain not in _NUMERIC_ELIGIBLE_DOMAINS:
            continue
        # climate.* and weather.* entities expose textual primary states
        # ("heat"/"off", "sunny"/"cloudy"); their numeric readings live
        # on attributes (``current_temperature``, ``humidity``, …). A
        # numeric_state trigger against the primary state would never
        # fire. Require an explicit ``attribute`` or refuse coercion —
        # we'd have to guess the attribute name otherwise, and a wrong
        # guess fails just as silently.
        if domain in ("climate", "weather") and not tr.get("attribute"):
            continue
        # A trigger that already names concrete ``to:`` / ``from:``
        # values is a deliberate state trigger, not a placeholder the
        # LoRA forgot to fill in. Coercing it to numeric_state would
        # silently change the fire condition — e.g. a thermostat
        # ``to: off`` trigger paired with an action clause ``set the
        # target below 18`` must NOT become ``below: 18``.
        if "to" in tr or "from" in tr:
            continue
        eligible.append(tr)
    # With exactly one comparator AND several eligible state triggers we
    # can't tell which trigger the comparator applies to — refuse rather
    # than rewrite all of them to the same threshold.
    if len(eligible) != 1:
        return False
    tr = eligible[0]
    eid = tr["entity_id"]
    # Preserve fields that carry meaning across the platform switch.
    # ``attribute`` is critical for entities like ``climate.thermostat``
    # whose primary state is a mode string ("heat"/"off"); the numeric
    # reading lives on ``current_temperature``. Dropping it would
    # silently make numeric_state evaluate the string state and never
    # fire. ``for:`` carries a debounce duration that applies to
    # numeric_state too.
    keep_for = tr.get("for")
    keep_attribute = tr.get("attribute")
    tr.clear()
    tr["trigger"] = "numeric_state"
    tr["entity_id"] = eid
    if keep_attribute is not None:
        tr["attribute"] = keep_attribute
    tr[direction] = value
    if keep_for is not None:
        tr["for"] = keep_for
    _commit_triggers(automation, triggers)
    return True


# Trigger-phrasing for a sun event: "at sunset", "every sunrise", "when
# the sun sets". Purely conditional phrasing ("…after sunset", "while
# it's dark") deliberately does NOT match — the sun word is then a guard
# on a separate trigger, not a fire-time, and synthesizing a sun trigger
# would make the automation fire unconditionally at sunset on top of
# whatever the user actually asked for.
_SUN_TRIGGER_LANG_RE = re.compile(
    r"\b(?:at|every|come|by|each|on)\s+(?:sunset|sundown|dusk|sunrise|sundawn|dawn)\b"
    r"|\bwhen\s+(?:the\s+)?sun\s+(?:sets?|rises?|comes?\s+up|goes?\s+down)\b",
    re.IGNORECASE,
)

# After a primary sun-trigger phrase, conjunctions ``and``/``or``/comma
# can chain bare event names that share the prefix. "at sunset and
# sunrise" requests both events; without this follow-on detector only
# the first event would be captured.
_SUN_TRIGGER_CONT_RE = re.compile(
    r"\s*(?:and|or|,)\s*"
    r"(?:sunset|sundown|dusk|sunrise|sundawn|dawn|"
    r"sun\s+sets?|sun\s+rises?|sun\s+comes?\s+up|sun\s+goes?\s+down)\b",
    re.IGNORECASE,
)

# Explicit clock-time phrase in the prompt (e.g. "at 10 PM", "at 22:00",
# "10 pm"). When the prompt contains BOTH a sun-trigger phrase AND an
# explicit time, the time trigger is intentional — append the sun
# trigger rather than replacing the time one.
_EXPLICIT_TIME_RE = re.compile(
    r"\b(?:\d{1,2}:\d{2}(?::\d{2})?|\d{1,2}\s*(?:am|pm|a\.m\.|p\.m\.))\b",
    re.IGNORECASE,
)


def _coerce_sun_triggers(automation: dict[str, Any], user_message: str) -> bool:
    """Ensure a sun trigger with the right event is present when the prompt
    *fires* on a sun event. Requires trigger phrasing ("at sunset",
    "every sunrise", "when the sun sets") — purely conditional phrases
    ("…after sunset") are ignored so motion+sun automations don't gain
    an unconditional fire-at-sunset trigger. Replaces a time-guess
    trigger (``time``/``time_pattern``) when one is present; otherwise
    appends. Mutates in place; returns True if anything changed."""
    if not user_message:
        return False
    msg = user_message.lower()
    # Collect every requested sun event in the prompt — "at sunrise and
    # at sunset" legitimately names both. Each match is independently
    # checked for trigger-vs-condition context using the nearest
    # preceding anchor:
    #
    # * If a trigger anchor (when/if/once/while/...) precedes THIS
    #   phrase without an intervening ``or``, the phrase is qualifying
    #   that earlier trigger as a condition (e.g. "if motion is
    #   detected at sunset", or the second clause of "at sunset turn
    #   light on, and turn it off if it is still on at sunrise"). Skip.
    # * Use the NEAREST preceding anchor so "when motion occurs or when
    #   the door opens at sunset" associates sunset with the second
    #   ``when`` clause rather than the first.
    wanted: set[str] = set()
    for match in _SUN_TRIGGER_LANG_RE.finditer(msg):
        head = msg[: match.start()]
        head_anchors = list(_TRIGGER_ANCHOR_RE.finditer(head))
        if head_anchors:
            anchor = head_anchors[-1]
            between = msg[anchor.end() : match.start()]
            if not re.search(r"\bor\b", between, re.IGNORECASE):
                continue
        phrase = match.group(0)
        # Greedily absorb any chained event names ("at sunset and
        # sunrise") so both events are recognised before the conflict
        # check downstream can delete one.
        end = match.end()
        while True:
            cont = _SUN_TRIGGER_CONT_RE.match(msg, end)
            if not cont:
                break
            phrase += msg[end : cont.end()]
            end = cont.end()
        if re.search(r"\b(sunset|sundown|dusk|sun\s+sets?|sun\s+goes?\s+down)\b", phrase):
            wanted.add("sunset")
        if re.search(r"\b(sunrise|sundawn|dawn|sun\s+rises?|sun\s+comes?\s+up)\b", phrase):
            wanted.add("sunrise")
    if not wanted:
        return False

    triggers = _read_triggers(automation)
    present_events: set[str] = set()
    conflict_indices: list[int] = []
    for idx, tr in enumerate(triggers):
        if not isinstance(tr, dict):
            continue
        platform = tr.get("trigger") or tr.get("platform")
        if platform != "sun":
            continue
        ev = tr.get("event")
        if ev in wanted:
            present_events.add(ev)
        else:
            conflict_indices.append(idx)
    missing = wanted - present_events

    # Already correct AND no contradicting sun trigger → no-op.
    if not missing and not conflict_indices:
        return False

    changed = False
    # Removing or repurposing an existing sun trigger is only safe in a
    # narrow case: the prompt does NOT use additive language ("also",
    # "too", "in addition"), no requested event is already present, AND
    # exactly one existing sun trigger disagrees. That pattern fits the
    # "model emitted the wrong sun event" case (single stale trigger
    # to correct). In any other shape — additive language present, an
    # existing requested event also present, or multiple conflicts —
    # leave existing sun triggers alone and only synthesize missing
    # events: the conflict is most likely a deliberate carry-over from
    # an earlier refinement.
    is_additive = re.search(r"\b(?:also|too|as\s+well|in\s+addition|and\s+also)\b", msg) is not None
    can_repurpose = (
        not is_additive and not present_events and len(conflict_indices) == 1 and bool(missing)
    )
    if can_repurpose:
        idx = conflict_indices[0]
        ev = sorted(missing)[0]
        missing.discard(ev)
        triggers[idx] = {"trigger": "sun", "event": ev}
        changed = True

    if missing:
        # Repurpose a guessed time/time_pattern trigger when the prompt
        # has no explicit clock time. Each repurpose satisfies one
        # missing event.
        prompt_has_explicit_time = _EXPLICIT_TIME_RE.search(msg) is not None
        if not prompt_has_explicit_time:
            for idx, tr in enumerate(triggers):
                if not missing:
                    break
                if not isinstance(tr, dict):
                    continue
                platform = tr.get("trigger") or tr.get("platform")
                if platform in ("time", "time_pattern"):
                    ev = sorted(missing)[0]
                    missing.discard(ev)
                    triggers[idx] = {"trigger": "sun", "event": ev}
                    changed = True
        # Append any still-missing requested events.
        for ev in sorted(missing):
            triggers.append({"trigger": "sun", "event": ev})
            changed = True

    if not changed:
        return False
    _commit_triggers(automation, triggers)
    return True


def _apply_prompt_aware_coercions(
    automation: dict[str, Any],
    user_message: str,
    hass: HomeAssistant,
) -> dict[str, Any] | None:
    """Apply prompt-aware trigger coercions and re-validate. Returns
    the normalized automation on success, None if no coercion was
    needed OR the coerced shape still fails validation."""
    if not isinstance(automation, dict):
        return None
    candidate = copy.deepcopy(automation)
    changed = False
    if _coerce_sun_triggers(candidate, user_message):
        changed = True
    if _coerce_numeric_state_triggers(candidate, user_message):
        changed = True
    if not changed:
        return None
    is_valid, _reason, normalized = validate_automation_payload(candidate, hass)
    if not is_valid or normalized is None:
        return None
    return normalized


def parse_architect_response(
    text: str,
    hass: HomeAssistant,
    entities: list[EntitySnapshot] | None = None,
    user_message: str | None = None,
) -> ArchitectResponse:
    """Parse the JSON response from the architect LLM. Normalises the result
    to always include 'intent' and 'response'; for 'automation' intent
    generates automation_yaml server-side. Strips
    ``suppressed_duplicate_command`` so a prompt-injected payload cannot
    bypass ``apply_command_policy``. When ``entities`` are provided an
    unknown-entity rejection is humanised + auto-corrected; when
    ``user_message`` is provided trigger coercions are applied."""
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            return {"intent": "answer", "response": text}

        data: dict[str, Any] = json.loads(text[start : end + 1])
        data.pop("suppressed_duplicate_command", None)

        if "intent" not in data:
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

            if not is_valid and reason and "unknown entity_id" in reason and entities:
                patched, subs = _resolve_unknown_entity_ids(
                    reason, data["automation"], entities, hass
                )
                if patched is not None and subs:
                    is_valid_retry, _r, normalized_retry = validate_automation_payload(
                        patched, hass
                    )
                    if is_valid_retry and normalized_retry is not None:
                        _LOGGER.info(
                            "Auto-corrected %d entity_id substitution(s) in automation: %s",
                            len(subs),
                            subs,
                        )
                        data["automation"] = patched
                        is_valid, reason, normalized = True, "", normalized_retry

            # Coerce wrong-shape triggers (state instead of numeric_state,
            # time instead of sun) when the prompt hints at the right shape;
            # re-validate and on success replace the original automation.
            if normalized is not None and user_message:
                coerced = _apply_prompt_aware_coercions(data["automation"], user_message, hass)
                if coerced is not None:
                    data["automation"] = coerced
                    normalized = coerced

            # Last-ditch trigger recovery — when validation rejected for a
            # missing/invalid trigger, synthesize it from the prompt.
            if not is_valid and reason and user_message and ("trigger" in reason.lower()):
                coerced = _apply_prompt_aware_coercions(data["automation"], user_message, hass)
                if coerced is not None:
                    _LOGGER.info(
                        "Recovered missing/invalid trigger from prompt for automation: %s", reason
                    )
                    data["automation"] = coerced
                    is_valid, reason, normalized = True, "", coerced

            if not is_valid or normalized is None:
                _LOGGER.warning("Discarding invalid architect automation payload: %s", reason)
                data.pop("automation", None)
                data.pop("automation_yaml", None)
                data["validation_error"] = reason
                data["validation_target"] = "automation"
                data["response"] = _humanise_unknown_entity_error(reason, entities)
                if data.get("intent") == "automation":
                    data["intent"] = "clarification" if "unknown entity_id" in reason else "answer"
            else:
                data["automation"] = normalized
                data["automation_yaml"] = yaml.dump(
                    normalized, default_flow_style=False, allow_unicode=True
                )
                data["risk_assessment"] = assess_automation_risk(normalized)

        _strip_markers_for_proposal_intents(data)
        return data

    except (
        json.JSONDecodeError,
        KeyError,
        ValueError,
    ):
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
    *,
    session_id: str | None = None,
    user_message: str | None = None,
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
        except (
            json.JSONDecodeError,
            ValueError,
        ):
            _LOGGER.warning("Failed to parse quick_actions block")
        text = text[: qa_match.start()] + text[qa_match.end() :]

    def _attach_qa(r: ArchitectResponse) -> ArchitectResponse:
        if quick_actions and r.get("intent") != "command_approval":
            r["quick_actions"] = quick_actions
        _strip_markers_for_proposal_intents(r)
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
            except (
                json.JSONDecodeError,
                ValueError,
            ):
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
                result = apply_command_policy(result, entities, hass=hass, session_id=session_id)
            return _attach_qa(result)
        except (
            json.JSONDecodeError,
            ValueError,
        ):
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
        except (
            json.JSONDecodeError,
            ValueError,
        ):
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
                result = apply_command_policy(result, entities, hass=hass, session_id=session_id)
            return _attach_qa(result)
        except (
            json.JSONDecodeError,
            ValueError,
        ):
            _LOGGER.warning("Failed to parse delayed_command block: %s", json_text[:200])

    # Check for scene fenced block. The prompt asks the model to put
    # the block at the end of the response, but some cloud models
    # violate that and emit trailing prose ("This scene will ensure
    # …"). Anchoring to end would drop the proposal entirely and the
    # chat would show only prose with no Accept & Save card. Match
    # the LAST ``` scene ``` block anywhere in the text instead — scene
    # proposals are always gated behind the Accept card, so a stray
    # example block can't auto-create.
    scene_match: re.Match[str] | None = None
    for m in re.finditer(r"```scene\s*\n?([\s\S]*?)```", text):
        scene_match = m
    if scene_match:
        from ..scene_utils import validate_scene_payload

        # Splice prose around the block so any trailing summary stays
        # in the bubble. Strip a single blank-line separator on either
        # side so the joined prose doesn't collapse into a double gap.
        prose_before = text[: scene_match.start()].rstrip()
        prose_after = text[scene_match.end() :].lstrip()
        response_text = "\n\n".join(p for p in (prose_before, prose_after) if p).strip()
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
        except (
            json.JSONDecodeError,
            ValueError,
        ):
            _LOGGER.warning("Failed to parse scene block: %s", json_text[:200])

    # Check for automation fenced block — must be the terminal block
    # so informational examples don't trigger real automation creation.
    auto_match = re.search(r"```automation\s*\n?([\s\S]*?)```\s*$", text)
    if auto_match:
        response_text = text[: auto_match.start()].strip()
        json_text = auto_match.group(1).strip()
        try:
            automation_data = json.loads(json_text)
            is_valid, reason, normalized = validate_automation_payload(automation_data, hass)
            # Auto-correct an unknown-entity rejection when the model
            # named a real device with the wrong domain prefix
            # (light.coffee_maker → switch.coffee_maker). Mirrors the
            # JSON-only path.
            if not is_valid and reason and "unknown entity_id" in reason and entities:
                patched, subs = _resolve_unknown_entity_ids(reason, automation_data, entities, hass)
                if patched is not None and subs:
                    is_valid_retry, _r, normalized_retry = validate_automation_payload(
                        patched, hass
                    )
                    if is_valid_retry and normalized_retry is not None:
                        _LOGGER.info(
                            "Auto-corrected %d entity_id substitution(s) "
                            "in streamed automation: %s",
                            len(subs),
                            subs,
                        )
                        automation_data = patched
                        is_valid, reason, normalized = (
                            True,
                            "",
                            normalized_retry,
                        )
            # Apply prompt-aware trigger coercions (state →
            # numeric_state, time → sun) when the model emitted a
            # valid-but-wrong trigger shape that downstream checks
            # reject. Mirrors the JSON-only path.
            if normalized is not None and user_message:
                coerced = _apply_prompt_aware_coercions(automation_data, user_message, hass)
                if coerced is not None:
                    automation_data = coerced
                    normalized = coerced
            # Last-ditch trigger recovery — when validation rejected for a
            # missing/invalid trigger, synthesize it from the prompt.
            # Mirrors the JSON-only path so a streamed "at sunset …"
            # request can repair a missing trigger instead of dead-ending.
            if not is_valid and reason and user_message and "trigger" in reason.lower():
                coerced = _apply_prompt_aware_coercions(automation_data, user_message, hass)
                if coerced is not None:
                    _LOGGER.info(
                        "Recovered missing/invalid trigger from prompt for streamed automation: %s",
                        reason,
                    )
                    automation_data = coerced
                    is_valid, reason, normalized = True, "", coerced
            if not is_valid or normalized is None:
                _LOGGER.warning("Discarding invalid streamed automation payload: %s", reason)
                # Always surface the device-listing clarification when
                # validation rejects — matches the JSON-mode path so a
                # user gets the same actionable list regardless of
                # transport. Any pre-block prose the LoRA emitted
                # described an automation that no longer exists.
                bubble = _humanise_unknown_entity_error(reason, entities)
                return _attach_qa(
                    {
                        "intent": ("clarification" if "unknown entity_id" in reason else "answer"),
                        "response": bubble,
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
        except (
            json.JSONDecodeError,
            ValueError,
        ):
            _LOGGER.warning("Failed to parse automation block: %s", json_text[:200])

    # No fenced block — try the old JSON-only parser. Pass entities
    # so an "unknown entity_id" rejection becomes a clarification
    # listing the user's actual devices instead of the bare reason.
    # Threading user_message lets parse_architect_response apply
    # the prompt-aware entity-substitution fallback (e.g. the LoRA
    # echoes ``lock.front_door`` from the prompt's EXAMPLES section
    # when the actual target is ``switch.coffee_maker`` — without
    # user_message the resolver can't find the prompt-named entity
    # and dead-ends as clarification). Same goes for the
    # prompt-aware trigger coercions (state → numeric_state, time
    # → sun).
    result = parse_architect_response(text, hass, entities, user_message=user_message)

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
        # Also accept the case where the LLM attempted execute_command
        # / validate_action with a service whose action verb appears in
        # the prose. The formal trust check requires ``executed:True``
        # entries; if a service errored or returned an unexpected shape
        # but the LLM clearly identified the entity (the tool referenced
        # it), the strict "no entity matched" stomp below would be
        # wrong. The narrower attempted-call test prevents the stomp
        # from misfiring without trusting prose for unrelated tools.
        attempted_match = _prose_describes_attempted_call(response_text, tool_log, entities)
        if (
            attempted_match
            or _prose_is_trusted_after_tool(response_text, executed_calls, entities)
            or (
                command_block_fully_stripped
                and executed_calls
                and not _response_names_unbacked_entity(response_text, executed_calls, entities)
            )
        ):
            result["suppressed_duplicate_command"] = True

    # Promote REVIEW-bucket tool attempts to a proper command_approval
    # proposal even when the LLM narrated the requires_approval result
    # back at the user instead of emitting a command JSON block. Without
    # this the chat shows "I can't unlock the door because it requires
    # approval" with no card — the user has no way to actually approve.
    # Also runs when the LLM directly emitted ``intent: "command_approval"``
    # (no tool_log needed) so we can mint a proposal_id and attach the
    # four sentinel quick-actions the chat UI needs.
    result = synthesize_approval_from_tool_log(result, tool_log, hass)

    # Apply command safety policy if entities are available.
    # Always run the policy — even when calls is empty — so that
    # command intents with no calls get downgraded to "answer".
    if entities is not None:
        result = apply_command_policy(result, entities, hass=hass, session_id=session_id)

    return _attach_qa(result)
