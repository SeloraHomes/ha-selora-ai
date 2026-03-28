from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
import uuid

from homeassistant.core import HomeAssistant
import yaml

from .const import AUTOMATION_ID_PREFIX, AUTOMATION_SOFT_DELETE_DAYS

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


def _read_automations_yaml(path: Path) -> list[dict[str, Any]]:
    """Read and parse automations.yaml (runs in executor)."""
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8").strip()
        if not text or text == "[]":
            return []
        data = yaml.safe_load(text)
        if isinstance(data, list):
            return data
    except Exception as exc:
        _LOGGER.error("Error reading automations.yaml: %s", exc)
    return []


def _write_automations_yaml(path: Path, automations: list[dict[str, Any]]) -> None:
    """Write automations list to YAML atomically, preserving formatting."""
    from ruamel.yaml import YAML

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

    normalized = {
        "alias": alias,
        "description": str(automation.get("description", "")).strip(),
        "trigger": normalized_triggers,
        "condition": conditions,
        "action": actions,
        "mode": automation.get("mode", "single"),
    }

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

    alias = suggestion.get("alias", "").strip()
    if not alias:
        return {"success": False, "automation_id": None}

    # Normalize trigger/action
    triggers = suggestion.get("triggers") or suggestion.get("trigger", [])
    actions = suggestion.get("actions") or suggestion.get("action", [])
    conditions = suggestion.get("conditions") or suggestion.get("condition", [])

    if not triggers or not actions:
        _LOGGER.error("Automation suggestion missing triggers or actions: %s", alias)
        return {"success": False, "automation_id": None}

    # Ensure lists
    if not isinstance(triggers, list):
        triggers = [triggers]
    if not isinstance(actions, list):
        actions = [actions]
    if conditions and not isinstance(conditions, list):
        conditions = [conditions]

    short_id = uuid.uuid4().hex[:8]
    automation_id = f"{AUTOMATION_ID_PREFIX}{short_id}"
    raw_initial_state = suggestion.get("initial_state", True)
    initial_state = raw_initial_state if isinstance(raw_initial_state, bool) else True
    automation = {
        "id": automation_id,
        "alias": alias,
        "description": f"[Selora AI] {suggestion.get('description', alias)}",
        "initial_state": initial_state,
        "trigger": triggers,
        "condition": conditions or [],
        "action": actions,
        "mode": "single",
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


async def async_soft_delete_automation(hass: HomeAssistant, automation_id: str) -> bool:
    """Disable the automation in automations.yaml and mark it deleted in the store.

    The automation is NOT removed from the file — it is disabled (initial_state: False)
    so HA hides it from the active automations list. Permanent removal happens only
    after the 30-day retention window via async_purge_deleted_automations().
    """
    automations_path = Path(hass.config.config_dir) / "automations.yaml"
    existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)

    found = False
    for automation in existing:
        if automation.get("id") == automation_id:
            automation["initial_state"] = False
            found = True
            break

    if not found:
        _LOGGER.error(
            "Automation id %s not found in automations.yaml for soft-delete", automation_id
        )
        return False

    try:
        await hass.async_add_executor_job(_write_automations_yaml, automations_path, existing)
        await hass.services.async_call("automation", "reload")
        store = _get_automation_store(hass)

        # Bootstrap a store record for automations created before store tracking was introduced.
        # soft_delete() returns False when no record exists; in that case we create a minimal
        # "imported" version entry so the automation is trackable, then mark it deleted.
        if not await store.soft_delete(automation_id):
            _LOGGER.info(
                "Automation %s has no store record — bootstrapping before soft-delete",
                automation_id,
            )
            # Find the automation's current YAML from the (already updated) file
            target = next((a for a in existing if a.get("id") == automation_id), {})
            yaml_text = yaml.dump(target, allow_unicode=True, default_flow_style=False)
            await store.add_version(
                automation_id,
                yaml_text,
                target,
                "imported",
                session_id=None,
                action="created",
            )
            await store.soft_delete(automation_id)

        _LOGGER.info("Soft-deleted automation: %s", automation_id)
        return True
    except Exception as exc:
        _LOGGER.exception("Failed to soft-delete automation %s: %s", automation_id, exc)
        return False


async def async_restore_automation(hass: HomeAssistant, automation_id: str) -> bool:
    """Re-enable a soft-deleted automation and clear its deleted_at in the store."""
    automations_path = Path(hass.config.config_dir) / "automations.yaml"
    existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)

    found = False
    for automation in existing:
        if automation.get("id") == automation_id:
            automation["initial_state"] = True
            found = True
            break

    if not found:
        _LOGGER.error("Automation id %s not found in automations.yaml for restore", automation_id)
        return False

    try:
        await hass.async_add_executor_job(_write_automations_yaml, automations_path, existing)
        await hass.services.async_call("automation", "reload")
        store = _get_automation_store(hass)
        await store.restore(automation_id)
        _LOGGER.info("Restored automation: %s", automation_id)
        return True
    except Exception as exc:
        _LOGGER.exception("Failed to restore automation %s: %s", automation_id, exc)
        return False


async def async_hard_delete_automation(
    hass: HomeAssistant,
    automation_store: AutomationStore,
    automation_id: str,
) -> None:
    """Permanently delete a soft-deleted automation and all version history.

    Raises ValueError if the automation is not soft-deleted.
    """
    record = await automation_store.get_record(automation_id)
    if not record or not record.get("deleted_at"):
        raise ValueError("Automation must be soft-deleted before hard delete")

    automations_path = Path(hass.config.config_dir) / "automations.yaml"
    existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)

    remaining = [a for a in existing if a.get("id") != automation_id]
    if len(remaining) == len(existing):
        raise ValueError("Automation not found in automations.yaml")

    await hass.async_add_executor_job(_write_automations_yaml, automations_path, remaining)
    await automation_store.purge_record(automation_id)
    await hass.services.async_call("automation", "reload")
    _LOGGER.info("Hard-deleted automation: %s", automation_id)


async def async_purge_deleted_automations(
    hass: HomeAssistant,
    older_than_days: int = AUTOMATION_SOFT_DELETE_DAYS,
) -> list[str]:
    """Permanently remove automations whose soft-delete window has expired.

    Removes both the store record and the automations.yaml entry.
    Returns the list of purged automation_ids.
    """
    store = _get_automation_store(hass)
    purged_ids = await store.purge_old_deleted(older_than_days)

    if not purged_ids:
        return []

    # Remove purged entries from automations.yaml
    automations_path = Path(hass.config.config_dir) / "automations.yaml"
    existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)
    purged_set = set(purged_ids)
    remaining = [a for a in existing if a.get("id") not in purged_set]

    if len(remaining) != len(existing):
        try:
            await hass.async_add_executor_job(_write_automations_yaml, automations_path, remaining)
            await hass.services.async_call("automation", "reload")
            _LOGGER.info(
                "Purged %d expired automations from automations.yaml: %s",
                len(purged_ids),
                purged_ids,
            )
        except Exception as exc:
            _LOGGER.exception("Failed to write automations.yaml during purge: %s", exc)

    return purged_ids
