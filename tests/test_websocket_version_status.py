"""Tests for `selora_ai/version_status` — the code-skew handshake.

Replacing the integration's files doesn't replace the modules in memory, so a
panel served fresh off disk can end up calling last-deploy's websocket schemas.
The handler compares the code signature captured at import against the live one
and tells the panel whether a restart (Python) or a page reload (bundle) is
needed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from custom_components.selora_ai.websocket import version as version_ws

_handler = version_ws._handle_websocket_version_status.__wrapped__
_schema = version_ws._handle_websocket_version_status._ws_schema

LOADED = "0123456789abcdef"


@pytest.fixture(autouse=True)
def _reset_warn_flags() -> None:
    """The handler warns once per process; isolate that between tests."""
    version_ws._warned_restart = False
    version_ws._warned_panel = False


async def _invoke(
    hass: Any,
    *,
    disk_python: str,
    panel_disk: str = "abc123",
    panel_build: str | None = None,
) -> dict[str, Any]:
    """Drive the handler through its real schema; return the sent result."""
    msg: dict[str, Any] = {"id": 1, "type": "selora_ai/version_status"}
    if panel_build is not None:
        msg["panel_build"] = panel_build
    connection = MagicMock()
    with (
        patch.object(version_ws, "_require_admin", return_value=True),
        patch.object(version_ws, "LOADED_PYTHON_SIGNATURE", LOADED),
        patch.object(version_ws, "python_signature", return_value=disk_python),
        patch.object(version_ws, "panel_build_id", return_value=panel_disk),
    ):
        await _handler(hass, connection, _schema(msg))
    connection.send_result.assert_called_once()
    return connection.send_result.call_args[0][1]


async def test_reports_current_when_disk_matches_loaded(hass: Any) -> None:
    """Nothing changed since import → no restart, no reload."""
    result = await _invoke(hass, disk_python=LOADED, panel_disk="abc123", panel_build="abc123")
    assert result["restart_required"] is False
    assert result["panel_reload_required"] is False
    assert result["loaded_code_signature"] == LOADED


async def test_flags_restart_when_the_signature_differs(hass: Any) -> None:
    """Files deployed after this process imported them → restart required."""
    result = await _invoke(hass, disk_python="fedcba9876543210")
    assert result["restart_required"] is True
    assert result["disk_code_signature"] == "fedcba9876543210"


async def test_a_rollback_also_asks_for_a_restart(hass: Any) -> None:
    """Direction is meaningless for a signature: different code is stale code."""
    result = await _invoke(hass, disk_python="00000000deadbeef")
    assert result["restart_required"] is True


async def test_flags_reload_when_browser_bundle_is_stale(hass: Any) -> None:
    """The panel reported a build id other than the deployed one."""
    result = await _invoke(hass, disk_python=LOADED, panel_disk="new456", panel_build="old123")
    assert result["panel_reload_required"] is True
    assert result["panel_build"] == "new456"


async def test_no_panel_stamp_skips_the_bundle_check(hass: Any) -> None:
    """A client built without a build id must not be told to reload."""
    result = await _invoke(hass, disk_python=LOADED, panel_disk="new456")
    assert result["panel_reload_required"] is False


async def test_empty_panel_stamp_skips_the_bundle_check(hass: Any) -> None:
    """Same for the fallback: an empty id is "unknown", not stale."""
    result = await _invoke(hass, disk_python=LOADED, panel_disk="new456", panel_build="")
    assert result["panel_reload_required"] is False


async def test_warns_once_per_process(hass: Any, caplog: Any) -> None:
    """The panel asks on every page load; the log must not fill up."""
    await _invoke(hass, disk_python="fedcba9876543210")
    assert "restart Home Assistant" in caplog.text
    caplog.clear()
    await _invoke(hass, disk_python="fedcba9876543210")
    assert "restart Home Assistant" not in caplog.text


async def test_non_admin_is_rejected(hass: Any) -> None:
    """Admin-only, like every other panel command."""
    connection = MagicMock()
    with patch.object(version_ws, "_require_admin", return_value=False):
        await _handler(hass, connection, {"id": 1, "type": "selora_ai/version_status"})
    connection.send_result.assert_not_called()


async def test_missing_sidecar_skips_the_bundle_check(hass: Any) -> None:
    """A source checkout with no `panel.build.json` can't judge the bundle."""
    result = await _invoke(hass, disk_python=LOADED, panel_disk="", panel_build="old123")
    assert result["panel_reload_required"] is False
