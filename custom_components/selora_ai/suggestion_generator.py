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
from math import ceil
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import yaml

from .automation_utils import (
    _collect_referenced_entity_ids,
    _read_automations_yaml,
    _strip_legacy_selora_prefix,
    suggestion_content_fingerprint,
    validate_automation_payload,
)
from .entity_filter import resolve_ignored_entity_ids

if TYPE_CHECKING:
    from .llm_client import LLMClient
    from .types import AutomationDict, PatternDict, SuggestionDict

from .const import (
    CONFIDENCE_MEDIUM,
    DISMISSAL_SUPPRESSION_WINDOW_DAYS,
    PATTERN_HISTORY_RETENTION_DAYS,
    PATTERN_STATUS_QUALITY_REJECTED,
    PATTERN_SUGGESTION_CEILING,
    PATTERN_SUGGESTION_DEVICES_PER,
    PATTERN_SUGGESTION_FLOOR,
    PATTERN_SUGGESTION_MIN_SCORE,
    PATTERN_TYPE_CORRELATION,
    PATTERN_TYPE_SEQUENCE,
    PATTERN_TYPE_TIME_BASED,
    SUGGESTION_SCORING_TIMEOUT,
)
from .pattern_store import PatternStore

_LOGGER = logging.getLogger(__name__)

# String tokens an LLM may emit for a false ``keep`` verdict. Needed because
# ``bool("false")`` is True — a plain cast would surface a candidate the model
# explicitly tried to reject.
_FALSEY_KEEP_TOKENS = frozenset({"false", "no", "0", "off", ""})


def _coerce_keep(value: Any) -> bool:
    """Interpret a verdict's ``keep`` field, tolerating string booleans.

    Real bools / numbers use normal truthiness; quoted booleans are parsed so
    ``"false"`` reads as False rather than True. Unrecognised strings fall back
    to truthiness (matching the missing-field default of keep=True).
    """
    if isinstance(value, str):
        return value.strip().lower() not in _FALSEY_KEEP_TOKENS
    return bool(value)


class _Candidate(TypedDict):
    """A validated suggestion candidate awaiting the quality gate."""

    pattern_id: str
    confidence: float
    automation_data: AutomationDict
    automation_yaml: str
    description: str
    evidence_summary: str
    cluster_key: str


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

    async def generate_from_patterns(
        self,
        patterns: list[PatternDict],
        *,
        score_timeout: float = SUGGESTION_SCORING_TIMEOUT,
    ) -> list[SuggestionDict]:
        """Convert patterns into automation suggestions.

        ``score_timeout`` bounds the LLM quality-scoring call. Callers on a
        tight wall-clock budget (the on-demand websocket path) pass a shorter
        value so the confidence-ranking fallback fires and its results are
        saved before their outer deadline cancels the whole coroutine.

        For each pattern above CONFIDENCE_MEDIUM:
        1. Skip if already has a pending suggestion
        2. Skip if pattern was dismissed within the suppression window (#44)
        3. Build a valid HA automation payload
        4. Validate through automation_utils
        5. Deduplicate against existing automations
        6. Deduplicate against other suggestions in this batch by content (#46)
        7. Deduplicate against already-stored suggestions by content (#46)
        8. Score candidates via LLM and drop low-quality / non-sequiturs
        9. Collapse fan-out variants (same trigger) to the best passing one
        10. Cap to a home-size-relative number of slots and save the winners
        """
        # Stop early once the suggestions tab is already at its home-size cap —
        # no point mining, clustering, or scoring candidates we can't surface.
        cap = self._suggestion_cap()
        pending_count = len(await self._store.get_suggestions(status="pending"))
        slots = max(0, cap - pending_count)
        if slots == 0:
            _LOGGER.debug(
                "Suggestion cap reached (%d pending / cap %d) — skipping generation",
                pending_count,
                cap,
            )
            return []

        candidates: list[_Candidate] = []
        existing_aliases = self._get_existing_aliases()
        # Entity groups of existing automations, to drop correlations that are
        # just an existing automation's own effect (see the helper docstring).
        existing_entity_groups = await self._existing_automation_entity_groups()

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

        # Resolve the ignore list once per generation cycle. Used to skip
        # patterns whose entities were marked off-limits after detection.
        ignored_entities = resolve_ignored_entity_ids(self._hass)

        for pattern in patterns:
            if pattern["confidence"] < CONFIDENCE_MEDIUM:
                continue

            if ignored_entities and any(
                eid in ignored_entities for eid in (pattern.get("entity_ids") or [])
            ):
                continue

            pattern_id = pattern.get("pattern_id", "")
            if pattern_id and await self._store.has_suggestion_for_pattern(pattern_id):
                continue

            # Skip a correlation/sequence whose entities are already linked by an
            # existing automation — the correlation is that automation's effect,
            # not a new opportunity (and re-suggesting it, often with cause and
            # effect reversed, would conflict with the automation that produced
            # it).
            if self._pair_already_automated(
                pattern.get("entity_ids") or [], existing_entity_groups
            ):
                _LOGGER.debug(
                    "Skipping pattern %s — its entities are already linked by an "
                    "existing automation (correlation is that automation's effect)",
                    pattern_id,
                )
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

            # Compare on the bare alias so a candidate built by a
            # legacy code path (with the ``[Selora AI]`` prefix) still
            # matches the normalised set of existing aliases.
            candidate_alias = _strip_legacy_selora_prefix(automation.get("alias", ""))
            alias_lower = candidate_alias.lower()
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

            candidates.append(
                {
                    "pattern_id": pattern_id,
                    "confidence": pattern["confidence"],
                    "automation_data": normalized,
                    "automation_yaml": yaml_text,
                    "description": pattern["description"],
                    "evidence_summary": self._build_evidence_summary(pattern),
                    "cluster_key": self._cluster_key(pattern),
                }
            )

        # Quality gate: score every candidate, then collapse each trigger's
        # fan-out variants to the best *passing* one (so a spurious
        # high-confidence variant can't crowd out a sensible lower-confidence
        # one) and cap. Falls back to confidence collapse+rank without an LLM.
        selected = await self._score_and_select(candidates, slots, score_timeout)

        suggestions: list[SuggestionDict] = []
        for cand in selected:
            suggestion: SuggestionDict = {
                "pattern_id": cand["pattern_id"],
                "source": "pattern",
                "confidence": cand["confidence"],
                "automation_data": cand["automation_data"],
                "automation_yaml": cand["automation_yaml"],
                "description": cand["description"],
                "evidence_summary": cand["evidence_summary"],
            }
            sid = await self._store.save_suggestion(suggestion)
            suggestion["suggestion_id"] = sid
            suggestions.append(suggestion)

        if suggestions:
            _LOGGER.info(
                "Generated %d proactive suggestions from %d candidates (cap %d, %d slots)",
                len(suggestions),
                len(candidates),
                cap,
                slots,
            )

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
        except (
            json.JSONDecodeError,
            ValueError,
        ):
            _LOGGER.debug("Batch LLM enrichment returned invalid JSON, descriptions unchanged")
        except Exception:
            _LOGGER.debug("Batch LLM enrichment failed, descriptions unchanged")
        return 0

    def _suggestion_cap(self) -> int:
        """Cap on pattern-derived suggestions, scaled to home size.

        ~1 slot per PATTERN_SUGGESTION_DEVICES_PER devices, clamped between a
        floor (small homes still get a few) and a ceiling (large homes don't
        drown in cards). Uses the device registry — the same source the
        collector's LLM-analysis cap uses.
        """
        device_count = len(dr.async_get(self._hass).devices)
        scaled = ceil(device_count / PATTERN_SUGGESTION_DEVICES_PER)
        return max(PATTERN_SUGGESTION_FLOOR, min(scaled, PATTERN_SUGGESTION_CEILING))

    @staticmethod
    def _cluster_key(pattern: PatternDict) -> str:
        """Group key that collapses fan-out variants of one insight.

        A busy home produces many near-duplicate patterns that share a trigger
        but fan out to different targets (front-door motion stops → porch light
        / garage spots / deck lights / sconces). They are the same idea, so we
        key on everything the generated automation's *trigger and conditions*
        use — but not the response target — and keep only the strongest
        candidate per key.

        The key must include every trigger/condition field the automation
        actually depends on, or genuinely distinct automations collapse: a
        weekday and a weekend time pattern differ only by ``is_weekday`` (their
        HA ``time`` condition), and two sequences with the same ``trigger_to``
        but different ``trigger_from`` are different state triggers.
        """
        evidence = pattern.get("evidence", {})
        ptype = pattern["type"]
        if ptype == PATTERN_TYPE_TIME_BASED:
            entity_id = (pattern.get("entity_ids") or [""])[0]
            return (
                f"time:{entity_id}:{evidence.get('time_slot', '')}"
                f":{evidence.get('target_state', '')}:{evidence.get('is_weekday')}"
            )
        trigger_entity = evidence.get("trigger_entity", "")
        if ptype == PATTERN_TYPE_SEQUENCE:
            return (
                f"sequence:{trigger_entity}"
                f":{evidence.get('trigger_from', '')}:{evidence.get('trigger_to', '')}"
            )
        trigger_state = evidence.get("trigger_state") or evidence.get("trigger_to", "")
        return f"{ptype}:{trigger_entity}:{trigger_state}"

    @staticmethod
    def _collapse_variants(candidates: list[_Candidate]) -> list[_Candidate]:
        """Keep only the highest-confidence candidate per cluster key."""
        best: dict[str, _Candidate] = {}
        for cand in candidates:
            key = cand["cluster_key"]
            current = best.get(key)
            if current is None or cand["confidence"] > current["confidence"]:
                best[key] = cand
        collapsed = list(best.values())
        if len(collapsed) < len(candidates):
            _LOGGER.debug(
                "Collapsed %d fan-out candidates into %d clusters",
                len(candidates),
                len(collapsed),
            )
        return collapsed

    async def _score_and_select(
        self, candidates: list[_Candidate], slots: int, score_timeout: float
    ) -> list[_Candidate]:
        """Score candidates, collapse fan-out variants, cap to ``slots``.

        The LLM rates each candidate 0-100 on whether it is a genuinely useful,
        sensible cause→effect worth automating, flagging spurious statistical
        coincidences (e.g. a camera in one room controlling an unrelated room).

        Scoring happens *before* collapsing so that within a trigger's fan-out
        cluster the best *passing* candidate wins — a spurious high-confidence
        variant can't crowd out a sensible lower-confidence one. On
        no-LLM / timeout / bad output we fall back to a confidence-based
        collapse + rank so the volume cap still applies.

        Candidates the LLM rejects have their pattern marked ``rejected`` in the
        store so ``_backfill_unsugested_patterns`` (#67) stops re-surfacing and
        re-scoring them every cycle — and so a later fallback can't quietly save
        a pattern the gate already judged low-quality. Reactivation on fresh
        detection (``save_pattern``) still lets a strengthened pattern retry.
        """
        if not candidates or slots <= 0:
            return []

        raw = await self._score_candidates(candidates, score_timeout)
        verdicts = self._normalize_verdicts(raw, len(candidates)) if raw is not None else None

        if verdicts is None:
            # Fallback: no usable scores — collapse by confidence, then rank.
            ranked = [(0.0, c["confidence"], c) for c in self._collapse_variants(candidates)]
        else:
            # Keep only passing candidates, then collapse each trigger cluster
            # to its best survivor (by score, tie-broken by confidence).
            best_per_cluster: dict[str, tuple[float, _Candidate]] = {}
            rejected_pattern_ids: list[str] = []
            for cand, (keep, score) in zip(candidates, verdicts, strict=False):
                if not keep or score < PATTERN_SUGGESTION_MIN_SCORE:
                    if cand["pattern_id"]:
                        rejected_pattern_ids.append(cand["pattern_id"])
                    continue
                key = cand["cluster_key"]
                current = best_per_cluster.get(key)
                if current is None or (score, cand["confidence"]) > (
                    current[0],
                    current[1]["confidence"],
                ):
                    best_per_cluster[key] = (score, cand)
            await self._persist_rejections(rejected_pattern_ids)
            ranked = [
                (score, cand["confidence"], cand) for score, cand in best_per_cluster.values()
            ]

        ranked.sort(key=lambda r: (r[0], r[1]), reverse=True)
        return [cand for _score, _conf, cand in ranked[:slots]]

    async def _persist_rejections(self, pattern_ids: list[str]) -> None:
        """Durably mark LLM-rejected patterns so they aren't re-scored.

        Uses PATTERN_STATUS_QUALITY_REJECTED, *not* the causality "rejected"
        status: ``save_pattern`` reactivates "rejected" patterns whenever they
        are re-detected, which for a still-occurring pattern would flip it back
        to active and re-score it every scan. A semantic non-sequitur won't
        become sensible with more data, so the quality verdict must survive
        rescans.
        """
        if not pattern_ids:
            return
        for pid in pattern_ids:
            await self._store.update_pattern_status(pid, PATTERN_STATUS_QUALITY_REJECTED)
        _LOGGER.debug("Marked %d LLM-rejected pattern(s) as quality-rejected", len(pattern_ids))

    @staticmethod
    def _normalize_verdicts(raw: list[Any], expected: int) -> list[tuple[bool, float]] | None:
        """Coerce raw LLM verdicts into (keep, score) tuples.

        Returns None — signalling the caller to fall back to confidence ranking
        (which persists no rejections) — when the batch is unusable: wrong
        length, any element that isn't a dict, or any element with a missing or
        non-numeric ``score``. A malformed score must NOT be treated as 0.0:
        rejections are durable (quality_rejected), so silently scoring a
        formatting glitch as 0 would permanently suppress a valid pattern.
        Numeric strings ("90") are accepted; booleans and anything ``float()``
        can't parse are treated as malformed.
        """
        if len(raw) != expected:
            return None
        normalized: list[tuple[bool, float]] = []
        for verdict in raw:
            if not isinstance(verdict, dict):
                return None
            raw_score = verdict.get("score")
            if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float, str)):
                return None
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                return None
            normalized.append((_coerce_keep(verdict.get("keep", True)), score))
        return normalized

    async def _score_candidates(
        self, candidates: list[_Candidate], score_timeout: float
    ) -> list[dict[str, Any]] | None:
        """LLM batch scorer. Returns per-candidate verdicts or None on failure."""
        if not self._llm:
            return None

        items = "\n".join(
            f"{i}. {self._describe_candidate(c)}" for i, c in enumerate(candidates, 1)
        )
        prompt = (
            "Review these candidate smart-home automations, each derived from an "
            "observed usage pattern. For each, decide whether it is a genuinely "
            "useful, sensible cause→effect a homeowner would want automated.\n"
            "Reject spurious statistical coincidences — e.g. a camera or sensor in "
            "one room controlling an unrelated room's devices, or effects with no "
            "plausible relationship to the trigger.\n"
            "Reply with ONLY a JSON array, one object per candidate in the same "
            'order: {"score": <0-100>, "keep": <true|false>, "reason": "<short>"}.'
            "\n\n" + items
        )

        try:
            result, _ = await asyncio.wait_for(
                self._llm.send_request(
                    system=(
                        "You are a smart-home automation reviewer. You score how "
                        "useful and sensible each proposed automation is. "
                        "Reply with only a JSON array."
                    ),
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=score_timeout,
            )
            if not result:
                return None
            verdicts = json.loads(result.strip())
            if not isinstance(verdicts, list):
                return None
            return verdicts
        except TimeoutError:
            _LOGGER.debug("Suggestion scoring timed out — falling back to confidence rank")
        except (json.JSONDecodeError, ValueError):
            _LOGGER.debug("Suggestion scoring returned invalid JSON — falling back")
        except Exception:
            _LOGGER.debug("Suggestion scoring failed — falling back to confidence rank")
        return None

    @staticmethod
    def _describe_candidate(cand: _Candidate) -> str:
        """One plain-English line describing a candidate for the scoring prompt."""
        return f"{cand['description']} ({cand['evidence_summary']})"

    def _get_existing_aliases(self) -> set[str]:
        """Return lowercase aliases of every automation, prefix-stripped.

        Pre-label automations carry the ``[Selora AI]`` prefix in their
        HA alias; stripping it here lets them compare equal to current
        suggestion aliases (which do not).
        """
        aliases: set[str] = set()
        for state in self._hass.states.async_all("automation"):
            alias = state.attributes.get("friendly_name", "")
            if alias:
                normalised = _strip_legacy_selora_prefix(alias)
                aliases.add(normalised.lower())
        return aliases

    async def _existing_automation_entity_groups(self) -> list[frozenset[str]]:
        """Per existing automation, the set of entity_ids it references.

        Used to drop a correlation/sequence pattern whose two entities are
        ALREADY tied together by one existing automation. Such a correlation is
        that automation's own effect, not a new opportunity — e.g. an "unlock
        front door -> turn on porch lights" automation makes the lock and the
        lights co-occur, which must not resurface as "automate the front door
        lock when the porch lights turn on" (reversed cause and effect, and a
        conflict with the very automation that produced the correlation).

        Read from automations.yaml (where HA stores UI + YAML automations);
        best-effort — an unreadable / split config just yields no groups and the
        existing alias/fingerprint dedup still applies.
        """
        path = Path(self._hass.config.config_dir) / "automations.yaml"
        automations = await self._hass.async_add_executor_job(_read_automations_yaml, path)
        groups: list[frozenset[str]] = []
        for auto in automations:
            refs = _collect_referenced_entity_ids(auto)
            if len(refs) >= 2:
                groups.append(frozenset(refs))
        return groups

    @staticmethod
    def _pair_already_automated(entity_ids: list[str], groups: list[frozenset[str]]) -> bool:
        """True when a single existing automation already references at least
        two of ``entity_ids`` — i.e. the pattern's entities are already linked,
        so any correlation between them is that automation's doing."""
        ids = {e for e in entity_ids if e}
        if len(ids) < 2:
            return False
        return any(len(ids & group) >= 2 for group in groups)

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
            "alias": pattern["description"],
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
            "alias": pattern["description"],
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
            "alias": pattern["description"],
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
