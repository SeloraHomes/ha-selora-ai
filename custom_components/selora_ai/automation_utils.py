
import logging
import uuid
import yaml
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from .const import AUTOMATION_ID_PREFIX

_LOGGER = logging.getLogger(__name__)

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

async def async_create_automation(hass: HomeAssistant, suggestion: dict[str, Any]) -> bool:
    """Write a single automation suggestion to automations.yaml and reload."""
    automations_path = Path(hass.config.config_dir) / "automations.yaml"

    # Read existing automations
    existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)

    alias = suggestion.get("alias", "").strip()
    if not alias:
        return False

    # Normalize trigger/action
    triggers = suggestion.get("triggers") or suggestion.get("trigger", [])
    actions = suggestion.get("actions") or suggestion.get("action", [])
    conditions = suggestion.get("conditions") or suggestion.get("condition", [])

    if not triggers or not actions:
        _LOGGER.error("Automation suggestion missing triggers or actions: %s", alias)
        return False

    # Ensure lists
    if not isinstance(triggers, list):
        triggers = [triggers]
    if not isinstance(actions, list):
        actions = [actions]
    if conditions and not isinstance(conditions, list):
        conditions = [conditions]

    short_id = uuid.uuid4().hex[:8]
    automation = {
        "id": f"{AUTOMATION_ID_PREFIX}{short_id}",
        "alias": alias,
        "description": f"[Selora AI] {suggestion.get('description', alias)}",
        "initial_state": False, # Start disabled for review
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
        return True
    except Exception as exc:
        _LOGGER.exception("Failed to create automation: %s", exc)
        return False
