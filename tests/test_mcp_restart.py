"""Tests for restarting Home Assistant over MCP: checked, then confirmed."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.selora_ai import restart_manager
from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import TOOL_RESTART_HOME_ASSISTANT


async def _mcp(hass: HomeAssistant, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[TOOL_RESTART_HOME_ASSISTANT](hass, arguments)


async def test_a_valid_config_asks_first_then_restarts(hass: HomeAssistant) -> None:
    calls = async_mock_service(hass, "homeassistant", "restart")
    with patch.object(restart_manager, "_config_errors", AsyncMock(return_value=None)):
        asked = await _mcp(hass)
        assert asked["requires_confirmation"] is True
        assert calls == []

        result = await _mcp(hass, confirmed=True)
        await hass.async_block_till_done()

    assert result["status"] == "restarting"
    assert len(calls) == 1


async def test_an_invalid_config_is_reported_not_restarted(hass: HomeAssistant) -> None:
    calls = async_mock_service(hass, "homeassistant", "restart")
    with patch.object(
        restart_manager,
        "_config_errors",
        AsyncMock(return_value="Invalid config for 'light' at configuration.yaml, line 12"),
    ):
        result = await _mcp(hass, confirmed=True)
        await hass.async_block_till_done()

    assert "not valid" in result["error"]
    assert "line 12" in result["config_errors"]
    assert calls == []


async def test_a_database_upgrade_blocks_it(hass: HomeAssistant) -> None:
    """Patched where Home Assistant defines it, so a wrong import path — one
    that fails and reads as "not migrating" — fails this test."""
    calls = async_mock_service(hass, "homeassistant", "restart")
    with patch("homeassistant.helpers.recorder.async_migration_in_progress", return_value=True):
        result = await _mcp(hass, confirmed=True)

    assert "database upgrade" in result["error"]
    assert calls == []


async def test_the_yaml_editor_points_at_the_restart() -> None:
    from custom_components.selora_ai.config_yaml import _RESTART_REQUIRED

    assert "selora_restart_home_assistant" in _RESTART_REQUIRED["hint"]


def test_restarting_needs_admin() -> None:
    assert TOOL_RESTART_HOME_ASSISTANT in mcp_access._ADMIN_TOOLS


async def test_the_real_config_check_runs(hass: HomeAssistant, tmp_path: Any) -> None:
    """Unpatched: Home Assistant's own check answers — a valid configuration
    gets the confirmation, a broken one its errors. A private config dir, so
    the answer is about this test's file and no other test's."""
    hass.config.config_dir = str(tmp_path)
    async_mock_service(hass, "homeassistant", "restart")

    (tmp_path / "configuration.yaml").write_text("homeassistant:\n", encoding="utf-8")
    valid = await _mcp(hass)
    (tmp_path / "configuration.yaml").write_text("broken: [unclosed\n", encoding="utf-8")
    broken = await _mcp(hass)

    assert valid.get("requires_confirmation") is True, valid
    assert "not valid" in broken["error"]


async def test_a_credential_in_a_config_error_is_not_shown(hass: HomeAssistant) -> None:
    with patch.object(
        restart_manager,
        "_config_errors",
        AsyncMock(
            return_value=(
                "Invalid config for 'mqtt': expected str for dictionary value "
                "@ data['port']. Got {'password': 'hunter2'}"
            )
        ),
    ):
        result = await _mcp(hass)

    assert "hunter2" not in result["config_errors"]
