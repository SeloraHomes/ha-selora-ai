"""The slim automation envelope must survive the trip to HA's schema.

The automation specialist emits ``{"r": …, "a": {…}}``. Before the branch
these tests cover existed, that envelope matched none of the slim shapes
the converter knew, fell through to the answer branch, and the entire
``a`` block was discarded silently — the user got a sentence describing
an automation that was never created.

Every envelope below is a verbatim shape from the training corpus, so a
change that breaks the wire contract fails here rather than in the field.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.selora_ai.automation_utils import (
    assess_automation_risk,
    validate_automation_payload,
)
from custom_components.selora_ai.providers import create_provider
from custom_components.selora_ai.providers._qwen_repair import (
    normalize_response_content,
    unwrap_listed_step_type,
)
from custom_components.selora_ai.providers.slim_automation import (
    SlimAutomationError,
    expand_slim_automation,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.selora_ai.providers.selora_local import SeloraLocalProvider


# ── The pure expansion ───────────────────────────────────────────────────────


class TestExpandSlimAutomation:
    def test_time_trigger_automation(self) -> None:
        expanded = expand_slim_automation(
            {
                "al": "Living Room Light On 8am",
                "t": [{"p": "time", "at": "08:00:00"}],
                "x": [{"s": "light.turn_on", "e": "light.living_room"}],
            }
        )
        assert expanded == {
            "alias": "Living Room Light On 8am",
            "triggers": [{"trigger": "time", "at": "08:00:00"}],
            "actions": [{"service": "light.turn_on", "target": {"entity_id": "light.living_room"}}],
        }

    def test_conditions_expand_with_their_own_type_key(self) -> None:
        """``p`` becomes ``condition`` in a condition, not ``trigger``."""
        expanded = expand_slim_automation(
            {
                "al": "Auto-Lock Back Door",
                "t": [{"p": "time", "at": "22:00:00"}],
                "c": [{"p": "state", "e": "lock.back_door", "state": "unlocked"}],
                "x": [{"s": "lock.lock", "e": "lock.back_door"}],
            }
        )
        assert expanded["conditions"] == [
            {"condition": "state", "entity_id": "lock.back_door", "state": "unlocked"}
        ]

    def test_omitted_conditions_do_not_become_an_empty_section(self) -> None:
        """The slim envelope drops ``c`` when empty; a written-back
        ``conditions: []`` would show up in the user's automations.yaml."""
        expanded = expand_slim_automation(
            {"al": "A", "t": [{"p": "sun", "event": "sunrise"}], "x": [{"s": "light.turn_off"}]}
        )
        assert "conditions" not in expanded

    def test_numeric_state_threshold_keys_pass_through(self) -> None:
        expanded = expand_slim_automation(
            {
                "al": "Fan On When Warm",
                "t": [{"p": "numeric_state", "e": "sensor.indoor_temperature", "below": 66}],
                "x": [{"s": "fan.turn_on", "e": "fan.bedroom_ceiling"}],
            }
        )
        assert expanded["triggers"] == [
            {"trigger": "numeric_state", "entity_id": "sensor.indoor_temperature", "below": 66}
        ]

    def test_action_data_and_multi_entity_target(self) -> None:
        expanded = expand_slim_automation(
            {
                "al": "Bedtime",
                "t": [{"p": "time", "at": "22:00:00"}],
                "x": [
                    {"s": "climate.set_temperature", "e": "climate.main", "d": {"temperature": 70}},
                    {"s": "light.turn_off", "e": ["light.attic", "light.pantry"]},
                ],
            }
        )
        assert expanded["actions"] == [
            {
                "service": "climate.set_temperature",
                "target": {"entity_id": "climate.main"},
                "data": {"temperature": 70},
            },
            {
                "service": "light.turn_off",
                "target": {"entity_id": ["light.attic", "light.pantry"]},
            },
        ]

    def test_targetless_action_keeps_no_target(self) -> None:
        """``notify.notify`` takes no entity — 51 corpus actions look like
        this (counted by ``test_targetless_actions_in_the_corpus`` below),
        and an invented empty target makes the validator reject them."""
        expanded = expand_slim_automation(
            {
                "al": "Door Alert",
                "t": [{"p": "state", "e": "binary_sensor.front_door", "to": "on"}],
                "x": [{"s": "notify.notify", "d": {"message": "A door just opened."}}],
            }
        )
        assert expanded["actions"] == [
            {"service": "notify.notify", "data": {"message": "A door just opened."}}
        ]

    def test_weekday_condition_list_passes_through(self) -> None:
        expanded = expand_slim_automation(
            {
                "al": "Weekday Wake",
                "t": [{"p": "time", "at": "07:00:00"}],
                "c": [{"p": "time", "weekday": ["mon", "tue"]}],
                "x": [{"s": "light.turn_on", "e": "light.bedroom"}],
            }
        )
        assert expanded["conditions"] == [{"condition": "time", "weekday": ["mon", "tue"]}]

    def test_nested_condition_list_expands_recursively(self) -> None:
        """``and``/``or``/``not`` nest a condition list under the same short
        key, so the expansion has to recurse instead of passing ``c`` on."""
        expanded = expand_slim_automation(
            {
                "al": "Nested",
                "t": [{"p": "time", "at": "07:00:00"}],
                "c": [
                    {
                        "p": "or",
                        "c": [
                            {"p": "state", "e": "person.sam", "state": "home"},
                            {"p": "state", "e": "person.alex", "state": "home"},
                        ],
                    }
                ],
                "x": [{"s": "light.turn_on", "e": "light.hall"}],
            }
        )
        assert expanded["conditions"] == [
            {
                "condition": "or",
                "conditions": [
                    {"condition": "state", "entity_id": "person.sam", "state": "home"},
                    {"condition": "state", "entity_id": "person.alex", "state": "home"},
                ],
            }
        ]

    def test_unknown_block_keys_are_carried_over(self) -> None:
        """Forward compatibility: a key the expansion has never seen is
        passed on to HA rather than dropped."""
        expanded = expand_slim_automation(
            {
                "al": "A",
                "t": [{"p": "time", "at": "07:00:00"}],
                "x": [{"s": "light.turn_on", "e": "light.hall"}],
                "mode": "restart",
            }
        )
        assert expanded["mode"] == "restart"

    def test_step_without_a_type_keeps_its_other_keys(self) -> None:
        """A typeless trigger must reach the validator intact so the user is
        told what is actually wrong, not handed a guessed trigger type."""
        expanded = expand_slim_automation(
            {"al": "A", "t": [{"at": "07:00:00"}], "x": [{"s": "light.turn_on", "e": "light.hall"}]}
        )
        assert expanded["triggers"] == [{"at": "07:00:00"}]

    def test_a_full_word_section_cannot_overwrite_the_expanded_one(self) -> None:
        """The forward-compatibility passthrough used to run last, so a block
        that sent both spellings kept the unexpanded copy and every short key
        in it reached HA."""
        expanded = expand_slim_automation(
            {
                "al": "Both Spellings",
                "t": [{"p": "time", "at": "07:00:00"}],
                "x": [{"s": "light.turn_on", "e": "light.hall"}],
                "actions": [{"s": "light.turn_on", "e": "light.hall"}],
            }
        )
        assert expanded["actions"] == [
            {"service": "light.turn_on", "target": {"entity_id": "light.hall"}}
        ]

    def test_an_already_expanded_block_survives_unchanged(self) -> None:
        """Expansion is idempotent: a block that is already in HA's spelling
        comes back as itself, so a mixed envelope cannot be half-translated."""
        full = {
            "alias": "Full Words",
            "triggers": [{"trigger": "time", "at": "07:00:00"}],
            "actions": [{"service": "light.turn_on", "target": {"entity_id": "light.hall"}}],
        }
        assert expand_slim_automation(dict(full)) == full

    def test_expansion_validates_as_a_real_automation(self) -> None:
        """The expanded object is the shape ``validate_automation_payload``
        accepts — checked without hass so only the schema gates run."""
        expanded = expand_slim_automation(
            {
                "al": "Main Light Sunrise",
                "t": [{"p": "sun", "event": "sunrise"}],
                "x": [{"s": "light.turn_off", "e": "light.main"}],
            }
        )
        is_valid, reason, normalized = validate_automation_payload(expanded, None)
        assert is_valid, reason
        assert normalized is not None
        assert normalized["triggers"] == [{"trigger": "sun", "event": "sunrise"}]
        assert normalized["mode"] == "single"


class TestNestedBranches:
    """A branch-shaped step nests its own steps, and the generator slims
    those with the same function it uses at the top level. Passing a
    container through with its short keys intact leaves a service call HA
    cannot run — and one no automation gate can see."""

    def test_if_then_expands_conditions_and_actions_apart(self) -> None:
        """Verbatim corpus shape (hysteresis: one trigger id per edge).
        ``if`` holds conditions, ``then`` holds actions — expanding both the
        same way writes ``service:`` onto a condition."""
        expanded = expand_slim_automation(
            {
                "al": "Bedroom Ceiling Fan Temp Control",
                "t": [
                    {"p": "numeric_state", "e": "sensor.indoor_temp", "above": 76, "id": "edge-on"},
                    {
                        "p": "numeric_state",
                        "e": "sensor.indoor_temp",
                        "below": 72,
                        "id": "edge-off",
                    },
                ],
                "x": [
                    {
                        "if": [{"p": "trigger", "id": "edge-on"}],
                        "then": [{"s": "fan.turn_on", "e": "fan.bedroom_ceiling"}],
                    }
                ],
            }
        )
        assert expanded["actions"] == [
            {
                "if": [{"condition": "trigger", "id": "edge-on"}],
                "then": [
                    {"service": "fan.turn_on", "target": {"entity_id": "fan.bedroom_ceiling"}}
                ],
            }
        ]

    def test_if_then_automation_validates(self) -> None:
        """The 143 corpus rows that used to be rejected with 'each condition
        must include a condition field'."""
        expanded = expand_slim_automation(
            {
                "al": "Hysteresis",
                "t": [{"p": "numeric_state", "e": "sensor.temp", "above": 76, "id": "on"}],
                "x": [
                    {
                        "if": [{"p": "trigger", "id": "on"}],
                        "then": [{"s": "fan.turn_on", "e": "fan.ceiling"}],
                    }
                ],
            }
        )
        is_valid, reason, _ = validate_automation_payload(expanded, None)
        assert is_valid, reason

    def test_else_and_nested_sequence_expand_as_actions(self) -> None:
        expanded = expand_slim_automation(
            {
                "al": "Either Way",
                "t": [{"p": "state", "e": "binary_sensor.motion", "to": "on"}],
                "x": [
                    {
                        "if": [{"p": "sun", "after": "sunset"}],
                        "then": [{"sequence": [{"s": "light.turn_on", "e": "light.porch"}]}],
                        "else": [{"s": "light.turn_off", "e": "light.porch"}],
                    }
                ],
            }
        )
        assert expanded["actions"] == [
            {
                "if": [{"condition": "sun", "after": "sunset"}],
                "then": [
                    {
                        "sequence": [
                            {"service": "light.turn_on", "target": {"entity_id": "light.porch"}}
                        ]
                    }
                ],
                "else": [{"service": "light.turn_off", "target": {"entity_id": "light.porch"}}],
            }
        ]

    def test_choose_branch_conditions_and_sequence_expand(self) -> None:
        expanded = expand_slim_automation(
            {
                "al": "Pick One",
                "t": [{"p": "time", "at": "18:00:00"}],
                "x": [
                    {
                        "choose": [
                            {
                                "conditions": [{"p": "state", "e": "person.sam", "state": "home"}],
                                "sequence": [{"s": "light.turn_on", "e": "light.den"}],
                            }
                        ],
                        "default": [{"s": "light.turn_off", "e": "light.den"}],
                    }
                ],
            }
        )
        assert expanded["actions"] == [
            {
                "choose": [
                    {
                        "conditions": [
                            {"condition": "state", "entity_id": "person.sam", "state": "home"}
                        ],
                        "sequence": [
                            {"service": "light.turn_on", "target": {"entity_id": "light.den"}}
                        ],
                    }
                ],
                "default": [{"service": "light.turn_off", "target": {"entity_id": "light.den"}}],
            }
        ]

    def test_repeat_sequence_and_until_expand(self) -> None:
        expanded = expand_slim_automation(
            {
                "al": "Keep Trying",
                "t": [{"p": "state", "e": "binary_sensor.leak", "to": "on"}],
                "x": [
                    {
                        "repeat": {
                            "sequence": [{"s": "notify.notify", "d": {"message": "Leak!"}}],
                            "until": [{"p": "state", "e": "binary_sensor.leak", "state": "off"}],
                            "count": 5,
                        }
                    }
                ],
            }
        )
        assert expanded["actions"] == [
            {
                "repeat": {
                    "sequence": [{"service": "notify.notify", "data": {"message": "Leak!"}}],
                    "until": [
                        {"condition": "state", "entity_id": "binary_sensor.leak", "state": "off"}
                    ],
                    "count": 5,
                }
            }
        ]

    def test_parallel_accepts_a_list_of_lists(self) -> None:
        """HA's ``parallel: [[…], […]]`` form — a list entry is a sequence,
        not a step."""
        expanded = expand_slim_automation(
            {
                "al": "Both At Once",
                "t": [{"p": "time", "at": "07:00:00"}],
                "x": [
                    {
                        "parallel": [
                            [{"s": "light.turn_on", "e": "light.a"}],
                            [{"s": "light.turn_on", "e": "light.b"}],
                        ]
                    }
                ],
            }
        )
        assert expanded["actions"] == [
            {
                "parallel": [
                    [{"service": "light.turn_on", "target": {"entity_id": "light.a"}}],
                    [{"service": "light.turn_on", "target": {"entity_id": "light.b"}}],
                ]
            }
        ]

    def test_inline_condition_action_expands_as_a_condition(self) -> None:
        """HA allows a condition as an action step. It carries ``p`` and no
        ``s``, so reading it as a service call would produce a target with
        no service."""
        expanded = expand_slim_automation(
            {
                "al": "Guarded",
                "t": [{"p": "time", "at": "07:00:00"}],
                "x": [
                    {"p": "state", "e": "lock.front", "state": "locked"},
                    {"s": "light.turn_on", "e": "light.hall"},
                ],
            }
        )
        assert expanded["actions"][0] == {
            "condition": "state",
            "entity_id": "lock.front",
            "state": "locked",
        }

    def test_shorthand_logical_condition_expands(self) -> None:
        """``{"or": [...]}`` with no ``condition`` key is HA's shorthand for
        the same group."""
        expanded = expand_slim_automation(
            {
                "al": "Either",
                "t": [{"p": "time", "at": "07:00:00"}],
                "c": [
                    {
                        "or": [
                            {"p": "state", "e": "person.sam", "state": "home"},
                            {"p": "state", "e": "person.alex", "state": "home"},
                        ]
                    }
                ],
                "x": [{"s": "light.turn_on", "e": "light.hall"}],
            }
        )
        assert expanded["conditions"] == [
            {
                "or": [
                    {"condition": "state", "entity_id": "person.sam", "state": "home"},
                    {"condition": "state", "entity_id": "person.alex", "state": "home"},
                ]
            }
        ]


class TestUnexpandedStepsAreRefused:
    """Every automation gate — the service-registry check, the unknown-entity
    check, the risk classifier — matches on Home Assistant's own key names.
    A step that kept its short keys is a step none of them can see, so it
    reaches automations.yaml with nothing having looked at it."""

    NESTED_WIPE = {
        "al": "Innocent Looking",
        "t": [{"p": "time", "at": "03:00:00"}],
        "x": [
            {
                "choose": [
                    {
                        "conditions": [{"p": "state", "e": "person.sam", "state": "not_home"}],
                        "sequence": [{"s": "shell_command.wipe_disk", "e": "light.does_not_exist"}],
                    }
                ]
            }
        ],
    }

    def test_a_container_the_expansion_does_not_know_is_refused(self) -> None:
        """The class, not the instance: a step nested under a key the
        expansion has never heard of would otherwise pass through with its
        short keys and reach HA unexamined."""
        with pytest.raises(SlimAutomationError) as excinfo:
            expand_slim_automation(
                {
                    "al": "Smuggled",
                    "t": [{"p": "time", "at": "03:00:00"}],
                    "x": [
                        {
                            "on_error": [
                                {"s": "shell_command.wipe_disk", "e": "light.does_not_exist"}
                            ]
                        }
                    ],
                }
            )
        assert "on_error" in str(excinfo.value)

    def test_a_short_key_left_on_the_block_is_refused(self) -> None:
        with pytest.raises(SlimAutomationError):
            expand_slim_automation(
                {
                    "al": "Leftover",
                    "t": [{"p": "time", "at": "03:00:00"}],
                    "x": [{"s": "light.turn_on", "e": "light.hall"}],
                    "p": "state",
                }
            )

    def test_service_fields_are_not_read_as_missed_expansion(self) -> None:
        """``data`` is the service's own payload. A field in there named
        ``e`` or ``t`` is application data, not a step the expansion
        missed."""
        expanded = expand_slim_automation(
            {
                "al": "Payload",
                "t": [{"p": "time", "at": "07:00:00"}],
                "x": [{"s": "mqtt.publish", "d": {"topic": "x/y", "payload": {"e": 1, "t": 2}}}],
            }
        )
        assert expanded["actions"][0]["data"]["payload"] == {"e": 1, "t": 2}

    def test_the_risk_classifier_now_sees_the_nested_service(self) -> None:
        """Before the branch bodies were inverted, the buried
        ``shell_command`` call was not a service call as far as the walk was
        concerned, and the automation was classified normal."""
        expanded = expand_slim_automation(dict(self.NESTED_WIPE))
        assert assess_automation_risk(expanded)["level"] == "elevated"

    async def test_a_hallucinated_nested_entity_is_rejected(self, hass: HomeAssistant) -> None:
        """The gate that matters, running against a real hass. The entity
        check reads ``target.entity_id``; a branch left in short keys has
        none, so the payload used to validate clean."""
        hass.states.async_set("light.hall", "off")
        expanded = expand_slim_automation(
            {
                "al": "Nested Hallucination",
                "t": [{"p": "time", "at": "03:00:00"}],
                "x": [
                    {
                        "choose": [
                            {
                                "conditions": [{"p": "state", "e": "light.hall", "state": "on"}],
                                "sequence": [{"s": "light.turn_off", "e": "light.does_not_exist"}],
                            }
                        ]
                    }
                ],
            }
        )
        is_valid, reason, _ = validate_automation_payload(expanded, hass)
        assert not is_valid
        assert "light.does_not_exist" in reason

    async def test_the_same_automation_with_a_real_entity_is_accepted(
        self, hass: HomeAssistant
    ) -> None:
        """Positive control: the rejection above is the entity gate firing,
        not the shape failing for some other reason."""
        hass.states.async_set("light.hall", "off")
        expanded = expand_slim_automation(
            {
                "al": "Nested Real",
                "t": [{"p": "time", "at": "03:00:00"}],
                "x": [
                    {
                        "choose": [
                            {
                                "conditions": [{"p": "state", "e": "light.hall", "state": "on"}],
                                "sequence": [{"s": "light.turn_off", "e": "light.hall"}],
                            }
                        ]
                    }
                ],
            }
        )
        is_valid, reason, _ = validate_automation_payload(expanded, hass)
        assert is_valid, reason


class TestMalformedStepsAreRefused:
    """A step the expansion cannot read used to become an empty object or a
    bare target. Both validate clean, both fail at HA reload — after the
    user has been told the automation was created."""

    BASE = {
        "al": "Base",
        "t": [{"p": "time", "at": "07:00:00"}],
        "x": [{"s": "light.turn_on", "e": "light.hall"}],
    }

    def _with(self, **overrides: Any) -> dict[str, Any]:
        return {**self.BASE, **overrides}

    def test_a_non_object_trigger_is_refused(self) -> None:
        with pytest.raises(SlimAutomationError):
            expand_slim_automation(self._with(t=["light.turn_on"]))

    def test_a_non_object_condition_is_refused(self) -> None:
        with pytest.raises(SlimAutomationError):
            expand_slim_automation(self._with(c=["always"]))

    def test_a_non_object_action_is_refused(self) -> None:
        with pytest.raises(SlimAutomationError):
            expand_slim_automation(self._with(x=["light.turn_on"]))

    def test_an_empty_action_is_refused(self) -> None:
        """``{}`` passes every validator gate and is rejected at reload."""
        with pytest.raises(SlimAutomationError):
            expand_slim_automation(self._with(x=[{}]))

    def test_a_target_with_no_service_is_refused(self) -> None:
        """A service call that lost its service. Written out it is a bare
        ``target:``, which validates and then fails at reload."""
        with pytest.raises(SlimAutomationError) as excinfo:
            expand_slim_automation(self._with(x=[{"e": "light.hall"}]))
        assert "light.hall" in str(excinfo.value)

    def test_a_non_object_automation_block_is_refused(self) -> None:
        with pytest.raises(SlimAutomationError):
            expand_slim_automation(["not", "a", "block"])  # type: ignore[arg-type]

    def test_a_non_object_data_payload_is_refused(self) -> None:
        """``d`` used to be dropped without a word, so the service ran
        without the arguments the model meant to pass it."""
        with pytest.raises(SlimAutomationError):
            expand_slim_automation(
                self._with(x=[{"s": "climate.set_temperature", "e": "climate.main", "d": 70}])
            )

    def test_an_empty_data_payload_is_dropped(self) -> None:
        """The contract omits ``d`` when there is no data; an empty one
        means the same thing and must not become ``data: {}``."""
        expanded = expand_slim_automation(
            self._with(x=[{"s": "light.turn_on", "e": "light.hall", "d": {}}])
        )
        assert expanded["actions"] == [
            {"service": "light.turn_on", "target": {"entity_id": "light.hall"}}
        ]

    def test_a_non_service_action_shape_is_left_alone(self) -> None:
        """``delay``, ``event``, ``scene``, ``stop`` and device actions are
        real HA actions with no service key. The slim contract does not
        abbreviate them, so they pass through."""
        expanded = expand_slim_automation(
            self._with(x=[{"delay": "00:05:00"}, {"s": "light.turn_on", "e": "light.hall"}])
        )
        assert expanded["actions"][0] == {"delay": "00:05:00"}


class TestWhatTheRepairPathWouldHaveDone:
    """This branch does not run `normalize_response_content`, so the two
    corrections that module makes to an automation have to exist here or
    they are simply lost."""

    def test_a_time_without_seconds_is_padded(self) -> None:
        """HA's time trigger insists on HH:MM:SS and says so at reload,
        after the automation is on disk."""
        expanded = expand_slim_automation(
            {
                "al": "Morning",
                "t": [{"p": "time", "at": "08:00"}],
                "x": [{"s": "light.turn_on", "e": "light.hall"}],
            }
        )
        assert expanded["triggers"] == [{"trigger": "time", "at": "08:00:00"}]

    def test_a_full_time_is_left_alone(self) -> None:
        expanded = expand_slim_automation(
            {
                "al": "Morning",
                "t": [{"p": "time", "at": "08:00:00"}],
                "x": [{"s": "light.turn_on", "e": "light.hall"}],
            }
        )
        assert expanded["triggers"][0]["at"] == "08:00:00"

    def test_only_a_time_trigger_is_padded(self) -> None:
        """Another platform's ``at`` is that platform's business."""
        expanded = expand_slim_automation(
            {
                "al": "Elsewhere",
                "t": [{"p": "calendar", "e": "calendar.work", "at": "08:00"}],
                "x": [{"s": "light.turn_on", "e": "light.hall"}],
            }
        )
        assert expanded["triggers"][0]["at"] == "08:00"

    def test_a_missing_alias_comes_from_the_response(self) -> None:
        """HA requires an alias. Rejecting an otherwise complete automation
        over the one field the sentence already describes helps nobody."""
        expanded = expand_slim_automation(
            {
                "t": [{"p": "time", "at": "08:00:00"}],
                "x": [{"s": "light.turn_on", "e": "light.hall"}],
            },
            "Turns on the hall light at 8am.",
        )
        assert expanded["alias"] == "Turns on the hall"
        is_valid, reason, _ = validate_automation_payload(expanded, None)
        assert is_valid, reason

    def test_a_blank_alias_is_replaced(self) -> None:
        expanded = expand_slim_automation(
            {
                "al": "   ",
                "t": [{"p": "time", "at": "08:00:00"}],
                "x": [{"s": "light.turn_on", "e": "light.hall"}],
            },
            "Locks the back door at ten.",
        )
        assert expanded["alias"] == "Locks the back door"

    def test_an_alias_with_nothing_to_build_from_still_exists(self) -> None:
        expanded = expand_slim_automation(
            {
                "t": [{"p": "time", "at": "08:00:00"}],
                "x": [{"s": "light.turn_on", "e": "light.hall"}],
            }
        )
        assert expanded["alias"] == "Automation"

    def test_the_model_s_own_alias_wins(self) -> None:
        expanded = expand_slim_automation(
            {
                "al": "Hall Light Morning",
                "t": [{"p": "time", "at": "08:00:00"}],
                "x": [{"s": "light.turn_on", "e": "light.hall"}],
            },
            "Turns on the hall light at 8am.",
        )
        assert expanded["alias"] == "Hall Light Morning"


# ── The converter branch ─────────────────────────────────────────────────────


@pytest.fixture
def provider(hass: HomeAssistant) -> SeloraLocalProvider:
    return create_provider("selora_local", hass)  # type: ignore[return-value]


def _convert(provider: SeloraLocalProvider, envelope: dict[str, Any]) -> dict[str, Any]:
    return json.loads(provider._convert_slim_shape(json.dumps(envelope)))


class TestConvertSlimShapeAutomationBranch:
    def test_slim_automation_becomes_an_automation_envelope(
        self, provider: SeloraLocalProvider
    ) -> None:
        """Before this branch the ``a`` block was discarded and the turn came
        back as ``intent=answer`` with the sentence only."""
        result = _convert(
            provider,
            {
                "r": "Turns main light off at sunrise.",
                "a": {
                    "al": "Main Light Sunrise",
                    "t": [{"p": "sun", "event": "sunrise"}],
                    "x": [{"s": "light.turn_off", "e": "light.main"}],
                },
            },
        )
        assert result["intent"] == "automation"
        assert result["response"] == "Turns main light off at sunrise."
        assert result["automation"]["alias"] == "Main Light Sunrise"
        assert result["automation"]["actions"] == [
            {"service": "light.turn_off", "target": {"entity_id": "light.main"}}
        ]

    def test_automation_branch_wins_over_the_answer_branch(
        self, provider: SeloraLocalProvider
    ) -> None:
        """``r`` is present on every slim automation envelope; reading it as
        the answer shape is exactly the bug."""
        result = _convert(
            provider,
            {
                "r": "Locks the back door at 10pm.",
                "a": {
                    "al": "Auto-Lock",
                    "t": [{"p": "time", "at": "22:00:00"}],
                    "x": [{"s": "lock.lock", "e": "lock.back_door"}],
                },
            },
        )
        assert result["intent"] != "answer"

    def test_clarification_escape_is_untouched(self, provider: SeloraLocalProvider) -> None:
        """The automation specialist escapes to ``{"q": …}`` — one key, no
        ``intent`` — when it cannot build the automation."""
        result = _convert(
            provider,
            {"q": "I don't see a temperature sensor — which one should trigger the fan?"},
        )
        assert result["intent"] == "answer"
        assert result["response"].startswith("I don't see a temperature sensor")

    def test_full_word_automation_envelope_still_takes_the_legacy_path(
        self, provider: SeloraLocalProvider
    ) -> None:
        """An older specialist's full envelope keeps its own handling even if
        it also carries an ``a`` key."""
        result = _convert(
            provider,
            {
                "intent": "automation",
                "response": "Done.",
                "a": {"al": "ignored"},
                "automation": {
                    "alias": "Legacy",
                    "triggers": [{"trigger": "time", "at": "07:00:00"}],
                    "actions": [{"service": "light.turn_on"}],
                },
            },
        )
        assert result["automation"]["alias"] == "Legacy"

    def test_empty_automation_block_falls_back_to_the_answer(
        self, provider: SeloraLocalProvider
    ) -> None:
        """``a`` present but empty means the model produced nothing to build;
        showing its sentence beats an automation-validation error."""
        result = _convert(provider, {"r": "I couldn't build that.", "a": {}})
        assert result["intent"] == "answer"
        assert result["response"] == "I couldn't build that."

    def test_a_refused_block_does_not_become_an_automation(
        self, provider: SeloraLocalProvider
    ) -> None:
        """A block the expansion could not finish falls through to the
        answer branch instead of building an automation nothing checked."""
        result = _convert(
            provider,
            {
                "r": "Cleans up at 3am.",
                "a": {
                    "al": "Smuggled",
                    "t": [{"p": "time", "at": "03:00:00"}],
                    "x": [{"on_error": [{"s": "shell_command.wipe_disk", "e": "light.nope"}]}],
                },
            },
        )
        assert result["intent"] == "answer"
        assert "automation" not in result

    def test_an_answer_envelope_is_not_hijacked_by_an_a_key(
        self, provider: SeloraLocalProvider
    ) -> None:
        """An envelope that names its own intent owns its shape; reading an
        ``a`` key off it would turn an answer into an automation."""
        result = _convert(
            provider,
            {
                "intent": "answer",
                "response": "The hall light is on.",
                "a": {
                    "al": "Not Requested",
                    "t": [{"p": "time", "at": "03:00:00"}],
                    "x": [{"s": "light.turn_off", "e": "light.hall"}],
                },
            },
        )
        assert result["intent"] == "answer"
        assert "automation" not in result

    def test_a_non_object_automation_block_falls_back_to_the_answer(
        self, provider: SeloraLocalProvider
    ) -> None:
        result = _convert(provider, {"r": "I couldn't build that.", "a": ["nope"]})
        assert result["intent"] == "answer"
        assert result["response"] == "I couldn't build that."

    def test_the_converter_passes_the_sentence_to_the_alias(
        self, provider: SeloraLocalProvider
    ) -> None:
        result = _convert(
            provider,
            {
                "r": "Turns on the hall light at 8am.",
                "a": {
                    "t": [{"p": "time", "at": "08:00"}],
                    "x": [{"s": "light.turn_on", "e": "light.hall"}],
                },
            },
        )
        assert result["automation"]["alias"] == "Turns on the hall"
        assert result["automation"]["triggers"][0]["at"] == "08:00:00"

    def test_slim_command_envelope_is_unaffected(self, provider: SeloraLocalProvider) -> None:
        result = _convert(
            provider,
            {"c": [{"s": "vacuum.stop", "e": "vacuum.roomba"}], "r": "Stopped the roomba."},
        )
        assert result["intent"] == "command"
        assert result["calls"] == [
            {"service": "vacuum.stop", "target": {"entity_id": "vacuum.roomba"}}
        ]


# ── The training corpus ──────────────────────────────────────────────────────
#
# The corpus is the wire contract in the only form that cannot go stale: it is
# what the model was actually shown. These tests read it directly, so the
# counts quoted above are reproducible by running the suite rather than by
# trusting a commit message. It does not ship with the integration — set
# SELORA_AUTOMATION_CORPUS to the directory holding train.jsonl and valid.jsonl,
# or the tests skip.

_CORPUS_ENV = "SELORA_AUTOMATION_CORPUS"
_CORPUS_DEFAULT = Path.home() / "Documents" / "SeloraAI" / "v0.4.9" / "data" / "automation"


def _corpus_dir() -> Path | None:
    directory = Path(os.environ.get(_CORPUS_ENV, _CORPUS_DEFAULT))
    files = [directory / "train.jsonl", directory / "valid.jsonl"]
    return directory if all(f.is_file() for f in files) else None


def _corpus_blocks() -> list[tuple[str, dict[str, Any]]]:
    """Every slim automation envelope in the corpus, as (response, block).

    Rows the converter routes elsewhere are skipped the same way the
    converter skips them: the clarification escape (a lone ``q``) and the
    blueprint rows, whose assistant turn is YAML rather than JSON.
    """
    directory = _corpus_dir()
    assert directory is not None
    blocks: list[tuple[str, dict[str, Any]]] = []
    for name in ("train.jsonl", "valid.jsonl"):
        with (directory / name).open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                content = json.loads(line)["messages"][-1]["content"]
                try:
                    envelope = json.loads(content)
                except json.JSONDecodeError:
                    continue
                if not isinstance(envelope, dict):
                    continue
                block = envelope.get("a")
                if isinstance(block, dict) and block:
                    blocks.append((envelope.get("r", "") or "", block))
    return blocks


@pytest.mark.skipif(_corpus_dir() is None, reason=f"{_CORPUS_ENV} corpus not present")
class TestAgainstTheTrainingCorpus:
    def test_every_envelope_becomes_a_valid_automation(self) -> None:
        """The claim in the commit message, checked rather than asserted:
        every envelope the model was trained to emit expands to something
        the automation validator accepts."""
        failures: list[str] = []
        blocks = _corpus_blocks()
        for response, block in blocks:
            try:
                expanded = expand_slim_automation(block, response)
            except SlimAutomationError as exc:
                failures.append(f"refused: {exc}")
                continue
            is_valid, reason, _ = validate_automation_payload(expanded, None)
            if not is_valid:
                failures.append(reason)
        assert blocks, "corpus present but held no slim automation envelopes"
        assert not failures, f"{len(failures)} of {len(blocks)}: {sorted(set(failures))[:5]}"

    def test_targetless_actions_in_the_corpus(self) -> None:
        """The count behind ``test_targetless_action_keeps_no_target``. A
        regenerated corpus moves this number, and the comment that quotes
        it should move with it."""
        targetless = 0
        for _response, block in _corpus_blocks():
            for action in block.get("x") or []:
                if isinstance(action, dict) and "s" in action and "e" not in action:
                    targetless += 1
        assert targetless == 51


class TestDriftAndContainersCodexFound:
    """Shapes the expansion met but did not invert.

    Each is either a valid automation refused wholesale, or — worse — one
    written to disk in a form Home Assistant rejects at reload, after the
    user has been told it was created.
    """

    def test_a_list_valued_trigger_type_is_unwrapped(self) -> None:
        """``{"p": ["time"]}`` is a known Qwen drift shape. The list is
        truthy so the payload validator accepts it, ``_pad_time_trigger``
        stops recognising the trigger, and HA rejects the entry at reload.
        Unwrapped the same way ``_qwen_repair`` unwraps it."""
        out = expand_slim_automation(
            {"al": "Morning", "t": [{"p": ["time"], "at": "08:00"}], "c": [], "x": []},
            "Morning lights",
        )
        trigger = out["triggers"][0]
        assert trigger["trigger"] == "time"
        # Unwrapping is what lets the seconds padding run at all.
        assert trigger["at"] == "08:00:00"

    def test_a_multi_element_trigger_type_takes_the_first(self) -> None:
        """Mirrors ``_qwen_repair``'s ``len(...) >= 1``, so the two paths
        cannot disagree about the same malformed trigger."""
        out = expand_slim_automation(
            {"al": "A", "t": [{"p": ["state", "time"], "e": "binary_sensor.x"}], "c": [], "x": []},
            "d",
        )
        assert out["triggers"][0]["trigger"] == "state"

    def test_a_non_string_trigger_type_is_left_for_the_validator(self) -> None:
        """Only a string is a platform name; anything else must reach the
        validator rather than be invented into one."""
        out = expand_slim_automation(
            {"al": "A", "t": [{"p": [{"nested": 1}], "e": "light.a"}], "c": [], "x": []},
            "d",
        )
        assert out["triggers"][0]["trigger"] == [{"nested": 1}]

    def test_wait_for_trigger_entries_are_expanded(self) -> None:
        """It holds TRIGGERS, so neither the condition nor the action
        branch claims it and its short keys reached the closing guard,
        which refused an automation the validator supports."""
        out = expand_slim_automation(
            {
                "al": "Wait",
                "t": [{"p": "state", "e": "binary_sensor.door"}],
                "c": [],
                "x": [{"wait_for_trigger": [{"p": "state", "e": "binary_sensor.motion"}]}],
            },
            "Wait for motion",
        )
        waited = out["actions"][0]["wait_for_trigger"][0]
        assert waited == {"trigger": "state", "entity_id": "binary_sensor.motion"}

    def test_a_shorthand_condition_action_is_expanded(self) -> None:
        """``{"or": [...]}`` used as an inline guard carries no ``p``, so
        it bypassed the condition branch and its nested slim keys were
        never inverted."""
        out = expand_slim_automation(
            {
                "al": "Guard",
                "t": [{"p": "state", "e": "binary_sensor.door"}],
                "c": [],
                "x": [
                    {"or": [{"p": "state", "e": "person.sam", "state": "home"}]},
                    {"s": "light.turn_on", "e": "light.hall"},
                ],
            },
            "Guarded",
        )
        guard = out["actions"][0]
        assert guard["or"] == [{"condition": "state", "entity_id": "person.sam", "state": "home"}]

    def test_a_shorthand_key_beside_a_service_is_still_a_service_call(self) -> None:
        """The guard must not capture an ordinary action that happens to
        carry one of the operator words as service data."""
        out = expand_slim_automation(
            {
                "al": "A",
                "t": [{"p": "state", "e": "binary_sensor.door"}],
                "c": [],
                "x": [{"s": "light.turn_on", "e": "light.hall", "or": ["ignored"]}],
            },
            "d",
        )
        assert out["actions"][0]["service"] == "light.turn_on"


class TestTriggerUnwrapIsOneRule:
    """The slim path and the full-envelope repair must answer alike.

    Both reach the same malformed trigger by different routes, and the
    slim path's docstring promises they agree. They agree because there
    is one implementation, not two that happen to match — this pins that
    the slim path really does defer to it, so re-inlining the rule fails
    here rather than in a user's automations.yaml.
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (["time"], "time"),
            # ``>= 1``, not ``== 1``: a multi-element list takes the first.
            (["state", "time"], "state"),
            ("time", "time"),
            # Not a platform name, so it must reach the validator intact
            # rather than be invented into one.
            ([], []),
            ([{"nested": 1}], [{"nested": 1}]),
            ([7], [7]),
        ],
    )
    def test_the_slim_path_uses_the_shared_rule(self, raw: Any, expected: Any) -> None:
        assert unwrap_listed_step_type(raw) == expected
        out = expand_slim_automation(
            {"al": "A", "t": [{"p": raw, "e": "light.a"}], "c": [], "x": []}, "d"
        )
        assert out["triggers"][0]["trigger"] == expected

    def test_the_full_envelope_path_uses_it_too(self) -> None:
        """The other caller, so the shared rule is pinned from both ends."""
        body = normalize_response_content(
            json.dumps(
                {
                    "intent": "automation",
                    "response": "ok",
                    "automation": {
                        "alias": "A",
                        "triggers": [{"trigger": ["time"], "at": "08:00"}],
                        "conditions": [],
                        "actions": [{"service": "light.turn_on"}],
                    },
                }
            )
        )
        assert json.loads(body)["automation"]["triggers"][0]["trigger"] == "time"
