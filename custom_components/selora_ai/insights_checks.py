"""Deterministic Insights checks — the LLM-free "what to fix / improve" catalog.

Each check is a pure rule over HA state / registries / ``automations.yaml`` that
returns :class:`Finding`s with EXACT ground truth and a TEMPLATED message. No
model is involved, so a finding can never speculate about causation the way the
retired free-form audit did (it read an ``unknown`` sensor as "stuck in identify
mode").

Checks live in the ``CHECKS`` registry so the panel can show the whole
assessment — every check that ran, whether it's clear, and what it found.
``async_run_checks`` returns one :class:`CheckResult` per registered check.

To add a check: write ``_check_<name>(ctx) -> list[Finding]``, add a ``Check``
entry to ``CHECKS``, and cover it with a fixture-home test in
``tests/test_insights_checks.py`` — every deterministic check must be stable.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .automation_utils import (
    _collect_referenced_entity_ids,
    _find_unknown_entity_ids,
    _read_automations_yaml,
    _strip_legacy_selora_prefix,
    suggestion_content_fingerprint,
)
from .const import (
    HEALTH_KIND_BATTERY_LOW,
    HEALTH_KIND_FLAPPING,
    HEALTH_KIND_INTEGRATION_ERROR,
    HEALTH_KIND_SILENT,
    HEALTH_KIND_UNAVAILABLE,
)
from .entity_filter import resolve_ignored_entity_ids
from .health_store import get_health_store

if TYPE_CHECKING:
    from .types import CheckResult, Finding

_LOGGER = logging.getLogger(__name__)

_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}
# Cap listed names/entities so a card stays readable and the payload bounded.
_MAX_LISTED = 6

# Deterministic = pure rules over HA state; model = needs the LLM (grounded on
# real data). Surfaced so the checklist can label AI-assisted checks.
KIND_DETERMINISTIC = "deterministic"
KIND_MODEL = "model"


@dataclass(frozen=True)
class _CheckContext:
    """Shared, pre-computed inputs handed to every check (built once per run)."""

    hass: HomeAssistant
    automations: list[dict[str, Any]]  # parsed automations.yaml
    id_to_entity: dict[str, str]  # automation config id -> live entity_id
    excluded: frozenset[str]  # entities muted via the Selora-exclude label
    signals: list[dict[str, Any]]  # active Layer-1 health signals


@dataclass(frozen=True)
class Check:
    """A registered check: what it assesses + how to run it."""

    id: str
    title: str  # user-facing, e.g. "Duplicate automations"
    kind: str
    run: Callable[[_CheckContext], list[Finding]]


CHECKS: list[Check] = [
    # Device health (Layer 1 signals) — the most deterministic checks.
    Check("offline_devices", "Devices offline", KIND_DETERMINISTIC, lambda c: _check_offline(c)),
    Check("low_batteries", "Low batteries", KIND_DETERMINISTIC, lambda c: _check_batteries(c)),
    Check(
        "integration_errors",
        "Integration errors",
        KIND_DETERMINISTIC,
        lambda c: _check_integration_errors(c),
    ),
    Check(
        "unresponsive_sensors",
        "Unresponsive sensors",
        KIND_DETERMINISTIC,
        lambda c: _check_unresponsive(c),
    ),
    Check("unstable_devices", "Unstable devices", KIND_DETERMINISTIC, lambda c: _check_unstable(c)),
    # Automation & config hygiene.
    Check(
        "duplicate_automations",
        "Duplicate automations",
        KIND_DETERMINISTIC,
        lambda c: _check_duplicate_automations(c),
    ),
    Check(
        "broken_automations",
        "Automations with missing entities",
        KIND_DETERMINISTIC,
        lambda c: _check_broken_automations(c),
    ),
    Check(
        "updates_available",
        "Pending updates",
        KIND_DETERMINISTIC,
        lambda c: _check_updates_available(c),
    ),
]


async def async_run_checks(hass: HomeAssistant) -> list[CheckResult]:
    """Run every registered check and return one CheckResult each (in registry
    order), so callers can show the full assessment — not just the findings.

    Deterministic checks are pure rules over HA state / registries /
    ``automations.yaml`` — no LLM — so results are stable given the same home.
    """
    excluded = resolve_ignored_entity_ids(hass)

    # automations.yaml is read once off-thread and shared by the automation
    # checks. Best-effort: an unreadable / split config just yields no
    # automation findings.
    path = Path(hass.config.config_dir) / "automations.yaml"
    try:
        automations = await hass.async_add_executor_job(_read_automations_yaml, path)
    except Exception:  # noqa: BLE001 — automation checks are best-effort
        _LOGGER.debug("Could not read automations.yaml for checks", exc_info=True)
        automations = []
    # A hand-edited automations.yaml can contain non-mapping entries (a bare
    # string/number). Drop them here so the automation checks — which call
    # ``auto.get(...)`` — can't raise on one bad line and silently disable
    # duplicate/broken detection for the whole home.
    automations = [a for a in automations if isinstance(a, dict)]

    # Map an automation's config ``id`` to its live entity_id so findings can
    # link the actual automation card.
    id_to_entity = {
        str(st.attributes.get("id")): st.entity_id
        for st in hass.states.async_all("automation")
        if st.attributes.get("id")
    }

    # Active Layer-1 health signals feed the device-health checks.
    store = get_health_store(hass)
    signals = await store.get_active_signals() if store is not None else []

    ctx = _CheckContext(hass, automations, id_to_entity, excluded, signals)

    results: list[CheckResult] = []
    for check in CHECKS:
        try:
            findings = check.run(ctx)
        except Exception:  # noqa: BLE001 — one check must not sink the whole run
            _LOGGER.exception("Insights check %s failed", check.id)
            # The check did NOT complete — mark it errored, never "clear".
            # Reporting a crashed check as clear would claim the condition was
            # assessed and healthy (and add no score penalty) when it wasn't.
            results.append(
                {
                    "check_id": check.id,
                    "title": check.title,
                    "kind": check.kind,
                    "status": "error",
                    "findings": [],
                }
            )
            continue
        findings.sort(key=lambda f: _SEVERITY_RANK.get(f.get("severity", "info"), 3))
        results.append(
            {
                "check_id": check.id,
                "title": check.title,
                "kind": check.kind,
                "status": "issues" if findings else "clear",
                "findings": findings,
            }
        )
    return results


def flatten_findings(results: list[CheckResult]) -> list[Finding]:
    """All findings across checks, most-severe first — for the card list."""
    findings = [f for r in results for f in r["findings"]]
    findings.sort(key=lambda f: _SEVERITY_RANK.get(f.get("severity", "info"), 3))
    return findings


# ── Health score ──────────────────────────────────────────────────────
# Deterministic 0-100 roll-up of every finding's severity. Transparent,
# penalty-based (HAGHS-style): the score is fully explained by the checklist.
_PENALTY = {"critical": 15, "warning": 5, "info": 1}
# Diminishing returns for warnings/info: the Nth finding of a severity counts
# ``_PENALTY_DECAY**(N-1)`` of its base. Homes accumulate many minor issues
# (a few consumer devices off, some duplicate automations) that shouldn't stack
# into a hard fail, and one more open warning shouldn't swing the score.
# Criticals do NOT diminish — each is independently serious.
_PENALTY_DECAY = 0.6


def score_from_severities(severities: Iterable[str]) -> int:
    """0-100 health score: 100 = nothing flagged.

    Criticals subtract their full weight each (independently serious). Warnings
    and info get geometric diminishing returns per severity, so a long tail of
    minor issues can't tank the score and adding one more barely moves it. Any
    warning-or-worse still keeps the score below 100 while problems are open.
    """
    seen: dict[str, int] = {}
    penalty = 0.0
    for sev in severities:
        base = _PENALTY.get(sev, _PENALTY["info"])
        if sev == "critical":
            penalty += base
            continue
        n = seen.get(sev, 0)
        penalty += base * (_PENALTY_DECAY**n)
        seen[sev] = n + 1
    return max(0, round(100 - penalty))


def band_for(score: int) -> str:
    """Letter band for a 0-100 score (A best … F worst)."""
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


# ── Device-health checks (Layer 1 signals) ────────────────────────────


def _collapse_signals_by_device(
    ctx: _CheckContext, signals: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Group signals by device (so a Sonos with 5 dead entities is one row),
    resolving a display name. Signals with no device pass through individually.
    Returns dicts: {name, device_id, entities, area, severity}."""
    dev_reg = dr.async_get(ctx.hass)
    by_device: dict[str, dict[str, Any]] = {}
    groups: list[dict[str, Any]] = []
    for sig in signals:
        target = sig.get("target", "")
        if target and target in ctx.excluded:
            continue
        device_id = sig.get("device_id")
        if device_id:
            g = by_device.setdefault(
                device_id,
                {
                    "device_id": device_id,
                    "entities": [],
                    "area": sig.get("area_name", ""),
                    "severity": sig.get("severity", "info"),
                },
            )
            if target:
                g["entities"].append(target)
        else:
            groups.append(
                {
                    "name": _entity_friendly(ctx.hass, target),
                    "device_id": None,
                    "entities": [target] if target else [],
                    "area": sig.get("area_name", ""),
                    "severity": sig.get("severity", "info"),
                }
            )
    for device_id, g in by_device.items():
        dev = dev_reg.async_get(device_id)
        g["name"] = (dev.name_by_user or dev.name if dev else None) or (
            g["entities"][0] if g["entities"] else device_id
        )
        groups.append(g)
    return groups


def _primary_entities(hass: HomeAssistant, entity_ids: list[str]) -> list[str]:
    """Keep only a device's primary entities (``entity_category`` unset) — the
    functional ones (media_player, light, lock). When a device goes offline its
    config/diagnostic entities (bass, crossfade, tv-autoplay…) go unavailable
    too, but listing them is noise: one dead speaker is one problem, shown by
    its media_player. Falls back to all ids when none are primary.
    """
    ent_reg = er.async_get(hass)
    primary = [
        eid
        for eid in entity_ids
        if (entry := ent_reg.async_get(eid)) is not None and entry.entity_category is None
    ]
    return primary or entity_ids


def _check_offline(ctx: _CheckContext) -> list[Finding]:
    """Devices whose entities are unavailable (a genuine outage)."""
    findings: list[Finding] = []
    sigs = [s for s in ctx.signals if s.get("kind") == HEALTH_KIND_UNAVAILABLE]
    for g in _collapse_signals_by_device(ctx, sigs):
        area = f" ({g['area']})" if g["area"] else ""
        findings.append(
            {
                "check_id": "offline_devices",
                "severity": g["severity"] or "warning",
                "category": "issue",
                "title": f"{g['name']} is offline",
                "detail": (
                    f"{g['name']}{area} is unavailable — check its power, battery, "
                    "or network/hub connection."
                ),
                "entities": _primary_entities(ctx.hass, g["entities"])[:_MAX_LISTED],
                "action": "Check the device",
                "device_id": g["device_id"],
            }
        )
    return findings


def _check_batteries(ctx: _CheckContext) -> list[Finding]:
    """Devices reporting a low battery."""
    findings: list[Finding] = []
    sigs = [s for s in ctx.signals if s.get("kind") == HEALTH_KIND_BATTERY_LOW]
    for g in _collapse_signals_by_device(ctx, sigs):
        area = f" ({g['area']})" if g["area"] else ""
        findings.append(
            {
                "check_id": "low_batteries",
                "severity": g["severity"] or "warning",
                "category": "fix",
                "title": f"Replace battery: {g['name']}",
                "detail": (
                    f"{g['name']}{area} battery is low — replace or recharge it soon "
                    "to avoid a gap in coverage."
                ),
                "entities": g["entities"][:_MAX_LISTED],
                "action": "Replace or recharge the battery",
                "device_id": g["device_id"],
            }
        )
    return findings


def _check_integration_errors(ctx: _CheckContext) -> list[Finding]:
    """Integrations in an error/retry state or with an active repair issue."""
    findings: list[Finding] = []
    for sig in ctx.signals:
        if sig.get("kind") != HEALTH_KIND_INTEGRATION_ERROR:
            continue
        domain = sig.get("target", "")
        findings.append(
            {
                "check_id": "integration_errors",
                "severity": sig.get("severity", "critical"),
                "category": "issue",
                "title": f"{domain} integration needs attention",
                "detail": (
                    f"The {domain} integration reported an error — check its configuration "
                    "or credentials in Settings → Devices & Services."
                ),
                "entities": [],
                "action": "Open Settings → Devices & Services",
                "link": f"/config/integrations/integration/{domain}",
                "link_label": "Open in Settings",
            }
        )
    return findings


def _check_unresponsive(ctx: _CheckContext) -> list[Finding]:
    """Entities whose update cadence has lapsed (available but gone quiet)."""
    findings: list[Finding] = []
    sigs = [s for s in ctx.signals if s.get("kind") == HEALTH_KIND_SILENT]
    for g in _collapse_signals_by_device(ctx, sigs):
        area = f" ({g['area']})" if g["area"] else ""
        findings.append(
            {
                "check_id": "unresponsive_sensors",
                "severity": g["severity"] or "warning",
                "category": "issue",
                "title": f"{g['name']} has gone quiet",
                "detail": (
                    f"{g['name']}{area} hasn't reported in far longer than its usual "
                    "interval — it may be stuck or losing connection."
                ),
                "entities": g["entities"][:_MAX_LISTED],
                "action": "Check the device",
                "device_id": g["device_id"],
            }
        )
    return findings


def _check_unstable(ctx: _CheckContext) -> list[Finding]:
    """Devices flapping between available and unavailable (weak signal / range)."""
    findings: list[Finding] = []
    sigs = [s for s in ctx.signals if s.get("kind") == HEALTH_KIND_FLAPPING]
    for g in _collapse_signals_by_device(ctx, sigs):
        area = f" ({g['area']})" if g["area"] else ""
        findings.append(
            {
                "check_id": "unstable_devices",
                "severity": g["severity"] or "warning",
                "category": "issue",
                "title": f"{g['name']} is unstable",
                "detail": (
                    f"{g['name']}{area} keeps dropping in and out — usually a weak "
                    "signal or range issue rather than a dead device."
                ),
                "entities": g["entities"][:_MAX_LISTED],
                "action": "Improve signal/range or move the device",
                "device_id": g["device_id"],
            }
        )
    return findings


def _entity_friendly(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id) if entity_id else None
    return (state.attributes.get("friendly_name") if state else None) or entity_id or "device"


# ── Checks ────────────────────────────────────────────────────────────


def _check_duplicate_automations(ctx: _CheckContext) -> list[Finding]:
    """Likely-duplicate automations, by two deterministic signals:

    1. Same friendly name (read off the state machine — the strongest
       real-world signal and reliable even without automations.yaml).
    2. Identical trigger+condition+action fingerprint (byte-identical copies
       that may carry different names).

    Both are flagged as *possible* duplicates for the user to review. Semantic
    near-duplicates (same intent, different wording) need the model — that's a
    separate model-backed check.
    """
    excluded = ctx.excluded
    findings: list[Finding] = []
    reported: set[str] = set()  # casefolded names already in a finding

    def _emit(entries: list[tuple[str, str | None]], reason: str) -> None:
        # Keep id-less YAML entries (eid=None) so differently-named,
        # byte-identical manual automations still count as duplicates; only drop
        # entries explicitly muted via the exclude label.
        kept = [(name, eid) for (name, eid) in entries if not (eid and eid in excluded)]
        if len(kept) < 2:
            return
        # Dedup by name (the one key common to the state-machine and YAML passes)
        # so the same set isn't reported twice; id-less entries have no eid to
        # key on. Skip only when every member is already in a finding.
        name_keys = {name.casefold() for name, _ in kept}
        if name_keys <= reported:
            return
        reported.update(name_keys)
        ids = [eid for _, eid in kept if eid]  # only the resolvable entity links
        names = [name for name, _ in kept]
        findings.append(
            {
                "check_id": "duplicate_automations",
                "severity": "warning",
                "category": "fix",
                "title": f"Possible duplicate automations: {names[0]}",
                "detail": (
                    f"{len(kept)} automations {reason} ({_join(names)}). "
                    "Review them and delete the extras."
                ),
                "entities": ids[:_MAX_LISTED],
                "action": "Review and delete the duplicates",
            }
        )

    # 1) Same friendly name (off the state machine).
    by_name: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for st in ctx.hass.states.async_all("automation"):
        name = _strip_legacy_selora_prefix(str(st.attributes.get("friendly_name") or "")).strip()
        if name:
            by_name[name.casefold()].append((name, st.entity_id))
    for entries in by_name.values():
        if len(entries) >= 2:
            _emit(entries, "share the same name")

    # 2) Identical trigger+condition+action fingerprint (from automations.yaml).
    by_fp: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for auto in ctx.automations:
        try:
            fp = suggestion_content_fingerprint(auto)
        except (TypeError, ValueError):
            continue
        by_fp[fp].append((_automation_name(auto), ctx.id_to_entity.get(str(auto.get("id")))))
    for entries in by_fp.values():
        if len(entries) >= 2:
            _emit(entries, "have identical triggers and actions")

    return findings


def _check_broken_automations(ctx: _CheckContext) -> list[Finding]:
    """Automations referencing entity_ids that no longer exist (neither a live
    state nor a registry entry) — they can silently fail."""
    findings: list[Finding] = []
    for auto in ctx.automations:
        entity_id = ctx.id_to_entity.get(str(auto.get("id")))
        if entity_id and entity_id in ctx.excluded:
            continue
        refs = _collect_referenced_entity_ids(auto)
        if not refs:
            continue
        missing = _find_unknown_entity_ids(ctx.hass, refs)
        if not missing:
            continue
        name = _automation_name(auto)
        noun = "entity" if len(missing) == 1 else "entities"
        findings.append(
            {
                "check_id": "broken_automations",
                "severity": "warning",
                "category": "issue",
                "title": f"Automation references missing entities: {name}",
                "detail": (
                    f"'{name}' references {len(missing)} {noun} that no longer exist "
                    f"({_join(missing)}); it may silently fail."
                ),
                "entities": [entity_id] if entity_id else [],
                "action": "Update or remove the automation",
            }
        )
    return findings


def _check_updates_available(ctx: _CheckContext) -> list[Finding]:
    """Devices/add-ons/HA components with a pending update (``update`` entity on)."""
    pending = [
        st
        for st in ctx.hass.states.async_all("update")
        if st.state == "on" and st.entity_id not in ctx.excluded
    ]
    if not pending:
        return []
    names = [st.attributes.get("friendly_name") or st.entity_id for st in pending]
    noun = "update is" if len(pending) == 1 else "updates are"
    return [
        {
            "check_id": "updates_available",
            "severity": "info",
            "category": "improvement",
            "title": f"{len(pending)} {noun} available",
            "detail": f"Updates are available for {_join(names)}.",
            "entities": [st.entity_id for st in pending][:_MAX_LISTED],
            "action": "Review and install updates",
        }
    ]


# ── Helpers ───────────────────────────────────────────────────────────


def _automation_name(auto: dict[str, Any]) -> str:
    """Human name for an automation config: its alias (Selora prefix stripped),
    falling back to its id."""
    alias = _strip_legacy_selora_prefix(str(auto.get("alias") or "").strip())
    return alias or str(auto.get("id") or "automation")


def _join(items: list[str]) -> str:
    """Comma-join, capped at ``_MAX_LISTED`` with a '+N more' suffix."""
    shown = items[:_MAX_LISTED]
    text = ", ".join(shown)
    extra = len(items) - len(shown)
    if extra > 0:
        text += f", +{extra} more"
    return text
