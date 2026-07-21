"""InsightsEngine — Layer 2 of Insights: the advisor.

Turns Layer 1 health signals (and, where available, detected patterns) into
user-facing insights: reported issues, suggested fixes, and improvement ideas.

Two tiers:
  * Deterministic — a rule table maps each signal kind to an insight. No LLM,
    works fully offline. This is the always-on baseline.
  * LLM-enriched (optional) — better phrasing and fuzzy "improvement" ideas via
    the configured LLM. Degrades to the deterministic tier when no LLM is
    configured. (Enrichment wiring is staged separately from this baseline.)

Insights are regenerated on demand from current signals, so only the user's
per-insight action (dismiss / acknowledge / resolve) is persisted — as a status
override keyed by the stable ``insight_id`` in HealthStore.
"""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    HEALTH_KIND_BATTERY_LOW,
    HEALTH_KIND_FLAPPING,
    HEALTH_KIND_INTEGRATION_ERROR,
    HEALTH_KIND_SILENT,
    HEALTH_KIND_UNAVAILABLE,
    INSIGHT_KIND_FIX,
    INSIGHT_KIND_IMPROVEMENT,
    INSIGHT_KIND_ISSUE,
)
from .helpers import integration_error_detail

if TYPE_CHECKING:
    from .health_store import HealthStore
    from .pattern_store import PatternStore
    from .types import HealthSignal, Insight

_LOGGER = logging.getLogger(__name__)

_STATUS_NEW = "new"
_STATUS_DISMISSED = "dismissed"


def _friendly(target: str) -> str:
    """Best-effort human label for an entity_id / domain."""
    return target.split(".", 1)[1].replace("_", " ") if "." in target else target


# Rule table: signal kind -> insight builder. Each builder returns
# (insight_kind, title, detail) from the signal's evidence. Deterministic and
# offline; the LLM tier only rephrases these, never replaces the facts.


def _rule_unavailable(sig: HealthSignal) -> tuple[str, str, str]:
    evidence = sig["evidence"]
    mins = int(evidence.get("unavailable_seconds", 0) // 60)
    name = _friendly(sig["target"])
    if evidence.get("intermittent"):
        return (
            INSIGHT_KIND_ISSUE,
            f"{name} keeps dropping out",
            f"{name} has gone in and out of availability repeatedly (currently "
            f"~{mins} min). That usually means a weak signal or an out-of-range "
            "device rather than a failure — check its range, antenna, or hub.",
        )
    return (
        INSIGHT_KIND_ISSUE,
        f"{name} is unavailable",
        f"{name} has been unavailable for about {mins} min. Check its power, "
        "battery, or network/hub connection.",
    )


def _rule_flapping(sig: HealthSignal) -> tuple[str, str, str]:
    n = sig["evidence"].get("transitions", 0)
    mins = int(sig["evidence"].get("window_seconds", 0) // 60)
    name = _friendly(sig["target"])
    return (
        INSIGHT_KIND_ISSUE,
        f"{name} is flapping",
        f"{name} went in and out of availability {n} times in {mins} min — "
        "often a weak signal, dying battery, or a failing device.",
    )


def _rule_silent(sig: HealthSignal) -> tuple[str, str, str]:
    mins = int(sig["evidence"].get("silent_seconds", 0) // 60)
    expected = int(sig["evidence"].get("expected_interval_seconds", 0))
    name = _friendly(sig["target"])
    exp_txt = f" (usually every ~{expected // 60} min)" if expected >= 60 else ""
    return (
        INSIGHT_KIND_ISSUE,
        f"{name} stopped reporting",
        f"{name} hasn't updated in about {mins} min{exp_txt}. It may be offline "
        "even though it still shows a value.",
    )


def _rule_battery(sig: HealthSignal) -> tuple[str, str, str]:
    level = sig["evidence"].get("battery_level")
    name = _friendly(sig["target"])
    # A binary battery sensor (on == low) has no percentage — render "is low"
    # rather than a bogus "0%".
    status = f"is at {level}%" if isinstance(level, (int, float)) else "is low"
    return (
        INSIGHT_KIND_FIX,
        f"{name} battery is low",
        f"{name} {status}. Replace or recharge it soon to avoid a gap in coverage.",
    )


def _rule_integration(sig: HealthSignal) -> tuple[str, str, str]:
    return (
        INSIGHT_KIND_FIX,
        f"The {sig['target']} integration needs attention",
        integration_error_detail(sig["target"], sig["evidence"]),
    )


_RULES = {
    HEALTH_KIND_UNAVAILABLE: _rule_unavailable,
    HEALTH_KIND_FLAPPING: _rule_flapping,
    HEALTH_KIND_SILENT: _rule_silent,
    HEALTH_KIND_BATTERY_LOW: _rule_battery,
    HEALTH_KIND_INTEGRATION_ERROR: _rule_integration,
}


class InsightsEngine:
    """Builds user-facing insights from health signals and patterns."""

    def __init__(
        self,
        hass: HomeAssistant,
        health_store: HealthStore,
        pattern_store: PatternStore | None = None,
        llm: Any | None = None,
    ) -> None:
        self._hass = hass
        self._health_store = health_store
        self._pattern_store = pattern_store
        self._llm = llm

    async def async_get_insights(self) -> list[Insight]:
        """Return current insights, honoring persisted user status overrides."""
        overrides = await self._health_store.get_insight_overrides()

        insights: list[Insight] = []
        for sig in await self._health_store.get_active_signals():
            built = self._insight_from_signal(sig)
            if built is not None:
                insights.append(built)

        insights.extend(await self._improvement_insights())

        out: list[Insight] = []
        for ins in insights:
            status = overrides.get(ins["insight_id"])
            if status == _STATUS_DISMISSED:
                continue
            if status:
                ins["status"] = status
            out.append(ins)

        _SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}
        out.sort(key=lambda i: _SEVERITY_RANK.get(i["severity"], 3))
        return out

    def _insight_from_signal(self, sig: HealthSignal) -> Insight | None:
        rule = _RULES.get(sig["kind"])
        if rule is None:
            return None
        kind, title, detail = rule(sig)
        return {
            "insight_id": f"signal:{sig['signal_id']}",
            "kind": kind,
            "severity": sig["severity"],
            "title": title,
            "detail": detail,
            "linked_signals": [sig["signal_id"]],
            "suggested_action": {"type": "acknowledge"},
            "source": "deterministic",
            "created_at": sig.get("first_seen", datetime.now(UTC).isoformat()),
            "status": _STATUS_NEW,
        }

    async def _improvement_insights(self) -> list[Insight]:
        """Surface pending pattern-derived automation suggestions as
        'improvement' insights — unifying SuggestionGenerator into Insights.
        """
        if self._pattern_store is None:
            return []
        out: list[Insight] = []
        for sug in await self._pattern_store.get_suggestions(status="pending"):
            sid = sug.get("suggestion_id", "")
            if not sid:
                continue
            out.append(
                {
                    "insight_id": f"suggestion:{sid}",
                    "kind": INSIGHT_KIND_IMPROVEMENT,
                    "severity": "info",
                    "title": sug.get("description", "Suggested automation"),
                    "detail": sug.get("evidence_summary", ""),
                    "linked_signals": [],
                    "suggested_action": {
                        "type": "automation",
                        "suggestion_id": sid,
                        "automation_data": sug.get("automation_data", {}),
                    },
                    "source": "deterministic",
                    "created_at": sug.get("created_at", datetime.now(UTC).isoformat()),
                    "status": _STATUS_NEW,
                }
            )
        return out

    async def set_insight_status(self, insight_id: str, status: str) -> None:
        """Persist a user action (dismiss / acknowledge / resolve)."""
        await self._health_store.set_insight_override(insight_id, status)


def get_insights_engine(hass: HomeAssistant) -> InsightsEngine | None:
    """Find the InsightsEngine from any active config entry."""
    domain_data = hass.data.get(DOMAIN, {})
    for key, val in domain_data.items():
        if key.startswith("_") or not isinstance(val, dict):
            continue
        engine = val.get("insights_engine")
        if engine is not None:
            return engine
    return None
