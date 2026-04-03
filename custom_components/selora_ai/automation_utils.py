from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
import uuid

from homeassistant.core import HomeAssistant
import yaml

from .const import AUTOMATION_ID_PREFIX

if TYPE_CHECKING:
    from .automation_store import AutomationStore

_LOGGER = logging.getLogger(__name__)

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


def _read_automations_yaml(path: Path) -> list[dict[str, Any]]:
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
    """Wrap trigger to/from string values that YAML would parse as booleans.

    YAML 1.1 treats bare on/off/yes/no/true/false as booleans.  Wrapping
    these in DoubleQuotedScalarString forces ruamel.yaml to emit them
    with double-quotes so they survive a round-trip as strings.
    """
    from ruamel.yaml.scalarstring import DoubleQuotedScalarString

    _YAML_BOOL_STRINGS = frozenset(
        {
            "true",
            "false",
            "yes",
            "no",
            "on",
            "off",
            "y",
            "n",
        }
    )

    for auto in automations:
        triggers = auto.get("trigger") or auto.get("triggers") or []
        if not isinstance(triggers, list):
            triggers = [triggers]
        for trig in triggers:
            if not isinstance(trig, dict):
                continue
            for key in ("to", "from"):
                val = trig.get(key)
                if isinstance(val, bool):
                    trig[key] = DoubleQuotedScalarString("on" if val else "off")
                elif isinstance(val, str) and val.lower() in _YAML_BOOL_STRINGS:
                    trig[key] = DoubleQuotedScalarString(val)
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


def _parse_automation_yaml(yaml_text: str) -> dict[str, Any] | None:
    """Parse a YAML string into an automation dict (runs in executor). Returns None on error."""
    try:
        data = yaml.safe_load(yaml_text)
        if isinstance(data, dict):
            return data
    except yaml.YAMLError as exc:
        _LOGGER.error("YAML parse error: %s", exc)
    return None


def validate_automation_payload(
    automation: dict[str, Any] | None,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Validate and normalize automation payload before it is shown or persisted."""
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

    normalized_triggers: list[dict[str, Any]] = []
    for trig in triggers:
        fixed_trigger = dict(trig)
        if not fixed_trigger.get("platform") and fixed_trigger.get("trigger"):
            fixed_trigger["platform"] = fixed_trigger.pop("trigger")
        if not fixed_trigger.get("platform"):
            return False, "each trigger must include a platform", None

        # HA state triggers require 'to' and 'from' to be strings.
        # LLMs often produce boolean values (true/false) instead of the
        # string equivalents ("on"/"off").  Coerce them here so the
        # automation passes HA schema validation at runtime.
        for key in ("to", "from"):
            if key in fixed_trigger and not isinstance(fixed_trigger[key], str):
                val = fixed_trigger[key]
                if isinstance(val, bool):
                    fixed_trigger[key] = "on" if val else "off"
                elif val is None:
                    fixed_trigger.pop(key, None)
                else:
                    fixed_trigger[key] = str(val)

        normalized_triggers.append(fixed_trigger)

    _VALID_MODES = {"single", "restart", "queued", "parallel"}
    mode = str(automation.get("mode", "single")).strip().lower()
    if mode not in _VALID_MODES:
        mode = "single"

    raw_initial_state = automation.get("initial_state")
    initial_state = raw_initial_state if isinstance(raw_initial_state, bool) else None

    normalized: dict[str, Any] = {
        "alias": alias,
        "description": str(automation.get("description", "")).strip(),
        "trigger": normalized_triggers,
        "condition": conditions,
        "action": actions,
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


def assess_automation_risk(automation: dict[str, Any]) -> dict[str, Any]:
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

    referenced_entity_ids: set[str] = set()

    def _collect_entity_ids(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == "entity_id":
                    if isinstance(value, str):
                        referenced_entity_ids.add(value)
                    elif isinstance(value, list):
                        for item in value:
                            if isinstance(item, str):
                                referenced_entity_ids.add(item)
                else:
                    _collect_entity_ids(value)
        elif isinstance(obj, list):
            for item in obj:
                _collect_entity_ids(item)

    _collect_entity_ids(triggers)
    _collect_entity_ids(actions)
    _collect_entity_ids(automation.get("condition") or automation.get("conditions") or [])

    for trigger in triggers:
        if not isinstance(trigger, dict):
            continue
        platform = str(trigger.get("platform") or trigger.get("trigger") or "").strip()
        if platform in _ELEVATED_RISK_TRIGGER_PLATFORMS:
            flags.append("remote_ingress_trigger")
            reasons.append("uses a webhook trigger, which creates a remotely invokable entry point")

    for action in actions:
        if not isinstance(action, dict):
            continue
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
    """Return (or lazily create) the AutomationStore from hass.data."""
    from .automation_store import AutomationStore
    from .const import DOMAIN

    domain_data = hass.data.setdefault(DOMAIN, {})
    if "_automation_store" not in domain_data:
        domain_data["_automation_store"] = AutomationStore(hass)
    return domain_data["_automation_store"]


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
    is_valid, reason, normalized = validate_automation_payload(updated)
    if not is_valid or normalized is None:
        _LOGGER.error("Invalid automation update for %s: %s", automation_id, reason)
        return False

    # Merge normalized trigger/action/condition back while preserving other fields
    updated["trigger"] = normalized["trigger"]
    updated["action"] = normalized["action"]
    updated["condition"] = normalized.get("condition", [])

    automations_path = Path(hass.config.config_dir) / "automations.yaml"
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
        await hass.services.async_call("automation", "reload")

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
    suggestion: dict[str, Any],
    *,
    session_id: str | None = None,
    version_message: str = "Created",
) -> dict[str, Any]:
    """Write a single automation suggestion to automations.yaml and reload.

    Returns a dict with keys: success (bool), automation_id (str | None).
    """
    automations_path = Path(hass.config.config_dir) / "automations.yaml"

    # Read existing automations
    existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)

    # Validate and normalize the suggestion (coerces boolean to/from → "on"/"off", etc.)
    is_valid, reason, normalized = validate_automation_payload(suggestion)
    if not is_valid or normalized is None:
        _LOGGER.error("Invalid automation suggestion: %s", reason)
        return {"success": False, "automation_id": None}

    alias = normalized["alias"]
    triggers = normalized["trigger"]
    actions = normalized["action"]
    conditions = normalized.get("condition", [])

    short_id = uuid.uuid4().hex[:8]
    automation_id = f"{AUTOMATION_ID_PREFIX}{short_id}"
    initial_state = normalized.get("initial_state", suggestion.get("initial_state", False))
    if not isinstance(initial_state, bool):
        initial_state = False
    automation = {
        "id": automation_id,
        "alias": alias,
        "description": f"[Selora AI] {suggestion.get('description', alias)}",
        "initial_state": initial_state,
        "trigger": triggers,
        "condition": conditions or [],
        "action": actions,
        "mode": normalized.get("mode", "single"),
    }

    existing.append(automation)

    try:
        await hass.async_add_executor_job(_write_automations_yaml, automations_path, existing)
        _LOGGER.info("Created new automation: %s", alias)

        # Reload HA automations
        await hass.services.async_call("automation", "reload")

        # Record first version
        yaml_text = yaml.dump(automation, allow_unicode=True, default_flow_style=False)
        store = _get_automation_store(hass)
        await store.add_version(automation_id, yaml_text, automation, version_message, session_id)

        return {"success": True, "automation_id": automation_id}
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
    existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)

    found = False
    for automation in existing:
        if automation.get("id") == automation_id:
            automation["initial_state"] = enable
            found = True
            break

    if not found:
        _LOGGER.error("Automation id %s not found in automations.yaml for toggle", automation_id)
        return False

    try:
        await hass.async_add_executor_job(_write_automations_yaml, automations_path, existing)
        service = "turn_on" if enable else "turn_off"
        await hass.services.async_call("automation", service, {"entity_id": entity_id})
        _LOGGER.info(
            "Toggled automation %s to %s", automation_id, "enabled" if enable else "disabled"
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
        await hass.services.async_call("automation", "reload")
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


async def _record_deletion_hash(hass: HomeAssistant, automation: dict) -> None:
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
            raw = automations_path.read_text(encoding="utf-8").strip()
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
