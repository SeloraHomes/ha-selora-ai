"""SuggestionGenerator — converts detected patterns into HA automation suggestions.

Each detected pattern is transformed into a valid Home Assistant automation
payload, validated, deduplicated, and saved to PatternStore as a proactive
suggestion with confidence score and human-readable evidence summary.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant
import yaml

from .automation_utils import validate_automation_payload
from .const import (
    CONFIDENCE_MEDIUM,
    DISMISSAL_SUPPRESSION_WINDOW_DAYS,
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
        llm: Any | None = None,
    ) -> None:
        self._hass = hass
        self._store = pattern_store
        self._llm = llm

    async def generate_from_patterns(self, patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert patterns into automation suggestions.

        For each pattern above CONFIDENCE_MEDIUM:
        1. Skip if already has a pending suggestion
        2. Skip if pattern was dismissed within the suppression window (#44)
        3. Build a valid HA automation payload
        4. Validate through automation_utils
        5. Deduplicate against existing automations
        6. Optionally enrich description via LLM (with dismissal context, #45)
        7. Save to pattern store
        """
        suggestions: list[dict[str, Any]] = []
        existing_aliases = self._get_existing_aliases()

        # Fetch recently dismissed suggestions once for the whole batch (#44 + #45)
        recently_dismissed = await self._store.get_recently_dismissed_suggestions()
        dismissed_pattern_ids: set[str] = {
            s["pattern_id"] for s in recently_dismissed if s.get("pattern_id")
        }
        dismissed_summary = self._build_dismissed_summary(recently_dismissed)
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

            automation = self._pattern_to_automation(pattern)
            if automation is None:
                continue

            # Deduplicate against existing automations
            alias_lower = automation.get("alias", "").lower()
            if alias_lower in existing_aliases:
                continue

            is_valid, reason, normalized = validate_automation_payload(automation)
            if not is_valid or normalized is None:
                _LOGGER.debug(
                    "Pattern %s produced invalid automation: %s",
                    pattern_id,
                    reason,
                )
                continue

            yaml_text = yaml.dump(normalized, allow_unicode=True, default_flow_style=False)

            suggestion: dict[str, Any] = {
                "pattern_id": pattern_id,
                "source": "pattern",
                "confidence": pattern["confidence"],
                "automation_data": normalized,
                "automation_yaml": yaml_text,
                "description": pattern["description"],
                "evidence_summary": self._build_evidence_summary(pattern),
            }

            # Optional LLM enrichment with dismissal context (best-effort, non-blocking) (#45)
            if self._llm:
                suggestion = await self._enrich_with_llm(
                    suggestion, pattern, dismissed_summary=dismissed_summary
                )

            sid = await self._store.save_suggestion(suggestion)
            suggestion["suggestion_id"] = sid
            suggestions.append(suggestion)

        if suggestions:
            _LOGGER.info("Generated %d proactive suggestions from patterns", len(suggestions))

        return suggestions

    def _get_existing_aliases(self) -> set[str]:
        """Collect lowercase aliases of all existing automations."""
        aliases: set[str] = set()
        for state in self._hass.states.async_all("automation"):
            alias = state.attributes.get("friendly_name", "")
            if alias:
                aliases.add(alias.lower())
        return aliases

    def _pattern_to_automation(self, pattern: dict[str, Any]) -> dict[str, Any] | None:
        """Convert a pattern into a valid HA automation dict."""
        ptype = pattern["type"]
        if ptype == PATTERN_TYPE_TIME_BASED:
            return self._time_pattern_to_automation(pattern)
        if ptype == PATTERN_TYPE_CORRELATION:
            return self._correlation_to_automation(pattern)
        if ptype == PATTERN_TYPE_SEQUENCE:
            return self._sequence_to_automation(pattern)
        return None

    def _time_pattern_to_automation(self, pattern: dict[str, Any]) -> dict[str, Any] | None:
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
            "trigger": [{"platform": "time", "at": time_slot}],
            "condition": conditions,
            "action": [action],
            "mode": "single",
        }

    def _correlation_to_automation(self, pattern: dict[str, Any]) -> dict[str, Any] | None:
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
            "trigger": [
                {
                    "platform": "state",
                    "entity_id": trigger_entity,
                    "to": trigger_state,
                }
            ],
            "condition": [],
            "action": [action],
            "mode": "single",
        }

    def _sequence_to_automation(self, pattern: dict[str, Any]) -> dict[str, Any] | None:
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
            "trigger": [trigger],
            "condition": [],
            "action": [action],
            "mode": "single",
        }

    @staticmethod
    def _build_action(domain: str, entity_id: str, target_state: str) -> dict[str, Any] | None:
        """Build an HA action dict for common state transitions."""
        if target_state == "on":
            return {
                "action": f"{domain}.turn_on",
                "target": {"entity_id": entity_id},
            }
        if target_state == "off":
            return {
                "action": f"{domain}.turn_off",
                "target": {"entity_id": entity_id},
            }
        if domain == "cover":
            if target_state == "open":
                return {
                    "action": "cover.open_cover",
                    "target": {"entity_id": entity_id},
                }
            if target_state == "closed":
                return {
                    "action": "cover.close_cover",
                    "target": {"entity_id": entity_id},
                }
        if domain == "lock":
            if target_state == "locked":
                return {
                    "action": "lock.lock",
                    "target": {"entity_id": entity_id},
                }
            if target_state == "unlocked":
                return {
                    "action": "lock.unlock",
                    "target": {"entity_id": entity_id},
                }
        return None

    @staticmethod
    def _build_dismissed_summary(dismissed: list[dict[str, Any]]) -> str:
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
    def _build_evidence_summary(pattern: dict[str, Any]) -> str:
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

    async def _enrich_with_llm(
        self,
        suggestion: dict[str, Any],
        pattern: dict[str, Any],
        dismissed_summary: str = "",
    ) -> dict[str, Any]:
        """Ask the LLM for a better human description (best-effort).

        Passes a summary of recently dismissed patterns so the LLM avoids
        re-proposing similar automations (#45).
        """
        try:
            dismissal_context = (
                f"\nRecently dismissed automation types (do not re-suggest similar patterns):\n{dismissed_summary}"
                if dismissed_summary
                else ""
            )
            prompt = (
                "Rewrite this automation description to be clear and friendly "
                "for a homeowner (one sentence, no technical jargon):\n"
                f"Pattern: {pattern['description']}\n"
                f"Evidence: {suggestion['evidence_summary']}"
                f"{dismissal_context}"
            )
            result, _ = await asyncio.wait_for(
                self._llm._send_request(
                    system=(
                        "You rewrite smart home automation descriptions. "
                        "Reply with just the improved description."
                    ),
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=15,
            )
            if result:
                suggestion["description"] = result.strip()
                suggestion["source"] = "hybrid"
        except TimeoutError:
            _LOGGER.debug("LLM enrichment timed out, using original description")
        except Exception:
            _LOGGER.debug("LLM enrichment failed, using original description")
        return suggestion
