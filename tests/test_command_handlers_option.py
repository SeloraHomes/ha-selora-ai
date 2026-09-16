"""The deterministic-override opt-out: ``command_handlers_enabled``.

``_convert_slim_shape`` consults ~25 ``_maybe_*`` overrides BEFORE the
model's text is parsed. Each takes no arguments — it reads
``self._user_message_raw`` and builds an envelope from the user's
SENTENCE — so a hit returns without ``text`` ever being read.

That is a reliability net under a 1.7B and the right thing to ship;
nothing here argues for moving the default. What it also means is that a
benchmark run through this provider scores the net rather than the model:
a control responder declining every turn still took 76.7% of assist-mini
(30 cases, seed 1729), failing only on ``lock`` and ``valve`` — the two
domains with no handler in ``providers/selora_local/commands/``.

The tests below are the local half of that acceptance check. The one that
matters is ``test_declining_envelope_survives_with_handlers_off``: with
the net off, a responder that asks for nothing must produce nothing.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.command_policy_options import (
    ENFORCED,
    CommandPolicyOptions,
    resolve_command_policy_options,
)
from custom_components.selora_ai.const import (
    CONF_COMMAND_HANDLERS_ENABLED,
    CONF_ENTRY_TYPE,
    CONF_LLM_PROVIDER,
    DOMAIN,
    ENTRY_TYPE_LLM,
)
from custom_components.selora_ai.providers.selora_local import SeloraLocalProvider

# What the control stub answers every completion with: a well-formed slim
# envelope that declines to act and names no entity.
_CONTROL_REPLY = json.dumps({"r": "Control run: this responder never acts.", "q": []})


def _llm_entry(hass, **options: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="test_llm_entry",
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_LLM, CONF_LLM_PROVIDER: "selora_local"},
        options=options,
    )
    entry.add_to_hass(hass)
    return entry


def _provider_for(hass, user_message: str) -> SeloraLocalProvider:
    """A provider whose conversion pass reads *user_message* as this turn's."""
    provider = SeloraLocalProvider(hass, host="http://hub")
    provider._user_message_raw.set(user_message)
    return provider


# ── The option resolves like its two siblings ───────────────────────────────


class TestResolution:
    def test_defaults_to_on(self, hass) -> None:
        assert resolve_command_policy_options(hass).handlers_enabled is True
        assert ENFORCED.handlers_enabled is True

    def test_entry_without_the_key_keeps_it_on(self, hass) -> None:
        _llm_entry(hass)
        assert resolve_command_policy_options(hass).handlers_enabled is True

    def test_option_turns_it_off(self, hass) -> None:
        _llm_entry(hass, **{CONF_COMMAND_HANDLERS_ENABLED: False})
        assert resolve_command_policy_options(hass).handlers_enabled is False

    def test_independent_of_its_siblings(self, hass) -> None:
        _llm_entry(hass, **{CONF_COMMAND_HANDLERS_ENABLED: False})
        policy = resolve_command_policy_options(hass)
        assert policy.handlers_enabled is False
        assert policy.approval_required is True
        assert policy.allowlist_enabled is True


class TestFullyEnforced:
    """``fully_enforced`` is what other code asks for "is anything relaxed?".
    A field left out of the conjunction keeps the property's name while
    checking less than the name claims — and answers True for an install
    that is running relaxed."""

    def test_true_only_with_every_field_set(self) -> None:
        assert CommandPolicyOptions().fully_enforced is True

    @pytest.mark.parametrize(
        "field",
        ["approval_required", "allowlist_enabled", "handlers_enabled"],
    )
    def test_any_single_override_makes_it_false(self, field: str) -> None:
        assert CommandPolicyOptions(**{field: False}).fully_enforced is False

    def test_every_field_is_in_the_conjunction(self) -> None:
        """Pins the rule rather than today's field list: a field added to the
        dataclass and forgotten in the property fails here."""
        import dataclasses

        for f in dataclasses.fields(CommandPolicyOptions):
            assert CommandPolicyOptions(**{f.name: False}).fully_enforced is False, (
                f"{f.name} is not in fully_enforced"
            )


# ── The guard: what the benchmark's fourth run depends on ───────────────────


class TestOverrideGuard:
    def test_handlers_on_answer_without_the_model(self, hass) -> None:
        """The shipped behaviour, stated plainly: a declining responder still
        produces a vacuum command, because the override never read its text."""
        _llm_entry(hass)
        hass.states.async_set(
            "vacuum.roborock_downstairs", "docked", {"friendly_name": "Roborock Downstairs"}
        )
        provider = _provider_for(hass, "Start vacuum in the living room")
        out = provider._convert_slim_shape(_CONTROL_REPLY)
        assert "vacuum" in out
        assert out != _CONTROL_REPLY

    def test_declining_envelope_survives_with_handlers_off(self, hass) -> None:
        """The acceptance check, locally: with the net off, a responder that
        asks for nothing must produce nothing. If this returns a command the
        bypass did not take effect, whatever the benchmark score says."""
        _llm_entry(hass, **{CONF_COMMAND_HANDLERS_ENABLED: False})
        hass.states.async_set(
            "vacuum.roborock_downstairs", "docked", {"friendly_name": "Roborock Downstairs"}
        )
        provider = _provider_for(hass, "Start vacuum in the living room")
        out = provider._convert_slim_shape(_CONTROL_REPLY)
        assert "vacuum.start" not in out
        assert '"calls"' not in out

    @pytest.mark.parametrize(
        "user_message",
        [
            "Start vacuum in the living room",
            "turn on the kitchen light",
            "what lights are on?",
            "set the thermostat to 21 degrees",
            "open the bedroom blinds",
        ],
    )
    def test_no_override_fires_with_handlers_off(self, hass, user_message: str) -> None:
        """Commands AND questions. The tuple opens with question handlers, so
        the switch is about the override MECHANISM, not about commands."""
        _llm_entry(hass, **{CONF_COMMAND_HANDLERS_ENABLED: False})
        for entity_id, state, attrs in (
            ("vacuum.roborock_downstairs", "docked", {"friendly_name": "Roborock"}),
            ("light.kitchen", "on", {"friendly_name": "Kitchen"}),
            ("climate.living_room", "heat", {"friendly_name": "Living Room"}),
            ("cover.bedroom", "closed", {"friendly_name": "Bedroom Blinds"}),
        ):
            hass.states.async_set(entity_id, state, attrs)
        provider = _provider_for(hass, user_message)
        out = provider._convert_slim_shape(_CONTROL_REPLY)
        assert '"calls"' not in out

    def test_the_raw_message_is_not_cleared_when_bypassed(self, hass) -> None:
        """Clearing is the override path's own bookkeeping — it stops a
        follow-up turn re-triggering the same hit. With no override consulted
        there is nothing to guard against, and clearing would strip context
        the JSON-parse branches below still read."""
        _llm_entry(hass, **{CONF_COMMAND_HANDLERS_ENABLED: False})
        provider = _provider_for(hass, "Start vacuum in the living room")
        provider._convert_slim_shape(_CONTROL_REPLY)
        assert provider._user_message_raw.get() == "Start vacuum in the living room"

    def test_a_real_model_command_still_parses_with_handlers_off(self, hass) -> None:
        """Turning the net off must not disable the parser — the model's own
        output is exactly what the run is there to measure."""
        _llm_entry(hass, **{CONF_COMMAND_HANDLERS_ENABLED: False})
        hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen"})
        provider = _provider_for(hass, "turn on the kitchen light")
        out = provider._convert_slim_shape(
            json.dumps(
                {
                    "r": "Turned on the Kitchen light.",
                    "c": [{"s": "light.turn_on", "e": "light.kitchen"}],
                }
            )
        )
        assert "light.turn_on" in out
        assert "light.kitchen" in out


# ── The sweep: the model's own words must survive every class ───────────────

# One phrasing per override class in ``_convert_slim_shape``'s dispatch
# tuple, plus the sentence-driven recoveries in the ``c`` and slim-answer
# branches. Guarding the tuple alone leaves those branches manufacturing
# envelopes from the sentence — which is exactly the shape of failure this
# option exists to remove, and it is invisible to any test that checks one
# domain.
_PROMPTS = [
    "what's on my calendar today?",
    "what lights are on?",
    "how many switches do I have?",
    "what's on my shopping list?",
    "is the kitchen plug on?",
    "is the living room speaker playing?",
    "are the sprinklers on?",
    "is it going to be sunny today?",
    "what's the battery level of the front door sensor?",
    "add milk to my shopping list",
    "turn on the kitchen light",
    "set the bedroom lights to 50%",
    "pause the living room speaker",
    "turn on the bedroom fan",
    "Start vacuum in the living room",
    "send the vacuum back to its dock",
    "activate movie night",
    "turn on guest mode",
    "open the bedroom blinds",
    "set the thermostat to 21 degrees",
    "lock the front door",
    "turn off the garden valve",
]


@pytest.fixture
def _home(hass: Any) -> Any:
    for entity_id, state, name in (
        ("vacuum.roborock_downstairs", "docked", "Roborock Downstairs"),
        ("light.kitchen", "on", "Kitchen"),
        ("light.bedroom", "off", "Bedroom"),
        ("switch.kitchen_plug", "on", "Kitchen Plug"),
        ("media_player.living_room", "playing", "Living Room Speaker"),
        ("climate.living_room", "heat", "Living Room"),
        ("cover.bedroom", "closed", "Bedroom Blinds"),
        ("fan.bedroom", "off", "Bedroom Fan"),
        ("lock.front_door", "locked", "Front Door"),
        ("valve.garden", "open", "Garden Valve"),
        ("scene.movie_night", "unknown", "Movie Night"),
        ("input_boolean.guest_mode", "off", "Guest Mode"),
        ("sensor.front_door_battery", "84", "Front Door Battery"),
    ):
        hass.states.async_set(entity_id, state, {"friendly_name": name})
    return hass


class TestControlResponderSweep:
    """The local stand-in for the benchmark's fourth run: control stub +
    handlers off must score ~0%. Expressed as an invariant rather than a
    score — the responder's OWN words have to come back. Any override that
    fires replaces them, and that is a case the run would credit to the
    model without the model having said anything.

    Run as a PAIR, the way rows 3 and 4 of the acceptance table are: the
    same prompts, the same home, one option flipped. Comparing against the
    net ON is what stops the OFF assertions passing for the wrong reason —
    a prompt that reaches no handler either way proves nothing, and most of
    the list is in that position here, needing a full ``set_chat_context``
    and an area registry this harness does not build.
    """

    # What fires today with the bare context below. A floor, not a target:
    # more handlers engage with richer context, and none of them may
    # survive the flag. Note ``lock`` and ``valve`` are absent — the two
    # domains with no handler in ``providers/selora_local/commands/``, and
    # the two the control run failed on.
    _MIN_OVERRIDDEN = 7

    @staticmethod
    def _convert(hass, prompt: str) -> str:
        provider = _provider_for(hass, prompt)
        return provider._convert_slim_shape(_CONTROL_REPLY)

    @staticmethod
    def _answer_text(out: str) -> str | None:
        """The user-facing text of *out*, or None if it is not an answer.

        Asking whether the control text is CONTAINED is too weak a question:
        ``_backfill_answer_marker`` appends a tile marker built from the
        sentence and leaves the model's prose in place, so a containment
        check passes over an answer that was half manufactured.
        """
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return None
        if data.get("calls") or data.get("c") or data.get("automation") or data.get("scene"):
            return None
        text = data.get("response") or data.get("r")
        return text if isinstance(text, str) else None

    def test_paired_run_over_every_override_class(self, _home) -> None:
        entry = _llm_entry(_home)

        control_text = json.loads(_CONTROL_REPLY)["r"]

        _home.config_entries.async_update_entry(entry, options={})
        fired_with_net = {
            p for p in _PROMPTS if self._answer_text(self._convert(_home, p)) != control_text
        }

        _home.config_entries.async_update_entry(
            entry, options={CONF_COMMAND_HANDLERS_ENABLED: False}
        )
        fired_without_net = {
            p for p in _PROMPTS if self._answer_text(self._convert(_home, p)) != control_text
        }

        assert fired_without_net == set(), (
            "an override answered for the control responder with the net OFF: "
            f"{sorted(fired_without_net)}"
        )
        assert len(fired_with_net) >= self._MIN_OVERRIDDEN, (
            f"only {len(fired_with_net)}/{len(_PROMPTS)} prompts hit an override with the net "
            "ON, so the OFF assertion above is weaker than it looks"
        )

    @pytest.mark.parametrize("prompt", _PROMPTS)
    def test_nothing_is_manufactured_with_handlers_off(self, _home, prompt: str) -> None:
        _llm_entry(_home, **{CONF_COMMAND_HANDLERS_ENABLED: False})
        out = self._convert(_home, prompt)
        assert '"calls"' not in out, f"an override manufactured a command for {prompt!r}"
        # EQUAL, not "contains": an appended tile marker leaves the model's
        # prose intact and would slip past a containment check.
        assert self._answer_text(out) == json.loads(_CONTROL_REPLY)["r"], (
            f"the responder's answer was altered for {prompt!r}: {out}"
        )


# ── The structural guard: a new override cannot forget the flag ─────────────


class TestEveryOverrideIsGated:
    """The behavioural sweep above can only cover phrasings someone thought
    of, and the override sites are spread across five branches of one long
    function — the tuple, the enveloped-command branch, the slim ``c``
    branch, the slim-answer branch and two truncated-JSON salvage paths.
    Gating a subset is the failure mode here: it leaves the benchmark
    scoring the net on whichever branch was missed, and every test of the
    branches that WERE gated still passes.

    So ask the question structurally instead. Inside ``_convert_slim_shape``,
    every read of the user's sentence must sit under ``overrides_enabled``.
    A site added later inherits the rule without anyone remembering it.
    """

    _SENTENCE_READS = {"_current_user_message", "_user_message_raw"}

    def _convert_slim_shape_ast(self) -> tuple[Any, Any]:
        import ast
        import inspect

        from custom_components.selora_ai.providers.selora_local.runtime import slim_parser

        tree = ast.parse(inspect.getsource(slim_parser))
        return ast, next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_convert_slim_shape"
        )

    def test_no_ungated_sentence_read(self) -> None:
        ast, fn = self._convert_slim_shape_ast()

        ungated: list[int] = []

        def walk(node, enclosing) -> None:
            for child in ast.iter_child_nodes(node):
                if (
                    isinstance(child, ast.Attribute)
                    and child.attr in self._SENTENCE_READS
                    and not any(
                        isinstance(a, ast.If) and "overrides_enabled" in ast.dump(a.test)
                        for a in enclosing
                    )
                ):
                    ungated.append(child.lineno)
                walk(child, [*enclosing, child])

        walk(fn, [])
        assert not ungated, (
            "these reads of the user's sentence in _convert_slim_shape are not under "
            f"`overrides_enabled` (source lines {sorted(set(ungated))}) — an override "
            "that answers from the sentence must honour command_handlers_enabled"
        )

    def test_the_detector_can_actually_fail(self) -> None:
        """Guards the guard: if the walk stopped finding sentence reads at all
        — a rename, a refactor into a helper — the test above would pass on an
        empty set and prove nothing."""
        ast, fn = self._convert_slim_shape_ast()
        found = [
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Attribute) and n.attr in self._SENTENCE_READS
        ]
        assert len(found) >= 5, (
            f"only {len(found)} sentence reads found in _convert_slim_shape; the "
            "detector above may no longer be looking at anything"
        )
