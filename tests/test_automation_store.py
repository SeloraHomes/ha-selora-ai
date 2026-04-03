"""Tests for AutomationStore — version and lifecycle management."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from custom_components.selora_ai.automation_store import AutomationStore

from .conftest import MockStore


@pytest.fixture
def automation_store(hass):
    """Create an AutomationStore backed by a MockStore."""
    with patch("custom_components.selora_ai.automation_store.Store") as MockStoreClass:
        mock_store_instance = MockStore()
        MockStoreClass.return_value = mock_store_instance
        store = AutomationStore(hass)
        store._store = mock_store_instance
        yield store, mock_store_instance


@pytest.fixture
def prefilled_store(hass):
    """Create an AutomationStore with pre-existing data."""
    initial_data = {
        "records": {
            "auto_1": {
                "automation_id": "auto_1",
                "current_version_id": "v1",
                "versions": [
                    {
                        "version_id": "v1",
                        "automation_id": "auto_1",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "yaml": "alias: Test\ntrigger: []\n",
                        "data": {"alias": "Test"},
                        "message": "initial",
                        "session_id": None,
                    }
                ],
                "deleted_at": None,
                "lineage": [
                    {
                        "version_id": "v1",
                        "session_id": None,
                        "message_index": None,
                        "action": "created",
                        "timestamp": "2026-01-01T00:00:00+00:00",
                    }
                ],
            }
        },
        "session_index": {},
        "drafts": {},
    }
    with patch("custom_components.selora_ai.automation_store.Store") as MockStoreClass:
        mock_store_instance = MockStore(initial_data)
        MockStoreClass.return_value = mock_store_instance
        store = AutomationStore(hass)
        store._store = mock_store_instance
        yield store, mock_store_instance


# ── _ensure_loaded ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_loaded_empty_store(automation_store):
    """Loading from an empty store creates the default structure."""
    store, mock = automation_store
    await store._ensure_loaded()
    assert store._data == {"records": {}, "session_index": {}, "drafts": {}}


@pytest.mark.asyncio
async def test_ensure_loaded_with_existing_data(prefilled_store):
    """Loading existing data preserves records."""
    store, _ = prefilled_store
    await store._ensure_loaded()
    assert "auto_1" in store._data["records"]


@pytest.mark.asyncio
async def test_ensure_loaded_migrates_missing_session_index(hass):
    """Migrates data that lacks the session_index key."""
    legacy_data = {
        "records": {
            "a1": {"automation_id": "a1", "versions": [], "deleted_at": None, "lineage": []}
        }
    }
    with patch("custom_components.selora_ai.automation_store.Store") as Cls:
        ms = MockStore(legacy_data)
        Cls.return_value = ms
        store = AutomationStore(hass)
        store._store = ms
        await store._ensure_loaded()
        assert "session_index" in store._data
        assert "drafts" in store._data


@pytest.mark.asyncio
async def test_ensure_loaded_migrates_missing_lineage(hass):
    """Migrates records that lack the lineage key."""
    legacy_data = {
        "records": {"a1": {"automation_id": "a1", "versions": [], "deleted_at": None}},
        "session_index": {},
    }
    with patch("custom_components.selora_ai.automation_store.Store") as Cls:
        ms = MockStore(legacy_data)
        Cls.return_value = ms
        store = AutomationStore(hass)
        store._store = ms
        await store._ensure_loaded()
        assert store._data["records"]["a1"]["lineage"] == []


@pytest.mark.asyncio
async def test_ensure_loaded_idempotent(automation_store):
    """Calling _ensure_loaded twice does not reload."""
    store, mock = automation_store
    await store._ensure_loaded()
    first_data = store._data
    await store._ensure_loaded()
    assert store._data is first_data


# ── add_version ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_version_creates_new_record(automation_store):
    """First version for an automation_id creates the record."""
    store, mock = automation_store
    vid = await store.add_version("auto_new", "alias: New\n", {"alias": "New"}, "created it")
    assert vid is not None
    record = store._data["records"]["auto_new"]
    assert record["automation_id"] == "auto_new"
    assert record["current_version_id"] == vid
    assert len(record["versions"]) == 1
    assert record["lineage"][0]["action"] == "created"
    assert len(mock.saved_data) == 1


@pytest.mark.asyncio
async def test_add_version_appends_to_existing(prefilled_store):
    """Subsequent version is appended and current_version_id is updated."""
    store, mock = prefilled_store
    vid = await store.add_version("auto_1", "alias: Updated\n", {"alias": "Updated"}, "updated it")
    record = store._data["records"]["auto_1"]
    assert len(record["versions"]) == 2
    assert record["current_version_id"] == vid
    assert record["lineage"][-1]["action"] == "updated"


@pytest.mark.asyncio
async def test_add_version_action_refined_with_session(automation_store):
    """When session_id is provided and no explicit action, action is 'refined'."""
    store, _ = automation_store
    await store.add_version("auto_x", "v1\n", {}, "init")
    await store.add_version("auto_x", "v2\n", {}, "refine", session_id="sess_1")
    record = store._data["records"]["auto_x"]
    assert record["lineage"][-1]["action"] == "refined"
    assert record["lineage"][-1]["session_id"] == "sess_1"


@pytest.mark.asyncio
async def test_add_version_explicit_action_overrides(automation_store):
    """Explicit action parameter takes precedence over defaults."""
    store, _ = automation_store
    await store.add_version("auto_x", "v1\n", {}, "init")
    await store.add_version("auto_x", "v2\n", {}, "restore", action="restored")
    record = store._data["records"]["auto_x"]
    assert record["lineage"][-1]["action"] == "restored"


@pytest.mark.asyncio
async def test_add_version_updates_session_index(automation_store):
    """Session index is populated when session_id is given."""
    store, _ = automation_store
    await store.add_version("auto_a", "yaml\n", {}, "msg", session_id="sess_1")
    await store.add_version("auto_b", "yaml\n", {}, "msg", session_id="sess_1")
    # Adding same automation to same session should not duplicate
    await store.add_version("auto_a", "yaml2\n", {}, "msg2", session_id="sess_1")
    assert store._data["session_index"]["sess_1"] == ["auto_a", "auto_b"]


# ── get_record / get_versions ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_record_returns_none_for_unknown(automation_store):
    store, _ = automation_store
    assert await store.get_record("nonexistent") is None


@pytest.mark.asyncio
async def test_get_versions_returns_empty_for_unknown(automation_store):
    store, _ = automation_store
    assert await store.get_versions("nonexistent") == []


# ── get_diff ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_diff_returns_unified_diff(automation_store):
    """Diff between two versions returns a unified diff string."""
    store, _ = automation_store
    v1 = await store.add_version("auto_d", "alias: Original\nmode: single\n", {}, "v1")
    v2 = await store.add_version("auto_d", "alias: Changed\nmode: single\n", {}, "v2")
    diff = await store.get_diff("auto_d", v1, v2)
    assert diff is not None
    assert "Original" in diff
    assert "Changed" in diff
    assert "---" in diff


@pytest.mark.asyncio
async def test_get_diff_returns_none_for_missing_version(automation_store):
    store, _ = automation_store
    await store.add_version("auto_d", "yaml\n", {}, "v1")
    assert await store.get_diff("auto_d", "fake_id_a", "fake_id_b") is None


# ── purge_record ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_purge_record_removes_permanently(prefilled_store):
    store, _ = prefilled_store
    assert await store.purge_record("auto_1") is True
    assert "auto_1" not in store._data["records"]


@pytest.mark.asyncio
async def test_purge_record_unknown_returns_false(automation_store):
    store, _ = automation_store
    assert await store.purge_record("nope") is False


# ── Draft operations ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_list_drafts(automation_store):
    store, mock = automation_store
    draft = await store.create_draft("My Draft Automation", "sess_42")
    assert draft["alias"] == "My Draft Automation"
    assert draft["session_id"] == "sess_42"
    assert "draft_id" in draft
    assert "created_at" in draft

    drafts = await store.list_drafts()
    assert len(drafts) == 1
    assert drafts[0]["draft_id"] == draft["draft_id"]


@pytest.mark.asyncio
async def test_remove_draft(automation_store):
    store, _ = automation_store
    draft = await store.create_draft("Temp", "sess_1")
    assert await store.remove_draft(draft["draft_id"]) is True
    assert await store.list_drafts() == []


@pytest.mark.asyncio
async def test_remove_draft_not_found(automation_store):
    store, _ = automation_store
    assert await store.remove_draft("nonexistent_draft") is False
