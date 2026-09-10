"""The utilities/RAG specialist has to be reachable, not just present.

The provider half — the specialist prompt, the docs bundle, the retrieval
and the ``chat_utilities`` branch in the request builder — ships with the
package. None of it runs unless the classifier produces ``utilities`` AND
the kind maps to an intent, so this pins the whole chain: a request that
should reach the specialist, a request that must not, and each link in
between. Breaking any one link makes the specialist silently unreachable
again, which is exactly the state it shipped in.
"""

from __future__ import annotations

import pytest

from custom_components.selora_ai.const import (
    SELORA_LOCAL_KIND_TO_INTENT,
    SELORA_LOCAL_LORA_FILENAME_KEYWORDS,
    SELORA_LOCAL_MAX_TOKENS_BY_KIND,
)
from custom_components.selora_ai.llm_client.intent import (
    _classify_chat_intent,
    _is_utilities_help,
)


class TestClassifierRoutesDocsHelp:
    @pytest.mark.parametrize(
        "message",
        [
            "how do i update home assistant",
            "how do i back up home assistant",
            "how do i restore from a backup",
            "how do i add a new integration",
            "where do i find the long-lived access token",
            "why is my thermostat unavailable",
            "my sensor stopped reporting, what should i check",
        ],
    )
    def test_documentation_help_routes_to_utilities(self, message: str) -> None:
        assert _classify_chat_intent(message) == "utilities"

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            # The device paths this must not swallow. A false positive here
            # costs a real command, which is far worse than a missed lookup.
            ("turn off the kitchen light", "command"),
            ("can you turn off the fan", "command"),
            ("which lights are on", "answer"),
            ("turn on the porch light at sunset", "automation"),
            ("shut down home assistant", "clarification"),
            # Live state, not documentation. ``update`` is absent from
            # COLLECTOR_DOMAINS, so no ``update.*`` entity can reach
            # AVAILABLE ENTITIES and the docs specialist would be asked which
            # integrations have updates while holding nothing that could say.
            ("are there any pending updates", "answer"),
            ("is my system up to date", "answer"),
            # A question about THIS home's objects, which the answer
            # specialist can see in the snapshot and this one cannot.
            ("where can i find the kitchen light", "answer"),
            ("what are the automations currently enabled", "answer"),
        ],
    )
    def test_device_traffic_is_untouched(self, message: str, expected: str) -> None:
        assert _classify_chat_intent(message) == expected

    @pytest.mark.parametrize(
        "message",
        [
            "where can i find the notification settings",
            "where do i find the long-lived access token",
            "what is a helper in home assistant",
            "how do i update home assistant",
        ],
    )
    def test_the_narrowed_patterns_still_claim_real_docs_help(self, message: str) -> None:
        """Narrowing must not empty the route: each of these needs the noun,
        the token, the definition shape or the how-to frame that survived."""
        assert _classify_chat_intent(message) == "utilities"

    def test_a_symptom_alone_is_not_a_documentation_lookup(self) -> None:
        """``_UTIL_SYMPTOM`` needs a troubleshooting frame beside it, or
        every "the light is not working" becomes a docs question."""
        assert not _is_utilities_help("the kitchen light is not working")
        assert _is_utilities_help("why is the kitchen light not working")


class TestTheChainBehindTheClassifier:
    """Each link the classifier's answer has to travel through."""

    def test_the_kind_maps_to_the_utilities_intent(self) -> None:
        assert SELORA_LOCAL_KIND_TO_INTENT["chat_utilities"] == "utilities"

    def test_the_kind_has_its_own_token_budget(self) -> None:
        """A grounded answer quotes documentation, so it needs more room
        than an answer turn and less than an automation."""
        assert SELORA_LOCAL_MAX_TOKENS_BY_KIND["chat_utilities"] == 320

    def test_slot_discovery_can_recognise_the_lora(self) -> None:
        """Without the filename keyword the adapter is never mapped to an
        intent, so activation falls back to the base model and the
        specialist is unreachable even when the hub has it loaded."""
        assert "utilities" in SELORA_LOCAL_LORA_FILENAME_KEYWORDS

    def test_the_specialist_prompt_ships(self) -> None:
        from custom_components.selora_ai.providers.selora_local import (
            _SELORA_LOCAL_PROMPT_FILENAMES,
            _SELORA_LOCAL_PROMPTS_DIR,
        )

        name = _SELORA_LOCAL_PROMPT_FILENAMES["utilities"]
        assert (_SELORA_LOCAL_PROMPTS_DIR / name).is_file()
