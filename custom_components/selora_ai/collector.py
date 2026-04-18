"""Data Collector — gathers HA data, analyzes with LLM, creates automations.

    HA data sources → DataCollector → LLMClient → automations.yaml + logging

Data sources:
1. Entity Registry: what devices/entities exist
2. State Machine: current state of every entity
3. Recorder (SQLite): historical state changes for pattern detection
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
import logging
from math import ceil
from pathlib import Path
import re
import time
from typing import TYPE_CHECKING, Any
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
import yaml

from .automation_utils import (
    assess_automation_risk,
    count_selora_automations,
    find_stale_automations,
    get_selora_automation_cap,
    suggestion_content_fingerprint,
    validate_automation_payload,
)
from .const import (
    ACTIVITY_HIGH_THRESHOLD,
    AUTOMATION_ID_PREFIX,
    AUTOMATION_STALE_DAYS,
    CATEGORY_LINK_WEIGHTS,
    COLLECTOR_DOMAINS,
    CONF_AUTO_PURGE_STALE,
    CONF_COLLECTOR_ENABLED,
    CONF_COLLECTOR_END_TIME,
    CONF_COLLECTOR_INTERVAL,
    CONF_COLLECTOR_MODE,
    CONF_COLLECTOR_START_TIME,
    DEFAULT_AUTO_PURGE_STALE,
    DEFAULT_CATEGORY_LINK_WEIGHT,
    DEFAULT_COLLECTOR_ENABLED,
    DEFAULT_COLLECTOR_INTERVAL,
    DEFAULT_COLLECTOR_MODE,
    DEFAULT_DEVICES_PER_SUGGESTION,
    DEFAULT_LLM_TIMEOUT,
    DEFAULT_MAX_SUGGESTIONS_CEILING,
    DEFAULT_MIN_SUGGESTIONS,
    DEFAULT_RECORDER_LOOKBACK_DAYS,
    DOMAIN,
    LIGHT_ENTITY_EXCLUDE_PATTERNS,
    MIN_RELEVANCE_SCORE,
    MODE_SCHEDULED,
    RELEVANCE_WEIGHT_ACTIVITY,
    RELEVANCE_WEIGHT_CATEGORY,
    RELEVANCE_WEIGHT_CATEGORY_LINK,
    RELEVANCE_WEIGHT_COMPLEXITY,
    RELEVANCE_WEIGHT_COVERAGE,
    RELEVANCE_WEIGHT_CROSS_DEVICE,
)
from .llm_client import LLMClient

if TYPE_CHECKING:
    from collections.abc import Callable

    from .pattern_store import PatternStore
    from .types import AutomationDict, HomeSnapshot

_LOGGER = logging.getLogger(__name__)
_STALE_NOTIFICATION_ID = "selora_ai_stale_automations"


class DataCollector:
    """Collects HA data, runs LLM analysis, logs suggestions."""

    def __init__(
        self,
        hass: HomeAssistant,
        llm: LLMClient,
        lookback_days: int = DEFAULT_RECORDER_LOOKBACK_DAYS,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self._hass: HomeAssistant = hass
        self._llm: LLMClient = llm
        self._lookback_days: int = lookback_days
        self._settings: dict[str, Any] = settings or {}
        self._unsub_timer: Callable[[], None] | None = None
        self._feedback_cache: str | None = None
        self._feedback_cache_time: float = 0.0

    def _get_pattern_store(self) -> PatternStore | None:
        """Find the PatternStore from any active config entry."""
        domain_data = self._hass.data.get(DOMAIN, {})
        for key, val in domain_data.items():
            if key.startswith("_") or not isinstance(val, dict):
                continue
            store = val.get("pattern_store")
            if store is not None:
                return store
        return None

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

    async def async_stop(self) -> None:
        """Stop the periodic timers."""
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None

    async def _scheduled_cycle(self, _now: datetime) -> None:
        """Timer callback."""
        # Respect schedule window
        mode = self._settings.get(CONF_COLLECTOR_MODE, DEFAULT_COLLECTOR_MODE)
        if mode == MODE_SCHEDULED:
            start_str = self._settings.get(CONF_COLLECTOR_START_TIME, "09:00")
            end_str = self._settings.get(CONF_COLLECTOR_END_TIME, "17:00")

            if not self._is_within_window(start_str, end_str):
                _LOGGER.debug(
                    "Outside scheduled window (%s - %s), skipping collection", start_str, end_str
                )
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
            return True  # Default to allowed if config is broken

    async def _calculate_dynamic_cap(self) -> int:
        """Calculate suggestion cap proportional to uncovered devices.

        Uses the entity registry to map devices → entities, then reads
        automations.yaml to determine which entity_ids are already
        covered by triggers, actions, and conditions.
        """
        device_reg = dr.async_get(self._hass)
        entity_reg = er.async_get(self._hass)

        total_devices = len(device_reg.devices)

        # Build device_id → set[entity_id] from the entity registry
        device_entities: dict[str, set[str]] = {}
        for entry in entity_reg.entities.values():
            if entry.device_id:
                device_entities.setdefault(entry.device_id, set()).add(entry.entity_id)

        # Read the full automation configs from automations.yaml.
        # State attributes only carry friendly_name/last_triggered/id,
        # not the trigger/action/condition entity_ids we need.
        from .automation_utils import _read_automations_yaml

        automations_path = Path(self._hass.config.config_dir) / "automations.yaml"
        automations = await self._hass.async_add_executor_job(
            _read_automations_yaml, automations_path
        )

        covered_entity_ids: set[str] = set()
        for automation in automations:
            covered_entity_ids.update(self._extract_entity_ids(automation))

        # Count devices whose entities are NOT covered by any automation
        uncovered_count = 0
        for device_id in device_reg.devices:
            entities = device_entities.get(device_id, set())
            if not entities or not entities & covered_entity_ids:
                uncovered_count += 1

        cap = max(
            DEFAULT_MIN_SUGGESTIONS,
            min(
                ceil(uncovered_count / DEFAULT_DEVICES_PER_SUGGESTION),
                DEFAULT_MAX_SUGGESTIONS_CEILING,
            ),
        )
        _LOGGER.debug(
            "Dynamic suggestion cap: %d (uncovered devices: %d/%d)",
            cap,
            uncovered_count,
            total_devices,
        )
        return cap

    async def _collect_analyze_log(self) -> None:
        """Full cycle: collect → LLM analysis → log suggestions."""
        if not self._llm:
            _LOGGER.debug("Skipping collection cycle: No LLM configured")
            return

        # Step 0: Check automation cap before doing any work
        cap = get_selora_automation_cap(self._hass)
        current_count = count_selora_automations(self._hass, enabled_only=True)
        if current_count >= cap:
            stale = find_stale_automations(self._hass)
            _LOGGER.info(
                "Selora automation cap reached (%d/%d). "
                "%d stale automations found (not triggered in %d days)",
                current_count,
                cap,
                len(stale),
                AUTOMATION_STALE_DAYS,
            )
            if stale:
                auto_purge = self._settings.get(CONF_AUTO_PURGE_STALE, DEFAULT_AUTO_PURGE_STALE)

                if auto_purge:
                    from .automation_utils import async_delete_automations_batch

                    # Only delete enough to drop below the cap
                    excess = current_count - cap + 1
                    to_purge = stale[:excess]
                    ids_to_purge = [s["automation_id"] for s in to_purge]

                    removed = await async_delete_automations_batch(self._hass, ids_to_purge)
                    if removed:
                        removed_lines = "\n".join(f"- {name}" for name in removed)
                        _LOGGER.info("Auto-purged %d stale automations", len(removed))
                        with contextlib.suppress(Exception):
                            await self._hass.services.async_call(
                                "persistent_notification",
                                "create",
                                {
                                    "title": "Selora AI: stale automations removed",
                                    "message": (
                                        f"Selora auto-removed {len(removed)} "
                                        f"automation{'s' if len(removed) != 1 else ''} "
                                        f"that hadn't triggered in "
                                        f"{AUTOMATION_STALE_DAYS} days:"
                                        f"\n\n{removed_lines}"
                                    ),
                                    "notification_id": _STALE_NOTIFICATION_ID,
                                },
                            )

                    # Re-check: if still at cap after purge, skip LLM
                    if count_selora_automations(self._hass, enabled_only=True) >= cap:
                        return
                else:
                    stale_lines = "\n".join(
                        f"- **{s['alias']}** (last triggered: {s['last_triggered'] or 'never'})"
                        for s in stale
                    )
                    with contextlib.suppress(Exception):
                        await self._hass.services.async_call(
                            "persistent_notification",
                            "create",
                            {
                                "title": "Selora AI: automation cap reached",
                                "message": (
                                    f"Selora has reached its automation cap "
                                    f"({current_count}/{cap}). "
                                    f"New suggestions are paused until space "
                                    f"is freed up."
                                    f"\n\nThe following automations haven't "
                                    f"triggered in {AUTOMATION_STALE_DAYS} "
                                    f"days and can be removed:"
                                    f"\n\n{stale_lines}"
                                ),
                                "notification_id": _STALE_NOTIFICATION_ID,
                            },
                        )
                    return
            else:
                return

        # Cap not reached — dismiss any leftover stale notification
        with contextlib.suppress(Exception):
            await self._hass.services.async_call(
                "persistent_notification",
                "dismiss",
                {"notification_id": _STALE_NOTIFICATION_ID},
            )

        # Step 1: Build the home data snapshot
        snapshot: HomeSnapshot = {  # type: ignore[typeddict-unknown-key]
            "devices": self._collect_devices(),
            "entity_states": self._collect_entity_states(),
            "automations": self._collect_automations(),
            "recorder_history": await self._collect_recorder_history(),
            "collected_at": datetime.now(UTC).isoformat(),
        }

        _LOGGER.info(
            "Collected snapshot: %d devices, %d entities, %d automations, %d history records",
            len(snapshot["devices"]),
            len(snapshot["entity_states"]),
            len(snapshot["automations"]),
            len(snapshot["recorder_history"]),
        )

        # Change 7: Warn if no recorder history available
        if not snapshot["recorder_history"]:
            _LOGGER.warning(
                "No recorder history available — suggestions will be based on "
                "current state only (is the recorder integration enabled?)"
            )

        # Compute the dynamic suggestion cap and propagate it to the LLM
        # client so the prompt asks for the right amount and parsing allows
        # them through.
        dynamic_cap = await self._calculate_dynamic_cap()
        self._llm.set_max_suggestions(dynamic_cap)

        # Inject user feedback context into snapshot for LLM prompt (#80)
        feedback_summary = await self._build_feedback_summary()
        if feedback_summary:
            snapshot["_feedback_summary"] = feedback_summary

        # Step 2: Feed snapshot to the configured LLM (with timeout)
        try:
            suggestions = await asyncio.wait_for(
                self._llm.analyze_home_data(snapshot),
                timeout=DEFAULT_LLM_TIMEOUT + 10,
            )
        except TimeoutError:
            _LOGGER.warning(
                "LLM analysis timed out after %ds — skipping this cycle",
                DEFAULT_LLM_TIMEOUT + 10,
            )
            return

        # Step 3: Log suggestions and store for UI
        if suggestions:
            _LOGGER.info(
                "Selora AI generated %d automation suggestions",
                len(suggestions),
            )

            # Build set of existing automation aliases to prevent re-suggesting
            existing_aliases: set[str] = set()
            for state in self._hass.states.async_all("automation"):
                alias = (state.attributes.get("friendly_name") or "").strip().lower()
                if alias:
                    existing_aliases.add(alias)

            # Filter out suggestions that duplicate existing automations
            novel: list[dict[str, Any]] = []
            for s in suggestions:
                alias = (s.get("alias") or "").strip().lower()
                if alias in existing_aliases:
                    _LOGGER.debug("Skipping suggestion that already exists: %s", s.get("alias"))
                    continue
                novel.append(s)

            if len(novel) < len(suggestions):
                _LOGGER.info(
                    "Filtered %d suggestions that duplicate existing automations",
                    len(suggestions) - len(novel),
                )

            # Deduplicate remaining by trigger+action content,
            # also filtering out hashes matching previously deleted automations
            deleted_hashes: set[str] = set()
            try:
                from .pattern_store import PatternStore

                pattern_store = PatternStore(self._hass)
                deleted_hashes = await pattern_store.get_deleted_hashes()
            except Exception:
                _LOGGER.debug("Could not load deleted automation hashes")

            seen_hashes: set[str] = set()
            unique_suggestions: list[dict[str, Any]] = []
            deleted_count = 0
            for s in novel:
                h = suggestion_content_fingerprint(s)
                if h in deleted_hashes:
                    _LOGGER.debug(
                        "Skipping suggestion matching deleted automation: %s",
                        s.get("alias", "<no alias>"),
                    )
                    deleted_count += 1
                    continue
                if h in seen_hashes:
                    _LOGGER.debug(
                        "Skipping duplicate suggestion: %s",
                        s.get("alias", "<no alias>"),
                    )
                    continue
                seen_hashes.add(h)
                unique_suggestions.append(s)

            if deleted_count:
                _LOGGER.info(
                    "Filtered %d suggestions matching previously deleted automations",
                    deleted_count,
                )
            if len(unique_suggestions) < len(novel) - deleted_count:
                _LOGGER.info(
                    "Removed %d duplicate suggestions",
                    len(novel) - deleted_count - len(unique_suggestions),
                )

            # Enrich suggestions with YAML for UI preview
            enriched = []
            for s in unique_suggestions:
                is_valid, reason, automation_preview = validate_automation_payload(s, self._hass)
                if not is_valid or automation_preview is None:
                    _LOGGER.warning(
                        "Skipping invalid collector suggestion '%s': %s",
                        s.get("alias", "<missing alias>"),
                        reason,
                    )
                    continue

                # Validate entity IDs referenced in the suggestion
                self._validate_entity_ids(s)

                suggestion = dict(s)
                suggestion["automation_yaml"] = yaml.dump(
                    automation_preview, default_flow_style=False, allow_unicode=True
                )
                suggestion["automation_data"] = automation_preview
                suggestion["risk_assessment"] = assess_automation_risk(automation_preview)
                enriched.append(suggestion)

            filtered_out = len(unique_suggestions) - len(enriched)
            if filtered_out:
                _LOGGER.warning("Filtered out %d invalid automation suggestions", filtered_out)

            # Build set of entity_ids referenced by existing automations for scoring
            # Read from automations.yaml (state attributes don't contain full config)
            existing_auto_entity_ids: set[str] = set()
            try:
                automations_path = Path(self._hass.config.config_dir) / "automations.yaml"
                existing_automations = await self._hass.async_add_executor_job(
                    self._read_automations_yaml, automations_path
                )
                for auto in existing_automations:
                    existing_auto_entity_ids.update(self._extract_entity_ids(auto))
            except Exception:
                _LOGGER.debug("Could not read automations.yaml for coverage scoring")

            # Pre-compute entity change counts once for all suggestions
            history = snapshot.get("recorder_history", [])
            entity_change_counts: dict[str, int] = {}
            for h in history:
                eid = h.get("entity_id")
                if eid:
                    entity_change_counts[eid] = entity_change_counts.get(eid, 0) + 1

            # Score and filter suggestions by relevance
            pre_score_count = len(enriched)
            scored: list[dict[str, Any]] = []
            for s in enriched:
                score = self._score_suggestion(
                    s, snapshot, existing_auto_entity_ids, entity_change_counts
                )
                s["relevance_score"] = score
                if score < MIN_RELEVANCE_SCORE:
                    _LOGGER.info(
                        "Filtering low-relevance suggestion '%s' (score: %.2f < %.2f)",
                        s.get("alias", "<no alias>"),
                        score,
                        MIN_RELEVANCE_SCORE,
                    )
                    continue
                scored.append(s)

            # Sort by relevance (highest first) so any downstream cap keeps the best
            scored.sort(key=lambda s: s.get("relevance_score", 0), reverse=True)
            enriched = scored

            if len(enriched) < pre_score_count:
                _LOGGER.info(
                    "Relevance filtering kept %d of %d suggestions",
                    len(enriched),
                    pre_score_count,
                )

            # Apply dynamic suggestion cap (computed before the LLM call)
            if len(enriched) > dynamic_cap:
                _LOGGER.info(
                    "Capping suggestions from %d to %d (dynamic cap based on home size)",
                    len(enriched),
                    dynamic_cap,
                )
                enriched = enriched[:dynamic_cap]

            # Filter suggestions that match recently dismissed ones
            # (by content hash or normalized alias)
            pattern_store = self._get_pattern_store()
            if pattern_store:
                recently_dismissed = await pattern_store.get_recently_dismissed_suggestions()
                if recently_dismissed:
                    dismissed_hashes: set[str] = set()
                    dismissed_aliases: set[str] = set()
                    for d in recently_dismissed:
                        # Build content hash from dismissed automation data
                        auto_data = d.get("automation_data")
                        if auto_data:
                            dismissed_hashes.add(suggestion_content_fingerprint(auto_data))
                        # Normalize dismissed alias
                        alias = d.get("automation_data", {}).get("alias") or d.get(
                            "description", ""
                        )
                        if alias:
                            dismissed_aliases.add(self._normalize_alias(alias))

                    pre_dismiss_count = len(enriched)
                    filtered = []
                    for s in enriched:
                        # Check content hash
                        auto_data = s.get("automation_data", s)
                        content_hash = suggestion_content_fingerprint(auto_data)
                        if content_hash in dismissed_hashes:
                            _LOGGER.info(
                                "Suppressing previously dismissed suggestion (content match): '%s'",
                                s.get("alias", "<no alias>"),
                            )
                            continue

                        # Check normalized alias
                        alias = self._normalize_alias(s.get("alias", ""))
                        if alias and alias in dismissed_aliases:
                            _LOGGER.info(
                                "Suppressing previously dismissed suggestion (alias match): '%s'",
                                s.get("alias", "<no alias>"),
                            )
                            continue

                        filtered.append(s)
                    enriched = filtered

                    if len(enriched) < pre_dismiss_count:
                        _LOGGER.info(
                            "Dismissal filter removed %d re-suggested automation(s)",
                            pre_dismiss_count - len(enriched),
                        )

            # Validate service/entity compatibility
            compat_filtered = []
            for s in enriched:
                is_compat, reason = self._validate_service_entity_compat(s)
                if not is_compat:
                    _LOGGER.warning(
                        "Filtering suggestion '%s': %s",
                        s.get("alias", "<no alias>"),
                        reason,
                    )
                    continue
                compat_filtered.append(s)
            enriched = compat_filtered

            # Store in hass.data for the side panel to fetch
            self._hass.data.setdefault(DOMAIN, {})
            self._hass.data[DOMAIN]["latest_suggestions"] = enriched

            self._notify_suggestions(enriched, created_count=0)
        else:
            _LOGGER.info("No new automation suggestions from LLM")

    async def _create_automations(self, suggestions: list[AutomationDict]) -> list[dict[str, str]]:
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

        existing_aliases = {a.get("alias", "").lower() for a in existing if isinstance(a, dict)}

        new_automations = []
        for suggestion in suggestions:
            if not isinstance(suggestion, dict):
                continue

            is_valid, reason, normalized = validate_automation_payload(suggestion, self._hass)
            if not is_valid or normalized is None:
                _LOGGER.debug("Skipping invalid suggestion: %s", reason)
                continue

            alias = normalized["alias"]

            # Skip duplicates
            if alias.lower() in existing_aliases:
                _LOGGER.debug("Skipping duplicate automation: %s", alias)
                continue

            triggers = normalized["triggers"]
            actions = normalized["actions"]
            conditions = normalized["conditions"]
            short_id = uuid.uuid4().hex[:8]
            description = normalized.get("description") or alias
            automation = {
                "id": f"{AUTOMATION_ID_PREFIX}{short_id}",
                "alias": alias,
                "description": f"[Selora AI] {description}",
                "initial_state": False,
                "triggers": triggers,
                "conditions": conditions or [],
                "actions": actions,
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
            await self._hass.services.async_call("automation", "reload", blocking=True)
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

    def _notify_suggestions(
        self, suggestions: list[dict[str, Any]], created_count: int = 0
    ) -> None:
        """Log suggestions (no persistent notifications)."""
        _LOGGER.info(
            "Selora AI generated %d suggestions (%d created as automations)",
            len(suggestions),
            created_count,
        )

    @staticmethod
    def _humanize_trigger(t: dict[str, Any] | Any) -> str:
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
    def _humanize_action(a: dict[str, Any] | Any) -> str:
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
        try:
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
        except Exception:
            _LOGGER.exception("Failed to collect devices from registry")
            return []

    # Attribute keys safe to pass to the LLM — excludes PII and secrets
    _SAFE_ATTRIBUTES = {
        "friendly_name",
        "device_class",
        "unit_of_measurement",
        "icon",
        "supported_features",
        "min_temp",
        "max_temp",
        "target_temp_step",
        "min_mireds",
        "max_mireds",
        "brightness",
        "color_temp",
        "effect_list",
        "source_list",
        "sound_mode_list",
        "media_content_type",
        # Climate
        "current_temperature",
        "current_humidity",
        "target_temperature",
        "hvac_modes",
        "preset_modes",
        "fan_modes",
        # Sensor / energy
        "state_class",
        "last_reset",
        # Cover
        "current_position",
        "current_tilt_position",
        # Presence / device tracker
        "source_type",
    }

    def _collect_entity_states(self) -> list[dict[str, Any]]:
        """Get current states of interesting entities — point-in-time snapshot.

        Only includes domains in COLLECTOR_DOMAINS and safe, non-PII attributes
        to avoid leaking sensitive data (e.g. GPS coordinates, tokens, IP
        addresses) to the LLM.  Disabled entities (e.g. switches converted to
        lights via HA's "Show as" feature) are excluded.
        """
        try:
            from .entity_filter import EntityFilter

            all_states = self._hass.states.async_all()
            ef = EntityFilter(self._hass, [s.entity_id for s in all_states])
            states = []

            for state in all_states:
                try:
                    domain = state.entity_id.split(".")[0]
                    if domain not in COLLECTOR_DOMAINS:
                        continue
                    if not ef.is_active(state.entity_id):
                        continue
                    if domain == "light" and any(
                        pat in state.entity_id for pat in LIGHT_ENTITY_EXCLUDE_PATTERNS
                    ):
                        continue

                    safe_attrs = {
                        k: v for k, v in state.attributes.items() if k in self._SAFE_ATTRIBUTES
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
                except Exception:
                    _LOGGER.warning(
                        "Skipping entity %s due to error", state.entity_id, exc_info=True
                    )

            return states
        except Exception:
            _LOGGER.exception("Failed to collect entity states")
            return []

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

    @staticmethod
    def _normalize_alias(alias: str) -> str:
        """Normalize an alias for comparison: strip prefix, lowercase, collapse whitespace."""
        normalized = alias.strip().lower()
        # Strip [Selora AI] or [selora ai] prefix
        normalized = re.sub(r"^\[selora\s*ai\]\s*", "", normalized)
        # Collapse whitespace
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized

    def _validate_service_entity_compat(self, suggestion: dict[str, Any]) -> tuple[bool, str]:
        """Check that action services are valid for the target entity domains."""
        automation = suggestion.get("automation_data", suggestion)
        actions = automation.get("action") or automation.get("actions") or []
        if isinstance(actions, dict):
            actions = [actions]

        for act in actions:
            if not isinstance(act, dict):
                continue
            service = act.get("service", "")
            if "." not in service:
                continue
            service_domain = service.split(".")[0]

            # Get target entity_id(s)
            entity_id = act.get("entity_id")
            target = act.get("target", {})
            if not entity_id and isinstance(target, dict):
                entity_id = target.get("entity_id")

            if not entity_id:
                continue

            if isinstance(entity_id, str):
                entity_ids = [entity_id]
            elif isinstance(entity_id, list):
                entity_ids = entity_id
            else:
                continue

            for eid in entity_ids:
                if not isinstance(eid, str) or "." not in eid:
                    continue
                entity_domain = eid.split(".")[0]

                # Service domain must match entity domain
                # (or be a generic service like homeassistant, notify, etc.)
                _GENERIC_DOMAINS = {
                    "homeassistant",
                    "notify",
                    "persistent_notification",
                    "script",
                    "scene",
                    "input_boolean",
                    "input_number",
                    "input_select",
                    "input_text",
                    "input_datetime",
                }
                if service_domain not in _GENERIC_DOMAINS and service_domain != entity_domain:
                    return False, (
                        f"Service '{service}' incompatible with entity '{eid}' "
                        f"(domain mismatch: {service_domain} != {entity_domain})"
                    )

        return True, ""

    def _validate_entity_ids(self, automation: dict[str, Any]) -> None:
        """Warn about entity IDs in a suggestion that don't exist in HA."""
        found = self._extract_entity_ids(automation)
        if not found:
            return

        known = {s.entity_id for s in self._hass.states.async_all()}
        for eid in found:
            if eid not in known:
                _LOGGER.warning(
                    "Suggestion '%s' references unknown entity_id: %s",
                    automation.get("alias", "<no alias>"),
                    eid,
                )

    @staticmethod
    def _extract_entity_ids(config: dict[str, Any] | list[Any] | Any) -> set[str]:
        """Recursively extract entity_id values from any nested config structure."""
        entity_ids: set[str] = set()
        if config is None:
            return entity_ids
        if isinstance(config, list):
            for item in config:
                entity_ids.update(DataCollector._extract_entity_ids(item))
        elif isinstance(config, dict):
            for key, value in config.items():
                if key == "entity_id":
                    if isinstance(value, str):
                        entity_ids.add(value)
                    elif isinstance(value, list):
                        entity_ids.update(e for e in value if isinstance(e, str))
                elif isinstance(value, (dict, list)):
                    entity_ids.update(DataCollector._extract_entity_ids(value))
        return entity_ids

    def _score_suggestion(
        self,
        suggestion: dict[str, Any],
        snapshot: HomeSnapshot,
        existing_entity_ids: set[str],
        entity_change_counts: dict[str, int] | None = None,
    ) -> float:
        """Score a suggestion 0.0-1.0 based on multiple relevance factors."""
        automation = suggestion.get("automation_data", suggestion)
        scores: dict[str, float] = {}

        # 1. Cross-device: trigger and action reference different domains/devices
        trigger_config = (
            automation.get("trigger") if "trigger" in automation else automation.get("triggers")
        )
        action_config = (
            automation.get("action") if "action" in automation else automation.get("actions")
        )
        trigger_entities = self._extract_entity_ids(trigger_config)
        action_entities = self._extract_entity_ids(action_config)
        trigger_domains = {e.split(".")[0] for e in trigger_entities if "." in e}
        action_domains = {e.split(".")[0] for e in action_entities if "." in e}

        if trigger_entities and action_entities:
            if trigger_entities.isdisjoint(action_entities):
                scores["cross_device"] = 1.0  # Different entities
            elif trigger_domains != action_domains:
                scores["cross_device"] = 0.7  # Different domains at least
            else:
                scores["cross_device"] = 0.1  # Same device acting on itself
        else:
            scores["cross_device"] = 0.5  # Can't determine, neutral

        # 2. Activity-aligned: score based on how frequently trigger entities
        # change state. More frequent = higher score (capped at ACTIVITY_HIGH_THRESHOLD).
        if entity_change_counts is None:
            # Fallback: build counts from snapshot (used by tests)
            history = snapshot.get("recorder_history", [])
            entity_change_counts = {}
            for h in history:
                eid = h.get("entity_id")
                if eid:
                    entity_change_counts[eid] = entity_change_counts.get(eid, 0) + 1

        if trigger_entities:
            entity_scores = []
            for e in trigger_entities:
                count = entity_change_counts.get(e, 0)
                entity_scores.append(min(count / ACTIVITY_HIGH_THRESHOLD, 1.0))
            scores["activity"] = sum(entity_scores) / len(entity_scores)
        else:
            scores["activity"] = 0.3  # No triggers identifiable, slight penalty

        # 3. Coverage: entities not already covered by existing automations
        all_entities = trigger_entities | action_entities
        if all_entities:
            novel_count = sum(1 for e in all_entities if e not in existing_entity_ids)
            scores["coverage"] = novel_count / len(all_entities)
        else:
            scores["coverage"] = 0.0

        # 4. Category: safety/security/energy automations get a boost
        boosted_domains = {
            "alarm_control_panel",
            "lock",
            "binary_sensor",
            "climate",
            "water_heater",
            "cover",
        }
        all_domains = trigger_domains | action_domains
        if all_domains & boosted_domains:
            scores["category"] = 1.0
        else:
            scores["category"] = 0.4  # Not penalized, just not boosted

        # 5. Complexity: conditions, multiple actions, time constraints
        condition_config = (
            automation.get("condition")
            if "condition" in automation
            else automation.get("conditions")
        )
        has_conditions = bool(condition_config)
        action_list = action_config or []
        multi_action = isinstance(action_list, list) and len(action_list) > 1
        has_mode = automation.get("mode") in ("queued", "restart", "parallel")

        complexity_signals = sum([has_conditions, multi_action, has_mode])
        scores["complexity"] = min(complexity_signals / 2, 1.0)

        # 6. Category link quality (#79): score based on how well
        # trigger/action domain pairs work together in automations.
        if trigger_domains and action_domains:
            pair_scores: list[float] = []
            for t_domain in trigger_domains:
                for a_domain in action_domains:
                    if t_domain == a_domain:
                        pair_scores.append(0.5)  # Same domain, neutral
                    else:
                        pair_key = frozenset({t_domain, a_domain})
                        pair_scores.append(
                            CATEGORY_LINK_WEIGHTS.get(pair_key, DEFAULT_CATEGORY_LINK_WEIGHT)
                        )
            scores["category_link"] = sum(pair_scores) / len(pair_scores)
        else:
            scores["category_link"] = DEFAULT_CATEGORY_LINK_WEIGHT

        # Weighted average
        weighted = (
            scores.get("cross_device", 0) * RELEVANCE_WEIGHT_CROSS_DEVICE
            + scores.get("activity", 0) * RELEVANCE_WEIGHT_ACTIVITY
            + scores.get("coverage", 0) * RELEVANCE_WEIGHT_COVERAGE
            + scores.get("category", 0) * RELEVANCE_WEIGHT_CATEGORY
            + scores.get("complexity", 0) * RELEVANCE_WEIGHT_COMPLEXITY
            + scores.get("category_link", 0) * RELEVANCE_WEIGHT_CATEGORY_LINK
        )
        return round(weighted, 3)

    _FEEDBACK_CACHE_TTL = 300  # seconds — feedback rarely changes mid-cycle

    async def _build_feedback_summary(self) -> str:
        """Build an LLM-readable summary of user decisions (#80).

        Retrieves accepted and declined suggestions from the PatternStore
        and formats them so the LLM can learn user preferences.  The result
        is cached for ``_FEEDBACK_CACHE_TTL`` seconds to avoid re-reading
        the store on every analysis cycle.
        """
        now = time.monotonic()
        if (
            self._feedback_cache is not None
            and now - self._feedback_cache_time < self._FEEDBACK_CACHE_TTL
        ):
            return self._feedback_cache

        result = await self._fetch_feedback_summary()
        self._feedback_cache = result
        self._feedback_cache_time = now
        return result

    async def _fetch_feedback_summary(self) -> str:
        """Fetch and format feedback from the PatternStore (uncached)."""
        pattern_store = self._get_pattern_store()
        if not pattern_store:
            return ""

        try:
            feedback = await pattern_store.get_feedback_summary()
        except Exception:
            _LOGGER.debug("Could not load feedback summary from pattern store")
            return ""

        accepted = feedback.get("accepted", [])
        declined = feedback.get("declined", [])

        if not accepted and not declined:
            return ""

        lines = ["USER FEEDBACK (learn from past decisions):"]

        if accepted:
            lines.append(
                f"  Accepted automations ({len(accepted)} total) — suggest MORE like these:"
            )
            seen: set[str] = set()
            for s in accepted:
                desc = self._truncate(s.get("description", ""), 80)
                if desc and desc not in seen:
                    seen.add(desc)
                    lines.append(f"    + {desc}")

        if declined:
            lines.append(
                f"  Declined automations ({len(declined)} total) — suggest FEWER like these:"
            )
            seen_d: set[str] = set()
            for s in declined:
                desc = self._truncate(s.get("description", ""), 80)
                reason = s.get("dismissal_reason") or "no reason given"
                if desc and desc not in seen_d:
                    seen_d.add(desc)
                    lines.append(f"    - {desc} (reason: {reason})")

        return "\n".join(lines)

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        """Truncate *text* to *limit* chars, adding '…' if shortened."""
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

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

            now = datetime.now(UTC)
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
