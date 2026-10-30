"""Turning a Frigate camera's snapshots on or off, from chat and over MCP."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai import _apply_destructive_actions
from custom_components.selora_ai.frigate import setup as frigate_setup
from custom_components.selora_ai.llm_client.command_policy import _pending_destructive_from_log
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import TOOL_SET_CAMERA_SNAPSHOTS
from custom_components.selora_ai.selora_auth import SeloraAuthContext
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import COMMAND_TOOL_NAMES, CONFIG_TOOL_NAMES

FRIGATE = "http://frigate.local:5000"


def _config(*, enabled: bool, running: bool | None = None, days: int = 10) -> dict[str, Any]:
    """Frigate's running config; *running* is what the Snapshots switch set."""
    return {
        "cameras": {
            "front_door": {
                "friendly_name": "Front Door",
                "snapshots": {
                    "enabled": enabled if running is None else running,
                    "enabled_in_config": enabled,
                    "retain": {"default": days},
                },
            }
        }
    }


@pytest.fixture
def frigate(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain="frigate", data={"url": FRIGATE})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry


def _api(aioclient_mock: Any, config: dict[str, Any]) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", json=config)
    aioclient_mock.put(f"{FRIGATE}/api/config/set", json={"success": True})
    aioclient_mock.post(f"{FRIGATE}/api/restart", json={"success": True})


def _written(aioclient_mock: Any) -> list[dict[str, Any]]:
    return [
        c[2] if isinstance(c[2], dict) else json.loads(c[2])
        for c in aioclient_mock.mock_calls
        if str(c[1]).endswith("/api/config/set")
    ]


async def _run(hass: HomeAssistant, arguments: dict[str, Any]) -> tuple[dict, ToolExecutor]:
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    return await executor.execute("set_camera_snapshots", arguments), executor


async def _mcp(hass: HomeAssistant, **arguments: Any) -> dict[str, Any]:
    result = await mcp_dispatch._jsonrpc_dispatch(
        hass,
        "tools/call",
        {"name": TOOL_SET_CAMERA_SNAPSHOTS, "arguments": arguments},
        SeloraAuthContext("admin", None, True, "ha_token"),
    )
    return json.loads(result["content"][0]["text"])


async def test_snapshots_are_turned_on_on_a_card_then_written(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _config(enabled=False))

    result, executor = await _run(
        hass, {"camera": "Front Door", "enabled": True, "retain_days": 30}
    )

    assert result["destructive"]["label"] == (
        "Keep a snapshot of everything Front Door tracks, kept 30 days (Frigate restarts)"
    )
    assert _written(aioclient_mock) == []
    (action,) = _pending_destructive_from_log(executor.call_log)
    action = {**action, "fingerprint": result["destructive"]["fingerprint"]}

    with patch.object(frigate_setup, "async_reload_when_back", AsyncMock()):
        applied, errors = await _apply_destructive_actions(hass, [action])

    assert errors == []
    assert applied == [
        "Front Door keeps a snapshot of everything it tracks, kept 30 days. "
        "Frigate is restarting, about a minute."
    ]
    (body,) = _written(aioclient_mock)
    assert body["config_data"] == {
        "cameras": {"front_door": {"snapshots": {"enabled": True, "retain": {"default": 30}}}}
    }
    assert any(str(c[1]).endswith("/api/restart") for c in aioclient_mock.mock_calls)


async def test_the_switch_does_not_count_as_the_configuration(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    # Switched on at runtime, off in its configuration: gone after a restart.
    _api(aioclient_mock, _config(enabled=False, running=True))

    result, _ = await _run(hass, {"camera": "front_door", "enabled": True})

    assert result.get("requires_approval"), result


async def test_snapshots_already_kept_need_no_card(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _config(enabled=True))

    result, _ = await _run(hass, {"camera": "front_door", "enabled": True})

    assert result["unchanged"] is True
    assert "already keeps snapshots, kept 10 days" in result["note"]


async def test_retention_out_of_range_is_refused(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _config(enabled=False))

    result, _ = await _run(hass, {"camera": "front_door", "enabled": True, "retain_days": 0})

    assert "between 1 and 365" in result["error"]


async def test_a_camera_changed_since_the_card_is_not_written(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _config(enabled=True))
    result, executor = await _run(hass, {"camera": "front_door", "enabled": False})
    (action,) = _pending_destructive_from_log(executor.call_log)
    aioclient_mock.clear_requests()
    _api(aioclient_mock, _config(enabled=True, days=3))

    _applied, errors = await _apply_destructive_actions(
        hass, [{**action, "fingerprint": result["destructive"]["fingerprint"]}]
    )

    assert "changed since it was shown" in errors[0]
    assert _written(aioclient_mock) == []


async def test_over_mcp_snapshots_are_confirmed_in_two_calls(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _config(enabled=True))

    asked = await _mcp(hass, camera="Front Door", enabled=False)

    assert asked["requires_confirmation"] is True
    assert "stay until their retention ends" in asked["change"]
    assert _written(aioclient_mock) == []

    with patch.object(frigate_setup, "async_reload_when_back", AsyncMock()):
        done = await _mcp(
            hass,
            camera="Front Door",
            enabled=False,
            confirmed=True,
            confirm_token=asked["confirm_token"],
        )

    assert done["done"].startswith("Front Door no longer keeps snapshots")
    (body,) = _written(aioclient_mock)
    assert body["config_data"] == {"cameras": {"front_door": {"snapshots": {"enabled": False}}}}


def test_the_tool_is_in_the_lanes_that_change_frigate() -> None:
    assert "set_camera_snapshots" in COMMAND_TOOL_NAMES
    assert "set_camera_snapshots" in CONFIG_TOOL_NAMES
