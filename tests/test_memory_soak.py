"""Memory-leak soak tests.

Repeatedly exercise bounded structures and assert that resident size
does not grow monotonically across iterations. Each test:

1. Warms the path (allocates lazy caches, JITs the codec, etc.).
2. Takes a tracemalloc snapshot.
3. Runs N more iterations of the same path.
4. Compares snapshots — total traced bytes (across every filename, not
   just ``selora_ai`` ones) must stay under the configured per-test
   budget. We total every byte because retained-object leaks frequently
   keep references whose original allocation site was in the test
   driver itself; filtering by ``selora_ai`` filename would exclude
   exactly the bytes the test claims to catch.

The budgets are deliberately generous (5–10× the per-iteration cost)
so transient allocations don't false-fail the gate. What we're guarding
against is the unbounded-growth case where each iteration leaves a
permanent footprint — that pattern shows up as a near-linear delta
between two snapshots taken hundreds of iterations apart.
"""

from __future__ import annotations

import gc
import tracemalloc
from typing import Any

import pytest

from custom_components.selora_ai.collector import _cap_history_records
from custom_components.selora_ai.const import (
    DEFAULT_RECORDER_HISTORY_MAX_RECORDS,
    PATTERN_HISTORY_MAX_PER_ENTITY,
    PATTERN_HISTORY_MAX_TOTAL,
)
from custom_components.selora_ai.mcp_server import _RateLimiter
from custom_components.selora_ai.pattern_store import PatternStore

# Every test in this module is opt-in via ``-m soak``. The default
# ``pytest tests/`` run (configured in pyproject.toml) deselects them
# because each one allocates / churns thousands of dicts and adds
# seconds to the unit-test loop. The pre-push lefthook and the CI
# ``soak`` job invoke ``pytest -m soak`` explicitly.
pytestmark = pytest.mark.soak


def _total_bytes(snapshot: tracemalloc.Snapshot) -> int:
    """Return total tracemalloc bytes across every traced allocation.

    We deliberately do NOT filter by filename. A leak frequently shows
    up as a list inside ``selora_ai`` retaining objects whose original
    allocation site is in the test driver (the inputs we built). Filing
    by ``selora_ai`` filename would attribute those retained bytes to
    the test file and exclude them — exactly the failure mode this
    test claims to catch. Totaling every traced byte is a strict
    superset; the warmup baseline removes pytest/HA noise that's
    already steady-state by the time we snapshot.
    """
    return sum(s.size for s in snapshot.statistics("filename"))


def _assert_no_growth(label: str, before: int, after: int, budget_bytes: int) -> None:
    """Assert ``after - before`` stays within budget.

    Negative deltas (GC freed more than the loop allocated) are fine and
    treated as zero growth. We assert the *absolute* delta, not a ratio,
    because the warmup snapshot can be small enough that small absolute
    growth shows up as 1000% ratio while still being well under the
    leak threshold we actually care about.
    """
    delta = max(0, after - before)
    assert delta < budget_bytes, (
        f"{label}: heap grew by {delta} bytes after soak loop "
        f"(before={before}, after={after}, budget={budget_bytes}). "
        f"Likely unbounded growth in a cache, dict, or listener set — "
        f"inspect tracemalloc statistics for the offending frame."
    )


# ── Pattern store ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pattern_store_state_history_does_not_leak(hass, monkeypatch) -> None:
    """Two-phase soak: drive the store into steady state, snapshot, drive
    the same workload again, snapshot. The second loop must add roughly
    the same bytes as the first (≈ scratch/transient) — if the delta
    between the two snapshots grows linearly with iteration count, the
    structures aren't actually bounded.

    Catches: the dict-growing-forever class of leak (missing ring-buffer
    truncation, never-evicting setdefault, global cap that doesn't fire).
    Steady-state comparison is robust to allocations made by the test
    driver itself, which a filename-based filter would have hidden.

    ``_save`` is patched to skip the Store JSON write — we exercise the
    in-memory caps, not persistence. Real ``_save`` triggers the global
    cap as a side effect, so we call ``_enforce_global_history_cap``
    directly in the replacement to keep that invariant intact.
    """
    store = PatternStore(hass)

    async def _fast_save() -> None:
        # In-memory cap-only variant. The real _save serialises the
        # entire state_history dict every 50 events, which turns the
        # soak loop into an I/O loop — minutes instead of milliseconds.
        # We still need the global cap to fire (that's what we test),
        # so invoke it directly.
        if store._data is not None:  # noqa: SLF001
            PatternStore._enforce_global_history_cap(  # noqa: SLF001
                store._data["state_history"]  # noqa: SLF001
            )

    monkeypatch.setattr(store, "_save", _fast_save)

    async def _drive_one_pass() -> None:
        """One soak pass: pump enough events to overflow both caps.

        Sized to fire both caps with the smallest event count possible:
        50 entities × 600 events each = 30 000 events. Per-entity ring
        clips each to PATTERN_HISTORY_MAX_PER_ENTITY (500); 50 × 500 =
        25 000 records exceeds PATTERN_HISTORY_MAX_TOTAL (20 000), so
        the global cap fires too. Smaller numbers would skip one cap.
        """
        for i in range(int(PATTERN_HISTORY_MAX_PER_ENTITY * 1.2)):
            await store.record_state_change(
                "light.hot", "on", "off", f"2026-06-04T01:00:{i % 60:02d}+00:00"
            )
        n_entities = 50
        per_entity = 600
        for ei in range(n_entities):
            eid = f"sensor.s{ei}"
            for i in range(per_entity):
                await store.record_state_change(
                    eid, "on", "off", f"2026-06-0{(i % 5) + 1}T02:00:{i % 60:02d}+00:00"
                )
        await store.flush()

    # Tracemalloc on for BOTH phases so the snap_a → snap_b delta is
    # "what phase 2 added on top of phase 1" — i.e. the growth a leak
    # would produce per iteration. Starting after phase 1 would zero
    # the baseline and attribute the entire phase-2 working set to
    # "growth."
    await store._ensure_loaded()  # noqa: SLF001
    tracemalloc.start()

    # Phase 1 — warm + drive to steady state.
    await _drive_one_pass()
    gc.collect()
    snap_a = tracemalloc.take_snapshot()
    bytes_a = _total_bytes(snap_a)

    # Phase 2 — repeat the same workload. If caps hold, the dict is
    # already full so this pass should not add persistent records.
    await _drive_one_pass()
    gc.collect()
    snap_b = tracemalloc.take_snapshot()
    bytes_b = _total_bytes(snap_b)
    tracemalloc.stop()

    # Verify caps actually held (the assertion above only catches the
    # growth pattern, not whether the cap math is correct).
    data = await store._get_loaded_data()  # noqa: SLF001
    total = sum(len(v) for v in data["state_history"].values())
    assert total <= PATTERN_HISTORY_MAX_TOTAL, (
        f"Global cap breached: state_history holds {total} records, "
        f"ceiling is {PATTERN_HISTORY_MAX_TOTAL}"
    )
    longest = max(len(v) for v in data["state_history"].values())
    assert longest <= PATTERN_HISTORY_MAX_PER_ENTITY, (
        f"Per-entity ring buffer breached: longest bucket has {longest} "
        f"records, ceiling is {PATTERN_HISTORY_MAX_PER_ENTITY}"
    )

    # Budget: 2.5 MB delta. The steady-state working set is ~5.7 MB
    # (100 entities × ~20 000 records); the residual phase-2 delta is
    # mostly asyncio coroutine frames + tracemalloc bookkeeping that
    # gc.collect can't synchronously release. A real leak in the caps
    # would add another full working-set worth per pass (≥ 5 MB), which
    # blows through this budget by 2×+.
    _assert_no_growth("pattern_store", bytes_a, bytes_b, budget_bytes=int(2.5 * 1024 * 1024))


# ── Recorder history cap ─────────────────────────────────────────────


def test_cap_history_records_does_not_retain_dropped_records() -> None:
    """Call ``_cap_history_records`` 500 times with overflow input. Each
    call must return ≤ cap and not retain references to the dropped tail.

    Catches: a future regression that stashes the dropped records on a
    module-level list (e.g. for "debug history") and forgets to evict.
    """
    cap = DEFAULT_RECORDER_HISTORY_MAX_RECORDS
    overflow = cap * 2

    # Warmup — settle first-call allocations + steady-state caches.
    sample = [
        {"entity_id": f"sensor.s{i}", "state": "on", "last_changed": f"2026-06-04T00:00:{i % 60:02d}+00:00"}
        for i in range(overflow)
    ]

    def _one_pass() -> None:
        for _ in range(100):
            out = _cap_history_records(list(sample), cap, lookback_days=7)
            assert len(out) == cap
            del out

    tracemalloc.start()
    _one_pass()  # warm
    gc.collect()
    snap_a = tracemalloc.take_snapshot()
    bytes_a = _total_bytes(snap_a)

    _one_pass()  # measure
    gc.collect()
    snap_b = tracemalloc.take_snapshot()
    bytes_b = _total_bytes(snap_b)
    tracemalloc.stop()

    # The function is pure on its input — second pass should add only
    # transient bytes the tracer briefly captures. 256 KB tolerance.
    _assert_no_growth("cap_history_records", bytes_a, bytes_b, budget_bytes=256 * 1024)


# ── MCP rate limiter ─────────────────────────────────────────────────


def test_rate_limiter_evicts_stale_keys_under_unique_ip_flood() -> None:
    """Hit the limiter with 10 000 unique keys, advancing time past the
    window between sweeps. The dict must collapse back to a near-zero
    footprint instead of holding every key forever.

    Catches: the defaultdict-grew-forever bug class (the one this branch
    just fixed) and any regression that re-introduces it.
    """
    # Tiny window (0.1s) so the test waits 0.1s instead of 1s+ between
    # phases. _RateLimiter uses time.monotonic — sub-second windows work.
    limiter = _RateLimiter(window=1, max_hits=5)
    limiter._window = 0.1  # type: ignore[assignment]  # noqa: SLF001
    # Force every sweep window to fire by zeroing the throttle. The
    # default 60 s sweep interval would never trigger in pytest wall
    # time; we still verify the sweep fires by checking dict size.
    limiter._SWEEP_INTERVAL_S = 0.0  # type: ignore[misc]  # noqa: SLF001

    import time

    def _flood_and_sweep() -> None:
        for i in range(5_000):
            limiter.is_allowed(f"ip-{i}-{time.monotonic_ns()}")
        time.sleep(0.15)
        limiter._last_sweep = 0.0  # noqa: SLF001
        limiter.is_allowed("post-soak")

    tracemalloc.start()
    # Warmup pass — drive the dict through one full flood-then-evict
    # cycle to settle any first-time allocations.
    _flood_and_sweep()
    gc.collect()
    snap_a = tracemalloc.take_snapshot()
    bytes_a = _total_bytes(snap_a)

    _flood_and_sweep()
    gc.collect()
    snap_b = tracemalloc.take_snapshot()
    bytes_b = _total_bytes(snap_b)
    tracemalloc.stop()

    # After eviction, only the handful of live keys should remain. A
    # regression that drops the sweep would leave 5 000+ keys per pass
    # and the byte delta would scale with iteration count.
    assert len(limiter._hits) <= 5, (  # noqa: SLF001
        f"Rate limiter retained {len(limiter._hits)} keys after sweep — "  # noqa: SLF001
        f"eviction is not running or is missing aged-out buckets"
    )
    _assert_no_growth("rate_limiter", bytes_a, bytes_b, budget_bytes=256 * 1024)


# ── Pattern store global cap — pure function micro-soak ──────────────


def test_enforce_global_history_cap_is_idempotent_and_bounded() -> None:
    """Repeatedly call ``_enforce_global_history_cap`` on a fresh
    overflowing history dict. Each call must drop the same number of
    records (the function is pure on the input) and not retain refs.
    """
    cap = PATTERN_HISTORY_MAX_TOTAL

    def _make_history() -> dict[str, list[dict[str, Any]]]:
        n_entities = 25
        per_entity = (cap * 2) // n_entities
        return {
            f"sensor.s{ei}": [
                {"state": "on", "prev": "off", "ts": f"2026-06-0{(i % 5) + 1}T00:00:{i % 60:02d}"}
                for i in range(per_entity)
            ]
            for ei in range(n_entities)
        }

    def _one_pass() -> None:
        for _ in range(20):
            history = _make_history()
            dropped = PatternStore._enforce_global_history_cap(history)  # noqa: SLF001
            total = sum(len(v) for v in history.values())
            assert total <= cap
            assert dropped > 0
            del history

    tracemalloc.start()
    _one_pass()  # warm
    gc.collect()
    snap_a = tracemalloc.take_snapshot()
    bytes_a = _total_bytes(snap_a)

    _one_pass()  # measure
    gc.collect()
    snap_b = tracemalloc.take_snapshot()
    bytes_b = _total_bytes(snap_b)
    tracemalloc.stop()

    _assert_no_growth("global_cap", bytes_a, bytes_b, budget_bytes=512 * 1024)
