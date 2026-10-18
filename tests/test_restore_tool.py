"""Restoring from Recently deleted by asking: chat shows a card, MCP needs an id.

Same-named automations are how the trash fills in the first place, so chat
never restores on its own — the user picks from a card that shows what each
match does — and MCP restores only the ``trash_id`` its caller chose.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
import pytest
import yaml

from custom_components.selora_ai.mcp_server.automations import _tool_delete_automation
from custom_components.selora_ai.mcp_server.trash import _tool_restore_deleted
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.trash import get_trash_store
from custom_components.selora_ai.websocket.trash import _handle_trash_get
from tests.chat_harness import ChatHarness


def _automation(automation_id: str, delay: str) -> dict[str, Any]:
    return {
        "id": automation_id,
        "alias": "Garage Night Spots",
        "description": f"Spots off after {delay}",
        "triggers": [{"trigger": "state", "entity_id": "binary_sensor.garage", "to": "on"}],
        "actions": [
            {"action": "switch.turn_on", "target": {"entity_id": "switch.spots"}},
            {"delay": delay},
        ],
        "mode": "restart",
    }


TWO_MIN = _automation("1700000000001", "00:02:00")
THREE_MIN = _automation("1700000000002", "00:03:00")
PORCH = {**_automation("1700000000003", "00:05:00"), "alias": "Porch at dusk"}


def _read(hass: HomeAssistant) -> list[dict[str, Any]]:
    path = Path(hass.config.config_dir) / "automations.yaml"
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or []) if path.exists() else []


@pytest.fixture(autouse=True)
async def deleted(hass: HomeAssistant) -> None:
    """Three automations deleted through the tool users reach, the porch last."""
    hass.services.async_register("automation", "reload", lambda call: None)
    path = Path(hass.config.config_dir) / "automations.yaml"
    path.write_text(yaml.safe_dump([TWO_MIN, THREE_MIN, PORCH]), encoding="utf-8")
    for entry in (TWO_MIN, THREE_MIN, PORCH):
        result = await _tool_delete_automation(hass, {"automation_id": entry["id"]})
        assert result["status"] == "deleted", result


async def _ws(hass: HomeAssistant, handler: Any, msg: dict[str, Any]) -> MagicMock:
    connection = MagicMock()
    connection.user.is_admin = True
    await handler.__wrapped__(hass, connection, {"id": 1, **msg})
    return connection


async def _chat_tool(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    return await ToolExecutor(hass, None, is_admin=True).execute("restore_deleted", arguments)


async def test_chat_shows_every_match_and_restores_nothing(hass: HomeAssistant) -> None:
    result = await _chat_tool(hass, {"query": "garage night"})

    candidates = result["candidates"]
    assert {c["name"] for c in candidates} == {"Garage Night Spots"}
    # Enough for the model to say what tells them apart.
    assert {c["description"] for c in candidates} == {
        "Spots off after 00:02:00",
        "Spots off after 00:03:00",
    }
    # The card fetches the rest itself: a long entry here would be trimmed
    # away by the tool-result size limit, Restore button and all.
    assert all("config" not in c and "yaml" not in c for c in candidates)
    assert _read(hass) == []


async def test_the_card_gets_each_match_in_full(hass: HomeAssistant) -> None:
    found = await _chat_tool(hass, {"query": "garage night"})
    ids = [c["trash_id"] for c in found["candidates"]]

    connection = await _ws(
        hass, _handle_trash_get, {"type": "selora_ai/trash/get", "trash_ids": [*ids, "gone"]}
    )

    items = connection.send_result.call_args.args[1]["items"]
    assert {i["config"]["actions"][1]["delay"] for i in items} == {"00:02:00", "00:03:00"}
    assert all("delay:" in i["yaml"] for i in items)


async def test_a_short_name_is_searchable(hass: HomeAssistant) -> None:
    store = get_trash_store(hass)
    await store.async_add("automation", {"alias": "TV off"}, "TV off")

    result = await _chat_tool(hass, {"query": "tv"})

    assert [c["name"] for c in result["candidates"]] == ["TV off"]


async def test_a_trash_id_narrows_the_card_to_one(hass: HomeAssistant) -> None:
    first = await _chat_tool(hass, {"query": "garage"})
    chosen = first["candidates"][0]["trash_id"]

    result = await _chat_tool(hass, {"trash_id": chosen})

    assert [c["trash_id"] for c in result["candidates"]] == [chosen]
    assert _read(hass) == []


async def test_no_query_offers_the_most_recent_first(hass: HomeAssistant) -> None:
    result = await _chat_tool(hass, {})

    assert result["candidates"][0]["name"] == "Porch at dusk"


async def test_no_match_says_what_is_there(hass: HomeAssistant) -> None:
    result = await _chat_tool(hass, {"query": "kitchen"})

    assert result["candidates"] == []
    assert {i["name"] for i in result["recently_deleted"]} == {
        "Garage Night Spots",
        "Porch at dusk",
    }


async def test_mcp_lists_until_given_the_chosen_id(hass: HomeAssistant) -> None:
    listed = await _tool_restore_deleted(hass, {"query": "garage night"})
    assert len(listed["candidates"]) == 2
    assert "Nothing was restored" in listed["note"]
    assert _read(hass) == []

    three = next(c for c in listed["candidates"] if c["config"]["id"] == THREE_MIN["id"])
    restored = await _tool_restore_deleted(hass, {"trash_id": three["trash_id"]})

    assert restored == {"status": "restored", "kind": "automation", "name": "Garage Night Spots"}
    assert _read(hass) == [THREE_MIN]
    again = await _tool_restore_deleted(hass, {"trash_id": three["trash_id"]})
    assert "Not in Recently deleted" in again["error"]


async def test_the_card_survives_a_reload(hass: HomeAssistant) -> None:
    """The panel renders the card from the reply's stored tool calls."""
    executor = ToolExecutor(hass, None, is_admin=True)
    await executor.execute("restore_deleted", {"query": "garage night"})
    harness = await ChatHarness.create(hass)

    await harness.stream(
        "bring back the garage night automation",
        chunks="Pick the one to restore below.",
        tool_calls=executor.call_log,
    )

    reply = (await harness.messages())[-1]
    [call] = [c for c in reply["tool_calls"] if c["tool"] == "restore_deleted"]
    assert len(call["result"]["candidates"]) == 2
    assert _read(hass) == []
