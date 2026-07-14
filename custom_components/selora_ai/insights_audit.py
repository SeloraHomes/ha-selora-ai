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

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval

if TYPE_CHECKING:
    from .health_store import HealthStore

_LOGGER = logging.getLogger(__name__)

# Delay the first run after startup so registries/states are populated and we
# don't compete with the heavier setup work.
_INITIAL_DELAY_SECONDS = 180
_DEFAULT_INTERVAL_HOURS = 24


class AuditRunner:
    """Runs and caches the deterministic home health checks."""

    def __init__(self, hass: HomeAssistant, health_store: HealthStore) -> None:
        self._hass = hass
        self._health_store = health_store
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._unsub_initial: CALLBACK_TYPE | None = None
        # Every in-flight audit task — the delayed initial run AND each periodic
        # tick. Tracked so async_stop can cancel/drain them on unload: an audit
        # that outlives its entry would finish holding a now-stale HealthStore
        # instance and save the whole store document over the replacement
        # entry's shared store (clobbering its signals / scan metadata / audit).
        self._tasks: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()

    async def async_start(self, interval_hours: int = _DEFAULT_INTERVAL_HOURS) -> None:
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
                score_from_severities,
            )

            results = await async_run_checks(self._hass)
            findings = flatten_findings(results)
            score = score_from_severities(f.get("severity", "info") for f in findings)
            return await self._store_result(
                _record(
                    status="ok",
                    recommendations=findings,
                    checks=results,
                    score=score,
                    band=band_for(score),
                )
            )

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
        "quick_actions": quick_actions or [],
        "fingerprint": fingerprint,
        "error": error,
        "generated_at": datetime.now(UTC).isoformat(),
    }
