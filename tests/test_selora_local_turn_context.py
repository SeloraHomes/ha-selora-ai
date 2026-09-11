"""A turn's context must survive to its conversion pass, and stay its own.

Two facts collide here. ``set_chat_context`` runs inside the request task,
and a ContextVar write there does not propagate up to the caller's context —
which is where the conversion pass runs, so the deterministic overrides read
back empty and silently skip. And the provider is shared, so a background
analysis cycle overlapping a panel chat writes the same state.

Answering a turn from another turn's request is the worst of the three
outcomes, so a turn is identified by the token its caller passes to both
halves. Without one, a single outstanding turn is still unambiguous; several
are not, and the caller falls back to the model's own output.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from custom_components.selora_ai.providers.selora_local import (
    _MAX_TURN_SNAPSHOTS,
    SeloraLocalProvider,
)

if TYPE_CHECKING:
    pass


def _provider() -> SeloraLocalProvider:
    hass = MagicMock()
    hass.states.async_all.return_value = []
    return SeloraLocalProvider(hass, host="http://hub")


async def _set_in_child_task(
    provider: SeloraLocalProvider, token: str | None, message: str
) -> None:
    """Set the context the way the request path does — inside a task.

    This is the whole reason the snapshots exist: the write lands in the
    task's own context copy and the caller never sees it.
    """

    async def _inner() -> None:
        provider.set_chat_context(user_message=message, entities=[], turn_token=token)

    await asyncio.create_task(_inner())


def _reads(provider: SeloraLocalProvider, token: str | None) -> str:
    """What the conversion pass for ``token`` would see."""
    previous = provider._active_turn_token
    provider._active_turn_token = token
    try:
        return provider._current_user_message()
    finally:
        provider._active_turn_token = previous


class TestTheWriteDoesNotReachTheConversionPass:
    async def test_the_context_var_reads_back_empty(self) -> None:
        """The premise. If this ever stops being true the snapshots are dead
        weight, and this test is where that shows up."""
        provider = _provider()
        await _set_in_child_task(provider, "a", "turn off the kitchen light")
        assert provider._user_message_raw.get() == ""

    async def test_the_snapshot_carries_it_instead(self) -> None:
        provider = _provider()
        await _set_in_child_task(provider, "a", "turn off the kitchen light")
        assert _reads(provider, "a") == "turn off the kitchen light"


class TestOneTurnIsNotAnsweredFromAnother:
    async def test_a_concurrent_turn_does_not_capture_the_first(self) -> None:
        """The bug: a single last-writer mirror handed turn A the message a
        background cycle wrote after it."""
        provider = _provider()
        await _set_in_child_task(provider, "a", "turn off the kitchen light")
        await _set_in_child_task(provider, "b", "summarise my energy usage")

        assert _reads(provider, "a") == "turn off the kitchen light"
        assert _reads(provider, "b") == "summarise my energy usage"

    async def test_an_untokened_turn_is_used_while_it_is_the_only_one(self) -> None:
        """The pre-token behaviour has to keep working — every caller that has
        not been updated depends on it."""
        provider = _provider()
        await _set_in_child_task(provider, None, "turn off the kitchen light")
        assert _reads(provider, None) == "turn off the kitchen light"

    async def test_an_untokened_turn_declines_once_it_is_ambiguous(self) -> None:
        """With several outstanding and nothing to tell them apart, the newest
        is not knowably this one. Declining costs a skipped override; guessing
        costs an answer to the wrong question."""
        provider = _provider()
        await _set_in_child_task(provider, None, "turn off the kitchen light")
        await _set_in_child_task(provider, "b", "summarise my energy usage")
        assert _reads(provider, None) == ""


class TestTheStoreIsBounded:
    async def test_the_conversion_pass_releases_its_own_snapshot(self) -> None:
        provider = _provider()
        await _set_in_child_task(provider, "a", "turn off the kitchen light")
        provider.convert_response_text("{}", turn_token="a")
        assert "a" not in provider._turn_snapshots

    async def test_turns_that_never_convert_are_evicted_oldest_first(self) -> None:
        """A turn that errors or is cancelled never reaches its conversion
        pass, so the store must not grow for the life of the process."""
        provider = _provider()
        for i in range(_MAX_TURN_SNAPSHOTS + 3):
            await _set_in_child_task(provider, f"t{i}", f"message {i}")
        assert len(provider._turn_snapshots) == _MAX_TURN_SNAPSHOTS
        assert "t0" not in provider._turn_snapshots
        assert f"t{_MAX_TURN_SNAPSHOTS + 2}" in provider._turn_snapshots

    async def test_the_token_is_unbound_after_the_conversion_pass(self) -> None:
        """It is a plain attribute, safe only because the pass is synchronous.
        Leaving it set would make the next turn read this one's context."""
        provider = _provider()
        await _set_in_child_task(provider, "a", "turn off the kitchen light")
        provider.convert_response_text("{}", turn_token="a")
        assert provider._active_turn_token is None
