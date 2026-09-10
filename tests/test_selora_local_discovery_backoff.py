"""The retry schedule for LoRA slot discovery, and the restart window it fixes.

The failure being pinned down: Home Assistant finishes restarting a few seconds
before the hub finishes loading its model, the pre-warm task probes, fails, and
a flat backoff then refuses every user request for the rest of that window even
though the hub came up almost immediately.

Nothing here needs a server. The hub is faked at the aiohttp boundary — as the
thing it actually does, answering 503 until it is ready — and the real
discovery code runs against it. The clock is real but only ever asked to pass
fractions of a second.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.selora_ai.providers.selora_local import (
    _SELORA_LOCAL_DISCOVERY_BACKOFF_MAX_S,
    _SELORA_LOCAL_DISCOVERY_BACKOFF_MIN_S,
    _SELORA_LOCAL_DISCOVERY_WAIT_S,
    SeloraLocalProvider,
    _SeloraLocalActivationError,
)

_MIN = _SELORA_LOCAL_DISCOVERY_BACKOFF_MIN_S
_MAX = _SELORA_LOCAL_DISCOVERY_BACKOFF_MAX_S


class _FakeResponse:
    def __init__(self, status: int = 200, payload: Any = None) -> None:
        self.status = status
        self._payload = payload

    async def json(self) -> Any:
        return self._payload

    async def text(self) -> str:
        return ""

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeSession:
    """A hub that answers 503 until it has finished loading, then serves.

    ``down_for`` is how many ``GET /lora-adapters`` calls it refuses before
    coming up — the boot race written as the thing the hub actually does,
    rather than as internal provider state set by hand.
    """

    def __init__(self, down_for: int = 0, *, status_when_up: int = 200) -> None:
        self.down_for = down_for
        self.status_when_up = status_when_up
        self.lora_gets = 0
        self.completions = 0

    def get(self, url: str, **_kw: Any) -> _FakeResponse:
        if url.endswith("/v1/models"):
            return _FakeResponse(200, {"data": [{"id": "selora"}]})
        self.lora_gets += 1
        if self.lora_gets <= self.down_for:
            return _FakeResponse(503, None)
        if self.status_when_up != 200:
            return _FakeResponse(self.status_when_up, None)
        return _FakeResponse(200, [{"id": 0, "path": "/m/selora-command.gguf"}])

    def post(self, url: str, **_kw: Any) -> _FakeResponse:
        if url.endswith("/lora-adapters"):
            return _FakeResponse(200, {})
        self.completions += 1
        return _FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]})


def make(session: _FakeSession | None = None) -> SeloraLocalProvider:
    hass = MagicMock()
    hass.states.async_all.return_value = []
    provider = SeloraLocalProvider(hass, host="http://hub.invalid:8080")
    provider._get_session = lambda: session or _FakeSession()  # type: ignore[method-assign]
    provider._get_headers = lambda: {}  # type: ignore[method-assign]
    # The trained prompts are read off disk through hass's executor, which a
    # MagicMock hass cannot run. Nothing here depends on their contents.
    provider._specialist_prompts_loaded = True
    provider._specialist_prompts = {}
    return provider


def elapse_the_window(provider: SeloraLocalProvider) -> None:
    """Stand in for the clock: the armed window is over.

    The only thing faked about time — the schedule itself, the probes and the
    waits below are all the real code.
    """
    provider._discovery_retry_after = 0.0


async def take_a_turn(provider: SeloraLocalProvider) -> tuple[str | None, str | None]:
    """One user chat turn, through the request path the panel actually calls."""
    provider.set_call_kind("chat_command")
    provider.set_chat_context(
        user_message="turn on the lamp",
        entities=[],
        existing_automations=[],
        history=[],
    )
    return await provider.send_request(
        "", [{"role": "user", "content": "turn on the lamp"}], max_tokens=8
    )


# ── the schedule ─────────────────────────────────────────────────────────────


def test_the_first_retry_is_short() -> None:
    """A flat half-minute is the wrong answer for a hub that is seconds away."""
    assert make()._schedule_discovery_retry() == _MIN


def test_repeated_failures_back_off_and_stop_at_the_ceiling() -> None:
    """A hub that is genuinely down must not be polled on every request."""
    provider = make()
    seen = [provider._schedule_discovery_retry() for _ in range(12)]
    assert seen[0] < seen[1] < seen[2]
    assert seen[-1] == _MAX
    assert max(seen) == _MAX


def test_the_armed_step_is_recorded_with_the_deadline() -> None:
    """The gate reads the step, so arming has to leave it behind."""
    provider = make()
    before = time.monotonic()
    delay = provider._schedule_discovery_retry()
    assert before + delay <= provider._discovery_retry_after <= time.monotonic() + delay
    assert provider._discovery_armed_delay == delay


async def test_a_failing_probe_escalates_the_real_schedule() -> None:
    """Driven through the discovery call itself, not the helper alone."""
    provider = make(_FakeSession(down_for=1))
    await provider._ensure_lora_discovery()
    assert provider._lora_slots is None
    assert provider._discovery_backoff_s > _MIN
    assert provider._discovery_retry_after > time.monotonic()


async def test_a_successful_discovery_resets_the_schedule() -> None:
    """Otherwise one bad night leaves the session at the ceiling for good."""
    session = _FakeSession(down_for=5)
    provider = make(session)
    for _ in range(5):
        elapse_the_window(provider)
        await provider._ensure_lora_discovery()
    assert provider._lora_slots is None
    assert provider._discovery_backoff_s > _MIN

    elapse_the_window(provider)
    await provider._ensure_lora_discovery()
    assert provider._lora_slots == {"command": 0}
    assert provider._discovery_backoff_s == _MIN


# ── what a user's first request after a restart gets ─────────────────────────


async def test_a_request_during_the_restart_window_waits_and_succeeds() -> None:
    """The hub comes up between the two probes, so the turn must go through."""
    session = _FakeSession(down_for=1)
    provider = make(session)
    await provider._ensure_lora_discovery()  # the boot-time probe: still loading
    assert provider._lora_slots is None

    started = time.monotonic()
    text, err = await take_a_turn(provider)
    elapsed = time.monotonic() - started

    assert err is None
    assert text == "ok"
    assert session.lora_gets == 2, "the failed probe, then the one that found it"
    assert _MIN <= elapsed < 2 * _MIN, "one short window, not several"


async def test_a_request_at_the_tail_of_a_long_window_fails_immediately() -> None:
    """Late in a fully-escalated window there is little time left but the hub
    has been failing for half a minute. Waiting buys nothing."""
    session = _FakeSession(down_for=99)
    provider = make(session)
    while provider._discovery_armed_delay < _MAX:
        provider._schedule_discovery_retry()
    assert provider._discovery_armed_delay > _SELORA_LOCAL_DISCOVERY_WAIT_S
    # The request lands 28.5s into the 30s window.
    provider._discovery_retry_after = time.monotonic() + 1.5
    probes_before = session.lora_gets

    started = time.monotonic()
    text, err = await take_a_turn(provider)
    elapsed = time.monotonic() - started

    assert text is None
    assert err is not None
    assert elapsed < 0.1, "the wait must key off the armed step, not the remainder"
    assert session.lora_gets == probes_before, "and it must not re-probe either"


async def test_a_hub_that_never_comes_back_reports_it() -> None:
    session = _FakeSession(down_for=99)
    provider = make(session)
    text, err = await take_a_turn(provider)
    assert text is None
    assert err is not None
    assert session.lora_gets == 2, "one bounded retry, then the honest error"


async def test_activation_refuses_to_guess_when_discovery_never_succeeded() -> None:
    """raw_request and the streaming path rely on this being a ConnectionError."""
    provider = make(_FakeSession(down_for=99))
    with pytest.raises(_SeloraLocalActivationError):
        await provider._activate_lora_for_kind("chat_command")


async def test_a_hub_without_the_endpoint_runs_on_the_base_model() -> None:
    """404 is an answer, not a hub that is still coming up: this build has no
    adapters. Treating it as transient escalated forever and left every
    request raising for the life of the process."""
    session = _FakeSession(status_when_up=404)
    provider = make(session)

    text, err = await take_a_turn(provider)
    assert err is None, "a hub with no LoRAs must still answer"
    assert text == "ok"
    assert provider._lora_slots == {}
    assert provider._n_slots == 0

    await take_a_turn(provider)
    assert session.lora_gets == 1, "the answer is settled, not re-asked every turn"


# ── pre-warm must not spend what the first real request needs ────────────────


async def test_prewarm_leaves_the_first_real_request_its_retry() -> None:
    """Pre-warm runs at setup, against the same not-yet-ready hub, and nobody
    is waiting on its answer. If it works its way through the schedule's short
    steps, the wait above is exhausted before the user has typed anything."""
    session = _FakeSession(down_for=1)
    provider = make(session)

    started = time.monotonic()
    await provider.prewarm(entities=[])
    prewarm_wall = time.monotonic() - started

    assert session.lora_gets == 1, "pre-warm re-probed a hub it already knew was down"
    assert prewarm_wall < _MIN, "pre-warm sat through windows nobody was waiting on"
    assert provider._discovery_armed_delay <= _SELORA_LOCAL_DISCOVERY_WAIT_S, (
        "the schedule escalated past the cap before the first real request"
    )

    # The hub has finished loading. Now the user types.
    started = time.monotonic()
    text, err = await take_a_turn(provider)
    elapsed = time.monotonic() - started

    assert err is None, "the wait never fired for the request it was written for"
    assert text == "ok"
    assert elapsed < 2 * _MIN


async def test_the_short_retries_do_not_shout(caplog: pytest.LogCaptureFixture) -> None:
    """A restart that settles in a second should not leave seven lines behind;
    a hub that is genuinely unwell still should."""
    logger = "custom_components.selora_ai.providers.selora_local"
    provider = make(_FakeSession(down_for=99))

    with caplog.at_level(logging.DEBUG, logger=logger):
        await provider._ensure_lora_discovery()
    assert not [r for r in caplog.records if r.levelno >= logging.INFO], (
        "the ordinary restart race logged above debug"
    )

    provider._discovery_backoff_s = _MAX
    elapse_the_window(provider)
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger=logger):
        await provider._ensure_lora_discovery()
    assert [r for r in caplog.records if r.levelno >= logging.INFO], (
        "a fully escalated hub is worth a line"
    )


# ── the wait's cost to everything else ───────────────────────────────────────


async def test_the_wait_leaves_the_event_loop_free() -> None:
    """A blocking sleep here would stall every other thing HA is doing."""
    provider = make(_FakeSession(down_for=1))
    await provider._ensure_lora_discovery()

    ticks = 0

    async def keep_counting() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    ticker = asyncio.create_task(keep_counting())
    try:
        _, err = await take_a_turn(provider)
    finally:
        ticker.cancel()

    assert err is None
    assert ticks > 100, "the loop did not run while the request waited"


async def test_concurrent_requests_share_one_window() -> None:
    """The wait runs before the request lock, so overlapping turns wait the
    same window once. Holding that lock across it would queue them up and
    charge each its own step — slower than the plain failure it replaced."""
    session = _FakeSession(down_for=99)
    provider = make(session)
    await provider._ensure_lora_discovery()  # arms the first short step

    started = time.monotonic()
    results = await asyncio.gather(*(take_a_turn(provider) for _ in range(5)))
    elapsed = time.monotonic() - started

    assert all(err is not None for _, err in results)
    assert elapsed < 2 * _MIN, "the requests queued up behind each other's waits"
    assert session.lora_gets == 2, "five requests, one shared window, one re-probe"
