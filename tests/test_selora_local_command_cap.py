"""The command specialist's output cap against what it was trained to emit."""

from __future__ import annotations

import pytest

from custom_components.selora_ai.const import (
    SELORA_LOCAL_KIND_TO_INTENT,
    SELORA_LOCAL_MAX_TOKENS_BY_KIND,
)

# Longest command-specialist reply in the v0.6.1 training corpus, in Qwen3
# tokens: three media players muted, with names apart from their ids (p99 73).
_LONGEST_TRAINED_COMMAND_REPLY_TOKENS = 118

# A one-token liveness probe, not a command turn.
_NOT_A_COMMAND_TURN = {"health_check"}


@pytest.mark.parametrize(
    "kind",
    sorted(
        kind
        for kind, intent in SELORA_LOCAL_KIND_TO_INTENT.items()
        if intent == "command" and kind not in _NOT_A_COMMAND_TURN
    ),
)
def test_the_command_cap_fits_the_longest_trained_reply(kind: str) -> None:
    """A cap below what the model was trained to emit cuts the JSON off
    mid-array, and the calls after the cut are lost."""
    assert SELORA_LOCAL_MAX_TOKENS_BY_KIND[kind] >= _LONGEST_TRAINED_COMMAND_REPLY_TOKENS
