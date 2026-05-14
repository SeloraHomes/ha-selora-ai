"""Persistent per-(provider, model) LLM usage aggregation.

The in-memory ring buffer in ``llm_client`` holds the last few hundred
events for the panel's "Where tokens go" detail view, but it resets on
HA restart. This store keeps daily rollups grouped by (provider, model)
so the panel can render accurate "Today / Last 7 days / This month"
totals broken down by backend, even across restarts.

Selora Cloud is excluded upstream (``_flush_usage`` short-circuits), so
no entries for ``selora_cloud`` ever reach this store.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any, TypedDict

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .types import LLMUsageEvent

_LOGGER = logging.getLogger(__name__)

_STORAGE_VERSION = 1
_STORAGE_KEY = f"{DOMAIN}.usage"

# Daily buckets older than this are pruned on each save. 30 covers the
# "Last 30 days" period card; pruning also preserves anything from the
# current calendar month so the "This month" card stays accurate at the
# end of long months (the start of a 31-day month would otherwise fall
# outside a strict 30-day window).
USAGE_RETENTION_DAYS = 30


class UsageBucket(TypedDict):
    """One (provider, model) accumulator for a single calendar day."""

    input: int
    output: int
    calls: int
    cost_usd: float


class UsageData(TypedDict):
    """Top-level persisted shape."""

    version: int
    days: dict[str, dict[str, dict[str, UsageBucket]]]


def _empty_bucket() -> UsageBucket:
    return {"input": 0, "output": 0, "calls": 0, "cost_usd": 0.0}


def _today_key(now: datetime | None = None) -> str:
    return (now or dt_util.now()).date().isoformat()


def _day_key_for_event(event: LLMUsageEvent) -> str:
    """Return the local calendar day to bucket ``event`` into.

    Uses the event's own ``timestamp`` (set when the LLM call completed)
    rather than the time the persistence task runs, so calls finishing
    just before midnight aren't pushed into the next day if the
    fire-and-forget save is delayed. Falls back to "now" if the
    timestamp is missing or unparseable.
    """
    ts = event.get("timestamp")
    if ts:
        try:
            parsed = datetime.fromisoformat(ts)
        except TypeError, ValueError:
            parsed = None
        if parsed is not None:
            return dt_util.as_local(parsed).date().isoformat()
    return _today_key()


class UsageStore:
    """Daily-rollup store keyed by (date, provider, model)."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store[UsageData] = Store(hass, version=_STORAGE_VERSION, key=_STORAGE_KEY)
        self._data: UsageData | None = None
        # Serialises mutate + save. Multiple flushed events can race into
        # ``record`` simultaneously; without the lock, two writers could
        # both read the in-memory dict, mutate, and overwrite each other
        # on save.
        self._lock = asyncio.Lock()
        # Shared one-shot task for the initial load. Both readers and
        # writers await this same future so we never run ``async_load``
        # twice in parallel — otherwise a slow read could complete after
        # a write and overwrite ``self._data`` with stale contents,
        # dropping the just-recorded event on the next save.
        self._load_task: asyncio.Task[None] | None = None

    async def _ensure_loaded(self) -> None:
        """Load from disk on first use. Safe to call from any coroutine."""
        if self._data is not None:
            return
        if self._load_task is None:
            self._load_task = asyncio.create_task(self._do_load())
        await self._load_task

    async def _do_load(self) -> None:
        raw = await self._store.async_load()
        if isinstance(raw, dict) and "days" in raw:
            self._data = {"version": _STORAGE_VERSION, "days": raw["days"]}
        else:
            self._data = {"version": _STORAGE_VERSION, "days": {}}

    def _prune(self) -> None:
        """Drop day buckets outside both the rolling retention window
        and the current calendar month.

        Keeping the whole current calendar month ensures the "This month"
        period card stays accurate in 31-day months and on the first day
        of a month, where a strict 30-day cutoff would otherwise discard
        the early days of the month.
        """
        if self._data is None:
            return
        today = dt_util.now().date()
        rolling_cutoff = today - timedelta(days=USAGE_RETENTION_DAYS - 1)
        month_start = today.replace(day=1)
        cutoff = min(rolling_cutoff, month_start).isoformat()
        stale = [d for d in self._data["days"] if d < cutoff]
        for d in stale:
            del self._data["days"][d]

    async def record(self, event: LLMUsageEvent) -> None:
        """Append one usage event to today's bucket and persist."""
        provider = event.get("provider")
        model = event.get("model") or ""
        if not provider:
            return
        async with self._lock:
            await self._ensure_loaded()
            if self._data is None:
                return
            day = _day_key_for_event(event)
            day_bucket = self._data["days"].setdefault(day, {})
            provider_bucket = day_bucket.setdefault(provider, {})
            bucket = provider_bucket.setdefault(model, _empty_bucket())
            bucket["input"] += int(event.get("input_tokens", 0))
            bucket["output"] += int(event.get("output_tokens", 0))
            bucket["calls"] += 1
            bucket["cost_usd"] = round(bucket["cost_usd"] + float(event.get("cost_usd", 0.0)), 6)
            self._prune()
            await self._store.async_save(self._data)

    def _iter_days(self, range_key: str) -> list[str]:
        """Return the list of day keys covered by ``range_key``."""
        today = dt_util.now().date()
        if range_key == "today":
            start = today
        elif range_key == "7d":
            start = today - timedelta(days=6)
        elif range_key == "30d":
            start = today - timedelta(days=29)
        elif range_key == "month":
            # Calendar month, not the rolling 30-day window — matches
            # what the UI labels "This month".
            start = today.replace(day=1)
        else:  # "all"
            start = date.min
        if self._data is None:
            return []
        return [d for d in self._data["days"] if d >= start.isoformat()]

    async def get_breakdown(self, range_key: str = "30d") -> dict[str, dict[str, UsageBucket]]:
        """Return totals grouped by ``{provider: {model: bucket}}`` for the range."""
        await self._ensure_loaded()
        if self._data is None:
            return {}
        result: dict[str, dict[str, UsageBucket]] = {}
        for day in self._iter_days(range_key):
            for provider, models in self._data["days"][day].items():
                provider_result = result.setdefault(provider, {})
                for model, bucket in models.items():
                    acc = provider_result.setdefault(model, _empty_bucket())
                    acc["input"] += bucket["input"]
                    acc["output"] += bucket["output"]
                    acc["calls"] += bucket["calls"]
                    acc["cost_usd"] = round(acc["cost_usd"] + bucket["cost_usd"], 6)
        return result

    async def get_totals(
        self,
        range_key: str = "30d",
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> UsageBucket:
        """Return a flat sum for the range, optionally filtered by provider/model."""
        await self._ensure_loaded()
        if self._data is None:
            return _empty_bucket()
        totals = _empty_bucket()
        for day in self._iter_days(range_key):
            for prov, models in self._data["days"][day].items():
                if provider is not None and prov != provider:
                    continue
                for mdl, bucket in models.items():
                    # ``model=""`` is a legitimate filter (the no-model
                    # bucket used by providers like selora_local); only
                    # ``None`` means "any model".
                    if model is not None and mdl != model:
                        continue
                    totals["input"] += bucket["input"]
                    totals["output"] += bucket["output"]
                    totals["calls"] += bucket["calls"]
                    totals["cost_usd"] = round(totals["cost_usd"] + bucket["cost_usd"], 6)
        return totals

    async def get_periods(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, UsageBucket]:
        """Return totals for the panel's three period cards."""
        return {
            "today": await self.get_totals("today", provider=provider, model=model),
            "7d": await self.get_totals("7d", provider=provider, model=model),
            "month": await self.get_totals("month", provider=provider, model=model),
        }

    async def reset(self) -> None:
        """Drop all stored buckets (used by the panel's reset action)."""
        async with self._lock:
            await self._ensure_loaded()
            self._data = {"version": _STORAGE_VERSION, "days": {}}
            await self._store.async_save(self._data)


def get_usage_store(hass: HomeAssistant) -> UsageStore:
    """Return the shared UsageStore, creating it lazily."""
    bucket = hass.data.setdefault(DOMAIN, {})
    store = bucket.get("_usage_store")
    if store is None:
        store = UsageStore(hass)
        bucket["_usage_store"] = store
    return store


def _as_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Return ``data`` as a plain dict for JSON-safe websocket transport."""
    return {k: dict(v) if isinstance(v, dict) else v for k, v in data.items()}
