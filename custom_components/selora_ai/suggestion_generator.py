"""SuggestionGenerator — converts detected patterns into HA automation suggestions.

Each detected pattern is transformed into a valid Home Assistant automation
payload, validated, deduplicated, and saved to PatternStore as a proactive
suggestion with confidence score and human-readable evidence summary.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
import yaml

from .automation_utils import suggestion_content_fingerprint, validate_automation_payload

if TYPE_CHECKING:
    from .llm_client import LLMClient
    from .types import AutomationDict, PatternDict, SuggestionDict

from .const import (
    CONFIDENCE_MEDIUM,
    DISMISSAL_SUPPRESSION_WINDOW_DAYS,
    PATTERN_HISTORY_RETENTION_DAYS,
    PATTERN_TYPE_CORRELATION,
    PATTERN_TYPE_SEQUENCE,
    PATTERN_TYPE_TIME_BASED,
)
from .pattern_store import PatternStore

_LOGGER = logging.getLogger(__name__)


class SuggestionGenerator:
    """Converts detected patterns into actionable automation suggestions."""

    def __init__(
        self,
        hass: HomeAssistant,
        pattern_store: PatternStore,
        llm: LLMClient | None = None,
    ) -> None:
        self._hass = hass
        self._store = pattern_store
        self._llm = llm

    async def generate_from_patterns(self, patterns: list[PatternDict]) -> list[SuggestionDict]:
        """Convert patterns into automation suggestions.

        For each pattern above CONFIDENCE_MEDIUM:
        1. Skip if already has a pending suggestion
        2. Skip if pattern was dismissed within the suppression window (#44)
        3. Build a valid HA automation payload
        4. Validate through automation_utils
        5. Deduplicate against existing automations
        6. Deduplicate against other suggestions in this batch by content (#46)
        7. Deduplicate against already-stored suggestions by content (#46)
        8. Optionally enrich description via LLM (with dismissal context, #45)
        9. Save to pattern store
        """
        suggestions: list[SuggestionDict] = []
        existing_aliases = self._get_existing_aliases()

        # Build content fingerprints of already-stored suggestions (#46)
        stored_fingerprints = await self._get_stored_suggestion_fingerprints()

        # Track fingerprints within this batch to prevent intra-batch duplicates (#46)
        batch_fingerprints: set[str] = set()

        # Retry active patterns that failed validation on a previous cycle (#67).
        # scan() only returns newly detected or reactivated patterns, so an active
        # pattern whose entities were transiently unavailable would never be retried
        # without this backfill.
        patterns = await self._backfill_unsugested_patterns(patterns)

        recently_dismissed: list[
            SuggestionDict
        ] = await self._store.get_recently_dismissed_suggestions()
        dismissed_pattern_ids: set[str] = {
            s["pattern_id"] for s in recently_dismissed if s.get("pattern_id")
        }
        if dismissed_pattern_ids:
            _LOGGER.debug(
                "Dismissal suppression active for %d pattern(s) within %d-day window",
                len(dismissed_pattern_ids),
                DISMISSAL_SUPPRESSION_WINDOW_DAYS,
            )

        for pattern in patterns:
            if pattern["confidence"] < CONFIDENCE_MEDIUM:
                continue

            pattern_id = pattern.get("pattern_id", "")
            if pattern_id and await self._store.has_suggestion_for_pattern(pattern_id):
                continue

            # Skip patterns whose suggestions were recently dismissed (#44)
            if pattern_id and pattern_id in dismissed_pattern_ids:
                _LOGGER.debug(
                    "Suppressing suggestion for pattern %s — dismissed within %d-day window",
                    pattern_id,
                    DISMISSAL_SUPPRESSION_WINDOW_DAYS,
                )
                continue

            # Hardening: verify entities are valid and have recent activity (#67)
            if not await self._validate_suggestion_entities(pattern):
                _LOGGER.debug(
                    "Skipping pattern %s — entities failed hardening checks",
                    pattern_id,
                )
                continue

            automation = self._pattern_to_automation(pattern)
            if automation is None:
                continue

            # Deduplicate against existing automations
            alias_lower = automation.get("alias", "").lower()
            if alias_lower in existing_aliases:
                continue

            is_valid, reason, normalized = validate_automation_payload(automation, self._hass)
            if not is_valid or normalized is None:
                _LOGGER.debug(
                    "Pattern %s produced invalid automation: %s",
                    pattern_id,
                    reason,
                )
                continue

            # Deduplicate by trigger+action content fingerprint (#46)
            fingerprint = suggestion_content_fingerprint(normalized)
            if fingerprint in batch_fingerprints:
                _LOGGER.debug(
                    "Skipping duplicate suggestion in batch (same trigger+action): %s",
                    alias_lower,
                )
                continue
            if fingerprint in stored_fingerprints:
                _LOGGER.debug(
                    "Skipping suggestion that duplicates an already-stored suggestion: %s",
                    alias_lower,
                )
                continue
            batch_fingerprints.add(fingerprint)

            yaml_text = yaml.dump(normalized, allow_unicode=True, default_flow_style=False)

            suggestion: SuggestionDict = {
                "pattern_id": pattern_id,
                "source": "pattern",
                "confidence": pattern["confidence"],
                "automation_data": normalized,
                "automation_yaml": yaml_text,
                "description": pattern["description"],
                "evidence_summary": self._build_evidence_summary(pattern),
            }

            sid = await self._store.save_suggestion(suggestion)
            suggestion["suggestion_id"] = sid
            suggestions.append(suggestion)

        if suggestions:
            _LOGGER.info("Generated %d proactive suggestions from patterns", len(suggestions))

        return suggestions

    async def enrich_pending(self) -> int:
        """Batch-enrich unenriched suggestions via a single LLM call.

        Scans the store for pending suggestions with source="pattern"
        (not yet enriched) so it survives restarts without a persistent
        queue.  Returns the number of suggestions enriched.
        """
        if not self._llm:
            return 0

        pending = await self._store.get_suggestions(status="pending")
        unenriched = [s for s in pending if s.get("source") == "pattern" and s.get("suggestion_id")]
        if not unenriched:
            return 0

        items = []
        for i, s in enumerate(unenriched, 1):
            items.append(
                f"{i}. Pattern: {s.get('description', '')}\n"
                f"   Evidence: {s.get('evidence_summary', '')}"
            )
        prompt = (
            "Rewrite these automation descriptions to be clear and friendly "
            "for a homeowner (one sentence each, no technical jargon).\n"
            "Reply with a JSON array of improved descriptions, in the same order.\n\n"
            + "\n".join(items)
        )

        try:
            result, _ = await asyncio.wait_for(
                self._llm.send_request(
                    system=(
                        "You rewrite smart home automation descriptions. "
                        "Reply with only a JSON array of strings."
                    ),
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=30,
            )
            if not result:
                return 0

            descriptions = json.loads(result.strip())
            if not isinstance(descriptions, list) or len(descriptions) != len(unenriched):
                _LOGGER.debug(
                    "Batch enrichment returned %d descriptions for %d items, skipping",
                    len(descriptions) if isinstance(descriptions, list) else 0,
                    len(unenriched),
                )
                return 0

            enriched = 0
            for s, new_desc in zip(unenriched, descriptions, strict=False):
                if isinstance(new_desc, str) and new_desc.strip():
                    await self._store.update_suggestion_fields(
                        s["suggestion_id"],
                        description=new_desc.strip(),
                        source="hybrid",
                    )
                    enriched += 1

            if enriched:
                _LOGGER.info("Batch-enriched %d suggestion descriptions via LLM", enriched)
            return enriched
        except TimeoutError:
            _LOGGER.debug("Batch LLM enrichment timed out, descriptions unchanged")
        except (json.JSONDecodeError, ValueError):
            _LOGGER.debug("Batch LLM enrichment returned invalid JSON, descriptions unchanged")
        except Exception:
            _LOGGER.debug("Batch LLM enrichment failed, descriptions unchanged")
        return 0

    def _get_existing_aliases(self) -> set[str]:
        """Collect lowercase aliases of all existing automations."""
        aliases: set[str] = set()
        for state in self._hass.states.async_all("automation"):
            alias = state.attributes.get("friendly_name", "")
            if alias:
                aliases.add(alias.lower())
        return aliases

    async def _get_stored_suggestion_fingerprints(self) -> set[str]:
        """Build content fingerprints of all pending/snoozed suggestions in the store."""
        fingerprints: set[str] = set()
        for s in await self._store.get_suggestions(status="pending"):
            auto_data = s.get("automation_data", {})
            if auto_data:
                fingerprints.add(suggestion_content_fingerprint(auto_data))
        for s in await self._store.get_suggestions(status="snoozed"):
            auto_data = s.get("automation_data", {})
            if auto_data:
                fingerprints.add(suggestion_content_fingerprint(auto_data))
        return fingerprints

    async def _backfill_unsugested_patterns(self, patterns: list[PatternDict]) -> list[PatternDict]:
        """Merge active patterns that lack suggestions into the candidate list.

        PatternEngine.scan() only returns newly detected or reactivated patterns.
        If a previous validation failed transiently (entity unavailable at HA
        startup, brief device outage), the active pattern won't be emitted again.
        This backfills those orphaned patterns so they are retried each cycle.
        """
        incoming_ids = {p.get("pattern_id") for p in patterns if p.get("pattern_id")}
        active_patterns = await self._store.get_patterns(status="active")
        backfilled = list(patterns)
        for p in active_patterns:
            pid = p.get("pattern_id", "")
            if (
                pid
                and pid not in incoming_ids
                and not await self._store.has_suggestion_for_pattern(pid)
            ):
                backfilled.append(p)
        return backfilled

    async def _validate_suggestion_entities(self, pattern: PatternDict) -> bool:
        """Validate that a pattern's entities are real, controllable, and recently active.

        Returns True if the suggestion is valid, False if it should be skipped.
        This prevents suggesting automations for entities that:
        - No longer exist in HA
        - Are unavailable or disabled
        - Have no recent activity (stale patterns from old data)
        """
        entity_ids = pattern.get("entity_ids", [])
        if not entity_ids:
            return False

        evidence = pattern.get("evidence", {})

        for entity_id in entity_ids:
            # Check entity exists in the state machine
            state = self._hass.states.get(entity_id)
            if state is None:
                _LOGGER.debug("Entity %s not found in state machine", entity_id)
                return False
            if state.state in ("unavailable", "unknown"):
                _LOGGER.debug("Entity %s is %s", entity_id, state.state)
                return False

        # Check trigger entity has recent activity within the retention window
        trigger_entity = evidence.get("trigger_entity", entity_ids[0])
        since = datetime.now(tz=UTC) - timedelta(days=PATTERN_HISTORY_RETENTION_DAYS)
        history = await self._store.get_entity_history(trigger_entity, since=since)
        if len(history) < 2:
            _LOGGER.debug(
                "Trigger entity %s has insufficient recent history (%d entries in last %d days)",
                trigger_entity,
                len(history),
                PATTERN_HISTORY_RETENTION_DAYS,
            )
            return False

        return True

    def _pattern_to_automation(self, pattern: PatternDict) -> AutomationDict | None:
        """Convert a pattern into a valid HA automation dict."""
        ptype = pattern["type"]
        if ptype == PATTERN_TYPE_TIME_BASED:
            return self._time_pattern_to_automation(pattern)
        if ptype == PATTERN_TYPE_CORRELATION:
            return self._correlation_to_automation(pattern)
        if ptype == PATTERN_TYPE_SEQUENCE:
            return self._sequence_to_automation(pattern)
        return None

    def _time_pattern_to_automation(self, pattern: PatternDict) -> AutomationDict | None:
        """Convert a time-based pattern to a time-trigger automation."""
        evidence = pattern.get("evidence", {})
        entity_id = pattern["entity_ids"][0]
        target_state = evidence.get("target_state", "")
        time_slot = evidence.get("time_slot", "")
        domain = entity_id.split(".")[0]

        action = self._build_action(domain, entity_id, target_state)
        if action is None:
            return None

        conditions: list[dict[str, Any]] = []
        if evidence.get("is_weekday") is True:
            conditions.append(
                {
                    "condition": "time",
                    "weekday": ["mon", "tue", "wed", "thu", "fri"],
                }
            )
        elif evidence.get("is_weekday") is False:
            conditions.append(
                {
                    "condition": "time",
                    "weekday": ["sat", "sun"],
                }
            )

        return {
            "alias": f"[Selora AI] {pattern['description']}",
            "description": pattern["description"],
            "triggers": [{"platform": "time", "at": time_slot}],
            "conditions": conditions,
            "actions": [action],
            "mode": "single",
        }

    def _correlation_to_automation(self, pattern: PatternDict) -> AutomationDict | None:
        """Convert a correlation pattern to a state-trigger automation."""
        evidence = pattern.get("evidence", {})
        trigger_entity = evidence.get("trigger_entity", "")
        trigger_state = evidence.get("trigger_state", "")
        response_entity = evidence.get("response_entity", "")
        response_state = evidence.get("response_state", "")
        response_domain = response_entity.split(".")[0]

        action = self._build_action(response_domain, response_entity, response_state)
        if action is None:
            return None

        return {
            "alias": f"[Selora AI] {pattern['description']}",
            "description": pattern["description"],
            "triggers": [
                {
                    "platform": "state",
                    "entity_id": trigger_entity,
                    "to": trigger_state,
                }
            ],
            "conditions": [],
            "actions": [action],
            "mode": "single",
        }

    def _sequence_to_automation(self, pattern: PatternDict) -> AutomationDict | None:
        """Convert a sequence pattern to a state-trigger automation with from/to."""
        evidence = pattern.get("evidence", {})
        trigger_entity = evidence.get("trigger_entity", "")
        trigger_from = evidence.get("trigger_from", "")
        trigger_to = evidence.get("trigger_to", "")
        response_entity = evidence.get("response_entity", "")
        response_state = evidence.get("response_state", "")
        response_domain = response_entity.split(".")[0]

        action = self._build_action(response_domain, response_entity, response_state)
        if action is None:
            return None

        trigger: dict[str, Any] = {
            "platform": "state",
            "entity_id": trigger_entity,
            "to": trigger_to,
        }
        if trigger_from:
            trigger["from"] = trigger_from

        return {
            "alias": f"[Selora AI] {pattern['description']}",
            "description": pattern["description"],
            "triggers": [trigger],
            "conditions": [],
            "actions": [action],
            "mode": "single",
        }

    def _build_action(
        self, domain: str, entity_id: str, target_state: str
    ) -> dict[str, Any] | None:
        """Build an HA action dict for common state transitions.

        Checks the HA service registry at runtime to verify the domain
        actually supports the resolved service, rather than maintaining a
        static domain allowlist.  Read-only domains (sensor, binary_sensor,
        device_tracker, person, …) are automatically rejected because they
        have no turn_on / turn_off / etc. services registered.
        """
        has = self._hass.services.has_service

        if target_state == "on" and has(domain, "turn_on"):
            return {
                "action": f"{domain}.turn_on",
                "target": {"entity_id": entity_id},
            }
        if target_state == "off" and has(domain, "turn_off"):
            return {
                "action": f"{domain}.turn_off",
                "target": {"entity_id": entity_id},
            }
        if domain == "cover":
            if target_state == "open" and has("cover", "open_cover"):
                return {
                    "action": "cover.open_cover",
                    "target": {"entity_id": entity_id},
                }
            if target_state == "closed" and has("cover", "close_cover"):
                return {
                    "action": "cover.close_cover",
                    "target": {"entity_id": entity_id},
                }
        if domain == "lock":
            if target_state == "locked" and has("lock", "lock"):
                return {
                    "action": "lock.lock",
                    "target": {"entity_id": entity_id},
                }
            if target_state == "unlocked" and has("lock", "unlock"):
                return {
                    "action": "lock.unlock",
                    "target": {"entity_id": entity_id},
                }
        return None

    @staticmethod
    def _build_dismissed_summary(dismissed: list[SuggestionDict]) -> str:
        """Build a short dismissal context string for the LLM prompt (#45).

        Groups dismissed suggestions by pattern type and reason so the model
        can avoid proposing automation categories the user has already rejected.
        """
        if not dismissed:
            return ""
        lines: list[str] = []
        seen: set[str] = set()
        for s in dismissed:
            desc = s.get("description", "")
            reason = s.get("dismissal_reason") or "user-declined"
            key = f"{desc[:60]}|{reason}"
            if key not in seen:
                seen.add(key)
                lines.append(f"- {desc[:80]} (reason: {reason})")
        return "\n".join(lines[:10])  # cap at 10 to keep prompt manageable

    @staticmethod
    def _build_evidence_summary(pattern: PatternDict) -> str:
        """Human-readable summary of pattern evidence."""
        evidence = pattern.get("evidence", {})
        ptype = pattern["type"]
        count = pattern.get("occurrence_count", evidence.get("occurrences", 0))
        days = evidence.get("total_days", 7)

        if ptype == PATTERN_TYPE_TIME_BASED:
            return (
                f"Observed {count} times over {days} days "
                f"at {evidence.get('time_slot', 'unknown time')}"
            )
        if ptype == PATTERN_TYPE_CORRELATION:
            co = evidence.get("co_occurrences", count)
            delay = evidence.get("avg_delay_seconds", "?")
            return f"Observed {co} co-occurrences (avg delay: {delay}s)"
        if ptype == PATTERN_TYPE_SEQUENCE:
            occ = evidence.get("occurrences", count)
            return f"Observed {occ} times in sequence"
        return f"Observed {count} times"
