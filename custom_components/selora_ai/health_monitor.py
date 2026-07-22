"""HealthMonitor — Layer 1 of Insights: deterministic, LLM-free health detection.

Observes only what HA itself can see (entities, devices, integrations) — never
the host. Feeds off the shared ``state_changed`` tap for real-time bookkeeping
and runs a periodic aggregation tick that writes signals to ``HealthStore``.

Detectors:
  * unavailable   — entity stuck ``unavailable`` past a grace window
  * flapping      — too many availability flips inside a rolling window
  * silent        — an entity whose observed update cadence has lapsed
  * battery_low   — battery entity/attribute at or below the threshold
  * integration_error — config entry in error/retry, or an active HA repair issue

Each detector reconciles: offenders are upserted as active signals, and
previously-active signals whose condition cleared are resolved.
"""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State, callback
from homeassistant.helpers import (
    area_registry as ar,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers import (
    issue_registry as ir,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .const import (
    COLLECTOR_DOMAINS,
    DEFAULT_INSIGHTS_INTERVAL,
    DOMAIN,
    HEALTH_BATTERY_LOW_PCT,
    HEALTH_FLAP_MIN_TRANSITIONS,
    HEALTH_FLAP_WINDOW_SECS,
    HEALTH_KIND_BATTERY_LOW,
    HEALTH_KIND_FLAPPING,
    HEALTH_KIND_INTEGRATION_ERROR,
    HEALTH_KIND_SILENT,
    HEALTH_KIND_UNAVAILABLE,
    HEALTH_SEVERITY_CRITICAL,
    HEALTH_SEVERITY_WARNING,
    HEALTH_SILENT_MIN_SECS,
    HEALTH_SILENT_MULTIPLIER,
    HEALTH_UNAVAILABLE_GRACE_SECS,
    SIGNAL_INSIGHTS_UPDATED,
)

if TYPE_CHECKING:
    from .health_store import HealthStore

_LOGGER = logging.getLogger(__name__)

# "Offline" means the integration explicitly reports the entity as
# ``unavailable`` (device unreachable). ``unknown`` is NOT offline — it's a
# valid "no value yet" state that many stateless service entities (TTS,
# notify) and freshly-started sensors sit in normally, so flagging it as a
# problem is noise the user can't act on. Aligns with HA's own device
# availability semantics.
_UNAVAILABLE_STATES = frozenset({"unavailable"})
# States that don't prove a device is reachable, for the whole-device "is it
# offline" roll-up. Broader than _UNAVAILABLE_STATES: an offline device's
# siblings often fall to "unknown" rather than "unavailable", and neither state
# demonstrates the device is responding (a real reading does). Used ONLY by
# _device_unreachable — availability elsewhere (flapping/silent) still treats
# "unknown" as available.
_UNREACHABLE_STATES = frozenset({"unavailable", "unknown"})
# Cap the per-entity transition ring so a pathological flapper can't grow the
# in-memory tracker without bound.
_MAX_TRANSITIONS = 64
# EMA smoothing factor for the observed update-interval baseline.
_EMA_ALPHA = 0.3
# Require this many observed intervals before trusting the cadence baseline,
# so "silent" never fires on an entity we've barely seen.
_MIN_INTERVALS_FOR_CADENCE = 5
# Availability-ratio gate. An entity available for less than
# ``_AVAIL_TRANSIENT_RATIO`` of its observed lifetime is behaving as a
# usually-absent device (a BLE beacon, a drive-by tag) — its unavailability is
# its normal condition, not a fault — so it's excluded from offline/flapping
# detection. This is config-agnostic: no integration allowlist needed. It only
# applies after ``_AVAIL_MIN_OBSERVED_SECS`` of history, so a device that JUST
# went offline (its ratio is still high) is still flagged; a device that dies
# and stays offline drops below the threshold only after being offline for a
# large fraction of its whole observed life. In-memory, so it re-learns after a
# restart — the ``_is_transient`` fast-path covers known cases immediately.
_AVAIL_MIN_OBSERVED_SECS = 6 * 3600
_AVAIL_TRANSIENT_RATIO = 0.4
# A currently-unavailable entity that has flipped availability at least this
# many times in the last day is "intermittent" — coming and going, typically a
# range / weak-signal issue rather than a dead device. Too slow for the
# short-window flap detector to catch.
_INTERMITTENT_WINDOW_SECS = 24 * 3600
_INTERMITTENT_MIN_TRANSITIONS = 3
# "Unavailable by design": an entity that has been unavailable for the large
# majority of a multi-day window — AND was already unavailable at the start of
# it, so it's a persistent normal condition rather than a device that was
# healthy and recently died — is reporting a no-data state on purpose (a
# HydroQuebec outage sensor with no outage; a seasonal / on-demand sensor). Read
# from the recorder so it survives restarts and works for standalone /
# single-entity sensors the whole-device gate can't judge. Reuses the
# availability-ratio threshold. A genuinely dead device stays reported because
# it WAS available at the window start (until it eventually falls out of
# recorder retention).
_BYDESIGN_WINDOW_SECS = 7 * 24 * 3600
# Delay before the first scan, so it fires well after HA bootstrap (never
# blocking startup) with the state machine already populated.
_INITIAL_SCAN_DELAY_SECONDS = 60


def _is_available(state: str) -> bool:
    return state.casefold() not in _UNAVAILABLE_STATES


# ``state_class`` values that mark a periodically-reporting measurement. Only
# ``measurement`` — a continuously-sampled quantity (temperature, power,
# humidity, battery %) — implies a reporting cadence, so only it can be
# "silent". ``total`` / ``total_increasing`` are cumulative counters that
# increment on EVENTS (energy consumed, LLM calls made — e.g. Selora's own
# usage sensors); they legitimately stay flat for long stretches, so state_class
# alone (which describes aggregation, not cadence) must NOT admit them, or a
# quiet counter would raise a false ``silent`` signal. Event-driven entities
# (lights, switches, locks, motion) carry no state_class and are excluded too.
_PERIODIC_STATE_CLASSES = frozenset({"measurement"})


def _is_periodic_reporter(state: State) -> bool:
    return str(state.attributes.get("state_class") or "") in _PERIODIC_STATE_CLASSES


@dataclass
class _EntityTrack:
    """In-memory, restart-disposable bookkeeping for one entity."""

    last_available: bool = True
    transitions: deque[float] = field(default_factory=lambda: deque(maxlen=_MAX_TRANSITIONS))
    last_update_ts: float | None = None
    ema_interval: float | None = None
    interval_samples: int = 0
    # Availability accounting for the transient/usually-absent gate.
    available_secs: float = 0.0
    observed_secs: float = 0.0
    last_avail_ts: float | None = None


class HealthMonitor:
    """Deterministic local health detection over HA-observable state."""

    def __init__(self, hass: HomeAssistant, store: HealthStore) -> None:
        self._hass = hass
        self._store = store
        self._tracks: dict[str, _EntityTrack] = {}
        self._excluded: frozenset[str] = frozenset()
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._unsub_initial: CALLBACK_TYPE | None = None
        # Every in-flight scan task — the delayed initial scan, each periodic
        # tick, AND externally-requested websocket rescans. Tracked so
        # async_stop can cancel/drain them on unload: a scan that outlives its
        # entry would finish holding a now-stale HealthStore and save the whole
        # store document over the replacement entry's store (losing newer
        # signals / scan metadata).
        self._tasks: set[asyncio.Task[None]] = set()
        self._interval = DEFAULT_INSIGHTS_INTERVAL
        # Serialize scans so a websocket rescan can't interleave with the
        # scheduled tick and lose store updates.
        self._scan_lock = asyncio.Lock()

    async def async_start(self, interval: int = DEFAULT_INSIGHTS_INTERVAL) -> None:
        """Seed trackers from current state and start the periodic tick."""
        self._interval = interval
        self._seed_from_state_machine()
        self._unsub_timer = async_track_time_interval(
            self._hass, self._periodic_scan, timedelta(seconds=interval)
        )
        # Arm the first scan with an untracked timer, NOT ``async_create_task``:
        # a task created during setup is awaited by HA bootstrap / config-entry
        # reload, so one that sleeps for the initial delay would stall startup
        # until the bootstrap watchdog fires. ``async_call_later`` just arms a
        # timer and returns (same pattern as the audit runner).
        self._unsub_initial = async_call_later(
            self._hass, _INITIAL_SCAN_DELAY_SECONDS, self._initial_scan
        )

    @callback
    def _initial_scan(self, _now: datetime) -> None:
        self._unsub_initial = None
        self._spawn_scan("selora_ai_initial_health_scan")

    @callback
    def _periodic_scan(self, _now: datetime) -> None:
        # async_track_time_interval would otherwise run the coroutine as an
        # UNTRACKED task; spawn it tracked so async_stop can drain an in-flight
        # periodic scan on unload.
        self._spawn_scan("selora_ai_periodic_health_scan")

    @callback
    def _spawn_scan(self, name: str) -> None:
        # Fires well after bootstrap, so a background task here never blocks
        # startup; it just must not be garbage-collected mid-flight, and must
        # be cancellable on unload.
        task = self._hass.async_create_background_task(self._scheduled_scan(None), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def async_request_scan(self) -> None:
        """Run a scan now as a TRACKED task and await it — used by the websocket
        rescan so an externally-requested scan in flight during a reload is
        cancelled/drained by async_stop rather than outliving the entry."""
        task = self._hass.async_create_task(self.async_scan(), name="selora_ai_rescan")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        await task

    async def async_stop(self) -> None:
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        if self._unsub_initial:
            self._unsub_initial()
            self._unsub_initial = None
        # Cancel and drain EVERY in-flight scan (initial + periodic + rescan) so
        # an old scan can't complete after unload and save its stale store
        # document over the replacement entry's store.
        pending = [t for t in self._tasks if not t.done()]
        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    def _seed_from_state_machine(self) -> None:
        now = datetime.now(UTC).timestamp()
        for state in self._hass.states.async_all():
            if not _in_scope(state.entity_id):
                continue
            track = self._tracks.setdefault(state.entity_id, _EntityTrack())
            track.last_available = _is_available(state.state)
            track.last_update_ts = now
            track.last_avail_ts = now

    # ── Hot path: pure in-memory bookkeeping ─────────────────────────────

    def handle_state_change(
        self, entity_id: str, new_state: State | None, old_state: State | None
    ) -> None:
        """Record a state transition for cadence + flap tracking (no I/O).

        Called from the shared ``state_changed`` listener in __init__.
        """
        if new_state is None or not _in_scope(entity_id):
            return
        # Never track transient BLE/presence entities: no flapping/silent
        # tracks are built for them, so those detectors skip them for free.
        if _is_transient(entity_id, er.async_get(self._hass)):
            return
        now = new_state.last_updated.timestamp()
        track = self._tracks.setdefault(entity_id, _EntityTrack())

        # Cadence baseline (EMA of inter-update intervals).
        if track.last_update_ts is not None:
            delta = now - track.last_update_ts
            if delta > 0:
                if track.ema_interval is None:
                    track.ema_interval = delta
                else:
                    track.ema_interval = _EMA_ALPHA * delta + (1 - _EMA_ALPHA) * track.ema_interval
                track.interval_samples += 1
        track.last_update_ts = now

        # Availability flip tracking. Bank the time spent in the prior state
        # into the availability totals before flipping.
        available = _is_available(new_state.state)
        if available != track.last_available:
            _account_availability(track, now)
            track.transitions.append(now)
            track.last_available = available

    def handle_entity_removed(self, entity_id: str) -> None:
        """Drop the in-memory tracker for an entity that left the state machine.

        HA emits ``state_changed`` with ``new_state=None`` when an integration
        removes an entity. Without discarding the tracker, an integration that
        churns dynamic entities would grow ``_tracks`` without bound over a
        long-running instance (and a stale track could still be flagged by the
        flap detector, which reads transitions rather than live state).
        """
        self._tracks.pop(entity_id, None)

    # ── Periodic tick ────────────────────────────────────────────────────

    async def _scheduled_scan(self, _now: datetime | None) -> None:
        try:
            await self.async_scan()
        except Exception:  # noqa: BLE001 — a scheduled callback must not kill its timer
            _LOGGER.exception("Health scan failed")

    async def async_scan(self) -> None:
        """Run all detectors and reconcile signals against the store.

        Serialized (a websocket rescan can't interleave with the scheduled
        tick) and batched: detectors defer their writes, then a single store
        save happens via set_last_scan at the end.
        """
        async with self._scan_lock:
            now = datetime.now(UTC)
            now_ts = now.timestamp()

            # Entities the user has muted via the "Selora exclude" label (on the
            # entity, its device, or its area) — the same label the suggestion
            # ignore-list uses. Resolved once per scan; detectors skip these so
            # no signal/fix/troubleshooting row is produced for them.
            from .entity_filter import resolve_ignored_entity_ids  # noqa: PLC0415

            self._excluded = resolve_ignored_entity_ids(self._hass)

            ent_reg = er.async_get(self._hass)
            dev_reg = dr.async_get(self._hass)
            area_reg = ar.async_get(self._hass)

            def area_of(entity_id: str) -> str:
                return _resolve_area(entity_id, ent_reg, dev_reg, area_reg)

            await self._detect_flapping(now_ts, area_of)
            await self._detect_unavailable(now_ts, area_of)
            await self._detect_silent(now_ts, area_of)
            await self._detect_battery(area_of)
            await self._detect_integration_health()

            # Drop resolved signals past the retention window, then persist the
            # whole scan's changes in one write (set_last_scan calls _save).
            await self._store.prune_resolved()
            await self._store.set_last_scan(now.isoformat())

        # Nudge the Home Health sensor to refresh off the just-written store
        # instead of waiting for its 60s poll (outside the lock — pure fan-out).
        async_dispatcher_send(self._hass, SIGNAL_INSIGHTS_UPDATED)

    async def _detect_flapping(self, now_ts: float, area_of: Any) -> None:
        offenders: dict[str, dict[str, Any]] = {}
        cutoff = now_ts - HEALTH_FLAP_WINDOW_SECS
        for entity_id, track in self._tracks.items():
            if entity_id in self._excluded:
                continue
            # Keeps every track's availability accounting current, and drops
            # usually-absent devices (their coming and going isn't a fault).
            if _usually_unavailable(track, now_ts):
                continue
            recent = [t for t in track.transitions if t >= cutoff]
            if len(recent) >= HEALTH_FLAP_MIN_TRANSITIONS:
                offenders[entity_id] = {
                    "transitions": len(recent),
                    "window_seconds": HEALTH_FLAP_WINDOW_SECS,
                }
        await self._reconcile(
            HEALTH_KIND_FLAPPING,
            "entity",
            offenders,
            HEALTH_SEVERITY_WARNING,
            area_of,
        )

    async def _detect_unavailable(self, now_ts: float, area_of: Any) -> None:
        # Anchor the duration to the signal's persisted first_seen, not the
        # entity's last_changed. last_changed resets to boot time on an HA
        # restart, so a device that's been offline for days would otherwise
        # report only the minutes since the last restart. first_seen survives
        # restarts (it's persisted) and is preserved while a signal stays
        # active; it resets to the episode start when a resolved signal
        # reactivates, so a recurrence isn't anchored to the previous outage.
        prior_first_seen: dict[str, float] = {}
        for sig in await self._store.get_signals(status="active", kind=HEALTH_KIND_UNAVAILABLE):
            seen = _iso_to_ts(sig.get("first_seen"))
            if seen is not None:
                prior_first_seen[sig["target"]] = seen

        ent_reg = er.async_get(self._hass)
        offenders: dict[str, dict[str, Any]] = {}
        for state in self._hass.states.async_all():
            if not _in_scope(state.entity_id):
                continue
            if state.entity_id in self._excluded:
                continue
            if _is_transient(state.entity_id, ent_reg):
                continue
            if _is_available(state.state):
                continue
            # Usually-absent device (low availability ratio) — but ONLY skip it
            # when it's actively coming and going (a transient/roaming device
            # that flaps). A device that went unavailable and STAYED that way
            # (few transitions) is dead: report it. Otherwise a real device
            # (e.g. a speaker) that's been off a while gets silently hidden,
            # because its accumulating offline time drives the ratio down and
            # reclassifies it as "usually absent by design".
            track = self._tracks.get(state.entity_id)
            if track is not None and _usually_unavailable(track, now_ts):
                cutoff = now_ts - _INTERMITTENT_WINDOW_SECS
                recent_flips = sum(1 for t in track.transitions if t >= cutoff)
                if recent_flips >= _INTERMITTENT_MIN_TRANSITIONS:
                    continue
            anchor_ts = state.last_changed.timestamp()
            seen_ts = prior_first_seen.get(state.entity_id)
            if seen_ts is not None:
                anchor_ts = min(anchor_ts, seen_ts)
            age = now_ts - anchor_ts
            if age >= HEALTH_UNAVAILABLE_GRACE_SECS:
                evidence: dict[str, Any] = {
                    "state": state.state,
                    "unavailable_seconds": int(age),
                }
                # Intermittent = the entity has gone in and out repeatedly over
                # the last day (too slow for the short-window flap detector).
                # That's a range / weak-signal issue, not a dead device —
                # flag it so it's characterised (and framed) differently. This
                # in-memory count catches flips seen this session; restarts
                # clear it, so _enrich_intermittent_from_history below rebuilds
                # it from the recorder (persisted).
                if track is not None:
                    cutoff = now_ts - _INTERMITTENT_WINDOW_SECS
                    flaps = sum(1 for t in track.transitions if t >= cutoff)
                    if flaps >= _INTERMITTENT_MIN_TRANSITIONS:
                        evidence["intermittent"] = True
                        evidence["flaps_24h"] = flaps
                offenders[state.entity_id] = evidence
        self._keep_vanished_offline(offenders, prior_first_seen, ent_reg, now_ts)
        offenders = self._only_wholly_down_devices(offenders, ent_reg)
        offenders = await self._enrich_from_history(offenders)
        await self._reconcile(
            HEALTH_KIND_UNAVAILABLE,
            "entity",
            offenders,
            HEALTH_SEVERITY_WARNING,
            area_of,
        )

    def _keep_vanished_offline(
        self,
        offenders: dict[str, dict[str, Any]],
        prior_first_seen: dict[str, float],
        ent_reg: er.EntityRegistry,
        now_ts: float,
    ) -> None:
        """Keep an already-flagged entity flagged when its integration briefly
        drops it from the state machine.

        Some integrations remove and re-add an offline device's entities on a
        rediscovery cycle — Sonos does this for a speaker that's been powered off
        a while. Between the remove and the re-add the entity has no state, so
        the scan above can't see it and would resolve the signal: the device
        flickers off the Health page every other scan. The registry entry is
        stable across that churn, so while the entity stays registered (enabled,
        its config entry still loaded) and hasn't returned with a real value,
        preserve its existing signal. Scoped to entities we ALREADY flagged
        (``prior_first_seen``), so a transient startup absence can't manufacture
        a brand-new false positive.
        """
        for eid, seen_ts in prior_first_seen.items():
            if eid in offenders or eid in self._excluded:
                continue
            # ``unknown`` (in _UNREACHABLE_STATES) does NOT count as recovery: it
            # shows up during the same remove/re-add churn and doesn't prove the
            # device is back, so the signal is kept until a genuinely reachable
            # state appears (see ``_is_preserved_offline`` for the full rule).
            if not _is_preserved_offline(self._hass, ent_reg, eid):
                continue
            state = self._hass.states.get(eid)
            offenders[eid] = {
                "state": state.state if state is not None else "unavailable",
                "unavailable_seconds": int(now_ts - seen_ts),
            }

    def _only_wholly_down_devices(
        self, offenders: dict[str, dict[str, Any]], ent_reg: er.EntityRegistry
    ) -> dict[str, dict[str, Any]]:
        """Suppress partial-device unavailability.

        Most multi-entity devices carry some entity that sits ``unavailable`` by
        design — an EV charger's charging entities when nothing's plugged in, a
        media player's tone controls when idle, a diagnostic that only populates
        under load. Flagging each of those as a fault is noise. When a device is
        genuinely unreachable, none of its entities still report a live value, so
        we only keep an unavailable entity when its whole device is down (see
        ``_device_unreachable`` — an entity in a real state, not just a non-\
        ``unavailable`` one, is what proves the device is responding).

        Kept regardless: entities with no device (nothing to compare against),
        and single-entity devices (their one entity being down IS the device
        being down). Only devices where some entity still reports a live value
        are treated as partial and dropped.
        """
        if not offenders:
            return offenders
        kept: dict[str, dict[str, Any]] = {}
        whole: dict[str, bool] = {}
        for entity_id, evidence in offenders.items():
            entry = ent_reg.async_get(entity_id)
            device_id = entry.device_id if entry else None
            if device_id is None:
                kept[entity_id] = evidence
                continue
            down = whole.get(device_id)
            if down is None:
                down = _device_unreachable(self._hass, ent_reg, device_id)
                whole[device_id] = down
            if down:
                kept[entity_id] = evidence
        return kept

    async def _enrich_from_history(
        self, offenders: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Mark offenders intermittent when they flipped availability often in
        the last day (a range/weak-signal issue), using persisted recorder
        history so it survives restarts (in-memory tracking is empty after one).

        Does NOT suppress anything: a device that's been unavailable is reported
        regardless of how long it's been off. (We used to drop "unavailable by
        design" entities here, but that also hid genuinely dead devices that had
        simply been off a long time — a false negative. Genuine by-design
        sensors are muted with the exclude label instead.)

        Best-effort: no recorder → offenders pass through unchanged.
        """
        if not offenders:
            return offenders
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import get_significant_states
        except ImportError:
            return offenders
        now_ts = datetime.now(UTC).timestamp()
        start = datetime.fromtimestamp(now_ts - _BYDESIGN_WINDOW_SECS, UTC)
        ids = list(offenders)

        def _query() -> dict[str, list[Any]]:
            return get_significant_states(
                self._hass,
                start,
                None,
                ids,
                significant_changes_only=True,
                no_attributes=True,
            )

        try:
            history = await get_instance(self._hass).async_add_executor_job(_query)
        except Exception:  # noqa: BLE001 — history is best-effort
            _LOGGER.debug("Recorder unavailable for availability history", exc_info=True)
            return offenders

        intermittent_cutoff = now_ts - _INTERMITTENT_WINDOW_SECS
        for eid, evidence in offenders.items():
            rows = history.get(eid) or []
            if not evidence.get("intermittent"):
                recent = [r for r in rows if (_row_ts(r) or 0.0) >= intermittent_cutoff]
                flaps = _count_availability_transitions(recent)
                if flaps >= _INTERMITTENT_MIN_TRANSITIONS:
                    evidence["intermittent"] = True
                    evidence["flaps_24h"] = flaps
        return offenders

    async def _detect_silent(self, now_ts: float, area_of: Any) -> None:
        offenders: dict[str, dict[str, Any]] = {}
        for entity_id, track in self._tracks.items():
            if entity_id in self._excluded:
                continue
            if (
                track.ema_interval is None
                or track.interval_samples < _MIN_INTERVALS_FOR_CADENCE
                or track.last_update_ts is None
            ):
                continue
            state = self._hass.states.get(entity_id)
            if state is None or not _is_available(state.state):
                continue  # unavailable is a separate, stronger signal
            if not _is_periodic_reporter(state):
                # Event-driven entity (light, switch, lock, motion): its update
                # intervals are event gaps, not a reporting cadence, so a quiet
                # spell is normal. Only periodic reporters can be "silent".
                continue
            age = now_ts - track.last_update_ts
            threshold = max(track.ema_interval * HEALTH_SILENT_MULTIPLIER, HEALTH_SILENT_MIN_SECS)
            if age >= threshold:
                offenders[entity_id] = {
                    "silent_seconds": int(age),
                    "expected_interval_seconds": int(track.ema_interval),
                }
        await self._reconcile(
            HEALTH_KIND_SILENT,
            "entity",
            offenders,
            HEALTH_SEVERITY_WARNING,
            area_of,
        )

    async def _detect_battery(self, area_of: Any) -> None:
        # Intentionally NOT gated on `_in_scope` (unlike the other detectors): a
        # low battery is a real, actionable fault on ANY device, including ones
        # in domains we don't otherwise track (a valve, a siren, a lawn mower),
        # where the battery reading is the only health signal available. Do not
        # "align" this with the other detectors by adding an `_in_scope` guard —
        # that would silently drop low-battery alerts for those devices.
        ent_reg = er.async_get(self._hass)
        offenders: dict[str, dict[str, Any]] = {}
        for state in self._hass.states.async_all():
            if state.entity_id in self._excluded:
                continue
            if _is_transient(state.entity_id, ent_reg):
                continue
            if not _is_battery_entity(state):
                continue
            # A binary_sensor with device_class battery reports on == low (HA's
            # BinarySensorDeviceClass.BATTERY), not a percentage. Flag it with
            # no numeric level so downstream renders "battery is low", not "0%".
            if state.attributes.get("device_class") == "battery" and state.entity_id.startswith(
                "binary_sensor."
            ):
                if state.state.casefold() == "on":
                    offenders[state.entity_id] = {"battery_low": True}
                continue
            level = _battery_level(state)
            if level is not None and level <= HEALTH_BATTERY_LOW_PCT:
                offenders[state.entity_id] = {"battery_level": level}
        await self._reconcile(
            HEALTH_KIND_BATTERY_LOW,
            "entity",
            offenders,
            HEALTH_SEVERITY_WARNING,
            area_of,
        )

    async def _detect_integration_health(self) -> None:
        offenders: dict[str, dict[str, Any]] = {}

        for entry in self._hass.config_entries.async_entries():
            if entry.domain == DOMAIN:
                continue
            if entry.state in (ConfigEntryState.SETUP_ERROR, ConfigEntryState.SETUP_RETRY):
                evidence: dict[str, Any] = {
                    "source": "config_entry",
                    "state": str(entry.state),
                    "title": entry.title,
                }
                # entry.reason is the human-readable failure HA recorded when
                # setup raised (e.g. "Timeout connecting to 192.168.1.5") — the
                # single field someone triaging this downstream actually needs.
                # It's None when the integration reported the failure through a
                # translation key only; we resolve that below.
                if entry.reason:
                    evidence["reason"] = entry.reason
                if entry.error_reason_translation_key:
                    evidence["error_translation_key"] = entry.error_reason_translation_key
                    if entry.error_reason_translation_placeholders:
                        evidence["error_translation_placeholders"] = dict(
                            entry.error_reason_translation_placeholders
                        )
                offenders[entry.domain] = evidence

        issue_reg = ir.async_get(self._hass)
        # Rank of the repair issue currently chosen per target domain, so a
        # later issue only wins when it's strictly more severe (first-seen wins
        # on a tie — stable within a scan).
        issue_rank: dict[str, int] = {}
        for issue in issue_reg.issues.values():
            # A user who ignores an issue in the Repairs UI leaves active=True
            # but records the choice in dismissed_version — respect it, or we'd
            # re-raise a critical signal for it on every scan.
            if not issue.active or issue.dismissed_version is not None:
                continue
            # Issues raised on behalf of another integration set `domain` to the
            # creator and `issue_domain` to the affected integration — blame and
            # deep-link the affected one.
            target_domain = issue.issue_domain or issue.domain
            if target_domain == DOMAIN:
                continue
            if issue.severity not in (ir.IssueSeverity.CRITICAL, ir.IssueSeverity.ERROR):
                continue

            # A config-entry setup failure is the primary, more actionable
            # problem — never let a repair issue overwrite it or graft its
            # fields onto that evidence.
            existing = offenders.get(target_domain)
            if existing is not None and existing.get("source") == "config_entry":
                continue

            # Build this issue's evidence as a self-contained unit: every
            # issue-specific field is set here or absent, so two different issues
            # on the same integration can never blend (issue A's translation key
            # rendered under issue B's id).
            issue_evidence: dict[str, Any] = {
                "source": "repair_issue",
                "issue_id": issue.issue_id,
                "issue_severity": str(issue.severity),
            }
            if issue.translation_key:
                issue_evidence["issue_translation_key"] = issue.translation_key
                # The issue's TEXT lives in the CREATOR's catalog
                # (``issue.domain``), which differs from the affected
                # ``target_domain`` for on-behalf-of issues (e.g. HA raises
                # ``config_entry_reauth`` under domain="homeassistant"). Keep it
                # so the resolver looks up the right component.
                issue_evidence["issue_creator_domain"] = issue.domain
            if issue.translation_placeholders:
                issue_evidence["issue_translation_placeholders"] = dict(
                    issue.translation_placeholders
                )
            if issue.breaks_in_ha_version:
                issue_evidence["breaks_in_ha_version"] = issue.breaks_in_ha_version
            if issue.learn_more_url:
                issue_evidence["learn_more_url"] = issue.learn_more_url

            # One signal per integration → report exactly one issue. When
            # several target the same domain, keep the most severe.
            rank = _issue_severity_rank(issue.severity)
            if rank > issue_rank.get(target_domain, -1):
                offenders[target_domain] = issue_evidence
                issue_rank[target_domain] = rank

        # Turn the raw translation keys into legible English text so the
        # exported evidence stands alone — the Selora OS host / Connect can
        # surface a real message without holding HA's translation catalogs.
        await self._resolve_offender_messages(offenders)

        await self._reconcile(
            HEALTH_KIND_INTEGRATION_ERROR,
            "integration",
            offenders,
            HEALTH_SEVERITY_CRITICAL,
            lambda _t: "",
        )

    async def _resolve_offender_messages(self, offenders: dict[str, dict[str, Any]]) -> None:
        """Fill in human-readable English text for integration-error evidence.

        Config-entry failures and repair issues often carry only a translation
        *key* (``error_translation_key`` / ``issue_translation_key``). We look
        each up in HA's ``exceptions`` / ``issues`` catalogs and store the
        rendered message alongside the key. Best-effort: an uncached or missing
        string simply leaves the raw key in place, and any failure is swallowed
        so a translation hiccup never blocks health detection.
        """
        if not offenders:
            return
        # Config-entry error text lives under the entry's own domain (the
        # offender key); repair-issue text lives under the CREATOR domain, which
        # may differ from the affected target — load both sets.
        exc_domains = set(offenders)
        issue_domains = {
            ev.get("issue_creator_domain", domain)
            for domain, ev in offenders.items()
            if ev.get("issue_translation_key")
        }
        from homeassistant.helpers import translation  # noqa: PLC0415 — one call site

        try:
            exc_tr = await translation.async_get_translations(
                self._hass, "en", "exceptions", exc_domains
            )
            issue_tr = (
                await translation.async_get_translations(self._hass, "en", "issues", issue_domains)
                if issue_domains
                else {}
            )
        except Exception:  # noqa: BLE001 — message resolution is best-effort
            _LOGGER.debug("Could not load integration-error translations", exc_info=True)
            return

        for domain, ev in offenders.items():
            # Config-entry error whose reason is missing OR is just the raw
            # translation key (HA stores the key verbatim when the exception
            # catalog wasn't cached at raise time) — render it to real text.
            if key := ev.get("error_translation_key"):
                reason = ev.get("reason")
                if not reason or reason == key:
                    msg = exc_tr.get(f"component.{domain}.exceptions.{key}.message")
                    if msg:
                        ev["reason"] = _apply_placeholders(
                            msg, ev.get("error_translation_placeholders")
                        )
            # Repair issue — render its title/description from the creator's catalog.
            if key := ev.get("issue_translation_key"):
                issue_domain = ev.get("issue_creator_domain", domain)
                ph = ev.get("issue_translation_placeholders")
                if title := issue_tr.get(f"component.{issue_domain}.issues.{key}.title"):
                    ev["issue_title"] = _apply_placeholders(title, ph)
                if desc := issue_tr.get(f"component.{issue_domain}.issues.{key}.description"):
                    ev["issue_description"] = _apply_placeholders(desc, ph)

    async def _reconcile(
        self,
        kind: str,
        target_kind: str,
        offenders: dict[str, dict[str, Any]],
        severity: str,
        area_of: Any,
    ) -> None:
        """Upsert offenders as active signals; resolve cleared ones.

        Writes are deferred (save=False) — async_scan persists them in one save.
        """
        ent_reg = er.async_get(self._hass) if target_kind == "entity" else None
        for target, evidence in offenders.items():
            device_id: str | None = None
            if ent_reg is not None:
                entry = ent_reg.async_get(target)
                device_id = entry.device_id if entry else None
            await self._store.record_signal(
                kind=kind,
                target=target,
                target_kind=target_kind,
                severity=severity,
                evidence=evidence,
                area_name=area_of(target) if target_kind == "entity" else "",
                device_id=device_id,
                save=False,
            )
        active = await self._store.get_signals(status="active", kind=kind)
        for sig in active:
            if sig["target"] not in offenders:
                await self._store.resolve_signal(kind, sig["target"], save=False)


# ── Module helpers ────────────────────────────────────────────────────


def _issue_severity_rank(severity: ir.IssueSeverity | None) -> int:
    """Order repair-issue severities so the most severe issue is chosen when
    several target the same integration (CRITICAL > ERROR > anything else)."""
    if severity == ir.IssueSeverity.CRITICAL:
        return 2
    if severity == ir.IssueSeverity.ERROR:
        return 1
    return 0


def _apply_placeholders(text: str, placeholders: dict[str, Any] | None) -> str:
    """Substitute ``{name}`` placeholders in a translated string.

    HA translation messages interpolate ``.format(**placeholders)``. A missing
    key or stray brace would raise — swallow that and return the raw text so a
    malformed catalog entry degrades to the un-substituted string rather than
    dropping the message entirely.
    """
    if not placeholders:
        return text
    try:
        return text.format(**placeholders)
    except (KeyError, IndexError, ValueError):
        return text


def _iso_to_ts(value: Any) -> float | None:
    """Parse an ISO-8601 string to a POSIX timestamp, or None if unparseable."""
    if not isinstance(value, str):
        return None
    with suppress(ValueError):
        return datetime.fromisoformat(value).timestamp()
    return None


def _in_scope(entity_id: str) -> bool:
    domain = entity_id.split(".")[0] if "." in entity_id else ""
    return domain in COLLECTOR_DOMAINS


# Integrations whose entities are inherently transient — BLE beacons / presence
# advertisements that HA materializes per detected device. They go "unavailable"
# simply when out of range, are often not even the user's devices (a passing
# car's TPMS, a neighbour's tag), and must never be reported as an offline
# device-health issue.
_TRANSIENT_INTEGRATIONS = frozenset(
    {
        "ibeacon",
        "private_ble_device",
        "bluetooth_le_tracker",
        "ble_monitor",
        "bermuda",
    }
)


_PRESENCE_DOMAINS = frozenset({"device_tracker", "person"})


def _is_transient(entity_id: str, ent_reg: er.EntityRegistry) -> bool:
    """Fast-path exclusion for entities whose absence is normal, not a device
    fault: presence entities (``device_tracker`` / ``person`` — away or out of
    range is expected) and entities from known BLE-beacon integrations (see
    ``_TRANSIENT_INTEGRATIONS``). Immediate and restart-proof; the availability
    ratio gate below catches the general (unlisted) case after observation."""
    if entity_id.split(".")[0] in _PRESENCE_DOMAINS:
        return True
    entry = ent_reg.async_get(entity_id)
    return bool(entry and entry.platform in _TRANSIENT_INTEGRATIONS)


def _is_preserved_offline(hass: HomeAssistant, ent_reg: er.EntityRegistry, entity_id: str) -> bool:
    """True when an already-flagged offline entity should keep its signal while
    its integration briefly drops the state object (Sonos rediscovery churn):
    the registry entry is still present and enabled, the entity hasn't returned
    with a real live value (no state, or an unreachable/restored one), and its
    config entry is still loaded. Shared by :meth:`HealthMonitor.
    _keep_vanished_offline` and the audit's fleet eligibility so both agree on
    what a "preserved offline" device is — a genuinely removed or disabled entity
    does NOT qualify, so its stale signal can't masquerade as a live outage."""
    entry = ent_reg.async_get(entity_id)
    if entry is None or entry.disabled_by is not None:
        return False
    state = hass.states.get(entity_id)
    if (
        state is not None
        and state.state.casefold() not in _UNREACHABLE_STATES
        and not state.attributes.get("restored")
    ):
        return False  # back with a real, live value
    ce_id = entry.config_entry_id
    ce = hass.config_entries.async_get_entry(ce_id) if ce_id else None
    # Kept only while the integration is still loaded — an unloaded/failed config
    # entry is an integration problem, not a device outage.
    return ce is None or ce.state is ConfigEntryState.LOADED


def _device_unreachable(hass: HomeAssistant, ent_reg: er.EntityRegistry, device_id: str) -> bool:
    """True when a device's primary entities are all unreachable.

    A device's *primary* entities (``entity_category`` unset — the media player,
    the light, the lock: what the device actually does) go unavailable when it
    drops off the network. Its *config* and *diagnostic* entities often don't:
    HA keeps them at their last cached value, so an offline Sonos still shows its
    Bass/Treble/Balance and toggle states even while its media_player is
    ``unavailable``. Counting those auxiliary entities masks a genuinely-offline
    device (one unavailable media_player vs. seven config entities that still
    read as set), so reachability is judged by the primary entities alone.

    A device with no primary entity falls back to "every entity unreachable".
    Disabled entities are ignored. "Unreachable" is ``unavailable`` OR
    ``unknown`` — neither proves the device responds, unlike a real reading
    (idle/paused/42%).
    """
    primary_total = 0
    primary_unreachable = 0
    any_total = 0
    any_reachable = 0
    for entry in er.async_entries_for_device(ent_reg, device_id, include_disabled_entities=False):
        state = hass.states.get(entry.entity_id)
        if state is None:
            # No state object: the integration isn't currently providing the
            # entity (removed / "no longer provided"). That doesn't prove
            # reachability any more than ``unavailable`` does.
            unreachable = True
        else:
            unreachable = bool(state.attributes.get("restored")) or (
                state.state.casefold() in _UNREACHABLE_STATES
            )
        any_total += 1
        if not unreachable:
            any_reachable += 1
        if entry.entity_category is None:
            primary_total += 1
            if unreachable:
                primary_unreachable += 1
    if primary_total >= 1:
        return primary_unreachable == primary_total
    return any_total >= 1 and any_reachable == 0


def _row_ts(state: Any) -> float | None:
    """Timestamp of a recorder history row (State object or minimal dict)."""
    raw = getattr(state, "last_changed", None)
    if raw is None and isinstance(state, dict):
        raw = state.get("last_changed")
    if raw is None:
        return None
    return raw.timestamp() if hasattr(raw, "timestamp") else None


def _count_availability_transitions(states: list[Any]) -> int:
    """Count available<->unavailable flips across a recorder history sequence.

    Accepts ``State`` objects or minimal-response dicts (both expose ``state``).
    """
    count = 0
    prev: bool | None = None
    for st in states:
        raw = getattr(st, "state", None)
        if raw is None and isinstance(st, dict):
            raw = st.get("state")
        if raw is None:
            continue
        available = _is_available(str(raw))
        if prev is not None and available != prev:
            count += 1
        prev = available
    return count


def _account_availability(track: _EntityTrack, now: float) -> None:
    """Bank the time elapsed since the last accounting into the availability
    totals, attributed to the entity's current availability state."""
    if track.last_avail_ts is None:
        track.last_avail_ts = now
        return
    dt = now - track.last_avail_ts
    if dt <= 0:
        return
    track.observed_secs += dt
    if track.last_available:
        track.available_secs += dt
    track.last_avail_ts = now


def _usually_unavailable(track: _EntityTrack, now: float) -> bool:
    """True when the entity has been available for less than the transient
    ratio of its observed lifetime — a usually-absent device whose
    unavailability is its normal state. Needs a minimum observation window so a
    device that only just went offline (ratio still high) is still flagged."""
    _account_availability(track, now)
    if track.observed_secs < _AVAIL_MIN_OBSERVED_SECS:
        return False
    # Never suppress an entity we've only ever observed unavailable. With zero
    # banked available time the in-memory ratio is 0 for two indistinguishable
    # cases: a genuinely usually-absent device, and a real device that was
    # already unavailable when HA started (``_seed_from_state_machine`` seeds it
    # as unavailable, so it never accrues available time) and never recovered.
    # In-memory data can't tell them apart — defer to the recorder-based
    # by-design filter in ``_enrich_from_history``, which has persisted history.
    if track.available_secs <= 0:
        return False
    return (track.available_secs / track.observed_secs) < _AVAIL_TRANSIENT_RATIO


def _battery_level(state: State) -> int | None:
    """Return a 0-100 battery level for a battery entity/attribute, else None."""
    attrs = state.attributes
    if attrs.get("device_class") == "battery":
        with suppress(ValueError, TypeError):
            return int(float(state.state))
    raw = attrs.get("battery_level")
    if raw is not None:
        with suppress(ValueError, TypeError):
            return int(float(raw))
    return None


def _is_battery_entity(state: State) -> bool:
    """True when :meth:`HealthMonitor._detect_battery` treats this entity as a
    battery reading it could flag — a binary battery sensor (``device_class``
    battery, on == low) or any entity exposing a numeric level. Intentionally NOT
    scope-gated, matching the detector: a low battery is actionable on any device
    (a valve, a siren, a mower) even in a domain we don't otherwise track."""
    if state.attributes.get("device_class") == "battery" and state.entity_id.startswith(
        "binary_sensor."
    ):
        return True
    return _battery_level(state) is not None


def _resolve_area(
    entity_id: str,
    ent_reg: er.EntityRegistry,
    dev_reg: dr.DeviceRegistry,
    area_reg: ar.AreaRegistry,
) -> str:
    ent = ent_reg.async_get(entity_id)
    if ent is None:
        return ""
    area_id = ent.area_id
    if area_id is None and ent.device_id:
        device = dev_reg.async_get(ent.device_id)
        if device is not None:
            area_id = device.area_id
    if area_id is None:
        return ""
    area = area_reg.async_get_area(area_id)
    return area.name if area else ""
