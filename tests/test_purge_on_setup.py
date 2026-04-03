"""Tests for orphaned entity cleanup on startup."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from custom_components.selora_ai.automation_utils import (
    async_cleanup_orphaned_entities,
)
from custom_components.selora_ai.const import AUTOMATION_ID_PREFIX

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_automations_yaml(tmp_path: Path, automations: list[dict]) -> None:
    """Write a test automations.yaml."""
    yaml_path = tmp_path / "automations.yaml"
    yaml_path.write_text(yaml.dump(automations, default_flow_style=False), encoding="utf-8")


def _make_hass(tmp_path: Path) -> MagicMock:
    """Build a minimal mock hass with config_dir pointing to tmp_path."""
    hass = MagicMock()
    hass.config.config_dir = str(tmp_path)
    hass.services.async_call = AsyncMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *a: fn(*a))
    return hass


def _make_mock_entity_registry(entities: list[dict]) -> MagicMock:
    """Build a mock entity registry with the given entities."""
    reg = MagicMock()
    mock_entities = {}
    for e in entities:
        entity = MagicMock()
        entity.entity_id = e["entity_id"]
        entity.unique_id = e["unique_id"]
        entity.platform = e.get("platform", "automation")
        mock_entities[e["entity_id"]] = entity
    reg.entities = MagicMock()
    reg.entities.values.return_value = list(mock_entities.values())
    reg.async_remove = MagicMock()
    return reg


class TestStartupOrphanCleanup:
    """Test that startup removes orphaned entity registry entries."""

    @pytest.mark.asyncio
    async def test_removes_orphaned_selora_entities(self, tmp_path: Path):
        """Entities without matching YAML are removed; matched ones are kept."""
        automations = [
            {
                "id": f"{AUTOMATION_ID_PREFIX}active01",
                "alias": "Active",
                "trigger": [],
                "action": [],
            },
        ]
        _write_automations_yaml(tmp_path, automations)

        mock_reg = _make_mock_entity_registry(
            [
                {
                    "entity_id": "automation.active",
                    "unique_id": f"{AUTOMATION_ID_PREFIX}active01",
                    "platform": "automation",
                },
                {
                    "entity_id": "automation.orphan",
                    "unique_id": f"{AUTOMATION_ID_PREFIX}orphan01",
                    "platform": "automation",
                },
                {
                    "entity_id": "automation.user_auto",
                    "unique_id": "user_auto_123",
                    "platform": "automation",
                },
            ]
        )
        hass = _make_hass(tmp_path)

        with patch(
            "homeassistant.helpers.entity_registry.async_get",
            return_value=mock_reg,
        ):
            removed = await async_cleanup_orphaned_entities(hass)

        assert f"{AUTOMATION_ID_PREFIX}orphan01" in removed
        assert f"{AUTOMATION_ID_PREFIX}active01" not in removed
        assert "user_auto_123" not in removed
        assert mock_reg.async_remove.call_count == 1

    @pytest.mark.asyncio
    async def test_noop_when_no_orphans(self, tmp_path: Path):
        """No orphans means empty return."""
        automations = [
            {
                "id": f"{AUTOMATION_ID_PREFIX}active01",
                "alias": "Active",
                "trigger": [],
                "action": [],
            },
        ]
        _write_automations_yaml(tmp_path, automations)

        mock_reg = _make_mock_entity_registry(
            [
                {
                    "entity_id": "automation.active",
                    "unique_id": f"{AUTOMATION_ID_PREFIX}active01",
                    "platform": "automation",
                },
            ]
        )
        hass = _make_hass(tmp_path)

        with patch(
            "homeassistant.helpers.entity_registry.async_get",
            return_value=mock_reg,
        ):
            removed = await async_cleanup_orphaned_entities(hass)

        assert removed == []
        assert mock_reg.async_remove.call_count == 0
