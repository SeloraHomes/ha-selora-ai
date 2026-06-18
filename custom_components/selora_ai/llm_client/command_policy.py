"""Command safety policy: validates and repairs LLM-proposed service calls.

Owns the allowlist of safe domains/services/parameters and the heuristics
that recover from common LLM mistakes (bogus service name, fake confirmation
without backing calls). Public entry point is ``apply_command_policy``.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import logging
import re
from typing import TYPE_CHECKING, Any
import uuid

from ..const import (
    APPROVAL_RISK_HIGH,
    APPROVAL_RISK_LOW,
    APPROVAL_RISK_MEDIUM,
    DOMAIN,
)
from ..types import ArchitectResponse, EntitySnapshot, ToolWriteResult

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..approval_store import ApprovalStore

_LOGGER = logging.getLogger(__name__)

# Concise narration shown once a command_approval card is up. Doubles as the
# non-empty sentinel the non-streaming tool loop returns on an approval
# short-circuit, so ``architect_chat`` doesn't mistake the held action for an
# LLM failure before ``synthesize_approval_from_tool_log`` builds the card.
APPROVAL_PENDING_HINT = "This request needs your approval before I run it."

# Localized variants of the pending-approval hint. The English entry is
# kept identical to the module-level constant so legacy callers (tests,
# any code path that compares against the sentinel) still match. Missing
# locales fall through to English via ``approval_pending_hint``.
_APPROVAL_PENDING_HINT_BY_LANG: dict[str, str] = {
    "en": APPROVAL_PENDING_HINT,
    "fr": "Cette requête nécessite votre approbation avant que je l'exécute.",
    "de": "Diese Anfrage erfordert Ihre Genehmigung, bevor ich sie ausführe.",
    "es": "Esta solicitud necesita su aprobación antes de que la ejecute.",
    "it": "Questa richiesta richiede la tua approvazione prima che la esegua.",
    "nl": "Dit verzoek vereist uw goedkeuring voordat ik het uitvoer.",
    "hu": "Ennek a kérésnek a végrehajtásához az Ön jóváhagyása szükséges.",
}


def approval_pending_hint(language: str | None = None) -> str:
    """Localized variant of ``APPROVAL_PENDING_HINT``.

    Returns the English sentinel when ``language`` is None / unknown so
    the existing test fixtures and any equality checks against the bare
    constant keep working.
    """
    base = (language or "en").lower().split("-")[0]
    return _APPROVAL_PENDING_HINT_BY_LANG.get(base, APPROVAL_PENDING_HINT)


def _all_targets_approved(
    approval_store: ApprovalStore | None,
    service: str,
    target_ids: list[str],
    session_id: str | None,
) -> bool:
    """True iff *every* targeted entity is approved for *service*.

    Per-entity lookup falls back to the wildcard inside ``is_approved``
    so a grant recorded as "lock.unlock for any lock" still covers each
    individual lock here. When the call has no entity target (notify.*,
    script.*, shell_command.*), only the wildcard is checked.

    A multi-entity call (e.g. ``light.turn_on`` on [light.a, light.b])
    requires BOTH ids to be approved — partial approval would silently
    skip the approval card for some of the targets.
    """
    if approval_store is None:
        return False
    if not target_ids:
        return approval_store.is_approved(service, session_id=session_id)
    return all(
        approval_store.is_approved(service, entity_id=eid, session_id=session_id)
        for eid in target_ids
    )


def _resolve_approval_store(
    hass: HomeAssistant | None,
    approval_store: ApprovalStore | None,
) -> ApprovalStore | None:
    """Return the ApprovalStore from explicit arg or hass.data, if any.

    Callers may pass either ``hass`` (let the policy look up the singleton)
    or ``approval_store`` directly (useful in tests). When both are absent,
    REVIEW calls fall through as if no standing approvals existed — the
    user still gets the approval card.
    """
    if approval_store is not None:
        return approval_store
    if hass is None:
        return None
    try:
        return hass.data.get(DOMAIN, {}).get("_approval_store")
    except AttributeError:
        return None


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


# ── REVIEW bucket: requires user approval before execution ──────────────
#
# Services here pass the same shape validation as SAFE services (must
# target an entity, data keys must be on the per-service whitelist) but
# instead of executing they surface a ``command_approval`` proposal in
# chat. The user picks Allow once / Session / Always / Deny. Risk level
# is the badge rendered on the approval card.
#
# Data-key policy for REVIEW services is permissive (``None``) when the
# service legitimately takes free-form payloads (e.g. ``tts.cloud_say``'s
# ``message`` can be any string). For services with a constrained
# parameter set, list the keys here just like the SAFE table.
_REVIEW_SERVICE_POLICIES: dict[str, dict[str, dict[str, Any]]] = {
    "tts": {
        # ``tts.<provider>_say`` is the legacy verb; we accept any service
        # under the tts domain via the catch-all ``"*"`` key below. The
        # target media_player must always be explicit — otherwise a single
        # approval click could broadcast across every speaker in the home.
        "*": {
            "risk": APPROVAL_RISK_LOW,
            "data": None,  # free-form: message, language, options, …
            "reason": "Speaks text aloud on a media player.",
            "requires_target": True,
        },
    },
    "notify": {
        # The notify channel is encoded in the service name itself
        # (``notify.mobile_app_<name>``); there is no entity_id to pin.
        "*": {
            "risk": APPROVAL_RISK_LOW,
            "data": None,  # message, title, target, data
            "reason": "Sends a notification to a user device.",
            "requires_target": False,
        },
    },
    "script": {
        # Scripts identify themselves by service name (``script.<id>``)
        # rather than entity_id targets.
        "*": {
            "risk": APPROVAL_RISK_MEDIUM,
            "data": None,
            "reason": "Runs a user-defined script with arbitrary side effects.",
            "requires_target": False,
        },
    },
    "shell_command": {
        "*": {
            "risk": APPROVAL_RISK_HIGH,
            "data": None,
            "reason": "Executes a shell command on the Home Assistant host.",
            "requires_target": False,
        },
    },
    # Physical / security entity domains: a missing entity_id would
    # broadcast across EVERY entity in the domain (every lock unlocked,
    # every vacuum started). The approval card always describes one
    # action, so the proposal must always carry an explicit target.
    "lock": {
        "lock": {
            "risk": APPROVAL_RISK_MEDIUM,
            "data": {"code"},
            "reason": "Engages a physical lock.",
            "requires_target": True,
        },
        "unlock": {
            "risk": APPROVAL_RISK_HIGH,
            "data": {"code"},
            "reason": "Releases a physical lock — physical access risk.",
            "requires_target": True,
        },
        "open": {
            "risk": APPROVAL_RISK_HIGH,
            "data": {"code"},
            "reason": "Opens a latch — physical access risk.",
            "requires_target": True,
        },
    },
    "alarm_control_panel": {
        "alarm_arm_home": {
            "risk": APPROVAL_RISK_MEDIUM,
            "data": {"code"},
            "reason": "Arms the alarm in home mode.",
            "requires_target": True,
        },
        "alarm_arm_away": {
            "risk": APPROVAL_RISK_MEDIUM,
            "data": {"code"},
            "reason": "Arms the alarm in away mode.",
            "requires_target": True,
        },
        "alarm_arm_night": {
            "risk": APPROVAL_RISK_MEDIUM,
            "data": {"code"},
            "reason": "Arms the alarm in night mode.",
            "requires_target": True,
        },
        "alarm_disarm": {
            "risk": APPROVAL_RISK_HIGH,
            "data": {"code"},
            "reason": "Disarms the alarm — security risk.",
            "requires_target": True,
        },
    },
    "vacuum": {
        "start": {
            "risk": APPROVAL_RISK_LOW,
            "data": None,
            "reason": "Starts a vacuum.",
            "requires_target": True,
        },
        "pause": {
            "risk": APPROVAL_RISK_LOW,
            "data": None,
            "reason": "Pauses a vacuum.",
            "requires_target": True,
        },
        "stop": {
            "risk": APPROVAL_RISK_LOW,
            "data": None,
            "reason": "Stops a vacuum.",
            "requires_target": True,
        },
        "return_to_base": {
            "risk": APPROVAL_RISK_LOW,
            "data": None,
            "reason": "Sends a vacuum back to its dock.",
            "requires_target": True,
        },
        "clean_spot": {
            "risk": APPROVAL_RISK_LOW,
            "data": None,
            "reason": "Starts a spot clean.",
            "requires_target": True,
        },
    },
    "water_heater": {
        "set_temperature": {
            "risk": APPROVAL_RISK_MEDIUM,
            "data": {"temperature", "operation_mode"},
            "reason": "Changes water heater temperature — energy/scald risk.",
            "requires_target": True,
        },
        "set_operation_mode": {
            "risk": APPROVAL_RISK_MEDIUM,
            "data": {"operation_mode"},
            "reason": "Switches water heater mode.",
            "requires_target": True,
        },
    },
}


# ── BLOCKED bucket: hard-denied even with user approval ─────────────────
#
# These cannot be unlocked from chat — they require the user to use
# Developer Tools (or the equivalent admin UI) directly. The rationale
# is irreversibility, data loss, or full-host compromise: the cost of a
# stray approval click is too high.
_BLOCKED_SERVICES: frozenset[str] = frozenset(
    {
        "homeassistant.restart",
        "homeassistant.stop",
        "homeassistant.check_config",
        "homeassistant.update_entity",
        "recorder.purge",
        "recorder.purge_entities",
        "hassio.host_reboot",
        "hassio.host_shutdown",
        "hassio.supervisor_update",
        "hassio.addon_uninstall",
        "persistent_notification.dismiss_all",
        "automation.reload",
        "scene.reload",
        "script.reload",
        # Caller-supplied raw service calls bypass the policy by design;
        # the LLM must never reach for the python_script escape hatch
        # from chat.
        "python_script.exec",
    }
)


# ── Entity-class elevation: SAFE → REVIEW based on device_class ─────────
#
# ``cover.open_cover`` on a venetian blind is harmless; the same service
# on a garage door grants physical access to the home. We can't tell
# them apart by service name alone — the only signal is the entity's
# ``device_class``. Cover services that target one of these classes are
# elevated to REVIEW with the matching risk level, while the same
# services targeting blinds / awnings / shades stay SAFE.
_HIGH_RISK_COVER_CLASSES: frozenset[str] = frozenset({"garage", "door", "gate"})

_HIGH_RISK_COVER_SERVICES: dict[str, dict[str, Any]] = {
    "cover.open_cover": {
        "risk": APPROVAL_RISK_HIGH,
        "data": set(),
        "reason": "Opens a garage door / gate / door — physical access risk.",
        "requires_target": True,
    },
    "cover.toggle": {
        "risk": APPROVAL_RISK_HIGH,
        "data": set(),
        "reason": "Toggles a garage door / gate / door — may grant physical access.",
        "requires_target": True,
    },
    "cover.set_cover_position": {
        "risk": APPROVAL_RISK_MEDIUM,
        "data": {"position"},
        "reason": "Changes the position of a garage door / gate / door.",
        "requires_target": True,
    },
}


def _entity_aware_review_entry(
    hass: HomeAssistant | None,
    service: str,
    entity_ids: list[str],
) -> dict[str, Any] | None:
    """Return a REVIEW policy entry when the call targets a high-risk
    cover (garage / door / gate). None means the call stays in its
    static bucket — most covers (blinds, awnings, shutters, curtains)
    are low-stakes and execute without approval.
    """
    if hass is None or service not in _HIGH_RISK_COVER_SERVICES:
        return None
    for eid in entity_ids:
        state = hass.states.get(eid)
        if state is None:
            continue
        device_class = state.attributes.get("device_class")
        if device_class in _HIGH_RISK_COVER_CLASSES:
            return _HIGH_RISK_COVER_SERVICES[service]
    return None


def _classify_call(
    service: str,
) -> tuple[str, dict[str, Any] | None]:
    """Return ``("safe" | "review" | "blocked", policy_entry | None)``.

    For REVIEW services, ``policy_entry`` is the matched dict containing
    ``risk``, ``data`` (allowed keys or None for free-form), and
    ``reason``. For SAFE / BLOCKED it's None.
    """
    if not service or "." not in service:
        return ("blocked", None)
    if service in _BLOCKED_SERVICES:
        return ("blocked", None)
    domain, service_name = service.split(".", 1)
    if domain in _ALLOWED_COMMAND_SERVICES:
        return ("safe", None)
    review_domain = _REVIEW_SERVICE_POLICIES.get(domain)
    if review_domain is None:
        return ("blocked", None)
    entry = review_domain.get(service_name) or review_domain.get("*")
    if entry is None:
        return ("blocked", None)
    return ("review", entry)


def _max_risk(levels: list[str]) -> str:
    """Pick the highest risk in *levels*. Higher beats lower."""
    order = {APPROVAL_RISK_LOW: 0, APPROVAL_RISK_MEDIUM: 1, APPROVAL_RISK_HIGH: 2}
    return max(levels, key=lambda lvl: order.get(lvl, 0))


def call_required_approval(
    hass: HomeAssistant | None,
    call: dict[str, Any],
) -> bool:
    """True iff *call* would have hit the approval gate.

    A proposal can bundle SAFE-bucket calls alongside REVIEW ones (the
    policy holds the whole turn until the user clicks through). Those
    SAFE calls don't need a persistent grant — and granting them would
    be unsafe: a future ``cover.open_cover`` on a garage door would
    look up its standing grant before reaching the device_class
    elevation check, silently skipping the new prompt the user
    expects to see.

    Returns True for:
    - Services in the static REVIEW bucket (lock.*, alarm_*, tts.*, …).
    - SAFE-bucket services elevated by entity device_class
      (cover.open_cover on a garage / gate / front door).
    Returns False for pure SAFE and BLOCKED services.
    """
    service = str(call.get("service", "")).strip()
    if not service:
        return False
    bucket, _ = _classify_call(service)
    if bucket == "review":
        return True
    if bucket != "safe":
        return False
    target = call.get("target") if isinstance(call.get("target"), dict) else {}
    raw = target.get("entity_id") if isinstance(target, dict) else None
    if isinstance(raw, str):
        ids = [raw] if raw else []
    elif isinstance(raw, list):
        ids = [eid for eid in raw if isinstance(eid, str)]
    else:
        ids = []
    return _entity_aware_review_entry(hass, service, ids) is not None


def validate_command_action(
    service: str,
    entity_id: str | list[str],
    data: dict[str, Any] | None = None,
    *,
    known_entity_ids: set[str] | None = None,
    hass: HomeAssistant | None = None,
    approval_store: ApprovalStore | None = None,
    session_id: str | None = None,
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
    approval_store = _resolve_approval_store(hass, approval_store)
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

    # Normalise target entity ids up-front so the REVIEW / elevated-SAFE
    # branches can pass them to ``is_approved`` for per-entity grant
    # resolution. The SAFE-path checks below re-validate shape from
    # the original parameter, so this early normalisation is
    # advisory — invalid shapes still trip the regular error path.
    if isinstance(entity_id, str):
        _approval_targets = [entity_id] if entity_id else []
    elif isinstance(entity_id, list):
        _approval_targets = [eid for eid in entity_id if isinstance(eid, str)]
    else:
        _approval_targets = []

    domain, service_name = service.split(".", 1)
    if domain not in _ALLOWED_COMMAND_SERVICES:
        # Service may still be eligible via the REVIEW bucket (with or
        # without a standing approval). The tool path needs to know
        # whether to ask for user approval rather than emit a hard
        # rejection — without this branch the LLM would tell the user
        # the request was refused even when the user could just tap
        # "Allow once".
        bucket, entry = _classify_call(service)
        if bucket == "review" and entry is not None:
            # Shape-validate BEFORE deciding requires_approval. Without
            # this, the tool path would let the LLM smuggle a malformed
            # payload (too many entity_ids, unsupported data keys,
            # off-domain target) through the approval gate — the user
            # would click Allow and the unvalidated payload would
            # execute via hass.services.async_call. We mirror the same
            # checks that ``apply_command_policy`` enforces on the JSON
            # path so the two paths agree on what's executable.
            synthetic_call: dict[str, Any] = {"service": service}
            if isinstance(entity_id, str):
                synthetic_call["target"] = {"entity_id": [entity_id]} if entity_id else {}
            elif isinstance(entity_id, list):
                synthetic_call["target"] = {"entity_id": entity_id}
            if data is not None:
                synthetic_call["data"] = data
            _validated, shape_err = _validate_review_call(synthetic_call, entry)
            if shape_err is not None:
                return {
                    "valid": False,
                    "errors": [shape_err],
                    "service": service,
                    "domain": domain,
                    "allowed_data_keys": (
                        sorted(entry["data"]) if isinstance(entry.get("data"), set) else []
                    ),
                    "requires_approval": False,
                    "risk_level": entry.get("risk"),
                    "approval_reason": entry.get("reason"),
                }
            already_approved = _all_targets_approved(
                approval_store, service, _approval_targets, session_id
            )
            return {
                "valid": already_approved,
                "errors": (
                    []
                    if already_approved
                    else [f"'{service}' requires user approval before it can run"]
                ),
                "service": service,
                "domain": domain,
                "allowed_data_keys": (
                    sorted(entry["data"]) if isinstance(entry.get("data"), set) else []
                ),
                "requires_approval": not already_approved,
                "risk_level": entry.get("risk"),
                "approval_reason": entry.get("reason"),
            }
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

    # Entity-class elevation: a SAFE-domain call (cover.*) can still
    # need approval when its target's device_class is high-risk (a
    # garage door / gate / front door). Same trust model as
    # lock.unlock — once shape validation passes, divert to the
    # REVIEW return shape so the tool path raises requires_approval.
    if not errors:
        review_entry = _entity_aware_review_entry(hass, service, target_ids)
        if review_entry is not None:
            already_approved = _all_targets_approved(
                approval_store, service, _approval_targets, session_id
            )
            return {
                "valid": already_approved,
                "errors": (
                    []
                    if already_approved
                    else [f"'{service}' requires user approval before it can run"]
                ),
                "service": service,
                "domain": domain,
                "allowed_services": allowed_services,
                "allowed_data_keys": (
                    sorted(review_entry["data"])
                    if isinstance(review_entry.get("data"), set)
                    else allowed_data_keys
                ),
                "requires_approval": not already_approved,
                "risk_level": review_entry.get("risk"),
                "approval_reason": review_entry.get("reason"),
            }

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
    # ``unlock`` must precede ``lock`` — "unlocking" contains "lock".
    "lock": [
        (re.compile(r"\bunlock(?:ing|ed)?\b", re.I), "unlock"),
        (re.compile(r"\block(?:ing|ed)?\b", re.I), "lock"),
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


# When the prose names a *known* entity via a ``[[entity:…]]`` marker, the
# device clearly matched — the model just narrated a confirmation without
# emitting the command block / calls array. "No entity matched" would be a
# lie in that case, so use an accurate retry message that nudges the user to
# re-issue the request (which routes to real execution or an approval card).
_UNBACKED_KNOWN_ENTITY_RESPONSE = (
    "I didn't actually run that — the command wasn't issued on my end. "
    "Please ask again so I can perform it."
)


# Pulls entity_ids out of ``[[entity:lock.front_door|Front Door]]`` and
# ``[[entities:lock.a,lock.b]]`` markers. Captures up to the ``|`` label
# separator or the closing bracket; tolerates the unclosed mid-stream shape.
_MARKER_ENTITY_ID_RE = re.compile(r"\[\[(?:entity|entities):([^\]|]+)", re.IGNORECASE)


def _marker_entity_ids(response: str) -> list[str]:
    """Extract entity_ids embedded in tile markers within *response*."""
    if not isinstance(response, str):
        return []
    ids: list[str] = []
    for chunk in _MARKER_ENTITY_ID_RE.findall(response):
        for token in chunk.split(","):
            token = token.strip()
            if "." in token:
                ids.append(token)
    return ids


def _unbacked_action_response(response: str, entities: list[EntitySnapshot]) -> tuple[str, str]:
    """Return the ``(message, validation_error)`` for an unbacked action claim.

    If the prose references a known entity through a tile marker, the entity
    matched and the model simply omitted the command — return the retry
    message. Otherwise fall back to the "no entity matched" wording.
    """
    known = {e.get("entity_id", "") for e in entities if e.get("entity_id")}
    if known and any(eid in known for eid in _marker_entity_ids(response)):
        return _UNBACKED_KNOWN_ENTITY_RESPONSE, "command_not_emitted"
    return _UNBACKED_ACTION_RESPONSE, "no_matching_entity_for_command"


def _recover_command_from_unbacked_prose(
    result: ArchitectResponse, entities: list[EntitySnapshot]
) -> dict[str, Any] | None:
    """Rebuild a service call when the model narrated an action but emitted
    no command block / tool call.

    The model sometimes replies with bare prose like "Unlocking the front
    door now." plus an ``[[entity:lock.front_door]]`` tile and no ``calls``
    array. The entity_id is unambiguous (it came from the system, not a
    fuzzy match) and the verb is readable from the prose, so we can
    reconstruct the intended call and route it through the normal approval /
    execution path instead of telling the user to try again.

    Returns a ``{service, target}`` call dict, or None when recovery isn't
    safe: no/multiple marker entities, an unknown entity, or an
    un-inferable verb. A single known entity is required so we never guess
    which device a multi-target narration meant.
    """
    response = result.get("response", "")
    if not _looks_like_unbacked_action(response):
        return None
    marker_ids = _marker_entity_ids(response)
    if len(marker_ids) != 1:
        return None
    entity_id = marker_ids[0]
    known = {e.get("entity_id", "") for e in entities if e.get("entity_id")}
    if entity_id not in known or "." not in entity_id:
        return None
    domain = entity_id.split(".", 1)[0]
    service = _repair_service_name(f"{domain}.", response)
    if service is None:
        return None
    return {"service": service, "target": {"entity_id": entity_id}}


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
    except (
        TypeError,
        ValueError,
    ):
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
            # Targetless REVIEW writes (notify.mobile_app_*, script.*,
            # shell_command.*) legitimately have no entity_ids. Keep them
            # so the duplicate guard can strip an echoed command block —
            # the data payload (below) is what makes two of them distinct.
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
    # An empty id set is valid for targetless REVIEW writes
    # (notify.*, script.*, shell_command.*); the data payload keeps
    # distinct calls apart, so still emit a signature for dedup.
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


# Past-tense verbs used by the tool-loop short-circuit and the
# approval result message. Mirrors ``action-format.js`` on the
# frontend — if you add a service here, add it there too so live
# and reload views match.
_PAST_VERBS_BY_SERVICE: dict[str, str] = {
    "lock.lock": "Locked",
    "lock.unlock": "Unlocked",
    "lock.open": "Opened",
    "tts.cloud_say": "Announced on",
    "tts.google_translate_say": "Announced on",
    "tts.speak": "Announced on",
    "alarm_control_panel.alarm_arm_home": "Armed (home mode)",
    "alarm_control_panel.alarm_arm_away": "Armed (away mode)",
    "alarm_control_panel.alarm_arm_night": "Armed (night mode)",
    "alarm_control_panel.alarm_disarm": "Disarmed",
    "vacuum.start": "Started",
    "vacuum.pause": "Paused",
    "vacuum.stop": "Stopped",
    "vacuum.return_to_base": "Sent to dock",
    "vacuum.clean_spot": "Spot-cleaned with",
    "water_heater.set_temperature": "Updated temperature on",
    "water_heater.set_operation_mode": "Changed mode on",
    "light.turn_on": "Turned on",
    "light.turn_off": "Turned off",
    "light.toggle": "Toggled",
    "switch.turn_on": "Turned on",
    "switch.turn_off": "Turned off",
    "scene.turn_on": "Activated",
    "cover.open_cover": "Opened",
    "cover.close_cover": "Closed",
    "cover.toggle": "Toggled",
    "cover.stop_cover": "Stopped",
    "cover.set_cover_position": "Repositioned",
}

_PAST_VERBS_BY_DOMAIN: dict[str, str] = {
    "tts": "Announced on",
    "notify": "Sent a notification via",
    "script": "Ran script",
    "shell_command": "Ran shell command",
}

# Locale overrides. Only keys translated here override the EN tables;
# anything missing falls back to English (acceptable for rarely-seen
# services). Keep this in sync with the EN dicts above when adding
# entries; missing keys = English passthrough, not an error.
_PAST_VERBS_BY_SERVICE_BY_LANG: dict[str, dict[str, str]] = {
    "fr": {
        "lock.lock": "Verrouillé",
        "lock.unlock": "Déverrouillé",
        "lock.open": "Ouvert",
        "tts.cloud_say": "Annoncé sur",
        "tts.google_translate_say": "Annoncé sur",
        "tts.speak": "Annoncé sur",
        "alarm_control_panel.alarm_arm_home": "Armé (mode maison)",
        "alarm_control_panel.alarm_arm_away": "Armé (mode absent)",
        "alarm_control_panel.alarm_arm_night": "Armé (mode nuit)",
        "alarm_control_panel.alarm_disarm": "Désarmé",
        "vacuum.start": "Démarré",
        "vacuum.pause": "Mis en pause",
        "vacuum.stop": "Arrêté",
        "vacuum.return_to_base": "Renvoyé à la base",
        "vacuum.clean_spot": "Nettoyé localement avec",
        "water_heater.set_temperature": "Température mise à jour sur",
        "water_heater.set_operation_mode": "Mode changé sur",
        "light.turn_on": "Allumé",
        "light.turn_off": "Éteint",
        "light.toggle": "Basculé",
        "switch.turn_on": "Allumé",
        "switch.turn_off": "Éteint",
        "scene.turn_on": "Activée",
        "cover.open_cover": "Ouvert",
        "cover.close_cover": "Fermé",
        "cover.toggle": "Basculé",
        "cover.stop_cover": "Arrêté",
        "cover.set_cover_position": "Repositionné",
    },
    "de": {
        "lock.lock": "Verriegelt",
        "lock.unlock": "Entriegelt",
        "lock.open": "Geöffnet",
        "tts.cloud_say": "Angekündigt auf",
        "tts.google_translate_say": "Angekündigt auf",
        "tts.speak": "Angekündigt auf",
        "alarm_control_panel.alarm_arm_home": "Aktiviert (Modus zu Hause)",
        "alarm_control_panel.alarm_arm_away": "Aktiviert (Modus abwesend)",
        "alarm_control_panel.alarm_arm_night": "Aktiviert (Nachtmodus)",
        "alarm_control_panel.alarm_disarm": "Deaktiviert",
        "vacuum.start": "Gestartet",
        "vacuum.pause": "Pausiert",
        "vacuum.stop": "Gestoppt",
        "vacuum.return_to_base": "Zurück zur Basis geschickt",
        "vacuum.clean_spot": "Punktuell gereinigt mit",
        "water_heater.set_temperature": "Temperatur aktualisiert auf",
        "water_heater.set_operation_mode": "Modus geändert auf",
        "light.turn_on": "Eingeschaltet",
        "light.turn_off": "Ausgeschaltet",
        "light.toggle": "Umgeschaltet",
        "switch.turn_on": "Eingeschaltet",
        "switch.turn_off": "Ausgeschaltet",
        "scene.turn_on": "Aktiviert",
        "cover.open_cover": "Geöffnet",
        "cover.close_cover": "Geschlossen",
        "cover.toggle": "Umgeschaltet",
        "cover.stop_cover": "Gestoppt",
        "cover.set_cover_position": "Neu positioniert",
    },
    "es": {
        "lock.lock": "Bloqueado",
        "lock.unlock": "Desbloqueado",
        "lock.open": "Abierto",
        "tts.cloud_say": "Anunciado en",
        "tts.google_translate_say": "Anunciado en",
        "tts.speak": "Anunciado en",
        "alarm_control_panel.alarm_arm_home": "Armado (modo casa)",
        "alarm_control_panel.alarm_arm_away": "Armado (modo fuera)",
        "alarm_control_panel.alarm_arm_night": "Armado (modo noche)",
        "alarm_control_panel.alarm_disarm": "Desarmado",
        "vacuum.start": "Iniciado",
        "vacuum.pause": "Pausado",
        "vacuum.stop": "Detenido",
        "vacuum.return_to_base": "Enviado a la base",
        "vacuum.clean_spot": "Limpiado localmente con",
        "water_heater.set_temperature": "Temperatura actualizada en",
        "water_heater.set_operation_mode": "Modo cambiado en",
        "light.turn_on": "Encendido",
        "light.turn_off": "Apagado",
        "light.toggle": "Alternado",
        "switch.turn_on": "Encendido",
        "switch.turn_off": "Apagado",
        "scene.turn_on": "Activada",
        "cover.open_cover": "Abierto",
        "cover.close_cover": "Cerrado",
        "cover.toggle": "Alternado",
        "cover.stop_cover": "Detenido",
        "cover.set_cover_position": "Reposicionado",
    },
    "it": {
        "lock.lock": "Bloccato",
        "lock.unlock": "Sbloccato",
        "lock.open": "Aperto",
        "tts.cloud_say": "Annunciato su",
        "tts.google_translate_say": "Annunciato su",
        "tts.speak": "Annunciato su",
        "alarm_control_panel.alarm_arm_home": "Inserito (modalità casa)",
        "alarm_control_panel.alarm_arm_away": "Inserito (modalità fuori)",
        "alarm_control_panel.alarm_arm_night": "Inserito (modalità notte)",
        "alarm_control_panel.alarm_disarm": "Disinserito",
        "vacuum.start": "Avviato",
        "vacuum.pause": "In pausa",
        "vacuum.stop": "Fermato",
        "vacuum.return_to_base": "Rinviato alla base",
        "vacuum.clean_spot": "Pulito localmente con",
        "water_heater.set_temperature": "Temperatura aggiornata su",
        "water_heater.set_operation_mode": "Modalità cambiata su",
        "light.turn_on": "Acceso",
        "light.turn_off": "Spento",
        "light.toggle": "Commutato",
        "switch.turn_on": "Acceso",
        "switch.turn_off": "Spento",
        "scene.turn_on": "Attivata",
        "cover.open_cover": "Aperto",
        "cover.close_cover": "Chiuso",
        "cover.toggle": "Commutato",
        "cover.stop_cover": "Fermato",
        "cover.set_cover_position": "Riposizionato",
    },
    "nl": {
        "lock.lock": "Vergrendeld",
        "lock.unlock": "Ontgrendeld",
        "lock.open": "Geopend",
        "tts.cloud_say": "Aangekondigd op",
        "tts.google_translate_say": "Aangekondigd op",
        "tts.speak": "Aangekondigd op",
        "alarm_control_panel.alarm_arm_home": "Ingeschakeld (thuis-modus)",
        "alarm_control_panel.alarm_arm_away": "Ingeschakeld (afwezig-modus)",
        "alarm_control_panel.alarm_arm_night": "Ingeschakeld (nachtmodus)",
        "alarm_control_panel.alarm_disarm": "Uitgeschakeld",
        "vacuum.start": "Gestart",
        "vacuum.pause": "Gepauzeerd",
        "vacuum.stop": "Gestopt",
        "vacuum.return_to_base": "Naar dock gestuurd",
        "vacuum.clean_spot": "Lokaal gereinigd met",
        "water_heater.set_temperature": "Temperatuur bijgewerkt voor",
        "water_heater.set_operation_mode": "Modus gewijzigd voor",
        "light.turn_on": "Aangezet",
        "light.turn_off": "Uitgezet",
        "light.toggle": "Omgeschakeld",
        "switch.turn_on": "Aangezet",
        "switch.turn_off": "Uitgezet",
        "scene.turn_on": "Geactiveerd",
        "cover.open_cover": "Geopend",
        "cover.close_cover": "Gesloten",
        "cover.toggle": "Omgeschakeld",
        "cover.stop_cover": "Gestopt",
        "cover.set_cover_position": "Herpositioneerd",
    },
    "hu": {
        "lock.lock": "Zárolva",
        "lock.unlock": "Feloldva",
        "lock.open": "Megnyitva",
        "tts.cloud_say": "Bejelentve",
        "tts.google_translate_say": "Bejelentve",
        "tts.speak": "Bejelentve",
        "alarm_control_panel.alarm_arm_home": "Élesítve (otthon mód)",
        "alarm_control_panel.alarm_arm_away": "Élesítve (távol mód)",
        "alarm_control_panel.alarm_arm_night": "Élesítve (éjszakai mód)",
        "alarm_control_panel.alarm_disarm": "Hatástalanítva",
        "vacuum.start": "Elindítva",
        "vacuum.pause": "Szüneteltetve",
        "vacuum.stop": "Megállítva",
        "vacuum.return_to_base": "Bázisra küldve",
        "vacuum.clean_spot": "Foltot tisztítva ezzel",
        "water_heater.set_temperature": "Hőmérséklet frissítve ezen",
        "water_heater.set_operation_mode": "Mód módosítva ezen",
        "light.turn_on": "Bekapcsolva",
        "light.turn_off": "Kikapcsolva",
        "light.toggle": "Átkapcsolva",
        "switch.turn_on": "Bekapcsolva",
        "switch.turn_off": "Kikapcsolva",
        "scene.turn_on": "Aktiválva",
        "cover.open_cover": "Megnyitva",
        "cover.close_cover": "Bezárva",
        "cover.toggle": "Átkapcsolva",
        "cover.stop_cover": "Megállítva",
        "cover.set_cover_position": "Áthelyezve",
    },
    "zh": {
        "lock.lock": "已锁定",
        "lock.unlock": "已解锁",
        "lock.open": "已打开",
        "tts.cloud_say": "已播报至",
        "tts.google_translate_say": "已播报至",
        "tts.speak": "已播报至",
        "alarm_control_panel.alarm_arm_home": "已布防（在家模式）",
        "alarm_control_panel.alarm_arm_away": "已布防（离家模式）",
        "alarm_control_panel.alarm_arm_night": "已布防（夜间模式）",
        "alarm_control_panel.alarm_disarm": "已撤防",
        "vacuum.start": "已启动",
        "vacuum.pause": "已暂停",
        "vacuum.stop": "已停止",
        "vacuum.return_to_base": "已返回基座",
        "vacuum.clean_spot": "已定点清扫",
        "water_heater.set_temperature": "已更新温度",
        "water_heater.set_operation_mode": "已切换模式",
        "light.turn_on": "已开启",
        "light.turn_off": "已关闭",
        "light.toggle": "已切换",
        "switch.turn_on": "已开启",
        "switch.turn_off": "已关闭",
        "scene.turn_on": "已激活",
        "cover.open_cover": "已打开",
        "cover.close_cover": "已关闭",
        "cover.toggle": "已切换",
        "cover.stop_cover": "已停止",
        "cover.set_cover_position": "已调整位置",
    },
    "pt": {
        "lock.lock": "Trancado",
        "lock.unlock": "Destrancado",
        "lock.open": "Aberto",
        "tts.cloud_say": "Anunciado em",
        "tts.google_translate_say": "Anunciado em",
        "tts.speak": "Anunciado em",
        "alarm_control_panel.alarm_arm_home": "Armado (modo em casa)",
        "alarm_control_panel.alarm_arm_away": "Armado (modo ausente)",
        "alarm_control_panel.alarm_arm_night": "Armado (modo noturno)",
        "alarm_control_panel.alarm_disarm": "Desarmado",
        "vacuum.start": "Iniciado",
        "vacuum.pause": "Em pausa",
        "vacuum.stop": "Parado",
        "vacuum.return_to_base": "Enviado para a base",
        "vacuum.clean_spot": "Limpeza localizada com",
        "water_heater.set_temperature": "Temperatura atualizada em",
        "water_heater.set_operation_mode": "Modo alterado em",
        "light.turn_on": "Ligado",
        "light.turn_off": "Desligado",
        "light.toggle": "Alternado",
        "switch.turn_on": "Ligado",
        "switch.turn_off": "Desligado",
        "scene.turn_on": "Ativada",
        "cover.open_cover": "Aberto",
        "cover.close_cover": "Fechado",
        "cover.toggle": "Alternado",
        "cover.stop_cover": "Parado",
        "cover.set_cover_position": "Reposicionado",
    },
    "ja": {
        "lock.lock": "施錠しました",
        "lock.unlock": "解錠しました",
        "lock.open": "開けました",
        "tts.cloud_say": "アナウンスしました",
        "tts.google_translate_say": "アナウンスしました",
        "tts.speak": "アナウンスしました",
        "alarm_control_panel.alarm_arm_home": "警戒オン（在宅モード）",
        "alarm_control_panel.alarm_arm_away": "警戒オン（外出モード）",
        "alarm_control_panel.alarm_arm_night": "警戒オン（夜間モード）",
        "alarm_control_panel.alarm_disarm": "解除しました",
        "vacuum.start": "開始しました",
        "vacuum.pause": "一時停止しました",
        "vacuum.stop": "停止しました",
        "vacuum.return_to_base": "ベースに戻しました",
        "vacuum.clean_spot": "スポット清掃しました",
        "water_heater.set_temperature": "温度を更新しました",
        "water_heater.set_operation_mode": "モードを変更しました",
        "light.turn_on": "オンにしました",
        "light.turn_off": "オフにしました",
        "light.toggle": "切り替えました",
        "switch.turn_on": "オンにしました",
        "switch.turn_off": "オフにしました",
        "scene.turn_on": "有効にしました",
        "cover.open_cover": "開けました",
        "cover.close_cover": "閉めました",
        "cover.toggle": "切り替えました",
        "cover.stop_cover": "停止しました",
        "cover.set_cover_position": "位置を調整しました",
    },
    "ko": {
        "lock.lock": "잠갔습니다",
        "lock.unlock": "잠금 해제했습니다",
        "lock.open": "열었습니다",
        "tts.cloud_say": "안내방송했습니다",
        "tts.google_translate_say": "안내방송했습니다",
        "tts.speak": "안내방송했습니다",
        "alarm_control_panel.alarm_arm_home": "경비 설정함(재실 모드)",
        "alarm_control_panel.alarm_arm_away": "경비 설정함(외출 모드)",
        "alarm_control_panel.alarm_arm_night": "경비 설정함(야간 모드)",
        "alarm_control_panel.alarm_disarm": "경비 해제했습니다",
        "vacuum.start": "시작했습니다",
        "vacuum.pause": "일시정지했습니다",
        "vacuum.stop": "정지했습니다",
        "vacuum.return_to_base": "충전대로 복귀시켰습니다",
        "vacuum.clean_spot": "집중 청소했습니다",
        "water_heater.set_temperature": "온도를 변경했습니다",
        "water_heater.set_operation_mode": "모드를 변경했습니다",
        "light.turn_on": "켰습니다",
        "light.turn_off": "껐습니다",
        "light.toggle": "전환했습니다",
        "switch.turn_on": "켰습니다",
        "switch.turn_off": "껐습니다",
        "scene.turn_on": "활성화했습니다",
        "cover.open_cover": "열었습니다",
        "cover.close_cover": "닫았습니다",
        "cover.toggle": "전환했습니다",
        "cover.stop_cover": "정지했습니다",
        "cover.set_cover_position": "위치를 조정했습니다",
    },
    "ru": {
        "lock.lock": "Заблокировано",
        "lock.unlock": "Разблокировано",
        "lock.open": "Открыто",
        "tts.cloud_say": "Объявлено на",
        "tts.google_translate_say": "Объявлено на",
        "tts.speak": "Объявлено на",
        "alarm_control_panel.alarm_arm_home": "Поставлено на охрану (режим «дома»)",
        "alarm_control_panel.alarm_arm_away": "Поставлено на охрану (режим «не дома»)",
        "alarm_control_panel.alarm_arm_night": "Поставлено на охрану (ночной режим)",
        "alarm_control_panel.alarm_disarm": "Снято с охраны",
        "vacuum.start": "Запущено",
        "vacuum.pause": "Приостановлено",
        "vacuum.stop": "Остановлено",
        "vacuum.return_to_base": "Отправлено на базу",
        "vacuum.clean_spot": "Локальная уборка с",
        "water_heater.set_temperature": "Температура обновлена на",
        "water_heater.set_operation_mode": "Режим изменён на",
        "light.turn_on": "Включено",
        "light.turn_off": "Выключено",
        "light.toggle": "Переключено",
        "switch.turn_on": "Включено",
        "switch.turn_off": "Выключено",
        "scene.turn_on": "Активирована",
        "cover.open_cover": "Открыто",
        "cover.close_cover": "Закрыто",
        "cover.toggle": "Переключено",
        "cover.stop_cover": "Остановлено",
        "cover.set_cover_position": "Положение изменено",
    },
}

_PAST_VERBS_BY_DOMAIN_BY_LANG: dict[str, dict[str, str]] = {
    "fr": {
        "tts": "Annoncé sur",
        "notify": "Notification envoyée via",
        "script": "Script exécuté",
        "shell_command": "Commande shell exécutée",
    },
    "de": {
        "tts": "Angekündigt auf",
        "notify": "Benachrichtigung gesendet über",
        "script": "Skript ausgeführt",
        "shell_command": "Shell-Befehl ausgeführt",
    },
    "es": {
        "tts": "Anunciado en",
        "notify": "Notificación enviada vía",
        "script": "Script ejecutado",
        "shell_command": "Comando shell ejecutado",
    },
    "it": {
        "tts": "Annunciato su",
        "notify": "Notifica inviata tramite",
        "script": "Script eseguito",
        "shell_command": "Comando shell eseguito",
    },
    "nl": {
        "tts": "Aangekondigd op",
        "notify": "Melding verzonden via",
        "script": "Script uitgevoerd",
        "shell_command": "Shell-commando uitgevoerd",
    },
    "hu": {
        "tts": "Bejelentve",
        "notify": "Értesítés elküldve via",
        "script": "Szkript lefuttatva",
        "shell_command": "Shell parancs lefuttatva",
    },
    "zh": {
        "tts": "已播报至",
        "notify": "已发送通知至",
        "script": "已执行脚本",
        "shell_command": "已执行 Shell 命令",
    },
    "pt": {
        "tts": "Anunciado em",
        "notify": "Notificação enviada via",
        "script": "Script executado",
        "shell_command": "Comando shell executado",
    },
    "ja": {
        "tts": "アナウンスしました",
        "notify": "通知を送信しました",
        "script": "スクリプトを実行しました",
        "shell_command": "シェルコマンドを実行しました",
    },
    "ko": {
        "tts": "안내방송했습니다",
        "notify": "알림을 보냈습니다",
        "script": "스크립트를 실행했습니다",
        "shell_command": "셸 명령을 실행했습니다",
    },
    "ru": {
        "tts": "Объявлено на",
        "notify": "Уведомление отправлено через",
        "script": "Скрипт выполнен",
        "shell_command": "Команда оболочки выполнена",
    },
}

_GENERIC_RAN_BY_LANG: dict[str, str] = {
    "fr": "Exécuté",
    "de": "Ausgeführt",
    "es": "Ejecutado",
    "it": "Eseguito",
    "nl": "Uitgevoerd",
    "hu": "Lefuttatva",
    "zh": "已执行",
    "pt": "Executado",
    "ja": "実行しました",
    "ko": "실행했습니다",
    "ru": "Выполнено",
}

_DONE_BY_LANG: dict[str, str] = {
    "fr": "Terminé.",
    "de": "Fertig.",
    "es": "Hecho.",
    "it": "Fatto.",
    "nl": "Klaar.",
    "hu": "Kész.",
    "zh": "完成。",
    "pt": "Concluído.",
    "ja": "完了しました。",
    "ko": "완료했습니다.",
    "ru": "Готово.",
}

# Sentence template per locale. Non-EN locales use a colon-separator so
# the past participle reads as a status label ("Allumé : Kitchen Lights")
# rather than a verb that needs gender/number agreement with the object —
# entity names from HA are in the user's setup language (often English)
# and won't agree with French/Italian past participles. FR follows the
# convention of space-colon-space; other languages use plain colon.
_SENTENCE_FORMAT_BY_LANG: dict[str, str] = {
    "en": "{past} {target}.",
    "fr": "{past} : {target}.",
    "de": "{past}: {target}.",
    "es": "{past}: {target}.",
    "it": "{past}: {target}.",
    "nl": "{past}: {target}.",
    "hu": "{past}: {target}.",
    "zh": "{past}：{target}。",
    "pt": "{past}: {target}.",
    "ja": "{past}：{target}。",
    "ko": "{past}: {target}.",
    "ru": "{past}: {target}.",
}


def _normalize_lang(language: str | None) -> str:
    if not language:
        return "en"
    base = str(language).lower().split("-")[0]
    return base if base in _PAST_VERBS_BY_SERVICE_BY_LANG else "en"


def past_verb_for(service: str, language: str | None = None) -> str:
    """Return a past-tense verb phrase for *service*.

    Falls back to the domain-level table, then to the generic "Ran"
    so an unknown service still produces a readable sentence.
    """
    lang = _normalize_lang(language)
    if lang != "en":
        loc = _PAST_VERBS_BY_SERVICE_BY_LANG.get(lang, {})
        if service in loc:
            return loc[service]
    if service in _PAST_VERBS_BY_SERVICE:
        return _PAST_VERBS_BY_SERVICE[service]
    domain = service.split(".", 1)[0] if "." in service else ""
    if lang != "en":
        loc_d = _PAST_VERBS_BY_DOMAIN_BY_LANG.get(lang, {})
        if domain in loc_d:
            return loc_d[domain]
        if domain not in _PAST_VERBS_BY_DOMAIN:
            return _GENERIC_RAN_BY_LANG.get(lang, "Ran")
    return _PAST_VERBS_BY_DOMAIN.get(domain, "Ran")


def _done_text(language: str | None) -> str:
    lang = _normalize_lang(language)
    return _DONE_BY_LANG.get(lang, "Done.")


def _friendly_name_resolver(hass: HomeAssistant | None) -> Callable[[str], str]:
    """Closure that maps entity_id → friendly_name for confirmation text.

    Returns the raw entity_id when the state is missing (deleted entity
    or hass not available).
    """

    def _resolve(entity_id: str) -> str:
        if hass is None:
            return entity_id
        state = hass.states.get(entity_id)
        if state is None:
            return entity_id
        return state.attributes.get("friendly_name") or entity_id

    return _resolve


def build_executed_confirmation(
    executed_calls: list[ToolWriteResult],
    friendly_name_resolver: Callable[[str], str] | None = None,
    *,
    exclude_marker_ids: set[str] | None = None,
    language: str | None = None,
) -> str:
    """Compose a friendly post-execution message from successful tool
    calls.

    Used by the tool-loop short-circuit to fabricate the assistant's
    reply without a second LLM round. Each call becomes a past-tense
    sentence ("Unlocked Front Door."); entity targets become a
    ``[[entities:…]]`` marker so the chat renders a live HA tile
    card under the bubble.

    ``friendly_name_resolver`` maps entity_id → friendly name (e.g.
    ``lambda eid: hass.states.get(eid).attributes.get("friendly_name")``).
    When omitted the raw entity_id is used — callers without hass
    context (tests, low-level helpers) can still produce a sentence
    that's at least informative.

    ``exclude_marker_ids`` lists entity_ids that already rendered a tile
    in earlier streamed prose (the model's pre-tool narration). Those are
    dropped from this message's ``[[entities:…]]`` marker so the chat
    doesn't show two identical entity cards for one executed action.
    """
    excluded = exclude_marker_ids or set()
    done_text = _done_text(language)
    if not executed_calls:
        return done_text
    lang = _normalize_lang(language)
    fmt = _SENTENCE_FORMAT_BY_LANG.get(lang, _SENTENCE_FORMAT_BY_LANG["en"])
    sentences: list[str] = []
    entity_ids: list[str] = []
    for call in executed_calls:
        service = str(call.get("service", "")).strip()
        if not service:
            continue
        ids = call.get("entity_ids")
        if not isinstance(ids, list):
            target = call.get("target")
            raw = target.get("entity_id") if isinstance(target, dict) else None
            if isinstance(raw, str):
                ids = [raw] if raw else []
            elif isinstance(raw, list):
                ids = [e for e in raw if isinstance(e, str)]
            else:
                ids = []
        past = past_verb_for(service, language)
        if ids:
            names = [friendly_name_resolver(eid) if friendly_name_resolver else eid for eid in ids]
            sentences.append(fmt.format(past=past, target=", ".join(names)))
            entity_ids.extend(ids)
        else:
            tail = service.split(".", 1)[1] if "." in service else service
            sentences.append(fmt.format(past=past, target=tail))
    content = " ".join(sentences) if sentences else done_text
    marker_ids = [eid for eid in entity_ids if eid not in excluded]
    if marker_ids:
        content += f"\n\n[[entities:{','.join(marker_ids)}]]"
    return content


def _build_command_confirmation(calls: list[dict[str, Any]], language: str | None = None) -> str:
    """Build a human-readable confirmation from a list of validated service calls.

    Only called after ``apply_command_policy`` has validated the calls,
    so types are guaranteed.  Used as fallback when the LLM returns a
    command intent without a ``response`` field (#94).
    """
    done_text = _done_text(language)
    if not isinstance(calls, list) or not calls:
        return done_text
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
        return done_text
    # Use the locale-aware "Done"; drop the trailing period since we
    # append our own list separator and a final dot below.
    done_prefix = done_text.rstrip(".")
    return done_prefix + " — " + "; ".join(parts) + "."


def _pending_approval_calls_from_log(
    tool_log: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Extract REVIEW-bucket calls the LLM attempted via ``execute_command``
    that came back ``requires_approval=True``.

    The tool path returns ``requires_approval`` so the LLM can route the
    call to the approval card instead of treating "outside the safe
    allowlist" as a hard rejection. Some models still narrate the result
    back to the user ("I can't unlock the door directly because it
    requires approval") instead of producing the proper JSON block.

    This helper lets the policy synthesize the missing ``command_approval``
    proposal from the tool log so the user gets the approval card
    regardless of how the LLM phrased its reply. Each entry is shaped
    like a ``ServiceCallDict`` plus the ``risk_level`` and
    ``approval_reason`` carried by the original tool result so the card
    renders without re-classifying.
    """
    pending: list[dict[str, Any]] = []
    # ``validate_action`` returns the same ``requires_approval`` shape as
    # ``execute_command``. Treat both the same way so a model that
    # validates first and then hedges in prose ("would you like me to
    # go ahead?") still triggers the approval card — the user shouldn't
    # need to say "yes" a second time after the request was clear.
    approval_tools = {"execute_command", "validate_action"}
    seen_signatures: set[_CallSignature] = set()
    for entry in tool_log or []:
        if entry.get("tool") not in approval_tools:
            continue
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        if not result.get("requires_approval"):
            continue
        args = entry.get("arguments") or {}
        service = str(result.get("service") or args.get("service") or "").strip()
        if not service or "." not in service:
            continue
        raw_entity = args.get("entity_id")
        if isinstance(raw_entity, str):
            ids = [raw_entity.strip()] if raw_entity.strip() else []
        elif isinstance(raw_entity, list):
            ids = [str(e).strip() for e in raw_entity if str(e).strip()]
        else:
            ids = []
        data = args.get("data") if isinstance(args.get("data"), dict) else {}
        proposed_call: dict[str, Any] = {
            "service": service,
            "target": {"entity_id": ids} if ids else {},
            "data": data,
        }
        # Dedup: a model that validates first and then executes (or
        # tries to and gets blocked again) will appear twice in the
        # tool log for the same call. Key on the data payload too so
        # distinct calls that share a service+entity set (e.g. two
        # notify.mobile_app_* with different messages, both empty
        # entity set) each surface their own card.
        signature = (service, frozenset(ids), _data_signature(data))
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)

        # Two ways a tool can have returned ``requires_approval``:
        #   1. Static REVIEW bucket (lock.*, alarm_*, tts.*, …).
        #   2. SAFE-bucket call elevated by device_class (cover.open_cover
        #      on a garage door). ``_classify_call`` still returns "safe"
        #      for these, so we fall back to the tool result's own
        #      ``risk_level`` + ``approval_reason`` and trust that the
        #      validator already shape-checked the call.
        # Without this branch, asking a tool-capable model to open a
        # garage would narrate "requires approval" with no card to act
        # on.
        bucket, policy_entry = _classify_call(service)
        if bucket == "review" and policy_entry is not None:
            validated, shape_err = _validate_review_call(proposed_call, policy_entry)
            if shape_err is not None or validated is None:
                _LOGGER.warning(
                    "Dropping requires_approval tool log entry for %s: %s",
                    service,
                    shape_err,
                )
                continue
            pending.append(
                {
                    **validated,
                    "_risk_level": policy_entry.get("risk", APPROVAL_RISK_LOW),
                    "_reason": policy_entry.get("reason", result.get("approval_reason", "")),
                }
            )
        elif result.get("risk_level"):
            # Elevated SAFE: the validator that produced this result
            # already enforced the SAFE shape policy upstream. Carry
            # the risk metadata it returned and let ``_resolve_approval``
            # revalidate via ``_validate_safe_call`` at click time.
            pending.append(
                {
                    **proposed_call,
                    "_risk_level": result["risk_level"],
                    "_reason": result.get("approval_reason", ""),
                }
            )
        else:
            # Defensive: requires_approval=True with neither a static
            # REVIEW entry nor a risk_level is malformed. Drop with a
            # warning so the LLM isn't smuggling unclassified calls.
            _LOGGER.warning(
                "Dropping requires_approval tool log entry with no risk metadata: %s",
                service,
            )
    return pending


def _proposal_call_entity_ids(call: dict[str, Any]) -> list[str]:
    """Extract entity ids from a proposal call, accepting either the
    ``target.entity_id`` or a top-level ``entity_id`` shape a model might
    emit."""
    target = call.get("target")
    raw = target.get("entity_id") if isinstance(target, dict) else call.get("entity_id")
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, list):
        return [str(e).strip() for e in raw if str(e).strip()]
    return []


def _server_derived_risk(
    calls: list[Any],
    hass: HomeAssistant | None,
) -> tuple[str, list[str]]:
    """Classify each proposed call server-side; return ``(risk_level, reasons)``.

    Never trust an LLM-supplied ``risk_level``: a model can emit a high-risk
    call (``lock.unlock``, ``alarm_control_panel.alarm_disarm``,
    ``shell_command.*``) while labelling the card LOW. Deriving the badge from
    the calls keeps what the user sees aligned with what ``_resolve_approval``
    will actually enforce at click time.
    """
    levels: list[str] = []
    # Reasons are rendered strictly per call index by the approval card,
    # so this list must stay parallel to ``calls`` — one slot per call,
    # empty string for SAFE/non-risky calls. A compacted list would shift
    # the REVIEW reason onto a SAFE row and leave the risky call unexplained.
    reasons: list[str] = []
    for call in calls:
        if not isinstance(call, dict):
            reasons.append("")
            continue
        service = str(call.get("service", "")).strip()
        if not service:
            reasons.append("")
            continue
        ids = _proposal_call_entity_ids(call)
        bucket, entry = _classify_call(service)
        # device_class escalation (garage/door/gate covers) needs hass.
        # A SAFE-bucket cover.open_cover/toggle on such an entity still
        # executes the high-risk physical action at click time, so the
        # badge must reflect that even though _classify_call says "safe".
        escalated = _entity_aware_review_entry(hass, service, ids) if ids else None
        chosen = escalated or (entry if bucket == "review" else None)
        if chosen is not None:
            levels.append(chosen.get("risk", APPROVAL_RISK_MEDIUM))
            reasons.append(chosen.get("reason") or "")
        elif bucket == "blocked":
            # A blocked service must never be presented as low-risk — the
            # click-time validator will refuse it. Surface HIGH so the badge
            # can't understate what the model proposed.
            levels.append(APPROVAL_RISK_HIGH)
            reasons.append(f"{service} is not an approvable action")
        else:
            # SAFE bucket contributes nothing to the badge — stays LOW
            # unless a riskier sibling raises the max — but still needs a
            # slot so later reasons stay aligned with their calls.
            reasons.append("")
    if not levels:
        return APPROVAL_RISK_LOW, reasons
    return _max_risk(levels), reasons


def _normalize_explicit_approval(
    result: ArchitectResponse,
    hass: HomeAssistant | None = None,
) -> ArchitectResponse:
    """Ensure an LLM-emitted ``intent: "command_approval"`` payload has
    a stable ``proposal_id`` and the four sentinel quick-actions.

    A tool-capable model that observes ``requires_approval=True`` from
    ``execute_command`` may emit its own ``command_approval`` JSON
    instead of letting the synthesizer build the proposal. The card
    persists with ``approval_status: "pending"``, but without the
    ``approve:<scope>:<proposal_id>`` buttons there's no way for the
    user to resolve it — short of reloading and hoping a later turn
    regenerates the proposal.

    This normaliser is idempotent: a well-formed proposal passes
    through unchanged; a malformed one (no ``calls`` list) is
    downgraded to ``intent: "answer"`` so the user isn't stuck
    staring at an unresolvable card.
    """
    if result.get("intent") != "command_approval":
        return result
    proposal_raw = result.get("command_approval")
    if not isinstance(proposal_raw, dict) or not isinstance(proposal_raw.get("calls"), list):
        _LOGGER.warning("Dropping malformed command_approval payload (no calls list)")
        downgraded: ArchitectResponse = dict(result)
        downgraded["intent"] = "answer"
        downgraded.pop("command_approval", None)
        downgraded.pop("quick_actions", None)
        downgraded["response"] = result.get("response") or "I can't process this request safely."
        return downgraded

    proposal = dict(proposal_raw)
    new_id = False
    if not isinstance(proposal.get("proposal_id"), str) or not proposal["proposal_id"]:
        proposal["proposal_id"] = str(uuid.uuid4())
        new_id = True
    # Always re-derive the risk badge from the proposed calls — never trust
    # or default the model's risk_level. A low-context / non-tool model can
    # mislabel lock.unlock / alarm_disarm / shell_command.* as LOW, which
    # would understate the user's safety decision.
    derived_risk, derived_reasons = _server_derived_risk(proposal.get("calls", []), hass)
    proposal["risk_level"] = derived_risk
    if not isinstance(proposal.get("risk_reasons"), list) or not proposal["risk_reasons"]:
        proposal["risk_reasons"] = derived_reasons

    # Regenerate quick_actions when proposal_id was minted OR when the
    # existing actions don't carry the complete four-scope set tied to
    # this proposal_id. A partial set would leave the user with some
    # buttons missing; a stale proposal_id would route clicks to a
    # phantom server-side proposal.
    pid = proposal["proposal_id"]
    actions = result.get("quick_actions") or []
    needs_regen = new_id
    if not needs_regen:
        seen_scopes: set[str] = set()
        for a in actions:
            if not isinstance(a, dict):
                continue
            value = str(a.get("value", ""))
            if not value.startswith("approve:"):
                continue
            rest = value[len("approve:") :]
            scope_sep = rest.split(":", 1)
            if len(scope_sep) != 2 or scope_sep[1] != pid:
                continue
            seen_scopes.add(scope_sep[0])
        needs_regen = seen_scopes != {"once", "session", "always", "deny"}
    if needs_regen:
        actions = _approval_quick_actions(pid)

    normalized: ArchitectResponse = dict(result)
    normalized["command_approval"] = proposal
    normalized["quick_actions"] = actions
    return normalized


def synthesize_approval_from_tool_log(
    result: ArchitectResponse,
    tool_log: list[dict[str, Any]] | None,
    hass: HomeAssistant | None = None,
    *,
    language: str | None = None,
) -> ArchitectResponse:
    """If the LLM tried ``execute_command`` on a REVIEW service and the
    tool returned ``requires_approval=True``, upgrade *result* to a
    ``command_approval`` proposal even when the model didn't emit a
    matching JSON block.

    When the model already produced its own ``command_approval``
    intent, normalise it (mint proposal_id if needed, attach the four
    sentinel actions) rather than passing it through untouched — an
    LLM-shaped payload typically lacks the ``approve:<scope>:<id>``
    quick-actions the chat UI needs to surface Allow/Deny buttons.
    """
    if result.get("intent") == "command_approval":
        return _normalize_explicit_approval(result, hass)
    if not tool_log:
        return result
    if result.get("intent") in ("command", "delayed_command"):
        return result
    pending = _pending_approval_calls_from_log(tool_log)
    if not pending:
        return result

    risks = [c.pop("_risk_level", APPROVAL_RISK_LOW) for c in pending]
    reasons = [c.pop("_reason", "") for c in pending]
    proposal = _build_approval_proposal(pending, risks, reasons)
    upgraded: ArchitectResponse = dict(result)
    upgraded["intent"] = "command_approval"
    upgraded["calls"] = []
    upgraded["command_approval"] = proposal
    upgraded["quick_actions"] = _approval_quick_actions(proposal["proposal_id"])
    # Replace the LLM's narration with a concise hint pointing at the
    # card. The model's text often editorialises ("I can't execute this
    # because…") which is misleading once the card is up — the action
    # is one click away, not refused.
    # Prefer the request locale so the hint matches the user's
    # frontend language. Server-wide hass.config.language stays as the
    # fallback for callers that don't thread a request locale through.
    effective_language = language or (hass.config.language if hass is not None else None)
    hint = approval_pending_hint(effective_language)
    # When other write tools already FIRED in the same round (e.g. "turn
    # off the kitchen light and unlock the door" — the light executes,
    # the unlock holds for approval), acknowledge the executed actions
    # alongside the card. Otherwise the confirmation + entity tile for
    # the action that really happened gets dropped behind the approval
    # hint. _iter_executed_write_actions skips the pending call (it isn't
    # executed) and normalises scenes, so this also covers mixed
    # scene/device rounds.
    executed = _iter_executed_write_actions(tool_log)
    if executed:
        resolver = _friendly_name_resolver(hass)
        confirmation = build_executed_confirmation(executed, resolver, language=effective_language)
        upgraded["response"] = f"{confirmation}\n\n{hint}"
    else:
        upgraded["response"] = hint
    return upgraded


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


# Looser verb regex used by the attempted-call gate below — broader
# than ``_ACTION_VERB_RE`` so it also catches lock/unlock/disarm/arm
# narration. The strict trust path uses ``_ACTION_VERB_RE`` for
# per-mention verb-direction consistency, which lock-style services
# don't need (they don't have an "opposite" verb in our table).
_ATTEMPTED_ACTION_VERB_RE = re.compile(
    r"\bturn(?:ing|ed)?\s+(?:on|off)\b"
    r"|\bswitch(?:ing|ed)?\s+(?:on|off)\b"
    r"|\bshut(?:ting)?\b"
    r"|\bopen(?:ing|ed)?\b"
    r"|\bclos(?:e|ing|ed)\b"
    r"|\block(?:ing|ed)?\b"
    r"|\bunlock(?:ing|ed)?\b"
    r"|\barm(?:ing|ed)?\b"
    r"|\bdisarm(?:ing|ed)?\b"
    r"|\bplay(?:ing|ed)?\b"
    r"|\bpaus(?:e|ing|ed)\b"
    r"|\bstopp(?:ing|ed)?\b"
    r"|\bresum(?:e|ing|ed)\b"
    r"|\bset(?:ting)?\b"
    r"|\bdimm(?:ing|ed)?\b",
    re.IGNORECASE,
)


def prose_describes_attempted_call(
    response: str,
    tool_log: list[dict[str, Any]] | None,
    entities: list[EntitySnapshot] | None = None,
) -> bool:
    """True iff *response* describes an entity from an attempted
    execute_command / validate_action call with verb-direction
    consistency.

    Used as a fallback trust signal in the parser so a tool that
    errored or returned an unexpected shape (no ``executed:True`` and
    no ``requires_approval:True``) still suppresses the
    "no entity matched" stomp when the LLM clearly identified the
    target. Reuses ``_response_describes_executed_call`` against
    synthetic call objects built from the tool arguments — so the
    same verb-direction + unbacked-entity safety checks that gate
    executed-tool trust also gate this attempted-tool trust.
    """
    if not response or not tool_log:
        return False
    approval_tools = {"execute_command", "validate_action"}
    attempted_calls: list[dict[str, Any]] = []
    seen: set[tuple[str, frozenset[str]]] = set()
    for entry in tool_log:
        if entry.get("tool") not in approval_tools:
            continue
        args = entry.get("arguments") or {}
        service = str(args.get("service", "")).strip()
        if not service or "." not in service:
            continue
        raw = args.get("entity_id")
        if isinstance(raw, str):
            ids = [raw] if raw else []
        elif isinstance(raw, list):
            ids = [e for e in raw if isinstance(e, str)]
        else:
            ids = []
        if not ids:
            continue
        sig = (service, frozenset(ids))
        if sig in seen:
            continue
        seen.add(sig)
        attempted_calls.append({"service": service, "target": {"entity_id": list(ids)}})
    if not attempted_calls:
        return False
    return _response_describes_executed_call(response, attempted_calls, entities)


# Backwards-compatible underscore alias kept for callers that imported
# this from llm_client during the API rename.
_prose_describes_attempted_call = prose_describes_attempted_call


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


# Entity tile markers ``[[entity:lock.front_door|Friendly]]`` /
# ``[[entities:lock.a,lock.b]]`` carry raw entity_ids inside the prose
# stream. Word-boundary token matching against the raw lowercased
# response treats those id fragments as regular words — so a marker
# like ``[[entity:lock.front_door]]`` injects "lock" and "entity" as
# matchable tokens. A user with any entity whose object_id contains
# "lock" (an automation, script, sensor — extremely common) then
# trips the unbacked-entity veto, falsely flunking the trust gate.
# Strip the markers before lowercasing so the veto sees only the
# user-visible prose.
_TILE_MARKER_RE = re.compile(r"\[\[(?:entity|entities|areas):[^\]]*\]?\]?")


def _haystack_without_markers(response: str) -> str:
    """Lowercase *response* with entity tile markers stripped.

    Tolerates the malformed ``[[entity:lock.front_door`` shape (no
    closing ``]]``) the LLM occasionally produces mid-stream — the
    regex strips through the next ``]]`` if any, otherwise to the
    end of the bracket-open run.
    """
    if not isinstance(response, str):
        return ""
    return _TILE_MARKER_RE.sub(" ", response).lower()


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
    haystack = _haystack_without_markers(response)

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
    haystack = _haystack_without_markers(response)
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


_EXHAUSTION_RAN_OUT_BY_LANG: dict[str, str] = {
    "en": (
        "Then I ran out of tool rounds before finishing — please try a "
        "more specific request only if there's more to do."
    ),
    "fr": (
        "Puis j'ai épuisé les tours d'outils avant de terminer — "
        "réessayez avec une demande plus précise uniquement s'il reste "
        "des choses à faire."
    ),
    "de": (
        "Dann gingen mir die Tool-Runden aus, bevor ich fertig war — "
        "bitte versuchen Sie es nur mit einer spezifischeren Anfrage "
        "erneut, wenn noch etwas zu tun ist."
    ),
    "es": (
        "Luego se me acabaron las rondas de herramientas antes de "
        "terminar — vuelva a intentarlo con una solicitud más "
        "específica solo si queda algo por hacer."
    ),
    "it": (
        "Poi ho esaurito i giri di strumenti prima di finire — riprova "
        "con una richiesta più specifica solo se c'è ancora qualcosa da "
        "fare."
    ),
    "nl": (
        "Daarna raakten mijn tool-rondes op voordat ik klaar was — "
        "probeer het alleen opnieuw met een specifiekere vraag als er "
        "nog iets te doen is."
    ),
    "hu": (
        "Ezután elfogytak az eszközforduló-keretek, mielőtt befejeztem "
        "volna — csak akkor próbálkozzon konkrétabb kéréssel, ha még "
        "van tennivaló."
    ),
}

_EXHAUSTION_NO_EXEC_BY_LANG: dict[str, str] = {
    "en": (
        "I used several tools but couldn't complete the analysis. "
        "Please try a more specific request."
    ),
    "fr": (
        "J'ai utilisé plusieurs outils mais je n'ai pas pu terminer "
        "l'analyse. Veuillez réessayer avec une demande plus précise."
    ),
    "de": (
        "Ich habe mehrere Tools verwendet, konnte die Analyse aber "
        "nicht abschließen. Bitte versuchen Sie es mit einer "
        "spezifischeren Anfrage."
    ),
    "es": (
        "Usé varias herramientas pero no pude completar el análisis. "
        "Por favor intente con una solicitud más específica."
    ),
    "it": (
        "Ho usato vari strumenti ma non sono riuscito a completare "
        "l'analisi. Riprova con una richiesta più specifica."
    ),
    "nl": (
        "Ik gebruikte meerdere tools maar kon de analyse niet "
        "voltooien. Probeer het opnieuw met een specifiekere vraag."
    ),
    "hu": (
        "Több eszközt használtam, de nem tudtam befejezni az "
        "elemzést. Próbálkozzon konkrétabb kéréssel."
    ),
}


def _exhaustion_text(language: str | None, executed_any: bool) -> str:
    """Localized tool-loop exhaustion suffix.

    Two phrasings: one acknowledges that something ran (so the user
    doesn't retry and double-execute), the other admits nothing
    completed. Falls back to English for unknown locales.
    """
    table = _EXHAUSTION_RAN_OUT_BY_LANG if executed_any else _EXHAUSTION_NO_EXEC_BY_LANG
    lang = _normalize_lang(language)
    return table.get(lang, table["en"])


def _tool_failure_response(
    tool_log: list[dict[str, Any]] | None,
    *,
    suffix: str | None = None,
    language: str | None = None,
) -> str:
    """Compose a user-facing message when the tool loop bailed out.

    If any ``execute_command`` already succeeded this turn, the result is
    something like ``"Done — light turn_off (kitchen). " + suffix`` so the
    user sees what already happened and is not tempted to retry and run
    the same service a second time. Otherwise just ``suffix`` is returned.

    ``suffix`` is optional — when omitted the locale-aware default is
    used (preferred call shape). Legacy callers that still pass a raw
    suffix string keep working.
    """
    executed = _executed_service_calls_from_log(tool_log)
    effective_suffix = (
        suffix if suffix is not None else _exhaustion_text(language, executed_any=bool(executed))
    )
    if not executed:
        return effective_suffix
    return _build_command_confirmation(executed, language=language) + " " + effective_suffix


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


def _validate_review_call(
    call: dict[str, Any],
    entry: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Light-weight shape check for a REVIEW-bucket call.

    Unlike SAFE calls we don't enforce the "entity must be in the
    known allowlist" rule — REVIEW services like ``notify.mobile_app_*``
    and ``shell_command.*`` legitimately take no entity, and
    ``tts.cloud_say`` may target a media_player not present in the
    filtered snapshot (announcement-only speakers). The user's explicit
    approval click is the safety gate; we only sanity-check the shape.

    Returns ``(validated_call, None)`` on success, or
    ``(None, error_string)`` on rejection.
    """
    service = str(call.get("service", "")).strip()
    if "." not in service:
        return None, "missing a valid service name"

    # Data keys: ``None`` means free-form (tts.cloud_say's message, etc.).
    # A set means restrict to those keys.
    allowed_keys = entry.get("data")
    data = call.get("data", {})
    if data is not None and not isinstance(data, dict):
        return None, f"{service} included an invalid data payload"
    data = data or {}
    if isinstance(allowed_keys, set):
        extra = sorted(set(data) - allowed_keys)
        if extra:
            return None, f"{service} included unsupported parameters: {', '.join(extra)}"

    target = call.get("target", {})
    if target is not None and not isinstance(target, dict):
        return None, f"{service} had an invalid target payload"
    target = target or {}

    entity_ids = target.get("entity_id")
    if isinstance(entity_ids, str):
        target_ids: list[str] = [entity_ids] if entity_ids else []
    elif isinstance(entity_ids, list) and all(isinstance(eid, str) for eid in entity_ids):
        target_ids = entity_ids
    elif entity_ids is None:
        target_ids = []
    else:
        return None, f"{service} had an invalid target payload"

    if len(target_ids) > _MAX_TARGET_ENTITIES:
        return None, f"{service} targeted too many entities at once (max {_MAX_TARGET_ENTITIES})"

    # Without ``requires_target=False`` we treat the entity_id as
    # mandatory. HA entity-services (lock.*, alarm_*, vacuum.*,
    # water_heater.*, tts.*) fall back to acting on EVERY entity in
    # the domain when no target is supplied — so a single "approve"
    # click on a malformed proposal could unlock every door / disarm
    # every alarm. Only services that legitimately target by service
    # name (notify.*, script.*, shell_command.*) get the opt-out.
    if entry.get("requires_target", True) and not target_ids:
        return None, f"{service} requires an explicit entity_id target"

    return (
        {
            "service": service,
            "target": {"entity_id": target_ids} if target_ids else {},
            "data": data,
        },
        None,
    )


def _validate_safe_call(
    call: dict[str, Any],
    known_entity_ids: set[str],
) -> tuple[dict[str, Any] | None, str | None]:
    """Apply the full SAFE-bucket policy to one ServiceCallDict.

    Mirrors the inline per-call checks in ``apply_command_policy``:
    domain + verb in the allowlist, entity targets non-empty and in
    the known set, each target's domain matches the service domain,
    target count under the cap, data dict only carries whitelisted
    keys.

    Used at approval-resolution time to defend against a
    ``command_approval`` payload that was synthesised from outside
    ``apply_command_policy``. The JSON parser can produce a result
    with ``intent="command_approval"`` straight from model output, so
    treating the stored proposal as already-validated would let a
    crafted ``calls`` array bypass the SAFE policy for services like
    ``light.turn_on`` or ``scene.turn_on``.

    Returns ``(validated_call, None)`` on success, ``(None, error)``
    on rejection.
    """
    service = str(call.get("service", "")).strip()
    if "." not in service:
        return None, "missing a valid service name"
    domain, service_name = service.split(".", 1)
    if domain not in _ALLOWED_COMMAND_SERVICES:
        return None, f"{service} is outside the safe command allowlist"
    if service_name not in _ALLOWED_COMMAND_SERVICES[domain]:
        return None, f"{service} is not a valid {domain} service"

    target = call.get("target", {})
    if target is not None and not isinstance(target, dict):
        return None, f"{service} had an invalid target payload"
    target = target or {}
    entity_ids = target.get("entity_id")
    if isinstance(entity_ids, str):
        target_ids: list[str] = [entity_ids] if entity_ids else []
    elif isinstance(entity_ids, list) and all(isinstance(eid, str) for eid in entity_ids):
        target_ids = entity_ids
    else:
        return None, f"{service} did not target explicit entity_ids"

    if not target_ids:
        return None, f"{service} did not include any target entities"
    if len(target_ids) > _MAX_TARGET_ENTITIES:
        return None, f"{service} targeted too many entities at once (max {_MAX_TARGET_ENTITIES})"
    for entity_id in target_ids:
        if entity_id not in known_entity_ids:
            return None, f"{service} referenced an unknown entity_id ({entity_id})"
        if entity_id.split(".", 1)[0] != domain:
            return None, f"{service} targeted {entity_id}, which is outside the {domain} domain"

    data = call.get("data", {})
    if data is not None and not isinstance(data, dict):
        return None, f"{service} included an invalid data payload"
    data = data or {}
    allowed_data_keys = _COMMAND_SERVICE_POLICIES[domain][service_name]
    extra = sorted(set(data) - allowed_data_keys)
    if extra:
        return None, f"{service} included unsupported parameters: {', '.join(extra)}"

    return (
        {
            "service": service,
            "target": {"entity_id": target_ids},
            "data": data,
        },
        None,
    )


def _build_approval_proposal(
    pending: list[dict[str, Any]],
    risks: list[str],
    reasons: list[str],
) -> dict[str, Any]:
    """Assemble the ``command_approval`` payload returned to the chat handler.

    ``risks`` and ``reasons`` are kept aligned by index with ``pending``
    so the frontend can render each call's reason on its own row. We
    don't dedupe — two notify targets with the same reason are still
    two distinct rows the user is approving.

    For a mixed proposal (SAFE calls bundled with REVIEW calls), the
    SAFE positions carry an empty string in both lists; only the
    non-empty risks contribute to ``risk_level``.
    """
    nonempty_risks = [r for r in risks if r]
    return {
        "proposal_id": str(uuid.uuid4()),
        "risk_level": _max_risk(nonempty_risks) if nonempty_risks else APPROVAL_RISK_LOW,
        "risk_reasons": reasons,
        "calls": pending,
    }


def _approval_quick_actions(proposal_id: str) -> list[dict[str, Any]]:
    """Action cards rendered under the approval card.

    Uses ``mode="choice"`` so the renderer picks the grid card layout
    (icon + label + description), and ``tone`` to drive the border
    accent — green for approve scopes, red for deny — overriding the
    default amber comet on these specific cards.

    Sentinel ``value`` (``approve:<scope>:<proposal_id>``) lets the
    chat handler dispatch the click to the approval WS handler
    instead of replaying it as a user message.
    """
    return [
        {
            "label": "Allow once",
            "description": "Just this one request",
            "value": f"approve:once:{proposal_id}",
            "mode": "choice",
            "icon": "mdi:check",
            "tone": "approve",
        },
        {
            "label": "Session",
            "description": "Allow for the rest of this conversation",
            "value": f"approve:session:{proposal_id}",
            "mode": "choice",
            "icon": "mdi:check-all",
            "tone": "approve",
        },
        {
            "label": "Always",
            "description": "Remember this approval",
            "value": f"approve:always:{proposal_id}",
            "mode": "choice",
            "icon": "mdi:shield-check",
            "tone": "approve",
        },
        {
            "label": "Deny",
            "description": "Do not run this request",
            "value": f"approve:deny:{proposal_id}",
            "mode": "choice",
            "icon": "mdi:close",
            "tone": "deny",
        },
    ]


def apply_command_policy(
    result: ArchitectResponse,
    entities: list[EntitySnapshot],
    *,
    hass: HomeAssistant | None = None,
    approval_store: ApprovalStore | None = None,
    session_id: str | None = None,
    language: str | None = None,
) -> ArchitectResponse:
    """Reject unsafe immediate commands before any caller can execute them."""
    approval_store = _resolve_approval_store(hass, approval_store)
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
        # The model narrated an action ("Unlocking the front door now.")
        # plus an entity tile but emitted no calls. When the entity and verb
        # are unambiguous, rebuild the call and route it through the normal
        # approval / execution path below instead of nagging the user to
        # retry. ``delayed_command`` is excluded — recovering to an immediate
        # call would drop the requested delay.
        if result.get("intent") != "delayed_command":
            recovered = _recover_command_from_unbacked_prose(result, entities)
            if recovered is not None:
                _LOGGER.info(
                    "Recovered command %s from unbacked action prose (model emitted no calls)",
                    recovered["service"],
                )
                result = dict(result, intent="command", calls=[recovered])
                calls = result["calls"]
        if not calls:
            # If the LLM classified as "command" or "delayed_command" but
            # provided no calls, downgrade to "answer". When the response
            # text reads as a confirmation ("Turning off …"), replace it
            # so the user isn't told an action ran when none did.
            if result.get("intent") in ("command", "delayed_command"):
                result = dict(result, intent="answer")
                if _looks_like_unbacked_action(result.get("response", "")):
                    msg, verr = _unbacked_action_response(result.get("response", ""), entities)
                    result["response"] = msg
                    result["validation_error"] = verr
            elif result.get("intent") == "answer" and _looks_like_unbacked_action(
                result.get("response", ""), strict=True
            ):
                # No upstream signal that the LLM intended a command, so use
                # the strict matcher to avoid replacing a short help answer
                # that happens to start with an action verb.
                msg, verr = _unbacked_action_response(result.get("response", ""), entities)
                result = dict(result, response=msg)
                result["validation_error"] = verr
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

    # Per-call records preserving the LLM's original ordering. Each
    # tuple is ``(idx, validated_call, risk_or_empty, reason_or_empty)``.
    # ``risk_or_empty`` is non-empty only for calls that require user
    # approval — that's the signal used at proposal-build time to
    # decide whether to bundle ANY of the calls into a card.
    #
    # The ordering matters: a request like "turn off the kitchen
    # light, then unlock the door" must execute in that order after
    # the user approves, otherwise the door opens while the light is
    # still on (or worse, in a "lights off + door open" combo the
    # user explicitly wanted reversed). Bundling all REVIEW calls
    # ahead of SAFE ones would silently invert the request.
    validated_records: list[tuple[int, dict[str, Any], str, str]] = []
    # Quick-lookup mirrors used by the SAFE / REVIEW append sites.
    pending_review: list[dict[str, Any]] = []  # legacy, populated alongside
    validated_calls: list[dict[str, Any]] = []
    for idx, call in enumerate(calls):
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

        # Classify before the SAFE-bucket-only checks so REVIEW/BLOCKED
        # services don't fall into the "outside the safe allowlist"
        # rejection (which is what produced the bad UX the approval
        # flow exists to replace).
        bucket, review_entry = _classify_call(service)
        if bucket == "blocked":
            return _blocked_command_result(
                f"{service} is on the no-chat-execution list and must be run "
                f"from Home Assistant directly",
                result,
            )
        if bucket == "review" and review_entry is not None:
            # Extract the call's target ids for per-entity approval
            # resolution. Per-entity grant (``lock.unlock:lock.front``)
            # wins over wildcard (``lock.unlock``); ``is_approved`` does
            # the fallback so a v1 wildcard grant still covers each
            # individual entity here.
            _ids = _entity_ids_from_call(call)
            approved = _all_targets_approved(approval_store, service, _ids, session_id)
            if approved:
                validated, err = _validate_review_call(call, review_entry)
                if err is not None or validated is None:
                    return _blocked_command_result(err or "invalid command", result)
                validated_calls.append(validated)
                validated_records.append((idx, validated, "", ""))
                continue
            validated, err = _validate_review_call(call, review_entry)
            if err is not None or validated is None:
                return _blocked_command_result(err or "invalid command", result)
            pending_review.append(validated)
            validated_records.append(
                (idx, validated, review_entry["risk"], review_entry.get("reason", ""))
            )
            continue

        domain, service_name = service.split(".", 1)
        if domain not in _ALLOWED_COMMAND_SERVICES:
            # Defensive: _classify_call already routed unknown domains to
            # "blocked", so this branch should be unreachable. Keep the
            # original rejection here as belt-and-suspenders.
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

        safe_call = {
            "service": service,
            "target": target,
            "data": data,
        }

        # Entity-class elevation for SAFE-domain calls (cover.*): if any
        # targeted entity has a high-risk device_class (garage door /
        # gate / front door), divert the SAFE-validated call to the
        # REVIEW bucket so the user explicitly approves it. The shape
        # is already validated for the SAFE policy and is a subset of
        # the equivalent REVIEW entry's allowed keys, so no second
        # shape pass is needed here.
        cover_entry = _entity_aware_review_entry(hass, service, list(target_ids))
        if cover_entry is not None:
            approved = _all_targets_approved(approval_store, service, list(target_ids), session_id)
            if approved:
                validated_calls.append(safe_call)
                validated_records.append((idx, safe_call, "", ""))
            else:
                pending_review.append(safe_call)
                validated_records.append(
                    (idx, safe_call, cover_entry["risk"], cover_entry.get("reason", ""))
                )
            continue

        validated_calls.append(safe_call)
        validated_records.append((idx, safe_call, "", ""))

    # If any REVIEW calls are still pending approval, surface them as a
    # ``command_approval`` proposal. Pre-approved REVIEW calls and SAFE
    # calls accumulated in ``validated_calls`` would normally execute
    # immediately — but when the same turn ALSO contains an unapproved
    # REVIEW call, we hold them all back together so the user sees the
    # full set on the approval card and can decide atomically. This
    # avoids "half the request ran while you were thinking" surprises.
    #
    # ``validated_records`` is iterated in the LLM's original order so a
    # request like "turn off the kitchen light, then unlock the door"
    # ends up with ``calls = [light.turn_off, lock.unlock]`` in the
    # proposal — concatenating ``pending_review + validated_calls``
    # would have flipped them.
    if pending_review:
        validated_records.sort(key=lambda r: r[0])
        ordered_calls = [c for _, c, _, _ in validated_records]
        ordered_risks = [r for _, _, r, _ in validated_records]
        ordered_reasons = [reason for _, _, _, reason in validated_records]
        proposal = _build_approval_proposal(ordered_calls, ordered_risks, ordered_reasons)

        # Carry scheduling metadata into the proposal when the LLM
        # asked for a delayed_command. The resolver consults these
        # fields and routes through the scheduler instead of calling
        # ``hass.services.async_call`` directly, so "unlock the door
        # in 10 minutes" actually waits 10 minutes after the user
        # approves — not "approve = unlock now".
        original_intent = result.get("intent")
        if original_intent == "delayed_command":
            proposal["original_intent"] = "delayed_command"
            delay_val = result.get("delay_seconds")
            if delay_val is not None:
                proposal["delay_seconds"] = delay_val
            scheduled_time = result.get("scheduled_time")
            if scheduled_time is not None:
                proposal["scheduled_time"] = scheduled_time

        approval_result: ArchitectResponse = dict(result)
        approval_result["intent"] = "command_approval"
        approval_result["calls"] = []  # nothing executes until user approves
        approval_result["command_approval"] = proposal
        approval_result["quick_actions"] = _approval_quick_actions(proposal["proposal_id"])
        if not approval_result.get("response"):
            approval_result["response"] = approval_pending_hint(language)
        return approval_result

    result["calls"] = validated_calls
    # Generate a human-readable fallback so callers never show raw JSON (#94).
    # Only set after policy validation confirms the calls are safe. The
    # locale-aware `_build_command_confirmation` matches the user's
    # locale rather than dropping back to English when the model
    # returned a valid command without a `response` field.
    if "response" not in result:
        result["response"] = _build_command_confirmation(validated_calls, language=language)
    return result
