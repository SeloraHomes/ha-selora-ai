"""Tests for the backend stream watchdog helper in __init__.py.

Companion guards to MR !248 (frontend per-turn token): bound the SERVER
side of an architect_chat_stream so a hung provider or runaway-rambling
model can't drain the LLM connection forever and inflate HA's heap.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from custom_components.selora_ai import (
    _consume_stream_with_guards,
    _StreamTooLarge,
)
from custom_components.selora_ai.const import STREAM_KEEPALIVE


class _CloseTracker:
    """Records whether ``aclose()`` was called on the wrapped generator."""

    def __init__(self, gen: AsyncIterator[str]) -> None:
        self._gen = gen
        self.closed = False

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        return await self._gen.__anext__()

    async def aclose(self) -> None:
        self.closed = True
        await self._gen.aclose()


async def _collect(gen: AsyncIterator[str]) -> list[str]:
    return [c async for c in gen]


async def _yield(chunks: list[str], delays: list[float] | None = None) -> AsyncIterator[str]:
    for i, c in enumerate(chunks):
        if delays:
            await asyncio.sleep(delays[i])
        yield c


@pytest.mark.asyncio
async def test_passthrough_when_within_bounds() -> None:
    """Normal stream — chunks pass through unchanged and aclose() fires once."""
    src = _CloseTracker(_yield(["he", "llo", " world"]))
    out = await _collect(_consume_stream_with_guards(src, idle_timeout=1.0, max_bytes=1024))
    assert "".join(out) == "hello world"
    assert src.closed is True


@pytest.mark.asyncio
async def test_size_cap_raises_and_closes() -> None:
    """Once accumulated bytes exceed the cap, raise + close the upstream gen
    so the provider socket is released instead of being held open."""
    src = _CloseTracker(_yield(["abcdef", "ghijkl", "mnopqr"]))
    with pytest.raises(_StreamTooLarge) as ei:
        await _collect(_consume_stream_with_guards(src, idle_timeout=1.0, max_bytes=10))
    assert ei.value.size > 10
    assert src.closed is True


@pytest.mark.asyncio
async def test_idle_timeout_raises_and_closes() -> None:
    """No chunk within idle_timeout → TimeoutError; underlying gen is closed."""

    async def hang_after_first() -> AsyncIterator[str]:
        yield "ok"
        await asyncio.sleep(10.0)
        yield "never"

    src = _CloseTracker(hang_after_first())
    with pytest.raises(TimeoutError):
        await _collect(_consume_stream_with_guards(src, idle_timeout=0.2, max_bytes=1024))
    assert src.closed is True


@pytest.mark.asyncio
async def test_empty_stream_closes_cleanly() -> None:
    """Provider returns no chunks — the wrapper exits without errors and
    still closes the generator."""
    src = _CloseTracker(_yield([]))
    out = await _collect(_consume_stream_with_guards(src, idle_timeout=1.0, max_bytes=1024))
    assert out == []
    assert src.closed is True


@pytest.mark.asyncio
async def test_keepalive_resets_timer_without_counting_bytes() -> None:
    """STREAM_KEEPALIVE resets the idle timer, passes through, and doesn't
    count toward the byte cap."""

    async def slow_with_keepalives() -> AsyncIterator[str]:
        yield "hello"
        await asyncio.sleep(0.1)
        yield STREAM_KEEPALIVE
        await asyncio.sleep(0.1)
        yield " world"

    src = _CloseTracker(slow_with_keepalives())
    out = await _collect(_consume_stream_with_guards(src, idle_timeout=0.3, max_bytes=11))
    assert STREAM_KEEPALIVE in out
    text_chunks = [c for c in out if c != STREAM_KEEPALIVE]
    assert "".join(text_chunks) == "hello world"
    assert src.closed is True


@pytest.mark.asyncio
async def test_keepalive_does_not_count_toward_byte_cap() -> None:
    """A stream of many keepalives plus a small payload must not blow the
    byte cap. Locks in the invariant that keepalive bytes are excluded."""

    async def many_keepalives() -> AsyncIterator[str]:
        for _ in range(1000):
            yield STREAM_KEEPALIVE
        yield "ok"

    src = _CloseTracker(many_keepalives())
    out = await _collect(_consume_stream_with_guards(src, idle_timeout=1.0, max_bytes=16))
    text = "".join(c for c in out if c != STREAM_KEEPALIVE)
    assert text == "ok"
    assert src.closed is True


@pytest.mark.asyncio
async def test_byte_cap_counts_utf8_not_chars() -> None:
    """Hungarian / CJK glyphs are multi-byte; the cap is in bytes so a
    2-byte-per-char payload must trip the cap at half the char count."""

    # "ő" is U+0151 → 2 bytes in UTF-8. 6 × "ő" = 12 bytes > 10-byte cap.
    src = _CloseTracker(_yield(["őőő", "őőő"]))
    with pytest.raises(_StreamTooLarge) as ei:
        await _collect(_consume_stream_with_guards(src, idle_timeout=1.0, max_bytes=10))
    assert ei.value.size > 10
    assert src.closed is True


@pytest.mark.asyncio
async def test_keepalive_prevents_idle_timeout() -> None:
    """A keepalive arriving mid-gap prevents the idle timeout from firing."""

    async def keepalive_bridge() -> AsyncIterator[str]:
        yield "start"
        # Long gap bridged by keepalive at 0.1s — total gap for real
        # chunk is 0.3s which exceeds the 0.25s idle timeout, but the
        # keepalive at 0.1s resets the clock.
        await asyncio.sleep(0.1)
        yield STREAM_KEEPALIVE
        await asyncio.sleep(0.15)
        yield " end"

    src = _CloseTracker(keepalive_bridge())
    out = await _collect(_consume_stream_with_guards(src, idle_timeout=0.25, max_bytes=1024))
    text_chunks = [c for c in out if c != STREAM_KEEPALIVE]
    assert "".join(text_chunks) == "start end"
    assert src.closed is True
