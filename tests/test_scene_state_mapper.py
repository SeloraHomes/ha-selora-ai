"""Tests for scene_state_mapper -- domain-specific state validation and defaults."""

from __future__ import annotations

import pytest

from custom_components.selora_ai.scene_state_mapper import (
    DOMAIN_STATE_SCHEMAS,
    SCENE_INTENT_PRESETS,
    apply_default_states,
    validate_entity_states,
)


@pytest.fixture(autouse=True)
def _enable_custom_component(enable_custom_integrations):
    """Auto-enable custom integrations so our domain is discoverable."""


# ── validate_entity_states ───────────────────────────────────────────


class TestValidateAcceptsValid:
    """Happy-path: payloads that should normalize successfully."""

    def test_valid_multi_domain(self) -> None:
        entities = {
            "light.living_room": {"state": "on", "brightness": 51, "color_temp": 400},
            "media_player.tv": {"state": "on", "volume_level": 0.5},
            "cover.blinds": {"state": "closed", "current_position": 0},
        }
        ok, _, norm = validate_entity_states(entities)
        assert ok
        assert norm["light.living_room"]["color_temp"] == 400
        assert norm["media_player.tv"]["volume_level"] == 0.5
        assert norm["cover.blinds"]["current_position"] == 0

    def test_empty_entities(self) -> None:
        ok, _, norm = validate_entity_states({})
        assert ok
        assert norm == {}

    @pytest.mark.parametrize(
        "entities, key, expected",
        [
            # basic domains
            ({"light.x": {"state": "on", "brightness": 128}}, "light.x", {"brightness": 128}),
            ({"climate.x": {"state": "heat", "temperature": 22.5, "hvac_mode": "heat"}}, "climate.x", {"temperature": 22.5}),
            ({"fan.x": {"state": "on", "percentage": 50}}, "fan.x", {"percentage": 50}),
            # preset_mode
            ({"climate.x": {"state": "heat", "preset_mode": "comfort"}}, "climate.x", {"preset_mode": "comfort"}),
            ({"fan.x": {"state": "on", "preset_mode": "auto"}}, "fan.x", {"preset_mode": "auto"}),
            # light off + brightness (HA resume value)
            ({"light.x": {"state": "off", "brightness": 128}}, "light.x", {"brightness": 128}),
        ],
        ids=["light", "climate", "fan", "climate-preset", "fan-preset", "light-off-resume"],
    )
    def test_valid_domain_entities(self, entities: dict, key: str, expected: dict) -> None:
        ok, _, norm = validate_entity_states(entities)
        assert ok
        for attr, val in expected.items():
            assert norm[key][attr] == val

    @pytest.mark.parametrize(
        "state",
        ["open", "closed", "opening", "closing"],
    )
    def test_valid_cover_states(self, state: str) -> None:
        ok, _, _ = validate_entity_states({"cover.x": {"state": state}})
        assert ok

    @pytest.mark.parametrize(
        "state",
        ["off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"],
    )
    def test_valid_climate_states(self, state: str) -> None:
        ok, _, _ = validate_entity_states({"climate.x": {"state": state}})
        assert ok

    @pytest.mark.parametrize(
        "mode",
        ["off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"],
    )
    def test_valid_hvac_modes(self, mode: str) -> None:
        ok, _, _ = validate_entity_states({"climate.x": {"state": "heat", "hvac_mode": mode}})
        assert ok


class TestValidateCoercion:
    """Type coercion and normalization of valid-but-mistyped inputs."""

    @pytest.mark.parametrize(
        "entities, key, attr, expected",
        [
            # string → int
            ({"light.x": {"state": "on", "brightness": "128"}}, "light.x", "brightness", 128),
            # string → float
            ({"media_player.x": {"state": "on", "volume_level": "0.7"}}, "media_player.x", "volume_level", 0.7),
            # bool state → on/off (or open/closed for covers)
            ({"light.x": {"state": True, "brightness": 128}}, "light.x", "state", "on"),
            ({"switch.x": {"state": False}}, "switch.x", "state", "off"),
            ({"cover.x": {"state": True}}, "cover.x", "state", "open"),
            ({"cover.x": {"state": False}}, "cover.x", "state", "closed"),
            # mixed-case state → lowercase
            ({"light.x": {"state": "ON", "brightness": 128}}, "light.x", "state", "on"),
            ({"cover.x": {"state": "Closed"}}, "cover.x", "state", "closed"),
            # mixed-case hvac_mode → lowercase
            ({"climate.x": {"state": "heat", "hvac_mode": "HEAT"}}, "climate.x", "hvac_mode", "heat"),
            # tuple → list for rgb_color
            ({"light.x": {"state": "on", "rgb_color": (255, 0, 0)}}, "light.x", "rgb_color", [255, 0, 0]),
            # string elements in rgb_color → coerced to int
            ({"light.x": {"state": "on", "rgb_color": ["255", "0", "0"]}}, "light.x", "rgb_color", [255, 0, 0]),
        ],
        ids=[
            "str-to-int", "str-to-float", "bool-true-on", "bool-false-off",
            "cover-bool-true-open", "cover-bool-false-closed",
            "uppercase-state", "mixed-case-cover", "uppercase-hvac",
            "tuple-rgb", "str-rgb-elements",
        ],
    )
    def test_coerces_value(self, entities: dict, key: str, attr: str, expected) -> None:
        ok, _, norm = validate_entity_states(entities)
        assert ok
        if isinstance(expected, float) and not isinstance(expected, bool):
            assert abs(norm[key][attr] - expected) < 0.01
        else:
            assert norm[key][attr] == expected


class TestValidateClamping:
    """Numeric values clamped to valid ranges."""

    @pytest.mark.parametrize(
        "entities, key, attr, expected",
        [
            # brightness
            ({"light.x": {"state": "on", "brightness": 300}}, "light.x", "brightness", 255),
            ({"light.x": {"state": "on", "brightness": -10}}, "light.x", "brightness", 0),
            # volume
            ({"media_player.x": {"state": "on", "volume_level": 1.5}}, "media_player.x", "volume_level", 1.0),
            # cover position (via alias)
            ({"cover.x": {"state": "open", "position": 150}}, "cover.x", "current_position", 100),
            ({"cover.x": {"state": "open", "current_position": 150}}, "cover.x", "current_position", 100),
            # color_temp (only enforces minimum)
            ({"light.x": {"state": "on", "color_temp": 0}}, "light.x", "color_temp", 1),
            # high color_temp preserved (per-entity max varies)
            ({"light.x": {"state": "on", "color_temp": 588}}, "light.x", "color_temp", 588),
            # color arrays
            ({"light.x": {"state": "on", "rgb_color": [999, -1, 0]}}, "light.x", "rgb_color", [255, 0, 0]),
            ({"light.x": {"state": "on", "hs_color": [720.0, 200.0]}}, "light.x", "hs_color", [360.0, 100.0]),
            ({"light.x": {"state": "on", "xy_color": [1.5, -0.2]}}, "light.x", "xy_color", [1.0, 0.0]),
        ],
        ids=[
            "brightness-high", "brightness-low", "volume-high",
            "position-alias", "position-canonical", "color-temp-min",
            "color-temp-high-preserved", "rgb-clamp", "hs-clamp", "xy-clamp",
        ],
    )
    def test_clamps_to_range(self, entities: dict, key: str, attr: str, expected) -> None:
        ok, _, norm = validate_entity_states(entities)
        assert ok
        assert norm[key][attr] == expected


class TestValidateAliases:
    """Snapshot/service-call attribute alias normalization."""

    def test_position_alias_normalized(self) -> None:
        ok, _, norm = validate_entity_states({"cover.x": {"state": "open", "position": 75}})
        assert ok
        assert norm["cover.x"]["current_position"] == 75
        assert "position" not in norm["cover.x"]

    def test_current_position_preserved(self) -> None:
        ok, _, norm = validate_entity_states({"cover.x": {"state": "open", "current_position": 75}})
        assert ok
        assert norm["cover.x"]["current_position"] == 75

    def test_target_temperature_normalized(self) -> None:
        ok, _, norm = validate_entity_states({"climate.x": {"state": "heat", "target_temperature": 22.5}})
        assert ok
        assert norm["climate.x"]["temperature"] == 22.5
        assert "target_temperature" not in norm["climate.x"]

    @pytest.mark.parametrize(
        "entities",
        [
            # same value — accepted
            {"cover.x": {"state": "open", "position": 50, "current_position": 50}},
            # mixed types coerced before comparison
            {"cover.x": {"state": "open", "position": "50", "current_position": 50}},
            # out-of-range aliases that clamp to the same value
            {"cover.x": {"state": "open", "current_position": 100, "position": 150}},
        ],
        ids=["same-value", "mixed-types", "clamp-to-same"],
    )
    def test_matching_aliases_accepted(self, entities: dict) -> None:
        ok, _, norm = validate_entity_states(entities)
        assert ok
        assert norm["cover.x"]["current_position"] == int(
            max(0, min(100, int(entities["cover.x"]["current_position"])))
        )

    @pytest.mark.parametrize(
        "entities",
        [
            {"cover.x": {"state": "open", "position": 50, "current_position": 75}},
            {"climate.x": {"state": "heat", "temperature": 20.0, "target_temperature": 22.5}},
        ],
        ids=["cover-position", "climate-temperature"],
    )
    def test_conflicting_aliases_rejected(self, entities: dict) -> None:
        ok, reason, _ = validate_entity_states(entities)
        assert not ok
        assert "conflicting" in reason.lower()

    def test_stray_aliases_on_wrong_domain_ignored(self) -> None:
        """position/current_position on a light should be dropped, not conflict."""
        entities = {
            "light.x": {"state": "on", "brightness": 128, "position": 50, "current_position": 75}
        }
        ok, _, norm = validate_entity_states(entities)
        assert ok
        assert "position" not in norm["light.x"]
        assert "current_position" not in norm["light.x"]


class TestValidateRejections:
    """Malformed payloads that must be rejected with (False, reason, None)."""

    @pytest.mark.parametrize(
        "entities, reason_fragment",
        [
            # top-level type errors
            (None, "dict"),
            ([{"light.x": {"state": "on"}}], "dict"),
            # bad entity ID types/formats
            ({123: {"state": "on"}}, "string"),
            ({"NOT_VALID": {"state": "on"}}, "entity_id"),
            # bad state_data types
            ({"light.kitchen": None}, "dict"),
            ({"light.kitchen": "on"}, "dict"),
            ({"light.kitchen": ["on", 128]}, "dict"),
            # too many entities
            ({f"light.room_{i}": {"state": "on"} for i in range(51)}, "50"),
            # missing state
            ({"light.x": {"brightness": 100}}, "state"),
            # invalid coercion
            ({"light.x": {"state": "on", "brightness": "not_a_number"}}, "coerce"),
            # string for list attribute
            ({"light.x": {"state": "on", "rgb_color": "[255,0,0]"}}, "coerce"),
            # cross-domain states
            ({"light.kitchen": {"state": "open"}}, "invalid state"),
            ({"cover.blinds": {"state": "on"}}, "invalid state"),
            ({"cover.x": {"state": "stopped"}}, "invalid state"),
            # invalid hvac_mode
            ({"climate.x": {"state": "heat", "hvac_mode": "banana"}}, "hvac_mode"),
            # duplicate entity ID after case-folding
            ({"Light.Kitchen": {"state": "on"}, "light.kitchen": {"state": "off"}}, "duplicate"),
            # contradictory cover state/position
            ({"cover.x": {"state": "closed", "current_position": 100}}, "contradictory"),
            ({"cover.x": {"state": "open", "current_position": 0}}, "contradictory"),
        ],
        ids=[
            "null-entities", "list-entities",
            "int-entity-id", "invalid-entity-id",
            "null-state-data", "str-state-data", "list-state-data",
            "too-many-entities", "missing-state", "bad-coercion", "str-for-list",
            "light-state-open", "cover-state-on", "cover-state-stopped",
            "invalid-hvac-mode", "duplicate-casefold",
            "cover-closed-pos100", "cover-open-pos0",
        ],
    )
    def test_rejects_malformed(self, entities, reason_fragment: str) -> None:
        ok, reason, _ = validate_entity_states(entities)
        assert not ok
        assert reason_fragment.lower() in reason.lower()

    @pytest.mark.parametrize(
        "entities",
        [
            # booleans for numeric fields
            {"light.x": {"state": "on", "brightness": True}},
            {"fan.x": {"state": "on", "percentage": False}},
            {"media_player.x": {"state": "on", "volume_level": True}},
            # booleans for non-state string fields
            {"media_player.tv": {"state": "on", "source": True}},
            {"climate.x": {"state": "heat", "hvac_mode": False}},
            {"fan.x": {"state": "on", "preset_mode": True}},
            # containers for string fields
            {"light.kitchen": {"state": ["on"]}},
            {"media_player.tv": {"state": "on", "source": ["HDMI 1"]}},
            {"switch.x": {"state": {"value": "on"}}},
        ],
        ids=[
            "bool-brightness", "bool-percentage", "bool-volume",
            "bool-source", "bool-hvac", "bool-preset",
            "list-state", "list-source", "dict-state",
        ],
    )
    def test_rejects_bad_type_coercion(self, entities: dict) -> None:
        ok, _, _ = validate_entity_states(entities)
        assert not ok

    @pytest.mark.parametrize(
        "entities",
        [
            {"light.x": {"state": "on", "rgb_color": [255, 0]}},
            {"light.x": {"state": "on", "hs_color": [30.0]}},
            {"light.x": {"state": "on", "xy_color": [0.4]}},
        ],
        ids=["rgb-2-elements", "hs-1-element", "xy-1-element"],
    )
    def test_rejects_wrong_color_length(self, entities: dict) -> None:
        ok, reason, _ = validate_entity_states(entities)
        assert not ok
        assert "elements" in reason

    @pytest.mark.parametrize(
        "entities",
        [
            {"light.x": {"state": "on", "rgb_color": [True, False, False]}},
            {"light.x": {"state": "on", "hs_color": [True, False]}},
            {"light.x": {"state": "on", "rgb_color": ["red", "green", "blue"]}},
        ],
        ids=["bool-rgb", "bool-hs", "non-numeric-rgb"],
    )
    def test_rejects_bad_color_elements(self, entities: dict) -> None:
        ok, _, _ = validate_entity_states(entities)
        assert not ok

    @pytest.mark.parametrize(
        "entities",
        [
            {"light.x": {"state": "on", "xy_color": [float("nan"), 0.5]}},
            {"light.x": {"state": "on", "hs_color": [float("inf"), 10.0]}},
            {"light.x": {"state": "on", "hs_color": ["nan", 50.0]}},
        ],
        ids=["nan-xy", "inf-hs", "nan-str-hs"],
    )
    def test_rejects_non_finite_color_elements(self, entities: dict) -> None:
        ok, reason, _ = validate_entity_states(entities)
        assert not ok
        assert "non-finite" in reason.lower()

    @pytest.mark.parametrize(
        "entities",
        [
            {"climate.x": {"state": "heat", "temperature": "nan"}},
            {"media_player.x": {"state": "on", "volume_level": "inf"}},
            {"climate.x": {"state": "heat", "temperature": float("-inf")}},
        ],
        ids=["nan-str", "inf-str", "neg-inf-float"],
    )
    def test_rejects_non_finite_scalars(self, entities: dict) -> None:
        ok, _, _ = validate_entity_states(entities)
        assert not ok


class TestValidateUnknownDomains:
    """Entities with domains not in DOMAIN_STATE_SCHEMAS are now rejected."""

    def test_rejects_unknown_domain(self) -> None:
        ok, reason, _ = validate_entity_states({"custom_domain.x": {"state": "active"}})
        assert not ok
        assert "unsupported domain" in reason.lower()

    def test_rejects_sensor_domain(self) -> None:
        ok, reason, _ = validate_entity_states({"sensor.temp": {"state": "21"}})
        assert not ok
        assert "unsupported domain" in reason.lower()

    def test_rejects_binary_sensor_domain(self) -> None:
        ok, reason, _ = validate_entity_states({"binary_sensor.door": {"state": "on"}})
        assert not ok
        assert "unsupported domain" in reason.lower()

    def test_rejects_input_select_domain(self) -> None:
        ok, reason, _ = validate_entity_states({"input_select.mode": {"state": "eco"}})
        assert not ok
        assert "unsupported domain" in reason.lower()


class TestValidateEntityIdFormats:
    """Entity ID parsing, case-folding, and deduplication."""

    @pytest.mark.parametrize(
        "entity_id",
        ["light.room-2", "switch.outlet_1", "Light.Living_Room"],
        ids=["hyphen", "underscore-digit", "mixed-case"],
    )
    def test_accepts_valid_formats(self, entity_id: str) -> None:
        ok, _, norm = validate_entity_states({entity_id: {"state": "on"}})
        assert ok
        assert entity_id.lower() in norm

    def test_ignores_unsupported_attributes(self) -> None:
        ok, _, norm = validate_entity_states({"light.x": {"state": "on", "brightness": 100, "unknown_attr": "foo"}})
        assert ok
        assert "unknown_attr" not in norm["light.x"]

    def test_caps_long_known_domain_string(self) -> None:
        ok, _, norm = validate_entity_states({"media_player.x": {"state": "on", "source": "a" * 300}})
        assert ok
        assert len(norm["media_player.x"]["source"]) == 200

    def test_cover_consistent_closed_zero(self) -> None:
        ok, _, _ = validate_entity_states({"cover.x": {"state": "closed", "current_position": 0}})
        assert ok

    def test_cover_consistent_open_nonzero(self) -> None:
        ok, _, _ = validate_entity_states({"cover.x": {"state": "open", "current_position": 50}})
        assert ok


# ── apply_default_states ─────────────────────────────────────────────


class TestApplyPresetDefaults:
    """Preset keyword matching and attribute merging."""

    @pytest.mark.parametrize(
        "intent, entity_id, input_state, expected",
        [
            ("cozy evening", "light.x", {"state": "on"}, {"brightness": 51, "color_temp": 400, "state": "on"}),
            ("make it bright", "light.x", {"state": "on"}, {"brightness": 255, "color_temp": 250}),
            ("work mode", "light.x", {"state": "on"}, {"brightness": 255}),
        ],
        ids=["cozy", "bright", "work"],
    )
    def test_fills_light_defaults(self, intent: str, entity_id: str, input_state: dict, expected: dict) -> None:
        result = apply_default_states({entity_id: input_state}, intent)
        for attr, val in expected.items():
            assert result[entity_id][attr] == val

    def test_cozy_fills_state_when_omitted(self) -> None:
        result = apply_default_states({"light.x": {}}, "cozy evening")
        assert result["light.x"]["state"] == "on"
        assert result["light.x"]["brightness"] == 51

    def test_does_not_override_existing_attributes(self) -> None:
        result = apply_default_states({"light.x": {"state": "on", "brightness": 200}}, "cozy evening")
        assert result["light.x"]["brightness"] == 200

    def test_no_matching_preset_passes_through(self) -> None:
        entities = {"light.x": {"state": "on"}}
        assert apply_default_states(entities, "random intent") == entities

    def test_preset_requires_word_boundary(self) -> None:
        result = apply_default_states({"light.x": {"state": "on"}}, "network reset")
        assert "brightness" not in result["light.x"]

    def test_domain_without_preset_unchanged(self) -> None:
        entities = {"climate.x": {"state": "heat", "temperature": 22.0}}
        assert apply_default_states(entities, "cozy evening")["climate.x"] == entities["climate.x"]

    def test_empty_entities(self) -> None:
        assert apply_default_states({}, "cozy") == {}

    def test_mixed_case_entity_id_gets_preset(self) -> None:
        result = apply_default_states({"Light.Living_Room": {"state": "on"}}, "cozy evening")
        assert result["Light.Living_Room"]["brightness"] == 51


class TestApplyOffStateSkipping:
    """Entities explicitly off should not get preset defaults."""

    @pytest.mark.parametrize(
        "entities",
        [
            {"light.kitchen": {"state": "off"}},
            {"light.kitchen": {"state": False}},
            {"cover.x": {"state": "closed"}},
        ],
        ids=["string-off", "bool-false", "cover-closed"],
    )
    def test_skips_defaults_for_off_entities(self, entities: dict) -> None:
        key = next(iter(entities))
        result = apply_default_states(entities, "cozy evening")
        # No attributes should be injected
        assert result[key] == entities[key]

    def test_sleep_preset_on_light_preserves_llm_state(self) -> None:
        """LLM said 'on' — sleep preset default 'off' should not override."""
        result = apply_default_states({"light.x": {"state": "on"}}, "sleep mode")
        assert result["light.x"]["state"] == "on"


class TestApplyCoverStateInference:
    """Cover state inferred from position when state is missing or contradicted."""

    @pytest.mark.parametrize(
        "entities, intent, expected_state",
        [
            # state inferred from preset-injected position
            ({"cover.x": {}}, "sleep", "closed"),
            ({"cover.x": {}}, "movie night", "closed"),
            ({"cover.x": {}}, "morning routine", "open"),
            # state inferred from LLM-supplied position (no preset match)
            ({"cover.x": {"current_position": 75}}, "set blinds halfway", "open"),
            ({"cover.x": {"current_position": 0}}, "close everything", "closed"),
            ({"cover.x": {"position": 50}}, "random intent", "open"),
            # string positions coerced before comparison
            ({"cover.x": {"position": "0"}}, "some intent", "closed"),
            ({"cover.x": {"current_position": "75"}}, "some intent", "open"),
            # negative position clamps to 0 → closed
            ({"cover.x": {"position": -10}}, "some intent", "closed"),
        ],
        ids=[
            "sleep-preset", "movie-preset", "morning-preset",
            "llm-pos-75", "llm-pos-0", "llm-alias-50",
            "str-pos-0", "str-pos-75", "negative-pos",
        ],
    )
    def test_infers_state(self, entities: dict, intent: str, expected_state: str) -> None:
        result = apply_default_states(entities, intent)
        key = next(iter(entities))
        assert result[key]["state"] == expected_state

    @pytest.mark.parametrize(
        "entities, intent, expected_state",
        [
            # preset overrides contradictory open → closed
            ({"cover.x": {"state": "open"}}, "sleep", "closed"),
            ({"cover.x": {"state": "open"}}, "movie night", "closed"),
        ],
        ids=["sleep-overrides-open", "movie-overrides-open"],
    )
    def test_reconciles_contradictory_state(self, entities: dict, intent: str, expected_state: str) -> None:
        result = apply_default_states(entities, intent)
        assert result["cover.x"]["state"] == expected_state

    @pytest.mark.parametrize("state", ["opening", "closing"])
    def test_preserves_transitional_states(self, state: str) -> None:
        result = apply_default_states({"cover.x": {"state": state}}, "sleep")
        assert result["cover.x"]["state"] == state
        assert result["cover.x"]["current_position"] == 0

    def test_llm_position_prevents_preset_injection(self) -> None:
        result = apply_default_states({"cover.x": {"state": "open", "current_position": 0}}, "cozy evening")
        assert result["cover.x"]["state"] == "open"

    def test_llm_position_alias_prevents_preset_injection(self) -> None:
        result = apply_default_states({"cover.x": {"state": "open", "position": 75}}, "movie night")
        assert result["cover.x"]["state"] == "open"
        assert result["cover.x"]["position"] == 75
        assert "current_position" not in result["cover.x"]

    def test_morning_preset_multi_domain(self) -> None:
        entities = {"light.x": {"state": "on"}, "cover.x": {"state": "open"}}
        result = apply_default_states(entities, "morning routine")
        assert result["light.x"]["brightness"] == 200
        assert result["cover.x"]["current_position"] == 100


class TestApplyMalformedInputs:
    """Malformed entries passed through without crashing."""

    def test_null_state_data(self) -> None:
        result = apply_default_states({"light.kitchen": None, "light.x": {"state": "on"}}, "cozy evening")
        assert result["light.kitchen"] is None
        assert result["light.x"]["brightness"] == 51

    def test_non_string_entity_id(self) -> None:
        result = apply_default_states({123: {"state": "on"}, "light.x": {"state": "on"}}, "cozy evening")
        assert result[123] == {"state": "on"}


# ── DOMAIN_STATE_SCHEMAS ─────────────────────────────────────────────


class TestDomainStateSchemas:
    def test_covers_scene_capable_domains(self) -> None:
        for domain in ("light", "switch", "media_player", "climate", "fan", "cover"):
            assert domain in DOMAIN_STATE_SCHEMAS

    def test_light_has_brightness_and_color_temp(self) -> None:
        schema = DOMAIN_STATE_SCHEMAS["light"]
        assert "brightness" in schema and schema["brightness"] is int
        assert "color_temp" in schema

    def test_cover_has_position_and_alias(self) -> None:
        assert "position" in DOMAIN_STATE_SCHEMAS["cover"]
        assert "current_position" in DOMAIN_STATE_SCHEMAS["cover"]

    def test_climate_has_temperature(self) -> None:
        assert DOMAIN_STATE_SCHEMAS["climate"]["temperature"] is float


# ── SCENE_INTENT_PRESETS ─────────────────────────────────────────────


class TestSceneIntentPresets:
    @pytest.mark.parametrize("keyword", ["cozy", "bright", "movie", "sleep", "morning"])
    def test_has_common_presets(self, keyword: str) -> None:
        assert keyword in SCENE_INTENT_PRESETS

    def test_cozy_preset_has_light(self) -> None:
        assert "brightness" in SCENE_INTENT_PRESETS["cozy"]["light"]
