"""Tests for listing apps (formerly add-ons) and reading their logs over MCP."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai import apps_manager
from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import TOOL_GET_APP_LOGS, TOOL_LIST_APPS

APPS = [
    {"slug": "core_mosquitto", "name": "Mosquitto broker", "state": "started", "version": "6.5"},
    {
        "slug": "a0d7b954_nodered",
        "name": "Node-RED",
        "state": "stopped",
        "version": "19.0",
        "version_latest": "19.1",
        "update_available": True,
    },
]


@pytest.fixture
def supervised(hass: HomeAssistant) -> Any:
    """A supervised install: the Supervisor-only pieces, stood in for."""
    send = AsyncMock(return_value="")
    hass.data["hassio"] = SimpleNamespace(send_command=send)
    with (
        patch.object(apps_manager, "_supervised", return_value=True),
        patch.object(apps_manager, "_installed", return_value=APPS),
    ):
        yield send


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[tool](hass, arguments)


async def test_apps_are_listed(hass: HomeAssistant, supervised: Any) -> None:
    listed = await _mcp(hass, TOOL_LIST_APPS)

    assert [a["slug"] for a in listed["apps"]] == ["core_mosquitto", "a0d7b954_nodered"]
    nodered = listed["apps"][1]
    assert nodered["state"] == "stopped"
    assert nodered["update_available"] is True
    assert nodered["version_latest"] == "19.1"
    assert "hassio.app_start" in listed["hint"]


async def test_logs_are_the_last_lines_cleaned(hass: HomeAssistant, supervised: Any) -> None:
    supervised.return_value = "\n".join(
        [f"line {i}" for i in range(300)] + ["\x1b[31mERROR\x1b[0m broker\x07 down"]
    )

    result = await _mcp(hass, TOOL_GET_APP_LOGS, slug="core_mosquitto", lines=3)

    assert result["lines"] == ["line 298", "line 299", "ERROR broker down"]
    assert supervised.await_args.args[0] == "/addons/core_mosquitto/logs"


async def test_only_an_installed_app_by_slug_is_asked(hass: HomeAssistant, supervised: Any) -> None:
    bad = await _mcp(hass, TOOL_GET_APP_LOGS, slug="../../host")
    unknown = await _mcp(hass, TOOL_GET_APP_LOGS, slug="nope")

    assert "slug" in bad["error"]
    assert "No installed app" in unknown["error"]
    supervised.assert_not_awaited()


async def test_a_supervisor_error_is_reported(hass: HomeAssistant, supervised: Any) -> None:
    supervised.side_effect = RuntimeError("Supervisor unreachable")

    result = await _mcp(hass, TOOL_GET_APP_LOGS, slug="core_mosquitto")

    assert "did not return the logs" in result["error"]


async def test_without_a_supervisor_there_are_no_apps(hass: HomeAssistant) -> None:
    with patch.object(apps_manager, "_supervised", return_value=False):
        result = await _mcp(hass, TOOL_LIST_APPS)

    assert "no Supervisor" in result["error"]


async def test_apps_need_admin() -> None:
    assert TOOL_LIST_APPS in mcp_access._ADMIN_TOOLS
    assert TOOL_GET_APP_LOGS in mcp_access._ADMIN_TOOLS


async def test_a_list_still_loading_says_so(hass: HomeAssistant) -> None:
    from homeassistant.exceptions import HomeAssistantError

    with (
        patch.object(apps_manager, "_supervised", return_value=True),
        patch.object(apps_manager, "_installed", side_effect=HomeAssistantError("not ready")),
    ):
        listed = await _mcp(hass, TOOL_LIST_APPS)
        logs = await _mcp(hass, TOOL_GET_APP_LOGS, slug="core_mosquitto")

    assert "not ready yet" in listed["error"]
    assert "not ready yet" in logs["error"]
