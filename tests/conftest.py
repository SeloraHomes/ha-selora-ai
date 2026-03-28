"""Shared fixtures for Selora AI integration tests.

Uses pytest-homeassistant-custom-component for the real ``hass`` fixture
instead of a bare MagicMock.  This ensures tests exercise actual HA APIs
(registries, config entries, services, event loop) rather than silently
accepting any attribute access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

# Register HA's test plugin — this provides the ``hass`` fixture, storage
# mocks, ``enable_custom_integrations``, and many other helpers.
pytest_plugins = "pytest_homeassistant_custom_component"


# ── Automations YAML helper ──────────────────────────────────────────


@pytest.fixture
def tmp_automations_yaml(hass) -> Path:
    """Provide a pre-populated automations.yaml in hass.config.config_dir."""
    path = Path(hass.config.config_dir) / "automations.yaml"
    automations = [
        {
            "id": "selora_ai_existing1",
            "alias": "[Selora AI] Turn on porch light at sunset",
            "description": "Auto-generated",
            "initial_state": True,
            "trigger": [{"platform": "sun", "event": "sunset"}],
            "condition": [],
            "action": [
                {
                    "action": "light.turn_on",
                    "target": {"entity_id": "light.porch"},
                }
            ],
            "mode": "single",
        },
        {
            "id": "manual_automation_1",
            "alias": "My manual automation",
            "trigger": [{"platform": "time", "at": "08:00"}],
            "condition": [],
            "action": [
                {
                    "action": "light.turn_on",
                    "target": {"entity_id": "light.kitchen"},
                }
            ],
        },
    ]
    path.write_text(yaml.dump(automations, default_flow_style=False), encoding="utf-8")
    return path


# ── Sample data fixtures ─────────────────────────────────────────────


@pytest.fixture
def sample_automation() -> dict[str, Any]:
    """Return a valid automation dict for testing."""
    return {
        "alias": "Test automation",
        "description": "A test automation",
        "trigger": [{"platform": "time", "at": "08:00"}],
        "condition": [],
        "action": [
            {
                "action": "light.turn_on",
                "target": {"entity_id": "light.living_room"},
            }
        ],
        "mode": "single",
    }


@pytest.fixture
def sample_time_pattern() -> dict[str, Any]:
    """Return a realistic time-based pattern."""
    return {
        "pattern_id": "pat_time_001",
        "type": "time_based",
        "entity_ids": ["light.living_room"],
        "description": "Living Room turns on around 18:00 on weekdays",
        "evidence": {
            "_signature": "light.living_room:on:72:True",
            "time_slot": "18:00",
            "is_weekday": True,
            "target_state": "on",
            "occurrences": 5,
            "total_days": 7,
        },
        "confidence": 0.71,
    }


@pytest.fixture
def sample_correlation_pattern() -> dict[str, Any]:
    """Return a realistic correlation pattern."""
    return {
        "pattern_id": "pat_corr_001",
        "type": "correlation",
        "entity_ids": ["binary_sensor.front_door", "light.hallway"],
        "description": "Hallway turns on within 30s of Front Door turning on",
        "evidence": {
            "_signature": "binary_sensor.front_door:on->light.hallway:on",
            "trigger_entity": "binary_sensor.front_door",
            "trigger_state": "on",
            "response_entity": "light.hallway",
            "response_state": "on",
            "avg_delay_seconds": 30.5,
            "co_occurrences": 8,
            "window_minutes": 5,
        },
        "confidence": 0.8,
    }


@pytest.fixture
def sample_sequence_pattern() -> dict[str, Any]:
    """Return a realistic sequence pattern."""
    return {
        "pattern_id": "pat_seq_001",
        "type": "sequence",
        "entity_ids": ["light.living_room", "cover.blinds"],
        "description": "When Living Room changes from off to on, Blinds turns open",
        "evidence": {
            "_signature": "light.living_room:off->on=>cover.blinds:open",
            "trigger_entity": "light.living_room",
            "trigger_from": "off",
            "trigger_to": "on",
            "response_entity": "cover.blinds",
            "response_state": "open",
            "occurrences": 6,
            "window_minutes": 5,
        },
        "confidence": 0.75,
    }


# ── Store mock (still needed: we don't want tests to hit real disk) ──


class MockStore:
    """A lightweight stand-in for homeassistant.helpers.storage.Store.

    We still mock Store at the *module* level in individual test files to avoid
    real disk I/O, but the HomeAssistant instance itself is the real thing.
    """

    def __init__(self, initial_data: dict[str, Any] | None = None) -> None:
        self._data = initial_data
        self.saved_data: list[dict[str, Any]] = []

    async def async_load(self) -> dict[str, Any] | None:
        return self._data

    async def async_save(self, data: dict[str, Any]) -> None:
        self.saved_data.append(data)


@pytest.fixture
def mock_store():
    """Return a MockStore factory."""
    return MockStore


@pytest.fixture
def patch_ha_store(mock_store):
    """Patch HA's Store class to use MockStore."""

    def _patch(initial_data=None):
        store_instance = mock_store(initial_data)
        return patch(
            "homeassistant.helpers.storage.Store",
            return_value=store_instance,
        ), store_instance

    return _patch
