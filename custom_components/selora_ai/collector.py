"""Data Collector — gathers HA data, analyzes with LLM, creates automations.

    HA data sources → DataCollector → LLMClient → automations.yaml + logging

Data sources:
1. Entity Registry: what devices/entities exist
2. State Machine: current state of every entity
3. Recorder (SQLite): historical state changes for pattern detection
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    AUTOMATION_ID_PREFIX,
    DEFAULT_PUSH_INTERVAL,
    DEFAULT_RECORDER_LOOKBACK_DAYS,
    DOMAIN,
    CONF_COLLECTOR_ENABLED,
    CONF_COLLECTOR_MODE,
    CONF_COLLECTOR_INTERVAL,
    CONF_COLLECTOR_START_TIME,
    CONF_COLLECTOR_END_TIME,
    MODE_SCHEDULED,
    DEFAULT_COLLECTOR_ENABLED,
    DEFAULT_COLLECTOR_MODE,
    DEFAULT_COLLECTOR_INTERVAL,
)
from .automation_utils import validate_automation_payload
from .llm_client import LLMClient

_LOGGER = logging.getLogger(__name__)


class DataCollector:
    """Collects HA data, runs LLM analysis, logs suggestions."""

    def __init__(
        self,
        hass: HomeAssistant,
        llm: LLMClient,
        lookback_days: int = DEFAULT_RECORDER_LOOKBACK_DAYS,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self._hass = hass
        self._llm = llm
        self._lookback_days = lookback_days
        self._settings = settings or {}
        self._unsub_timer = None
        self._unsub_purge_timer = None

    async def async_start(self) -> None:
        """Start the periodic collection → analysis → log cycle."""
        enabled = self._settings.get(CONF_COLLECTOR_ENABLED, DEFAULT_COLLECTOR_ENABLED)
        if not enabled:
            _LOGGER.info("Data collector is disabled in settings")
            return

        interval = self._settings.get(CONF_COLLECTOR_INTERVAL, DEFAULT_COLLECTOR_INTERVAL)

        try:
            await self._collect_analyze_log()
        except Exception:
            _LOGGER.exception("Initial collection cycle failed — will retry on next interval")

        self._unsub_timer = async_track_time_interval(
            self._hass,
            self._scheduled_cycle,
            timedelta(seconds=interval),
        )
        _LOGGER.info("Data collector started (interval: %ss)", interval)

        # Daily purge of expired soft-deleted automations
        self._unsub_purge_timer = async_track_time_interval(
            self._hass,
            self._scheduled_purge,
            timedelta(days=1),
        )

    async def async_stop(self) -> None:
        """Stop the periodic timers."""
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        if self._unsub_purge_timer:
            self._unsub_purge_timer()
            self._unsub_purge_timer = None

    async def _scheduled_purge(self, _now: datetime) -> None:
        """Daily timer callback: purge expired soft-deleted automations."""
        try:
            from .automation_utils import async_purge_deleted_automations
            purged = await async_purge_deleted_automations(self._hass)
            if purged:
                _LOGGER.info("Daily purge removed %d expired automations: %s", len(purged), purged)
        except Exception:
            _LOGGER.exception("Daily automation purge failed")

    async def _scheduled_cycle(self, _now: datetime) -> None:
        """Timer callback."""
        # Respect schedule window
        mode = self._settings.get(CONF_COLLECTOR_MODE, DEFAULT_COLLECTOR_MODE)
        if mode == MODE_SCHEDULED:
            start_str = self._settings.get(CONF_COLLECTOR_START_TIME, "09:00")
            end_str = self._settings.get(CONF_COLLECTOR_END_TIME, "17:00")
            
            if not self._is_within_window(start_str, end_str):
                _LOGGER.debug("Outside scheduled window (%s - %s), skipping collection", start_str, end_str)
                return

        try:
            await self._collect_analyze_log()
        except Exception:
            _LOGGER.exception("Scheduled collection cycle failed")

    def _is_within_window(self, start_time: str, end_time: str) -> bool:
        """Check if current local time is within the HH:MM window."""
        try:
            now = datetime.now().time()
            start = datetime.strptime(start_time, "%H:%M").time()
            end = datetime.strptime(end_time, "%H:%M").time()

            if start <= end:
                return start <= now <= end
            else:
                # Spans midnight (e.g., 22:00 to 04:00)
                return now >= start or now <= end
        except ValueError:
            _LOGGER.error("Invalid time format in settings: %s or %s", start_time, end_time)
            return True # Default to allowed if config is broken

    async def _collect_analyze_log(self) -> None:
        """Full cycle: collect → LLM analysis → log suggestions."""
        if not self._llm:
            _LOGGER.debug("Skipping collection cycle: No LLM configured")
            return

        # Step 1: Build the home data snapshot
        snapshot = {
            "devices": self._collect_devices(),
            "entity_states": self._collect_entity_states(),
            "automations": self._collect_automations(),
            "recorder_history": await self._collect_recorder_history(),
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }

        _LOGGER.info(
            "Collected snapshot: %d devices, %d entities, %d automations, %d history records",
            len(snapshot["devices"]),
            len(snapshot["entity_states"]),
            len(snapshot["automations"]),
            len(snapshot["recorder_history"]),
        )

        # Step 2: Feed snapshot to the configured LLM
        suggestions = await self._llm.analyze_home_data(snapshot)

        # Step 3: Log suggestions and store for UI
        if suggestions:
            _LOGGER.info(
                "Selora AI generated %d automation suggestions",
                len(suggestions),
            )
            
            # Enrich suggestions with YAML for UI preview
            from .const import DOMAIN
            import yaml
            
            enriched = []
            for s in suggestions:
                is_valid, reason, automation_preview = validate_automation_payload(s)
                if not is_valid or automation_preview is None:
                    _LOGGER.warning(
                        "Skipping invalid collector suggestion '%s': %s",
                        s.get("alias", "<missing alias>"),
                        reason,
                    )
                    continue

                suggestion = dict(s)
                suggestion["automation_yaml"] = yaml.dump(
                    automation_preview, default_flow_style=False, allow_unicode=True
                )
                suggestion["automation_data"] = automation_preview
                enriched.append(suggestion)

            filtered_out = len(suggestions) - len(enriched)
            if filtered_out:
                _LOGGER.warning("Filtered out %d invalid automation suggestions", filtered_out)

            # Store in hass.data for the side panel to fetch
            self._hass.data.setdefault(DOMAIN, {})
            self._hass.data[DOMAIN]["latest_suggestions"] = enriched

            self._notify_suggestions(enriched, created_count=0)
        else:
            _LOGGER.info("No new automation suggestions from LLM")

    async def _create_automations(self, suggestions: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Write valid suggestions to automations.yaml and reload HA automations.

        Automations are created **disabled** so the user can review first.
        Returns list of {"id": ..., "alias": ...} for each created automation.
        """
        automations_path = Path(self._hass.config.config_dir) / "automations.yaml"

        # Read existing automations
        try:
            existing = await self._hass.async_add_executor_job(
                self._read_automations_yaml, automations_path
            )
        except Exception:
            _LOGGER.exception("Failed to read automations.yaml")
            return []

        existing_aliases = {
            a.get("alias", "").lower() for a in existing if isinstance(a, dict)
        }

        new_automations = []
        for suggestion in suggestions:
            if not isinstance(suggestion, dict):
                continue

            is_valid, reason, normalized = validate_automation_payload(suggestion)
            if not is_valid or normalized is None:
                _LOGGER.debug("Skipping invalid suggestion: %s", reason)
                continue

            alias = normalized["alias"]

            # Skip duplicates
            if alias.lower() in existing_aliases:
                _LOGGER.debug("Skipping duplicate automation: %s", alias)
                continue

            triggers = normalized["trigger"]
            actions = normalized["action"]
            conditions = normalized["condition"]
            short_id = uuid.uuid4().hex[:8]
            description = normalized.get("description") or alias
            automation = {
                "id": f"{AUTOMATION_ID_PREFIX}{short_id}",
                "alias": alias,
                "description": f"[Selora AI] {description}",
                "initial_state": False,
                "trigger": triggers,
                "condition": conditions or [],
                "action": actions,
                "mode": normalized.get("mode", "single"),
            }

            new_automations.append(automation)
            existing_aliases.add(alias.lower())

        if not new_automations:
            _LOGGER.info("No new automations to create (all duplicates or invalid)")
            return []

        # Append and write
        existing.extend(new_automations)
        try:
            await self._hass.async_add_executor_job(
                self._write_automations_yaml, automations_path, existing
            )
        except Exception:
            _LOGGER.exception("Failed to write automations.yaml")
            return []

        # Reload HA automations so they appear immediately
        try:
            await self._hass.services.async_call("automation", "reload")
        except Exception:
            _LOGGER.warning("Failed to reload automations — restart HA to pick them up")

        # Record first version for each new automation in the lifecycle store
        try:
            from .automation_utils import _get_automation_store
            store = _get_automation_store(self._hass)
            for automation in new_automations:
                yaml_text = yaml.dump(automation, allow_unicode=True, default_flow_style=False)
                await store.add_version(
                    automation["id"],
                    yaml_text,
                    automation,
                    "Created by collector",
                    session_id=None,
                )
        except Exception:
            _LOGGER.exception("Failed to record automation versions in store")

        _LOGGER.info("Created %d new automations in automations.yaml", len(new_automations))
        return [{"id": a["id"], "alias": a["alias"]} for a in new_automations]

    @staticmethod
    def _read_automations_yaml(path: Path) -> list[dict[str, Any]]:
        """Read and parse automations.yaml (runs in executor)."""
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8").strip()
        if not text or text == "[]":
            return []
        data = yaml.safe_load(text)
        if isinstance(data, list):
            return data
        return []

    @staticmethod
    def _write_automations_yaml(path: Path, automations: list[dict[str, Any]]) -> None:
        """Write automations list to YAML atomically, preserving formatting (runs in executor).

        Writes to a temp file first, then renames — prevents corruption
        if the process crashes mid-write.
        """
        from ruamel.yaml import YAML
        ryaml = YAML()
        ryaml.default_flow_style = False
        ryaml.allow_unicode = True
        tmp_path = path.with_suffix(".yaml.tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            ryaml.dump(automations, fh)
        tmp_path.replace(path)  # atomic on POSIX

    def _notify_suggestions(self, suggestions: list[dict[str, Any]], created_count: int = 0) -> None:
        """Log suggestions (no persistent notifications)."""
        _LOGGER.info(
            "Selora AI generated %d suggestions (%d created as automations)",
            len(suggestions), created_count,
        )

    @staticmethod
    def _humanize_trigger(t: Any) -> str:
        """Convert a trigger dict to a readable string."""
        if not isinstance(t, dict):
            return str(t)
        platform = t.get("platform", "")
        if platform == "sun":
            return f"At {t.get('event', 'sunset')}"
        if platform == "time":
            time_val = t.get("at") or t.get("event", "")
            return f"At {time_val}" if time_val else "At a scheduled time"
        if platform == "zone":
            event = t.get("event", "enter")
            zone = t.get("entity_id", "zone.home").replace("zone.", "").replace("_", " ")
            return f"When someone {'leaves' if event == 'leave' else 'enters'} {zone}"
        if platform == "state":
            entity = t.get("entity_id", "unknown")
            to_state = t.get("to", "")
            from_state = t.get("from", "")
            parts = [f"{entity} changes"]
            if from_state:
                parts.append(f"from {from_state}")
            if to_state:
                parts.append(f"to {to_state}")
            return " ".join(parts)
        if platform == "numeric_state":
            entity = t.get("entity_id", "unknown")
            above = t.get("above")
            below = t.get("below")
            if above and below:
                return f"{entity} is between {above} and {below}"
            if above:
                return f"{entity} goes above {above}"
            if below:
                return f"{entity} goes below {below}"
            return f"{entity} value changes"
        return f"{platform}: {t.get('entity_id', '')}" if platform else str(t)

    @staticmethod
    def _humanize_action(a: Any) -> str:
        """Convert an action dict to a readable string."""
        if not isinstance(a, dict):
            return str(a)
        service = a.get("service", "")
        data = a.get("data", {})
        if not service:
            return str(a)
        # Make service name readable
        parts = service.replace(".", " → ", 1).replace("_", " ")
        target = data.get("entity_id", "")
        message = data.get("message", "")
        extras = []
        if target:
            extras.append(target)
        if message:
            extras.append(f'"{message}"')
        if extras:
            return f"{parts} ({', '.join(extras)})"
        return parts

    def _collect_devices(self) -> list[dict[str, Any]]:
        """Get all devices from the HA device registry."""
        registry = dr.async_get(self._hass)
        devices = []

        for device in registry.devices.values():
            devices.append(
                {
                    "id": device.id,
                    "name": device.name or device.name_by_user or "Unknown",
                    "manufacturer": device.manufacturer,
                    "model": device.model,
                    "area_id": device.area_id,
                    "connections": list(device.connections),
                    "identifiers": list(device.identifiers),
                    "via_device_id": device.via_device_id,
                }
            )

        return devices

    # Attribute keys safe to pass to the LLM — excludes PII and secrets
    _SAFE_ATTRIBUTES = {
        "friendly_name", "device_class", "unit_of_measurement", "icon",
        "supported_features", "min_temp", "max_temp", "target_temp_step",
        "min_mireds", "max_mireds", "brightness", "color_temp",
        "effect_list", "source_list", "sound_mode_list", "media_content_type",
    }

    def _collect_entity_states(self) -> list[dict[str, Any]]:
        """Get current states of all entities — point-in-time snapshot.

        Only includes safe, non-PII attributes to avoid leaking sensitive data
        (e.g. GPS coordinates, tokens, IP addresses) to the LLM.
        """
        states = []

        for state in self._hass.states.async_all():
            safe_attrs = {
                k: v for k, v in state.attributes.items()
                if k in self._SAFE_ATTRIBUTES
            }
            states.append(
                {
                    "entity_id": state.entity_id,
                    "state": state.state,
                    "attributes": safe_attrs,
                    "last_changed": state.last_changed.isoformat()
                    if state.last_changed
                    else None,
                    "last_updated": state.last_updated.isoformat()
                    if state.last_updated
                    else None,
                }
            )

        return states

    def _collect_automations(self) -> list[dict[str, Any]]:
        """Get existing automations so the LLM doesn't suggest duplicates."""
        automations = []

        for state in self._hass.states.async_all("automation"):
            automations.append(
                {
                    "entity_id": state.entity_id,
                    "alias": state.attributes.get("friendly_name", ""),
                    "state": state.state,
                    "last_triggered": state.attributes.get("last_triggered"),
                }
            )

        return automations

    async def _collect_recorder_history(self) -> list[dict[str, Any]]:
        """Pull historical state changes from HA Recorder (SQLite).

        Gives the LLM usage patterns for smarter suggestions:
        - What time do lights usually go on/off?
        - How often is the thermostat adjusted?
        - Devices that are always on but shouldn't be?
        """
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import (
                get_significant_states,
            )

            now = datetime.now(timezone.utc)
            start = now - timedelta(days=self._lookback_days)

            # HA 2025.1+ requires entity_ids — collect all from state machine
            entity_ids = [s.entity_id for s in self._hass.states.async_all()]
            if not entity_ids:
                return []

            states = await get_instance(self._hass).async_add_executor_job(
                get_significant_states,
                self._hass,
                start,
                now,
                entity_ids,
            )

            history = []
            for entity_id, entity_states in states.items():
                for state in entity_states:
                    history.append(
                        {
                            "entity_id": entity_id,
                            "state": state.state,
                            "last_changed": state.last_changed.isoformat()
                            if state.last_changed
                            else None,
                        }
                    )

            _LOGGER.debug(
                "Collected %d history records (%d day lookback)",
                len(history),
                self._lookback_days,
            )
            return history

        except ImportError:
            _LOGGER.warning("Recorder not available — skipping history")
            return []
        except Exception:
            _LOGGER.exception("Failed to collect recorder history")
            return []
