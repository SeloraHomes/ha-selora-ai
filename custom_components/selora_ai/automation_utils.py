from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
from math import floor
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any
import urllib.parse
import uuid

from homeassistant.const import EVENT_STATE_CHANGED, STATE_OFF, STATE_ON
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
import yaml

from .const import (
    AUTOMATION_CAP_CEILING,
    AUTOMATION_CAP_FLOOR,
    AUTOMATION_ID_PREFIX,
    AUTOMATION_STALE_DAYS,
    AUTOMATIONS_PER_DEVICE,
    SELORA_AI_LABEL_ID,
    SELORA_AI_LABEL_NAME,
)
from .telemetry import record_activity

if TYPE_CHECKING:
    from .automation_store import AutomationStore
    from .types import (
        AutomationCreateResult,
        AutomationDict,
        RiskAssessment,
        StaleAutomation,
    )

_LOGGER = logging.getLogger(__name__)


# Legacy text marker that used to be prepended to alias/description on
# every Selora-created automation. We now tag them with the
# `selora_ai` label instead, but old automations on disk still carry
# this prefix — the detection helpers in helpers.py keep recognising
# it so pre-migration automations remain identifiable.
_LEGACY_SELORA_PREFIX = "[Selora AI]"


# HA device registry IDs are 32 lowercase hex characters. The LLM has
# been observed substituting a friendly slug (e.g. ``smart_button_master
# _bedroom``), which HA reports as "Unknown device" at reload time.
_DEVICE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


# Logical condition operators that wrap a nested ``conditions:`` list (or
# HA's shorthand ``{or: [...]}`` form). Used only to know when to recurse,
# not as a closed allowlist of valid condition types — integrations can
# register their own condition platforms (e.g. ``condition: mqtt``) which
# must not be blocked here.
_LOGICAL_CONDITION_TYPES: frozenset[str] = frozenset({"and", "or", "not"})


def _validate_condition(
    cond: dict[str, Any],
    hass: HomeAssistant | None,
) -> tuple[bool, str]:
    """Validate a single condition dict against the same gates used for triggers.

    Returns ``(True, "")`` on success or ``(False, message)`` on rejection.
    Recurses into ``and`` / ``or`` / ``not`` logical wrappers — including
    HA's shorthand form (``{or: [...]}``) and the singular-dict ``conditions``
    sugar — so a slug ``device_id`` buried two levels deep is still caught
    before HA reload.
    """
    # HA accepts a bare template string as a condition (e.g.
    # ``if: ["{{ is_state('input_boolean.away', 'on') }}"]``). There are no
    # device_id / event_type fields to validate on a string, so accept it
    # and let HA's renderer surface a bad template at runtime.
    if isinstance(cond, str):
        return True, ""
    if not isinstance(cond, dict):
        return False, "condition must be an object"

    # HA shorthand: `{or: [...]}` / `{and: [...]}` / `{not: [...]}` with no
    # explicit `condition:` key. Treat the operator key as the type and the
    # value as the nested list.
    for op in ("and", "or", "not"):
        if op in cond and "condition" not in cond:
            nested = cond[op]
            if isinstance(nested, dict):
                nested = [nested]
            if not isinstance(nested, list) or not nested:
                return False, f"'{op}' shorthand requires a non-empty list"
            for sub in nested:
                ok, err = _validate_condition(sub, hass)
                if not ok:
                    return False, err
            return True, ""

    ctype = cond.get("condition")
    if not ctype or not isinstance(ctype, str):
        return False, "each condition must include a 'condition' field"
    if ctype in _LOGICAL_CONDITION_TYPES:
        nested = cond.get("conditions")
        # HA accepts a singular dict here and normalizes it to a list.
        if isinstance(nested, dict):
            nested = [nested]
        if not isinstance(nested, list) or not nested:
            return False, f"'{ctype}' condition requires a non-empty 'conditions' list"
        for sub in nested:
            ok, err = _validate_condition(sub, hass)
            if not ok:
                return False, err
        return True, ""
    if ctype == "device":
        device_id = cond.get("device_id")
        if not device_id or not isinstance(device_id, str):
            return False, "device condition requires 'device_id'"
        if not _DEVICE_ID_RE.match(device_id):
            return (
                False,
                f"device condition 'device_id' must be a 32-char hex registry "
                f"identifier, not '{device_id}'",
            )
        if hass is not None:
            device_reg = dr.async_get(hass)
            if device_reg.async_get(device_id) is None:
                return False, f"device condition references unknown device_id '{device_id}'"
    return True, ""


def _validate_trigger(
    trig: dict[str, Any],
    hass: HomeAssistant | None,
) -> tuple[bool, str]:
    """Apply the trigger schema gates shared by top-level and nested triggers.

    Catches the `platform` + `trigger` key conflict, missing platform,
    `event` trigger without `event_type`, and `device` trigger with a
    non-hex or unknown ``device_id``.

    Coerces HA 2024.10+ ``trigger:`` key to ``platform:`` so downstream
    code sees one uniform shape. A dotted value under either key
    (``timer.finished``, ``shelly.click``, …) is rejected — HA's
    documented spelling for that case is an event trigger
    (``{platform: event, event_type: timer.finished}``) or a state
    trigger on the corresponding entity; a literal dotted trigger
    type / platform makes ``automation.reload`` fail.
    """
    if not isinstance(trig, dict):
        return False, "trigger must be an object"
    if trig.get("platform") and trig.get("trigger"):
        return False, "trigger must not contain both 'platform' and 'trigger' keys"

    # HA 2024.10+ uses `trigger:` as the canonical key (the legacy
    # `platform:` form is still accepted at reload). Normalize TO
    # `trigger:` — the wire envelope and downstream behavioural
    # checks (chk_trigger_is_sun_event, chk_trigger_is_numeric_state)
    # require the `trigger` field, and emitting the legacy `platform`
    # key here makes them dead-end fail.
    platform_value = trig.get("platform")
    if not trig.get("trigger") and isinstance(platform_value, str) and platform_value:
        trig["trigger"] = trig.pop("platform")
    # Drop a co-existing empty legacy ``platform`` key whenever a
    # valid ``trigger`` is present. Without this the normalised
    # payload retains ``platform: ""`` next to the canonical
    # ``trigger:`` value; the local schema doesn't object, but Home
    # Assistant's reload-time validator rejects the unexpected extra
    # key and the automation silently fails to load.
    if trig.get("trigger") and "platform" in trig and not trig.get("platform"):
        del trig["platform"]

    platform = trig.get("trigger")
    if isinstance(platform, str) and "." in platform:
        return (
            False,
            f"'{platform}' is not a valid trigger type — use "
            f'`{{platform: event, event_type: "{platform}"}}` or a state trigger '
            f"on the relevant entity instead",
        )
    if not platform:
        return False, "each trigger must include a platform"
    if platform == "event" and not trig.get("event_type"):
        return False, "event trigger requires 'event_type'"
    if platform == "device":
        device_id = trig.get("device_id")
        if not device_id or not isinstance(device_id, str):
            return False, "device trigger requires 'device_id'"
        if not _DEVICE_ID_RE.match(device_id):
            return (
                False,
                f"device trigger 'device_id' must be a 32-char hex registry "
                f"identifier, not '{device_id}'",
            )
        if hass is not None:
            device_reg = dr.async_get(hass)
            if device_reg.async_get(device_id) is None:
                return False, f"device trigger references unknown device_id '{device_id}'"
    return True, ""


def _validate_action_conditions(
    action: Any,
    hass: HomeAssistant | None,
) -> tuple[bool, str]:
    """Walk an action step and validate any condition blocks it contains.

    HA accepts condition blocks in several places inside an action sequence:
    inline as an action step itself, in ``choose[].conditions``, ``if``,
    ``repeat.while`` / ``repeat.until``, plus any nested ``sequence``
    inside ``parallel`` / ``repeat`` / ``choose[].sequence`` /
    ``choose.default`` / ``then`` / ``else``. Without recursing into all
    of those, a slug ``device_id`` buried two levels deep in
    ``choose[0].conditions`` slips past the top-level condition gate.
    """
    if isinstance(action, list):
        for step in action:
            ok, err = _validate_action_conditions(step, hass)
            if not ok:
                return False, err
        return True, ""
    if not isinstance(action, dict):
        return True, ""

    # Inline condition-as-action step (including shorthand or/and/not).
    # A service call carries an ``action:`` or ``service:`` key, so skip
    # those to avoid validating service-call payloads as conditions.
    is_service_call = "action" in action or "service" in action
    if not is_service_call and (
        "condition" in action or any(op in action for op in _LOGICAL_CONDITION_TYPES)
    ):
        ok, err = _validate_condition(action, hass)
        if not ok:
            return False, err

    # `choose: [{conditions: [...], sequence: [...]}, ...]` + optional default.
    choose = action.get("choose")
    if isinstance(choose, list):
        for branch in choose:
            if not isinstance(branch, dict):
                continue
            for cond in _as_condition_list(branch.get("conditions")):
                ok, err = _validate_condition(cond, hass)
                if not ok:
                    return False, err
            ok, err = _validate_action_conditions(branch.get("sequence"), hass)
            if not ok:
                return False, err
    if "default" in action:
        ok, err = _validate_action_conditions(action.get("default"), hass)
        if not ok:
            return False, err

    # `if: [...], then: [...], else: [...]` action.
    for cond in _as_condition_list(action.get("if")):
        ok, err = _validate_condition(cond, hass)
        if not ok:
            return False, err
    for branch_key in ("then", "else"):
        ok, err = _validate_action_conditions(action.get(branch_key), hass)
        if not ok:
            return False, err

    # `wait_for_trigger: [...]` embeds full trigger dicts; apply the same
    # schema gates so a slug `device_id` buried inside a wait step is
    # rejected before HA reload.
    for trig in _as_condition_list(action.get("wait_for_trigger")):
        if isinstance(trig, dict):
            ok, err = _validate_trigger(trig, hass)
            if not ok:
                return False, err

    # `repeat: {while|until: [...], sequence: [...]}`.
    repeat = action.get("repeat")
    if isinstance(repeat, dict):
        for guard_key in ("while", "until"):
            for cond in _as_condition_list(repeat.get(guard_key)):
                ok, err = _validate_condition(cond, hass)
                if not ok:
                    return False, err
        ok, err = _validate_action_conditions(repeat.get("sequence"), hass)
        if not ok:
            return False, err

    # `parallel: [...]` and `sequence: [...]` just nest more action steps.
    for nested_key in ("parallel", "sequence"):
        ok, err = _validate_action_conditions(action.get(nested_key), hass)
        if not ok:
            return False, err

    return True, ""


def _as_condition_list(value: Any) -> list[Any]:
    """HA accepts a singular dict where a list is expected (conditions,
    triggers, wait_for_trigger, …). Wrap so callers iterate uniformly."""
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return value
    return []


def _strip_legacy_selora_prefix(text: str | None) -> str:
    """Remove any leading ``[Selora AI]`` markers from a string.

    Callers occasionally hand us text that already carries the legacy
    prefix (e.g. a suggestion alias whose description was generated by
    an older Selora build, or a refinement turn replaying an earlier
    automation's text). We strip both single and accidentally-doubled
    occurrences so the saved YAML stays clean. The label attached to
    the automation is what identifies it as Selora-created from now
    on — the prose no longer needs to carry the marker.
    """
    if not text:
        return text or ""
    cleaned = text
    while cleaned.lstrip().lower().startswith(_LEGACY_SELORA_PREFIX.lower()):
        # Strip the prefix and any single separator space that
        # followed it, then re-loop to peel off duplicates.
        idx = cleaned.lower().find(_LEGACY_SELORA_PREFIX.lower())
        cleaned = cleaned[:idx] + cleaned[idx + len(_LEGACY_SELORA_PREFIX) :]
        cleaned = cleaned.lstrip()
    return cleaned


async def _ensure_selora_label(hass: HomeAssistant) -> str:
    """Return the label_id of the Selora AI label, creating it if absent.

    HA's ``LabelRegistry.async_create`` derives the label_id from the
    name (slugify); on a name collision it suffixes the id, so we look
    the label up by name after creation and return whatever HA assigned
    rather than assuming ``SELORA_AI_LABEL_ID``. Failure to create is
    non-fatal — automations are still created, just without the label.
    """
    try:
        from homeassistant.helpers import label_registry as lr

        registry = lr.async_get(hass)
        if registry.async_get_label(SELORA_AI_LABEL_ID) is not None:
            return SELORA_AI_LABEL_ID
        existing = registry.async_get_label_by_name(SELORA_AI_LABEL_NAME)
        if existing is not None:
            return existing.label_id
        created = registry.async_create(
            name=SELORA_AI_LABEL_NAME,
            icon="mdi:robot",
            color="amber",
        )
        if created.label_id != SELORA_AI_LABEL_ID:
            _LOGGER.warning(
                "Selora AI label registered as %r (expected %r); "
                "downstream label matching may be inconsistent",
                created.label_id,
                SELORA_AI_LABEL_ID,
            )
        return created.label_id
    except Exception as exc:  # pragma: no cover — defensive
        _LOGGER.debug("Could not pre-create Selora AI label: %s", exc)
    return SELORA_AI_LABEL_ID


async def _attach_selora_label_to_entity(hass: HomeAssistant, automation_id: str) -> None:
    """Attach the Selora AI label to a freshly-created automation entity.

    Labels live in the *entity* registry, not in automations.yaml —
    writing ``labels:`` into the YAML causes HA's automation schema
    validation to fail and the entity to load as "unavailable". After
    the YAML has been written and the ``automation.reload`` service
    has materialised the new entity, we resolve the entity_id by
    matching ``attributes.id`` to the automation_id we just wrote,
    then update the entity registry to attach the label.

    Best-effort: any failure is logged and swallowed. The automation
    still works, just without the label — the legacy id-prefix and
    ``[Selora AI]`` checks in helpers.is_selora_automation continue
    to identify it.
    """
    try:
        label_id = await _ensure_selora_label(hass)
        entity_id: str | None = None
        for state in hass.states.async_all("automation"):
            if state.attributes.get("id") == automation_id:
                entity_id = state.entity_id
                break
        if entity_id is None:
            return
        entry = er.async_get(hass).async_get(entity_id)
        if entry is None:
            return
        labels = set(entry.labels or set())
        if label_id in labels:
            return
        labels.add(label_id)
        er.async_get(hass).async_update_entity(entity_id, labels=labels)
    except Exception as exc:  # pragma: no cover — defensive
        _LOGGER.debug("Could not attach Selora AI label to %s: %s", automation_id, exc)


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
            # tts.speak / tts.cloud_say address their target speaker via
            # data.media_player_entity_id — a genuine entity reference (always
            # media_player.*). Walk it so it gets the unknown-entity check,
            # notably the speaker an announcement rewrite moves here from the
            # original target.entity_id.
            _add(data.get("media_player_entity_id"))

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


def _collect_referenced_resources(
    automation: AutomationDict | dict[str, Any],
) -> tuple[set[str], set[str], set[str]]:
    """Walk an automation payload and return the entity / device / area IDs
    it references in a single pass.

    Counterpart to ``_collect_referenced_entity_ids`` for callers (like the
    proactive-suggestion ignore filter) that also need to compare against
    ignored devices and areas. Device IDs come from the device trigger /
    condition / action forms and ``target.device_id``; area IDs come from
    ``target.area_id``. The same control-flow descent (choose / if / repeat
    / sequence / parallel) is applied so nested device or area references
    don't slip through.

    Entity-ID validation matches the entity-only walker: must contain a
    ``.`` and not be a template. Device / area IDs are returned verbatim
    (HA generates them as slugs / UUIDs with no dot).
    """
    entities: set[str] = set()
    devices: set[str] = set()
    areas: set[str] = set()

    def _as_list(value: Any) -> list[Any]:
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            return value
        return []

    def _add(bucket: set[str], value: Any) -> None:
        if isinstance(value, str):
            for part in value.split(","):
                stripped = part.strip()
                if stripped:
                    bucket.add(stripped)
        elif isinstance(value, list):
            for item in value:
                _add(bucket, item)

    def _walk_trigger(trigger: Any) -> None:
        if not isinstance(trigger, dict):
            return
        _add(entities, trigger.get("entity_id"))
        # Device triggers carry the device_id at top level.
        _add(devices, trigger.get("device_id"))

    def _walk_condition(condition: Any) -> None:
        if not isinstance(condition, dict):
            return
        _add(entities, condition.get("entity_id"))
        _add(devices, condition.get("device_id"))
        for nested in _as_list(condition.get("conditions")):
            _walk_condition(nested)
        for key in ("and", "or", "not"):
            for nested in _as_list(condition.get(key)):
                _walk_condition(nested)

    def _walk_action(action: Any) -> None:
        if not isinstance(action, dict):
            return
        _walk_condition(action)

        _add(entities, action.get("entity_id"))
        # Device actions have device_id at the top level alongside entity_id.
        _add(devices, action.get("device_id"))

        target = action.get("target")
        if isinstance(target, dict):
            _add(entities, target.get("entity_id"))
            _add(devices, target.get("device_id"))
            _add(areas, target.get("area_id"))

        data = action.get("data")
        if isinstance(data, dict):
            _add(entities, data.get("entity_id"))
            # tts.speak / tts.cloud_say address the speaker via
            # data.media_player_entity_id (a media_player.* entity ref). Collect
            # it so ignored-media-player filtering still applies after an
            # announcement rewrite moves the speaker here from target.entity_id.
            _add(entities, data.get("media_player_entity_id"))

        for trigger in _as_list(action.get("wait_for_trigger")):
            _walk_trigger(trigger)

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

    valid_entities = {eid for eid in entities if "." in eid and "{{" not in eid and "{%" not in eid}
    return valid_entities, devices, areas


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


# Substrings / suffixes that mark a ``media_content_id`` as a real, playable
# audio reference rather than a sentence the user wants spoken aloud. Used to
# tell a genuine ``media_player.play_media`` call apart from the common model
# mistake of handing it announcement text (which plays nothing).
_PLAYABLE_MEDIA_MARKERS: tuple[str, ...] = (
    "://",  # any URI scheme (http, https, rtsp, …)
    "media-source:",
    "spotify:",
    "/local/",
    "/media/",
)
_AUDIO_EXTENSIONS: tuple[str, ...] = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac")


def _looks_like_spoken_text(value: str) -> bool:
    """Return ``True`` when *value* reads as an announcement sentence rather
    than a playable media reference (URL, media-source id, file path, stream).

    Known false-positive: a genuine multi-word friendly-name ``media_content_id``
    with no URI marker (e.g. a radio/playlist name) is classed as prose and
    rewritten to ``tts.speak``. Accepted on purpose — the silent-announcement
    failure is far more common than friendly-name content ids, and the rewrite
    still falls back to leaving the action untouched when no TTS engine exists.
    """
    text = value.strip()
    if not text:
        return False
    # A Jinja template renders to a dynamic media reference (URL / media-source
    # id) at run time — it is not a sentence to speak, even though it reads like
    # prose. Leave templated play_media calls alone.
    if "{{" in text or "{%" in text:
        return False
    low = text.lower()
    if any(marker in low for marker in _PLAYABLE_MEDIA_MARKERS):
        return False
    if low.endswith(_AUDIO_EXTENSIONS):
        return False
    # Non-ASCII letters (CJK, Cyrillic, Greek, accented Latin, …) effectively
    # never appear in an opaque media token, so their presence marks the
    # payload as a sentence to speak. CJK scripts have no word spaces, so we
    # cannot require whitespace here — this is what lets ja/ko/zh/ru
    # announcements get repaired, not just Latin-script ones.
    if any(ch.isalpha() and ord(ch) > 127 for ch in text):
        return True
    # ASCII-only: an opaque media token is a single word; prose is several.
    if " " not in text:
        return False
    return len(re.findall(r"[a-zA-Z]{2,}", text)) >= 2


def _play_media_speaker(action: dict[str, Any]) -> str | list[str] | None:
    """Return the media_player entity_id(s) a ``play_media`` action targets,
    checking ``target.entity_id``, a bare ``entity_id``, then ``data.entity_id``."""
    target = action.get("target")
    if isinstance(target, dict) and target.get("entity_id"):
        return target["entity_id"]
    if action.get("entity_id"):
        return action["entity_id"]
    data = action.get("data")
    if isinstance(data, dict) and data.get("entity_id"):
        return data["entity_id"]
    return None


def _engine_matches_provider(engine_entity_id: str, provider: str) -> bool:
    """True when ``engine_entity_id`` looks like it belongs to ``provider``.

    ``provider`` is the prefix of a legacy ``tts.<provider>_say`` service
    (``cloud``, ``google_translate``, ``piper``, …). Match on the full provider
    token or its first segment so ``google_translate`` also matches a
    ``tts.google_en_com`` entity. Used to keep canonicalization on the same
    provider that the legacy service named.
    """
    eid = engine_entity_id.lower()
    return provider in eid or provider.split("_", 1)[0] in eid


def _cloud_tts_active(hass: HomeAssistant) -> bool:
    """True when Home Assistant Cloud TTS is actually usable on this install.

    The ``cloud`` integration ships in ``default_config`` and creates the
    ``tts.home_assistant_cloud`` entity unconditionally — its TTS platform is
    forwarded regardless of login state, and the entity reports ``available``
    with a live (``unknown``) state even with no Nabu Casa subscription. So
    entity presence / state / availability cannot tell a working cloud engine
    apart from a dead one that only fails at call time; the login + active
    subscription state is the only reliable signal. Imported lazily and guarded
    so the integration still works when the cloud component is absent.
    """
    try:
        from homeassistant.components.cloud import async_active_subscription
    except ImportError:
        return False
    try:
        return bool(async_active_subscription(hass))
    except (KeyError, AttributeError):
        # Cloud data not populated / internal API shifted — treat as inactive
        # rather than route an announcement at an engine that can't speak.
        return False


def _is_ha_cloud_engine(engine_entity_id: str) -> bool:
    """True only for the Home Assistant Cloud (Nabu Casa) TTS engine entity.

    HA Cloud creates ``tts.home_assistant_cloud``; that is the one engine whose
    availability lies about subscription state (see :func:`_cloud_tts_active`).
    The match is anchored to ``home_assistant_cloud`` so a third-party provider
    whose entity_id merely *contains* "cloud" — e.g. ``tts.google_cloud_…`` —
    is NOT treated as HA Cloud and subscription-gated.
    """
    return "home_assistant_cloud" in engine_entity_id.lower()


def _legacy_say_usable(hass: HomeAssistant, provider: str, name: str) -> bool:
    """True when a legacy ``tts.<provider>_say`` service can actually speak.

    Registration is not usability: the ``cloud`` integration ships in
    ``default_config`` and registers ``tts.cloud_say`` unconditionally, so
    :meth:`hass.services.has_service` reports it present even with no active
    Nabu Casa subscription — a call that validates but runs silently mute.
    Only the HA Cloud say service (provider exactly ``cloud`` →
    ``tts.cloud_say``) is subscription-gated; a third-party provider whose name
    merely contains "cloud" (``google_cloud_say``) is a normal working service.
    """
    if not hass.services.has_service("tts", name):
        return False
    if provider == "cloud":
        return _cloud_tts_active(hass)
    return True


def _tts_engine_usable(hass: HomeAssistant, engine: str) -> bool:
    """True when *engine* is a live ``tts.*`` entity that can actually speak.

    The HA Cloud engine reports ``available`` regardless of subscription (see
    :func:`_cloud_tts_active`), so it is only usable with an active HA Cloud
    subscription. Any other engine — including a third-party ``tts.google_cloud_*``
    — is usable whenever it is present as a live entity.
    """
    if engine not in hass.states.async_entity_ids("tts"):
        return False
    if _is_ha_cloud_engine(engine):
        return _cloud_tts_active(hass)
    return True


def _resolve_tts_engine(hass: HomeAssistant, *, prefer: str | None = None) -> str | None:
    """Pick a usable TTS engine entity for ``tts.speak``. When ``prefer`` is
    given (the provider of a legacy ``tts.<provider>_say`` service), return an
    engine that belongs to that provider so canonicalization keeps the original
    voice. Else prefer Piper, then Google, else the first usable ``tts.*``
    entity. HA Cloud (Nabu Casa) is a competitor, so it's never preferred —
    only used as a last-resort fallback when it's the sole usable engine.

    Only *usable* engines are considered (:func:`_tts_engine_usable`): a cloud
    engine present without an active Nabu Casa subscription is skipped, since it
    reports available yet fails at call time. ``None`` when no usable engine
    exists."""
    usable = [
        eid for eid in sorted(hass.states.async_entity_ids("tts")) if _tts_engine_usable(hass, eid)
    ]
    if not usable:
        return None
    if prefer:
        for eid in usable:
            if _engine_matches_provider(eid, prefer):
                return eid
    for preferred in ("piper", "google"):
        for eid in usable:
            if preferred in eid:
                return eid
    # Cloud is a competitor → never chosen while any other usable engine
    # exists (e.g. tts.microsoft), even though the sorted list might put
    # it first. Fall back to cloud only when it's the sole usable engine.
    for eid in usable:
        if not _is_ha_cloud_engine(eid):
            return eid
    return usable[0]


_TTS_MEDIA_SOURCE_PREFIX = "media-source://tts/"


def _rewrite_tts_media_source(
    action: dict[str, Any],
    hass: HomeAssistant,
) -> dict[str, Any] | None:
    """Rewrite a ``media_player.play_media`` whose ``media_content_id`` is a TTS
    media-source URI (``media-source://tts/<engine>?message=…``) into a portable
    ``tts.speak`` call.

    The LLM sometimes encodes an announcement as a TTS media-source URI with a
    hard-coded engine — e.g. ``media-source://tts/cloud_say?message=Someone+is+at
    +the+front+door``. Like the legacy ``tts.<provider>_say`` service, that engine
    is non-portable: ``cloud_say`` needs a Nabu Casa subscription, so on an
    install without one the ``play_media`` call fails at run time ("Playback
    failed to start") even though it validates. Extract the spoken message (and
    ``language``) from the URI query and rebuild as ``tts.speak`` on a *usable*
    engine, preferring the URI's own provider so the voice is preserved when that
    provider works (cloud → the cloud engine when subscribed, else a fallback).

    Returns the rewritten action, or ``None`` when it is not a TTS media-source
    play_media or is not rewritable (templated URI, no message, no ``tts.speak``,
    no usable engine, or no concrete media_player speaker).
    """
    service = str(action.get("action", action.get("service", "")))
    if service != "media_player.play_media":
        return None

    data = action.get("data")
    data = data if isinstance(data, dict) else {}
    content_id = data.get("media_content_id")
    if not isinstance(content_id, str) or not content_id.startswith(_TTS_MEDIA_SOURCE_PREFIX):
        return None
    # A templated URI resolves to its real value at run time — rewriting it
    # would change behaviour based on a guess. Leave it for the normal gates.
    if "{{" in content_id or "{%" in content_id:
        return None

    remainder = content_id[len(_TTS_MEDIA_SOURCE_PREFIX) :]
    engine_token, _, query = remainder.partition("?")
    params = urllib.parse.parse_qs(query)  # already URL-decodes values (+, %xx)
    message = (params.get("message") or [""])[0]
    if not message.strip():
        # No spoken text to rebuild from (e.g. a non-message TTS media source).
        return None

    if not hass.services.has_service("tts", "speak"):
        _LOGGER.warning(
            "Announcement uses TTS media-source %s but tts.speak is unavailable "
            "to canonicalize it to; leaving as-is.",
            content_id,
        )
        return None
    # The URI engine token mirrors the legacy service name (``cloud_say``) or a
    # provider/engine id; prefer an engine from that provider so the voice is
    # kept when usable, falling back to any working engine otherwise.
    provider = engine_token[: -len("_say")] if engine_token.endswith("_say") else engine_token
    engine = _resolve_tts_engine(hass, prefer=provider)
    if engine is None:
        _LOGGER.warning(
            "Announcement uses TTS media-source %s but no usable TTS engine is "
            "configured to retarget tts.speak at; leaving as-is.",
            content_id,
        )
        return None

    speaker = _play_media_speaker(action)
    speaker_ids: list[str] = []
    for part in speaker if isinstance(speaker, list) else [speaker]:
        if isinstance(part, str):
            speaker_ids.extend(p.strip() for p in part.split(",") if p.strip())
    if not speaker_ids or any(s.split(".")[0] != "media_player" for s in speaker_ids):
        _LOGGER.warning(
            "Announcement uses TTS media-source %s but its target is not a "
            "concrete media_player entity; leaving as-is (it will be silent).",
            content_id,
        )
        return None

    rewritten: dict[str, Any] = {
        key: val
        for key, val in action.items()
        if key not in ("action", "service", "target", "entity_id", "data")
    }
    rewritten["action"] = "tts.speak"
    rewritten["target"] = {"entity_id": engine}
    new_data: dict[str, Any] = {
        "media_player_entity_id": speaker_ids[0] if len(speaker_ids) == 1 else speaker_ids,
        "message": message,
    }
    language = (params.get("language") or [""])[0]
    if language:
        new_data["language"] = language
    rewritten["data"] = new_data
    _LOGGER.info(
        "Canonicalized TTS media-source announcement (%s) to tts.speak (engine=%s, speaker=%s).",
        content_id,
        engine,
        ", ".join(speaker_ids),
    )
    return rewritten


def _rewrite_spoken_play_media(
    action: dict[str, Any],
    hass: HomeAssistant,
) -> dict[str, Any] | None:
    """Rewrite a ``media_player.play_media`` action handed announcement text
    into a working ``tts.speak`` call.

    ``media_player.play_media`` only plays audio files or streams, so a plain
    text payload produces a valid-but-silent automation — the exact failure
    mode behind doorbell-announcement automations. When the payload reads as
    spoken text and a TTS engine is available, retarget it to ``tts.speak``
    with the engine as the target and the original speaker passed as
    ``media_player_entity_id``.

    Returns the rewritten action, or ``None`` when the action is a genuine
    media call or is not rewritable (no TTS engine).
    """
    service = str(action.get("action", action.get("service", "")))
    if service != "media_player.play_media":
        return None

    data = action.get("data")
    data = data if isinstance(data, dict) else {}
    raw_text = data.get("media_content_id") or data.get("message") or ""
    if not isinstance(raw_text, str) or not _looks_like_spoken_text(raw_text):
        return None

    if not hass.services.has_service("tts", "speak"):
        _LOGGER.warning(
            "Announcement automation uses media_player.play_media with text but "
            "tts.speak is unavailable; leaving as-is (it will be silent)."
        )
        return None
    engine = _resolve_tts_engine(hass)
    if engine is None:
        _LOGGER.warning(
            "Announcement automation uses media_player.play_media with text but "
            "no TTS engine entity is configured; leaving as-is (it will be silent)."
        )
        return None

    speaker = _play_media_speaker(action)
    speaker_ids: list[str] = []
    for part in speaker if isinstance(speaker, list) else [speaker]:
        if isinstance(part, str):
            speaker_ids.extend(p.strip() for p in part.split(",") if p.strip())
    # tts.speak addresses the speaker through ``data.media_player_entity_id``,
    # which accepts only media_player entity_ids — not ``area_id`` / ``device_id``
    # targets, and not other domains. If we can't identify a concrete
    # media_player speaker, leave the play_media action untouched: it stays
    # silent, but we never emit a tts.speak that can't run, and the original
    # action still goes through the normal service/entity/read-only gates.
    if not speaker_ids or any(s.split(".")[0] != "media_player" for s in speaker_ids):
        _LOGGER.warning(
            "Announcement automation uses media_player.play_media with text but its "
            "target is not a concrete media_player entity (area/device target or "
            "another domain); leaving as-is (it will be silent)."
        )
        return None

    # Preserve any per-action keys the original carried (alias, enabled,
    # continue_on_error, variables, …); only the service-call shape is
    # replaced — drop the old media-call keys so no stale target/data leaks.
    rewritten: dict[str, Any] = {
        key: val
        for key, val in action.items()
        if key not in ("action", "service", "target", "entity_id", "data")
    }
    rewritten["action"] = "tts.speak"
    rewritten["target"] = {"entity_id": engine}
    rewritten["data"] = {
        "media_player_entity_id": speaker_ids[0] if len(speaker_ids) == 1 else speaker_ids,
        "message": raw_text,
    }
    _LOGGER.info(
        "Rewrote silent media_player.play_media announcement to tts.speak (engine=%s, speaker=%s).",
        engine,
        ", ".join(speaker_ids),
    )
    return rewritten


def _rewrite_legacy_tts_say(
    action: dict[str, Any],
    hass: HomeAssistant,
) -> dict[str, Any] | None:
    """Rewrite a legacy ``tts.<provider>_say`` action into the unified
    ``tts.speak`` call whenever the canonical path is available.

    The per-provider ``tts.<provider>_say`` services (``tts.cloud_say``,
    ``tts.google_translate_say``, …) are the legacy, non-portable form: each
    only exists while that specific provider is set up, and ``tts.speak`` is
    HA's canonical replacement. We always retarget to ``tts.speak`` — not just
    when the legacy service is missing — so an automation never hard-codes a
    provider-specific service that breaks the moment that provider is removed
    or the config is moved to another install. (The missing-service variant is
    the worst case: an LLM emitting ``tts.cloud_say`` on an install without that
    provider produces an action that silently does nothing.)

    When ``tts.speak`` and a TTS engine are available, retarget to ``tts.speak``
    with the engine as the target and the original speaker passed as
    ``media_player_entity_id`` — preserving the message and any ``language`` /
    ``options`` / ``cache`` data. The engine is chosen to match the legacy
    service's own provider (``cloud_say`` → the cloud engine,
    ``google_translate_say`` → the google engine) so the voice never changes.
    A *registered* (working) legacy call is left untouched when no engine for
    its provider exists, rather than swapping it onto a different provider; an
    unregistered (broken) call still falls back to any working engine.

    Returns the rewritten action, or ``None`` when the action is not a legacy
    say call, or it is not rewritable (no ``tts.speak``, no engine, or no
    concrete media_player speaker). When not rewritable, a registered legacy
    service is left to run as-is and an unregistered one is rejected by the
    missing-service gate downstream.
    """
    service = str(action.get("action", action.get("service", "")))
    if "." not in service or "{{" in service:
        # A templated service name (e.g. tts.{{ states('input_select.x') }}_say)
        # resolves at runtime; treating it as a fixed unregistered service and
        # rewriting it would change the automation's behavior. Leave it — the
        # service-existence gate skips templated names too.
        return None
    domain, name = service.split(".", 1)
    if domain != "tts" or not name.endswith("_say"):
        return None
    provider = name[: -len("_say")]
    # Canonicalize even when the legacy say service is registered: tts.speak is
    # the portable form, and a per-provider tts.*_say is non-portable. Bail only
    # when the canonical path can't be built (handled by the gates below).

    if not hass.services.has_service("tts", "speak"):
        _LOGGER.warning(
            "Announcement automation uses legacy %s but tts.speak is unavailable "
            "to canonicalize it to; leaving as-is.",
            service,
        )
        return None
    # Prefer the engine belonging to the legacy service's own provider so the
    # voice/provider never silently changes (cloud_say → the cloud engine,
    # google_translate_say → the google engine).
    engine = _resolve_tts_engine(hass, prefer=provider)
    if engine is None:
        _LOGGER.warning(
            "Announcement automation uses legacy %s but no TTS engine entity is "
            "configured to retarget tts.speak at; leaving as-is.",
            service,
        )
        return None
    # Don't change the provider of a *working* call: when the legacy service is
    # genuinely usable but the only engine we can resolve belongs to a different
    # provider, leave it as-is rather than swapping the voice (and possibly
    # invalidating provider-specific options). A broken call — unregistered, or
    # registered-but-mute like tts.cloud_say with no active subscription — is
    # worth rewriting onto any working engine instead. Registration alone is not
    # usability (see _legacy_say_usable), so we gate on usability, not presence.
    if _legacy_say_usable(hass, provider, name) and not _engine_matches_provider(engine, provider):
        _LOGGER.debug(
            "Leaving registered legacy %s as-is: no matching %s TTS engine to "
            "canonicalize to without changing the provider.",
            service,
            provider,
        )
        return None

    data = action.get("data")
    data = data if isinstance(data, dict) else {}
    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        # A say call without a concrete message is not something we can safely
        # rebuild as tts.speak — leave it for the missing-service gate.
        return None

    # Legacy say targets the speaker through target.entity_id / a bare
    # entity_id / data.entity_id — the same shapes _play_media_speaker reads.
    speaker = _play_media_speaker(action)
    speaker_ids: list[str] = []
    for part in speaker if isinstance(speaker, list) else [speaker]:
        if isinstance(part, str):
            speaker_ids.extend(p.strip() for p in part.split(",") if p.strip())
    # tts.speak addresses the speaker via data.media_player_entity_id, which
    # accepts only media_player entity_ids. If we can't identify a concrete
    # media_player speaker, leave the action untouched so it goes through the
    # normal missing-service gate rather than emitting a tts.speak that can't run.
    if not speaker_ids or any(s.split(".")[0] != "media_player" for s in speaker_ids):
        _LOGGER.warning(
            "Announcement automation uses %s but its target is not a concrete "
            "media_player entity (area/device target or another domain); leaving "
            "as-is (it will be silent).",
            service,
        )
        return None

    # Preserve any per-action keys the original carried (alias, enabled,
    # continue_on_error, variables, …); only the service-call shape is replaced.
    rewritten: dict[str, Any] = {
        key: val
        for key, val in action.items()
        if key not in ("action", "service", "target", "entity_id", "data")
    }
    rewritten["action"] = "tts.speak"
    rewritten["target"] = {"entity_id": engine}
    new_data: dict[str, Any] = {
        "media_player_entity_id": speaker_ids[0] if len(speaker_ids) == 1 else speaker_ids,
        "message": message,
    }
    # Carry over the say options tts.speak also understands.
    for key in ("language", "options", "cache"):
        if key in data:
            new_data[key] = data[key]
    rewritten["data"] = new_data
    _LOGGER.info(
        "Canonicalized legacy %s announcement to tts.speak (engine=%s, speaker=%s).",
        service,
        engine,
        ", ".join(speaker_ids),
    )
    return rewritten


def _retarget_tts_speak_engine(
    action: dict[str, Any],
    hass: HomeAssistant,
) -> dict[str, Any] | None:
    """Pin a ``tts.speak`` action's engine to one that actually exists on this
    install.

    ``tts.speak`` addresses the TTS *engine* via ``target.entity_id``. The LLM
    routinely copies the example engine (``tts.home_assistant_cloud``) from the
    system prompt verbatim, regardless of which engines the home actually has.
    ``tts.home_assistant_cloud`` exists and reports available on nearly every
    install (the ``cloud`` integration ships in ``default_config``), yet only
    speaks with an active Nabu Casa subscription — so the action validates but
    runs silently mute on installs without one. (The engine can also be wholly
    absent / registry-only, which the unknown-entity gate would otherwise pass.)

    When the action's engine is not *usable* (:func:`_tts_engine_usable` —
    missing, registry-only, or a cloud engine without an active subscription),
    retarget it to a resolved usable engine. A usable engine the LLM picked is
    left untouched. Returns the rewritten action, or ``None`` when nothing
    better can be done (the chosen engine is already usable, no engine is
    resolvable, or the target is not a single concrete engine entity_id).
    """
    service = str(action.get("action", action.get("service", "")))
    if service != "tts.speak":
        return None
    target = action.get("target")
    target = target if isinstance(target, dict) else {}
    engine = target.get("entity_id")
    # Only handle the canonical single-engine string. A list target or a
    # templated engine resolves elsewhere/at runtime — leave it for the gates
    # rather than guess at it.
    if engine is not None and (not isinstance(engine, str) or "{{" in engine):
        return None
    if isinstance(engine, str) and _tts_engine_usable(hass, engine):
        # The LLM picked a usable engine — keep its choice.
        return None
    resolved = _resolve_tts_engine(hass)
    if resolved is None or resolved == engine:
        # No usable engine to offer (home has no working TTS): leave as-is so
        # the existing service/entity gates handle it rather than emit a guess.
        return None
    rewritten = dict(action)
    rewritten["target"] = {**target, "entity_id": resolved}
    _LOGGER.info(
        "Retargeted tts.speak engine %s -> %s (chosen engine is not a live TTS "
        "entity on this install).",
        engine or "<unset>",
        resolved,
    )
    return rewritten


def _rewrite_announcements(action: dict[str, Any], hass: HomeAssistant) -> dict[str, Any]:
    """Apply the announcement repairs (:func:`_rewrite_tts_media_source`,
    :func:`_rewrite_spoken_play_media`, :func:`_rewrite_legacy_tts_say`, and
    :func:`_retarget_tts_speak_engine`) to *action* and to any actions nested in
    its control-flow branches (``choose`` / ``if`` / ``sequence`` / ``repeat`` /
    ``parallel`` …).

    Mirrors the recursion the entity walker and condition validator already do,
    so a silent announcement buried inside a conditional branch is repaired just
    like a top-level one. Returns the resulting action (a rewritten ``tts.speak``
    form, or the original with nested branches rewritten in place).
    """
    rewritten = (
        _rewrite_tts_media_source(action, hass)
        or _rewrite_spoken_play_media(action, hass)
        or _rewrite_legacy_tts_say(action, hass)
        or _retarget_tts_speak_engine(action, hass)
    )
    if rewritten is not None:
        return rewritten

    def _rewrite_seq(value: Any) -> Any:
        # HA accepts the singular dict form, a flat list, and nested sequence
        # lists (e.g. ``parallel: [[{...}], [{...}]]``) for action sequences;
        # recurse through whichever shape is present so a play_media buried in a
        # nested list still gets rewritten rather than returned as-is.
        if isinstance(value, dict):
            return _rewrite_announcements(value, hass)
        if isinstance(value, list):
            return [_rewrite_seq(item) for item in value]
        return value

    for key in ("sequence", "then", "else", "default", "parallel"):
        if key in action:
            action[key] = _rewrite_seq(action[key])
    choose = action.get("choose")
    if isinstance(choose, list):
        for branch in choose:
            if isinstance(branch, dict) and "sequence" in branch:
                branch["sequence"] = _rewrite_seq(branch["sequence"])
    repeat = action.get("repeat")
    if isinstance(repeat, dict) and "sequence" in repeat:
        repeat["sequence"] = _rewrite_seq(repeat["sequence"])
    return action


_MAX_SERVICES_IN_FEEDBACK = 30


def _iter_action_dicts(actions: Any) -> Iterator[dict[str, Any]]:
    """Yield every action dict in *actions*, recursing into control-flow
    branches (``choose`` / ``if`` / ``repeat`` / ``sequence`` / ``parallel`` /
    ``then`` / ``else`` / ``default``) — the same shapes the validator and the
    announcement rewriter walk."""
    if isinstance(actions, dict):
        actions = [actions]
    if not isinstance(actions, list):
        return
    for act in actions:
        if not isinstance(act, dict):
            continue
        yield act
        for key in ("sequence", "then", "else", "default", "parallel"):
            if key in act:
                yield from _iter_action_dicts(act[key])
        choose = act.get("choose")
        if isinstance(choose, list):
            for branch in choose:
                if isinstance(branch, dict):
                    yield from _iter_action_dicts(branch.get("sequence"))
        repeat = act.get("repeat")
        if isinstance(repeat, dict):
            yield from _iter_action_dicts(repeat.get("sequence"))


def _action_service(action: dict[str, Any]) -> str:
    """The service id an action calls, from the ``action`` or legacy ``service`` key."""
    return str(action.get("action", action.get("service", "")))


def _action_target_entity_ids(action: dict[str, Any]) -> list[str]:
    """Every entity_id an action targets — ``target.entity_id``, a bare
    ``entity_id``, or ``data.entity_id`` — flattened across comma-strings and lists."""
    ids: list[str] = []
    candidates: list[Any] = []
    target = action.get("target")
    if isinstance(target, dict):
        candidates.append(target.get("entity_id"))
    candidates.append(action.get("entity_id"))
    data = action.get("data")
    if isinstance(data, dict):
        candidates.append(data.get("entity_id"))
    for cand in candidates:
        if isinstance(cand, str):
            ids.extend(part.strip() for part in cand.split(",") if part.strip())
        elif isinstance(cand, list):
            ids.extend(str(part).strip() for part in cand if str(part).strip())
    return ids


def _real_services_for_domain(hass: HomeAssistant, domain: str) -> list[str]:
    """Sorted ``domain.service`` ids actually registered for *domain*, capped so
    a chatty domain can't blow up the feedback prompt."""
    services = hass.services.async_services_for_domain(domain)
    names = sorted(f"{domain}.{name}" for name in services)
    return names[:_MAX_SERVICES_IN_FEEDBACK]


def _entity_integration(hass: HomeAssistant, entity_id: str) -> str | None:
    """The integration (platform) that provides *entity_id*, or ``None`` if it
    is not in the entity registry."""
    entry = er.async_get(hass).async_get(entity_id)
    return entry.platform if entry else None


def build_service_feedback(
    hass: HomeAssistant,
    reason: str,
    rejected_automation: dict[str, Any] | None,
) -> str:
    """Turn a :func:`validate_automation_payload` rejection into an actionable
    correction the model can act on, naming the REAL services available on this
    Home Assistant for the entities and domains involved.

    This is the generalised alternative to per-service rewrite patches: instead
    of hand-coding a fix for each hallucinated service (``media_player.snapshot``
    → ``sonos.snapshot`` …), we hand the model ground truth — the actual
    integration behind each target entity and that integration's real service
    list — and let it correct its own output. One mechanism covers the whole
    class of non-existent-service / read-only-target rejections.
    """
    lines: list[str] = [
        "The automation you proposed was rejected by Home Assistant and was NOT created.",
        f"Reason: {reason}",
        "",
    ]

    svc_match = re.search(r"non-existent service '([^']+)'", reason)
    if svc_match:
        bad_service = svc_match.group(1)
        bad_domain = bad_service.split(".", 1)[0]
        lines.append(f"The service '{bad_service}' does not exist on this Home Assistant.")

        # List the real services of the integration actually behind the
        # entities the offending action targeted — this is where calls like
        # snapshot/restore really live (e.g. ``sonos.snapshot`` for a Sonos
        # media_player), so the model can retarget to a service that exists.
        target_ids: list[str] = []
        if isinstance(rejected_automation, dict):
            actions = rejected_automation.get("action") or rejected_automation.get("actions") or []
            for act in _iter_action_dicts(actions):
                if _action_service(act) == bad_service:
                    target_ids.extend(_action_target_entity_ids(act))

        listed_integrations: set[str] = set()
        for eid in target_ids:
            integration = _entity_integration(hass, eid)
            if not integration or integration in listed_integrations:
                continue
            listed_integrations.add(integration)
            integ_services = _real_services_for_domain(hass, integration)
            if integ_services:
                lines.append(
                    f"- {eid} is provided by the '{integration}' integration. "
                    f"Its real services: {', '.join(integ_services)}."
                )

        domain_services = _real_services_for_domain(hass, bad_domain)
        if domain_services:
            lines.append(f"- Real '{bad_domain}' services: {', '.join(domain_services)}.")

        lines.append("")
        lines.append(
            "Rewrite the action(s) to use ONLY services listed above. Do not invent "
            "service names. If no suitable service exists for a step, omit that step."
        )
        return "\n".join(lines)

    ro_match = re.search(r"read-only domain '([^']+)'", reason)
    if ro_match:
        domain = ro_match.group(1)
        lines.append(
            f"The '{domain}' domain is read-only (it registers no services), so you "
            "cannot call a service on it or use it as a service target. Use its state "
            "in a trigger or condition instead, and put the action on a controllable "
            "entity (light, switch, media_player, lock, climate, …)."
        )
        return "\n".join(lines)

    lines.append(
        "Fix the issue above and resubmit a corrected automation. Use only entity_ids "
        "and services that exist on this Home Assistant."
    )
    return "\n".join(lines)


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
    # No static allowlist of trigger platforms: HA integrations register
    # their own platforms at runtime (e.g. ``platform: litejet``) and
    # HA 2024.10+ adds the ``<domain>.<event>`` form. Trust HA to surface
    # unknown platforms at reload — the schema gates in `_validate_trigger`
    # catch the specific LLM failure modes we have seen in the wild.
    normalized_triggers: list[dict[str, Any]] = []
    for trig in triggers:
        fixed = _normalize_item(trig)
        ok, err = _validate_trigger(fixed, hass)
        if not ok:
            return False, err, None
        # Remove None-valued to/from (LLM sometimes emits explicit nulls)
        for key in ("to", "from"):
            if key in fixed and fixed[key] is None:
                fixed.pop(key)
        normalized_triggers.append(fixed)

    # --- Condition normalization -------------------------------------------
    # Apply the same shape gates the trigger block uses: unknown condition
    # types and slug `device_id`s on device conditions cause HA to reject
    # the whole automation at reload, after the YAML is already on disk.
    normalized_conditions: list[dict[str, Any]] = []
    for cond in conditions:
        norm_cond = _normalize_item(cond)
        ok, err = _validate_condition(norm_cond, hass)
        if not ok:
            return False, err, None
        normalized_conditions.append(norm_cond)

    # --- Action normalization ----------------------------------------------
    # When hass is available, validate actions against the service registry:
    # 1. The action's domain.service must exist (rejects e.g. binary_sensor.turn_on).
    # 2. Target entity domains must have *some* service registered — this catches
    #    cross-domain calls like homeassistant.turn_on targeting binary_sensor.motion
    #    (binary_sensor has no services at all, so it's read-only).
    # Both gates run over every service call in the action tree, not just the
    # top-level ones: a call buried in a choose/if/repeat/parallel branch (e.g.
    # an unregistered tts.cloud_say that _rewrite_announcements couldn't repair
    # because no TTS engine is available) must be rejected exactly like a
    # top-level one, never persisted as a silent/non-existent service.
    normalized_actions: list[dict[str, Any]] = []
    for act in actions:
        norm_act = _normalize_item(act)
        if hass is not None:
            # Repair the silent-announcement defect: media_player.play_media
            # handed spoken text never produces audio. Retarget to tts.speak
            # (recursively, incl. control-flow branches) before the
            # service/entity gates below validate the result.
            norm_act = _rewrite_announcements(norm_act, hass)
            for svc_act in _iter_service_actions([norm_act]):
                action_service = str(svc_act.get("action", svc_act.get("service", "")))
                if "." in action_service and "{{" not in action_service:
                    svc_domain, svc_name = action_service.split(".", 1)
                    if svc_domain and not hass.services.has_service(svc_domain, svc_name):
                        return (
                            False,
                            f"action uses non-existent service '{action_service}'",
                            None,
                        )
                # Reject targets in read-only domains (no services registered at all)
                target = svc_act.get("target", {})
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

    # Recurse into action control-flow (`choose`, `if`, `repeat`, etc.) so
    # condition blocks embedded inside an action branch get the same
    # validation as top-level conditions — otherwise a slug `device_id`
    # inside `choose[].conditions` would pass here and explode at reload.
    for norm_act in normalized_actions:
        ok, err = _validate_action_conditions(norm_act, hass)
        if not ok:
            return False, err, None

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
        if isinstance(action, list):
            # HA accepts nested action-sequence lists (e.g.
            # ``parallel: [[{...}], [{...}]]``); recurse so service calls
            # inside them aren't skipped — _rewrite_announcements walks this
            # same shape, so the validator must too or an unrepairable missing
            # service buried in such a branch slips through.
            yield from _iter_service_actions(action)
            continue
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


def _resolve_automation_entity_id(hass: HomeAssistant, automation_id: str) -> str | None:
    """Return the entity_id of the automation with this unique_id, or ``None``."""
    entity_reg = er.async_get(hass)
    for entity in entity_reg.entities.values():
        if entity.platform == "automation" and entity.unique_id == automation_id:
            return entity.entity_id
    return None


def _resolve_live_enabled_state(hass: HomeAssistant, automation_id: str) -> bool | None:
    """Return the automation's definitive live enabled state, or ``None``.

    Resolves the automation entity via the registry (unique_id == automation_id)
    and reads its live state from the state machine. Returns ``True``/``False``
    only for a definitive `"on"`/`"off"`; returns ``None`` when there is no signal
    — the entity is missing or the state is transient (`unavailable` / `unknown`
    during startup or reload) — so callers can fall back rather than treat
    "unknown" as disabled.
    """
    entity_id = _resolve_automation_entity_id(hass, automation_id)
    if entity_id is None:
        return None
    state = hass.states.get(entity_id)
    if state is not None and state.state in (STATE_ON, STATE_OFF):
        return state.state == STATE_ON
    return None


class _RuntimeToggleWatcher:
    """Records the automation's latest externally-applied enabled state during an update.

    Subscribes to the entity's ``state_changed`` events, so only a *successful*
    toggle counts: a rejected/failed/unauthorized ``turn_on`` / ``turn_off`` /
    ``toggle`` (however targeted — direct, ``all``, area/device/label, routed
    ``homeassistant.*``) produces no state change and is ignored.

    Only genuine *live transitions* — where both ``old_state`` and ``new_state``
    exist — are recorded. Our own ``automation.reload`` tears the entity down and
    rebuilds it (``EntityComponent`` reset removes every entity, then re-adds from
    the new config), so its transitions are a removal (``new_state`` is ``None``)
    and an add (``old_state`` is ``None``) — both ignored. This lets a user toggle
    that lands *during* the reload (after the rebuild, on the live entity) still be
    honored, instead of being dropped by a blanket suppression.
    """

    def __init__(self, hass: HomeAssistant, entity_id: str) -> None:
        self._entity_id = entity_id
        self.latest: bool | None = None
        self._ignore_context_ids: set[str] = set()
        # A raw EVENT_STATE_CHANGED bus listener (not async_track_state_change_event,
        # which defers @callback dispatch to the next loop iteration): a @callback bus
        # listener runs synchronously during async_fire, so `latest` is up to date the
        # instant a blocking restore/reload returns — required for the restore-race
        # re-check to be reliable.
        self._unsub = hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_event)

    def ignore_context(self, context: Context) -> None:
        """Ignore state changes caused by this context (e.g. our own restore call),
        so the watcher keeps detecting *external* toggles through the restore."""
        self._ignore_context_ids.add(context.id)

    @callback
    def _on_event(self, event: Event) -> None:
        if event.data.get("entity_id") != self._entity_id:
            return
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        # Ignore add (old is None) and remove (new is None): those are the reload
        # rebuilding the entity, not a user toggle.
        if old_state is None or new_state is None:
            return
        # Ignore our own restore's write so it isn't mistaken for an external toggle.
        if event.context is not None and event.context.id in self._ignore_context_ids:
            return
        if new_state.state in (STATE_ON, STATE_OFF):
            self.latest = new_state.state == STATE_ON

    def stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None


async def _restore_runtime_enabled_state(
    hass: HomeAssistant,
    automation_id: str,
    enabled: bool,
    *,
    context: Context | None = None,
) -> None:
    """Re-apply a runtime enabled state without rewriting automations.yaml.

    Resolves the automation entity and calls ``automation.turn_on``/``turn_off`` so
    the reload's boot-override/RestoreState result is nudged back to the state the
    automation was actually in. The YAML `initial_state` (startup preference) is
    deliberately left untouched. The optional ``context`` tags the call so a
    concurrency watcher can tell our own write apart from an external toggle.
    Best-effort: never raises.
    """
    entity_id = _resolve_automation_entity_id(hass, automation_id)
    if entity_id is None:
        return
    service = "turn_on" if enabled else "turn_off"
    try:
        # blocking so the runtime state is actually applied before we return —
        # otherwise a rapid subsequent edit could capture the reload-selected
        # state, and events right after the save could be missed/mishandled.
        await hass.services.async_call(
            "automation", service, {"entity_id": entity_id}, blocking=True, context=context
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning(
            "Could not restore runtime state for %s after reload: %s", automation_id, exc
        )


async def async_update_automation(
    hass: HomeAssistant,
    automation_id: str,
    updated: dict[str, Any],
    *,
    session_id: str | None = None,
    version_message: str = "Updated via YAML editor",
    preserve_enabled_state: bool = True,
) -> bool:
    """Replace an existing automation (by id) in automations.yaml and reload.

    ``preserve_enabled_state`` (default) keeps the automation's active/inactive
    status identical across the forced ``automation.reload`` *without* altering its
    startup preference: the on-disk `initial_state` boot override is written back
    verbatim (or left omitted), any stale `initial_state` in ``updated`` is
    discarded, and the pre-edit live runtime state is re-applied afterwards via a
    non-persisting ``turn_on``/``turn_off`` (or, if the user toggled during the
    write+reload, that newer choice). So a temporarily UI/service-toggled automation
    keeps its current state now yet still honors its configured boot override on the
    next restart. Callers that mean to change the enabled state (the
    MCP enable/disable flow, an explicit YAML-editor save, a version restore) pass
    ``False``: the submitted payload is authoritative as-is — a submitted
    `initial_state` is honored, and omitting it removes any existing boot override.
    """
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
        # In preserve mode, capture the current live runtime state so it can be
        # re-applied after the forced `automation.reload` below. `None` when
        # indeterminate (missing entity / transient unavailable / unknown) — nothing
        # to restore then. Captured *inside* the lock, immediately before the
        # read/write, so a toggle performed while this request was queued behind an
        # overlapping update isn't clobbered by a stale pre-lock snapshot.
        captured_live: bool | None = None
        toggle_watcher: _RuntimeToggleWatcher | None = None
        if preserve_enabled_state:
            captured_live = _resolve_live_enabled_state(hass, automation_id)
            # Watch for runtime toggles that land between now and the restore below.
            # A user/integration `turn_on`/`turn_off` doesn't take AUTOMATIONS_YAML_LOCK,
            # so it can slip in during the (potentially slow) write+reload; the watcher
            # lets us honor that newest choice instead of restoring the older snapshot.
            watched_entity_id = _resolve_automation_entity_id(hass, automation_id)
            if watched_entity_id is not None:
                toggle_watcher = _RuntimeToggleWatcher(hass, watched_entity_id)

        # Everything after the watcher exists must run under cleanup: a raise from
        # the read, the entry loop, or the pre-write dump would otherwise leak the
        # event-bus listener (it processes every service call until unsubscribed).
        try:
            existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)

            found = False
            for i, a in enumerate(existing):
                if a.get("id") == automation_id:
                    updated["id"] = automation_id
                    # `initial_state` is a boot override, not a runtime flag: when
                    # present HA forces that state on the `automation.reload` below;
                    # when absent HA restores the automation's last (live) state.
                    if preserve_enabled_state:
                        # Refine/content edit: keep the on-disk boot override
                        # *verbatim* (copy when present, keep omitted when absent),
                        # discarding any stale `initial_state` in the incoming YAML
                        # (the store's versioned copy is captured at create time and
                        # isn't updated when the user later toggles the automation).
                        # The current runtime state is preserved separately, after
                        # the reload, so a temporary UI/service toggle never rewrites
                        # the user's startup preference.
                        if "initial_state" in a:
                            updated["initial_state"] = a["initial_state"]
                        else:
                            updated.pop("initial_state", None)
                    # Explicit mode (MCP enable/disable, YAML-editor save, version
                    # restore): the submitted payload is authoritative exactly as-is —
                    # a submitted boolean is honored, and an omitted key removes any
                    # existing boot override (restoring HA's last-state behavior).
                    existing[i] = updated
                    found = True
                    break

            if not found:
                _LOGGER.error("Automation id %s not found in automations.yaml", automation_id)
                return False

            # Capture the version YAML from the clean dict BEFORE writing. The
            # writer's _quote_yaml_booleans mutates `updated` in place (it lives
            # in `existing`), wrapping time/bool strings in ruamel scalar types;
            # PyYAML's dumper would then serialize those as `!!python/object/new`
            # tags into the stored version (and the refine context fed to the
            # LLM). Dumping first keeps the version YAML plain.
            yaml_text = yaml.dump(updated, allow_unicode=True, default_flow_style=False)

            try:
                await hass.async_add_executor_job(
                    _write_automations_yaml, automations_path, existing
                )
                _LOGGER.info("Updated automation: %s", automation_id)
                await hass.services.async_call("automation", "reload", blocking=True)

                # Restore the runtime state without touching the YAML: the reload
                # applied the boot override (or RestoreState), which can differ from
                # the state the automation was actually in. Honor any *successful*
                # toggle that landed during the write, the reload, OR the restore
                # itself (an actual live state change — a failed/unauthorized call
                # leaves the state untouched and is never observed; the reload's own
                # rebuild is ignored) over the captured snapshot. The watcher stays
                # active through the restore (our own write filtered out by context),
                # and we re-apply the newest choice if a user toggle races us — so a
                # toggle during the awaited restore isn't silently clobbered. Bounded
                # so a persistent toggler can't loop us. Best-effort.
                if preserve_enabled_state:
                    restore_ctx = Context()
                    if toggle_watcher is not None:
                        toggle_watcher.ignore_context(restore_ctx)
                    for _ in range(2):
                        desired_state = captured_live
                        if toggle_watcher is not None and toggle_watcher.latest is not None:
                            desired_state = toggle_watcher.latest
                        if desired_state is None:
                            break
                        await _restore_runtime_enabled_state(
                            hass, automation_id, desired_state, context=restore_ctx
                        )
                        if (
                            toggle_watcher is None
                            or toggle_watcher.latest is None
                            or toggle_watcher.latest == desired_state
                        ):
                            break

                # Record version
                store = _get_automation_store(hass)
                await store.add_version(
                    automation_id, yaml_text, updated, version_message, session_id
                )

                return True
            except Exception as exc:
                _LOGGER.exception("Failed to update automation: %s", exc)
                return False
        finally:
            # Idempotent — no-op if already stopped on the success path.
            if toggle_watcher is not None:
                toggle_watcher.stop()


async def async_create_automation(
    hass: HomeAssistant,
    suggestion: AutomationDict | dict[str, Any],
    *,
    session_id: str | None = None,
    version_message: str = "Created",
    enabled: bool = False,
    bypass_risk_gate: bool = False,
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

    ``bypass_risk_gate`` keeps an elevated-risk automation enabled despite
    that gate. It exists for the explicit command-approval flow: the user
    has already authorised these specific calls through the approval card,
    so a one-shot absolute-time schedule must be allowed to fire (the
    relative-delay path executes them directly, with no gate, so the two
    must agree). Never set this for LLM-proposed automations.
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
        if bypass_risk_gate:
            _LOGGER.warning(
                "Keeping elevated-risk automation '%s' enabled — risk gate "
                "bypassed for a user-approved scheduled action (flags=%s)",
                alias,
                risk.get("flags"),
            )
        else:
            _LOGGER.warning(
                "Forcing initial_state=False for elevated-risk automation '%s' (flags=%s)",
                alias,
                risk.get("flags"),
            )
            enabled = False
            forced_disabled = True

    short_id = uuid.uuid4().hex[:8]
    automation_id = f"{AUTOMATION_ID_PREFIX}{short_id}"
    # Ensure the Selora AI label exists in HA's label registry; we'll
    # attach it to the entity AFTER the automation.reload service has
    # materialised the new entity. Critically we do NOT write
    # ``labels:`` into the YAML — that key isn't part of HA's
    # automation schema and including it makes the entity load as
    # "unavailable" because schema validation rejects the unknown
    # field.
    # Strip any legacy ``[Selora AI]`` markers the input might still
    # carry — they used to be unconditionally prepended and
    # occasionally doubled up when a refinement round-tripped through
    # the LLM.
    await _ensure_selora_label(hass)
    clean_alias = _strip_legacy_selora_prefix(alias) or alias
    raw_description = suggestion.get("description") or clean_alias
    clean_description = _strip_legacy_selora_prefix(raw_description) or clean_alias
    automation = {
        "id": automation_id,
        "alias": clean_alias,
        "description": clean_description,
        "initial_state": enabled,
        "triggers": triggers,
        "conditions": conditions or [],
        "actions": actions,
        "mode": normalized.get("mode", "single"),
    }

    async with AUTOMATIONS_YAML_LOCK:
        existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)
        existing.append(automation)

        # Capture the version YAML before writing — _write_automations_yaml's
        # _quote_yaml_booleans mutates `automation` in place (wrapping
        # time/bool strings in ruamel scalar types), which PyYAML would then
        # serialize as `!!python/object/new` tags. Dump first to keep it plain.
        yaml_text = yaml.dump(automation, allow_unicode=True, default_flow_style=False)

        try:
            await hass.async_add_executor_job(_write_automations_yaml, automations_path, existing)
            _LOGGER.info("Created new automation: %s", alias)

            # Reload HA automations (blocking so entities exist before we return)
            await hass.services.async_call("automation", "reload", blocking=True)

            # Attach the Selora AI label to the new automation entity
            # via the entity registry — best-effort, the YAML write
            # above is what makes the automation functional.
            await _attach_selora_label_to_entity(hass, automation_id)

            # Record first version
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
            record_activity(hass, "automations_enabled" if enable else "automations_disabled")
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
