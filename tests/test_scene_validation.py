"""Tests for scene_validation -- security hardening for scene creation.

Covers entity existence checks, area scoping, name sanitization,
injection prevention, and malformed input handling.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

def _has_scene_modules() -> bool:
    """Check if scene_utils and scene_state_mapper are available (merged from !103-!105)."""
    try:
        import custom_components.selora_ai.scene_utils  # noqa: F401
        import custom_components.selora_ai.scene_state_mapper  # noqa: F401
        return True
    except ImportError:
        return False


from custom_components.selora_ai.scene_validation import (
    _MAX_ENTITIES_PER_SCENE,
    _MAX_SCENE_NAME_LEN,
    sanitize_scene_name,
    validate_entities_exist,
    validate_entities_in_area,
    validate_scene_security,
)


@pytest.fixture(autouse=True)
def _enable_custom_component(enable_custom_integrations):
    """Auto-enable custom integrations so our domain is discoverable."""


# ── sanitize_scene_name ──────────────────────────────────────────────


class TestSanitizeSceneName:
    def test_strips_whitespace(self) -> None:
        assert sanitize_scene_name("  Movie Time  ") == "Movie Time"

    def test_removes_html_tags(self) -> None:
        assert sanitize_scene_name("<script>alert('xss')</script>") == "scriptalert(xss)/script"

    def test_removes_angle_brackets(self) -> None:
        assert "<" not in sanitize_scene_name("Test <b>bold</b>")
        assert ">" not in sanitize_scene_name("Test <b>bold</b>")

    def test_removes_quotes(self) -> None:
        result = sanitize_scene_name('He said "hello"')
        assert '"' not in result

    def test_removes_backticks(self) -> None:
        result = sanitize_scene_name("Scene `name`")
        assert "`" not in result

    def test_removes_semicolons(self) -> None:
        result = sanitize_scene_name("Scene; DROP TABLE")
        assert ";" not in result

    def test_removes_braces_and_brackets(self) -> None:
        result = sanitize_scene_name("Scene {evil} [stuff]")
        assert "{" not in result
        assert "[" not in result

    def test_removes_backslashes(self) -> None:
        result = sanitize_scene_name("Scene\\name")
        assert "\\" not in result

    def test_truncates_long_names(self) -> None:
        long_name = "A" * 200
        result = sanitize_scene_name(long_name)
        assert len(result) == _MAX_SCENE_NAME_LEN

    def test_preserves_normal_names(self) -> None:
        assert sanitize_scene_name("Movie Night") == "Movie Night"
        assert sanitize_scene_name("Cozy Evening 2") == "Cozy Evening 2"

    def test_handles_empty_string(self) -> None:
        assert sanitize_scene_name("") == ""

    def test_handles_only_unsafe_chars(self) -> None:
        assert sanitize_scene_name("<>\"'`;{}[]\\") == ""


# ── validate_entities_exist ──────────────────────────────────────────


class TestValidateEntitiesExist:
    async def test_all_entities_exist(self, hass) -> None:
        hass.states.async_set("light.living_room", "on")
        hass.states.async_set("light.bedroom", "off")

        existing, missing = await validate_entities_exist(
            hass, ["light.living_room", "light.bedroom"]
        )
        assert existing == ["light.living_room", "light.bedroom"]
        assert missing == []

    async def test_some_entities_missing(self, hass) -> None:
        hass.states.async_set("light.living_room", "on")

        existing, missing = await validate_entities_exist(
            hass, ["light.living_room", "light.nonexistent"]
        )
        assert existing == ["light.living_room"]
        assert missing == ["light.nonexistent"]

    async def test_all_entities_missing(self, hass) -> None:
        existing, missing = await validate_entities_exist(
            hass, ["light.fake1", "light.fake2"]
        )
        assert existing == []
        assert missing == ["light.fake1", "light.fake2"]

    async def test_empty_list(self, hass) -> None:
        existing, missing = await validate_entities_exist(hass, [])
        assert existing == []
        assert missing == []


# ── validate_entities_in_area ────────────────────────────────────────


class TestValidateEntitiesInArea:
    async def test_entities_in_correct_area(self, hass) -> None:
        mock_area = MagicMock()
        mock_area.id = "area_lr"
        mock_area.name = "Living Room"
        mock_area_reg = MagicMock()
        mock_area_reg.async_list_areas.return_value = [mock_area]

        entry = MagicMock()
        entry.area_id = "area_lr"
        mock_entity_reg = MagicMock()
        mock_entity_reg.async_get.return_value = entry

        with (
            patch("homeassistant.helpers.area_registry.async_get", return_value=mock_area_reg),
            patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_entity_reg),
        ):
            in_area, out_of_area = await validate_entities_in_area(
                hass, ["light.living_room"], "Living Room"
            )

        assert in_area == ["light.living_room"]
        assert out_of_area == []

    async def test_entity_in_wrong_area(self, hass) -> None:
        mock_area = MagicMock()
        mock_area.id = "area_lr"
        mock_area.name = "Living Room"
        mock_area_reg = MagicMock()
        mock_area_reg.async_list_areas.return_value = [mock_area]

        entry = MagicMock()
        entry.area_id = "area_bedroom"
        mock_entity_reg = MagicMock()
        mock_entity_reg.async_get.return_value = entry

        with (
            patch("homeassistant.helpers.area_registry.async_get", return_value=mock_area_reg),
            patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_entity_reg),
        ):
            in_area, out_of_area = await validate_entities_in_area(
                hass, ["light.bedroom"], "Living Room"
            )

        assert in_area == []
        assert out_of_area == ["light.bedroom"]

    async def test_nonexistent_area(self, hass) -> None:
        mock_area_reg = MagicMock()
        mock_area_reg.async_list_areas.return_value = []

        with patch(
            "homeassistant.helpers.area_registry.async_get", return_value=mock_area_reg
        ):
            in_area, out_of_area = await validate_entities_in_area(
                hass, ["light.x"], "Nonexistent Room"
            )

        assert in_area == []
        assert out_of_area == ["light.x"]

    async def test_mixed_area_assignment(self, hass) -> None:
        mock_area = MagicMock()
        mock_area.id = "area_lr"
        mock_area.name = "Living Room"
        mock_area_reg = MagicMock()
        mock_area_reg.async_list_areas.return_value = [mock_area]

        lr_entry = MagicMock()
        lr_entry.area_id = "area_lr"
        br_entry = MagicMock()
        br_entry.area_id = "area_br"

        def _get_entry(eid):
            return lr_entry if eid == "light.living_room" else br_entry

        mock_entity_reg = MagicMock()
        mock_entity_reg.async_get.side_effect = _get_entry

        with (
            patch("homeassistant.helpers.area_registry.async_get", return_value=mock_area_reg),
            patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_entity_reg),
        ):
            in_area, out_of_area = await validate_entities_in_area(
                hass, ["light.living_room", "light.bedroom"], "Living Room"
            )

        assert in_area == ["light.living_room"]
        assert out_of_area == ["light.bedroom"]

    async def test_case_insensitive_area_matching(self, hass) -> None:
        mock_area = MagicMock()
        mock_area.id = "area_lr"
        mock_area.name = "Living Room"
        mock_area_reg = MagicMock()
        mock_area_reg.async_list_areas.return_value = [mock_area]

        entry = MagicMock()
        entry.area_id = "area_lr"
        mock_entity_reg = MagicMock()
        mock_entity_reg.async_get.return_value = entry

        with (
            patch("homeassistant.helpers.area_registry.async_get", return_value=mock_area_reg),
            patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_entity_reg),
        ):
            in_area, _ = await validate_entities_in_area(
                hass, ["light.x"], "living room"
            )

        assert in_area == ["light.x"]


# ── validate_scene_security ──────────────────────────────────────────


class TestValidateSceneSecurity:
    def test_valid_scene_is_safe(self) -> None:
        scene = {
            "name": "Movie Time",
            "entities": {"light.living_room": {"state": "on", "brightness": 51}},
        }
        is_safe, warnings = validate_scene_security(scene)
        assert is_safe
        assert warnings == []

    def test_rejects_non_dict_payload(self) -> None:
        is_safe, warnings = validate_scene_security("not a dict")
        assert not is_safe

    def test_rejects_non_string_name(self) -> None:
        scene = {"name": 123, "entities": {"light.x": {"state": "on"}}}
        is_safe, warnings = validate_scene_security(scene)
        assert not is_safe

    def test_warns_on_unsafe_name_characters(self) -> None:
        scene = {
            "name": '<script>alert("xss")</script>',
            "entities": {"light.x": {"state": "on"}},
        }
        is_safe, warnings = validate_scene_security(scene)
        assert is_safe  # warns but doesn't reject
        assert any("unsafe characters" in w for w in warnings)

    def test_warns_on_long_name(self) -> None:
        scene = {
            "name": "A" * 200,
            "entities": {"light.x": {"state": "on"}},
        }
        is_safe, warnings = validate_scene_security(scene)
        assert is_safe
        assert any(str(_MAX_SCENE_NAME_LEN) in w for w in warnings)

    def test_rejects_too_many_entities(self) -> None:
        entities = {f"light.room_{i}": {"state": "on"} for i in range(51)}
        scene = {"name": "Big Scene", "entities": entities}
        is_safe, warnings = validate_scene_security(scene)
        assert not is_safe
        assert any("maximum" in w.lower() or "50" in w for w in warnings)

    def test_rejects_non_dict_entities(self) -> None:
        scene = {"name": "Test", "entities": "not a dict"}
        is_safe, warnings = validate_scene_security(scene)
        assert not is_safe

    def test_rejects_non_string_entity_id(self) -> None:
        scene = {"name": "Test", "entities": {123: {"state": "on"}}}
        is_safe, warnings = validate_scene_security(scene)
        assert not is_safe

    def test_rejects_invalid_entity_id_format(self) -> None:
        scene = {
            "name": "Test",
            "entities": {"../../../etc/passwd": {"state": "on"}},
        }
        is_safe, warnings = validate_scene_security(scene)
        assert not is_safe
        assert any("entity_id" in w.lower() for w in warnings)

    def test_rejects_entity_id_with_special_chars(self) -> None:
        scene = {
            "name": "Test",
            "entities": {"light.<script>": {"state": "on"}},
        }
        is_safe, warnings = validate_scene_security(scene)
        assert not is_safe

    def test_rejects_non_dict_state_data(self) -> None:
        scene = {"name": "Test", "entities": {"light.x": "on"}}
        is_safe, warnings = validate_scene_security(scene)
        assert not is_safe

    def test_rejects_deeply_nested_state_data(self) -> None:
        scene = {
            "name": "Test",
            "entities": {
                "light.x": {
                    "state": "on",
                    "extra": {"nested": {"deep": {"very_deep": "value"}}},
                }
            },
        }
        is_safe, warnings = validate_scene_security(scene)
        assert not is_safe
        assert any("nesting" in w.lower() for w in warnings)

    def test_warns_on_long_string_values(self) -> None:
        scene = {
            "name": "Test",
            "entities": {"light.x": {"state": "a" * 300}},
        }
        is_safe, warnings = validate_scene_security(scene)
        assert is_safe  # warns but doesn't reject
        assert any("Long string" in w for w in warnings)

    def test_sql_injection_in_name(self) -> None:
        scene = {
            "name": "'; DROP TABLE scenes; --",
            "entities": {"light.x": {"state": "on"}},
        }
        is_safe, warnings = validate_scene_security(scene)
        assert is_safe  # warns on unsafe chars
        assert any("unsafe characters" in w for w in warnings)

    def test_template_injection_in_entity_id(self) -> None:
        scene = {
            "name": "Test",
            "entities": {"light.{{ malicious }}": {"state": "on"}},
        }
        is_safe, warnings = validate_scene_security(scene)
        assert not is_safe  # invalid entity_id format

    def test_empty_entities_dict(self) -> None:
        scene = {"name": "Test", "entities": {}}
        is_safe, warnings = validate_scene_security(scene)
        assert is_safe  # empty is technically safe, upstream validation rejects it

    def test_multiple_warnings_accumulated(self) -> None:
        scene = {
            "name": "A" * 200 + "<script>",
            "entities": {"light.x": {"state": "a" * 300}},
        }
        is_safe, warnings = validate_scene_security(scene)
        assert is_safe
        assert len(warnings) >= 2  # long name + unsafe chars + long value


# ── Integration: full pipeline validation ────────────────────────────


@pytest.mark.skipif(
    not _has_scene_modules(),
    reason="scene_utils/scene_state_mapper not yet merged (requires !103-!105)",
)
class TestFullPipelineValidation:
    """End-to-end tests combining scene_utils, scene_state_mapper, and scene_validation.

    These tests require the scene modules from MRs !103-!105 to be merged.
    They are skipped on branches where those modules are not yet available.
    """

    async def test_valid_scene_passes_all_checks(self, hass) -> None:
        from custom_components.selora_ai.scene_state_mapper import validate_entity_states
        from custom_components.selora_ai.scene_utils import validate_scene_payload

        hass.states.async_set("light.living_room", "on")

        scene = {
            "name": "Movie Time",
            "entities": {"light.living_room": {"state": "on", "brightness": 51}},
        }

        # Step 1: payload validation
        is_valid, reason, normalized = validate_scene_payload(scene)
        assert is_valid

        # Step 2: security checks
        is_safe, warnings = validate_scene_security(normalized)
        assert is_safe
        assert warnings == []

        # Step 3: state validation
        is_valid, reason, validated_entities = validate_entity_states(
            normalized["entities"]
        )
        assert is_valid

        # Step 4: entity existence check
        existing, missing = await validate_entities_exist(
            hass, list(normalized["entities"].keys())
        )
        assert missing == []

    async def test_nonexistent_entity_caught(self, hass) -> None:
        from custom_components.selora_ai.scene_utils import validate_scene_payload

        scene = {
            "name": "Bad Scene",
            "entities": {"light.does_not_exist": {"state": "on"}},
        }

        is_valid, _, normalized = validate_scene_payload(scene)
        assert is_valid  # format is valid

        # But entity doesn't exist in HA
        _, missing = await validate_entities_exist(
            hass, list(normalized["entities"].keys())
        )
        assert "light.does_not_exist" in missing

    async def test_invalid_brightness_caught_by_state_mapper(self, hass) -> None:
        from custom_components.selora_ai.scene_state_mapper import validate_entity_states

        entities = {"light.x": {"state": "on", "brightness": 999}}
        is_valid, _, normalized = validate_entity_states(entities)
        assert is_valid  # valid but clamped
        assert normalized["light.x"]["brightness"] == 255

    def test_injection_in_name_caught_by_security(self) -> None:
        scene = {
            "name": '<img src=x onerror="alert(1)">',
            "entities": {"light.x": {"state": "on"}},
        }
        _, warnings = validate_scene_security(scene)
        assert len(warnings) > 0

        # Sanitize cleans it
        clean_name = sanitize_scene_name(scene["name"])
        assert "<" not in clean_name
        assert ">" not in clean_name

    def test_path_traversal_in_entity_id_rejected(self) -> None:
        scene = {
            "name": "Test",
            "entities": {"../../etc/passwd": {"state": "on"}},
        }
        is_safe, _ = validate_scene_security(scene)
        assert not is_safe
