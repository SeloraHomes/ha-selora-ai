"""Tests for reading the home's backups over MCP."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import TOOL_GET_BACKUPS


async def _mcp(hass: HomeAssistant, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[TOOL_GET_BACKUPS](hass, arguments)


def _manager(
    hass: HomeAssistant,
    backups: list[Any],
    *,
    attempted: datetime.datetime | None,
    completed: datetime.datetime | None,
    agent_errors: dict[str, Exception] | None = None,
) -> None:
    """A stand-in for Home Assistant's backup manager, in the shape it reads."""
    hass.config.components.add("backup")
    hass.data["backup"] = SimpleNamespace(
        async_get_backups=AsyncMock(
            return_value=({b.backup_id: b for b in backups}, agent_errors or {})
        ),
        config=SimpleNamespace(
            data=SimpleNamespace(
                last_attempted_automatic_backup=attempted,
                last_completed_automatic_backup=completed,
                schedule=SimpleNamespace(next_automatic_backup=None),
            )
        ),
        state="idle",
    )


def _backup(backup_id: str, days_ago: int, *, automatic: bool = True) -> Any:
    return SimpleNamespace(
        backup_id=backup_id,
        name=f"Backup {backup_id}",
        date=(dt_util.utcnow() - datetime.timedelta(days=days_ago)).isoformat(),
        agents={"backup.local": SimpleNamespace(size=250_000_000, protected=True)},
        with_automatic_settings=automatic,
        homeassistant_version="2026.9.4",
        database_included=True,
        failed_agent_ids=[],
    )


async def test_the_real_backup_integration_is_read(hass: HomeAssistant) -> None:
    """Against the core's own manager: an install with no backup yet."""
    assert await async_setup_component(hass, "backup", {})
    await hass.async_block_till_done()

    result = await _mcp(hass)

    assert result["backups"] == []
    assert result["last_completed_automatic_backup"] is None
    assert "error" not in result


async def test_backups_are_listed_newest_first(hass: HomeAssistant) -> None:
    now = dt_util.utcnow()
    _manager(
        hass,
        [_backup("old", 3), _backup("new", 1, automatic=False)],
        attempted=now - datetime.timedelta(days=1),
        completed=now - datetime.timedelta(days=1),
    )

    result = await _mcp(hass)

    assert [b["backup_id"] for b in result["backups"]] == ["new", "old"]
    assert result["backups"][0]["automatic"] is False
    assert result["backups"][0]["size_mb"] == 250.0
    assert result["backups"][0]["locations"] == ["backup.local"]
    assert "warning" not in result


async def test_a_failed_automatic_backup_is_called_out(hass: HomeAssistant) -> None:
    now = dt_util.utcnow()
    _manager(
        hass,
        [_backup("old", 2)],
        attempted=now - datetime.timedelta(hours=1),
        completed=now - datetime.timedelta(days=2),
        agent_errors={"cloud.cloud": RuntimeError("quota exceeded")},
    )

    result = await _mcp(hass)

    assert "failed" in result["warning"]
    assert result["location_errors"] == {"cloud.cloud": "quota exceeded"}


async def test_a_stale_automatic_backup_is_called_out(hass: HomeAssistant) -> None:
    long_ago = dt_util.utcnow() - datetime.timedelta(days=30)
    _manager(hass, [], attempted=long_ago, completed=long_ago)

    result = await _mcp(hass)

    assert "30 days" in result["warning"]


async def test_without_the_backup_integration_it_says_so(hass: HomeAssistant) -> None:
    assert "not loaded" in (await _mcp(hass))["error"]


async def test_a_backup_being_made_is_not_a_failure(hass: HomeAssistant) -> None:
    now = dt_util.utcnow()
    _manager(
        hass,
        [],
        attempted=now - datetime.timedelta(minutes=2),
        completed=now - datetime.timedelta(days=1),
    )
    hass.data["backup"].state = "create_backup"

    result = await _mcp(hass)

    assert "warning" not in result
    assert result["state"] == "create_backup"


async def test_an_unknown_origin_stays_unknown_and_order_is_by_instant(
    hass: HomeAssistant,
) -> None:
    earlier = _backup("earlier", 0)
    earlier.date = "2026-11-01T01:30:00-04:00"
    later = _backup("later", 0)
    later.date = "2026-11-01T01:15:00-05:00"
    later.with_automatic_settings = None
    _manager(hass, [earlier, later], attempted=None, completed=None)

    result = await _mcp(hass)

    assert [b["backup_id"] for b in result["backups"]] == ["later", "earlier"]
    assert result["backups"][0]["automatic"] is None


async def test_reading_backups_needs_admin() -> None:
    assert TOOL_GET_BACKUPS in mcp_access._ADMIN_TOOLS
