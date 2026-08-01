"""Selora AI websocket handlers: version / code-skew status.

The panel calls this once on load to find out whether the code it is talking to
is the code that was deployed — see ``code_stamp`` for why a reload is not
enough and only a restart clears a Python-side skew.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import decorators
from homeassistant.core import HomeAssistant
import voluptuous as vol

from .. import _require_admin
from ..code_stamp import LOADED_PYTHON_SIGNATURE, panel_build_id, python_signature

_LOGGER = logging.getLogger(__name__)

# One warning per condition per process — the panel asks on every page load.
_warned_restart = False
_warned_panel = False


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/version_status",
        # Build id baked into the bundle the browser is running. Omitted by
        # clients built without one.
        vol.Optional("panel_build"): str,
    }
)
async def _handle_websocket_version_status(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Report whether the loaded code and the panel bundle are current."""
    if not _require_admin(connection, msg):
        return

    global _warned_restart, _warned_panel  # noqa: PLW0603

    disk_python = await hass.async_add_executor_job(python_signature)
    disk_panel = await hass.async_add_executor_job(panel_build_id)

    # Any difference means the modules in memory aren't the code on disk —
    # an update, a rollback, a deleted file. All of them need a restart.
    restart_required = disk_python != LOADED_PYTHON_SIGNATURE
    if restart_required and not _warned_restart:
        _warned_restart = True
        _LOGGER.warning(
            "Selora AI code on disk differs from the loaded code "
            "(disk=%s, loaded=%s) — restart Home Assistant to finish the "
            "update; reloading the integration re-runs setup but cannot "
            "re-import Python modules",
            disk_python,
            LOADED_PYTHON_SIGNATURE,
        )

    # A build id that doesn't match the one deployed beside the bundle means
    # this browser is running an older panel. Only report it when both sides
    # actually have an id — an unknown id is not evidence of staleness.
    sent_build = msg.get("panel_build") or ""
    panel_reload_required = bool(sent_build) and bool(disk_panel) and sent_build != disk_panel
    if panel_reload_required and not _warned_panel:
        _warned_panel = True
        _LOGGER.warning(
            "Selora AI panel loaded by the browser is stale (loaded=%s, "
            "on disk=%s) — reload the page",
            sent_build,
            disk_panel,
        )

    connection.send_result(
        msg["id"],
        {
            "loaded_code_signature": LOADED_PYTHON_SIGNATURE,
            "disk_code_signature": disk_python,
            "restart_required": restart_required,
            "panel_build": disk_panel,
            "panel_reload_required": panel_reload_required,
        },
    )


def async_register(hass: HomeAssistant) -> None:
    """Register the version websocket commands."""
    websocket_api.async_register_command(hass, _handle_websocket_version_status)
