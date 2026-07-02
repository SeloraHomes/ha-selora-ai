"""Selora AI websocket handlers: devices.

Extracted from __init__.py. Handlers reach shared integration
helpers via ``from .. import`` (safe: this module is imported
lazily at registration time, after the package has loaded).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import decorators
from homeassistant.core import HomeAssistant
import voluptuous as vol

from .. import (
    _automation_references_device,
    _get_pattern_store,
    _require_admin,
)

_LOGGER = logging.getLogger(__name__)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_device_detail",
        vol.Required("device_id"): str,
    }
)
async def _handle_websocket_get_device_detail(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return full device detail with state history, linked automations, and patterns."""
    if not _require_admin(connection, msg):
        return

    try:
        device_id = msg["device_id"]

        # 1. Device metadata + entity states (reuse MCP tool)
        from ..mcp_server import _tool_get_device

        device_data = await _tool_get_device(hass, {"device_id": device_id})
        if "error" in device_data:
            connection.send_error(msg["id"], "not_found", device_data["error"])
            return

        # 2. State history (last 24h for device entities)
        entity_ids = [e["entity_id"] for e in device_data.get("entities", [])]
        state_history: list[dict[str, Any]] = []
        if entity_ids:
            try:
                from homeassistant.components.recorder import get_instance
                from homeassistant.components.recorder.history import (
                    get_significant_states,
                )

                now = datetime.now(UTC)
                start = now - timedelta(hours=24)
                states = await get_instance(hass).async_add_executor_job(
                    get_significant_states, hass, start, now, entity_ids
                )
                for eid, eid_states in states.items():
                    for state in eid_states[-20:]:  # Last 20 per entity
                        state_history.append(
                            {
                                "entity_id": eid,
                                "state": state.state,
                                "last_changed": state.last_changed.isoformat()
                                if state.last_changed
                                else None,
                            }
                        )
            except (
                ImportError,
                KeyError,
            ):
                _LOGGER.debug("Recorder not available for device detail history")

        # 3. Linked automations (scan for entity references)
        linked_automations: list[dict[str, Any]] = []
        from pathlib import Path

        from ..automation_utils import _read_automations_yaml

        automations_path = Path(hass.config.config_dir) / "automations.yaml"
        try:
            yaml_autos = await hass.async_add_executor_job(_read_automations_yaml, automations_path)
            identifiers = set(entity_ids) | {device_id}
            for auto in yaml_autos:
                if _automation_references_device(auto, identifiers):
                    linked_automations.append(
                        {
                            "id": auto.get("id", ""),
                            "alias": auto.get("alias", ""),
                            "description": auto.get("description", ""),
                        }
                    )
        except (
            FileNotFoundError,
            OSError,
        ):
            _LOGGER.debug("Could not read automations for device detail")

        # 4. Related patterns
        related_patterns: list[dict[str, Any]] = []
        pattern_store = _get_pattern_store(hass)
        if pattern_store:
            all_patterns = await pattern_store.get_patterns()
            entity_set = set(entity_ids)
            for p in all_patterns:
                pattern_entities = set(p.get("entity_ids", []))
                if pattern_entities & entity_set:
                    related_patterns.append(
                        {
                            "pattern_id": p.get("pattern_id", ""),
                            "type": p.get("type", ""),
                            "description": p.get("description", ""),
                            "confidence": p.get("confidence", 0),
                            "status": p.get("status", ""),
                        }
                    )

        connection.send_result(
            msg["id"],
            {
                **device_data,
                "state_history": state_history,
                "linked_automations": linked_automations,
                "related_patterns": related_patterns,
            },
        )
    except Exception as exc:
        _LOGGER.exception("Error in get_device_detail")
        connection.send_error(msg["id"], "unknown_error", str(exc))


def async_register(hass: HomeAssistant) -> None:
    """Register the devices websocket commands."""
    from homeassistant.components import websocket_api

    websocket_api.async_register_command(hass, _handle_websocket_get_device_detail)
