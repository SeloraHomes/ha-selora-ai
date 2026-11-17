"""Health issue events — the Health view's device problems, for automations.

After each audit, the device-health findings (the same cards the Health view
shows) are reconciled against the issues already known, and the bus gets
``selora_ai_health_issue_raised`` / ``selora_ai_health_issue_cleared``. A
notification automation tags its push with ``issue_id`` and clears that tag on
the cleared event, so the phone mirrors the Health view.

Health findings come and go (a device rebooting, a battery hovering at the
threshold), so neither edge fires on one scan:

* **Evidence is a health scan, not an audit.** The device checks read the
  signals the last scan stored, so an audit with no new scan behind it (the
  daily periodic run) adds nothing and is skipped, and time is measured
  between scan timestamps.
* **Raised** only when a scan still finds the issue at least
  ``HEALTH_EVENT_CONFIRM_SECS`` after it first appeared, with no scan in
  between missing it.
* **Cleared** only for an issue that was raised, once it has been missing for
  the same window. Coming back inside the window cancels the clear.
* **No scan taken while HA was still booting** counts: devices that haven't
  reconnected yet are not problems.
* **A check that errored** says nothing about its issues; they are left as they
  were rather than read as resolved.
* **A check's first successful run records what is already wrong as a
  baseline** and announces none of it, so an upgrade does not push every
  standing problem at once — even when the check failed on that first audit.
  A baseline issue still counts in the entities, and leaves silently.

The state persists in the health store, so a restart neither re-announces an
issue nor forgets one it owes a clear for.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import logging
from typing import TYPE_CHECKING

from .const import (
    EVENT_HEALTH_ISSUE_CLEARED,
    EVENT_HEALTH_ISSUE_RAISED,
    HEALTH_EVENT_CONFIRM_SECS,
)
from .health_store import get_health_store

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .health_store import HealthStore
    from .types import CheckResult, Finding, HealthEventState, HealthIssue, HealthIssueRecord

_LOGGER = logging.getLogger(__name__)

# The checks about the home's devices. Automation hygiene and pending updates
# are advice, not something to be woken up for.
NOTIFY_CHECKS = frozenset(
    {
        "offline_devices",
        "low_batteries",
        "integration_errors",
        "unresponsive_sensors",
        "unstable_devices",
    }
)

_PENDING = "pending"
_ANNOUNCED = "announced"
_BASELINE = "baseline"
_CONFIRMED = frozenset({_ANNOUNCED, _BASELINE})


def issue_id(finding: Finding) -> str | None:
    """Stable id for the problem a finding reports, across audits."""
    check = finding.get("check_id")
    if not check:
        return None
    subject = finding.get("device_id") or next(iter(finding.get("entities") or ()), None)
    subject = subject or finding.get("link") or finding.get("title")
    return f"{check}:{subject}" if subject else None


def _issue(finding: Finding, iid: str, since: str) -> HealthIssue:
    issue: HealthIssue = {
        "issue_id": iid,
        "check": finding.get("check_id", ""),
        "severity": finding.get("severity", "warning"),
        "title": finding.get("title", ""),
        "detail": finding.get("detail", ""),
        "device_id": finding.get("device_id"),
        "entities": list(finding.get("entities") or []),
        "since": since,
    }
    if finding.get("link"):
        issue["link"] = finding["link"]
    return issue


def _elapsed(since: str | None, now: datetime) -> float:
    if not since:
        return 0.0
    try:
        return (now - datetime.fromisoformat(since)).total_seconds()
    except ValueError:
        return 0.0


def confirmed_issues(records: dict[str, HealthIssueRecord]) -> list[HealthIssue]:
    """Issues the entities report: raised ones and the baseline, worst first."""
    rank = {"critical": 0, "warning": 1, "info": 2}
    issues = [r["issue"] for r in records.values() if r.get("state") in _CONFIRMED]
    issues.sort(key=lambda i: (rank.get(i.get("severity", ""), 3), i.get("since", "")))
    return issues


async def async_confirmed_issues(hass: HomeAssistant) -> list[HealthIssue] | None:
    """Confirmed issues, or None when Insights is off or has not run yet."""
    store = get_health_store(hass)
    if store is None:
        return None
    state = await store.get_health_events()
    return None if state is None else confirmed_issues(state["issues"])


class HealthEventTracker:
    """Turns successive audits into raised/cleared events."""

    def __init__(self, hass: HomeAssistant, store: HealthStore) -> None:
        self._hass = hass
        self._store = store

    async def async_process(
        self,
        results: list[CheckResult],
        scan: str | None = None,
        *,
        not_before: datetime | None = None,
    ) -> None:
        """Reconcile one audit's results and fire the events it confirms.

        ``scan`` is the health scan the results were computed from (the store's
        ``last_scan`` when omitted). Each check takes a scan once: one already
        processed adds no evidence, while a check that errored on it can still
        take it on a later audit. A scan taken before ``not_before`` (the boot
        settle deadline) is not evidence.
        """
        if scan is None:
            scan = await self._store.get_last_scan()
        if not scan:
            return
        try:
            now = datetime.fromisoformat(scan)
        except ValueError:
            return
        if not_before is not None and now < not_before:
            return
        state: HealthEventState = await self._store.get_health_events() or {
            "scans": {},
            "issues": {},
        }
        scans = state["scans"]
        # A check with no scan recorded has never succeeded: its findings are
        # the baseline.
        baselined = set(scans)
        ran = {
            r["check_id"]
            for r in results
            if r["check_id"] in NOTIFY_CHECKS
            and r.get("status") != "error"
            and scans.get(r["check_id"]) != scan
        }
        if not ran:
            return
        present: dict[str, Finding] = {}
        for result in results:
            if result["check_id"] not in ran:
                continue
            for finding in result["findings"]:
                iid = issue_id(finding)
                if iid is not None:
                    present.setdefault(iid, finding)

        records: dict[str, HealthIssueRecord] = state["issues"]
        before = (deepcopy(records), baselined)
        raised: list[HealthIssue] = []
        cleared: list[HealthIssue] = []

        for iid, finding in present.items():
            record = records.get(iid)
            if record is None:
                standing = iid.split(":", 1)[0] not in baselined
                records[iid] = {
                    "state": _BASELINE if standing else _PENDING,
                    "since": scan,
                    "missing_since": None,
                    "issue": _issue(finding, iid, scan),
                }
                continue
            record["missing_since"] = None
            if record["state"] != _PENDING:
                # Keep the announced wording, so the clear matches the raise.
                continue
            record["issue"] = _issue(finding, iid, record["since"])
            if _elapsed(record["since"], now) >= HEALTH_EVENT_CONFIRM_SECS:
                record["state"] = _ANNOUNCED
                raised.append(record["issue"])

        for iid in list(records):
            record = records[iid]
            if iid in present or iid.split(":", 1)[0] not in ran:
                continue
            if record["state"] != _ANNOUNCED:
                # Never announced, so nothing to take back.
                del records[iid]
            elif record["missing_since"] is None:
                record["missing_since"] = scan
            elif _elapsed(record["missing_since"], now) >= HEALTH_EVENT_CONFIRM_SECS:
                cleared.append(record["issue"])
                del records[iid]

        for check in ran:
            scans[check] = scan
        # Every scan moves ``scans``, but only a record change needs the disk now:
        # re-processing the same scan after a crash reaches the same records.
        changed = (records, set(scans)) != before
        await self._store.set_health_events(state, save=changed)

        active = len(confirmed_issues(records))
        for issue, event_type in [(i, EVENT_HEALTH_ISSUE_RAISED) for i in raised] + [
            (i, EVENT_HEALTH_ISSUE_CLEARED) for i in cleared
        ]:
            self._hass.bus.async_fire(event_type, {**issue, "active_issues": active})
        if raised or cleared:
            _LOGGER.debug("Health issues: %d raised, %d cleared", len(raised), len(cleared))
