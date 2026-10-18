"""Recently deleted: every delete the user asked for can be undone.

Driven through the delete paths users reach (MCP and chat share the tool, the
panel has its own handler) and the trash's websocket commands, against real
automations.yaml / scenes.yaml files.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
import pytest
import yaml

from custom_components.selora_ai.automation_utils import (
    _deletion_hash,
    _get_automation_store,
    async_delete_automation,
)
from custom_components.selora_ai.const import TRASH_DAYS, TRASH_MAX_PER_KIND
from custom_components.selora_ai.helpers import get_scene_store
from custom_components.selora_ai.mcp_server.automations import (
    _tool_create_automation,
    _tool_delete_automation,
)
from custom_components.selora_ai.mcp_server.scenes import _tool_delete_scene
from custom_components.selora_ai.pattern_store import PatternStore
from custom_components.selora_ai.trash import (
    TrashRestoreError,
    async_delete_forever,
    async_restore,
    get_trash_store,
)
from custom_components.selora_ai.websocket.automations import (
    _handle_websocket_delete_automation,
)
from custom_components.selora_ai.websocket.trash import (
    _handle_trash_list,
    _handle_trash_restore,
)

_PROPOSAL = (
    "alias: Porch at dusk\n"
    "triggers:\n- trigger: sun\n  event: sunset\n"
    "actions:\n- action: light.turn_on\n  target:\n    entity_id: light.porch\n"
)

# Hand-written, the way a user's own automation sits in the file: not Selora's.
_THEIRS = {
    "id": "1700000000000",
    "alias": "Hall at night",
    "description": "Their own",
    "triggers": [{"trigger": "time", "at": "22:00:00"}],
    "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.hall"}}],
    "mode": "single",
}


def _path(hass: HomeAssistant, name: str) -> Path:
    return Path(hass.config.config_dir) / name


def _read(hass: HomeAssistant, name: str = "automations.yaml") -> list[dict[str, Any]]:
    path = _path(hass, name)
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


def _write(hass: HomeAssistant, name: str, entries: list[dict[str, Any]]) -> None:
    _path(hass, name).write_text(yaml.safe_dump(entries), encoding="utf-8")


@pytest.fixture(autouse=True)
def home(hass: HomeAssistant) -> None:
    hass.states.async_set("light.porch", "off")
    hass.states.async_set("light.hall", "off")
    hass.services.async_register("automation", "reload", lambda call: None)
    hass.services.async_register("scene", "reload", lambda call: None)


async def _created(hass: HomeAssistant) -> str:
    result = await _tool_create_automation(hass, {"yaml": _PROPOSAL})
    assert result.get("status") == "created", result
    return result["automation_id"]


async def _ws(hass: HomeAssistant, handler: Any, msg: dict[str, Any]) -> MagicMock:
    connection = MagicMock()
    connection.user.is_admin = True
    await handler.__wrapped__(hass, connection, {"id": 1, **msg})
    return connection


async def _only_item(hass: HomeAssistant) -> dict[str, Any]:
    items = await get_trash_store(hass).async_list()
    assert len(items) == 1, items
    return items[0]


# ── Automations ──────────────────────────────────────────────────────────────


async def test_a_panel_delete_can_be_restored_with_its_history(hass: HomeAssistant) -> None:
    automation_id = await _created(hass)
    await _tool_create_automation(
        hass,
        {"yaml": _PROPOSAL.replace("sunset", "sunrise"), "automation_id": automation_id},
    )

    await _ws(
        hass,
        _handle_websocket_delete_automation,
        {"type": "selora_ai/delete_automation", "automation_id": automation_id},
    )
    assert _read(hass) == []
    listed = await _ws(hass, _handle_trash_list, {"type": "selora_ai/trash/list"})
    [row] = listed.send_result.call_args.args[1]
    assert (row["kind"], row["name"]) == ("automation", "Porch at dusk")
    assert "entry" not in row

    await _ws(
        hass,
        _handle_trash_restore,
        {"type": "selora_ai/trash/restore", "trash_id": row["trash_id"]},
    )

    assert [a["id"] for a in _read(hass)] == [automation_id]
    record = await _get_automation_store(hass).get_record(automation_id)
    assert record is not None
    assert len(record["versions"]) == 3
    assert record["lineage"][-1]["action"] == "restored"
    assert await get_trash_store(hass).async_list() == []


async def test_a_restore_lets_the_automation_be_suggested_again(hass: HomeAssistant) -> None:
    automation_id = await _created(hass)
    entry = _read(hass)[0]
    await _tool_delete_automation(hass, {"automation_id": automation_id})
    assert _deletion_hash(entry) in await PatternStore(hass).get_deleted_hashes()

    await async_restore(hass, (await _only_item(hass))["trash_id"])

    assert _deletion_hash(entry) not in await PatternStore(hass).get_deleted_hashes()


async def test_a_restore_updates_the_running_pattern_store(hass: HomeAssistant) -> None:
    """A second copy would be overwritten by the running one's next save."""
    running = PatternStore(hass)
    hass.data.setdefault("selora_ai", {})["entry"] = {"pattern_store": running}
    automation_id = await _created(hass)
    entry = _read(hass)[0]
    await _tool_delete_automation(hass, {"automation_id": automation_id})
    assert _deletion_hash(entry) in await running.get_deleted_hashes()

    await async_restore(hass, (await _only_item(hass))["trash_id"])

    assert _deletion_hash(entry) not in await running.get_deleted_hashes()


async def test_someone_elses_automation_comes_back_as_it_was(hass: HomeAssistant) -> None:
    _write(hass, "automations.yaml", [_THEIRS])

    result = await _tool_delete_automation(hass, {"automation_id": _THEIRS["id"]})
    assert result["status"] == "deleted"
    await async_restore(hass, (await _only_item(hass))["trash_id"])

    assert _read(hass) == [_THEIRS]


async def test_an_idless_automation_is_kept_too(hass: HomeAssistant) -> None:
    idless = {k: v for k, v in _THEIRS.items() if k != "id"}
    _write(hass, "automations.yaml", [idless])

    result = await _tool_delete_automation(hass, {"expected_alias": "Hall at night"})
    assert result["status"] == "deleted"
    await async_restore(hass, (await _only_item(hass))["trash_id"])

    assert _read(hass) == [idless]


async def test_a_restore_never_takes_an_id_in_use(hass: HomeAssistant) -> None:
    """Two automations under one id would load as one."""
    _write(hass, "automations.yaml", [_THEIRS])
    await _tool_delete_automation(hass, {"automation_id": _THEIRS["id"]})
    _write(hass, "automations.yaml", [{**_THEIRS, "alias": "Replacement"}])

    item = await _only_item(hass)
    with pytest.raises(TrashRestoreError) as err:
        await async_restore(hass, item["trash_id"])

    assert err.value.code == "in_use"
    assert [a["alias"] for a in _read(hass)] == ["Replacement"]
    assert await get_trash_store(hass).async_get(item["trash_id"]) is not None


async def test_an_idless_restore_never_duplicates_an_alias(hass: HomeAssistant) -> None:
    """The alias-keyed delete refuses two id-less entries under one alias."""
    idless = {k: v for k, v in _THEIRS.items() if k != "id"}
    _write(hass, "automations.yaml", [idless])
    await _tool_delete_automation(hass, {"expected_alias": "Hall at night"})
    _write(hass, "automations.yaml", [{**idless, "description": "Made again"}])

    with pytest.raises(TrashRestoreError) as err:
        await async_restore(hass, (await _only_item(hass))["trash_id"])

    assert err.value.code == "in_use"


async def test_delete_forever_drops_the_kept_history(hass: HomeAssistant) -> None:
    automation_id = await _created(hass)
    await _tool_delete_automation(hass, {"automation_id": automation_id})
    store = _get_automation_store(hass)
    assert await store.is_retired(automation_id)

    assert await async_delete_forever(hass, (await _only_item(hass))["trash_id"])

    assert not await store.is_retired(automation_id)
    assert await get_trash_store(hass).async_list() == []


async def test_a_clean_up_delete_is_not_kept(hass: HomeAssistant) -> None:
    """A scheduled action removing its own automation is not the user's delete."""
    automation_id = await _created(hass)

    assert await async_delete_automation(hass, automation_id)

    assert await get_trash_store(hass).async_list() == []


# ── Scenes ───────────────────────────────────────────────────────────────────


async def test_a_selora_scene_comes_back_and_is_listed_again(hass: HomeAssistant) -> None:
    scene = {
        "id": "selora_ai_scene_movie",
        "name": "Movie night",
        "entities": {"light.porch": {"state": "off"}},
    }
    _write(hass, "scenes.yaml", [scene])
    store = get_scene_store(hass)
    await store.async_reconcile_yaml(force=True)

    result = await _tool_delete_scene(hass, {"scene_id": scene["id"]})
    assert result["status"] == "deleted", result
    assert _read(hass, "scenes.yaml") == []
    item = await _only_item(hass)
    assert (item["kind"], item["name"]) == ("scene", "Movie night")

    await async_restore(hass, item["trash_id"])

    assert _read(hass, "scenes.yaml") == [scene]
    assert [s["scene_id"] for s in await store.async_list_scenes()] == [scene["id"]]


async def test_a_scene_restore_never_takes_an_id_in_use(hass: HomeAssistant) -> None:
    scene = {"id": "1700000000001", "name": "Dinner", "entities": {"light.hall": "on"}}
    _write(hass, "scenes.yaml", [scene])
    hass.states.async_set("scene.dinner", "scening", {"id": scene["id"]})
    await _tool_delete_scene(hass, {"scene_id": scene["id"]})
    _write(hass, "scenes.yaml", [{**scene, "name": "Other"}])

    with pytest.raises(TrashRestoreError) as err:
        await async_restore(hass, (await _only_item(hass))["trash_id"])

    assert err.value.code == "in_use"


# ── Retention ────────────────────────────────────────────────────────────────


async def test_items_leave_after_their_time(hass: HomeAssistant) -> None:
    store = get_trash_store(hass)
    old = await store.async_add("automation", {"alias": "Old"}, "Old")
    await store.async_add("automation", {"alias": "New"}, "New")
    old["deleted_at"] = (datetime.now(UTC) - timedelta(days=TRASH_DAYS + 1)).isoformat()

    assert [i["name"] for i in await store.async_list()] == ["New"]


async def test_each_kind_keeps_its_newest(hass: HomeAssistant) -> None:
    store = get_trash_store(hass)
    await store.async_add("scene", {"name": "Kept scene"}, "Kept scene")
    for n in range(TRASH_MAX_PER_KIND + 1):
        await store.async_add("automation", {"alias": f"A{n}"}, f"A{n}")

    items = await store.async_list()
    automations = [i["name"] for i in items if i["kind"] == "automation"]
    assert len(automations) == TRASH_MAX_PER_KIND
    assert "A0" not in automations
    assert any(i["name"] == "Kept scene" for i in items)
