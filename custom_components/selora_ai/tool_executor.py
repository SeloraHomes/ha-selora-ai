"""Execute tool calls from the LLM using existing Selora AI components.

Dispatches tool names to handlers that wrap DeviceManager and MCP server
functions. No logic is duplicated — all tool execution calls existing code.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .const import MAX_TOOL_RESULT_CHARS
from .device_manager import DeviceManager
from .tool_registry import TOOL_MAP

_LOGGER = logging.getLogger(__name__)


class ToolExecutor:
    """Dispatch tool calls to existing Selora AI components."""

    def __init__(
        self,
        hass: HomeAssistant,
        device_manager: DeviceManager,
        *,
        is_admin: bool,
    ) -> None:
        self._hass = hass
        self._device_manager = device_manager
        self._is_admin = is_admin

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a tool call and return a JSON-serialisable result."""
        tool_def = TOOL_MAP.get(tool_name)
        if tool_def is None:
            _LOGGER.warning("Unknown tool requested by LLM: %s", tool_name)
            return {"error": f"Unknown tool: {tool_name}"}

        if tool_def.requires_admin and not self._is_admin:
            _LOGGER.warning("Non-admin attempted write tool: %s", tool_name)
            return {"error": f"Tool '{tool_name}' requires admin privileges"}

        handler = self._handlers.get(tool_name)
        if handler is None:
            return {"error": f"No handler for tool: {tool_name}"}

        try:
            result = await handler(arguments)
        except Exception as exc:
            _LOGGER.exception("Tool %s execution failed", tool_name)
            return {"error": f"Tool execution failed: {exc}"}

        return _truncate_result(result)

    @property
    def _handlers(self) -> dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]]:
        """Map tool names to handler coroutines."""
        return {
            "get_home_snapshot": self._get_home_snapshot,
            "discover_network_devices": self._discover_network_devices,
            "list_discovered_flows": self._list_discovered_flows,
            "start_device_flow": self._start_device_flow,
            "accept_device_flow": self._accept_device_flow,
            "list_devices": self._list_devices,
            "get_device": self._get_device,
            "list_suggestions": self._list_suggestions,
            "accept_suggestion": self._accept_suggestion,
            "dismiss_suggestion": self._dismiss_suggestion,
        }

    # ── Read tools ──────────────────────────────────────────────────

    async def _get_home_snapshot(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        from .mcp_server import _tool_get_home_snapshot

        return await _tool_get_home_snapshot(self._hass)

    async def _discover_network_devices(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._device_manager.discover_network_devices()

    async def _list_discovered_flows(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        flows = await self._device_manager.list_discovered()
        return {"flows": flows}

    async def _list_devices(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .mcp_server import _tool_list_devices

        return await _tool_list_devices(self._hass, arguments)

    async def _get_device(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .mcp_server import _tool_get_device

        return await _tool_get_device(self._hass, arguments)

    _VALID_SUGGESTION_STATUSES = frozenset({"pending", "accepted", "dismissed", "snoozed"})

    async def _list_suggestions(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Return pending automation suggestions from the pattern store."""
        from . import _get_pattern_store
        from .types import SuggestionDict

        store = _get_pattern_store(self._hass)
        if store is None:
            return {"suggestions": [], "message": "No suggestion data available yet."}

        status = str(arguments.get("status", "pending")).strip()
        if status not in self._VALID_SUGGESTION_STATUSES:
            return {
                "error": f"Invalid status '{status}'. Must be one of: {', '.join(sorted(self._VALID_SUGGESTION_STATUSES))}"
            }

        suggestions: list[SuggestionDict] = await store.get_suggestions(status=status)

        # Return a concise view for the LLM to present conversationally
        result = []
        for s in suggestions[:10]:  # Cap at 10 to keep token usage bounded
            result.append(
                {
                    "suggestion_id": s.get("suggestion_id", ""),
                    "description": s.get("description", ""),
                    "confidence": round(s.get("confidence", 0), 2),
                    "evidence_summary": s.get("evidence_summary", ""),
                }
            )

        return {
            "suggestions": result,
            "total": len(suggestions),
        }

    # ── Write tools (admin-only, checked in execute()) ──────────────

    async def _accept_suggestion(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Accept a PatternStore suggestion and create the automation in HA."""
        from . import _get_pattern_store
        from .automation_utils import assess_automation_risk, async_create_automation

        suggestion_id = str(arguments.get("suggestion_id", "")).strip()
        if not suggestion_id:
            return {"error": "suggestion_id is required"}

        store = _get_pattern_store(self._hass)
        if store is None:
            return {"error": "Suggestion store not available yet"}

        suggestions = await store.get_suggestions(status="pending")
        target = next((s for s in suggestions if s.get("suggestion_id") == suggestion_id), None)
        if target is None:
            return {"error": f"Suggestion {suggestion_id} not found or not pending"}

        automation_data = target.get("automation_data", {})
        if not automation_data:
            return {"error": "Suggestion does not include automation data"}

        created = await async_create_automation(
            self._hass,
            automation_data,
            version_message=f"Created from suggestion {suggestion_id}",
        )
        if not created["success"]:
            return {"error": "Failed to create automation from suggestion"}

        await store.update_suggestion_status(suggestion_id, status="accepted")

        return {
            "suggestion_id": suggestion_id,
            "status": "accepted",
            "automation_id": created.get("automation_id", ""),
            "risk_assessment": assess_automation_risk(automation_data),
        }

    async def _dismiss_suggestion(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dismiss a PatternStore suggestion the user doesn't want."""
        from datetime import UTC, datetime

        from . import _get_pattern_store

        suggestion_id = str(arguments.get("suggestion_id", "")).strip()
        reason = str(arguments.get("reason", "")).strip() or "user-declined"
        if not suggestion_id:
            return {"error": "suggestion_id is required"}

        store = _get_pattern_store(self._hass)
        if store is None:
            return {"error": "Suggestion store not available yet"}

        updated = await store.update_suggestion_status(
            suggestion_id,
            status="dismissed",
            dismissed_at=datetime.now(UTC).isoformat(),
            dismissal_reason=reason,
        )
        if not updated:
            return {"error": f"Suggestion {suggestion_id} not found"}

        return {
            "suggestion_id": suggestion_id,
            "status": "dismissed",
            "reason": reason,
        }

    async def _start_device_flow(self, arguments: dict[str, Any]) -> dict[str, Any]:
        domain = str(arguments.get("domain", "")).strip()
        host = str(arguments.get("host", "")).strip()
        if not domain:
            return {"error": "domain is required"}
        return await self._device_manager.start_device_flow(domain, host)

    async def _accept_device_flow(self, arguments: dict[str, Any]) -> dict[str, Any]:
        flow_id = str(arguments.get("flow_id", "")).strip()
        if not flow_id:
            return {"error": "flow_id is required"}
        return await self._device_manager.accept_flow(flow_id)


def _truncate_result(result: dict[str, Any]) -> dict[str, Any]:
    """Truncate large tool results semantically to prevent token explosion.

    Instead of cutting JSON at a character boundary (which produces malformed
    data and causes LLM hallucination), we remove entities from the end of
    lists until the result fits within the character limit.
    """
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    if len(serialized) <= MAX_TOOL_RESULT_CHARS:
        return result

    original_len = len(serialized)

    # Semantically trim: remove items from lists until under limit
    trimmed = _semantic_trim(result, MAX_TOOL_RESULT_CHARS)

    _LOGGER.debug(
        "Truncated tool result from %d to %d chars",
        original_len,
        len(json.dumps(trimmed, ensure_ascii=False, default=str)),
    )
    return trimmed


def _semantic_trim(data: dict[str, Any], limit: int) -> dict[str, Any]:
    """Recursively trim list values in a dict until serialized size fits."""
    result = dict(data)
    trimmed_count = 0

    # Find dict values that may contain nested lists to trim
    dict_keys = [k for k, v in result.items() if isinstance(v, dict)]

    # First try trimming nested dicts (e.g., areas with entity lists)
    for key in dict_keys:
        inner = result[key]
        if isinstance(inner, dict):
            trimmed_inner = {}
            for sub_key, sub_val in inner.items():
                if isinstance(sub_val, list):
                    trimmed_inner[sub_key] = sub_val
                else:
                    trimmed_inner[sub_key] = sub_val
            result[key] = trimmed_inner

    # Iteratively remove items from the longest list until under limit
    for _ in range(500):  # safety cap
        serialized = json.dumps(result, ensure_ascii=False, default=str)
        if len(serialized) <= limit:
            break

        # Find the longest list anywhere in the result
        longest_key, longest_sub = _find_longest_list(result)
        if longest_key is None:
            break  # No more lists to trim

        if longest_sub is not None:
            # Nested: result[longest_key][longest_sub] is the list
            result[longest_key][longest_sub].pop()
            trimmed_count += 1
            if not result[longest_key][longest_sub]:
                del result[longest_key][longest_sub]
        else:
            # Top-level list
            result[longest_key].pop()
            trimmed_count += 1
            if not result[longest_key]:
                del result[longest_key]

    if trimmed_count > 0:
        result["truncated"] = (
            f"{trimmed_count} items were omitted to fit the response size limit. Ask the user if they want more details."
        )

    return result


def _find_longest_list(
    data: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Find the key path to the longest list in a (possibly nested) dict."""
    best_key: str | None = None
    best_sub: str | None = None
    best_len = 0

    for key, val in data.items():
        if isinstance(val, list) and len(val) > best_len:
            best_key = key
            best_sub = None
            best_len = len(val)
        elif isinstance(val, dict):
            for sub_key, sub_val in val.items():
                if isinstance(sub_val, list) and len(sub_val) > best_len:
                    best_key = key
                    best_sub = sub_key
                    best_len = len(sub_val)

    return best_key, best_sub
