"""AuditRunner — schedules the deterministic health checks for the Health page.

Runs the ``insights_checks`` catalog on a schedule and caches the result (the
per-check checklist + findings + score) so the panel can render it. The checks
are pure rules over HA state — no LLM — so a run is cheap, stable across runs,
and never needs a provider configured.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval

if TYPE_CHECKING:
    from .health_store import HealthStore

_LOGGER = logging.getLogger(__name__)

# Delay the first run after startup so registries/states are populated and we
# don't compete with the heavier setup work.
_INITIAL_DELAY_SECONDS = 180
_DEFAULT_INTERVAL_HOURS = 24
# Grace after HA finishes starting before a score is trustworthy. A just-booted
# home reports many devices `unavailable` until their integrations reconnect;
# an audit taken in that window over-counts offline devices and tanks the score.
# We flag the audit "settling" until this grace elapses so the panel shows a
# spinner instead of a misleading number.
_BOOT_SETTLE_SECONDS = 90


class AuditRunner:
    """Runs and caches the deterministic home health checks."""

    def __init__(self, hass: HomeAssistant, health_store: HealthStore) -> None:
        self._hass = hass
        self._health_store = health_store
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._unsub_initial: CALLBACK_TYPE | None = None
        self._unsub_started: CALLBACK_TYPE | None = None
        # Boot-settle state (see _BOOT_SETTLE_SECONDS): the deadline after which
        # the score is trustworthy, and a latch so is_settling() is cheap and
        # never flips back to True once the grace has passed.
        self._settle_deadline: datetime | None = None
        self._settled: bool = False
        # Every in-flight audit task — the delayed initial run AND each periodic
        # tick. Tracked so async_stop can cancel/drain them on unload: an audit
        # that outlives its entry would finish holding a now-stale HealthStore
        # instance and save the whole store document over the replacement
        # entry's shared store (clobbering its signals / scan metadata / audit).
        self._tasks: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()

    async def async_start(self, interval_hours: int = _DEFAULT_INTERVAL_HOURS) -> None:
        # Arm the boot-settle window. If HA is already running when we set up (a
        # reload or a late-added entry), devices are already online — nothing to
        # settle. Otherwise start the grace once HA finishes booting.
        if self._hass.is_running:
            self._settled = True
        else:
            self._unsub_started = self._hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._on_ha_started
            )
        self._unsub_timer = async_track_time_interval(
            self._hass, self._periodic_run, timedelta(hours=interval_hours)
        )
        # Schedule the first audit with an untracked timer, NOT
        # ``async_create_task``: a task created during setup is awaited by HA
        # bootstrap, so a task that sleeps for the initial delay (and then
        # makes a slow LLM call) stalls startup until the bootstrap watchdog
        # times out. ``async_call_later`` just arms a timer and returns.
        self._unsub_initial = async_call_later(
            self._hass, _INITIAL_DELAY_SECONDS, self._initial_run
        )

    @callback
    def _on_ha_started(self, _event: Event) -> None:
        self._unsub_started = None
        self._settle_deadline = datetime.now(UTC) + timedelta(seconds=_BOOT_SETTLE_SECONDS)

    def is_settling(self) -> bool:
        """True while the home is still starting up — devices are reconnecting,
        so any score would over-count offline devices. Ends a fixed grace after
        HA finishes starting (or immediately if HA was already up at setup)."""
        if self._settled:
            return False
        if self._settle_deadline is None:
            return True  # HA hasn't fired STARTED yet
        if datetime.now(UTC) >= self._settle_deadline:
            self._settled = True  # latch: never flip back once settled
            return False
        return True

    def settle_retry_seconds(self) -> int:
        """How long the panel should wait before re-checking while settling: a
        short poll before HA has started (we can't know when it will), then the
        exact remaining grace once the deadline is set."""
        if self._settle_deadline is None:
            return 15
        remaining = (self._settle_deadline - datetime.now(UTC)).total_seconds()
        return max(1, min(_BOOT_SETTLE_SECONDS, round(remaining)))

    @callback
    def _initial_run(self, _now: datetime) -> None:
        self._unsub_initial = None
        self._spawn_audit("selora_ai_initial_audit")

    @callback
    def _periodic_run(self, _now: datetime) -> None:
        # async_track_time_interval would otherwise run the coroutine as an
        # UNTRACKED task; spawn it tracked so async_stop can drain an in-flight
        # periodic audit on unload.
        self._spawn_audit("selora_ai_periodic_audit")

    @callback
    def _spawn_audit(self, name: str) -> None:
        # Fires well after bootstrap, so a background task here never blocks
        # startup; it just must not be garbage-collected mid-flight, and must
        # be cancellable on unload.
        task = self._hass.async_create_background_task(self._scheduled_run(None), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def async_stop(self) -> None:
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        if self._unsub_initial:
            self._unsub_initial()
            self._unsub_initial = None
        if self._unsub_started:
            self._unsub_started()
            self._unsub_started = None
        # Cancel and drain EVERY in-flight audit (initial + periodic) so a run
        # awaiting a slow LLM response can't complete after unload and persist a
        # stale store document over the replacement entry's store.
        pending = [t for t in self._tasks if not t.done()]
        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    async def async_run_tracked(self, *, force: bool = False) -> dict[str, Any]:
        """Run an audit as a TRACKED task and await its result — used by the
        manual websocket rerun so a long rerun still in flight during a reload
        is cancelled/drained by async_stop rather than outliving the entry and
        persisting its stale store over the replacement entry's store."""
        task = self._hass.async_create_task(
            self.async_run(force=force), name="selora_ai_manual_audit"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return await task

    async def _scheduled_run(self, _now: datetime | None) -> None:
        try:
            await self.async_run()
        except Exception:  # noqa: BLE001 — a scheduled callback must not kill its timer
            _LOGGER.exception("Home audit failed")

    async def get_last_audit(self) -> dict[str, Any] | None:
        return await self._health_store.get_last_audit()

    async def async_run(self, *, force: bool = False) -> dict[str, Any]:
        """Run the deterministic health checks and cache the result.

        The "audit" is a deterministic check catalog (``insights_checks``): pure
        rules over HA state — no LLM, no config needed, stable across runs — so
        it runs regardless of ``force`` and always returns ``ok`` (empty when
        the home is healthy).
        """
        async with self._lock:
            from .insights_checks import (  # noqa: PLC0415
                async_run_checks,
                band_for,
                flatten_findings,
                score_breakdown_from_findings,
            )

            results = await async_run_checks(self._hass)
            findings = flatten_findings(results)
            # Fleet for fraction-weighted device-health penalties: the per-device
            # outage checks subtract a penalty proportional to the share of these
            # devices affected, so the score tracks how many are down (not just
            # whether any are). Scope it to the *active* fleet — devices that can
            # actually produce a finding — and pass the exact ID set to scoring so
            # a finding for a device no longer in that fleet (deleted/disabled/
            # retired since its signal was raised) can't be scored as a slice of a
            # fleet it has left. The breakdown also carries the per-finding point
            # attribution so the panel can explain "why this score".
            active_ids = self._active_device_ids(findings)
            breakdown = score_breakdown_from_findings(findings, len(active_ids), active_ids)
            score = breakdown["score"]
            return await self._store_result(
                _record(
                    status="ok",
                    recommendations=findings,
                    checks=results,
                    score=score,
                    band=band_for(score),
                    score_breakdown=breakdown,
                )
            )

    def _active_device_ids(self, findings: list[dict[str, Any]]) -> set[str]:
        """IDs of the *active* device fleet the fleet-fraction penalty normalizes
        against: the devices the health monitor could actually raise a finding
        for. Kept in lockstep with the monitor's own eligibility so the fraction
        denominator matches the population that fed the numerator.

        A device qualifies when it owns at least one entity the monitor would
        scan — enabled, not muted via the Selora exclude label, in a tracked
        domain (:func:`_in_scope`), not an inherently transient BLE/presence
        entity (:func:`_is_transient`), and currently backed by a state object —
        OR when a current fleet finding already represents it (below).

        Excluded, because they can never contribute a finding (and would only
        distort the fraction): user-disabled devices, retained-but-empty devices,
        fully Selora-muted devices, out-of-scope/transient-only devices, and
        devices whose entities linger in the registry with no state object.

        The one exception is preserved offline devices: when an integration
        briefly drops an already-offline device's state objects (Sonos
        rediscovery churn), :meth:`HealthMonitor._keep_vanished_offline` keeps
        the outage signal alive off the still-enabled registry entry, so the
        device has a real finding but no state right now. Those devices ARE in
        the fleet — union in every finding whose device is still present and
        enabled so a genuine outage scores as a fleet slice, not a lone warning.
        """
        from homeassistant.helpers import device_registry as dr  # noqa: PLC0415
        from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

        from .entity_filter import resolve_ignored_entity_ids  # noqa: PLC0415
        from .health_monitor import (  # noqa: PLC0415
            _in_scope,
            _is_battery_entity,
            _is_preserved_offline,
            _is_transient,
        )

        ignored = resolve_ignored_entity_ids(self._hass)
        states = self._hass.states
        ent_reg = er.async_get(self._hass)
        dev_reg = dr.async_get(self._hass)

        eligible_device_ids: set[str] = set()
        for e in ent_reg.entities.values():
            if not e.device_id or e.disabled_by or e.entity_id in ignored:
                continue
            if _is_transient(e.entity_id, ent_reg):
                continue
            state = states.get(e.entity_id)
            if state is None:
                continue
            # In lockstep with the monitor's detector scopes: offline/silent/
            # flapping only fire for tracked domains (``_in_scope``), but the
            # battery detector is intentionally all-domain — so a device whose
            # only battery entity is out of scope (a valve, a siren, a mower) can
            # still be flagged and must count toward the fleet, or ten of them
            # with one low battery would score as a 1/1 outage instead of 1/10.
            if _in_scope(e.entity_id) or _is_battery_entity(state):
                eligible_device_ids.add(e.device_id)
        active = {
            dev.id
            for dev in dev_reg.devices.values()
            if dev.disabled_by is None and dev.id in eligible_device_ids
        }
        # Preserved offline devices have a finding but no current state — count
        # only genuine ones, where a still-present, enabled entity is holding the
        # signal alive through rediscovery churn (``_is_preserved_offline``). A
        # stale signal whose entity was actually removed/disabled does NOT qualify
        # (the device may be a retained-but-empty shell), so it stays out of the
        # fleet and its finding is scored as a standalone warning, not a 1/1 hit.
        #
        # Restricted to ``offline_devices``: only unavailable signals are
        # preserved (``HealthMonitor._keep_vanished_offline``). The battery /
        # silent / unstable detectors resolve their signals the next scan once the
        # state vanishes, so promoting a stale one of those would over-penalize.
        for f in findings:
            if f.get("check_id") != "offline_devices":
                continue
            device_id = f.get("device_id")
            if not device_id or device_id in active:
                continue
            dev = dev_reg.async_get(device_id)
            if dev is None or dev.disabled_by is not None:
                continue
            if any(
                _is_preserved_offline(self._hass, ent_reg, eid) for eid in (f.get("entities") or ())
            ):
                active.add(device_id)
        return active

    async def _store_result(self, record: dict[str, Any]) -> dict[str, Any]:
        """Persist and return ``record`` — but never let a failed run clobber the
        last successful audit. A transient Cloud 502/timeout keeps showing the
        last good cards; the error is still returned to the immediate caller
        (the Re-run click) so it can surface a toast. An error/no_llm is only
        cached when there's no good audit to preserve.
        """
        if record.get("status") == "ok":
            await self._health_store.set_last_audit(record)
            return record
        prior = await self._health_store.get_last_audit()
        if not (prior and prior.get("status") == "ok"):
            await self._health_store.set_last_audit(record)
        return record


def _record(
    *,
    status: str,
    response: str = "",
    recommendations: list[dict[str, Any]] | None = None,
    checks: list[dict[str, Any]] | None = None,
    score: int | None = None,
    band: str = "",
    score_breakdown: dict[str, Any] | None = None,
    quick_actions: list[Any] | None = None,
    fingerprint: str = "",
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,  # ok | no_llm | error
        "response": response,
        "recommendations": recommendations or [],
        # Per-check results (checklist): every check that ran + its outcome.
        "checks": checks or [],
        # Deterministic 0-100 health score + A-F band (None when not computed).
        "score": score,
        "band": band,
        # Per-finding point attribution behind the score ("why this score").
        "score_breakdown": score_breakdown,
        "quick_actions": quick_actions or [],
        "fingerprint": fingerprint,
        "error": error,
        "generated_at": datetime.now(UTC).isoformat(),
    }
