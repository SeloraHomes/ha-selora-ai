"""A deleted automation's history, kept for the restore its delete hands back.

The MCP and chat delete return the removed automation as ``previous``; made
again from it, a Selora automation is back under its id with its versions. A
panel delete, which hands nothing back, still purges.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest
import yaml

from custom_components.selora_ai.automation_store import AutomationStore
from custom_components.selora_ai.automation_utils import (
    _get_automation_store,
    async_delete_automation,
)
from custom_components.selora_ai.const import (
    MAX_RETIRED_AUTOMATIONS,
    RETIRED_AUTOMATION_DAYS,
)
from custom_components.selora_ai.mcp_server.automations import (
    _tool_create_automation,
    _tool_delete_automation,
)

from .conftest import MockStore

_PROPOSAL = (
    "alias: Porch at dusk\n"
    "triggers:\n- trigger: sun\n  event: sunset\n"
    "actions:\n- action: light.turn_on\n  target:\n    entity_id: light.porch\n"
)


def _read(hass: HomeAssistant) -> list[dict[str, Any]]:
    path = Path(hass.config.config_dir) / "automations.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


@pytest.fixture(autouse=True)
def home(hass: HomeAssistant) -> None:
    hass.states.async_set("light.porch", "off")
    hass.services.async_register("automation", "reload", lambda call: None)


async def _created(hass: HomeAssistant) -> str:
    result = await _tool_create_automation(hass, {"yaml": _PROPOSAL})
    assert result.get("status") == "created", result
    return result["automation_id"]


async def _deleted(hass: HomeAssistant, automation_id: str) -> dict[str, Any]:
    result = await _tool_delete_automation(hass, {"automation_id": automation_id})
    assert result.get("status") == "deleted", result
    return result


async def test_a_restore_gets_its_id_and_history_back(hass: HomeAssistant) -> None:
    automation_id = await _created(hass)
    await _tool_create_automation(
        hass,
        {"yaml": _PROPOSAL.replace("sunset", "sunrise"), "automation_id": automation_id},
    )
    deleted = await _deleted(hass, automation_id)
    store = _get_automation_store(hass)
    assert await store.get_record(automation_id) is None

    result = await _tool_create_automation(hass, {"yaml": json.dumps(deleted["previous"])})

    assert result["automation_id"] == automation_id
    assert result["history_restored"] is True
    assert [a["id"] for a in _read(hass)] == [automation_id]
    record = await store.get_record(automation_id)
    assert record is not None
    assert len(record["versions"]) == 3
    assert [e["action"] for e in record["lineage"]] == ["created", "updated", "restored"]
    assert "retired_at" not in record
    assert not await store.is_retired(automation_id)


async def test_an_id_in_use_again_gets_a_new_one(hass: HomeAssistant) -> None:
    """Two automations under one id would load as one."""
    automation_id = await _created(hass)
    deleted = await _deleted(hass, automation_id)
    copy = json.dumps(deleted["previous"])
    first = await _tool_create_automation(hass, {"yaml": copy})

    second = await _tool_create_automation(hass, {"yaml": copy})

    assert first["automation_id"] == automation_id
    assert second["automation_id"] != automation_id
    assert "history_restored" not in second


async def test_an_id_with_no_kept_history_is_not_taken(hass: HomeAssistant) -> None:
    """Only a deleted automation's own id can be asked for."""
    result = await _tool_create_automation(hass, {"yaml": f"id: chosen_by_caller\n{_PROPOSAL}"})

    assert result["automation_id"] != "chosen_by_caller"
    assert "history_restored" not in result


async def test_a_panel_delete_still_purges(hass: HomeAssistant) -> None:
    """The panel says it deletes the history, and hands back nothing to restore."""
    automation_id = await _created(hass)

    assert await async_delete_automation(hass, automation_id)

    store = _get_automation_store(hass)
    assert not await store.is_retired(automation_id)
    restored = await _tool_create_automation(hass, {"yaml": f"id: {automation_id}\n{_PROPOSAL}"})
    assert restored["automation_id"] != automation_id


# ── Pruning ──────────────────────────────────────────────────────────────────


def _record(automation_id: str, retired_at: datetime) -> dict[str, Any]:
    return {
        "automation_id": automation_id,
        "current_version_id": "v1",
        "versions": [],
        "lineage": [],
        "retired_at": retired_at.isoformat(),
    }


def _store(hass: HomeAssistant, data: dict[str, Any]) -> tuple[AutomationStore, MockStore]:
    with patch("custom_components.selora_ai.automation_store.Store") as store_class:
        backing = MockStore(data)
        store_class.return_value = backing
        store = AutomationStore(hass)
    return store, backing


async def test_kept_history_expires(hass: HomeAssistant) -> None:
    now = datetime.now(UTC)
    store, backing = _store(
        hass,
        {
            "records": {},
            "session_index": {},
            "retired": {
                "old": _record("old", now - timedelta(days=RETIRED_AUTOMATION_DAYS + 1)),
                "recent": _record("recent", now - timedelta(days=1)),
            },
        },
    )

    assert not await store.is_retired("old")
    assert await store.is_retired("recent")
    assert set(backing.saved_data[-1]["retired"]) == {"recent"}


async def test_only_the_newest_are_kept(hass: HomeAssistant) -> None:
    now = datetime.now(UTC)
    retired = {
        f"a{i}": _record(f"a{i}", now - timedelta(minutes=i))
        for i in range(MAX_RETIRED_AUTOMATIONS)
    }
    store, _ = _store(hass, {"records": {}, "session_index": {}, "retired": retired})
    data = await store._get_loaded_data()
    data["records"]["fresh"] = {
        "automation_id": "fresh",
        "current_version_id": "v1",
        "versions": [],
        "lineage": [],
    }

    assert await store.retire_record("fresh")

    assert await store.is_retired("fresh")
    assert not await store.is_retired(f"a{MAX_RETIRED_AUTOMATIONS - 1}")
    assert len(data["retired"]) == MAX_RETIRED_AUTOMATIONS


async def test_history_expires_while_the_hub_runs(hass: HomeAssistant) -> None:
    """Loaded before the deadline, it is still refused after it."""
    now = datetime.now(UTC)
    store, _ = _store(
        hass,
        {
            "records": {},
            "session_index": {},
            "retired": {"old": _record("old", now - timedelta(days=RETIRED_AUTOMATION_DAYS - 1))},
        },
    )
    assert await store.is_retired("old")

    later = now + timedelta(days=2)
    with patch("custom_components.selora_ai.automation_store.datetime") as clock:
        clock.now.return_value = later
        assert not await store.is_retired("old")
        assert not await store.revive_record("old")
