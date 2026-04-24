"""Tests for SceneStore -- lifecycle management for Selora-managed scenes."""

from __future__ import annotations

import pytest

from custom_components.selora_ai.scene_store import SceneStore


@pytest.fixture(autouse=True)
def _enable_custom_component(enable_custom_integrations):
    """Auto-enable custom integrations so our domain is discoverable."""


class MockStore:
    """Lightweight stand-in for homeassistant.helpers.storage.Store."""

    def __init__(self, initial_data=None):
        self._data = initial_data
        self.saved: list = []

    async def async_load(self):
        return self._data

    async def async_save(self, data):
        self.saved.append(data)


def _make_scene_store(hass, initial_data=None):
    """Create a SceneStore with a mock backing store."""
    store = SceneStore(hass)
    mock = MockStore(initial_data)
    store._store = mock
    return store, mock


class TestSceneStoreAdd:
    async def test_add_scene(self, hass) -> None:
        store, mock = _make_scene_store(hass)
        record = await store.async_add_scene(
            "s1", "Movie Time", 3, session_id="sess1", entity_id="scene.s1"
        )
        assert record["scene_id"] == "s1"
        assert record["name"] == "Movie Time"
        assert record["entity_count"] == 3
        assert record["session_id"] == "sess1"
        assert record["entity_id"] == "scene.s1"
        assert record["deleted_at"] is None
        assert len(mock.saved) == 1

    async def test_add_scene_without_entity_id(self, hass) -> None:
        store, _ = _make_scene_store(hass)
        record = await store.async_add_scene("s1", "Test", 2)
        assert record["entity_id"] is None

    async def test_refinement_preserves_lifecycle_metadata(self, hass) -> None:
        """Re-adding the same scene_id preserves created_at and session_id."""
        store, _ = _make_scene_store(hass)
        original = await store.async_add_scene(
            "s1", "Movie Time", 3, session_id="sess1", entity_id="scene.s1"
        )
        refined = await store.async_add_scene(
            "s1", "Movie Night", 5, session_id="sess2", entity_id="scene.s1_2"
        )
        assert refined["name"] == "Movie Night"
        assert refined["entity_count"] == 5
        assert refined["entity_id"] == "scene.s1_2"
        # Lifecycle provenance preserved from original
        assert refined["created_at"] == original["created_at"]
        assert refined["session_id"] == "sess1"
        assert refined["updated_at"] != original["created_at"]

    async def test_refinement_adopts_session_when_null(self, hass) -> None:
        """Refining a backfilled scene (session_id=None) adopts the active session."""
        store, _ = _make_scene_store(hass)
        await store.async_add_scene("s1", "Backfilled", 2)
        record = await store.async_get_scene("s1")
        assert record["session_id"] is None

        refined = await store.async_add_scene("s1", "Refined", 3, session_id="sess1")
        assert refined["session_id"] == "sess1"

    async def test_add_multiple_scenes(self, hass) -> None:
        store, mock = _make_scene_store(hass)
        await store.async_add_scene("s1", "Scene 1", 2)
        await store.async_add_scene("s2", "Scene 2", 4)
        scenes = await store.async_list_scenes()
        assert len(scenes) == 2


class TestSceneStoreUpdate:
    async def test_update_entity_count(self, hass) -> None:
        store, _ = _make_scene_store(hass)
        await store.async_add_scene("s1", "Test", 3)
        updated = await store.async_update_scene("s1", entity_count=5)
        assert updated["entity_count"] == 5

    async def test_update_name(self, hass) -> None:
        store, _ = _make_scene_store(hass)
        await store.async_add_scene("s1", "Old Name", 3)
        updated = await store.async_update_scene("s1", name="New Name")
        assert updated["name"] == "New Name"

    async def test_update_nonexistent_returns_none(self, hass) -> None:
        store, _ = _make_scene_store(hass)
        result = await store.async_update_scene("nonexistent", entity_count=5)
        assert result is None


class TestSceneStoreGet:
    async def test_get_existing_scene(self, hass) -> None:
        store, _ = _make_scene_store(hass)
        await store.async_add_scene("s1", "Test", 3)
        record = await store.async_get_scene("s1")
        assert record is not None
        assert record["name"] == "Test"

    async def test_get_nonexistent_returns_none(self, hass) -> None:
        store, _ = _make_scene_store(hass)
        record = await store.async_get_scene("nonexistent")
        assert record is None


class TestSceneStoreList:
    async def test_list_excludes_deleted_by_default(self, hass) -> None:
        store, _ = _make_scene_store(hass)
        await store.async_add_scene("s1", "Active", 2)
        await store.async_add_scene("s2", "Deleted", 3)
        await store.async_soft_delete("s2")
        scenes = await store.async_list_scenes()
        assert len(scenes) == 1
        assert scenes[0]["scene_id"] == "s1"

    async def test_list_includes_deleted_when_requested(self, hass) -> None:
        store, _ = _make_scene_store(hass)
        await store.async_add_scene("s1", "Active", 2)
        await store.async_add_scene("s2", "Deleted", 3)
        await store.async_soft_delete("s2")
        scenes = await store.async_list_scenes(include_deleted=True)
        assert len(scenes) == 2

    async def test_list_empty(self, hass) -> None:
        store, _ = _make_scene_store(hass)
        scenes = await store.async_list_scenes()
        assert scenes == []


class TestSceneStoreSoftDelete:
    async def test_soft_delete(self, hass) -> None:
        store, _ = _make_scene_store(hass)
        await store.async_add_scene("s1", "Test", 2)
        result = await store.async_soft_delete("s1")
        assert result is True
        record = await store.async_get_scene("s1")
        assert record["deleted_at"] is not None

    async def test_soft_delete_nonexistent_returns_false(self, hass) -> None:
        store, _ = _make_scene_store(hass)
        result = await store.async_soft_delete("nonexistent")
        assert result is False


class TestSceneStoreRestore:
    async def test_restore_deleted_scene(self, hass) -> None:
        store, _ = _make_scene_store(hass)
        await store.async_add_scene("s1", "Test", 2)
        await store.async_soft_delete("s1")
        result = await store.async_restore("s1")
        assert result is True
        record = await store.async_get_scene("s1")
        assert record["deleted_at"] is None

    async def test_restore_nonexistent_returns_false(self, hass) -> None:
        store, _ = _make_scene_store(hass)
        result = await store.async_restore("nonexistent")
        assert result is False

    async def test_restore_non_deleted_returns_false(self, hass) -> None:
        store, _ = _make_scene_store(hass)
        await store.async_add_scene("s1", "Test", 2)
        result = await store.async_restore("s1")
        assert result is False


class TestSceneStoreLoadExisting:
    async def test_loads_existing_data(self, hass) -> None:
        initial = {
            "scenes": {
                "s1": {
                    "scene_id": "s1",
                    "name": "Existing",
                    "entity_count": 4,
                    "entity_id": "scene.s1",
                    "session_id": None,
                    "created_at": "2025-01-01T00:00:00",
                    "updated_at": "2025-01-01T00:00:00",
                    "deleted_at": None,
                }
            }
        }
        store, _ = _make_scene_store(hass, initial_data=initial)
        scenes = await store.async_list_scenes()
        assert len(scenes) == 1
        assert scenes[0]["name"] == "Existing"


class TestSceneStoreBackfill:
    """Tests for the YAML → store migration on upgraded installs."""

    @staticmethod
    def _patch_yaml_read(yaml_scenes):
        """Return a context manager that mocks YAML reading for backfill."""
        from pathlib import Path
        from unittest.mock import patch

        return (
            patch(
                "custom_components.selora_ai.scene_utils._read_scenes_yaml",
                return_value=yaml_scenes,
            ),
            patch(
                "custom_components.selora_ai.scene_utils._get_scenes_path",
                return_value=Path("/fake/scenes.yaml"),
            ),
            patch(
                "custom_components.selora_ai.scene_utils.resolve_scene_entity_id",
                side_effect=lambda _hass, sid, _name=None: f"scene.{sid}",
            ),
        )

    async def test_backfill_imports_selora_scenes(self, hass) -> None:
        """Pre-existing Selora scenes in YAML are imported on first backfill."""
        yaml_scenes = [
            {"id": "selora_ai_scene_abc", "name": "[Selora AI] Movie Time", "entities": {"light.a": {}, "light.b": {}}},
            {"id": "user_scene_xyz", "name": "My Scene", "entities": {"light.c": {}}},
        ]
        store, _ = _make_scene_store(hass)
        p1, p2, p3 = self._patch_yaml_read(yaml_scenes)

        with p1, p2, p3:
            count = await store.async_reconcile_yaml()

        assert count == 1
        scenes = await store.async_list_scenes()
        assert len(scenes) == 1
        assert scenes[0]["scene_id"] == "selora_ai_scene_abc"
        assert scenes[0]["name"] == "Movie Time"
        assert scenes[0]["entity_count"] == 2
        assert scenes[0]["entity_id"] == "scene.selora_ai_scene_abc"

    async def test_refreshes_metadata_for_active_scene(self, hass) -> None:
        """External YAML edits to an active scene are picked up."""
        yaml_scenes = [
            {"id": "selora_ai_scene_abc", "name": "[Selora AI] New Name", "entities": {"light.a": {}}},
        ]
        store, _ = _make_scene_store(hass)
        original = await store.async_add_scene(
            "selora_ai_scene_abc", "Original Name", 3, session_id="sess1"
        )

        p1, p2, p3 = self._patch_yaml_read(yaml_scenes)
        with p1, p2, p3:
            count = await store.async_reconcile_yaml()

        # Not counted as imported (already existed)
        assert count == 0
        record = await store.async_get_scene("selora_ai_scene_abc")
        # Metadata refreshed from YAML
        assert record["name"] == "New Name"
        assert record["entity_count"] == 1
        # Provenance preserved
        assert record["session_id"] == "sess1"
        assert record["created_at"] == original["created_at"]

    async def test_throttles_within_interval(self, hass) -> None:
        """Second call within the TTL window is a no-op."""
        store, _ = _make_scene_store(hass)

        p1, p2, p3 = self._patch_yaml_read([])
        with p1, p2, p3:
            await store.async_reconcile_yaml()
            # Second call within interval — throttled
            count = await store.async_reconcile_yaml()

        assert count == 0

    async def test_retries_after_transient_failure(self, hass) -> None:
        """A transient read failure leaves the timer unset so the next call retries."""
        from pathlib import Path
        from unittest.mock import patch

        store, _ = _make_scene_store(hass)

        # First call: simulate a read failure
        with (
            patch(
                "custom_components.selora_ai.scene_utils._read_scenes_yaml",
                side_effect=OSError("disk error"),
            ),
            patch(
                "custom_components.selora_ai.scene_utils._get_scenes_path",
                return_value=Path("/fake/scenes.yaml"),
            ),
        ):
            count = await store.async_reconcile_yaml()
        assert count == 0
        assert store._last_reconcile == 0.0

        # Second call: YAML is now readable — retries immediately
        yaml_scenes = [
            {"id": "selora_ai_scene_abc", "name": "[Selora AI] Test", "entities": {"light.a": {}}},
        ]
        p1, p2, p3 = self._patch_yaml_read(yaml_scenes)
        with p1, p2, p3:
            count = await store.async_reconcile_yaml()
        assert count == 1
        assert store._last_reconcile > 0.0

    async def test_backfill_reconciles_removed_scenes(self, hass) -> None:
        """Scenes in the store but missing from YAML are soft-deleted."""
        store, _ = _make_scene_store(hass)
        # Pre-populate the store with a scene that no longer exists in YAML
        await store.async_add_scene("selora_ai_scene_old", "Old Scene", 2)

        # YAML only has a different scene
        yaml_scenes = [
            {"id": "selora_ai_scene_new", "name": "[Selora AI] New", "entities": {"light.a": {}}},
        ]
        p1, p2, p3 = self._patch_yaml_read(yaml_scenes)
        with p1, p2, p3:
            count = await store.async_reconcile_yaml()

        # New scene was imported
        assert count == 1
        # Old scene was soft-deleted
        old = await store.async_get_scene("selora_ai_scene_old")
        assert old is not None
        assert old["deleted_at"] is not None
        # Active list only shows the new scene
        active = await store.async_list_scenes()
        assert len(active) == 1
        assert active[0]["scene_id"] == "selora_ai_scene_new"

    async def test_backfill_restores_deleted_scene_from_yaml(self, hass) -> None:
        """A soft-deleted scene that reappears in YAML is restored."""
        store, _ = _make_scene_store(hass)
        await store.async_add_scene(
            "selora_ai_scene_abc", "Original", 2, session_id="sess1"
        )
        await store.async_soft_delete("selora_ai_scene_abc")

        # Scene reappears in YAML (e.g. backup restore)
        yaml_scenes = [
            {"id": "selora_ai_scene_abc", "name": "[Selora AI] Restored", "entities": {"light.a": {}, "light.b": {}, "light.c": {}}},
        ]
        p1, p2, p3 = self._patch_yaml_read(yaml_scenes)
        with p1, p2, p3:
            count = await store.async_reconcile_yaml()

        assert count == 1
        record = await store.async_get_scene("selora_ai_scene_abc")
        assert record["deleted_at"] is None
        assert record["name"] == "Restored"
        assert record["entity_count"] == 3
        # Original provenance preserved
        assert record["session_id"] == "sess1"
