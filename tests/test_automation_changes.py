"""Tests for the stored per-version change list."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant

from custom_components.selora_ai.automation_changes import CHANGES_FORMAT, compute_version_changes
from custom_components.selora_ai.automation_store import AutomationStore

from .conftest import MockStore

_ON = {"action": "light.turn_on", "target": {"entity_id": "light.sconces"}}
_OFF = {"action": "light.turn_off", "target": {"entity_id": "light.sconces"}}


def _automation(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "selora_ai_1",
        "alias": "Stairs",
        "description": "Keep them on for 5 minutes.",
        "triggers": [{"trigger": "state", "entity_id": "binary_sensor.stairs", "to": "on"}],
        "conditions": [],
        "actions": [_ON, {"delay": {"minutes": 5}}, _OFF],
        "mode": "single",
        "initial_state": True,
    }
    return {**base, **overrides}


def test_an_edited_delay_names_the_value_that_moved() -> None:
    after = _automation(
        actions=[_ON, {"delay": {"minutes": 2}}, _OFF],
        description="Keep them on for 2 minutes.",
    )
    changes = compute_version_changes(_automation(), after)
    assert changes is not None
    item = next(c for c in changes if c["kind"] == "item_changed")
    assert item["section"] == "actions"
    assert item["index"] == 1
    assert item["details"] == [{"path": ["delay", "minutes"], "before": 5, "after": 2}]
    assert item["before"] == {"delay": {"minutes": 5}}
    assert item["after"] == {"delay": {"minutes": 2}}
    assert {"kind": "field_changed", "field": "description"}.items() <= next(
        c for c in changes if c["kind"] == "field_changed"
    ).items()


def test_a_reorder_is_a_change() -> None:
    after = _automation(actions=[{"delay": {"minutes": 5}}, _ON, _OFF])
    assert compute_version_changes(_automation(), after) == [
        {"kind": "reordered", "section": "actions"}
    ]


def test_added_and_removed_items() -> None:
    cond = {"condition": "state", "entity_id": "light.sconces", "state": "off"}
    added = compute_version_changes(_automation(), _automation(conditions=[cond]))
    assert added == [{"kind": "item_added", "section": "conditions", "after": cond}]
    removed = compute_version_changes(_automation(conditions=[cond]), _automation())
    assert removed == [{"kind": "item_removed", "section": "conditions", "before": cond}]


def test_managed_fields_key_order_and_spelling_are_not_changes() -> None:
    after = {
        "initial_state": False,
        "id": "selora_ai_2",
        "mode": "single",
        "action": [_ON, {"delay": {"minutes": 5}}, {"target": _OFF["target"], **_OFF}],
        "trigger": {"to": "on", "entity_id": "binary_sensor.stairs", "trigger": "state"},
        "conditions": [],
        "description": "Keep them on for 5 minutes.",
        "alias": "Stairs",
    }
    assert compute_version_changes(_automation(), after) == []


def test_other_top_level_settings_are_reported() -> None:
    changes = compute_version_changes(_automation(), _automation(max=3, mode="queued"))
    assert changes == [
        {"kind": "field_changed", "field": "max", "before": None, "after": 3},
        {"kind": "field_changed", "field": "mode", "before": "single", "after": "queued"},
    ]


def test_no_earlier_version_is_none() -> None:
    assert compute_version_changes(None, _automation()) is None


async def test_add_version_stores_the_changes(hass: HomeAssistant) -> None:
    with patch("custom_components.selora_ai.automation_store.Store") as store_cls:
        backing = MockStore()
        store_cls.return_value = backing
        store = AutomationStore(hass)
        await store.add_version("a1", "", _automation(), "Created")
        await store.add_version("a1", "", _automation(alias="Stairs night"), "Refined via chat")
    first, second = await store.get_versions("a1")
    assert first["changes"] is None
    assert second["changes"] == [
        {"kind": "field_changed", "field": "alias", "before": "Stairs", "after": "Stairs night"}
    ]


async def test_existing_versions_are_backfilled_once(hass: HomeAssistant) -> None:
    def version(vid: str, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "version_id": vid,
            "automation_id": "a1",
            "created_at": "2026-01-01T00:00:00+00:00",
            "yaml": "",
            "data": data,
            "message": "Refined via chat",
            "session_id": None,
        }

    initial = {
        "records": {
            "a1": {
                "automation_id": "a1",
                "current_version_id": "v2",
                "versions": [
                    version("v1", _automation()),
                    version("v2", _automation(mode="restart")),
                ],
                "lineage": [],
            }
        },
        "session_index": {},
    }
    with patch("custom_components.selora_ai.automation_store.Store") as store_cls:
        backing = MockStore(initial)
        store_cls.return_value = backing
        store = AutomationStore(hass)
        versions = await store.get_versions("a1")
    assert versions[0]["changes"] is None
    assert versions[1]["changes"] == [
        {"kind": "field_changed", "field": "mode", "before": "single", "after": "restart"}
    ]
    assert versions[1]["changes_format"] == CHANGES_FORMAT
    assert len(backing.saved_data) == 1


def _heatpump(*, legacy: bool, away: int) -> dict[str, Any]:
    """The away/home thermostat rule, in the old syntax or the current one."""
    svc = "service" if legacy else "action"

    def set_temp(temp: int) -> dict[str, Any]:
        return {
            svc: "climate.set_temperature",
            "data": {"temperature": temp},
            "target": {"entity_id": "climate.heatpump"},
        }

    def branch(state: str, temp: int) -> dict[str, Any]:
        return {
            "conditions": [{"condition": "state", "entity_id": "person.me", "state": state}],
            "sequence": [set_temp(temp)],
        }

    trigger = {"entity_id": "person.me", "trigger" if not legacy else "platform": "state"}
    return {
        "alias": "Heat pump presence",
        "trigger" if legacy else "triggers": [trigger],
        "action" if legacy else "actions": [
            {"choose": [branch("not_home", away), branch("home", 20)]}
        ],
        "mode": "single",
    }


def test_a_syntax_migration_is_not_a_change() -> None:
    assert (
        compute_version_changes(_heatpump(legacy=True, away=16), _heatpump(legacy=False, away=16))
        == []
    )


def test_the_real_edit_survives_a_syntax_migration() -> None:
    changes = compute_version_changes(
        _heatpump(legacy=True, away=16), _heatpump(legacy=False, away=15)
    )
    assert changes is not None
    assert len(changes) == 1
    (detail,) = changes[0]["details"]
    assert detail["path"][-2:] == ["data", "temperature"]
    assert (detail["before"], detail["after"]) == (16, 15)
    assert detail["entity"] == "climate.heatpump"


async def test_versions_from_an_older_comparison_are_recomputed(hass: HomeAssistant) -> None:
    stale = {
        "version_id": "v2",
        "automation_id": "a1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "yaml": "",
        "data": _heatpump(legacy=False, away=15),
        "message": "Refined via chat",
        "session_id": None,
        "changes": [{"kind": "field_changed", "field": "trigger"}],
    }
    first = {**stale, "version_id": "v1", "data": _heatpump(legacy=True, away=16)}
    first.pop("changes")
    initial = {
        "records": {
            "a1": {
                "automation_id": "a1",
                "current_version_id": "v2",
                "versions": [first, stale],
                "lineage": [],
            }
        },
        "session_index": {},
    }
    with patch("custom_components.selora_ai.automation_store.Store") as store_cls:
        store_cls.return_value = MockStore(initial)
        store = AutomationStore(hass)
        versions = await store.get_versions("a1")
    (change,) = versions[1]["changes"]
    assert change["kind"] == "item_changed"
