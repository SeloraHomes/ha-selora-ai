"""Tests for parser-side recovery helpers: auto-correct, humanise, coercions.

Covers the v3-added functions in `llm_client/parsers.py` that turn LoRA
mistakes into useful behaviour:

- `_resolve_unknown_entity_ids` — auto-correct a wrong-domain entity_id
  (light.coffee_maker → switch.coffee_maker) by slug, friendly_name, or
  prompt fallback
- `_apply_entity_substitutions` — JSON tree walker that also swaps the
  action's service domain when entity_id moves domain
- `_humanise_unknown_entity_error` — replace the validator's bare reason
  with a clarification listing the user's actual devices
- `_coerce_sun_triggers` / `_coerce_numeric_state_triggers` — synthesize
  the right trigger shape from temporal/conditional language in the
  prompt when the LoRA emits the wrong shape
- `_service_verb_for_domain` — semantic verb mapping (on/off) across
  domains so cross-domain swaps preserve user intent
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN202
from typing import Any

import pytest

from custom_components.selora_ai.llm_client.parsers import (
    _apply_entity_substitutions,
    _coerce_numeric_state_triggers,
    _coerce_presence_for_duration_trigger,
    _coerce_sun_triggers,
    _entities_named_in_prompt,
    _extract_numeric_threshold,
    _find_presence_entity,
    _has_presence_for_duration,
    _humanise_unknown_entity_error,
    _match_presence_for_duration,
    _prompt_keyword_best_entity,
    _resolve_unknown_entity_ids,
    _service_verb_for_domain,
    _synthesize_action_from_prompt,
    parse_command_response_text,
    parse_suggestions,
)


def _entity(entity_id: str, friendly_name: str) -> dict[str, Any]:
    return {"entity_id": entity_id, "attributes": {"friendly_name": friendly_name}}


class _FakeState:
    def __init__(
        self,
        entity_id: str,
        friendly_name: str = "",
        *,
        members: list[str] | None = None,
        device_class: str | None = None,
        state: str = "on",
    ) -> None:
        self.entity_id = entity_id
        self.state = state
        self.attributes: dict[str, Any] = {"friendly_name": friendly_name}
        if members is not None:
            self.attributes["entity_id"] = members
        if device_class is not None:
            self.attributes["device_class"] = device_class


class _FakeStates:
    def __init__(self, states: list[_FakeState]) -> None:
        self._states = states
        self._by_id = {s.entity_id: s for s in states}

    def async_all(self) -> list[_FakeState]:
        return self._states

    def get(self, entity_id: str) -> _FakeState | None:
        return self._by_id.get(entity_id)


class _FakeHass:
    def __init__(self, states: list[_FakeState]) -> None:
        self.states = _FakeStates(states)


class TestServiceVerbForDomain:
    """Cross-domain semantic verb mapping."""

    @pytest.mark.parametrize(
        ("verb", "domain", "expected"),
        [
            ("on", "light", "turn_on"),
            ("off", "light", "turn_off"),
            ("on", "switch", "turn_on"),
            ("off", "switch", "turn_off"),
            ("on", "cover", "open_cover"),
            ("off", "cover", "close_cover"),
            ("on", "lock", "unlock"),
            ("off", "lock", "lock"),
            ("on", "vacuum", "start"),
            ("off", "vacuum", "stop"),
        ],
    )
    def test_known_domain_mapping(self, verb: str, domain: str, expected: str) -> None:
        assert _service_verb_for_domain(verb, domain) == expected

    def test_unknown_domain_falls_back_to_turn(self) -> None:
        assert _service_verb_for_domain("on", "weird_unknown") == "turn_on"
        assert _service_verb_for_domain("off", "weird_unknown") == "turn_off"


class TestResolveUnknownEntityIds:
    """Auto-correct wrong-domain entity_ids via slug / friendly_name / prompt."""

    def test_resolves_wrong_domain_via_slug(self) -> None:
        """``light.coffee_maker`` (hallucination) → ``switch.coffee_maker`` when
        the slug matches exactly one real entity in another domain."""
        reason = "automation references unknown entity_id(s): light.coffee_maker"
        automation = {
            "triggers": [{"trigger": "time", "at": "09:00:00"}],
            "actions": [
                {"service": "light.turn_on", "target": {"entity_id": "light.coffee_maker"}}
            ],
        }
        entities = [_entity("switch.coffee_maker", "Coffee Maker")]
        patched, subs = _resolve_unknown_entity_ids(reason, automation, entities)
        assert patched is not None
        assert subs == {"light.coffee_maker": "switch.coffee_maker"}

    def test_prefers_real_device_when_slug_shared_with_helper(self) -> None:
        """When ``cover.garage_door`` AND ``input_boolean.garage_door`` share the
        slug, the cover wins because input_boolean is helper-class."""
        reason = "automation references unknown entity_id(s): lock.garage_door"
        automation = {
            "triggers": [{"trigger": "time", "at": "22:00:00"}],
            "actions": [{"service": "lock.lock", "target": {"entity_id": "lock.garage_door"}}],
        }
        entities = [
            _entity("cover.garage_door", "Garage Door"),
            _entity("input_boolean.garage_door", "Garage Door Helper"),
        ]
        _patched, subs = _resolve_unknown_entity_ids(reason, automation, entities)
        assert subs.get("lock.garage_door") == "cover.garage_door"

    def test_prompt_name_does_not_retarget_unrelated_unknown(self) -> None:
        """Prompt mentions Coffee Maker, but the bad entity is a
        nonexistent ``light.porch`` trigger. We must NOT retarget the
        unknown trigger onto switch.coffee_maker — that would produce a
        self-triggering automation ("stop the thing that just turned on
        itself"). Refuse so the user gets a clarification listing real
        devices."""
        reason = "automation references unknown entity_id(s): light.porch"
        automation = {
            "triggers": [{"trigger": "state", "entity_id": "light.porch", "to": "on"}],
            "actions": [
                {"service": "switch.turn_off", "target": {"entity_id": "switch.coffee_maker"}}
            ],
        }
        entities = [_entity("switch.coffee_maker", "Coffee Maker")]
        patched, subs = _resolve_unknown_entity_ids(reason, automation, entities)
        assert patched is None
        assert subs == {}

    def test_prompt_fallback_not_applied_to_trigger_entity(self) -> None:
        """P1 — aggressive prompt-name fallback must not retarget a
        TRIGGER. "turn on the kitchen light when the front door opens"
        with a hallucinated ``light.front_door`` trigger must NOT become
        a ``light.kitchen`` trigger (would fire on the kitchen light,
        not the door). The prompt names exactly one device (kitchen
        light) — fallback applies to action targets only."""
        reason = "automation references unknown entity_id(s): light.front_door"
        automation = {
            "triggers": [{"trigger": "state", "entity_id": "light.front_door", "to": "on"}],
            "actions": [
                {"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}}
            ],
        }
        entities = [_entity("light.kitchen", "Kitchen Light")]
        patched, subs = _resolve_unknown_entity_ids(
            reason,
            automation,
            entities,
            user_message="turn on the kitchen light when the front door opens",
            aggressive=True,
        )
        assert "light.front_door" not in subs

    def test_prompt_fallback_not_used_when_named_device_is_trigger(self) -> None:
        """P1 — when the prompt names only the TRIGGER device ("When the
        Front Door opens, turn on a light"), an unknown action target
        must NOT be filled with that trigger device. Refuse so the user
        clarifies which light, instead of acting on the front door."""
        reason = "automation references unknown entity_id(s): light.some_light"
        automation = {
            "triggers": [
                {"trigger": "state", "entity_id": "cover.front_door", "to": "open"}
            ],
            "actions": [
                {"service": "light.turn_on", "target": {"entity_id": "light.some_light"}}
            ],
        }
        entities = [_entity("cover.front_door", "Front Door")]
        patched, subs = _resolve_unknown_entity_ids(
            reason,
            automation,
            entities,
            user_message="When the Front Door opens, turn on a light",
            aggressive=True,
        )
        assert "light.some_light" not in subs
        # No valid substitution → refuse rather than retarget to the door.
        assert subs == {}

    def test_aggressive_still_refuses_intent_inversion(self) -> None:
        """P1 — aggressive mode must NOT bypass the intent-inversion
        gate. "turn off Movie when nobody is home for 10 minutes" with a
        hallucinated ``light.movie`` resolving to ``scene.movie`` would
        flip ``light.turn_off`` into ``scene.turn_on`` — refuse."""
        reason = "automation references unknown entity_id(s): light.movie"
        automation = {
            "triggers": [{"trigger": "time", "at": "22:00:00"}],
            "actions": [
                {"service": "light.turn_off", "target": {"entity_id": "light.movie"}}
            ],
        }
        entities = [_entity("scene.movie", "Movie Scene")]
        patched, subs = _resolve_unknown_entity_ids(
            reason,
            automation,
            entities,
            user_message="turn off Movie when nobody is home for 10 minutes",
            aggressive=True,
        )
        assert patched is None
        assert subs == {}

    def test_aggressive_still_refuses_incompatible_service_data(self) -> None:
        """P1 — aggressive mode must NOT bypass the service-data gate. A
        ``light.turn_on`` with ``brightness`` swapped to a switch fails
        at runtime — refuse even in aggressive mode."""
        reason = "automation references unknown entity_id(s): light.movie"
        automation = {
            "triggers": [{"trigger": "time", "at": "22:00:00"}],
            "actions": [
                {
                    "service": "light.turn_on",
                    "target": {"entity_id": "light.movie"},
                    "data": {"brightness": 200},
                }
            ],
        }
        entities = [_entity("switch.movie", "Movie Switch")]
        patched, subs = _resolve_unknown_entity_ids(
            reason,
            automation,
            entities,
            user_message="turn on Movie when someone is home for 10 minutes",
            aggressive=True,
        )
        assert patched is None
        assert subs == {}

    def test_off_action_refused_when_target_resolves_to_scene(self) -> None:
        """``light.turn_off`` against an unknown ``light.movie`` that
        slug-resolves to ``scene.movie`` would silently become
        ``scene.turn_on`` (scenes have no off verb). Refuse — the user
        asked to turn it off, not to fire the scene."""
        reason = "automation references unknown entity_id(s): light.movie"
        automation = {
            "triggers": [{"trigger": "time", "at": "22:00:00"}],
            "actions": [
                {"service": "light.turn_off", "target": {"entity_id": "light.movie"}}
            ],
        }
        entities = [_entity("scene.movie", "Movie Scene")]
        patched, subs = _resolve_unknown_entity_ids(reason, automation, entities)
        assert patched is None
        assert subs == {}

    def test_cross_domain_swap_refused_when_action_has_light_specific_data(self) -> None:
        """``light.turn_on`` with ``brightness: 200`` swapped to a
        switch would silently fail at runtime — switches reject
        brightness. Refuse the substitution."""
        reason = "automation references unknown entity_id(s): light.coffee_maker"
        automation = {
            "triggers": [{"trigger": "time", "at": "09:00:00"}],
            "actions": [
                {
                    "service": "light.turn_on",
                    "target": {"entity_id": "light.coffee_maker"},
                    "data": {"brightness": 200},
                }
            ],
        }
        entities = [_entity("switch.coffee_maker", "Coffee Maker")]
        patched, subs = _resolve_unknown_entity_ids(reason, automation, entities)
        assert patched is None
        assert subs == {}


    def test_off_action_refused_when_data_target_resolves_to_scene(self) -> None:
        """Legacy ``data.entity_id`` shape: off-action with a target
        that would resolve to scene must be refused too (not just the
        flat / ``target.entity_id`` shapes)."""
        reason = "automation references unknown entity_id(s): light.movie"
        automation = {
            "triggers": [{"trigger": "time", "at": "22:00:00"}],
            "actions": [
                {"service": "light.turn_off", "data": {"entity_id": "light.movie"}}
            ],
        }
        entities = [_entity("scene.movie", "Movie Scene")]
        patched, subs = _resolve_unknown_entity_ids(reason, automation, entities)
        assert patched is None
        assert subs == {}

    def test_on_action_to_scene_still_allowed(self) -> None:
        """``light.turn_on`` → ``scene.movie`` correctly becomes
        ``scene.turn_on`` — only the off→scene inversion is refused."""
        reason = "automation references unknown entity_id(s): light.movie"
        automation = {
            "triggers": [{"trigger": "time", "at": "22:00:00"}],
            "actions": [
                {"service": "light.turn_on", "target": {"entity_id": "light.movie"}}
            ],
        }
        entities = [_entity("scene.movie", "Movie Scene")]
        patched, subs = _resolve_unknown_entity_ids(reason, automation, entities)
        assert patched is not None
        assert subs == {"light.movie": "scene.movie"}
        assert patched["actions"][0]["service"] == "scene.turn_on"
        assert patched["actions"][0]["target"]["entity_id"] == "scene.movie"

    def test_cross_domain_substitution_refused_when_trigger_pins_state(self) -> None:
        """``lock.front_door`` with ``to: locked`` should NOT silently
        become ``cover.front_door`` — covers don't emit ``locked``, so
        the rewritten trigger would never fire."""
        reason = "automation references unknown entity_id(s): lock.front_door"
        automation = {
            "triggers": [
                {
                    "trigger": "state",
                    "entity_id": "lock.front_door",
                    "to": "locked",
                },
            ],
            "actions": [],
        }
        entities = [_entity("cover.front_door", "Front Door")]
        patched, subs = _resolve_unknown_entity_ids(reason, automation, entities)
        assert patched is None
        assert subs == {}

    def test_cross_domain_substitution_allowed_when_no_state_pin(self) -> None:
        """A wrong-domain entity in an ACTION target (no to/from/state)
        still gets corrected — only triggers/conditions with pinned
        state semantics block the swap."""
        reason = "automation references unknown entity_id(s): light.coffee_maker"
        automation = {
            "triggers": [{"trigger": "time", "at": "09:00:00"}],
            "actions": [
                {"service": "light.turn_on", "target": {"entity_id": "light.coffee_maker"}}
            ],
        }
        entities = [_entity("switch.coffee_maker", "Coffee Maker")]
        patched, subs = _resolve_unknown_entity_ids(reason, automation, entities)
        assert patched is not None
        assert subs == {"light.coffee_maker": "switch.coffee_maker"}

    def test_substitution_refused_when_flat_entity_list_becomes_mixed_domain(self) -> None:
        """Same mixed-domain risk applies to a flat ``entity_id`` list
        on the action node (no ``target`` wrapper). Refuse."""
        reason = "automation references unknown entity_id(s): light.coffee_maker"
        automation = {
            "triggers": [{"trigger": "time", "at": "09:00:00"}],
            "actions": [
                {
                    "service": "light.turn_on",
                    "entity_id": ["light.kitchen", "light.coffee_maker"],
                }
            ],
        }
        entities = [
            _entity("light.kitchen", "Kitchen Light"),
            _entity("switch.coffee_maker", "Coffee Maker"),
        ]
        patched, subs = _resolve_unknown_entity_ids(reason, automation, entities)
        assert patched is None
        assert subs == {}

    def test_substitution_refused_when_data_entity_list_becomes_mixed_domain(self) -> None:
        """Legacy ``data.entity_id`` list path: same guard."""
        reason = "automation references unknown entity_id(s): light.coffee_maker"
        automation = {
            "triggers": [{"trigger": "time", "at": "09:00:00"}],
            "actions": [
                {
                    "service": "light.turn_on",
                    "data": {"entity_id": ["light.kitchen", "light.coffee_maker"]},
                }
            ],
        }
        entities = [
            _entity("light.kitchen", "Kitchen Light"),
            _entity("switch.coffee_maker", "Coffee Maker"),
        ]
        patched, subs = _resolve_unknown_entity_ids(reason, automation, entities)
        assert patched is None
        assert subs == {}

    def test_substitution_refused_when_target_list_becomes_mixed_domain(self) -> None:
        """An unknown ``light.coffee_maker`` in a target list alongside a
        real light would, after substitution, leave the action's target
        spanning [light.kitchen, switch.coffee_maker] — the service
        domain can't serve both. Refuse."""
        reason = "automation references unknown entity_id(s): light.coffee_maker"
        automation = {
            "triggers": [{"trigger": "time", "at": "09:00:00"}],
            "actions": [
                {
                    "service": "light.turn_on",
                    "target": {
                        "entity_id": ["light.kitchen", "light.coffee_maker"],
                    },
                }
            ],
        }
        entities = [
            _entity("light.kitchen", "Kitchen Light"),
            _entity("switch.coffee_maker", "Coffee Maker"),
        ]
        patched, subs = _resolve_unknown_entity_ids(reason, automation, entities)
        assert patched is None
        assert subs == {}

    def test_slug_substitution_rejected_for_non_device_bad_domain(self) -> None:
        """Unknown ``person.garage_door`` shares its slug with a real
        cover, but ``person`` is not a controllable-device domain — the
        substitution would change a presence trigger into a cover-state
        trigger. Refuse so the user can clarify."""
        reason = "automation references unknown entity_id(s): person.garage_door"
        automation = {
            "triggers": [{"trigger": "state", "entity_id": "person.garage_door"}],
            "actions": [],
        }
        entities = [_entity("cover.garage_door", "Garage Door")]
        patched, subs = _resolve_unknown_entity_ids(reason, automation, entities)
        assert patched is None
        assert subs == {}

    def test_slug_substitution_rejected_for_non_device_candidate(self) -> None:
        """Unknown ``light.coffee_maker`` slug-matches only
        ``input_boolean.coffee_maker`` — helper-class, not controllable.
        Refuse rather than retarget."""
        reason = "automation references unknown entity_id(s): light.coffee_maker"
        automation = {
            "triggers": [{"trigger": "time", "at": "09:00:00"}],
            "actions": [
                {"service": "light.turn_on", "target": {"entity_id": "light.coffee_maker"}}
            ],
        }
        entities = [_entity("input_boolean.coffee_maker", "Coffee Maker Helper")]
        patched, subs = _resolve_unknown_entity_ids(reason, automation, entities)
        assert patched is None
        assert subs == {}

    def test_resolves_more_unknowns_than_validator_preview(self) -> None:
        """Validator reasons truncate the preview to the first three with
        a ``(+N more)`` suffix. Resolution must walk the automation for
        ALL referenced unknowns, not just the ones in the reason text,
        so a 5-entity payload can fully recover in one pass."""
        reason = (
            "automation references unknown entity_id(s): "
            "light.l1, light.l2, light.l3 (+2 more)"
        )
        automation = {
            "triggers": [{"trigger": "time", "at": "09:00:00"}],
            "actions": [
                {
                    "service": "light.turn_on",
                    "target": {
                        "entity_id": [
                            "light.l1",
                            "light.l2",
                            "light.l3",
                            "light.l4",
                            "light.l5",
                        ],
                    },
                }
            ],
        }
        entities = [
            _entity(f"switch.l{i}", f"L{i}") for i in range(1, 6)
        ]
        patched, subs = _resolve_unknown_entity_ids(reason, automation, entities)
        assert patched is not None
        # All five substituted, not just the three named in the reason
        assert subs == {f"light.l{i}": f"switch.l{i}" for i in range(1, 6)}
        assert patched["actions"][0]["target"]["entity_id"] == [
            f"switch.l{i}" for i in range(1, 6)
        ]
        assert patched["actions"][0]["service"] == "switch.turn_on"

    def test_registered_entity_not_treated_as_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Snapshot ``entities`` is filtered. The HA state machine plus
        entity registry are the ground truth for "exists" — a
        registered-but-stateless entity (disabled, unavailable) MUST
        NOT be retargeted to a same-slug entity in another domain."""
        import custom_components.selora_ai.llm_client.parsers as parsers_mod

        # HA has both switch.coffee_maker (state) and light.coffee_maker
        # (registry only, no state). Validator accepts the light; recovery
        # must too.
        ha_known = {"switch.coffee_maker", "light.coffee_maker"}

        def _fake_finder(_hass: object, eids: set[str]) -> list[str]:
            return sorted(e for e in eids if e not in ha_known)

        monkeypatch.setattr(parsers_mod, "_find_unknown_entity_ids", _fake_finder)

        reason = "automation references unknown entity_id(s): light.coffee_maker"
        automation = {
            "triggers": [{"trigger": "time", "at": "09:00:00"}],
            "actions": [
                {"service": "light.turn_on", "target": {"entity_id": "light.coffee_maker"}}
            ],
        }
        entities = [_entity("switch.coffee_maker", "Coffee Maker")]
        patched, subs = _resolve_unknown_entity_ids(
            reason, automation, entities, hass=object()
        )
        assert patched is None
        assert subs == {}

    def test_returns_none_when_truly_unknown(self) -> None:
        reason = "automation references unknown entity_id(s): lock.front_door"
        automation = {
            "triggers": [{"trigger": "time", "at": "09:00:00"}],
            "actions": [{"service": "lock.lock", "target": {"entity_id": "lock.front_door"}}],
        }
        entities = [_entity("light.kitchen", "Kitchen Light")]
        patched, subs = _resolve_unknown_entity_ids(reason, automation, entities)
        assert patched is None
        assert subs == {}

    def test_no_entities_returns_none(self) -> None:
        patched, subs = _resolve_unknown_entity_ids(
            "automation references unknown entity_id(s): light.x", {}, None
        )
        assert patched is None
        assert subs == {}

    def test_reason_without_entity_ids_returns_none(self) -> None:
        patched, subs = _resolve_unknown_entity_ids(
            "some unrelated error", {}, [_entity("light.x", "X")]
        )
        assert patched is None
        assert subs == {}


class TestApplyEntitySubstitutions:
    """JSON tree walker for in-place entity_id substitution."""

    def test_swaps_target_entity_and_service_domain(self) -> None:
        node = {
            "service": "light.turn_on",
            "target": {"entity_id": "light.coffee_maker"},
        }
        _apply_entity_substitutions(node, {"light.coffee_maker": "switch.coffee_maker"})
        assert node["target"]["entity_id"] == "switch.coffee_maker"
        assert node["service"] == "switch.turn_on"

    def test_substitutes_in_nested_list(self) -> None:
        tree = {
            "actions": [
                {"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}},
                {"service": "light.turn_off", "target": {"entity_id": "light.kitchen"}},
            ]
        }
        _apply_entity_substitutions(tree, {"light.kitchen": "light.kitchen_relay"})
        for a in tree["actions"]:
            assert a["target"]["entity_id"] == "light.kitchen_relay"

    def test_handles_flat_entity_id_string(self) -> None:
        node = {"entity_id": "light.kitchen", "service": "light.turn_on"}
        _apply_entity_substitutions(node, {"light.kitchen": "switch.coffee_maker"})
        assert node["entity_id"] == "switch.coffee_maker"
        assert node["service"] == "switch.turn_on"

    def test_no_substitution_leaves_tree_intact(self) -> None:
        node = {"service": "light.turn_on", "target": {"entity_id": "light.x"}}
        _apply_entity_substitutions(node, {"light.y": "light.z"})
        assert node["target"]["entity_id"] == "light.x"
        assert node["service"] == "light.turn_on"

    def test_event_data_payload_not_rewritten(self) -> None:
        """``event_data`` is integration-defined payload, NOT an HA
        entity reference. A field literally named ``entity_id`` inside
        it is event-filter data; the substitution walker must not
        touch it even when an unrelated entity elsewhere is being
        corrected."""
        tree = {
            "triggers": [
                {
                    "trigger": "event",
                    "event_type": "custom_event",
                    "event_data": {"entity_id": "light.coffee_maker"},
                },
            ],
            "actions": [
                {"service": "light.turn_on", "target": {"entity_id": "light.coffee_maker"}},
            ],
        }
        _apply_entity_substitutions(tree, {"light.coffee_maker": "switch.coffee_maker"})
        # event_data payload untouched
        assert tree["triggers"][0]["event_data"]["entity_id"] == "light.coffee_maker"
        # real action target substituted
        assert tree["actions"][0]["target"]["entity_id"] == "switch.coffee_maker"
        assert tree["actions"][0]["service"] == "switch.turn_on"

    def test_unrelated_multi_target_action_is_not_rewritten(self) -> None:
        """A valid ``homeassistant.update_entity`` action targeting many
        sensors must NOT have its service rewritten to
        ``sensor.update_entity`` just because the recovery walks past it
        while substituting a DIFFERENT entity elsewhere in the tree."""
        tree = {
            "actions": [
                {
                    "service": "homeassistant.update_entity",
                    "target": {"entity_id": ["sensor.temperature", "sensor.humidity"]},
                },
                {
                    "service": "light.turn_on",
                    "target": {"entity_id": "light.coffee_maker"},
                },
            ]
        }
        _apply_entity_substitutions(tree, {"light.coffee_maker": "switch.coffee_maker"})
        # Unrelated action untouched
        assert tree["actions"][0]["service"] == "homeassistant.update_entity"
        assert tree["actions"][0]["target"]["entity_id"] == [
            "sensor.temperature",
            "sensor.humidity",
        ]
        # Substituted action did swap
        assert tree["actions"][1]["service"] == "switch.turn_on"
        assert tree["actions"][1]["target"]["entity_id"] == "switch.coffee_maker"

    def test_legacy_data_entity_id_substitution_swaps_service(self) -> None:
        """Action using the legacy ``data.entity_id`` shape (no
        ``target`` wrapper). When the nested id is substituted across
        domains, the parent service-domain must move with it."""
        node = {
            "service": "light.turn_on",
            "data": {"entity_id": "light.coffee_maker"},
        }
        _apply_entity_substitutions(node, {"light.coffee_maker": "switch.coffee_maker"})
        assert node["data"]["entity_id"] == "switch.coffee_maker"
        assert node["service"] == "switch.turn_on"

    def test_comma_separated_entity_id_string_is_split_and_substituted(self) -> None:
        """HA accepts ``entity_id: "light.a, light.b"`` as shorthand for
        a list. The substitution walker must split this form, apply
        per-id substitutions, and rejoin — otherwise unknown ids
        embedded in a comma-string never get patched."""
        node = {
            "service": "light.turn_on",
            "target": {"entity_id": "light.coffee_maker, light.kettle"},
        }
        _apply_entity_substitutions(
            node,
            {
                "light.coffee_maker": "switch.coffee_maker",
                "light.kettle": "switch.kettle",
            },
        )
        assert node["target"]["entity_id"] == "switch.coffee_maker, switch.kettle"
        assert node["service"] == "switch.turn_on"

    def test_comma_string_partial_substitution_preserves_unknowns(self) -> None:
        """When only some ids in the comma-string are in substitutions,
        the rest stay verbatim."""
        node = {
            "service": "light.turn_on",
            "entity_id": "light.coffee_maker, light.kitchen",
        }
        _apply_entity_substitutions(node, {"light.coffee_maker": "switch.coffee_maker"})
        # mixed-domain refusal is handled by the resolver; the walker
        # itself just applies substitutions.
        assert node["entity_id"] == "switch.coffee_maker, light.kitchen"

    def test_flat_entity_list_substitution_swaps_service(self) -> None:
        """Flat ``entity_id`` list (no ``target`` wrapper) whose entries
        move domain should also drag the service-domain along, so
        ``light.turn_on`` with ``entity_id: [light.coffee_maker]``
        becomes ``switch.turn_on`` with the new list."""
        node = {
            "service": "light.turn_on",
            "entity_id": ["light.coffee_maker"],
        }
        _apply_entity_substitutions(node, {"light.coffee_maker": "switch.coffee_maker"})
        assert node["entity_id"] == ["switch.coffee_maker"]
        assert node["service"] == "switch.turn_on"

    def test_list_target_partial_substitution_swaps_service(self) -> None:
        """When an entity_id list IS substituted and all members share a
        new domain, the service still moves with it."""
        node = {
            "service": "light.turn_on",
            "target": {"entity_id": ["light.coffee_maker", "light.kettle"]},
        }
        _apply_entity_substitutions(
            node,
            {"light.coffee_maker": "switch.coffee_maker", "light.kettle": "switch.kettle"},
        )
        assert node["target"]["entity_id"] == ["switch.coffee_maker", "switch.kettle"]
        assert node["service"] == "switch.turn_on"


class TestHumaniseUnknownEntityError:
    """User-facing rewrite of the validator's bare error."""

    def test_falls_back_when_no_entities(self) -> None:
        out = _humanise_unknown_entity_error("anything", None)
        assert "refine" in out.lower()

    def test_falls_back_when_not_unknown_entity_reason(self) -> None:
        out = _humanise_unknown_entity_error(
            "some other validation problem", [_entity("light.x", "X")]
        )
        assert "refine" in out.lower()

    def test_lists_user_devices_grouped_by_domain(self) -> None:
        reason = "automation references unknown entity_id(s): lock.front_door"
        entities = [
            _entity("light.kitchen", "Kitchen Light"),
            _entity("light.porch", "Porch Light"),
            _entity("switch.coffee_maker", "Coffee Maker"),
        ]
        out = _humanise_unknown_entity_error(reason, entities)
        assert "Kitchen Light" in out
        assert "Coffee Maker" in out
        assert "which device" in out.lower()

    def test_caps_three_friendly_names_per_domain_with_more_marker(self) -> None:
        reason = "automation references unknown entity_id(s): light.foo"
        entities = [_entity(f"light.l{i}", f"Light {i}") for i in range(5)]
        out = _humanise_unknown_entity_error(reason, entities)
        assert "+2 more" in out

    def test_domain_labels_are_correctly_pluralised(self) -> None:
        reason = "automation references unknown entity_id(s): cover.foo"
        entities = [
            _entity("switch.kettle", "Kettle"),
            _entity("climate.lounge", "Lounge AC"),
            _entity("media_player.tv", "Living Room TV"),
            _entity("scene.movie", "Movie Night"),
        ]
        out = _humanise_unknown_entity_error(reason, entities)
        assert "switches:" in out
        assert "switchs:" not in out
        assert "climate:" in out
        assert "climates:" not in out
        assert "media players:" in out
        assert "scenes:" in out


class TestNumericThresholdExtraction:
    """Pull (direction, value) from comparator phrasing in the prompt."""

    @pytest.mark.parametrize(
        ("prompt", "expected"),
        [
            ("when the temperature drops below 18", ("below", 18.0)),
            ("if it gets warmer than 26", ("above", 26.0)),
            ("when humidity rises above 80", ("above", 80.0)),
            ("if temperature is cooler than 16", ("below", 16.0)),
        ],
    )
    def test_extracts_direction_and_value(self, prompt: str, expected: tuple[str, float]) -> None:
        assert _extract_numeric_threshold(prompt) == expected

    @pytest.mark.parametrize(
        "prompt",
        [
            "",
            "just turn on the light",
            "what's the temperature?",
            # Ambiguous "to" phrasing has no directional context — must not
            # be coerced (e.g. "battery goes to 20%" usually describes a
            # falling battery, not an above-threshold crossing).
            "when the battery goes to 20%",
            "when humidity gets to 50",
        ],
    )
    def test_returns_none_when_no_comparator(self, prompt: str) -> None:
        assert _extract_numeric_threshold(prompt) is None

    @pytest.mark.parametrize(
        ("prompt", "expected"),
        [
            ("when the temperature drops to 16", ("below", 16.0)),
            ("when the temperature falls to 10", ("below", 10.0)),
            ("when humidity rises to 80", ("above", 80.0)),
        ],
    )
    def test_directional_to_phrasing(
        self, prompt: str, expected: tuple[str, float]
    ) -> None:
        assert _extract_numeric_threshold(prompt) == expected


class TestCoerceSunTriggers:
    """Synthesize a sun trigger when the prompt names a sun event."""

    def test_singular_trigger_wins_over_plural_during_coercion(self) -> None:
        """When both ``trigger`` (singular) and ``triggers`` (plural)
        keys are present, the validator reads the singular one — so
        coercion must operate on THAT, not the plural list. After
        commit the singular is dropped and the plural list contains the
        coerced singular value, not whatever was in the original
        plural list."""
        automation = {
            "trigger": {"trigger": "time", "at": "18:00:00"},
            "triggers": [{"trigger": "state", "entity_id": "binary_sensor.unrelated"}],
            "actions": [],
        }
        changed = _coerce_sun_triggers(automation, "turn on the porch light at sunset")
        assert changed
        # Singular ``trigger`` key dropped.
        assert "trigger" not in automation
        # Plural list now holds the coerced singular value (the time
        # trigger turned into a sun trigger). The pre-existing plural
        # ``binary_sensor.unrelated`` is NOT mixed in because it wasn't
        # the active trigger to begin with.
        assert automation["triggers"] == [{"trigger": "sun", "event": "sunset"}]

    def test_singular_trigger_key_is_dropped_after_coercion(self) -> None:
        """An automation using the legacy singular ``trigger`` key must
        have that key removed after coercion — otherwise the validator
        prioritizes it and the LoRA's guessed time trigger remains in
        force despite a successful sun coercion."""
        automation = {
            "trigger": {"trigger": "time", "at": "18:00:00"},
            "actions": [],
        }
        changed = _coerce_sun_triggers(automation, "turn on the porch light at sunset")
        assert changed
        assert "trigger" not in automation
        assert automation["triggers"][0]["trigger"] == "sun"
        assert automation["triggers"][0]["event"] == "sunset"

    def test_sunset_replaces_time_guess(self) -> None:
        automation = {
            "triggers": [{"trigger": "time", "at": "18:00:00"}],
            "actions": [{"service": "light.turn_on", "target": {"entity_id": "light.x"}}],
        }
        changed = _coerce_sun_triggers(automation, "turn on the kitchen light at sunset")
        assert changed
        assert automation["triggers"][0]["trigger"] == "sun"
        assert automation["triggers"][0]["event"] == "sunset"

    def test_sunrise_synthesises_when_no_trigger(self) -> None:
        automation = {"triggers": [], "actions": []}
        changed = _coerce_sun_triggers(automation, "wake me up at sunrise")
        assert changed
        assert automation["triggers"][0]["event"] == "sunrise"

    def test_no_sun_word_leaves_trigger_alone(self) -> None:
        automation = {
            "triggers": [{"trigger": "time", "at": "18:00:00"}],
            "actions": [],
        }
        changed = _coerce_sun_triggers(automation, "turn on the light at 7pm")
        assert not changed
        assert automation["triggers"][0]["trigger"] == "time"

    def test_existing_matching_sun_trigger_is_no_op(self) -> None:
        automation = {
            "triggers": [{"trigger": "sun", "event": "sunset"}],
            "actions": [],
        }
        changed = _coerce_sun_triggers(automation, "at sunset turn on the light")
        assert not changed

    def test_opposing_actions_two_events_not_merged(self) -> None:
        """P2 — "turn on at sunset and turn it off at sunrise" is a
        multi-action automation. A valid sunset/turn_on automation must
        NOT gain a sunrise trigger (would turn ON at sunrise too)."""
        automation = {
            "triggers": [{"trigger": "sun", "event": "sunset"}],
            "actions": [{"service": "light.turn_on", "target": {"entity_id": "light.porch"}}],
        }
        changed = _coerce_sun_triggers(
            automation,
            "turn on the porch light at sunset and turn it off at sunrise",
        )
        assert changed is False
        events = {t.get("event") for t in automation["triggers"]}
        assert events == {"sunset"}

    def test_same_action_two_events_still_merged(self) -> None:
        """Same action for both events ("turn off at sunset and sunrise")
        is safe to merge — both triggers share the one turn_off action."""
        automation = {
            "triggers": [{"trigger": "sun", "event": "sunset"}],
            "actions": [{"service": "light.turn_off", "target": {"entity_id": "light.porch"}}],
        }
        changed = _coerce_sun_triggers(
            automation,
            "turn off the porch light at sunset and at sunrise",
        )
        assert changed is True
        events = {t.get("event") for t in automation["triggers"]}
        assert events == {"sunset", "sunrise"}

    def test_after_sunset_condition_does_not_append_sun_trigger(self) -> None:
        """``after sunset`` scopes a sun condition, not a trigger. Must
        not append a sun trigger that would fire unconditionally at
        sunset on top of the real motion trigger."""
        automation = {
            "triggers": [
                {"trigger": "state", "entity_id": "binary_sensor.motion", "to": "on"},
            ],
            "actions": [],
        }
        changed = _coerce_sun_triggers(
            automation, "when motion is detected after sunset turn on the light"
        )
        assert not changed
        assert len(automation["triggers"]) == 1
        assert automation["triggers"][0]["entity_id"] == "binary_sensor.motion"

    def test_before_sunrise_condition_does_not_append_sun_trigger(self) -> None:
        automation = {
            "triggers": [
                {"trigger": "state", "entity_id": "binary_sensor.motion", "to": "on"},
            ],
            "actions": [],
        }
        changed = _coerce_sun_triggers(
            automation, "when motion is detected before sunrise alert me"
        )
        assert not changed
        assert len(automation["triggers"]) == 1

    def test_when_the_sun_sets_is_trigger_phrasing(self) -> None:
        automation = {"triggers": [], "actions": []}
        changed = _coerce_sun_triggers(automation, "when the sun sets, close the blinds")
        assert changed
        assert automation["triggers"][0]["event"] == "sunset"

    def test_if_motion_at_sunset_is_a_condition_not_trigger(self) -> None:
        """``If motion is detected at sunset`` — the motion is the
        trigger and sunset is a condition. Appending a sun trigger
        would OR with motion and fire unconditionally at sunset. Must
        refuse to synthesize."""
        automation = {
            "triggers": [
                {"trigger": "state", "entity_id": "binary_sensor.motion", "to": "on"},
            ],
            "actions": [],
        }
        changed = _coerce_sun_triggers(
            automation, "if motion is detected at sunset, turn on porch"
        )
        assert not changed
        assert len(automation["triggers"]) == 1
        assert automation["triggers"][0]["entity_id"] == "binary_sensor.motion"

    def test_when_x_happens_at_sunset_is_a_condition(self) -> None:
        automation = {
            "triggers": [
                {"trigger": "state", "entity_id": "binary_sensor.door", "to": "on"},
            ],
            "actions": [],
        }
        changed = _coerce_sun_triggers(
            automation, "when the door opens at sunset, turn on the light"
        )
        assert not changed
        assert len(automation["triggers"]) == 1

    def test_sunset_qualifies_only_the_nearest_clause(self) -> None:
        """``when motion occurs or when the door opens at sunset`` —
        sunset qualifies the door clause as a condition. The leading
        ``or`` is between the motion clause and the door clause, NOT
        between the door clause and the sun phrase, so it must not
        authorize an independent sun trigger."""
        automation = {
            "triggers": [
                {"trigger": "state", "entity_id": "binary_sensor.motion", "to": "on"},
                {"trigger": "state", "entity_id": "binary_sensor.door", "to": "on"},
            ],
            "actions": [],
        }
        changed = _coerce_sun_triggers(
            automation,
            "when motion occurs or when the door opens at sunset, alert me",
        )
        assert not changed
        assert len(automation["triggers"]) == 2
        platforms = {t.get("trigger") for t in automation["triggers"]}
        assert platforms == {"state"}

    def test_when_or_at_sunset_remains_a_trigger(self) -> None:
        """``or`` between the earlier trigger and the sun phrase signals
        OR semantics — sunset IS an independent trigger here."""
        automation = {
            "triggers": [
                {"trigger": "state", "entity_id": "binary_sensor.motion", "to": "on"},
            ],
            "actions": [],
        }
        changed = _coerce_sun_triggers(
            automation, "when motion is detected or at sunset, turn on light"
        )
        assert changed
        platforms = [t.get("trigger") for t in automation["triggers"]]
        assert "sun" in platforms
        assert "state" in platforms

    def test_conflicting_sun_trigger_is_replaced_not_duplicated(self) -> None:
        """Model emitted a sunrise trigger but the prompt asks for
        sunset. Appending a sunset trigger would leave the sunrise live
        and fire at both events. Correct the existing trigger in place."""
        automation = {
            "triggers": [{"trigger": "sun", "event": "sunrise"}],
            "actions": [],
        }
        changed = _coerce_sun_triggers(
            automation, "turn on the porch light at sunset"
        )
        assert changed
        assert len(automation["triggers"]) == 1
        assert automation["triggers"][0]["trigger"] == "sun"
        assert automation["triggers"][0]["event"] == "sunset"

    def test_second_sun_phrase_is_validated_as_trigger_vs_condition(self) -> None:
        """Compound request: ``At sunset turn the light on, and turn it
        off if it is still on at sunrise``. First phrase is a trigger
        (sunset), second is a condition on a different clause (sunrise
        after ``if``, no ``or``). Only sunset must be added."""
        automation = {"triggers": [], "actions": []}
        changed = _coerce_sun_triggers(
            automation,
            "At sunset turn the light on, and turn it off if it is still on at sunrise",
        )
        assert changed
        events = [t["event"] for t in automation["triggers"]]
        assert events == ["sunset"]

    def test_conjoined_sun_events_recognised_at_sunset_and_sunrise(self) -> None:
        """``at sunset and sunrise`` chains a bare event name onto the
        primary ``at sunset`` phrase. Both events must be captured so
        an existing sunrise trigger isn't classified as conflicting."""
        automation = {
            "triggers": [
                {"trigger": "sun", "event": "sunrise"},
                {"trigger": "sun", "event": "sunset"},
            ],
            "actions": [],
        }
        changed = _coerce_sun_triggers(
            automation, "alert me at sunset and sunrise"
        )
        assert not changed
        events = {t["event"] for t in automation["triggers"]}
        assert events == {"sunrise", "sunset"}

    def test_conjoined_sun_events_with_or(self) -> None:
        """``at sunrise or sunset`` chains via ``or``."""
        automation = {"triggers": [], "actions": []}
        changed = _coerce_sun_triggers(
            automation, "alert me at sunrise or sunset"
        )
        assert changed
        events = {t["event"] for t in automation["triggers"]}
        assert events == {"sunrise", "sunset"}

    def test_prompt_with_both_sun_events_preserves_both(self) -> None:
        """``at sunrise and at sunset`` names both events. An automation
        with both sun triggers must be left intact — neither is
        contradictory."""
        automation = {
            "triggers": [
                {"trigger": "sun", "event": "sunrise"},
                {"trigger": "sun", "event": "sunset"},
            ],
            "actions": [],
        }
        changed = _coerce_sun_triggers(
            automation, "alert me at sunrise and at sunset"
        )
        assert not changed
        events = {t["event"] for t in automation["triggers"]}
        assert events == {"sunrise", "sunset"}

    def test_prompt_with_both_sun_events_adds_missing(self) -> None:
        """Prompt names both events; automation only has sunrise →
        append sunset."""
        automation = {
            "triggers": [{"trigger": "sun", "event": "sunrise"}],
            "actions": [],
        }
        changed = _coerce_sun_triggers(
            automation, "alert me at sunrise and at sunset"
        )
        assert changed
        events = {t["event"] for t in automation["triggers"]}
        assert events == {"sunrise", "sunset"}

    def test_additive_refinement_keeps_existing_sun_trigger(self) -> None:
        """Refinement: prompt mentions sunset; automation already has
        sunrise AND sunset (user previously added sunrise, now refining).
        Must NOT delete sunrise — treat as additive, only synthesize
        what's missing. With sunset already present → no-op."""
        automation = {
            "triggers": [
                {"trigger": "sun", "event": "sunrise"},
                {"trigger": "sun", "event": "sunset"},
            ],
            "actions": [],
        }
        changed = _coerce_sun_triggers(automation, "at sunset turn on the light")
        assert not changed
        events = {t["event"] for t in automation["triggers"]}
        assert events == {"sunrise", "sunset"}

    def test_additive_refinement_adds_missing_sun_trigger(self) -> None:
        """Refinement: prompt says "also run at sunrise"; automation
        already has sunset. Append sunrise; keep sunset."""
        automation = {
            "triggers": [{"trigger": "sun", "event": "sunset"}],
            "actions": [],
        }
        changed = _coerce_sun_triggers(automation, "also run at sunrise")
        assert changed
        events = {t["event"] for t in automation["triggers"]}
        assert events == {"sunrise", "sunset"}

    def test_explicit_time_in_prompt_preserves_time_trigger(self) -> None:
        """Prompt explicitly mentions both sunset AND a clock time
        ("at sunset or at 10 PM"). The existing 10 PM time trigger is
        intentional, NOT a guess — the sun trigger must be appended,
        not used to clobber the time one."""
        automation = {
            "triggers": [{"trigger": "time", "at": "22:00:00"}],
            "actions": [],
        }
        changed = _coerce_sun_triggers(
            automation, "turn on the porch light at sunset or at 10 PM"
        )
        assert changed
        platforms = [t.get("trigger") for t in automation["triggers"]]
        assert "sun" in platforms
        assert "time" in platforms
        time_tr = next(t for t in automation["triggers"] if t.get("trigger") == "time")
        assert time_tr["at"] == "22:00:00"

    def test_sun_condition_with_motion_trigger_not_widened(self) -> None:
        """P2 — "At sunset, turn on the lights if motion is detected": the
        model emitted a valid motion trigger; sunset is a CONDITION. A
        sun trigger must NOT be appended (HA ORs triggers → would fire on
        motion at any time). No "or" → refuse the append."""
        automation = {
            "triggers": [
                {"trigger": "state", "entity_id": "binary_sensor.motion", "to": "on"},
            ],
            "actions": [{"service": "light.turn_on", "target": {"entity_id": "light.x"}}],
        }
        changed = _coerce_sun_triggers(
            automation, "At sunset, turn on the lights if motion is detected"
        )
        assert changed is False
        platforms = [t.get("trigger") for t in automation["triggers"]]
        assert "sun" not in platforms
        assert platforms == ["state"]

    def test_preserves_unrelated_triggers_appending_sun(self) -> None:
        """Multi-trigger automation must keep its motion trigger when the
        prompt also mentions sunset — sun trigger is appended, not used
        to clobber the rest of the list."""
        automation = {
            "triggers": [
                {"trigger": "state", "entity_id": "binary_sensor.motion", "to": "on"},
            ],
            "actions": [],
        }
        changed = _coerce_sun_triggers(
            automation, "at sunset or when motion is detected, turn on the light"
        )
        assert changed
        triggers = automation["triggers"]
        platforms = [t.get("trigger") for t in triggers]
        assert "sun" in platforms
        # Motion trigger preserved
        motion = next(t for t in triggers if t.get("entity_id") == "binary_sensor.motion")
        assert motion["to"] == "on"


class TestCoerceNumericStateTriggers:
    """Rewrite state triggers to numeric_state when prompt has a comparator."""

    def test_singular_trigger_key_is_dropped_after_numeric_coercion(self) -> None:
        automation = {
            "trigger": {"trigger": "state", "entity_id": "sensor.temperature"},
            "actions": [],
        }
        changed = _coerce_numeric_state_triggers(
            automation, "when the temperature drops below 18"
        )
        assert changed
        assert "trigger" not in automation
        assert automation["triggers"][0]["trigger"] == "numeric_state"
        assert automation["triggers"][0]["below"] == 18.0

    def test_state_trigger_becomes_numeric_state(self) -> None:
        automation = {
            "triggers": [{"trigger": "state", "entity_id": "sensor.temperature"}],
            "actions": [],
        }
        changed = _coerce_numeric_state_triggers(
            automation, "when the temperature drops below 18, do something"
        )
        assert changed
        t = automation["triggers"][0]
        assert t["trigger"] == "numeric_state"
        assert t["entity_id"] == "sensor.temperature"
        assert t.get("below") == 18.0

    def test_no_comparator_no_change(self) -> None:
        automation = {
            "triggers": [{"trigger": "state", "entity_id": "sensor.x"}],
            "actions": [],
        }
        changed = _coerce_numeric_state_triggers(automation, "do a thing")
        assert not changed

    def test_existing_numeric_state_is_skipped(self) -> None:
        automation = {
            "triggers": [{"trigger": "numeric_state", "entity_id": "sensor.x", "above": 5}],
            "actions": [],
        }
        changed = _coerce_numeric_state_triggers(automation, "when x rises above 10, do something")
        assert not changed

    def test_does_not_clobber_binary_sensor_motion_trigger(self) -> None:
        """A motion state-trigger alongside a temperature numeric phrase
        must NOT be rewritten as numeric_state above N — binary_sensor
        domains are not numeric-eligible."""
        automation = {
            "triggers": [
                {"trigger": "state", "entity_id": "binary_sensor.motion", "to": "on"},
                {"trigger": "state", "entity_id": "sensor.temperature"},
            ],
            "actions": [],
        }
        changed = _coerce_numeric_state_triggers(
            automation,
            "when motion is detected or temperature rises above 25",
        )
        assert changed
        motion = automation["triggers"][0]
        assert motion["trigger"] == "state"
        assert motion["entity_id"] == "binary_sensor.motion"
        temp = automation["triggers"][1]
        assert temp["trigger"] == "numeric_state"
        assert temp["above"] == 25.0

    def test_climate_state_trigger_without_attribute_refused(self) -> None:
        """``climate.*`` primary state is a mode string ("heat"/"off").
        Without an ``attribute`` field naming the numeric reading, the
        resulting numeric_state trigger would compare a string against
        a number and never fire — refuse coercion."""
        automation = {
            "triggers": [{"trigger": "state", "entity_id": "climate.thermostat"}],
            "actions": [],
        }
        changed = _coerce_numeric_state_triggers(
            automation, "when temperature drops below 18"
        )
        assert not changed
        assert automation["triggers"][0]["trigger"] == "state"

    def test_weather_state_trigger_without_attribute_refused(self) -> None:
        """``weather.*`` primary state is a condition string
        ("sunny"/"cloudy"). Same logic as climate."""
        automation = {
            "triggers": [{"trigger": "state", "entity_id": "weather.home"}],
            "actions": [],
        }
        changed = _coerce_numeric_state_triggers(
            automation, "when temperature drops below 18"
        )
        assert not changed
        assert automation["triggers"][0]["trigger"] == "state"

    def test_preserves_attribute_on_state_trigger(self) -> None:
        """``climate.thermostat`` state is a mode string; the numeric
        reading lives on ``current_temperature``. The ``attribute`` key
        must survive the coercion or numeric_state would compare the
        mode string against a number and never fire."""
        automation = {
            "triggers": [
                {
                    "trigger": "state",
                    "entity_id": "climate.thermostat",
                    "attribute": "current_temperature",
                },
            ],
            "actions": [],
        }
        changed = _coerce_numeric_state_triggers(
            automation, "when the temperature drops below 18"
        )
        assert changed
        tr = automation["triggers"][0]
        assert tr["trigger"] == "numeric_state"
        assert tr["entity_id"] == "climate.thermostat"
        assert tr["attribute"] == "current_temperature"
        assert tr["below"] == 18.0

    def test_preserves_for_duration_on_state_trigger(self) -> None:
        automation = {
            "triggers": [
                {
                    "trigger": "state",
                    "entity_id": "sensor.temperature",
                    "for": {"minutes": 5},
                }
            ],
            "actions": [],
        }
        changed = _coerce_numeric_state_triggers(
            automation, "when temperature drops below 18"
        )
        assert changed
        tr = automation["triggers"][0]
        assert tr["trigger"] == "numeric_state"
        assert tr["below"] == 18.0
        assert tr["for"] == {"minutes": 5}

    def test_refuses_when_prompt_has_multiple_comparators(self) -> None:
        """Ambiguous which trigger gets which threshold — refuse to coerce
        rather than apply the first comparator to every eligible trigger."""
        automation = {
            "triggers": [
                {"trigger": "state", "entity_id": "sensor.temperature"},
                {"trigger": "state", "entity_id": "sensor.humidity"},
            ],
            "actions": [],
        }
        changed = _coerce_numeric_state_triggers(
            automation, "when temperature above 25 or humidity below 40, alert me"
        )
        assert not changed
        for t in automation["triggers"]:
            assert t["trigger"] == "state"

    def test_refuses_when_multiple_eligible_triggers_single_comparator(self) -> None:
        """One comparator, two eligible triggers — can't tell which gets it."""
        automation = {
            "triggers": [
                {"trigger": "state", "entity_id": "sensor.temperature"},
                {"trigger": "state", "entity_id": "sensor.outdoor_temp"},
            ],
            "actions": [],
        }
        changed = _coerce_numeric_state_triggers(automation, "when temperature drops below 18")
        assert not changed
        for t in automation["triggers"]:
            assert t["trigger"] == "state"

    def test_ignores_non_state_platforms(self) -> None:
        automation = {
            "triggers": [
                {"trigger": "sun", "event": "sunset"},
                {"trigger": "time", "at": "18:00:00"},
            ],
            "actions": [],
        }
        changed = _coerce_numeric_state_triggers(
            automation, "when temperature drops below 18"
        )
        assert not changed
        assert automation["triggers"][0]["trigger"] == "sun"
        assert automation["triggers"][1]["trigger"] == "time"

    def test_comparator_already_on_numeric_trigger_leaves_others_alone(self) -> None:
        """Automation already has ``numeric_state above 25`` for
        temperature plus an unqualified state trigger for humidity.
        The prompt's "above 25" is already attached — must not rewrite
        humidity to the same threshold."""
        automation = {
            "triggers": [
                {
                    "trigger": "numeric_state",
                    "entity_id": "sensor.temperature",
                    "above": 25.0,
                },
                {"trigger": "state", "entity_id": "sensor.humidity"},
            ],
            "actions": [],
        }
        changed = _coerce_numeric_state_triggers(
            automation, "when temperature is above 25 or humidity changes"
        )
        assert not changed
        assert automation["triggers"][0]["trigger"] == "numeric_state"
        assert automation["triggers"][0]["above"] == 25.0
        assert automation["triggers"][1]["trigger"] == "state"
        assert "above" not in automation["triggers"][1]

    def test_state_trigger_with_explicit_to_is_left_alone(self) -> None:
        """The numeric phrase belongs to the action clause, not the
        trigger. A thermostat trigger with ``to: off`` must not be
        clobbered to ``below: 18``."""
        automation = {
            "triggers": [
                {"trigger": "state", "entity_id": "climate.thermostat", "to": "off"},
            ],
            "actions": [],
        }
        changed = _coerce_numeric_state_triggers(
            automation, "when the thermostat turns off, set the target below 18"
        )
        assert not changed
        tr = automation["triggers"][0]
        assert tr["trigger"] == "state"
        assert tr["to"] == "off"

    def test_numeric_in_action_clause_does_not_coerce_trigger(self) -> None:
        """Numeric value sits after the comma in the action clause. The
        trigger fires on any temperature change — must NOT be rewritten
        to ``numeric_state below: 18``."""
        automation = {
            "triggers": [
                {"trigger": "state", "entity_id": "sensor.outdoor_temperature"},
            ],
            "actions": [],
        }
        changed = _coerce_numeric_state_triggers(
            automation,
            "when the outdoor temperature changes, set the thermostat below 18",
        )
        assert not changed
        tr = automation["triggers"][0]
        assert tr["trigger"] == "state"
        assert "below" not in tr

    def test_undelimited_action_text_does_not_coerce_trigger(self) -> None:
        """Prompt has no punctuation. Trigger-clause extraction must
        still detect the action boundary at the imperative verb
        ("set"), so "when the outdoor temperature changes set the
        thermostat below 18" → trigger clause is "when the outdoor
        temperature changes" → no comparator → refuse."""
        automation = {
            "triggers": [
                {"trigger": "state", "entity_id": "sensor.outdoor_temperature"},
            ],
            "actions": [],
        }
        changed = _coerce_numeric_state_triggers(
            automation,
            "when the outdoor temperature changes set the thermostat below 18",
        )
        assert not changed
        tr = automation["triggers"][0]
        assert tr["trigger"] == "state"
        assert "below" not in tr

    def test_leading_action_numeric_does_not_coerce_trigger(self) -> None:
        """No comma: "Set the thermostat below 18 when the outdoor
        temperature changes" — anchor at ``when`` so the trigger clause
        is "when the outdoor temperature changes" (no comparator).
        Action's "below 18" must NOT be attributed to the trigger."""
        automation = {
            "triggers": [
                {"trigger": "state", "entity_id": "sensor.outdoor_temperature"},
            ],
            "actions": [],
        }
        changed = _coerce_numeric_state_triggers(
            automation,
            "Set the thermostat below 18 when the outdoor temperature changes",
        )
        assert not changed
        tr = automation["triggers"][0]
        assert tr["trigger"] == "state"
        assert "below" not in tr

    def test_leading_imperative_keeps_trigger_comparator(self) -> None:
        """The prompt leads with an action verb but no comma — the
        trigger comparator at the tail is still attributable to the
        trigger because there is no action-clause boundary in front of
        it. We still coerce."""
        automation = {
            "triggers": [
                {"trigger": "state", "entity_id": "sensor.temperature"},
            ],
            "actions": [],
        }
        changed = _coerce_numeric_state_triggers(
            automation, "Turn on the heater when temperature drops below 18"
        )
        assert changed
        assert automation["triggers"][0]["trigger"] == "numeric_state"
        assert automation["triggers"][0]["below"] == 18.0

    def test_state_trigger_with_explicit_from_is_left_alone(self) -> None:
        automation = {
            "triggers": [
                {
                    "trigger": "state",
                    "entity_id": "sensor.temperature",
                    "from": "unknown",
                },
            ],
            "actions": [],
        }
        changed = _coerce_numeric_state_triggers(
            automation, "when temperature drops below 18"
        )
        assert not changed
        assert automation["triggers"][0]["from"] == "unknown"


class TestMatchPresenceForDuration:
    """Regex split between affirmative and negative presence phrasings."""

    def test_negative_phrasing_returns_not_positive(self) -> None:
        m = _match_presence_for_duration("when nobody is in the kitchen for 10 minutes")
        assert m is not None
        _, is_positive = m
        assert is_positive is False

    def test_positive_phrasing_returns_positive(self) -> None:
        m = _match_presence_for_duration("when someone is in the kitchen for 10 minutes")
        assert m is not None
        _, is_positive = m
        assert is_positive is True

    def test_room_is_empty_is_negative(self) -> None:
        m = _match_presence_for_duration("when the kitchen is empty for 10 minutes")
        assert m is not None
        _, is_positive = m
        assert is_positive is False

    def test_no_match_returns_none(self) -> None:
        assert _match_presence_for_duration("turn on the porch light at sunset") is None

    def test_has_presence_for_duration_covers_all_variants(self) -> None:
        assert _has_presence_for_duration("when nobody is home for 5 minutes")
        assert _has_presence_for_duration("if anyone is in the office for 3 hours")
        assert _has_presence_for_duration("when the bedroom is empty for 30 minutes")
        assert not _has_presence_for_duration("turn the light off at 10pm")


class TestFindPresenceEntity:
    """Room-named prompts must resolve to a room-matching entity."""

    def test_room_match_wins(self) -> None:
        hass = _FakeHass(
            [
                _FakeState("binary_sensor.kitchen_occupancy", "Kitchen Occupancy"),
                _FakeState("binary_sensor.bedroom_occupancy", "Bedroom Occupancy"),
            ]
        )
        eid = _find_presence_entity(hass, "when nobody is in the kitchen for 10 minutes")
        assert eid == "binary_sensor.kitchen_occupancy"

    def test_room_without_matching_entity_returns_none(self) -> None:
        """P1#2 — kitchen prompt with only a bedroom sensor returns None
        rather than silently picking the bedroom sensor."""
        hass = _FakeHass([_FakeState("binary_sensor.bedroom_occupancy", "Bedroom Occupancy")])
        eid = _find_presence_entity(hass, "when nobody is in the kitchen for 10 minutes")
        assert eid is None

    def test_room_without_matching_entity_ignores_person(self) -> None:
        """A household-level ``person.*`` is not a substitute for a missing
        room-specific sensor when the prompt names a room."""
        hass = _FakeHass([_FakeState("person.gunnar", "Gunnar")])
        eid = _find_presence_entity(hass, "when nobody is in the kitchen for 10 minutes")
        assert eid is None

    def test_no_room_prompt_accepts_household_group(self) -> None:
        """Whole-household prompts pick a ``group.*`` aggregate so the
        trigger fires only when EVERY member is away — an individual
        ``person.*`` would fire when one resident leaves while others
        remain home, the opposite of "nobody is home"."""
        hass = _FakeHass(
            [
                _FakeState(
                    "group.all_persons",
                    "All Persons",
                    members=["person.gunnar", "person.alice"],
                ),
                _FakeState("person.gunnar", "Gunnar"),
                _FakeState("person.alice", "Alice"),
            ]
        )
        eid = _find_presence_entity(hass, "when nobody is home for 10 minutes")
        assert eid == "group.all_persons"

    def test_no_room_prompt_rejects_non_person_group(self) -> None:
        """P1 — ``group.all_lights`` reports ``on``/``off``, never
        ``not_home``. A synthesized ``to: not_home`` trigger against it
        would never fire — return None instead of selecting it."""
        hass = _FakeHass(
            [
                _FakeState(
                    "group.all_lights",
                    "All Lights",
                    members=["light.kitchen", "light.bedroom"],
                ),
                _FakeState("light.kitchen", "Kitchen"),
            ]
        )
        eid = _find_presence_entity(hass, "when nobody is home for 10 minutes")
        assert eid is None

    def test_no_room_prompt_rejects_empty_group(self) -> None:
        """A group with no members reveals nothing about presence —
        refuse rather than guess."""
        hass = _FakeHass([_FakeState("group.empty", "Empty Group")])
        eid = _find_presence_entity(hass, "when nobody is home for 10 minutes")
        assert eid is None

    def test_no_room_prompt_with_only_individual_person_refuses(self) -> None:
        """P1#4 — no household-aggregate source → refuse rather than
        pick a single ``person.*`` and silently narrow the meaning."""
        hass = _FakeHass([_FakeState("person.gunnar", "Gunnar")])
        eid = _find_presence_entity(hass, "when nobody is home for 10 minutes")
        assert eid is None

    def test_no_room_prompt_rejects_room_specific_sensor(self) -> None:
        """Without a room hint, a room-specific occupancy sensor would
        silently narrow the scope — return None instead."""
        hass = _FakeHass([_FakeState("binary_sensor.bedroom_occupancy", "Bedroom Occupancy")])
        eid = _find_presence_entity(hass, "when nobody is home for 10 minutes")
        assert eid is None

    def test_action_room_does_not_leak_into_presence_selection(self) -> None:
        """P1 — action target's room must not be picked as the presence
        room. "turn off the kitchen lights when nobody is home for 10
        minutes" → presence is household-wide, NOT kitchen-occupancy."""
        hass = _FakeHass(
            [
                _FakeState("binary_sensor.kitchen_occupancy", "Kitchen Occupancy"),
                _FakeState(
                    "group.all_persons",
                    "All Persons",
                    members=["person.gunnar"],
                ),
            ]
        )
        eid = _find_presence_entity(
            hass,
            "turn off the kitchen lights when nobody is home for 10 minutes",
        )
        assert eid == "group.all_persons"

    def test_action_room_does_not_leak_when_no_household_group(self) -> None:
        """Same prompt with NO household-aggregate present should
        refuse, NOT pick up the action-target room's occupancy sensor."""
        hass = _FakeHass(
            [
                _FakeState("binary_sensor.kitchen_occupancy", "Kitchen Occupancy"),
            ]
        )
        eid = _find_presence_entity(
            hass,
            "turn off the kitchen lights when nobody is home for 10 minutes",
        )
        assert eid is None

    def test_room_non_presence_binary_sensor_rejected(self) -> None:
        """P1 — a room-matching binary_sensor that is NOT presence-class
        (window/door) must not be selected. ``to: off`` on a window
        would mean 'window closed', not 'nobody present'."""
        hass = _FakeHass(
            [_FakeState("binary_sensor.kitchen_window", "Kitchen Window")]
        )
        eid = _find_presence_entity(hass, "when nobody is in the kitchen for 10 minutes")
        assert eid is None

    def test_room_presence_via_device_class(self) -> None:
        """A room sensor with no presence keyword in its name but an
        occupancy ``device_class`` is accepted."""
        hass = _FakeHass(
            [
                _FakeState(
                    "binary_sensor.kitchen_sensor_3",
                    "Kitchen Sensor 3",
                    device_class="occupancy",
                )
            ]
        )
        eid = _find_presence_entity(hass, "when nobody is in the kitchen for 10 minutes")
        assert eid == "binary_sensor.kitchen_sensor_3"

    def test_room_light_group_rejected(self) -> None:
        """P1 — a room-matching light group ("group.kitchen_lights")
        reports on/off, never home/not_home. Must NOT be selected as the
        kitchen presence source — a synthesized to:not_home trigger on it
        would never fire."""
        hass = _FakeHass(
            [
                _FakeState(
                    "group.kitchen_lights",
                    "Kitchen Lights",
                    members=["light.kitchen_1", "light.kitchen_2"],
                )
            ]
        )
        eid = _find_presence_entity(hass, "when nobody is in the kitchen for 10 minutes")
        assert eid is None

    def test_unavailable_sensor_skipped_for_live_one(self) -> None:
        """P2 — an unavailable occupancy sensor must be skipped in favour
        of a live lower-ranked motion sensor in the same room. A trigger
        on a dead sensor validates but never fires."""
        hass = _FakeHass(
            [
                _FakeState(
                    "binary_sensor.kitchen_occupancy",
                    "Kitchen Occupancy",
                    state="unavailable",
                ),
                _FakeState(
                    "binary_sensor.kitchen_motion",
                    "Kitchen Motion",
                    state="off",
                ),
            ]
        )
        eid = _find_presence_entity(hass, "when nobody is in the kitchen for 10 minutes")
        assert eid == "binary_sensor.kitchen_motion"

    def test_all_room_sensors_unavailable_refuses(self) -> None:
        """No live presence sensor in the room → refuse."""
        hass = _FakeHass(
            [
                _FakeState(
                    "binary_sensor.kitchen_occupancy",
                    "Kitchen Occupancy",
                    state="unavailable",
                ),
            ]
        )
        eid = _find_presence_entity(hass, "when nobody is in the kitchen for 10 minutes")
        assert eid is None

    def test_room_person_group_accepted(self) -> None:
        """A room-matching person group IS a valid presence source."""
        hass = _FakeHass(
            [
                _FakeState(
                    "group.kitchen_people",
                    "Kitchen People",
                    members=["person.gunnar"],
                )
            ]
        )
        eid = _find_presence_entity(hass, "when nobody is in the kitchen for 10 minutes")
        assert eid == "group.kitchen_people"

    def test_leading_connector_splits_condition_clause(self) -> None:
        """P1 — connector at message START splits correctly. "When
        nobody is home for 10 minutes, turn off the kitchen lights" →
        presence is household-wide, NOT kitchen."""
        hass = _FakeHass(
            [
                _FakeState("binary_sensor.kitchen_occupancy", "Kitchen Occupancy"),
                _FakeState(
                    "group.all_persons",
                    "All Persons",
                    members=["person.gunnar"],
                ),
            ]
        )
        eid = _find_presence_entity(
            hass,
            "When nobody is home for 10 minutes, turn off the kitchen lights",
        )
        assert eid == "group.all_persons"

    def test_leading_connector_no_punctuation_splits(self) -> None:
        """P2 — leading connector with NO comma/"then": "When nobody is
        home for 10 minutes turn off the kitchen lights". The condition
        ends at "for 10 minutes"; "kitchen" in the action clause must NOT
        leak into the presence-room scan → household group."""
        hass = _FakeHass(
            [
                _FakeState("binary_sensor.kitchen_occupancy", "Kitchen Occupancy"),
                _FakeState(
                    "group.all_persons",
                    "All Persons",
                    members=["person.gunnar"],
                ),
            ]
        )
        eid = _find_presence_entity(
            hass,
            "When nobody is home for 10 minutes turn off the kitchen lights",
        )
        assert eid == "group.all_persons"

    def test_user_defined_room_resolved_from_live_entities(self) -> None:
        """P1 — a room absent from the static list ("conservatory") still
        resolves to its occupancy sensor via the live-entity room
        vocabulary, NOT falling back to household-wide presence."""
        hass = _FakeHass(
            [
                _FakeState(
                    "binary_sensor.conservatory_occupancy",
                    "Conservatory Occupancy",
                ),
                _FakeState(
                    "group.all_persons",
                    "All Persons",
                    members=["person.gunnar"],
                ),
            ]
        )
        eid = _find_presence_entity(
            hass, "when nobody is in the conservatory for 10 minutes"
        )
        assert eid == "binary_sensor.conservatory_occupancy"


class TestCoercePresenceForDurationTrigger:
    """End-to-end coercion: presence + duration → state trigger with ``for:``."""

    def test_negative_kitchen_emits_off_with_for(self) -> None:
        hass = _FakeHass(
            [_FakeState("binary_sensor.kitchen_occupancy", "Kitchen Occupancy")]
        )
        automation = {
            "triggers": [{"trigger": "time", "at": "10:00:00"}],
            "actions": [
                {"service": "light.turn_off", "target": {"entity_id": "light.kitchen"}}
            ],
        }
        changed = _coerce_presence_for_duration_trigger(
            automation, "turn off the kitchen light when nobody is in the kitchen for 10 minutes", hass
        )
        assert changed is True
        triggers = automation["triggers"]
        # Time trigger that encoded the duration is dropped; synthesized
        # state trigger replaces it.
        assert len(triggers) == 1
        tr = triggers[0]
        assert tr["trigger"] == "state"
        assert tr["entity_id"] == "binary_sensor.kitchen_occupancy"
        assert tr["to"] == "off"
        assert tr["for"] == {"minutes": 10}

    def test_positive_kitchen_emits_on_with_for(self) -> None:
        """P1#1 — affirmative phrasing must emit ``to: on``, not ``to: off``."""
        hass = _FakeHass(
            [_FakeState("binary_sensor.kitchen_occupancy", "Kitchen Occupancy")]
        )
        automation = {
            "triggers": [{"trigger": "time", "at": "10:00:00"}],
            "actions": [
                {"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}}
            ],
        }
        changed = _coerce_presence_for_duration_trigger(
            automation, "turn on the kitchen light when someone is in the kitchen for 10 minutes", hass
        )
        assert changed is True
        tr = automation["triggers"][0]
        assert tr["to"] == "on"
        assert tr["for"] == {"minutes": 10}

    def test_positive_whole_home_emits_home(self) -> None:
        hass = _FakeHass(
            [
                _FakeState(
                    "group.all_persons",
                    "All Persons",
                    members=["person.gunnar"],
                )
            ]
        )
        automation = {
            "triggers": [{"trigger": "time", "at": "10:00:00"}],
            "actions": [{"service": "light.turn_on", "target": {"entity_id": "light.hall"}}],
        }
        changed = _coerce_presence_for_duration_trigger(
            automation, "when anyone is home for 10 minutes", hass
        )
        assert changed is True
        tr = automation["triggers"][0]
        assert tr["entity_id"] == "group.all_persons"
        assert tr["to"] == "home"

    def test_refuses_when_no_room_match(self) -> None:
        """P1#2 — kitchen prompt with only bedroom sensor refuses recovery."""
        hass = _FakeHass(
            [_FakeState("binary_sensor.bedroom_occupancy", "Bedroom Occupancy")]
        )
        automation = {
            "triggers": [{"trigger": "time", "at": "10:00:00"}],
            "actions": [
                {"service": "light.turn_off", "target": {"entity_id": "light.kitchen"}}
            ],
        }
        changed = _coerce_presence_for_duration_trigger(
            automation, "turn off the kitchen light when nobody is in the kitchen for 10 minutes", hass
        )
        assert changed is False
        # Original time trigger is left alone for the caller to clarify.
        assert automation["triggers"] == [{"trigger": "time", "at": "10:00:00"}]

    def test_refuses_when_no_presence_entity_at_all(self) -> None:
        """P1#3 — no presence/occupancy in the home → refuse, do NOT fall
        back to using the controlled device as the trigger entity."""
        hass = _FakeHass([_FakeState("light.porch", "Porch Light")])
        automation = {
            "triggers": [{"trigger": "time", "at": "10:00:00"}],
            "actions": [
                {"service": "light.turn_off", "target": {"entity_id": "light.porch"}}
            ],
        }
        changed = _coerce_presence_for_duration_trigger(
            automation, "turn off the porch light when nobody is there for 10 minutes", hass
        )
        assert changed is False

    def test_trigger_entity_is_single_string_not_comma(self) -> None:
        """P1#4 — the synthesized trigger watches ONLY the presence entity,
        not a comma-string that also watches the action target."""
        hass = _FakeHass(
            [_FakeState("binary_sensor.kitchen_occupancy", "Kitchen Occupancy")]
        )
        automation = {
            "triggers": [{"trigger": "time", "at": "10:00:00"}],
            "actions": [
                {"service": "light.turn_off", "target": {"entity_id": "light.kitchen"}}
            ],
        }
        _coerce_presence_for_duration_trigger(
            automation, "turn off the kitchen light when nobody is in the kitchen for 10 minutes", hass
        )
        eid = automation["triggers"][0]["entity_id"]
        assert "," not in eid
        assert eid == "binary_sensor.kitchen_occupancy"

    def test_preserves_compound_triggers_and_conditions(self) -> None:
        """P1#5 — compound shape ("…or at sunset") and explicit conditions
        survive the coercion. Only the LoRA's duration-misread time
        trigger is dropped."""
        hass = _FakeHass(
            [_FakeState("binary_sensor.kitchen_occupancy", "Kitchen Occupancy")]
        )
        sunset_trigger = {"trigger": "sun", "event": "sunset"}
        condition = {"condition": "state", "entity_id": "light.kitchen", "state": "on"}
        automation = {
            "triggers": [
                {"trigger": "time", "at": "10:00:00"},  # LoRA misread
                sunset_trigger,
            ],
            "conditions": [condition],
            "actions": [
                {"service": "light.turn_off", "target": {"entity_id": "light.kitchen"}}
            ],
        }
        changed = _coerce_presence_for_duration_trigger(
            automation,
            "turn off the kitchen light when nobody is in the kitchen for 10 minutes or at sunset",
            hass,
        )
        assert changed is True
        triggers = automation["triggers"]
        # Sunset preserved; LoRA's misread time trigger removed; presence
        # trigger appended.
        assert sunset_trigger in triggers
        assert all(
            not (t.get("trigger") == "time" and t.get("at") == "10:00:00") for t in triggers
        )
        assert any(
            t.get("trigger") == "state"
            and t.get("entity_id") == "binary_sensor.kitchen_occupancy"
            for t in triggers
        )
        # Conditions are preserved.
        assert automation["conditions"] == [condition]

    def test_no_match_returns_false_without_mutation(self) -> None:
        hass = _FakeHass([_FakeState("binary_sensor.kitchen_occupancy", "Kitchen")])
        automation = {
            "triggers": [{"trigger": "time", "at": "10:00:00"}],
            "actions": [],
        }
        original = {
            "triggers": list(automation["triggers"]),
            "actions": list(automation["actions"]),
        }
        changed = _coerce_presence_for_duration_trigger(
            automation, "turn on the kitchen light at sunset", hass
        )
        assert changed is False
        assert automation["triggers"] == original["triggers"]

    def test_everyone_phrasing_refused(self) -> None:
        """P2 — "everyone is home" requires ALL members present; a
        default person-group ``to: home`` fires when ANY member arrives.
        Coercion refuses rather than emit a misleading trigger."""
        hass = _FakeHass(
            [
                _FakeState(
                    "group.all_persons",
                    "All Persons",
                    members=["person.gunnar", "person.alice"],
                )
            ]
        )
        automation = {
            "triggers": [{"trigger": "time", "at": "10:00:00"}],
            "actions": [{"service": "light.turn_on", "target": {"entity_id": "light.hall"}}],
        }
        changed = _coerce_presence_for_duration_trigger(
            automation, "when everyone is home for 10 minutes", hass
        )
        assert changed is False

    def test_if_presence_with_primary_trigger_becomes_condition(self) -> None:
        """P1 — "At sunset, turn on the lights if nobody is home for 10
        minutes": presence is a GATE on the sunset trigger, not its own
        trigger. Emit it as a state condition; keep sunset as trigger."""
        hass = _FakeHass(
            [
                _FakeState(
                    "group.all_persons",
                    "All Persons",
                    members=["person.gunnar"],
                )
            ]
        )
        automation = {
            "triggers": [{"trigger": "sun", "event": "sunset"}],
            "actions": [{"service": "light.turn_on", "target": {"entity_id": "light.hall"}}],
        }
        changed = _coerce_presence_for_duration_trigger(
            automation,
            "At sunset, turn on the lights if nobody is home for 10 minutes",
            hass,
        )
        assert changed is True
        # Sunset stays as the only trigger.
        triggers = automation["triggers"]
        assert len(triggers) == 1
        assert triggers[0]["trigger"] == "sun"
        # Presence shipped as a state condition with the for: window.
        conditions = automation["conditions"]
        assert len(conditions) == 1
        cond = conditions[0]
        assert cond["condition"] == "state"
        assert cond["entity_id"] == "group.all_persons"
        assert cond["state"] == "not_home"
        assert cond["for"] == {"minutes": 10}

    def test_explicit_time_matching_duration_preserved(self) -> None:
        """P1 — "At 10:00, turn off the lights if nobody is home for 10
        minutes": the 10:00 trigger is GENUINE (prompt names it), not a
        "for 10 minutes"→10:00 misread. Keep it as the primary trigger;
        presence becomes a condition."""
        hass = _FakeHass(
            [
                _FakeState(
                    "group.all_persons",
                    "All Persons",
                    members=["person.gunnar"],
                )
            ]
        )
        automation = {
            "triggers": [{"trigger": "time", "at": "10:00:00"}],
            "actions": [{"service": "light.turn_off", "target": {"entity_id": "light.hall"}}],
        }
        changed = _coerce_presence_for_duration_trigger(
            automation,
            "At 10:00, turn off the lights if nobody is home for 10 minutes",
            hass,
        )
        assert changed is True
        triggers = automation["triggers"]
        # The explicit 10:00 time trigger survives.
        assert any(
            t.get("trigger") == "time" and t.get("at") == "10:00:00" for t in triggers
        )
        # Presence demoted to a condition (an "if"-introduced gate).
        conditions = automation.get("conditions", [])
        assert any(
            c.get("condition") == "state" and c.get("for") == {"minutes": 10}
            for c in conditions
        )

    def test_stray_hallucinated_time_trigger_dropped(self) -> None:
        """P2 — standalone presence prompt "turn off the porch light when
        nobody is home for 10 minutes" with a STRAY model time trigger at
        12:00 (not named in the prompt). The stray trigger must be
        dropped and presence become the trigger — not "at noon if nobody
        is home"."""
        hass = _FakeHass(
            [
                _FakeState(
                    "group.all_persons",
                    "All Persons",
                    members=["person.gunnar"],
                )
            ]
        )
        automation = {
            "triggers": [{"trigger": "time", "at": "12:00:00"}],
            "actions": [{"service": "light.turn_off", "target": {"entity_id": "light.porch"}}],
        }
        changed = _coerce_presence_for_duration_trigger(
            automation,
            "turn off the porch light when nobody is home for 10 minutes",
            hass,
        )
        assert changed is True
        triggers = automation["triggers"]
        # Stray noon trigger gone; presence is the trigger.
        assert all(t.get("trigger") != "time" for t in triggers)
        assert any(
            t.get("trigger") == "state"
            and t.get("entity_id") == "group.all_persons"
            and t.get("for") == {"minutes": 10}
            for t in triggers
        )
        # No phantom condition.
        assert not automation.get("conditions")

    def test_explicit_time_synthesized_when_model_dropped_trigger(self) -> None:
        """P2 — model returned NO valid trigger for "At 10:00, turn off
        the lights if nobody is home for 10 minutes". The explicit 10:00
        is the primary trigger — synthesize it and keep presence as a
        condition, NOT make presence the trigger (which would fire
        whenever the home empties, ignoring the schedule)."""
        hass = _FakeHass(
            [
                _FakeState(
                    "group.all_persons",
                    "All Persons",
                    members=["person.gunnar"],
                )
            ]
        )
        # No trigger at all (model truncated / emitted invalid).
        automation = {
            "triggers": [],
            "actions": [{"service": "light.turn_off", "target": {"entity_id": "light.hall"}}],
        }
        changed = _coerce_presence_for_duration_trigger(
            automation,
            "At 10:00, turn off the lights if nobody is home for 10 minutes",
            hass,
        )
        assert changed is True
        triggers = automation["triggers"]
        assert any(
            t.get("trigger") == "time" and t.get("at") == "10:00:00" for t in triggers
        )
        # Presence is a condition, NOT a trigger.
        assert all(t.get("trigger") != "state" for t in triggers)
        conditions = automation.get("conditions", [])
        assert any(
            c.get("condition") == "state" and c.get("for") == {"minutes": 10}
            for c in conditions
        )

    def test_when_presence_with_primary_trigger_becomes_condition(self) -> None:
        """P1 — "At 10:00, turn off the lights WHEN nobody is home for 10
        minutes": "when" (not just "if") gating a genuine time trigger is
        a condition. Must NOT append a second presence trigger (HA would
        OR them, firing whenever the home empties)."""
        hass = _FakeHass(
            [
                _FakeState(
                    "group.all_persons",
                    "All Persons",
                    members=["person.gunnar"],
                )
            ]
        )
        automation = {
            "triggers": [{"trigger": "time", "at": "10:00:00"}],
            "actions": [{"service": "light.turn_off", "target": {"entity_id": "light.hall"}}],
        }
        changed = _coerce_presence_for_duration_trigger(
            automation,
            "At 10:00, turn off the lights when nobody is home for 10 minutes",
            hass,
        )
        assert changed is True
        triggers = automation["triggers"]
        # Only the time trigger remains — no appended presence trigger.
        assert [t.get("trigger") for t in triggers] == ["time"]
        conditions = automation.get("conditions", [])
        assert any(
            c.get("condition") == "state"
            and c.get("entity_id") == "group.all_persons"
            and c.get("for") == {"minutes": 10}
            for c in conditions
        )

    def test_when_presence_standalone_stays_trigger(self) -> None:
        """"when nobody is home for 10 minutes" with no other trigger →
        presence remains the trigger (not demoted to a condition)."""
        hass = _FakeHass(
            [
                _FakeState(
                    "group.all_persons",
                    "All Persons",
                    members=["person.gunnar"],
                )
            ]
        )
        automation = {
            "triggers": [{"trigger": "time", "at": "10:00:00"}],
            "actions": [{"service": "light.turn_off", "target": {"entity_id": "light.hall"}}],
        }
        changed = _coerce_presence_for_duration_trigger(
            automation, "when nobody is home for 10 minutes", hass
        )
        assert changed is True
        triggers = automation["triggers"]
        assert len(triggers) == 1
        assert triggers[0]["trigger"] == "state"
        assert triggers[0]["entity_id"] == "group.all_persons"
        assert triggers[0]["to"] == "not_home"

    def test_seconds_misread_time_trigger_dropped(self) -> None:
        """P2 — "for 10 seconds" misread as ``at: 00:00:10`` (or
        10:00:00) is dropped alongside synthesizing the presence
        trigger."""
        hass = _FakeHass(
            [_FakeState("binary_sensor.kitchen_occupancy", "Kitchen Occupancy")]
        )
        automation = {
            "triggers": [{"trigger": "time", "at": "00:00:10"}],
            "actions": [
                {"service": "light.turn_off", "target": {"entity_id": "light.kitchen"}}
            ],
        }
        changed = _coerce_presence_for_duration_trigger(
            automation,
            "turn off the kitchen light when nobody is in the kitchen for 10 seconds",
            hass,
        )
        assert changed is True
        triggers = automation["triggers"]
        assert len(triggers) == 1
        assert triggers[0]["trigger"] == "state"
        assert triggers[0]["for"] == {"seconds": 10}


class TestSynthesizeActionFromPrompt:
    """Last-ditch action synthesis from prompt verb + named target."""

    def test_unique_overlap_synthesizes_action(self) -> None:
        hass = _FakeHass([_FakeState("light.porch", "Porch Light")])
        action = _synthesize_action_from_prompt(
            hass, "turn off the porch light when nobody is home for 10 minutes"
        )
        assert action is not None
        assert action["service"] == "light.turn_off"
        assert action["target"]["entity_id"] == "light.porch"

    def test_ambiguous_overlap_refuses(self) -> None:
        """P2 — two entities tie on score + domain rank ("Front Porch
        Light" vs "Back Porch Light" for "the porch light"). Refuse
        rather than synthesize for an arbitrary one."""
        hass = _FakeHass(
            [
                _FakeState("light.front_porch", "Front Porch Light"),
                _FakeState("light.back_porch", "Back Porch Light"),
            ]
        )
        action = _synthesize_action_from_prompt(
            hass, "turn off the porch light when nobody is home for 10 minutes"
        )
        assert action is None

    def test_no_overlap_returns_none(self) -> None:
        hass = _FakeHass([_FakeState("light.bedroom", "Bedroom Lamp")])
        action = _synthesize_action_from_prompt(
            hass, "turn off the porch sconce when nobody is home for 10 minutes"
        )
        assert action is None

    def test_no_verb_returns_none(self) -> None:
        hass = _FakeHass([_FakeState("light.porch", "Porch Light")])
        action = _synthesize_action_from_prompt(
            hass, "nobody is home for 10 minutes"
        )
        assert action is None


class TestPromptKeywordBestEntity:
    """Last-ditch keyword overlap picker — must refuse ambiguous ties."""

    def _ent(self, eid: str, fname: str) -> dict[str, Any]:
        return {"entity_id": eid, "attributes": {"friendly_name": fname}}

    def test_unique_overlap_picks_entity(self) -> None:
        ents = [self._ent("light.porch", "Porch Light")]
        assert (
            _prompt_keyword_best_entity("turn off the porch light", ents)
            == "light.porch"
        )

    def test_ambiguous_tie_refuses(self) -> None:
        """P2 — equal overlap + domain rank across two entities ("Front
        Porch Light" vs "Back Porch Light" for "the porch light") must
        return None, not pick by entity_id order."""
        ents = [
            self._ent("light.front_porch", "Front Porch Light"),
            self._ent("light.back_porch", "Back Porch Light"),
        ]
        assert _prompt_keyword_best_entity("turn off the porch light", ents) is None

    def test_no_overlap_returns_none(self) -> None:
        ents = [self._ent("light.bedroom", "Bedroom Lamp")]
        assert _prompt_keyword_best_entity("turn off the porch sconce", ents) is None


class TestEntitiesNamedInPrompt:
    """Token-subset name matching — particle-independent across locales."""

    def _ent(self, eid: str, fname: str) -> dict[str, Any]:
        return {"entity_id": eid, "attributes": {"friendly_name": fname}}

    def test_english_contiguous(self) -> None:
        ents = [self._ent("light.lr", "Living Room Light")]
        assert _entities_named_in_prompt("turn off the living room light", ents) == [
            "light.lr"
        ]

    def test_interleaved_particles_match_in_all_locales(self) -> None:
        # The substring approach failed on the particle between name words;
        # token-subset survives it. One representative prompt per locale.
        cases = [
            ("light.salon", "Lumière Salon", "allume la lumière du salon"),  # fr
            ("light.wz", "Licht Wohnzimmer", "schalte das licht im wohnzimmer ein"),  # de
            ("light.sala", "Luz Salón", "enciende la luz del salón"),  # es
            ("light.salotto", "Luce Salotto", "accendi la luce del salotto"),  # it
        ]
        for eid, fname, prompt in cases:
            ents = [self._ent(eid, fname)]
            assert _entities_named_in_prompt(prompt, ents) == [eid], prompt

    def test_partial_name_does_not_match(self) -> None:
        # "Fan" not in the prompt → must not be returned (precision: the
        # single-hit caller would otherwise retarget the wrong device).
        ents = [
            self._ent("light.lr", "Living Room Light"),
            self._ent("fan.lr", "Living Room Fan"),
        ]
        assert _entities_named_in_prompt("turn off the living room light", ents) == [
            "light.lr"
        ]

    def test_longest_name_first(self) -> None:
        ents = [
            self._ent("light.room", "Room"),
            self._ent("light.living_room", "Living Room"),
        ]
        # Both are token-subsets of the prompt; the longer name ranks first.
        assert _entities_named_in_prompt("the living room please", ents)[0] == (
            "light.living_room"
        )


class TestCloudJsonSalvage:
    """Weak gateway-routed models emit trailing commas,
    single quotes, and unquoted keys that kill a plain ``json.loads`` and
    drop the whole response. The salvage fallback recovers them; clean JSON
    is untouched and genuinely-broken JSON still fails closed."""

    def test_suggestions_salvages_trailing_comma_and_single_quotes(self) -> None:
        raw = (
            "[{'alias': 'Sunset Alert', 'description': 'x', "
            "'triggers': [{'platform': 'sun', 'event': 'sunset'},],},]"
        )
        result = parse_suggestions(raw, "selora_cloud")
        assert len(result) == 1
        assert result[0]["alias"] == "Sunset Alert"

    def test_suggestions_salvages_unquoted_keys(self) -> None:
        raw = '[{alias: "A", actions: [{action: "light.turn_on"}]}]'
        result = parse_suggestions(raw, "selora_cloud")
        assert len(result) == 1
        assert result[0]["alias"] == "A"

    def test_command_salvages_trailing_comma(self) -> None:
        raw = "{'calls': [], 'response': 'Done.',}"
        result = parse_command_response_text(raw)
        assert result["response"] == "Done."
        assert result["calls"] == []

    def test_clean_json_unaffected(self) -> None:
        raw = '[{"alias": "A", "actions": []}]'
        assert len(parse_suggestions(raw, "x")) == 1

    def test_unrepairable_suggestions_fail_closed(self) -> None:
        # Not salvageable as JSON — must return [] rather than raise.
        assert parse_suggestions("[this is not json at all <<<]", "x") == []

    def test_salvage_records_repair_in_scope(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []
        monkeypatch.setattr(
            "custom_components.selora_ai.llm_client.parsers.record_repair",
            lambda kind: calls.append(kind),
        )
        parse_suggestions("[{'alias': 'A', 'actions': [],},]", "selora_cloud")
        assert "cloud_json_salvage" in calls

    def test_no_repair_recorded_for_clean_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        monkeypatch.setattr(
            "custom_components.selora_ai.llm_client.parsers.record_repair",
            lambda kind: calls.append(kind),
        )
        parse_suggestions('[{"alias": "A", "actions": []}]', "x")
        assert "cloud_json_salvage" not in calls
