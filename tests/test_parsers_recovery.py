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
    _coerce_sun_triggers,
    _extract_numeric_threshold,
    _humanise_unknown_entity_error,
    _resolve_unknown_entity_ids,
    _service_verb_for_domain,
)


def _entity(entity_id: str, friendly_name: str) -> dict[str, Any]:
    return {"entity_id": entity_id, "attributes": {"friendly_name": friendly_name}}


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
