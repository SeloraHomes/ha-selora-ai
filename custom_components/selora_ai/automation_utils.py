from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
from math import floor
from pathlib import Path
from typing import TYPE_CHECKING, Any
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
import yaml

from .const import (
    AUTOMATION_CAP_CEILING,
    AUTOMATION_CAP_FLOOR,
    AUTOMATION_ID_PREFIX,
    AUTOMATION_STALE_DAYS,
    AUTOMATIONS_PER_DEVICE,
)

if TYPE_CHECKING:
    from .automation_store import AutomationStore
    from .types import (
        AutomationCreateResult,
        AutomationDict,
        RiskAssessment,
        StaleAutomation,
    )

_LOGGER = logging.getLogger(__name__)

# Serializes read-modify-write cycles on automations.yaml so concurrent
# requests (LLM auto-writer + chat-driven create + UI toggle + rename)
# cannot clobber each other between read and atomic-rename.
#
# Public symbol — every caller that does a read-modify-write on
# automations.yaml MUST acquire this lock for the full read→write→reload
# span. Read-only callers don't need the lock since writes are atomic
# rename, but they should not interleave their own write back.
AUTOMATIONS_YAML_LOCK = asyncio.Lock()


def suggestion_content_fingerprint(automation: AutomationDict | dict[str, Any]) -> str:
    """SHA-256 fingerprint of trigger+condition+action for suggestion deduplication.

    Handles both singular (trigger/action/condition) and plural
    (triggers/actions/conditions) key names so raw LLM output and normalized
    YAML produce the same hash.  Conditions are included because time-based
    patterns can share trigger+action but differ by weekday/weekend condition.
    """
    trigger = automation.get("trigger") or automation.get("triggers")
    condition = automation.get("condition") or automation.get("conditions")
    action = automation.get("action") or automation.get("actions")
    key = {"trigger": trigger, "condition": condition, "action": action}
    raw = json.dumps(key, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


_ELEVATED_RISK_SERVICE_DOMAINS = {
    "shell_command",
    "python_script",
    "pyscript",
    "rest_command",
    "hassio",
}
_ELEVATED_RISK_SERVICE_NAMES = {
    "script.turn_on",
    "script.toggle",
}
_ELEVATED_RISK_TRIGGER_PLATFORMS = {
    "webhook",
}
_SCRUTINY_ENTITY_DOMAINS = {
    "lock": "Access control",
    "cover": "Entry point",
    "camera": "Camera",
    "alarm_control_panel": "Security system",
    "person": "Presence",
    "device_tracker": "Presence",
    "water_heater": "Water / heat",
    "humidifier": "Water / HVAC",
    "vacuum": "Appliance",
    "media_player": "Media device",
}
_SCRUTINY_SERVICE_DOMAINS = {
    "notify": "Notification",
    "tts": "Notification",
    "persistent_notification": "Notification",
}


def _to_plain_types(obj: Any) -> Any:
    """Convert ruamel.yaml rich types (CommentedMap, CommentedSeq, etc.) to plain Python."""
    if isinstance(obj, dict):
        return {k: _to_plain_types(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_plain_types(item) for item in obj]
    return obj


def _read_automations_yaml(path: Path) -> list[AutomationDict]:
    """Read and parse automations.yaml (runs in executor).

    Uses ruamel.yaml to correctly parse double-quoted on/off/yes/no as
    strings (not booleans), then converts the result to plain Python types
    so ruamel internals never leak into HA or other serialisation paths.
    """
    from ruamel.yaml import YAML

    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8").strip()
        if not text or text == "[]":
            return []
        ryaml = YAML()
        data = ryaml.load(text)
        if isinstance(data, list):
            return _to_plain_types(data)
    except Exception as exc:
        _LOGGER.error("Error reading automations.yaml: %s", exc)
    return []


def _quote_yaml_booleans(automations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Quote string values that YAML 1.1 would silently reinterpret.

    YAML 1.1 treats bare on/off/yes/no/true/false as booleans and
    bare HH:MM:SS patterns as sexagesimal integers (e.g. ``23:46:00``
    becomes ``85560``).  This walks the entire automation tree and wraps
    any such value in ``DoubleQuotedScalarString`` so ruamel.yaml emits
    them with double-quotes, surviving a PyYAML round-trip as strings.
    """
    import re

    from ruamel.yaml.scalarstring import DoubleQuotedScalarString

    _YAML_BOOL_STRINGS = frozenset({"true", "false", "yes", "no", "on", "off", "y", "n"})
    _SEXAGESIMAL_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")

    # Keys whose boolean values are intentional (not state strings)
    _BOOL_KEYS = frozenset({"initial_state", "enabled", "hide_entity"})

    def _walk(obj: Any, key: str | None = None) -> Any:
        if isinstance(obj, dict):
            for k, v in obj.items():
                obj[k] = _walk(v, k)
            return obj
        if isinstance(obj, list):
            for i, v in enumerate(obj):
                obj[i] = _walk(v)
            return obj
        if isinstance(obj, bool) and key not in _BOOL_KEYS:
            return DoubleQuotedScalarString("on" if obj else "off")
        if isinstance(obj, str) and (
            obj.lower() in _YAML_BOOL_STRINGS or _SEXAGESIMAL_RE.match(obj)
        ):
            return DoubleQuotedScalarString(obj)
        return obj

    for auto in automations:
        _walk(auto)
    return automations


def _write_automations_yaml(path: Path, automations: list[dict[str, Any]]) -> None:
    """Write automations list to YAML atomically, preserving formatting."""
    from ruamel.yaml import YAML

    _quote_yaml_booleans(automations)

    ryaml = YAML()
    ryaml.default_flow_style = False
    ryaml.allow_unicode = True
    tmp_path = path.with_suffix(".yaml.tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        ryaml.dump(automations, fh)
    tmp_path.replace(path)


def _parse_automation_yaml(yaml_text: str) -> AutomationDict | None:
    """Parse a YAML string into an automation dict (runs in executor). Returns None on error."""
    try:
        data = yaml.safe_load(yaml_text)
        if isinstance(data, dict):
            return data
    except yaml.YAMLError as exc:
        _LOGGER.error("YAML parse error: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Pattern-based value coercion
#
# Instead of hardcoding which fields need fixing, we detect the *type* of
# mistake and fix it based on context.  This catches LLM errors on any
# field -- even ones we haven't seen yet -- as long as the mistake fits
# a known pattern (wrong type for the context).
# ---------------------------------------------------------------------------

# Keys where HA expects a time string ("HH:MM:SS").
_TIME_KEYS = frozenset({"at", "after", "before"})

# Keys where HA expects a duration (dict or "HH:MM:SS").
_DURATION_KEYS = frozenset({"for", "delay"})

# Keys where HA expects a state string ("on"/"off"/"home"/etc.).
_STATE_KEYS = frozenset({"to", "from", "state"})

# Keys where boolean values are intentional (not state strings).
_BOOL_KEYS = frozenset({"initial_state", "enabled", "hide_entity", "continue_on_error"})


def _coerce_time_value(value: Any) -> str | None:
    """Coerce a value to ``HH:MM:SS`` time string.

    - Integers/floats in 0..86399 are treated as seconds since midnight.
    - Out-of-range numbers are stringified as a fallback.
    - Strings pass through unchanged.
    - ``None`` is returned as ``None`` (caller should remove the key).
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return None  # bool is nonsensical for time; drop it
    if isinstance(value, (int, float)):
        total = int(value)
        if total < 0 or total >= 86400:
            return str(value)
        hours = total // 3600
        minutes = (total % 3600) // 60
        seconds = total % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return str(value)


def _coerce_duration_value(value: Any) -> Any:
    """Coerce a raw number to a duration dict ``{"seconds": N}``."""
    if isinstance(value, bool):
        return value  # don't misinterpret booleans
    if isinstance(value, (int, float)):
        return {"seconds": int(value)}
    return value


def _coerce_state_string(value: Any) -> str | None:
    """Coerce a value that HA expects to be a state string.

    - Booleans become ``"on"``/``"off"``.
    - Other non-strings are stringified.
    - ``None`` is returned as ``None`` (caller should remove the key).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "on" if value else "off"
    if not isinstance(value, str):
        return str(value)
    return value


def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    """Apply pattern-based coercion to a single trigger or condition dict.

    Detection is by *key name* and *value type*, not by which section
    the item lives in.  This lets us catch the same class of mistake
    in triggers, conditions, or any future HA automation section.
    """
    fixed = dict(item)

    for key in list(fixed.keys()):
        if key in _BOOL_KEYS:
            continue  # intentional boolean -- leave it alone

        val = fixed[key]

        # --- Time keys: integers are seconds-since-midnight ----------------
        if key in _TIME_KEYS:
            result = _coerce_time_value(val)
            if result is None:
                fixed.pop(key, None)
            else:
                fixed[key] = result
            continue

        # --- Duration keys: raw numbers -> {"seconds": N} -----------------
        if key in _DURATION_KEYS:
            fixed[key] = _coerce_duration_value(val)
            continue

        # --- State keys: must always be strings ----------------------------
        if key in _STATE_KEYS:
            result = _coerce_state_string(val)
            if result is None:
                fixed.pop(key, None)
            else:
                fixed[key] = result
            continue

    return fixed


def _collect_referenced_entity_ids(
    automation: AutomationDict | dict[str, Any],
) -> set[str]:
    """Walk an automation payload and return every ``entity_id`` it references
    *as a state-machine entity*.

    Only descends into the contexts where HA actually expects an entity
    reference: trigger top-level ``entity_id`` (state / numeric_state / zone),
    condition ``entity_id`` and nested ``conditions``, and action
    ``target.entity_id`` plus the deprecated top-level / ``data.entity_id``
    service-call forms. Action control-flow blocks (``choose``, ``if`` /
    ``then`` / ``else``, ``parallel``, ``sequence``, ``repeat``) are traversed
    recursively.

    Integration-defined match payloads are **not** walked — most notably
    ``event_data`` (trigger filter), ``event_data_template``, ``payload`` (MQTT
    triggers), and ``webhook_id``. A field named ``entity_id`` inside those
    blocks is application data, not an HA entity, and must not be looked up.

    Comma-separated strings ("light.a, light.b") are split before being added.
    Templates and IDs without a domain are excluded from the result.
    """
    referenced: set[str] = set()

    def _as_list(value: Any) -> list[Any]:
        # HA accepts the singular dict form (``action: {...}``,
        # ``wait_for_trigger: {...}``) as well as lists. Without coercion the
        # loops below would iterate dict keys and miss every entity reference
        # in the section.
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            return value
        return []

    def _add(value: Any) -> None:
        if isinstance(value, str):
            # HA accepts the legacy comma-separated shorthand
            # ("light.kitchen, light.dining"). Split before lookup so each
            # ID is checked individually rather than the joined literal.
            for part in value.split(","):
                stripped = part.strip()
                if stripped:
                    referenced.add(stripped)
        elif isinstance(value, list):
            for item in value:
                _add(item)

    def _walk_trigger(trigger: Any) -> None:
        if isinstance(trigger, dict):
            _add(trigger.get("entity_id"))

    def _walk_condition(condition: Any) -> None:
        if isinstance(condition, dict):
            _add(condition.get("entity_id"))
            # HA accepts the singular dict form for an and/or/not group's
            # ``conditions`` field — use _as_list so we don't iterate the
            # nested dict's keys and miss every entity inside it.
            for nested in _as_list(condition.get("conditions")):
                _walk_condition(nested)
            # Shorthand logical syntax: ``{or: [...]}`` / ``{and: [...]}`` /
            # ``{not: [...]}`` instead of ``{condition: or, conditions:
            # [...]}``. Without this branch, nested entities under the
            # shorthand form would be silently skipped.
            for key in ("and", "or", "not"):
                for nested in _as_list(condition.get(key)):
                    _walk_condition(nested)

    def _walk_action(action: Any) -> None:
        if not isinstance(action, dict):
            return
        # HA accepts a condition block as an action step (acts as an
        # inline guard inside the sequence), e.g.
        # ``{condition: state, entity_id: lock.front_door, state: locked}``
        # or the shorthand ``{or: [...]}`` form. Delegate to
        # _walk_condition so nested entity references in those shapes are
        # validated and risk-tagged. The call is a no-op for normal
        # service-call actions because they don't carry condition-specific
        # keys (and the top-level ``entity_id`` add below is idempotent).
        _walk_condition(action)

        # Service call: target.entity_id is the canonical form. The bare
        # top-level entity_id and data.entity_id forms are deprecated but
        # still accepted by HA, so treat them as real entity refs too.
        _add(action.get("entity_id"))
        target = action.get("target")
        if isinstance(target, dict):
            _add(target.get("entity_id"))
        data = action.get("data")
        if isinstance(data, dict):
            _add(data.get("entity_id"))

        # wait_for_trigger embeds trigger dicts (each with its own
        # platform/entity_id) inside an action. Without this the entity
        # walker misses references like binary_sensor.motion in
        # ``action: [{wait_for_trigger: [{platform: state, entity_id: ...}]}]``.
        for trigger in _as_list(action.get("wait_for_trigger")):
            _walk_trigger(trigger)

        # Control-flow descent.
        for key in ("sequence", "then", "else", "default", "parallel"):
            for nested in _as_list(action.get(key)):
                _walk_action(nested)
        for cond in _as_list(action.get("if")):
            _walk_condition(cond)
        for branch in _as_list(action.get("choose")):
            if not isinstance(branch, dict):
                continue
            for cond in _as_list(branch.get("conditions")):
                _walk_condition(cond)
            for nested in _as_list(branch.get("sequence")):
                _walk_action(nested)
        repeat = action.get("repeat")
        if isinstance(repeat, dict):
            for nested in _as_list(repeat.get("sequence")):
                _walk_action(nested)
            for key in ("while", "until"):
                for cond in _as_list(repeat.get(key)):
                    _walk_condition(cond)

    for trigger in _as_list(automation.get("trigger") or automation.get("triggers")):
        _walk_trigger(trigger)
    for condition in _as_list(automation.get("condition") or automation.get("conditions")):
        _walk_condition(condition)
    for action in _as_list(automation.get("action") or automation.get("actions")):
        _walk_action(action)

    return {eid for eid in referenced if "." in eid and "{{" not in eid and "{%" not in eid}


def _find_unknown_entity_ids(
    hass: HomeAssistant,
    entity_ids: set[str],
) -> list[str]:
    """Return the subset of ``entity_ids`` not known to Home Assistant.

    An entity is considered known if it has a current state in
    :attr:`hass.states` **or** is present in the entity registry (covers
    disabled / unavailable entities that exist but haven't surfaced a
    state). Templates and malformed IDs are filtered upstream.

    **Timing caveat**: like :func:`validate_action_services`, this relies on
    the state machine and entity registry being populated. Callers running
    very early in boot may see false negatives; the collector path already
    waits for HA to finish loading.
    """
    if not entity_ids:
        return []
    entity_reg = er.async_get(hass)
    unknown: list[str] = []
    for eid in entity_ids:
        if hass.states.get(eid) is not None:
            continue
        if entity_reg.async_get(eid) is not None:
            continue
        unknown.append(eid)
    return sorted(unknown)


def validate_action_services(
    hass: HomeAssistant,
    automation: AutomationDict | dict[str, Any],
) -> bool:
    """Check that all action services in the automation exist on this HA instance.

    Returns ``True`` if every service is available (or the automation has no
    service calls).  Returns ``False`` if any ``domain.service`` is missing.

    **Timing caveat**: HA loads services lazily.  If called too early in
    the boot sequence, legitimate services may not yet be registered and
    this function will return a false negative.  Callers should only use
    this after integrations have finished loading (e.g. not in the
    collector's initial cycle).
    """
    actions = automation.get("action") or automation.get("actions") or []
    if not isinstance(actions, list):
        actions = [actions]

    for act in actions:
        if not isinstance(act, dict):
            continue
        service = act.get("action") or act.get("service")
        if not service or not isinstance(service, str):
            continue
        parts = service.split(".", 1)
        if len(parts) != 2:
            _LOGGER.warning(
                "Automation '%s' has malformed service: %s",
                automation.get("alias", "<no alias>"),
                service,
            )
            return False
        domain, service_name = parts
        if not hass.services.has_service(domain, service_name):
            _LOGGER.warning(
                "Automation '%s' uses non-existent service: %s",
                automation.get("alias", "<no alias>"),
                service,
            )
            return False
    return True


def validate_automation_payload(
    automation: dict[str, Any] | None,
    hass: HomeAssistant | None = None,
) -> tuple[bool, str, AutomationDict | None]:
    """Validate and normalize automation payload before it is shown or persisted.

    When *hass* is provided, action domains are validated against the HA
    service registry (rejects domains with no registered services).  When
    *hass* is ``None`` the domain check is skipped — callers should rely on
    :func:`validate_action_services` for runtime verification.
    """
    if not isinstance(automation, dict):
        return False, "automation payload must be an object", None

    alias = str(automation.get("alias", "")).strip()
    if not alias:
        return False, "automation alias is required", None

    triggers = automation.get("trigger") or automation.get("triggers") or []
    actions = automation.get("action") or automation.get("actions") or []
    conditions = automation.get("condition") or automation.get("conditions") or []

    if not isinstance(triggers, list):
        triggers = [triggers]
    if not isinstance(actions, list):
        actions = [actions]
    if conditions and not isinstance(conditions, list):
        conditions = [conditions]

    if not triggers:
        return False, "automation must include at least one trigger", None
    if not actions:
        return False, "automation must include at least one action", None

    if not all(isinstance(t, dict) for t in triggers):
        return False, "all triggers must be objects", None
    if not all(isinstance(a, dict) for a in actions):
        return False, "all actions must be objects", None
    if not all(isinstance(c, dict) for c in conditions):
        return False, "all conditions must be objects", None

    # --- Trigger normalization ---------------------------------------------
    normalized_triggers: list[dict[str, Any]] = []
    for trig in triggers:
        fixed = _normalize_item(trig)
        # Fix LLM using "trigger" key instead of "platform"
        if not fixed.get("platform") and fixed.get("trigger"):
            fixed["platform"] = fixed.pop("trigger")
        if not fixed.get("platform"):
            return False, "each trigger must include a platform", None
        # Remove None-valued to/from (LLM sometimes emits explicit nulls)
        for key in ("to", "from"):
            if key in fixed and fixed[key] is None:
                fixed.pop(key)
        normalized_triggers.append(fixed)

    # --- Condition normalization -------------------------------------------
    normalized_conditions: list[dict[str, Any]] = []
    for cond in conditions:
        normalized_conditions.append(_normalize_item(cond))

    # --- Action normalization ----------------------------------------------
    # When hass is available, validate actions against the service registry:
    # 1. The action's domain.service must exist (rejects e.g. binary_sensor.turn_on).
    # 2. Target entity domains must have *some* service registered — this catches
    #    cross-domain calls like homeassistant.turn_on targeting binary_sensor.motion
    #    (binary_sensor has no services at all, so it's read-only).
    normalized_actions: list[dict[str, Any]] = []
    for act in actions:
        norm_act = _normalize_item(act)
        if hass is not None:
            action_service = str(norm_act.get("action", norm_act.get("service", "")))
            if "." in action_service and "{{" not in action_service:
                svc_domain, svc_name = action_service.split(".", 1)
                if svc_domain and not hass.services.has_service(svc_domain, svc_name):
                    return (
                        False,
                        f"action uses non-existent service '{action_service}'",
                        None,
                    )
            # Reject targets in read-only domains (no services registered at all)
            target = norm_act.get("target", {})
            entity_ids = target.get("entity_id", "") if isinstance(target, dict) else ""
            if isinstance(entity_ids, str):
                entity_ids = [entity_ids] if entity_ids else []
            for eid in entity_ids:
                if "{{" in eid:
                    continue
                eid_domain = eid.split(".")[0] if "." in eid else ""
                if eid_domain and not hass.services.async_services_for_domain(eid_domain):
                    return (
                        False,
                        f"action targets read-only domain '{eid_domain}' ({eid})",
                        None,
                    )
        normalized_actions.append(norm_act)

    # --- Entity reference check --------------------------------------------
    # Reject payloads that reference entity_ids the running HA instance does
    # not know about. The LLM occasionally hallucinates entity names (e.g.
    # script.expose_to_siri when no such script exists, or a stale
    # automation.* reference). Without this gate the broken automation gets
    # written to automations.yaml and surfaces as "unavailable" only after
    # reload.
    if hass is not None:
        payload_for_walk: dict[str, Any] = {
            "triggers": normalized_triggers,
            "conditions": normalized_conditions,
            "actions": normalized_actions,
        }
        unknown = _find_unknown_entity_ids(hass, _collect_referenced_entity_ids(payload_for_walk))
        if unknown:
            preview = ", ".join(unknown[:3])
            suffix = f" (+{len(unknown) - 3} more)" if len(unknown) > 3 else ""
            return (
                False,
                f"automation references unknown entity_id(s): {preview}{suffix}",
                None,
            )

    _VALID_MODES = {"single", "restart", "queued", "parallel"}
    mode = str(automation.get("mode", "single")).strip().lower()
    if mode not in _VALID_MODES:
        mode = "single"

    raw_initial_state = automation.get("initial_state")
    initial_state = raw_initial_state if isinstance(raw_initial_state, bool) else None

    normalized: dict[str, Any] = {
        "alias": alias,
        "description": str(automation.get("description", "")).strip(),
        "triggers": normalized_triggers,
        "conditions": normalized_conditions,
        "actions": normalized_actions,
        "mode": mode,
    }
    if initial_state is not None:
        normalized["initial_state"] = initial_state

    try:
        yaml_text = yaml.safe_dump(normalized, allow_unicode=True, default_flow_style=False)
        reparsed = yaml.safe_load(yaml_text)
        if not isinstance(reparsed, dict):
            return False, "automation YAML did not round-trip to an object", None
    except (yaml.YAMLError, TypeError, ValueError) as exc:
        return False, f"automation YAML serialization failed: {exc}", None

    return True, "", normalized


def _iter_service_actions(actions: Any) -> Iterator[dict[str, Any]]:
    """Yield every service-call action dict in a possibly-nested HA action tree.

    HA automations can wrap service calls inside control-flow primitives:
    ``choose: [{conditions, sequence}]``, ``if/then/else``, ``parallel``,
    ``repeat.sequence``, ``repeat.while``, ``repeat.until``, and bare
    ``sequence`` blocks. A safe-looking top-level ``choose`` could hide a
    ``shell_command.*`` call deeper down — without recursion the risk gate
    would miss it and let the LLM smuggle elevated-risk primitives past
    the enabled-on-create check.

    Only descends through known action-container keys, so service-call
    data payloads (e.g. ``data: {action: "do_x"}`` for services that
    happen to take an ``action`` field) are not traversed.
    """
    if isinstance(actions, dict):
        actions = [actions]
    if not isinstance(actions, list):
        return
    for action in actions:
        if not isinstance(action, dict):
            continue
        yield action
        choose = action.get("choose")
        if isinstance(choose, list):
            for branch in choose:
                if isinstance(branch, dict):
                    yield from _iter_service_actions(branch.get("sequence"))
        for nested_key in ("sequence", "then", "else", "default", "parallel"):
            if nested_key in action:
                yield from _iter_service_actions(action[nested_key])
        repeat = action.get("repeat")
        if isinstance(repeat, dict):
            yield from _iter_service_actions(repeat.get("sequence"))


def assess_automation_risk(automation: AutomationDict | dict[str, Any]) -> RiskAssessment:
    """Classify automation proposals that could expand HA compute/control risk."""
    flags: list[str] = []
    reasons: list[str] = []
    scrutiny_tags: list[str] = []

    triggers = automation.get("trigger") or automation.get("triggers") or []
    actions = automation.get("action") or automation.get("actions") or []

    if not isinstance(triggers, list):
        triggers = [triggers]
    if not isinstance(actions, list):
        actions = [actions]

    referenced_entity_ids = _collect_referenced_entity_ids(automation)

    for trigger in triggers:
        if not isinstance(trigger, dict):
            continue
        platform = str(trigger.get("platform") or trigger.get("trigger") or "").strip()
        if platform in _ELEVATED_RISK_TRIGGER_PLATFORMS:
            flags.append("remote_ingress_trigger")
            reasons.append("uses a webhook trigger, which creates a remotely invokable entry point")

    # Walk nested action containers (choose/if/parallel/repeat/sequence) so
    # services hidden inside control-flow blocks are still classified.
    for action in _iter_service_actions(actions):
        service = str(action.get("service") or action.get("action") or "").strip()
        if not service:
            continue
        domain = service.split(".", 1)[0]

        if domain in _ELEVATED_RISK_SERVICE_DOMAINS:
            flags.append("compute_capability")
            reasons.append(
                f"calls {service}, which can execute code, invoke add-ons, or reach external systems"
            )
            continue

        if service in _ELEVATED_RISK_SERVICE_NAMES:
            flags.append("indirect_execution")
            reasons.append(
                f"calls {service}, which can delegate execution to pre-existing HA scripts"
            )
            continue

        if domain in _SCRUTINY_SERVICE_DOMAINS:
            scrutiny_tags.append(_SCRUTINY_SERVICE_DOMAINS[domain])

    for entity_id in referenced_entity_ids:
        domain = entity_id.split(".", 1)[0]
        if domain in _SCRUTINY_ENTITY_DOMAINS:
            scrutiny_tags.append(_SCRUTINY_ENTITY_DOMAINS[domain])

    unique_flags = sorted(set(flags))
    unique_reasons = []
    for reason in reasons:
        if reason not in unique_reasons:
            unique_reasons.append(reason)
    unique_scrutiny_tags = sorted(set(scrutiny_tags))

    if unique_flags:
        summary = (
            "Elevated risk: this automation uses execution or ingress primitives that could "
            "expand Home Assistant control or compute exposure."
        )
        return {
            "level": "elevated",
            "flags": unique_flags,
            "summary": summary,
            "reasons": unique_reasons,
            "scrutiny_tags": unique_scrutiny_tags,
        }

    return {
        "level": "normal",
        "flags": [],
        "summary": "",
        "reasons": [],
        "scrutiny_tags": unique_scrutiny_tags,
    }


def _get_automation_store(hass: HomeAssistant) -> AutomationStore:
    """Return (or lazily create) the AutomationStore from hass.data.

    .. deprecated:: Use :func:`helpers.get_automation_store` instead.
       Kept as a module-level alias for backward compatibility.
    """
    from .helpers import get_automation_store

    return get_automation_store(hass)


async def async_update_automation(
    hass: HomeAssistant,
    automation_id: str,
    updated: dict[str, Any],
    *,
    session_id: str | None = None,
    version_message: str = "Updated via YAML editor",
) -> bool:
    """Replace an existing automation (by id) in automations.yaml and reload."""
    # Validate and normalize trigger values (coerces boolean to/from → "on"/"off")
    is_valid, reason, normalized = validate_automation_payload(updated, hass)
    if not is_valid or normalized is None:
        _LOGGER.error("Invalid automation update for %s: %s", automation_id, reason)
        return False

    # Merge normalized trigger/action/condition back using plural keys (HA 2024+)
    updated["triggers"] = normalized["triggers"]
    updated["actions"] = normalized["actions"]
    updated["conditions"] = normalized.get("conditions", [])
    # Remove old singular keys if present
    updated.pop("trigger", None)
    updated.pop("action", None)
    updated.pop("condition", None)

    automations_path = Path(hass.config.config_dir) / "automations.yaml"
    async with AUTOMATIONS_YAML_LOCK:
        existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)

        found = False
        for i, a in enumerate(existing):
            if a.get("id") == automation_id:
                # Preserve the original id and keep initial_state from existing unless overridden
                updated.setdefault("id", automation_id)
                updated.setdefault("initial_state", a.get("initial_state", False))
                existing[i] = updated
                found = True
                break

        if not found:
            _LOGGER.error("Automation id %s not found in automations.yaml", automation_id)
            return False

        try:
            await hass.async_add_executor_job(_write_automations_yaml, automations_path, existing)
            _LOGGER.info("Updated automation: %s", automation_id)
            await hass.services.async_call("automation", "reload", blocking=True)

            # Record version
            yaml_text = yaml.dump(updated, allow_unicode=True, default_flow_style=False)
            store = _get_automation_store(hass)
            await store.add_version(automation_id, yaml_text, updated, version_message, session_id)

            return True
        except Exception as exc:
            _LOGGER.exception("Failed to update automation: %s", exc)
            return False


async def async_create_automation(
    hass: HomeAssistant,
    suggestion: AutomationDict | dict[str, Any],
    *,
    session_id: str | None = None,
    version_message: str = "Created",
    enabled: bool = False,
) -> AutomationCreateResult:
    """Write a single automation suggestion to automations.yaml and reload.

    New automations are written **disabled** by default. Callers that need an
    automation to be active immediately (UI quick-create, scheduled one-shots,
    explicit MCP enabled=True) must pass ``enabled=True``. Any
    ``initial_state`` supplied inside ``suggestion`` is ignored — this prevents
    a prompt-injected LLM payload from smuggling an enabled flag past the
    user-confirmation step.

    Elevated-risk automations (compute capability, remote ingress, indirect
    execution — see :func:`assess_automation_risk`) are always written
    disabled, even when ``enabled=True`` is requested. The ``forced_disabled``
    flag in the result lets the caller surface this to the user.
    """
    automations_path = Path(hass.config.config_dir) / "automations.yaml"

    # Validate and normalize the suggestion (coerces boolean to/from → "on"/"off", etc.)
    is_valid, reason, normalized = validate_automation_payload(suggestion, hass)
    if not is_valid or normalized is None:
        _LOGGER.error("Invalid automation suggestion: %s", reason)
        return {"success": False, "automation_id": None}

    alias = normalized["alias"]
    triggers = normalized["triggers"]
    actions = normalized["actions"]
    conditions = normalized.get("conditions", [])

    risk = assess_automation_risk(normalized)
    forced_disabled = False
    if enabled and risk.get("level") == "elevated":
        _LOGGER.warning(
            "Forcing initial_state=False for elevated-risk automation '%s' (flags=%s)",
            alias,
            risk.get("flags"),
        )
        enabled = False
        forced_disabled = True

    short_id = uuid.uuid4().hex[:8]
    automation_id = f"{AUTOMATION_ID_PREFIX}{short_id}"
    automation = {
        "id": automation_id,
        "alias": alias,
        "description": f"[Selora AI] {suggestion.get('description', alias)}",
        "initial_state": enabled,
        "triggers": triggers,
        "conditions": conditions or [],
        "actions": actions,
        "mode": normalized.get("mode", "single"),
    }

    async with AUTOMATIONS_YAML_LOCK:
        existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)
        existing.append(automation)

        try:
            await hass.async_add_executor_job(_write_automations_yaml, automations_path, existing)
            _LOGGER.info("Created new automation: %s", alias)

            # Reload HA automations (blocking so entities exist before we return)
            await hass.services.async_call("automation", "reload", blocking=True)

            # Record first version
            yaml_text = yaml.dump(automation, allow_unicode=True, default_flow_style=False)
            store = _get_automation_store(hass)
            await store.add_version(
                automation_id, yaml_text, automation, version_message, session_id
            )

            return {
                "success": True,
                "automation_id": automation_id,
                "risk_level": risk.get("level", "normal"),
                "forced_disabled": forced_disabled,
            }
        except Exception as exc:
            _LOGGER.exception("Failed to create automation: %s", exc)
            return {"success": False, "automation_id": None}


async def async_toggle_automation(
    hass: HomeAssistant,
    automation_id: str,
    entity_id: str,
    enable: bool,
) -> bool:
    """Toggle an automation's initial_state in automations.yaml and apply at runtime.

    This persists the enabled/disabled state so it survives HA restarts.
    """
    automations_path = Path(hass.config.config_dir) / "automations.yaml"
    async with AUTOMATIONS_YAML_LOCK:
        existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)

        found = False
        for automation in existing:
            if automation.get("id") == automation_id:
                automation["initial_state"] = enable
                found = True
                break

        if not found:
            _LOGGER.error(
                "Automation id %s not found in automations.yaml for toggle", automation_id
            )
            return False

        try:
            await hass.async_add_executor_job(_write_automations_yaml, automations_path, existing)
            service = "turn_on" if enable else "turn_off"
            await hass.services.async_call("automation", service, {"entity_id": entity_id})
            _LOGGER.info(
                "Toggled automation %s to %s",
                automation_id,
                "enabled" if enable else "disabled",
            )
            return True
        except Exception as exc:
            _LOGGER.exception("Failed to toggle automation %s: %s", automation_id, exc)
            return False


async def async_delete_automation(hass: HomeAssistant, automation_id: str) -> bool:
    """Permanently delete an automation from automations.yaml and the store.

    Records the automation's trigger/action content hash in PatternStore so the
    collector will not re-suggest a similar automation.
    """
    automations_path = Path(hass.config.config_dir) / "automations.yaml"
    async with AUTOMATIONS_YAML_LOCK:
        existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)

        target = next((a for a in existing if a.get("id") == automation_id), None)
        if target is None:
            _LOGGER.error("Automation id %s not found in automations.yaml", automation_id)
            return False

        remaining = [a for a in existing if a.get("id") != automation_id]

        try:
            # Record the content hash before deleting so the LLM won't re-suggest it
            await _record_deletion_hash(hass, target)

            await hass.async_add_executor_job(_write_automations_yaml, automations_path, remaining)
            await hass.services.async_call("automation", "reload", blocking=True)
            store = _get_automation_store(hass)
            await store.purge_record(automation_id)

            from homeassistant.helpers import entity_registry as er

            entity_reg = er.async_get(hass)
            for entity in list(entity_reg.entities.values()):
                if entity.platform != "automation":
                    continue
                if entity.unique_id == automation_id:
                    entity_reg.async_remove(entity.entity_id)
                    break

            alias = target.get("alias", automation_id)
            _LOGGER.info("Deleted automation: %s (%s)", alias, automation_id)
            return True
        except Exception as exc:
            _LOGGER.exception("Failed to delete automation %s: %s", automation_id, exc)
            return False


async def async_delete_automations_batch(
    hass: HomeAssistant, automation_ids: list[str]
) -> list[str]:
    """Delete multiple automations in a single read/write/reload cycle.

    Returns the list of aliases that were successfully removed.
    """
    if not automation_ids:
        return []

    ids_to_remove = set(automation_ids)
    automations_path = Path(hass.config.config_dir) / "automations.yaml"
    async with AUTOMATIONS_YAML_LOCK:
        existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)

        targets = [a for a in existing if a.get("id") in ids_to_remove]
        if not targets:
            return []

        remaining = [a for a in existing if a.get("id") not in ids_to_remove]
        removed_aliases: list[str] = []

        # Record deletion hashes before removing
        for target in targets:
            await _record_deletion_hash(hass, target)

        try:
            await hass.async_add_executor_job(_write_automations_yaml, automations_path, remaining)
        except (OSError, yaml.YAMLError) as exc:
            _LOGGER.exception("Failed to write automations YAML during batch delete: %s", exc)
            return removed_aliases

        try:
            await hass.services.async_call("automation", "reload", blocking=True)
        except Exception:
            _LOGGER.warning("automation.reload failed after batch delete; states may be stale")

        store = _get_automation_store(hass)
        entity_reg = er.async_get(hass)

        for target in targets:
            aid = target.get("id", "")
            try:
                await store.purge_record(aid)
            except Exception:
                _LOGGER.warning("Failed to purge store record for %s", aid)
            for entity in list(entity_reg.entities.values()):
                if entity.platform == "automation" and entity.unique_id == aid:
                    entity_reg.async_remove(entity.entity_id)
                    break
            removed_aliases.append(target.get("alias", aid))

        _LOGGER.info("Batch-deleted %d automations", len(removed_aliases))
        return removed_aliases


async def _record_deletion_hash(hass: HomeAssistant, automation: dict[str, Any]) -> None:
    """Store the trigger+action content hash of a deleted automation in PatternStore."""
    import hashlib
    import json

    trigger = automation.get("trigger") or automation.get("triggers")
    action = automation.get("action") or automation.get("actions")
    key = {"trigger": trigger, "action": action}
    raw = json.dumps(key, sort_keys=True, default=str)
    content_hash = hashlib.sha256(raw.encode()).hexdigest()
    alias = str(automation.get("alias", ""))

    try:
        from .pattern_store import PatternStore

        store = PatternStore(hass)
        await store.record_deleted_automation(content_hash, alias)
        _LOGGER.debug("Recorded deletion hash for '%s': %s", alias, content_hash[:12])
    except Exception:
        _LOGGER.warning("Failed to record deletion hash for '%s'", alias)


async def async_cleanup_orphaned_entities(hass: HomeAssistant) -> list[str]:
    """Remove orphaned Selora entity registry entries on startup.

    Finds automation entities whose unique_id starts with the Selora prefix
    but have no matching entry in automations.yaml. This is always safe —
    it only removes entity registrations for automations whose YAML was
    already deleted.

    Returns the list of removed entity unique_ids.
    """
    from homeassistant.helpers import entity_registry as er

    automations_path = Path(hass.config.config_dir) / "automations.yaml"
    existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)

    # If the file exists and has non-trivial content but parsed as empty,
    # it's likely a YAML error — bail out to avoid deleting valid entities.
    if not existing and automations_path.exists():
        try:
            raw = await hass.async_add_executor_job(automations_path.read_text, "utf-8")
            raw = raw.strip()
        except OSError:
            raw = ""
        if raw and raw != "[]":
            _LOGGER.warning("Skipping orphan cleanup: automations.yaml may be unreadable")
            return []

    yaml_ids = {str(a.get("id", "")) for a in existing}

    entity_reg = er.async_get(hass)
    orphaned: list[str] = []

    for entity in list(entity_reg.entities.values()):
        if entity.platform != "automation":
            continue
        uid = entity.unique_id or ""
        if not uid.startswith(AUTOMATION_ID_PREFIX):
            continue
        if uid not in yaml_ids:
            orphaned.append(uid)
            entity_reg.async_remove(entity.entity_id)

    if orphaned:
        _LOGGER.info(
            "Removed %d orphaned Selora entity registry entries: %s",
            len(orphaned),
            orphaned,
        )

    return orphaned


def get_selora_automation_cap(hass: HomeAssistant) -> int:
    """Return the dynamic cap on background-suggested automations.

    Cap = clamp(floor(AUTOMATIONS_PER_DEVICE × devices_with_entities), FLOOR, CEILING).
    Only counts devices that have at least one entity (skips infrastructure
    devices like coordinators, supervisors, etc.).
    """
    device_reg = dr.async_get(hass)
    entity_reg = er.async_get(hass)

    devices_with_entities: set[str] = set()
    for entry in entity_reg.entities.values():
        if entry.device_id:
            devices_with_entities.add(entry.device_id)

    device_count = len(devices_with_entities & set(device_reg.devices))
    raw = floor(AUTOMATIONS_PER_DEVICE * device_count)
    return max(AUTOMATION_CAP_FLOOR, min(raw, AUTOMATION_CAP_CEILING))


def count_selora_automations(hass: HomeAssistant, *, enabled_only: bool = False) -> int:
    """Count existing Selora-created automations via HA state machine.

    When enabled_only is True, only automations in the "on" state are counted.
    """
    count = 0
    for state in hass.states.async_all("automation"):
        uid = state.attributes.get("id", "")
        if not str(uid).startswith(AUTOMATION_ID_PREFIX):
            continue
        if enabled_only and state.state != "on":
            continue
        count += 1
    return count


def find_stale_automations(hass: HomeAssistant) -> list[StaleAutomation]:
    """Find Selora automations that haven't triggered in AUTOMATION_STALE_DAYS.

    Only considers enabled automations — disabled automations are skipped
    because they can never trigger and shouldn't be flagged as stale.

    Returns a list of dicts with automation_id, entity_id, alias, and
    last_triggered for each stale automation.
    """
    cutoff = datetime.now(UTC) - timedelta(days=AUTOMATION_STALE_DAYS)
    stale: list[StaleAutomation] = []

    for state in hass.states.async_all("automation"):
        uid = str(state.attributes.get("id", ""))
        if not uid.startswith(AUTOMATION_ID_PREFIX):
            continue

        # Skip disabled automations — they can't trigger
        if state.state != "on":
            continue

        last_triggered = state.attributes.get("last_triggered")
        if last_triggered is None:
            # Enabled but never triggered — only flag if the automation has
            # existed long enough (use last_updated as a proxy for creation time)
            last_updated = state.last_updated
            if not isinstance(last_updated, datetime):
                _LOGGER.warning(
                    "Unexpected last_updated type for %s: %r",
                    state.entity_id,
                    last_updated,
                )
                continue
            if last_updated >= cutoff:
                continue
            stale.append(
                {
                    "automation_id": uid,
                    "entity_id": state.entity_id,
                    "alias": state.attributes.get("friendly_name", uid),
                    "last_triggered": None,
                }
            )
            continue

        if isinstance(last_triggered, str):
            try:
                last_triggered = datetime.fromisoformat(last_triggered)
            except ValueError:
                _LOGGER.warning(
                    "Unparseable last_triggered for %s: %s",
                    state.entity_id,
                    state.attributes.get("last_triggered"),
                )
                continue
        elif not isinstance(last_triggered, datetime):
            _LOGGER.warning(
                "Unexpected last_triggered type for %s: %r",
                state.entity_id,
                last_triggered,
            )
            continue

        if hasattr(last_triggered, "tzinfo") and last_triggered.tzinfo is None:
            last_triggered = last_triggered.replace(tzinfo=UTC)

        if last_triggered < cutoff:
            stale.append(
                {
                    "automation_id": uid,
                    "entity_id": state.entity_id,
                    "alias": state.attributes.get("friendly_name", uid),
                    "last_triggered": last_triggered.isoformat(),
                }
            )

    return stale
