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
from .helpers import caller_scope
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
        session_id: str | None = None,
    ) -> None:
        self._hass = hass
        self._device_manager = device_manager
        self._is_admin = is_admin
        # Threaded through to ``_tool_execute_command`` /
        # ``_tool_validate_action`` so they can honour the user's
        # Session-scope and Always-scope approval grants. Without this
        # the tool path would always answer ``requires_approval`` for
        # a REVIEW service even after the user clicked Allow — every
        # turn would surface another approval card.
        self._session_id = session_id
        # Per-request log of tools that successfully dispatched. The
        # streaming chat handler reads this after the LLM stream ends
        # to suppress duplicate execute_command JSON blocks.
        self.call_log: list[dict[str, Any]] = []

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
            # Some tools (the dashboard reads) are non-admin but must still
            # respect a per-dashboard require_admin flag, and the handler
            # signature carries no identity.
            with caller_scope(self._is_admin):
                result = await handler(arguments)
        except Exception as exc:
            _LOGGER.exception("Tool %s execution failed", tool_name)
            err_result = {"error": f"Tool execution failed: {exc}"}
            self.call_log.append({"tool": tool_name, "arguments": arguments, "result": err_result})
            return err_result

        truncated = _truncate_result(result)
        self.call_log.append({"tool": tool_name, "arguments": arguments, "result": truncated})
        return truncated

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
            "get_device_triggers": self._get_device_triggers,
            "get_entity_state": self._get_entity_state,
            "find_entities_by_area": self._find_entities_by_area,
            "validate_action": self._validate_action,
            "execute_command": self._execute_command,
            "activate_scene": self._activate_scene,
            "list_dashboards": self._list_dashboards,
            "insert_dashboard_card": self._insert_dashboard_card,
            "search_entities": self._search_entities,
            "get_entity_history": self._get_entity_history,
            "eval_template": self._eval_template,
            "list_suggestions": self._list_suggestions,
            "accept_suggestion": self._accept_suggestion,
            "dismiss_suggestion": self._dismiss_suggestion,
            "delete_automation": self._delete_automation,
            "delete_scene": self._delete_scene,
            "list_groups": self._list_groups,
            "create_group": self._create_group,
            "update_group": self._update_group,
            "delete_group": self._delete_group,
            "list_areas": self._list_areas,
            "assign_area": self._assign_area,
            "create_area": self._create_area,
            "update_area": self._update_area,
            "delete_area": self._delete_area,
            "update_entity": self._update_entity,
            "update_device": self._update_device,
            "list_services": self._list_services,
            "list_scripts": self._list_scripts,
            "get_script": self._get_script,
            "set_script": self._set_script,
            "delete_script": self._delete_script,
            "list_labels": self._list_labels,
            "create_label": self._create_label,
            "assign_labels": self._assign_labels,
            "delete_label": self._delete_label,
            "list_helpers": self._list_helpers,
            "get_logs": self._get_logs,
            "get_automation_traces": self._get_automation_traces,
            "get_dashboard": self._get_dashboard,
            "get_dashboard_card": self._get_dashboard_card,
            "add_dashboard_view": self._add_dashboard_view,
            "update_dashboard_view": self._update_dashboard_view,
            "remove_dashboard_view": self._remove_dashboard_view,
            "update_dashboard_card": self._update_dashboard_card,
            "remove_dashboard_card": self._remove_dashboard_card,
            "move_dashboard_card": self._move_dashboard_card,
            "group_dashboard_cards": self._group_dashboard_cards,
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

    async def _get_device_triggers(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .mcp_server import _tool_get_device_triggers

        return await _tool_get_device_triggers(self._hass, arguments)

    async def _get_entity_state(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .mcp_server import _tool_get_entity_state

        return await _tool_get_entity_state(self._hass, arguments)

    async def _find_entities_by_area(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .mcp_server import _tool_find_entities_by_area

        return await _tool_find_entities_by_area(self._hass, arguments)

    async def _validate_action(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .mcp_server import _tool_validate_action

        return await _tool_validate_action(self._hass, arguments, session_id=self._session_id)

    async def _execute_command(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .mcp_server import _tool_execute_command

        return await _tool_execute_command(self._hass, arguments, session_id=self._session_id)

    async def _activate_scene(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .mcp_server import _tool_activate_scene

        return await _tool_activate_scene(self._hass, arguments)

    async def _list_dashboards(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        # Every dashboard, not just the writable ones: this is how a caller
        # discovers the url_path for the read tools, which handle YAML boards
        # fine. `editable` is what keeps it usable as a placement picker.
        from .dashboard_manager import list_dashboards

        return {"dashboards": await list_dashboards(self._hass)}

    async def _insert_dashboard_card(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .dashboard_manager import async_insert_card

        return await async_insert_card(self._hass, arguments)

    async def _search_entities(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .mcp_server import _tool_search_entities

        return await _tool_search_entities(self._hass, arguments)

    async def _get_entity_history(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .mcp_server import _tool_get_entity_history

        return await _tool_get_entity_history(self._hass, arguments)

    async def _eval_template(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .mcp_server import _tool_eval_template

        return await _tool_eval_template(self._hass, arguments)

    async def _list_groups(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .mcp_server import _tool_list_groups

        return await _tool_list_groups(self._hass, arguments)

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

    async def _delete_automation(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Resolve a delete target and surface a confirmation card.

        The tool never deletes directly — it returns a ``requires_approval``
        result carrying a ``delete`` descriptor. The tool loop short-circuits
        on ``requires_approval`` and the synthesizer turns it into a
        ``command_approval`` card; the actual delete runs only when the user
        taps Delete (``_resolve_approval`` in ``__init__``).
        """
        from .mcp_server import _preview_delete_automation

        return await _preview_delete_automation(self._hass, arguments)

    async def _delete_scene(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Resolve a delete target and surface a confirmation card.

        See :meth:`_delete_automation` — deletion is deferred to the user's
        tap on the confirmation card.
        """
        from .mcp_server import _preview_delete_scene

        return await _preview_delete_scene(self._hass, arguments)

    async def _create_group(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Create a group helper (executes immediately — creation is reversible)."""
        from .mcp_server import _tool_create_group

        return await _tool_create_group(self._hass, arguments)

    async def _update_group(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Change a group's members or name (executes immediately)."""
        from .mcp_server import _tool_update_group

        return await _tool_update_group(self._hass, arguments)

    async def _delete_group(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Resolve a delete target and surface a confirmation card.

        See :meth:`_delete_automation` — deletion is deferred to the user's
        tap on the confirmation card.
        """
        from .mcp_server import _preview_delete_group

        return await _preview_delete_group(self._hass, arguments)

    # ── Registry tools ──────────────────────────────────────────────

    async def _list_areas(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .registry_manager import area_overview

        return area_overview(self._hass, include_entities=bool(arguments.get("include_entities")))

    async def _list_services(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .registry_manager import list_services

        return list_services(self._hass, arguments.get("domain"))

    async def _assign_area(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .registry_manager import async_assign_area

        return await async_assign_area(
            self._hass,
            area=str(arguments.get("area", "")),
            entity_ids=_as_list(arguments.get("entity_ids")),
            device_ids=_as_list(arguments.get("device_ids")),
        )

    async def _create_area(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .registry_manager import async_create_area

        return async_create_area(
            self._hass,
            name=str(arguments.get("name", "")),
            floor=_opt_str(arguments.get("floor")),
            icon=_opt_str(arguments.get("icon")),
            aliases=_opt_list(arguments.get("aliases")),
        )

    async def _update_area(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .registry_manager import async_update_area

        return async_update_area(
            self._hass,
            area=str(arguments.get("area", "")),
            new_name=_opt_str(arguments.get("new_name")),
            floor=_opt_str(arguments.get("floor")),
            icon=_opt_str(arguments.get("icon")),
            aliases=_opt_list(arguments.get("aliases")),
        )

    async def _delete_area(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Resolve a delete target and surface a confirmation card.

        See :meth:`_delete_automation` — deletion is deferred to the user's
        tap on the confirmation card.
        """
        from .mcp_server import _preview_delete_area

        return await _preview_delete_area(self._hass, arguments)

    async def _update_entity(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Metadata edits execute; a disable or an entity_id rename asks first.

        See :meth:`_delete_automation` — the irreversible cases are deferred to
        the user's tap on the confirmation card.
        """
        from .mcp_server import _preview_update_entity

        return await _preview_update_entity(self._hass, arguments)

    async def _update_device(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Rename and area moves execute; a disable asks first."""
        from .mcp_server import _preview_update_device

        return await _preview_update_device(self._hass, arguments)

    # ── Scripts ─────────────────────────────────────────────────────

    async def _list_scripts(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        from .script_manager import async_list_scripts

        return await async_list_scripts(self._hass)

    async def _get_script(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .script_manager import async_get_script

        return await async_get_script(self._hass, str(arguments.get("script", "")))

    async def _set_script(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Creating a script executes; replacing an existing one asks first."""
        from .mcp_server import _preview_set_script

        sequence = arguments.get("sequence")
        if not isinstance(sequence, list):
            return {"error": "sequence must be a list of action steps."}
        return await _preview_set_script(self._hass, arguments)

    async def _delete_script(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Resolve a delete target and surface a confirmation card.

        See :meth:`_delete_automation` — deletion is deferred to the user's
        tap on the confirmation card.
        """
        from .mcp_server import _preview_delete_script

        return await _preview_delete_script(self._hass, arguments)

    # ── Labels ──────────────────────────────────────────────────────

    async def _list_labels(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        from .label_manager import label_overview

        return label_overview(self._hass)

    async def _create_label(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .label_manager import async_create_label

        return async_create_label(
            self._hass,
            name=str(arguments.get("name", "")),
            icon=_opt_str(arguments.get("icon")),
            color=_opt_str(arguments.get("color")),
        )

    async def _assign_labels(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .label_manager import async_assign_labels

        return await async_assign_labels(
            self._hass,
            add_labels=_as_list(arguments.get("add_labels")),
            remove_labels=_as_list(arguments.get("remove_labels")),
            entity_ids=_as_list(arguments.get("entity_ids")),
            device_ids=_as_list(arguments.get("device_ids")),
            areas=_as_list(arguments.get("areas")),
        )

    async def _delete_label(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Resolve a delete target and surface a confirmation card."""
        from .mcp_server import _preview_delete_label

        return await _preview_delete_label(self._hass, arguments)

    # ── Helpers and diagnostics ─────────────────────────────────────

    async def _list_helpers(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .registry_manager import helper_overview

        return await helper_overview(self._hass, _opt_str(arguments.get("domain")))

    async def _get_logs(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .diagnostics_tools import get_logs

        return get_logs(
            self._hass,
            level=_opt_str(arguments.get("level")),
            contains=_opt_str(arguments.get("contains")),
        )

    async def _get_automation_traces(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .diagnostics_tools import get_automation_traces

        return await get_automation_traces(self._hass, str(arguments.get("automation", "")))

    # ── Dashboards ──────────────────────────────────────────────────

    async def _get_dashboard(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .dashboard_manager import async_get_dashboard

        return await async_get_dashboard(
            self._hass,
            _opt_str(arguments.get("dashboard_target")),
            arguments.get("view"),
        )

    async def _get_dashboard_card(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .dashboard_manager import async_get_card

        return await async_get_card(
            self._hass,
            _opt_str(arguments.get("dashboard_target")),
            arguments.get("view"),
            _as_index(arguments.get("card_index")),
        )

    async def _add_dashboard_view(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .dashboard_manager import async_add_view

        return await async_add_view(
            self._hass,
            target=_opt_str(arguments.get("dashboard_target")),
            title=str(arguments.get("title", "")),
            path=_opt_str(arguments.get("path")),
            icon=_opt_str(arguments.get("icon")),
            sections=bool(_opt_bool(arguments.get("sections"))),
        )

    async def _update_dashboard_view(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .dashboard_manager import async_update_view

        return await async_update_view(
            self._hass,
            target=_opt_str(arguments.get("dashboard_target")),
            view=arguments.get("view"),
            title=_opt_str(arguments.get("title")),
            path=_opt_str(arguments.get("path")),
            icon=_opt_str(arguments.get("icon")),
            clear=_opt_list(arguments.get("clear")),
            expected_fingerprint=_opt_str(arguments.get("expected_fingerprint")),
        )

    async def _remove_dashboard_view(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Resolve a removal target and surface a confirmation card.

        A view takes every card on it, so this defers to the user's tap — see
        :meth:`_delete_automation`.
        """
        from .mcp_server import _preview_remove_dashboard_view

        return await _preview_remove_dashboard_view(self._hass, arguments)

    async def _update_dashboard_card(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .dashboard_manager import async_update_card

        card = arguments.get("card")
        if not isinstance(card, dict):
            return {"error": "card must be an object with a 'type' field."}
        return await async_update_card(
            self._hass,
            target=_opt_str(arguments.get("dashboard_target")),
            view=arguments.get("view"),
            card_index=_as_index(arguments.get("card_index")),
            card=card,
            expected_fingerprint=_opt_str(arguments.get("expected_fingerprint")),
        )

    async def _remove_dashboard_card(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .dashboard_manager import async_remove_card

        return await async_remove_card(
            self._hass,
            target=_opt_str(arguments.get("dashboard_target")),
            view=arguments.get("view"),
            card_index=_as_index(arguments.get("card_index")),
            expected_fingerprint=_opt_str(arguments.get("expected_fingerprint")),
        )

    async def _move_dashboard_card(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .dashboard_manager import async_move_card

        return await async_move_card(
            self._hass,
            target=_opt_str(arguments.get("dashboard_target")),
            view=arguments.get("view"),
            from_index=_as_index(arguments.get("from_index")),
            to_index=_as_index(arguments.get("to_index")),
            expected_fingerprint=_opt_str(arguments.get("expected_fingerprint")),
            expected_view_fingerprint=_opt_str(arguments.get("expected_view_fingerprint")),
        )

    async def _group_dashboard_cards(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .dashboard_manager import async_group_cards

        raw = arguments.get("card_indices")
        indices = [_as_index(i) for i in raw] if isinstance(raw, list) else []
        container = arguments.get("container")
        return await async_group_cards(
            self._hass,
            target=_opt_str(arguments.get("dashboard_target")),
            view=arguments.get("view"),
            card_indices=indices,
            container=container if isinstance(container, dict) else {},
            expected_view_fingerprint=_opt_str(arguments.get("expected_view_fingerprint")),
        )

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


def _as_list(value: Any) -> list[str]:
    """Coerce a tool argument to a list of non-empty strings.

    Models emit a bare string where the schema says array often enough that
    treating it as a one-element list is worth more than the strictness — the
    alternative is refusing "assign_area(entity_ids='light.lamp')" over syntax
    the user never sees.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _as_index(value: Any) -> int:
    """Coerce a card index, tolerating the string a model often sends.

    A JSON-schema integer still arrives as "2" from some providers. Anything
    unparseable becomes -1, which every caller rejects as out of range rather
    than silently addressing card 0.
    """
    if isinstance(value, bool):
        return -1
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    return int(text) if text.lstrip("-").isdigit() else -1


def _opt_list(value: Any) -> list[str] | None:
    """Absent vs. explicitly-empty, for replacement-list arguments.

    ``None`` means the argument was omitted; ``[]`` means the caller sent a real
    empty array. The distinction is the only way to clear a replacement list:
    treating ``[]`` as absent makes "remove the last alias" a silent no-op, and
    an alias list is documented as a replacement, so an empty one is a request a
    user can genuinely make.

    An empty *string* still counts as absent. That is not an inconsistency: for
    an array-typed parameter a real ``[]`` is a deliberate value, whereas ``""``
    is a type-mismatched filler models routinely emit for params they are not
    using — honouring it would wipe aliases as a side effect of a rename.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return [value.strip()] if value.strip() else None
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return None


def _opt_str(value: Any) -> str | None:
    """None for an absent or blank argument, the trimmed string otherwise.

    A blank string is treated as absent for the same reason the group tools
    do it: models routinely fill unused optional params with ``""``, and
    honouring that literally would blank the very field the user asked to keep.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_bool(value: Any) -> bool | None:
    """Tri-state coercion — ``False`` is a real request, absent is not.

    Unlike the string case, a literal ``false`` carries intent here ("unhide
    it", "stop exposing it"), so only ``None`` and an empty string mean absent.
    JSON-schema booleans still arrive as the strings "true"/"false" from some
    providers, hence the text branch.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in ("true", "yes", "1", "on"):
        return True
    if text in ("false", "no", "0", "off"):
        return False
    return None


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
