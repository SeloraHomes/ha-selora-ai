"""Tests for the persistent per-(provider, model) LLM usage store."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.selora_ai.usage_store import (
    USAGE_RETENTION_DAYS,
    UsageStore,
    get_usage_store,
)


def _event(
    *,
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cost_usd: float = 0.001,
    kind: str = "chat",
    timestamp: str | None = None,
) -> dict[str, Any]:
    # Default to "now" so tests that exercise the ``today`` range stay
    # correct on any wall-clock date. Tests that care about specific
    # day-bucketing behaviour pass an explicit timestamp.
    from homeassistant.util import dt as dt_util

    if timestamp is None:
        timestamp = dt_util.utcnow().isoformat()
    return {
        "timestamp": timestamp,
        "kind": kind,
        "provider": provider,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
    }


@pytest.fixture
def store(hass) -> UsageStore:
    """Return a fresh in-memory UsageStore (Store I/O is mocked elsewhere)."""
    store = UsageStore(hass)
    # Replace HA's real Store with an in-memory stand-in.
    store._store.async_load = AsyncMock(return_value=None)
    store._store.async_save = AsyncMock()
    return store


class TestRecordAndBreakdown:
    async def test_record_creates_today_bucket(self, store) -> None:
        await store.record(_event(input_tokens=10, output_tokens=5, cost_usd=0.0002))

        breakdown = await store.get_breakdown("today")
        assert breakdown == {
            "anthropic": {
                "claude-sonnet-4-6": {
                    "input": 10,
                    "output": 5,
                    "calls": 1,
                    "cost_usd": 0.0002,
                }
            }
        }

    async def test_record_accumulates_same_provider_model(self, store) -> None:
        await store.record(_event(input_tokens=10, output_tokens=5, cost_usd=0.001))
        await store.record(_event(input_tokens=20, output_tokens=15, cost_usd=0.002))

        bucket = (await store.get_breakdown("today"))["anthropic"]["claude-sonnet-4-6"]
        assert bucket["input"] == 30
        assert bucket["output"] == 20
        assert bucket["calls"] == 2
        assert bucket["cost_usd"] == pytest.approx(0.003)

    async def test_record_separates_providers(self, store) -> None:
        await store.record(_event(provider="anthropic", model="claude-sonnet-4-6"))
        await store.record(_event(provider="gemini", model="gemini-2.5-flash"))

        breakdown = await store.get_breakdown("today")
        assert set(breakdown) == {"anthropic", "gemini"}
        assert "claude-sonnet-4-6" in breakdown["anthropic"]
        assert "gemini-2.5-flash" in breakdown["gemini"]

    async def test_record_skips_event_without_provider(self, store) -> None:
        bad_event = _event()
        del bad_event["provider"]

        await store.record(bad_event)  # type: ignore[arg-type]

        assert await store.get_breakdown("today") == {}


class TestTotalsAndFiltering:
    async def test_totals_sum_across_providers(self, store) -> None:
        await store.record(
            _event(provider="anthropic", input_tokens=100, output_tokens=50, cost_usd=0.001)
        )
        await store.record(
            _event(provider="gemini", input_tokens=200, output_tokens=75, cost_usd=0.0005)
        )

        totals = await store.get_totals("today")
        assert totals["input"] == 300
        assert totals["output"] == 125
        assert totals["calls"] == 2
        assert totals["cost_usd"] == pytest.approx(0.0015)

    async def test_totals_filter_by_provider(self, store) -> None:
        await store.record(_event(provider="anthropic", input_tokens=100, cost_usd=0.01))
        await store.record(_event(provider="gemini", input_tokens=200, cost_usd=0.005))

        anth = await store.get_totals("today", provider="anthropic")
        assert anth["input"] == 100
        assert anth["cost_usd"] == pytest.approx(0.01)

    async def test_totals_filter_by_provider_and_model(self, store) -> None:
        await store.record(_event(provider="anthropic", model="claude-sonnet-4-6", input_tokens=10))
        await store.record(_event(provider="anthropic", model="claude-haiku-4-5", input_tokens=20))

        totals = await store.get_totals("today", provider="anthropic", model="claude-haiku-4-5")
        assert totals["input"] == 20
        assert totals["calls"] == 1

    async def test_empty_model_filter_targets_no_model_bucket(self, store) -> None:
        """``model=""`` selects only the no-model bucket (e.g. selora_local)."""
        # selora_local writes with model="" because it has no user-visible
        # model picker. A peer call with a named model under the same
        # provider should NOT leak into the empty-model totals.
        await store.record(_event(provider="selora_local", model="", input_tokens=5))
        await store.record(_event(provider="selora_local", model="phi-3", input_tokens=99))

        only_blank = await store.get_totals(
            "today", provider="selora_local", model=""
        )
        assert only_blank["input"] == 5
        assert only_blank["calls"] == 1

        any_model = await store.get_totals("today", provider="selora_local")
        assert any_model["input"] == 104
        assert any_model["calls"] == 2

    async def test_periods_returns_three_buckets(self, store) -> None:
        await store.record(_event(input_tokens=10, cost_usd=0.001))

        periods = await store.get_periods()
        assert set(periods) == {"today", "7d", "month"}
        assert periods["today"]["input"] == 10
        assert periods["7d"]["input"] == 10
        assert periods["month"]["input"] == 10


class TestRetention:
    async def test_old_days_are_pruned(self, store) -> None:
        # Seed the store with a day older than the retention window.
        from homeassistant.util import dt as dt_util

        old_day = (dt_util.now().date() - timedelta(days=USAGE_RETENTION_DAYS + 5)).isoformat()
        store._data = {
            "version": 1,
            "days": {
                old_day: {
                    "anthropic": {
                        "claude-sonnet-4-6": {
                            "input": 999,
                            "output": 999,
                            "calls": 1,
                            "cost_usd": 0.5,
                        }
                    }
                }
            },
        }

        # A fresh record triggers prune.
        await store.record(_event(input_tokens=1))

        assert old_day not in store._data["days"]

    async def test_reset_clears_all(self, store) -> None:
        await store.record(_event(input_tokens=10))
        await store.reset()
        assert await store.get_breakdown("all") == {}


class TestConcurrency:
    async def test_read_does_not_clobber_concurrent_write(self, hass) -> None:
        """A slow read must not overwrite ``_data`` after a write completed.

        Reproduces the race the reviewer flagged: reader and writer both
        observe ``_data is None`` and both call ``async_load``. Writer
        finishes first (loads empty, mutates, saves). Reader's load then
        resolves with the pre-write contents and — without the shared
        load-task — reassigns ``_data``, wiping the write from memory.
        The next ``record`` would then save from a stale baseline and
        drop the first event.
        """
        store = UsageStore(hass)
        first_call = asyncio.Event()
        release_load = asyncio.Event()
        load_count = 0

        async def coordinated_load():
            nonlocal load_count
            load_count += 1
            first_call.set()
            await release_load.wait()
            return None  # empty backing store

        store._store.async_load = AsyncMock(side_effect=coordinated_load)
        store._store.async_save = AsyncMock()

        # Reader starts first and stalls on async_load.
        reader = asyncio.create_task(store.get_breakdown("today"))
        await first_call.wait()
        # Writer arrives while the reader is still waiting.
        writer = asyncio.create_task(store.record(_event(input_tokens=42)))
        await asyncio.sleep(0)  # let writer register the load awaiter

        release_load.set()
        await asyncio.gather(reader, writer)

        # Exactly one disk load — both callers shared the same task.
        assert load_count == 1
        # And the writer's mutation is still in memory.
        bucket = (await store.get_breakdown("today"))["anthropic"]["claude-sonnet-4-6"]
        assert bucket["input"] == 42
        assert bucket["calls"] == 1

    async def test_concurrent_records_do_not_drop_events(self, hass) -> None:
        """Many records dispatched before first load completes must all land."""
        store = UsageStore(hass)
        # Simulate a slow first load so several record() tasks pile up
        # waiting on it. Without the lock, each would replace ``_data``
        # after its own load and clobber the others' increments.
        load_started = asyncio.Event()
        release_load = asyncio.Event()

        async def slow_load():
            load_started.set()
            await release_load.wait()
            return None  # empty store

        store._store.async_load = AsyncMock(side_effect=slow_load)
        store._store.async_save = AsyncMock()

        # Kick off N parallel records before the first load returns.
        n = 25
        tasks = [
            asyncio.create_task(store.record(_event(input_tokens=1, output_tokens=1)))
            for _ in range(n)
        ]
        await load_started.wait()
        release_load.set()
        await asyncio.gather(*tasks)

        bucket = (await store.get_breakdown("today"))["anthropic"]["claude-sonnet-4-6"]
        assert bucket["calls"] == n
        assert bucket["input"] == n
        assert bucket["output"] == n


class TestEventTimestampBucketing:
    async def test_event_timestamp_determines_day_key(self, store) -> None:
        """A late-night UTC event must land on its own local day, not the next."""
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        from homeassistant.util import dt as dt_util

        # Pick a local datetime two days in the past so it's well clear of
        # any "today" the persistence task might use.
        local_tz = dt_util.DEFAULT_TIME_ZONE
        anchor = (
            dt_util.now()
            .astimezone(local_tz)
            .replace(hour=23, minute=55, second=0, microsecond=0)
            - timedelta(days=2)
        )
        # Convert to UTC ISO — this is what _flush_usage records.
        ts = anchor.astimezone(ZoneInfo("UTC")).isoformat()
        expected_day = anchor.date().isoformat()

        await store.record(_event(input_tokens=7, timestamp=ts))

        # The bucket must be keyed by the event's local day, not today's.
        assert expected_day in store._data["days"]
        bucket = store._data["days"][expected_day]["anthropic"]["claude-sonnet-4-6"]
        assert bucket["input"] == 7
        assert bucket["calls"] == 1

    async def test_missing_timestamp_falls_back_to_today(self, store) -> None:
        from homeassistant.util import dt as dt_util

        event = _event(input_tokens=3)
        del event["timestamp"]
        # Sanity: the fixture default writes a timestamp; we just removed it.
        assert "timestamp" not in event

        await store.record(event)

        today = dt_util.now().date().isoformat()
        assert today in store._data["days"]


class TestMonthBounds:
    async def test_month_range_uses_calendar_month(self, store) -> None:
        """``month`` covers first-of-month → today, not last 30 days."""
        from homeassistant.util import dt as dt_util

        today = dt_util.now().date()
        # Seed a day 35 days ago (definitely in the previous month early
        # in any month, and outside a strict 30-day window everywhere).
        old = (today - timedelta(days=35)).isoformat()
        # And a day inside the current calendar month (yesterday or the
        # 1st, whichever is in-month).
        in_month = max(today.replace(day=1), today - timedelta(days=1)).isoformat()

        store._data = {
            "version": 1,
            "days": {
                old: {
                    "anthropic": {
                        "claude-sonnet-4-6": {
                            "input": 1000,
                            "output": 0,
                            "calls": 1,
                            "cost_usd": 0.0,
                        }
                    }
                },
                in_month: {
                    "anthropic": {
                        "claude-sonnet-4-6": {
                            "input": 7,
                            "output": 0,
                            "calls": 1,
                            "cost_usd": 0.0,
                        }
                    }
                },
            },
        }

        month_totals = await store.get_totals("month")
        # Only the in-month day should contribute.
        assert month_totals["input"] == 7
        assert month_totals["calls"] == 1

    async def test_prune_keeps_current_month_days(self, store) -> None:
        """Pruning must not drop early-month days even past the 30-day window."""
        from homeassistant.util import dt as dt_util

        today = dt_util.now().date()
        # Only meaningful when we're far enough into the month that
        # day-1 is outside the rolling 30-day window. Skip otherwise.
        days_into_month = (today - today.replace(day=1)).days
        if days_into_month < USAGE_RETENTION_DAYS:
            pytest.skip("not deep enough into month to trigger the edge case")

        first_of_month = today.replace(day=1).isoformat()
        store._data = {
            "version": 1,
            "days": {
                first_of_month: {
                    "anthropic": {
                        "claude-sonnet-4-6": {
                            "input": 5,
                            "output": 0,
                            "calls": 1,
                            "cost_usd": 0.0,
                        }
                    }
                }
            },
        }

        await store.record(_event(input_tokens=1))

        assert first_of_month in store._data["days"]


class TestSingleton:
    def test_get_usage_store_returns_same_instance(self, hass) -> None:
        with patch("custom_components.selora_ai.usage_store.Store"):
            first = get_usage_store(hass)
            second = get_usage_store(hass)
        assert first is second
