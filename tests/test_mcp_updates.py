"""Tests for listing updates and reading release notes over MCP."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import TOOL_GET_RELEASE_NOTES, TOOL_LIST_UPDATES


class _Update(UpdateEntity):
    def __init__(
        self,
        key: str,
        installed: str,
        latest: str,
        *,
        notes: str | None = None,
        summary: str | None = None,
    ) -> None:
        self.entity_id = f"update.{key}"
        self._attr_name = key.replace("_", " ").title()
        self._attr_installed_version = installed
        self._attr_latest_version = latest
        self._attr_release_summary = summary
        self._notes = notes
        self._attr_supported_features = UpdateEntityFeature.INSTALL | (
            UpdateEntityFeature.RELEASE_NOTES if notes is not None else 0
        )

    async def async_release_notes(self) -> str | None:
        return self._notes


@pytest.fixture
async def updates(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "update", {})
    await hass.data["update"].async_add_entities(
        [
            _Update(
                "home_assistant_core_update",
                "2026.9.4",
                "2026.10.0",
                notes="## Breaking\n\n\n\n- one\x07\r\n- two",
                summary="Big   release",
            ),
            _Update("lamp_firmware", "1.0", "1.1"),
            _Update("current", "2.0", "2.0"),
        ]
    )
    await hass.async_block_till_done()


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[tool](hass, arguments)


async def test_only_updates_waiting_are_listed(hass: HomeAssistant, updates: None) -> None:
    listed = await _mcp(hass, TOOL_LIST_UPDATES)

    by_id = {u["entity_id"]: u for u in listed["updates"]}
    assert set(by_id) == {"update.home_assistant_core_update", "update.lamp_firmware"}
    core = by_id["update.home_assistant_core_update"]
    assert core["installed_version"] == "2026.9.4"
    assert core["latest_version"] == "2026.10.0"
    assert core["release_summary"] == "Big release"
    assert "update.install" in listed["hint"]


async def test_a_skipped_update_is_listed_on_request(hass: HomeAssistant, updates: None) -> None:
    await hass.services.async_call(
        "update", "skip", {"entity_id": "update.lamp_firmware"}, blocking=True
    )

    default = await _mcp(hass, TOOL_LIST_UPDATES)
    with_skipped = await _mcp(hass, TOOL_LIST_UPDATES, include_skipped=True)

    assert "update.lamp_firmware" not in {u["entity_id"] for u in default["updates"]}
    (lamp,) = [u for u in with_skipped["updates"] if u["entity_id"] == "update.lamp_firmware"]
    assert lamp["skipped"] is True


async def test_updates_are_grouped_by_where_they_come_from(
    hass: HomeAssistant, updates: None
) -> None:
    from custom_components.selora_ai.update_manager import _category

    registry = er.async_get(hass)
    core = registry.async_get_or_create("update", "hassio", "home_assistant_core_version_latest")
    app = registry.async_get_or_create("update", "hassio", "core_mosquitto_version_latest")
    hacs = registry.async_get_or_create("update", "hacs", "123")

    assert [_category(e) for e in (core, app, hacs, None)] == [
        "home_assistant",
        "app",
        "hacs",
        "other",
    ]


async def test_release_notes_keep_their_lines_but_nothing_else(
    hass: HomeAssistant, updates: None
) -> None:
    result = await _mcp(hass, TOOL_GET_RELEASE_NOTES, entity_id="update.home_assistant_core_update")

    assert result["release_notes"] == "## Breaking\n\n- one\n- two"


async def test_an_update_without_notes_points_at_the_summary(
    hass: HomeAssistant, updates: None
) -> None:
    result = await _mcp(hass, TOOL_GET_RELEASE_NOTES, entity_id="update.lamp_firmware")

    assert result["release_notes"] is None
    assert "release_url" in result["hint"]


async def test_only_an_update_has_release_notes(hass: HomeAssistant, updates: None) -> None:
    assert (
        "not an update"
        in (await _mcp(hass, TOOL_GET_RELEASE_NOTES, entity_id="light.kitchen"))["error"]
    )
    assert (
        "list_updates"
        in (await _mcp(hass, TOOL_GET_RELEASE_NOTES, entity_id="update.nope"))["error"]
    )


async def test_listing_is_open_and_release_notes_need_admin() -> None:
    assert TOOL_LIST_UPDATES in mcp_access._READ_ONLY_TOOLS
    assert TOOL_GET_RELEASE_NOTES in mcp_access._ADMIN_TOOLS


async def test_versions_are_bounded(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "update", {})
    await hass.data["update"].async_add_entities([_Update("odd", "1.0", "9" * 500)])
    await hass.async_block_till_done()

    (row,) = (await _mcp(hass, TOOL_LIST_UPDATES))["updates"]

    assert len(row["latest_version"]) <= 60
