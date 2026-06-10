"""Tests for the MCP rate-limiter's empty-bucket eviction.

The previous implementation used a ``defaultdict(list)`` whose
``__getitem__`` inserted an empty list on every read, so every unique
client IP we ever rate-limited stayed in the dict for the lifetime of
the HA process. On a public MCP server (bots, CGNAT clients, normal
DHCP churn) that's a slow but unbounded memory leak.

This pins the new behaviour: bucket reads via ``.get()`` don't
auto-create entries, and a periodic sweep evicts keys whose timestamps
have all aged past the window.
"""

from __future__ import annotations

import time

from custom_components.selora_ai.mcp_server import _RateLimiter


def test_allow_creates_one_entry_per_key() -> None:
    """Each allowed call inserts a single key — no defaultdict-style
    read-time auto-creation of empty buckets."""
    rl = _RateLimiter(window=60, max_hits=5)
    assert rl.is_allowed("ip-a") is True
    assert rl.is_allowed("ip-b") is True
    assert set(rl._hits.keys()) == {"ip-a", "ip-b"}
    # Each bucket has the one recorded timestamp — never empty after allow.
    assert len(rl._hits["ip-a"]) == 1
    assert len(rl._hits["ip-b"]) == 1


def test_denied_keeps_full_bucket() -> None:
    """Denied requests don't grow the bucket (would defeat the limit) and
    don't drop the key (the next call still needs to see the full bucket
    to keep denying)."""
    rl = _RateLimiter(window=60, max_hits=2)
    assert rl.is_allowed("ip-a") is True
    assert rl.is_allowed("ip-a") is True
    assert rl.is_allowed("ip-a") is False
    assert rl.is_allowed("ip-a") is False
    # Bucket still hot at the limit, key still present.
    assert len(rl._hits["ip-a"]) == 2


def test_expired_bucket_evicted_on_sweep() -> None:
    """Sweep drops keys whose every timestamp aged past ``window``."""
    rl = _RateLimiter(window=0.05, max_hits=5)
    rl.is_allowed("ip-old")
    time.sleep(0.06)  # let the window expire
    # Bucket still in dict before sweep — eviction is explicit.
    assert "ip-old" in rl._hits
    evicted = rl._evict_empty()
    assert evicted == 1
    assert "ip-old" not in rl._hits


def test_sweep_keeps_active_keys() -> None:
    """A key that still has at least one in-window timestamp survives sweep."""
    rl = _RateLimiter(window=60, max_hits=5)
    rl.is_allowed("ip-fresh")
    evicted = rl._evict_empty()
    assert evicted == 0
    assert "ip-fresh" in rl._hits


def test_is_allowed_triggers_sweep_when_interval_passed() -> None:
    """Sweep fires inline from ``is_allowed`` after the interval has elapsed —
    no separate scheduler / cron call needed for the eviction to take effect."""
    rl = _RateLimiter(window=0.05, max_hits=5)
    rl._SWEEP_INTERVAL_S = 0.01  # speed up for the test
    rl.is_allowed("ip-old")
    time.sleep(0.06)
    # Trigger sweep via a fresh allow on a NEW key — the old one should
    # be evicted as a side effect.
    rl.is_allowed("ip-new")
    assert "ip-old" not in rl._hits
    assert "ip-new" in rl._hits


def test_get_does_not_auto_create_keys() -> None:
    """Read-only operations on the rate limiter must not pollute the dict
    (the leak the defaultdict path enabled)."""
    rl = _RateLimiter(window=60, max_hits=5)
    # Internal .get() with no allow call → no entry created.
    bucket = rl._hits.get("never-queried", None)
    assert bucket is None
    assert "never-queried" not in rl._hits
