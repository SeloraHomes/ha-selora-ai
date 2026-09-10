"""Trailing bytes around the envelope must not cost the whole reply.

Every JSON-envelope parser used to crop from the first ``{`` to the last
``}``. One stray brace outside the model's envelope — a duplicated
closing brace, a second object, a sentence of prose containing one —
makes that slice unbalanced, ``json.loads`` fails, and the command turn
comes back as ``intent=answer`` with the raw JSON in the chat bubble.
The service call is gone and nothing reports an error.

Cropping on balanced braces fixes that, and opens a second way to lose
the same reply: the first balanced object is often not the model's
answer. A reply that opens with ``{}``, quotes the format back
(``Format: {"intent":"answer"} then ...``), or writes a template
expression (``{{ states('sensor.x') }}``) puts a decoy in front of the
real envelope, and taking the first thing that parses is how a command
turn becomes an empty success — the user is told it worked and nothing
ran.

Every recovery case here asserts the OLD crop failed on the same input,
so a test that would pass against the code this replaced is not
mistaken for evidence.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.selora_ai.json_repair import (
    json_object_candidates,
    loads_first_json_object,
)
from custom_components.selora_ai.llm_client.parsers import (
    parse_architect_response,
    parse_command_response_text,
)
from custom_components.selora_ai.providers import create_provider

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.selora_ai.providers.selora_local import SeloraLocalProvider


_ENVELOPE = '{"intent": "command", "response": "Turning the light on.", "calls": []}'
_CALLS = '{"calls": [{"service": "light.turn_on"}], "response": "On."}'
_SLIM = '{"c": [{"s": "light.turn_on", "e": "light.hall"}], "r": "On."}'

# The decoys, in front of the real envelope. ``{}`` parses to a dict and
# an example envelope parses to a dict carrying an envelope key, so
# neither is filtered out by "does it parse" or "does it look like ours".
_EMPTY_PREFIX = "{} "
_DECOY_PREFIX = 'Format: {"intent":"answer"} then '
# The mirror of the same bug: a Jinja template in the model's prose is
# balanced braces that parse as nothing.
_TEMPLATE_PREFIX = "I'd use {{ states('sensor.x') }} here: "

# A command the token cap cut off mid-envelope. Handling must stay
# byte-identical to before any of this landed: a half-written unlock is
# never completed on the model's behalf.
_TRUNCATED_UNLOCK = '{"c":[{"s":"lock.unlock","e":"lock.front"'
# The same truncation one byte later, where the INNER object closes.
# This is the case a rescan could quietly break — restarting the scan
# inside an object that never closed would offer the inner service call
# up as if the model had finished writing it.
_TRUNCATED_UNLOCK_INNER_CLOSED = '{"c":[{"s":"lock.unlock","e":"lock.front"}'


def assert_naive_crop_failed(text: str) -> None:
    """Pin that first-brace-to-last-brace really does fail on ``text``.

    Every recovery test below is only meaningful if the crop it replaced
    could not have handled the same input. Without this assertion a case
    can quietly stop testing anything.
    """
    naive = text[text.find("{") : text.rfind("}") + 1]
    with pytest.raises(json.JSONDecodeError):
        json.loads(naive)


@pytest.fixture
def provider(hass: HomeAssistant) -> SeloraLocalProvider:
    return create_provider("selora_local", hass)  # type: ignore[return-value]


class TestJsonObjectCandidates:
    def test_balanced_object_leads(self) -> None:
        assert json_object_candidates('{"a": 1}}')[0] == '{"a": 1}'

    def test_naive_slice_is_kept_as_a_fallback(self) -> None:
        """A reply cut off before its closing brace has no balanced
        object, so the wide slice has to survive for the callers' own
        salvage passes to see it."""
        assert json_object_candidates('{"a": 1, "b": {"c": 2}') == ['{"a": 1, "b": {"c": 2}']

    def test_clean_json_yields_exactly_one_candidate(self) -> None:
        assert json_object_candidates('{"a": 1}') == ['{"a": 1}']

    def test_braces_inside_strings_do_not_close_the_object(self) -> None:
        payload = '{"response": "use {{ states(\'sensor.x\') }} here"}'
        assert json_object_candidates(payload)[0] == payload

    def test_no_object_at_all(self) -> None:
        assert json_object_candidates("not json at all") == []

    def test_an_open_brace_with_no_close_yields_nothing(self) -> None:
        assert json_object_candidates('trailing off {"a": 1') == []

    def test_every_balanced_object_is_offered_not_just_the_first(self) -> None:
        """The first object is a decoy here. Offering only it is how the
        real envelope stopped being reachable at all."""
        candidates = json_object_candidates(_EMPTY_PREFIX + _ENVELOPE)
        assert candidates[0] == "{}"
        assert _ENVELOPE in candidates

    def test_the_scan_resumes_past_a_template_expression(self) -> None:
        candidates = json_object_candidates(_TEMPLATE_PREFIX + _ENVELOPE)
        assert candidates[0] == "{{ states('sensor.x') }}"
        assert _ENVELOPE in candidates

    def test_the_scan_never_restarts_inside_an_unterminated_object(self) -> None:
        """The inner ``{"s": "lock.unlock", ...}`` closes, but the object
        containing it never does. Offering the inner one would hand back
        a service call the model never finished committing to."""
        candidates = json_object_candidates(_TRUNCATED_UNLOCK_INNER_CLOSED)
        assert candidates == [_TRUNCATED_UNLOCK_INNER_CLOSED]
        assert not any(c.startswith('{"s"') for c in candidates)


class TestEscapedStringsInTheScan:
    """The escape branch decides where a JSON string ends, and therefore
    which braces count. Getting it wrong swallows the envelope or ends
    it early."""

    def test_an_escaped_quote_does_not_end_the_string(self) -> None:
        payload = '{"r": "he said \\"hi}\\" ok"}'
        assert json_object_candidates(payload) == [payload]

    def test_an_escaped_backslash_does_not_escape_the_next_quote(self) -> None:
        payload = '{"r": "C:\\\\"}'
        assert json_object_candidates(payload) == [payload]

    def test_a_brace_inside_an_escaped_string_still_recovers(self) -> None:
        text = '{"r": "he said \\"hi}\\" ok"} }'
        assert_naive_crop_failed(text)
        assert loads_first_json_object(text) == {"r": 'he said "hi}" ok'}

    def test_a_trailing_backslash_string_still_recovers(self) -> None:
        text = '{"r": "C:\\\\"} }'
        assert_naive_crop_failed(text)
        assert loads_first_json_object(text) == {"r": "C:\\"}


class TestLoadsFirstJsonObject:
    def test_an_empty_object_in_front_does_not_win(self) -> None:
        text = _EMPTY_PREFIX + _ENVELOPE
        assert_naive_crop_failed(text)
        assert loads_first_json_object(text) == json.loads(_ENVELOPE)

    def test_an_example_envelope_in_front_does_not_win(self) -> None:
        """The decoy carries ``intent``, so "looks like one of ours" is
        not enough to tell it from the real reply — only carrying
        something to say or do is."""
        text = _DECOY_PREFIX + _ENVELOPE
        assert_naive_crop_failed(text)
        assert loads_first_json_object(text) == json.loads(_ENVELOPE)

    def test_a_template_expression_in_front_does_not_win(self) -> None:
        text = _TEMPLATE_PREFIX + _ENVELOPE
        assert_naive_crop_failed(text)
        assert loads_first_json_object(text) == json.loads(_ENVELOPE)

    def test_a_quoted_command_example_does_not_beat_the_real_reply(self) -> None:
        """The decoys above carry only ``intent``, so content alone tells
        them from the answer. An example that quotes a whole COMMAND
        carries ``calls`` and content no longer separates them — taking
        the first would run the example. The command prompt is full of
        ``c``-array examples and a small model echoes them, so this is the
        ordinary shape, not a contrived one."""
        real = '{"response": "Turned the lamp on.", "calls": [{"service": "light.turn_on"}]}'
        example = '{"calls": [{"service": "lock.unlock"}], "response": "Unlocked."}'
        text = f"For example: {example} Result: {real}"
        assert loads_first_json_object(text) == json.loads(real)

    def test_an_example_is_still_used_when_it_is_the_only_envelope(self) -> None:
        """Quoting the one envelope in the reply is how a small model
        often phrases a real answer, so demoting must not discard it."""
        example = '{"calls": [{"service": "light.turn_on"}], "response": "On."}'
        assert loads_first_json_object(f"For example: {example}") == json.loads(example)

    def test_a_marker_earlier_in_the_reply_does_not_demote_a_later_envelope(self) -> None:
        """The marker only counts in the unbroken prose immediately before
        the object, or a reply that says "format" once would demote every
        envelope after it."""
        real = '{"response": "Done.", "calls": []}'
        text = f"I checked the format of the request. {real}"
        assert loads_first_json_object(text) == json.loads(real)

    def test_the_first_envelope_wins_when_both_carry_content(self) -> None:
        """Ranking must not reorder two real replies — document order
        decides once content is equal."""
        first = '{"response": "first"}'
        assert loads_first_json_object(f'{first} {{"response": "second"}}') == {"response": "first"}

    def test_an_object_with_nothing_familiar_is_a_last_resort(self) -> None:
        assert loads_first_json_object('{"foo": "bar"}') == {"foo": "bar"}

    def test_no_object_reports_none_rather_than_raising(self) -> None:
        assert loads_first_json_object("just talking") is None

    def test_candidates_that_all_fail_raise_rather_than_reporting_none(self) -> None:
        """The two outcomes route to different salvage in the local
        provider, so they must stay distinguishable."""
        with pytest.raises(json.JSONDecodeError):
            loads_first_json_object("{not json}")

    def test_the_loads_hook_is_used_for_every_candidate(self) -> None:
        seen: list[str] = []

        def spy(candidate: str) -> Any:
            seen.append(candidate)
            return json.loads(candidate)

        loads_first_json_object(_EMPTY_PREFIX + _ENVELOPE, spy)
        assert seen[0] == "{}"
        assert _ENVELOPE in seen


class TestParseArchitectResponse:
    def test_trailing_brace_keeps_the_command(self, hass: HomeAssistant) -> None:
        text = _ENVELOPE + "}"
        assert_naive_crop_failed(text)
        result = parse_architect_response(text, hass)
        assert result["intent"] == "command"
        assert result["response"] == "Turning the light on."

    def test_trailing_prose_keeps_the_command(self, hass: HomeAssistant) -> None:
        result = parse_architect_response(_ENVELOPE + "\nHope that helps!", hass)
        assert result["intent"] == "command"

    def test_a_second_envelope_does_not_break_the_first(self, hass: HomeAssistant) -> None:
        result = parse_architect_response(f"{_ENVELOPE}\n{_ENVELOPE}", hass)
        assert result["intent"] == "command"

    def test_leading_prose_still_tolerated(self, hass: HomeAssistant) -> None:
        result = parse_architect_response("Sure thing! " + _ENVELOPE, hass)
        assert result["intent"] == "command"

    def test_leading_empty_object_keeps_the_command(self, hass: HomeAssistant) -> None:
        """Taking the first thing that parses returned ``{}`` here, which
        renders as a command turn with no calls and no error."""
        text = _EMPTY_PREFIX + _ENVELOPE
        assert_naive_crop_failed(text)
        result = parse_architect_response(text, hass)
        assert result["intent"] == "command"
        assert result["response"] == "Turning the light on."

    def test_a_quoted_example_keeps_the_command(self, hass: HomeAssistant) -> None:
        text = _DECOY_PREFIX + _ENVELOPE
        assert_naive_crop_failed(text)
        result = parse_architect_response(text, hass)
        assert result["intent"] == "command"
        assert result["response"] == "Turning the light on."

    def test_a_template_expression_keeps_the_command(self, hass: HomeAssistant) -> None:
        text = _TEMPLATE_PREFIX + _ENVELOPE
        assert_naive_crop_failed(text)
        result = parse_architect_response(text, hass)
        assert result["intent"] == "command"

    def test_text_with_no_json_is_still_an_answer(self, hass: HomeAssistant) -> None:
        result = parse_architect_response("just talking", hass)
        assert result == {"intent": "answer", "response": "just talking"}

    def test_unrecoverable_json_still_falls_back_to_the_raw_text(self, hass: HomeAssistant) -> None:
        broken = '{"intent": "command", '
        assert parse_architect_response(broken, hass) == {"intent": "answer", "response": broken}

    def test_an_envelope_with_no_response_still_gets_one(self, hass: HomeAssistant) -> None:
        """``response`` is Required on ``ArchitectResponse``; a reply
        carrying only an intent used to arrive without it."""
        result = parse_architect_response('{"intent": "answer"}', hass)
        assert result["response"] == '{"intent": "answer"}'

    def test_a_command_keeps_its_response_slot_open(self, hass: HomeAssistant) -> None:
        """A missing ``response`` is what tells ``apply_command_policy``
        to build the locale-aware confirmation, so filling it here would
        replace that sentence with raw JSON."""
        result = parse_architect_response(_CALLS, hass)
        assert result["response"] == "On."
        assert "response" not in parse_architect_response('{"calls": []}', hass)


class TestParseCommandResponseText:
    def test_trailing_brace_keeps_the_calls(self) -> None:
        text = _CALLS + "}"
        assert_naive_crop_failed(text)
        result = parse_command_response_text(text)
        assert result["calls"] == [{"service": "light.turn_on"}]
        assert result["response"] == "On."

    def test_leading_empty_object_keeps_the_calls(self) -> None:
        """The empty object parsed, so this reported success with no
        calls — the user was told the command ran and nothing did."""
        text = _EMPTY_PREFIX + _CALLS
        assert_naive_crop_failed(text)
        result = parse_command_response_text(text)
        assert result["calls"] == [{"service": "light.turn_on"}]
        assert result["response"] == "On."

    def test_a_quoted_example_keeps_the_calls(self) -> None:
        text = _DECOY_PREFIX + _CALLS
        assert_naive_crop_failed(text)
        result = parse_command_response_text(text)
        assert result["calls"] == [{"service": "light.turn_on"}]

    def test_text_with_no_json_reports_the_parse_failure(self) -> None:
        assert parse_command_response_text("nope")["response"] == "Could not parse LLM response"


class TestConvertSlimShape:
    def _convert(self, provider: SeloraLocalProvider, text: str) -> dict[str, Any]:
        return json.loads(provider._convert_slim_shape(text))

    def test_trailing_brace_keeps_the_slim_command(self, provider: SeloraLocalProvider) -> None:
        text = _SLIM + "}"
        assert_naive_crop_failed(text)
        result = self._convert(provider, text)
        assert result["intent"] == "command"
        assert result["calls"] == [
            {"service": "light.turn_on", "target": {"entity_id": "light.hall"}}
        ]

    def test_trailing_prose_keeps_the_slim_command(self, provider: SeloraLocalProvider) -> None:
        result = self._convert(provider, _SLIM + " Let me know if you need anything else.")
        assert result["intent"] == "command"

    def test_leading_empty_object_keeps_the_slim_command(
        self, provider: SeloraLocalProvider
    ) -> None:
        """``{}`` matched no slim shape, so the converter handed the raw
        JSON straight through to the chat bubble."""
        text = _EMPTY_PREFIX + _SLIM
        assert_naive_crop_failed(text)
        result = self._convert(provider, text)
        assert result["intent"] == "command"
        assert result["calls"] == [
            {"service": "light.turn_on", "target": {"entity_id": "light.hall"}}
        ]

    def test_a_quoted_example_keeps_the_slim_command(self, provider: SeloraLocalProvider) -> None:
        text = _DECOY_PREFIX + _SLIM
        assert_naive_crop_failed(text)
        result = self._convert(provider, text)
        assert result["intent"] == "command"
        assert result["calls"] == [
            {"service": "light.turn_on", "target": {"entity_id": "light.hall"}}
        ]

    def test_truncated_envelope_still_reaches_the_visible_text_salvage(
        self, provider: SeloraLocalProvider
    ) -> None:
        """No balanced object exists here; the wide slice is what the
        salvage path needs, so it must not have been dropped."""
        result = self._convert(provider, '{"r":"Three lights are on.","q":["light.a"')
        assert result["intent"] == "answer"
        assert result["response"] == "Three lights are on."


class TestTruncatedCommandStaysUnparsed:
    """A command cut off mid-envelope must behave exactly as it did
    before any of this landed. Recovering half of one means running a
    service call the model never finished asking for — here, unlocking a
    front door."""

    def test_no_candidate_is_offered(self) -> None:
        assert json_object_candidates(_TRUNCATED_UNLOCK) == []
        assert loads_first_json_object(_TRUNCATED_UNLOCK) is None

    def test_the_architect_parser_returns_the_raw_text(self, hass: HomeAssistant) -> None:
        assert parse_architect_response(_TRUNCATED_UNLOCK, hass) == {
            "intent": "answer",
            "response": _TRUNCATED_UNLOCK,
        }

    def test_the_command_parser_reports_the_failure(self) -> None:
        result = parse_command_response_text(_TRUNCATED_UNLOCK)
        assert result == {"calls": [], "response": "Could not parse LLM response"}

    def test_the_slim_converter_passes_the_text_through(
        self, provider: SeloraLocalProvider
    ) -> None:
        assert provider._convert_slim_shape(_TRUNCATED_UNLOCK) == _TRUNCATED_UNLOCK

    def test_a_closed_inner_object_is_not_recovered_either(self, hass: HomeAssistant) -> None:
        """One byte later the inner call object closes. It is still not a
        call the model committed to."""
        with pytest.raises(json.JSONDecodeError):
            loads_first_json_object(_TRUNCATED_UNLOCK_INNER_CLOSED)
        assert parse_architect_response(_TRUNCATED_UNLOCK_INNER_CLOSED, hass) == {
            "intent": "answer",
            "response": _TRUNCATED_UNLOCK_INNER_CLOSED,
        }
        assert parse_command_response_text(_TRUNCATED_UNLOCK_INNER_CLOSED)["calls"] == []
