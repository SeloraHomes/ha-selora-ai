"""Tests for scene_utils — validation, creation, and listing.

Also covers scene intent parsing in LLMClient's response parsers.
"""

from __future__ import annotations

import json

import pytest

from custom_components.selora_ai.llm_client import LLMClient
from custom_components.selora_ai.scene_utils import (
    async_create_scene,
    generate_scene_id,
    validate_scene_payload,
)


@pytest.fixture(autouse=True)
def _enable_custom_component(enable_custom_integrations):
    """Auto-enable custom integrations so our domain is discoverable."""


# ── validate_scene_payload ───────────────────────────────────────────


class TestValidateScenePayload:
    """Tests for validate_scene_payload."""

    def test_valid_single_entity(self) -> None:
        scene = {
            "name": "Movie Time",
            "entities": {"light.living_room": {"state": "on", "brightness": 51}},
        }
        is_valid, reason, normalized = validate_scene_payload(scene)
        assert is_valid
        assert normalized is not None
        assert normalized["name"] == "Movie Time"
        assert "light.living_room" in normalized["entities"]

    def test_valid_multiple_entities(self) -> None:
        scene = {
            "name": "Bedtime",
            "entities": {
                "light.bedroom": {"state": "off"},
                "light.hallway": {"state": "on", "brightness": 25},
            },
        }
        is_valid, reason, normalized = validate_scene_payload(scene)
        assert is_valid
        assert len(normalized["entities"]) == 2

    def test_rejects_non_dict(self) -> None:
        is_valid, reason, normalized = validate_scene_payload("not a dict")
        assert not is_valid
        assert "dict" in reason.lower()
        assert normalized is None

    def test_rejects_missing_name(self) -> None:
        scene = {"entities": {"light.living_room": {"state": "on"}}}
        is_valid, reason, _ = validate_scene_payload(scene)
        assert not is_valid
        assert "name" in reason.lower()

    def test_rejects_empty_name(self) -> None:
        scene = {"name": "  ", "entities": {"light.living_room": {"state": "on"}}}
        is_valid, reason, _ = validate_scene_payload(scene)
        assert not is_valid
        assert "name" in reason.lower()

    def test_rejects_non_string_name(self) -> None:
        scene = {"name": 123, "entities": {"light.living_room": {"state": "on"}}}
        is_valid, reason, _ = validate_scene_payload(scene)
        assert not is_valid
        assert "string" in reason.lower()

    def test_rejects_missing_entities(self) -> None:
        scene = {"name": "Test"}
        is_valid, reason, _ = validate_scene_payload(scene)
        assert not is_valid
        assert "entities" in reason.lower()

    def test_rejects_empty_entities(self) -> None:
        scene = {"name": "Test", "entities": {}}
        is_valid, reason, _ = validate_scene_payload(scene)
        assert not is_valid
        assert "entities" in reason.lower()

    def test_rejects_invalid_entity_id_format(self) -> None:
        scene = {"name": "Test", "entities": {"not_valid": {"state": "on"}}}
        is_valid, reason, _ = validate_scene_payload(scene)
        assert not is_valid
        assert "entity_id" in reason.lower()

    def test_rejects_entity_without_state(self) -> None:
        scene = {"name": "Test", "entities": {"light.living_room": {"brightness": 51}}}
        is_valid, reason, _ = validate_scene_payload(scene)
        assert not is_valid
        assert "state" in reason.lower()

    def test_rejects_entity_non_dict_state(self) -> None:
        scene = {"name": "Test", "entities": {"light.living_room": "on"}}
        is_valid, reason, _ = validate_scene_payload(scene)
        assert not is_valid
        assert "dict" in reason.lower()

    def test_coerces_boolean_state_to_string(self) -> None:
        scene = {"name": "Test", "entities": {"light.living_room": {"state": True}}}
        is_valid, _, normalized = validate_scene_payload(scene)
        assert is_valid
        assert normalized["entities"]["light.living_room"]["state"] == "on"

    def test_coerces_false_state_to_off(self) -> None:
        scene = {"name": "Test", "entities": {"light.living_room": {"state": False}}}
        is_valid, _, normalized = validate_scene_payload(scene)
        assert is_valid
        assert normalized["entities"]["light.living_room"]["state"] == "off"

    def test_coerces_numeric_state_to_string(self) -> None:
        scene = {"name": "Test", "entities": {"sensor.temperature": {"state": 23.5}}}
        is_valid, _, normalized = validate_scene_payload(scene)
        assert is_valid
        assert normalized["entities"]["sensor.temperature"]["state"] == "23.5"

    def test_accepts_hyphens_in_entity_id(self) -> None:
        scene = {"name": "Test", "entities": {"light.living-room": {"state": "on"}}}
        is_valid, _, normalized = validate_scene_payload(scene)
        assert is_valid
        assert "light.living-room" in normalized["entities"]

    def test_normalizes_mixed_case_entity_id(self) -> None:
        scene = {"name": "Test", "entities": {"Light.Living_Room": {"state": "on"}}}
        is_valid, _, normalized = validate_scene_payload(scene)
        assert is_valid
        assert "light.living_room" in normalized["entities"]

    def test_rejects_multi_domain_entities(self) -> None:
        scene = {
            "name": "Test",
            "entities": {
                "light.living_room": {"state": "on"},
                "media_player.tv": {"state": "on"},
            },
        }
        is_valid, reason, _ = validate_scene_payload(scene)
        assert not is_valid
        assert "single domain" in reason.lower()

    def test_accepts_single_domain_multiple_entities(self) -> None:
        scene = {
            "name": "Test",
            "entities": {
                "light.living_room": {"state": "on"},
                "light.kitchen": {"state": "off"},
            },
        }
        is_valid, _, normalized = validate_scene_payload(scene)
        assert is_valid
        assert len(normalized["entities"]) == 2


# ── validate_scene_payload with hass (entity existence) ──────────────


class TestValidateScenePayloadWithHass:
    """Tests that validate_scene_payload checks entity existence when hass is provided."""

    def test_accepts_existing_entity(self, hass) -> None:
        hass.states.async_set("light.living_room", "on")
        scene = {"name": "Test", "entities": {"light.living_room": {"state": "on"}}}
        is_valid, _, normalized = validate_scene_payload(scene, hass)
        assert is_valid

    def test_rejects_nonexistent_entity(self, hass) -> None:
        scene = {"name": "Test", "entities": {"light.does_not_exist": {"state": "on"}}}
        is_valid, reason, _ = validate_scene_payload(scene, hass)
        assert not is_valid
        assert "does not exist" in reason

    def test_skips_existence_check_without_hass(self) -> None:
        """Without hass, only format is validated — hallucinated IDs pass."""
        scene = {"name": "Test", "entities": {"light.does_not_exist": {"state": "on"}}}
        is_valid, _, _ = validate_scene_payload(scene)
        assert is_valid


# ── generate_scene_id ────────────────────────────────────────────────


class TestGenerateSceneId:
    def test_has_prefix(self) -> None:
        scene_id = generate_scene_id()
        assert scene_id.startswith("selora_ai_scene_")

    def test_unique(self) -> None:
        ids = {generate_scene_id() for _ in range(100)}
        assert len(ids) == 100


# ── _write_scenes_yaml boolean handling ──────────────────────────────


class TestWriteScenesYamlBooleans:
    """Boolean attributes must survive YAML round-trips correctly."""

    def test_preserves_real_boolean_attributes(self, tmp_path) -> None:
        """Real bool values (e.g. climate flags) stay as booleans."""
        from pathlib import Path

        from custom_components.selora_ai.scene_utils import _read_scenes_yaml, _write_scenes_yaml

        scenes_path = tmp_path / "scenes.yaml"
        scenes = [
            {
                "id": "test_1",
                "name": "Climate Test",
                "entities": {
                    "climate.living_room": {
                        "state": "heat",
                        "aux_heat": True,
                    },
                },
            }
        ]
        _write_scenes_yaml(scenes_path, scenes)
        reloaded = _read_scenes_yaml(scenes_path)
        assert reloaded[0]["entities"]["climate.living_room"]["aux_heat"] is True

    def test_quotes_string_on_off_values(self, tmp_path) -> None:
        """String 'on'/'off' must be quoted to survive YAML 1.1 parsing."""
        from custom_components.selora_ai.scene_utils import _write_scenes_yaml

        scenes_path = tmp_path / "scenes.yaml"
        scenes = [
            {
                "id": "test_2",
                "name": "Light Test",
                "entities": {"light.bedroom": {"state": "on"}},
            }
        ]
        _write_scenes_yaml(scenes_path, scenes)
        raw = scenes_path.read_text()
        # The string "on" must appear quoted in the output
        assert '"on"' in raw

    def test_quotes_time_like_values(self, tmp_path) -> None:
        """HH:MM:SS strings must be quoted to prevent sexagesimal conversion."""
        from custom_components.selora_ai.scene_utils import _read_scenes_yaml, _write_scenes_yaml

        scenes_path = tmp_path / "scenes.yaml"
        scenes = [
            {
                "id": "test_3",
                "name": "Alarm Test",
                "entities": {"input_datetime.alarm": {"state": "23:46:00"}},
            }
        ]
        _write_scenes_yaml(scenes_path, scenes)
        reloaded = _read_scenes_yaml(scenes_path)
        # Must round-trip as the original string, not as 85560
        assert reloaded[0]["entities"]["input_datetime.alarm"]["state"] == "23:46:00"


# ── _read_scenes_yaml error handling ────────────────────────────────


class TestReadScenesYamlErrors:
    def test_raises_on_corrupt_yaml(self, tmp_path) -> None:
        """Corrupt YAML must raise instead of silently returning empty."""
        from custom_components.selora_ai.scene_utils import ScenesYamlError, _read_scenes_yaml

        scenes_path = tmp_path / "scenes.yaml"
        scenes_path.write_text("{ invalid yaml [[[", encoding="utf-8")
        with pytest.raises(ScenesYamlError, match="Failed to parse"):
            _read_scenes_yaml(scenes_path)

    def test_raises_on_non_list_content(self, tmp_path) -> None:
        """A YAML file that is valid but not a list must raise."""
        from custom_components.selora_ai.scene_utils import ScenesYamlError, _read_scenes_yaml

        scenes_path = tmp_path / "scenes.yaml"
        scenes_path.write_text("key: value\n", encoding="utf-8")
        with pytest.raises(ScenesYamlError, match="YAML list"):
            _read_scenes_yaml(scenes_path)

    def test_returns_empty_for_missing_file(self, tmp_path) -> None:
        from custom_components.selora_ai.scene_utils import _read_scenes_yaml

        scenes_path = tmp_path / "scenes.yaml"
        assert _read_scenes_yaml(scenes_path) == []

    def test_returns_empty_for_comment_only_file(self, tmp_path) -> None:
        """A file with only comments should be treated as empty, not corrupt."""
        from custom_components.selora_ai.scene_utils import _read_scenes_yaml

        scenes_path = tmp_path / "scenes.yaml"
        scenes_path.write_text("# Managed by Selora AI\n---\n", encoding="utf-8")
        assert _read_scenes_yaml(scenes_path) == []


# ── async_create_scene ───────────────────────────────────────────────


class TestAsyncCreateScene:
    async def test_writes_scenes_yaml_and_reloads(self, hass, monkeypatch) -> None:
        import custom_components.selora_ai.scene_utils as su

        fixed_id = su.generate_scene_id()  # unique per run
        monkeypatch.setattr(su, "generate_scene_id", lambda: fixed_id)

        service_calls: list[tuple[str, str, dict]] = []

        async def _track_reload(call):
            service_calls.append((call.domain, call.service, dict(call.data)))
            # Simulate HA loading the scene entity after reload.
            # HA derives entity_id from slugify(name); id is only unique_id.
            hass.states.async_set(
                "scene.selora_ai_movie_time", "scening",
                {"friendly_name": "[Selora AI] Movie Time"},
            )

        hass.services.async_register("scene", "reload", _track_reload)

        scene_data = {
            "name": "Movie Time",
            "entities": {"light.living_room": {"state": "on", "brightness": 51}},
        }
        result = await async_create_scene(hass, scene_data)

        assert result["success"] is True
        assert result["name"] == "Movie Time"
        assert result["entity_count"] == 1
        assert result["scene_id"].startswith("selora_ai_scene_")

        # scene.reload is called so the new entity appears in HA
        assert len(service_calls) == 1
        assert service_calls[0][0] == "scene"
        assert service_calls[0][1] == "reload"

        # The scene was persisted to scenes.yaml
        from pathlib import Path

        from custom_components.selora_ai.scene_utils import _read_scenes_yaml

        scenes_path = Path(hass.config.config_dir) / "scenes.yaml"
        scenes = _read_scenes_yaml(scenes_path)
        our_scene = [s for s in scenes if s.get("id") == result["scene_id"]]
        assert len(our_scene) == 1
        assert our_scene[0]["name"] == "[Selora AI] Movie Time"
        assert "light.living_room" in our_scene[0]["entities"]

    async def test_does_not_apply_scene_on_creation(self, hass, monkeypatch) -> None:
        """Creating a scene should NOT drive entity states immediately."""
        import custom_components.selora_ai.scene_utils as su

        fixed_id = "selora_ai_scene_noapply"
        monkeypatch.setattr(su, "generate_scene_id", lambda: fixed_id)

        service_calls: list[tuple[str, str, dict]] = []

        async def _track(call):
            service_calls.append((call.domain, call.service, dict(call.data)))

        hass.services.async_register("scene", "apply", _track)

        # Simulate successful reload that registers the scene entity
        # HA derives entity_id from slugify(name); id is only unique_id.
        async def _reload(call):
            hass.states.async_set("scene.selora_ai_test", "scening")

        hass.services.async_register("scene", "reload", _reload)

        scene_data = {
            "name": "Test",
            "entities": {"light.living_room": {"state": "off"}},
        }
        await async_create_scene(hass, scene_data)

        # scene.apply must NOT be called
        apply_calls = [c for c in service_calls if c[1] == "apply"]
        assert len(apply_calls) == 0

    async def test_raises_and_rolls_back_when_scene_not_loaded(self, hass) -> None:
        """If HA doesn't load the scene, raise and remove it from scenes.yaml."""
        from pathlib import Path

        from custom_components.selora_ai.scene_utils import (
            SceneCreateError,
            _read_scenes_yaml,
        )

        # Reload is a no-op — doesn't register the scene entity
        hass.services.async_register("scene", "reload", lambda _: None)

        scenes_path = Path(hass.config.config_dir) / "scenes.yaml"
        scenes_before = _read_scenes_yaml(scenes_path)

        scene_data = {
            "name": "Ghost Scene",
            "entities": {"light.living_room": {"state": "on"}},
        }
        with pytest.raises(SceneCreateError, match="did not load"):
            await async_create_scene(hass, scene_data)

        # scenes.yaml must be rolled back — the new entry must not be on disk
        scenes_after = _read_scenes_yaml(scenes_path)
        assert len(scenes_after) == len(scenes_before)
        scene_ids_before = {s.get("id") for s in scenes_before}
        scene_ids_after = {s.get("id") for s in scenes_after}
        assert scene_ids_before == scene_ids_after

    async def test_rollback_deletes_file_when_it_did_not_exist(self, hass, tmp_path) -> None:
        """If scenes.yaml didn't exist before, rollback removes it entirely."""
        from custom_components.selora_ai.scene_utils import SceneCreateError

        # Point hass config at a clean tmp dir with no scenes.yaml
        hass.config.config_dir = str(tmp_path)
        scenes_path = tmp_path / "scenes.yaml"
        assert not scenes_path.exists()

        # Reload is a no-op — scene won't appear
        hass.services.async_register("scene", "reload", lambda _: None)

        scene_data = {
            "name": "Phantom",
            "entities": {"light.living_room": {"state": "on"}},
        }
        with pytest.raises(SceneCreateError):
            await async_create_scene(hass, scene_data)

        # The file must not exist — rollback should delete, not write []
        assert not scenes_path.exists()


# ── LLM parse_architect_response with scene intent ───────────────────


class TestParseArchitectSceneResponse:
    """Tests that _parse_architect_response handles scene intent."""

    def _make_client(self, hass) -> LLMClient:
        from custom_components.selora_ai.providers import create_provider

        provider = create_provider("anthropic", hass, api_key="test-key")
        return LLMClient(hass, provider=provider)

    def test_parses_scene_intent_json(self, hass) -> None:
        hass.states.async_set("light.living_room", "on")
        client = self._make_client(hass)
        response_json = json.dumps(
            {
                "intent": "scene",
                "response": "Created Movie Time scene.",
                "scene": {
                    "name": "Movie Time",
                    "entities": {"light.living_room": {"state": "on", "brightness": 51}},
                },
            }
        )
        result = client._parse_architect_response(response_json)
        assert result["intent"] == "scene"
        assert result["scene"]["name"] == "Movie Time"
        assert "scene_yaml" in result

    def test_infers_scene_intent_from_content(self, hass) -> None:
        hass.states.async_set("light.bedroom", "off")
        client = self._make_client(hass)
        response_json = json.dumps(
            {
                "response": "Created it.",
                "scene": {
                    "name": "Bedtime",
                    "entities": {"light.bedroom": {"state": "off"}},
                },
            }
        )
        result = client._parse_architect_response(response_json)
        assert result["intent"] == "scene"

    def test_invalid_scene_falls_back_to_answer(self, hass) -> None:
        client = self._make_client(hass)
        response_json = json.dumps(
            {
                "intent": "scene",
                "response": "Here it is.",
                "scene": {"name": "", "entities": {}},
            }
        )
        result = client._parse_architect_response(response_json)
        assert result["intent"] == "answer"
        assert "validation_error" in result

    def test_empty_scene_object_triggers_validation_error(self, hass) -> None:
        """An empty scene dict {} must not silently pass as a valid scene."""
        client = self._make_client(hass)
        response_json = json.dumps(
            {
                "intent": "scene",
                "response": "Here's the scene.",
                "scene": {},
            }
        )
        result = client._parse_architect_response(response_json)
        assert result["intent"] == "answer"
        assert "validation_error" in result


# ── LLM parse_streamed_response with scene fenced block ──────────────


class TestParseStreamedSceneResponse:
    """Tests that parse_streamed_response handles ```scene``` blocks."""

    def _make_client(self, hass) -> LLMClient:
        from custom_components.selora_ai.providers import create_provider

        provider = create_provider("anthropic", hass, api_key="test-key")
        return LLMClient(hass, provider=provider)

    def test_parses_scene_fenced_block(self, hass) -> None:
        hass.states.async_set("light.living_room", "on")
        client = self._make_client(hass)
        text = (
            "Here's your movie scene.\n\n"
            "```scene\n"
            '{"name": "Movie Time", "entities": {"light.living_room": {"state": "on", "brightness": 51}}}\n'
            "```"
        )
        result = client.parse_streamed_response(text)
        assert result["intent"] == "scene"
        assert result["scene"]["name"] == "Movie Time"
        assert "scene_yaml" in result
        assert "Movie Time" in result["scene_yaml"]

    def test_invalid_scene_block_falls_back(self, hass) -> None:
        client = self._make_client(hass)
        text = 'Sure thing.\n\n```scene\n{"name": "", "entities": {}}\n```'
        result = client.parse_streamed_response(text)
        assert result["intent"] == "answer"
        assert "validation_error" in result

    def test_malformed_json_in_scene_block(self, hass) -> None:
        client = self._make_client(hass)
        text = "Here.\n\n```scene\n{not valid json}\n```"
        result = client.parse_streamed_response(text)
        # Falls back to JSON parser, which treats it as answer
        assert result["intent"] == "answer"

    def test_mid_text_scene_block_is_not_actionable(self, hass) -> None:
        """A scene block followed by more text is informational, not a command."""
        client = self._make_client(hass)
        text = (
            "Here's an example scene definition:\n\n"
            "```scene\n"
            '{"name": "Movie Time", "entities": {"light.living_room": {"state": "on"}}}\n'
            "```\n\n"
            "You can customize the brightness and color temperature as needed."
        )
        result = client.parse_streamed_response(text)
        assert result["intent"] == "answer"
        assert "scene" not in result or result.get("scene") is None

    def test_terminal_scene_block_with_trailing_whitespace(self, hass) -> None:
        """A scene block at the end with trailing whitespace is still actionable."""
        hass.states.async_set("light.living_room", "on")
        client = self._make_client(hass)
        text = (
            "Here's your scene.\n\n"
            "```scene\n"
            '{"name": "Cozy", "entities": {"light.living_room": {"state": "on"}}}\n'
            "```\n\n"
        )
        result = client.parse_streamed_response(text)
        assert result["intent"] == "scene"


# ── System prompt includes scene instructions ────────────────────────


class TestScenePromptInstructions:
    def _make_client(self, hass) -> LLMClient:
        from custom_components.selora_ai.providers import create_provider

        provider = create_provider("anthropic", hass, api_key="test-key")
        return LLMClient(hass, provider=provider)

    def test_json_prompt_has_scene_intent(self, hass) -> None:
        prompt = self._make_client(hass)._build_architect_system_prompt()
        assert '"intent": "scene"' in prompt
        assert '"scene"' in prompt
        assert "SCENE RULES" in prompt
        assert "SAME domain" in prompt

    def test_stream_prompt_has_scene_block(self, hass) -> None:
        prompt = self._make_client(hass)._build_architect_stream_system_prompt()
        assert "```scene" in prompt
        assert "SCENE RULES" in prompt
        assert '"name"' in prompt
        assert "SAME domain" in prompt
