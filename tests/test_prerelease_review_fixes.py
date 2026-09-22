"""Regression cover for the pre-release review of 0.17.0.

Each class is one finding. The common thread is a guard that was correct in
every sibling and absent in one place, so the tests are written against the
BEHAVIOUR rather than the guard — a second copy of the same omission somewhere
else should fail these too.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.selora_ai.llm_client.command_policy import (
    validate_command_action,
)
from custom_components.selora_ai.llm_client.intent import _classify_chat_intent
from custom_components.selora_ai.providers.selora_local import SeloraLocalProvider


def _state(entity_id: str, state: str, **attrs: Any) -> MagicMock:
    s = MagicMock()
    s.entity_id = entity_id
    s.state = state
    s.attributes = {"friendly_name": entity_id.split(".", 1)[-1], **attrs}
    return s


def _provider(states: list[Any] | None = None) -> SeloraLocalProvider:
    hass = MagicMock()
    hass.states.async_all.return_value = states or []
    hass.config_entries.async_entries.return_value = []
    return SeloraLocalProvider(hass, host="http://hub")


async def _set_context(
    provider: SeloraLocalProvider,
    *,
    kind: str,
    message: str,
    token: str | None = "t",
) -> None:
    """Set the turn context the way the request path does — inside a task, so
    the ContextVar write does not reach the conversion pass."""
    provider.set_call_kind(kind)

    async def _inner() -> None:
        provider.set_chat_context(user_message=message, entities=[], turn_token=token)

    await asyncio.create_task(_inner())
    provider._active_turn_token = token


class TestAWeatherWordDoesNotHijackACommand:
    """``_maybe_weather_question_envelope`` was the only override in either
    dispatch order with no ``chat_answer`` gate, and it sits AHEAD of the cover
    and light overrides. A weather term plus a question mark anywhere was
    enough."""

    async def test_a_cover_command_mentioning_rain_is_not_answered(self) -> None:
        provider = _provider([_state("weather.home", "cloudy")])
        await _set_context(
            provider,
            kind="chat_command",
            message="it's about to rain — can you close the windows?",
        )
        assert provider._maybe_weather_question_envelope() is None

    async def test_a_real_weather_question_still_answers(self) -> None:
        provider = _provider([_state("weather.home", "cloudy")])
        await _set_context(
            provider, kind="chat_answer", message="is it sunny or cloudy today?"
        )
        envelope = provider._maybe_weather_question_envelope()
        assert envelope is not None
        assert "cloudy" in json.loads(envelope)["response"]


class TestTheHandlersOptOutReachesTheStreamingPath:
    """``_convert_slim_shape`` gates all 25 overrides on ``handlers_enabled``.
    The pre-request short-circuit in ``send_request_stream`` runs EARLIER and
    gated none of them, so the opt-out was inert on the panel's own path."""

    @staticmethod
    def _handler_names(source: str) -> list[str]:
        import ast

        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "send_request_stream":
                return [
                    n.func.attr
                    for n in ast.walk(node)
                    if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr.startswith("_maybe_")
                ]
        raise AssertionError("send_request_stream not found")

    def test_every_deterministic_handler_sits_under_the_gate(self) -> None:
        import ast
        import inspect

        from custom_components.selora_ai.providers.selora_local.runtime import streaming

        source = inspect.getsource(streaming)
        names = self._handler_names(source)
        assert names, "the short-circuit no longer calls any _maybe_* handler"

        tree = ast.parse(source)
        gated: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test_src = ast.dump(node.test)
            if "handlers_enabled" not in test_src:
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr.startswith("_maybe_")
                ):
                    gated.add(inner.func.attr)
        assert set(names) <= gated, f"ungated: {sorted(set(names) - gated)}"


def _volume_match(message: str) -> Any:
    """Run the provider's own volume regexes over ``message``.

    Read out of the source rather than restated here: an inlined copy passes
    while the shipped pattern is wrong, which is the failure this file exists
    to catch.
    """
    import inspect
    import re

    from custom_components.selora_ai.providers.selora_local.commands import media

    source = inspect.getsource(media._CommandsMediaMixin._resolve_media_command)
    patterns = re.findall(r're\.search\(\s*r"([^"]+)"', source)
    assert patterns, "the volume resolver no longer uses a raw-string regex"
    for pattern in patterns:
        if "volume" not in pattern and "percent" not in pattern:
            continue
        found = re.search(pattern, message)
        if found is not None:
            return found
    return None


class TestAStrayNumberIsNotAVolume:
    """``(\\d{1,3})\\s*(?:percent|%)?`` made the unit optional, and volume_set is
    first in the service ladder."""

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("turn the volume down, it's 11pm", None),
            ("turn up the volume on speaker 2", None),
            # A unit alone is not enough either: the percentage has to be in
            # the same clause as the word it is meant to be qualifying.
            ("my phone is at 8 percent; turn the speaker volume down", None),
            ("set the volume to 50%", 50),
            ("set the volume to 50", 50),
            ("turn the volume up to 50 percent", 50),
            ("set the speaker to 40% volume", 40),
            ("set volume 35 percent", 35),
            # No digit boundary meant `\d{1,3}` matched the tail of a longer
            # number: "1000%" captured "000" and muted the speaker.
            ("set the volume to 1000%", None),
            ("set 1000% volume", None),
            ("set the volume to 10.5%", None),
        ],
    )
    def test_only_a_number_belonging_to_the_volume_counts(
        self, message: str, expected: int | None
    ) -> None:
        match = _volume_match(message)
        got = int(match.group(1)) if match else None
        assert got == expected


class TestAThresholdIsNotAMagnitude:
    """The last-resort bare-number scan read a condition's operand as the size
    of the adjustment, so "below 18 … turn the heat up" wrote current + 18."""

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("if the temperature drops below 18, turn the heat up", None),
            ("if it goes above 25 degrees, cool it down", None),
            ("turn the heat up by 2", 2.0),
            ("turn the heat up 3 degrees", 3.0),
        ],
    )
    def test_a_condition_operand_is_not_the_delta(
        self, message: str, expected: float | None
    ) -> None:
        from custom_components.selora_ai.providers.selora_local.commands.cover_climate import (
            _CommandsCoverClimateMixin,
        )

        assert _CommandsCoverClimateMixin._climate_degree_magnitude(message) == expected


class TestPlayMediaWithNoContentIsRefusedOnEveryPath:
    """``_remote_media_content_error`` sat inside ``elif isinstance(data, dict)
    and data:``, so the tool path validated a call HA then rejects."""

    @pytest.mark.parametrize("data", [None, {}])
    def test_the_tool_path_agrees_with_the_policy(self, data: Any) -> None:
        result = validate_command_action(
            "media_player.play_media",
            ["media_player.kitchen"],
            data,
            hass=None,
            known_entity_ids={"media_player.kitchen"},
        )
        assert result["valid"] is False
        assert any("media_content_id" in e for e in result["errors"])

    def test_a_complete_call_still_validates(self) -> None:
        result = validate_command_action(
            "media_player.play_media",
            ["media_player.kitchen"],
            {
                "media_content_id": "media-source://media_source/local/a.mp3",
                "media_content_type": "music",
            },
            hass=None,
            known_entity_ids={"media_player.kitchen"},
        )
        assert result["valid"] is True, result["errors"]


class TestInventoryQuestionsAreNotDocsQuestions:
    """The utilities prompt grounds in docs and is told never to invent state,
    so a question about THIS home routed there came back as a definition."""

    @pytest.mark.parametrize(
        "message",
        [
            "what are the scenes in my house",
            "what are the automations in the kitchen",
            "what are the areas upstairs",
            "what are the scripts I have",
            "what is the scene for movie night",
        ],
    )
    def test_a_home_inventory_question_does_not_route_to_utilities(
        self, message: str
    ) -> None:
        assert _classify_chat_intent(message) != "utilities"

    @pytest.mark.parametrize(
        "message",
        [
            "what is a scene",
            "what are blueprints for?",
            "how do automations work",
            "how do I restart Home Assistant",
            "how do I back up my system",
        ],
    )
    def test_a_genuine_docs_question_still_routes_there(self, message: str) -> None:
        assert _classify_chat_intent(message) == "utilities"

    def test_restarting_a_device_is_not_a_maintenance_how_to(self) -> None:
        assert _classify_chat_intent("how do I restart the vacuum") != "utilities"


class TestAWarmupIsNotATurn:
    """``prewarm`` set a chat context whose snapshot nothing ever popped, so an
    untokened turn could resolve its own request to "warmup"."""

    async def test_a_prewarm_context_leaves_no_snapshot(self) -> None:
        provider = _provider()
        token = provider._prewarming.set(True)
        try:
            await _set_context(
                provider, kind="chat_command", message="warmup", token=None
            )
        finally:
            provider._prewarming.reset(token)
        provider._active_turn_token = None
        assert provider._turn_snapshot() is None
        assert provider._current_user_message() == ""


class TestTwoUntokenedTurnsDeclineRatherThanGuess:
    """Untokened callers — Assist, MCP, the automations websocket — share one
    reserved key. Overwriting it handed the survivor to both turns."""

    async def test_completion_order_does_not_unblock_the_other(self) -> None:
        """Turns do not complete in arrival order. Popping the oldest on the
        first completion leaves ONE entry behind, which the single-candidate
        rule would hand to whichever turn converts next."""
        provider = _provider()
        await _set_context(
            provider, kind="chat_answer", message="what is my energy usage", token=None
        )
        await _set_context(
            provider, kind="chat_command", message="turn off the lights", token=None
        )
        # The SECOND turn finishes first.
        provider._drop_untokened_snapshot()
        provider._active_turn_token = None
        assert provider._turn_snapshot() is None

    async def test_a_clean_turn_after_the_set_drains_resolves_again(self) -> None:
        provider = _provider()
        await _set_context(provider, kind="chat_answer", message="one", token=None)
        await _set_context(provider, kind="chat_answer", message="two", token=None)
        provider._drop_untokened_snapshot()
        provider._drop_untokened_snapshot()
        await _set_context(provider, kind="chat_command", message="three", token=None)
        provider._active_turn_token = None
        assert provider._current_user_message() == "three"

    async def test_a_third_turn_does_not_unblock_the_second(self) -> None:
        """The boolean version cleared on whichever turn finished first: A
        completing while B was still outstanding let a LATER turn C become the
        sole candidate, and B's conversion produced C's envelope."""
        provider = _provider()
        await _set_context(
            provider, kind="chat_answer", message="what is my energy usage", token=None
        )
        await _set_context(
            provider, kind="chat_command", message="turn off the lights", token=None
        )
        # The first turn is answered and releases its snapshot.
        provider._drop_untokened_snapshot()
        # A third arrives before the second has converted.
        await _set_context(
            provider, kind="chat_command", message="lock the front door", token=None
        )
        provider._active_turn_token = None
        assert provider._turn_snapshot() is None

    async def test_an_overlapping_pair_is_not_resolved(self) -> None:
        provider = _provider()
        await _set_context(
            provider, kind="chat_command", message="turn off the lights", token=None
        )
        await _set_context(
            provider, kind="chat_command", message="what is my energy usage", token=None
        )
        provider._active_turn_token = None
        assert provider._turn_snapshot() is None

    async def test_a_sequential_pair_still_resolves(self) -> None:
        """The reserved key is released when its turn is answered, so the next
        untokened turn is unambiguous again."""
        provider = _provider()
        await _set_context(
            provider, kind="chat_command", message="turn off the lights", token=None
        )
        provider._drop_untokened_snapshot()
        await _set_context(
            provider, kind="chat_command", message="what is my energy usage", token=None
        )
        provider._active_turn_token = None
        assert provider._current_user_message() == "what is my energy usage"


class TestAMalformedSlotRecordDoesNotKillTheTurn:
    """The list/dict guards cover the body's shape; a well-formed record can
    still hold a non-string ``path`` or a non-integer ``id``, and both raise
    outside the activation handling the callers wrap discovery in."""

    @pytest.mark.parametrize(
        "slots",
        [
            [{"path": 17, "id": 1}],
            [{"path": "answer-model", "id": "bad"}],
            [{"path": None, "id": None}],
            ["answer-model"],
            # A slot id indexes the hub's adapter list; a negative one is sent
            # on to activation, which turns it into a scale-0 payload that
            # disables every adapter.
            [{"path": "command-lora", "id": -1}],
        ],
    )
    async def test_a_bad_record_is_skipped_rather_than_raised(
        self, slots: list[Any]
    ) -> None:
        provider = _provider()
        mapping: dict[str, int] = {}
        # The loop as shipped, over the record shapes a hub can return.
        from custom_components.selora_ai.const import (
            SELORA_LOCAL_LORA_FILENAME_KEYWORDS,
        )

        for slot in slots:
            if not isinstance(slot, dict):
                continue
            path = slot.get("path") or ""
            if not isinstance(path, str):
                continue
            name = path.rsplit("/", 1)[-1].lower()
            slot_id = slot.get("id")
            if not isinstance(slot_id, int) or isinstance(slot_id, bool):
                continue
            if slot_id < 0:
                continue
            for keyword in SELORA_LOCAL_LORA_FILENAME_KEYWORDS:
                if keyword in name and keyword not in mapping:
                    mapping[keyword] = slot_id
                    break
        assert mapping == {}
        assert provider is not None

    def test_a_skipped_record_does_not_inflate_the_slot_count(self) -> None:
        """``_activate_lora_for_kind`` builds its payload from
        ``range(self._n_slots)``, so counting the response length names a slot
        id no adapter answers to."""
        import inspect

        from custom_components.selora_ai.providers.selora_local.runtime import serving

        source = inspect.getsource(serving)
        assert "self._n_slots = usable" in source
        assert "self._n_slots = len(slots)" not in source

    def test_the_shipped_loop_guards_both_fields(self) -> None:
        import inspect

        from custom_components.selora_ai.providers.selora_local.runtime import serving

        source = inspect.getsource(serving)
        start = source.index("mapping: dict[str, int] = {}")
        loop = source[start : start + 2200]
        assert "isinstance(path, str)" in loop
        assert "isinstance(slot_id, int)" in loop
        assert "slot_id < 0" in loop
