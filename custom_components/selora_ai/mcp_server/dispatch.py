"""JSON-RPC dispatch and the tool-name → handler table."""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import functools
import hashlib
import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import Unauthorized

from ..const import (
    DOMAIN,
)
from ..selora_auth import SeloraAuthContext
from .access import _can_access_tool, _check_tool_access
from .apps import (
    _tool_get_app_logs,
    _tool_get_app_options,
    _tool_install_app,
    _tool_list_apps,
    _tool_search_app_store,
    _tool_set_app_options,
)
from .automations import (
    _tool_accept_automation,
    _tool_create_automation,
    _tool_delete_automation,
    _tool_get_automation,
    _tool_list_automations,
    _tool_rename_automation,
    _tool_trigger_automation,
    _tool_validate_automation,
)
from .backups import _tool_get_backups
from .blueprints_write import _tool_delete_blueprint, _tool_import_blueprint
from .calendars import (
    _tool_delete_calendar_event,
    _tool_list_calendar_events,
    _tool_set_calendar_event,
)
from .cameras import _tool_get_camera_image
from .chat import (
    _tool_accept_suggestion,
    _tool_chat,
    _tool_dismiss_suggestion,
    _tool_get_pattern,
    _tool_list_patterns,
    _tool_list_sessions,
    _tool_list_suggestions,
    _tool_trigger_scan,
)
from .commands import _tool_mcp_execute_command, _tool_mcp_validate_action
from .dashboards import (
    _tool_add_dashboard_resource,
    _tool_add_dashboard_view,
    _tool_create_dashboard,
    _tool_delete_dashboard,
    _tool_get_config_yaml,
    _tool_get_dashboard,
    _tool_get_dashboard_card,
    _tool_group_dashboard_cards,
    _tool_insert_dashboard_card,
    _tool_list_dashboard_resources,
    _tool_list_dashboards,
    _tool_move_dashboard_card,
    _tool_remove_dashboard_card,
    _tool_remove_dashboard_resource,
    _tool_remove_dashboard_view,
    _tool_set_config_yaml,
    _tool_set_dashboard_strategy,
    _tool_update_dashboard,
    _tool_update_dashboard_card,
    _tool_update_dashboard_resource,
    _tool_update_dashboard_view,
)
from .definitions import _TOOL_DEFINITIONS, _dashboard_write_tools
from .energy import _tool_get_energy_prefs, _tool_set_energy_prefs
from .entities import (
    _tool_eval_template,
    _tool_find_entities_by_area,
    _tool_get_device,
    _tool_get_device_triggers,
    _tool_get_entity_history,
    _tool_get_entity_state,
    _tool_get_home_snapshot,
    _tool_home_analytics,
    _tool_list_devices,
    _tool_search_entities,
)
from .files import (
    _tool_delete_file,
    _tool_list_files,
    _tool_read_file,
    _tool_write_file,
)
from .groups import _tool_create_group, _tool_delete_group, _tool_list_groups, _tool_update_group
from .hacs import (
    _tool_hacs_add_repository,
    _tool_hacs_info,
    _tool_hacs_install,
    _tool_hacs_remove,
    _tool_hacs_search,
)
from .integrations import (
    _tool_check_system,
    _tool_fire_event,
    _tool_get_diagnostics,
    _tool_get_network_health,
    _tool_get_system_health,
    _tool_list_integrations,
    _tool_reload_integration,
    _tool_remove_integration,
    _tool_restart_home_assistant,
    _tool_set_integration_enabled,
    _tool_set_integration_options,
)
from .names import (
    TOOL_ACCEPT_AUTOMATION,
    TOOL_ACCEPT_SUGGESTION,
    TOOL_ACTIVATE_SCENE,
    TOOL_ADD_DASHBOARD_RESOURCE,
    TOOL_ADD_DASHBOARD_VIEW,
    TOOL_ASSIGN_AREA,
    TOOL_ASSIGN_CATEGORY,
    TOOL_ASSIGN_LABELS,
    TOOL_CHAT,
    TOOL_CHECK_SYSTEM,
    TOOL_CREATE_AREA,
    TOOL_CREATE_AUTOMATION,
    TOOL_CREATE_CATEGORY,
    TOOL_CREATE_DASHBOARD,
    TOOL_CREATE_FLOOR,
    TOOL_CREATE_GROUP,
    TOOL_CREATE_HELPER,
    TOOL_CREATE_LABEL,
    TOOL_CREATE_SCENE,
    TOOL_DELETE_AREA,
    TOOL_DELETE_ASSIST_PIPELINE,
    TOOL_DELETE_AUTOMATION,
    TOOL_DELETE_BLUEPRINT,
    TOOL_DELETE_CALENDAR_EVENT,
    TOOL_DELETE_CATEGORY,
    TOOL_DELETE_DASHBOARD,
    TOOL_DELETE_FILE,
    TOOL_DELETE_FLOOR,
    TOOL_DELETE_GROUP,
    TOOL_DELETE_HELPER,
    TOOL_DELETE_LABEL,
    TOOL_DELETE_SCENE,
    TOOL_DELETE_SCRIPT,
    TOOL_DISMISS_SUGGESTION,
    TOOL_EVAL_TEMPLATE,
    TOOL_EXECUTE_COMMAND,
    TOOL_FIND_ENTITIES_BY_AREA,
    TOOL_FIND_REFERENCES,
    TOOL_FIRE_EVENT,
    TOOL_FIX_REPAIR,
    TOOL_GET_APP_LOGS,
    TOOL_GET_APP_OPTIONS,
    TOOL_GET_AUTOMATION,
    TOOL_GET_AUTOMATION_TRACES,
    TOOL_GET_BACKUPS,
    TOOL_GET_BLUEPRINT,
    TOOL_GET_CAMERA_IMAGE,
    TOOL_GET_CONFIG_YAML,
    TOOL_GET_DASHBOARD,
    TOOL_GET_DASHBOARD_CARD,
    TOOL_GET_DEVICE,
    TOOL_GET_DEVICE_TRIGGERS,
    TOOL_GET_DIAGNOSTICS,
    TOOL_GET_ENERGY_PREFS,
    TOOL_GET_ENTITY_HISTORY,
    TOOL_GET_ENTITY_STATE,
    TOOL_GET_HOME_SNAPSHOT,
    TOOL_GET_LOGS,
    TOOL_GET_NETWORK_HEALTH,
    TOOL_GET_PATTERN,
    TOOL_GET_RELEASE_NOTES,
    TOOL_GET_SCENE,
    TOOL_GET_SCRIPT,
    TOOL_GET_SYSTEM_HEALTH,
    TOOL_GROUP_DASHBOARD_CARDS,
    TOOL_HACS_ADD_REPOSITORY,
    TOOL_HACS_INFO,
    TOOL_HACS_INSTALL,
    TOOL_HACS_REMOVE,
    TOOL_HACS_SEARCH,
    TOOL_HOME_ANALYTICS,
    TOOL_IGNORE_REPAIR,
    TOOL_IMPORT_BLUEPRINT,
    TOOL_INSERT_DASHBOARD_CARD,
    TOOL_INSTALL_APP,
    TOOL_LIST_APPS,
    TOOL_LIST_AREAS,
    TOOL_LIST_ASSIST_PIPELINES,
    TOOL_LIST_AUTOMATIONS,
    TOOL_LIST_BLUEPRINTS,
    TOOL_LIST_CALENDAR_EVENTS,
    TOOL_LIST_CATEGORIES,
    TOOL_LIST_DASHBOARD_RESOURCES,
    TOOL_LIST_DASHBOARDS,
    TOOL_LIST_DEVICES,
    TOOL_LIST_FILES,
    TOOL_LIST_FLOORS,
    TOOL_LIST_GROUPS,
    TOOL_LIST_HELPERS,
    TOOL_LIST_INTEGRATIONS,
    TOOL_LIST_LABELS,
    TOOL_LIST_PATTERNS,
    TOOL_LIST_REPAIRS,
    TOOL_LIST_SCENES,
    TOOL_LIST_SCRIPTS,
    TOOL_LIST_SERVICES,
    TOOL_LIST_SESSIONS,
    TOOL_LIST_SUGGESTIONS,
    TOOL_LIST_UPDATES,
    TOOL_MOVE_DASHBOARD_CARD,
    TOOL_READ_FILE,
    TOOL_RELOAD_INTEGRATION,
    TOOL_REMOVE_DASHBOARD_CARD,
    TOOL_REMOVE_DASHBOARD_RESOURCE,
    TOOL_REMOVE_DASHBOARD_VIEW,
    TOOL_REMOVE_DEVICE,
    TOOL_REMOVE_ENTITY,
    TOOL_REMOVE_INTEGRATION,
    TOOL_RENAME_AUTOMATION,
    TOOL_RESTART_HOME_ASSISTANT,
    TOOL_SEARCH_APP_STORE,
    TOOL_SEARCH_ENTITIES,
    TOOL_SET_APP_OPTIONS,
    TOOL_SET_ASSIST_PIPELINE,
    TOOL_SET_CALENDAR_EVENT,
    TOOL_SET_CONFIG_YAML,
    TOOL_SET_DASHBOARD_STRATEGY,
    TOOL_SET_ENERGY_PREFS,
    TOOL_SET_INTEGRATION_ENABLED,
    TOOL_SET_INTEGRATION_OPTIONS,
    TOOL_SET_SCRIPT,
    TOOL_TRIGGER_AUTOMATION,
    TOOL_TRIGGER_SCAN,
    TOOL_UPDATE_AREA,
    TOOL_UPDATE_DASHBOARD,
    TOOL_UPDATE_DASHBOARD_CARD,
    TOOL_UPDATE_DASHBOARD_RESOURCE,
    TOOL_UPDATE_DASHBOARD_VIEW,
    TOOL_UPDATE_DEVICE,
    TOOL_UPDATE_ENTITY,
    TOOL_UPDATE_FLOOR,
    TOOL_UPDATE_GROUP,
    TOOL_UPDATE_HELPER,
    TOOL_UPDATE_SCENE,
    TOOL_VALIDATE_ACTION,
    TOOL_VALIDATE_AUTOMATION,
    TOOL_VALIDATE_SCENE,
    TOOL_WRITE_FILE,
)
from .pipelines import (
    _tool_delete_assist_pipeline,
    _tool_list_assist_pipelines,
    _tool_set_assist_pipeline,
)
from .protocol import MCPImageContent, MCPTextContent, ToolImage
from .registry import (
    _tool_assign_area,
    _tool_assign_category,
    _tool_create_area,
    _tool_create_category,
    _tool_create_floor,
    _tool_delete_area,
    _tool_delete_category,
    _tool_delete_floor,
    _tool_get_blueprint,
    _tool_list_areas,
    _tool_list_blueprints,
    _tool_list_categories,
    _tool_list_floors,
    _tool_list_services,
    _tool_update_area,
    _tool_update_device,
    _tool_update_entity,
    _tool_update_floor,
)
from .removals import _tool_remove_device, _tool_remove_entity
from .repairs import _tool_fix_repair, _tool_ignore_repair, _tool_list_repairs
from .scenes import (
    _tool_activate_scene,
    _tool_create_scene,
    _tool_delete_scene,
    _tool_get_scene,
    _tool_list_scenes,
    _tool_update_scene,
    _tool_validate_scene,
)
from .scripts_helpers import (
    _tool_assign_labels,
    _tool_create_helper,
    _tool_create_label,
    _tool_delete_helper,
    _tool_delete_label,
    _tool_delete_script,
    _tool_find_references,
    _tool_get_automation_traces,
    _tool_get_logs,
    _tool_get_script,
    _tool_list_helpers,
    _tool_list_labels,
    _tool_list_scripts,
    _tool_set_script,
    _tool_update_helper,
)
from .updates import _tool_get_release_notes, _tool_list_updates

_LOGGER = logging.getLogger(__name__)


# ── JSON-RPC dispatch (stateless, no mcp dependency) ────────────────────────


_MCP_PROTOCOL_VERSION = "2025-03-26"
_MCP_SERVER_NAME = "selora-ai"


async def _async_server_version(hass: HomeAssistant) -> str:
    """The integration version, plus a hash of the tool set as build metadata.

    A client may treat an unchanged ``serverInfo.version`` as an unchanged tool
    set and keep the list it already has. The manifest version moves only on
    release, so a deploy that adds or rewords a tool would look the same. The
    hash covers every definition — name, description, schema — so touching a
    tool changes the version with nobody having to remember to bump it, and a
    change elsewhere in the code does not.
    """
    from homeassistant.loader import (  # noqa: PLC0415
        IntegrationNotFound,
        async_get_integration,
    )

    release = "0"
    with contextlib.suppress(IntegrationNotFound):
        version = (await async_get_integration(hass, DOMAIN)).version
        if version is not None:
            release = str(version)
    return f"{release}+{_tool_set_hash()}"


@functools.cache
def _tool_set_hash() -> str:
    """Content hash of every tool definition; they are fixed once imported."""
    payload = json.dumps(
        [
            {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
            for t in sorted(_TOOL_DEFINITIONS, key=lambda t: t.name)
        ],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:8]


async def _jsonrpc_dispatch(
    hass: HomeAssistant,
    method: str,
    params: dict[str, Any] | None,
    auth_ctx: SeloraAuthContext,
) -> dict[str, Any]:
    """Dispatch a JSON-RPC method and return the result payload."""
    if method == "initialize":
        return {
            "protocolVersion": _MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": _MCP_SERVER_NAME,
                "version": await _async_server_version(hass),
            },
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {
            "tools": [
                {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
                for t in _TOOL_DEFINITIONS
                if _can_access_tool(auth_ctx, t.name)
            ]
        }
    if method == "tools/call":
        tool_name = (params or {}).get("name", "")
        arguments = (params or {}).get("arguments", {})
        content = await _dispatch(hass, tool_name, arguments, auth_ctx=auth_ctx)
        return {"content": [dataclasses.asdict(c) for c in content]}
    raise ValueError(f"Unknown method: {method}")


# ── Tool dispatch ──────────────────────────────────────────────────────────────


# Tools that take only (hass) — no arguments parameter
_NO_ARGS_TOOLS = frozenset({TOOL_GET_HOME_SNAPSHOT, TOOL_LIST_SESSIONS, TOOL_TRIGGER_SCAN})


def _get_tool_handlers() -> dict[str, Any]:
    """Return the tool handler registry (lazy to avoid forward-reference issues)."""
    return {
        TOOL_LIST_AUTOMATIONS: _tool_list_automations,
        TOOL_GET_AUTOMATION: _tool_get_automation,
        TOOL_VALIDATE_AUTOMATION: _tool_validate_automation,
        TOOL_CREATE_AUTOMATION: _tool_create_automation,
        TOOL_ACCEPT_AUTOMATION: _tool_accept_automation,
        TOOL_DELETE_AUTOMATION: _tool_delete_automation,
        TOOL_RENAME_AUTOMATION: _tool_rename_automation,
        TOOL_TRIGGER_AUTOMATION: _tool_trigger_automation,
        TOOL_GET_HOME_SNAPSHOT: _tool_get_home_snapshot,
        TOOL_CHAT: _tool_chat,
        TOOL_LIST_SESSIONS: _tool_list_sessions,
        TOOL_LIST_PATTERNS: _tool_list_patterns,
        TOOL_GET_PATTERN: _tool_get_pattern,
        TOOL_LIST_SUGGESTIONS: _tool_list_suggestions,
        TOOL_ACCEPT_SUGGESTION: _tool_accept_suggestion,
        TOOL_DISMISS_SUGGESTION: _tool_dismiss_suggestion,
        TOOL_TRIGGER_SCAN: _tool_trigger_scan,
        TOOL_LIST_DEVICES: _tool_list_devices,
        TOOL_GET_DEVICE: _tool_get_device,
        TOOL_GET_DEVICE_TRIGGERS: _tool_get_device_triggers,
        TOOL_GET_ENTITY_STATE: _tool_get_entity_state,
        TOOL_FIND_ENTITIES_BY_AREA: _tool_find_entities_by_area,
        TOOL_VALIDATE_ACTION: _tool_mcp_validate_action,
        TOOL_EXECUTE_COMMAND: _tool_mcp_execute_command,
        TOOL_SEARCH_ENTITIES: _tool_search_entities,
        TOOL_GET_ENTITY_HISTORY: _tool_get_entity_history,
        TOOL_EVAL_TEMPLATE: _tool_eval_template,
        TOOL_HOME_ANALYTICS: _tool_home_analytics,
        TOOL_LIST_SCENES: _tool_list_scenes,
        TOOL_GET_SCENE: _tool_get_scene,
        TOOL_VALIDATE_SCENE: _tool_validate_scene,
        TOOL_CREATE_SCENE: _tool_create_scene,
        TOOL_UPDATE_SCENE: _tool_update_scene,
        TOOL_DELETE_SCENE: _tool_delete_scene,
        TOOL_ACTIVATE_SCENE: _tool_activate_scene,
        TOOL_LIST_GROUPS: _tool_list_groups,
        TOOL_CREATE_GROUP: _tool_create_group,
        TOOL_UPDATE_GROUP: _tool_update_group,
        TOOL_DELETE_GROUP: _tool_delete_group,
        TOOL_LIST_AREAS: _tool_list_areas,
        TOOL_ASSIGN_AREA: _tool_assign_area,
        TOOL_CREATE_AREA: _tool_create_area,
        TOOL_UPDATE_AREA: _tool_update_area,
        TOOL_DELETE_AREA: _tool_delete_area,
        TOOL_UPDATE_ENTITY: _tool_update_entity,
        TOOL_UPDATE_DEVICE: _tool_update_device,
        TOOL_LIST_SERVICES: _tool_list_services,
        TOOL_LIST_SCRIPTS: _tool_list_scripts,
        TOOL_GET_SCRIPT: _tool_get_script,
        TOOL_SET_SCRIPT: _tool_set_script,
        TOOL_DELETE_SCRIPT: _tool_delete_script,
        TOOL_LIST_LABELS: _tool_list_labels,
        TOOL_CREATE_LABEL: _tool_create_label,
        TOOL_ASSIGN_LABELS: _tool_assign_labels,
        TOOL_DELETE_LABEL: _tool_delete_label,
        TOOL_LIST_HELPERS: _tool_list_helpers,
        TOOL_CREATE_HELPER: _tool_create_helper,
        TOOL_UPDATE_HELPER: _tool_update_helper,
        TOOL_DELETE_HELPER: _tool_delete_helper,
        TOOL_GET_LOGS: _tool_get_logs,
        TOOL_GET_AUTOMATION_TRACES: _tool_get_automation_traces,
        TOOL_FIND_REFERENCES: _tool_find_references,
        TOOL_LIST_BLUEPRINTS: _tool_list_blueprints,
        TOOL_GET_BLUEPRINT: _tool_get_blueprint,
        TOOL_LIST_CATEGORIES: _tool_list_categories,
        TOOL_CREATE_CATEGORY: _tool_create_category,
        TOOL_ASSIGN_CATEGORY: _tool_assign_category,
        TOOL_DELETE_CATEGORY: _tool_delete_category,
        TOOL_LIST_FLOORS: _tool_list_floors,
        TOOL_CREATE_FLOOR: _tool_create_floor,
        TOOL_UPDATE_FLOOR: _tool_update_floor,
        TOOL_DELETE_FLOOR: _tool_delete_floor,
        TOOL_LIST_DASHBOARDS: _tool_list_dashboards,
        TOOL_INSERT_DASHBOARD_CARD: _tool_insert_dashboard_card,
        TOOL_GET_DASHBOARD: _tool_get_dashboard,
        TOOL_GET_DASHBOARD_CARD: _tool_get_dashboard_card,
        TOOL_ADD_DASHBOARD_VIEW: _tool_add_dashboard_view,
        TOOL_UPDATE_DASHBOARD_VIEW: _tool_update_dashboard_view,
        TOOL_REMOVE_DASHBOARD_VIEW: _tool_remove_dashboard_view,
        TOOL_UPDATE_DASHBOARD_CARD: _tool_update_dashboard_card,
        TOOL_REMOVE_DASHBOARD_CARD: _tool_remove_dashboard_card,
        TOOL_MOVE_DASHBOARD_CARD: _tool_move_dashboard_card,
        TOOL_GROUP_DASHBOARD_CARDS: _tool_group_dashboard_cards,
        TOOL_CREATE_DASHBOARD: _tool_create_dashboard,
        TOOL_DELETE_DASHBOARD: _tool_delete_dashboard,
        TOOL_UPDATE_DASHBOARD: _tool_update_dashboard,
        TOOL_GET_CONFIG_YAML: _tool_get_config_yaml,
        TOOL_SET_CONFIG_YAML: _tool_set_config_yaml,
        TOOL_LIST_FILES: _tool_list_files,
        TOOL_READ_FILE: _tool_read_file,
        TOOL_WRITE_FILE: _tool_write_file,
        TOOL_DELETE_FILE: _tool_delete_file,
        TOOL_HACS_SEARCH: _tool_hacs_search,
        TOOL_HACS_INFO: _tool_hacs_info,
        TOOL_GET_CAMERA_IMAGE: _tool_get_camera_image,
        TOOL_LIST_CALENDAR_EVENTS: _tool_list_calendar_events,
        TOOL_SET_CALENDAR_EVENT: _tool_set_calendar_event,
        TOOL_DELETE_CALENDAR_EVENT: _tool_delete_calendar_event,
        TOOL_GET_ENERGY_PREFS: _tool_get_energy_prefs,
        TOOL_SET_ENERGY_PREFS: _tool_set_energy_prefs,
        TOOL_LIST_ASSIST_PIPELINES: _tool_list_assist_pipelines,
        TOOL_SET_ASSIST_PIPELINE: _tool_set_assist_pipeline,
        TOOL_DELETE_ASSIST_PIPELINE: _tool_delete_assist_pipeline,
        TOOL_LIST_INTEGRATIONS: _tool_list_integrations,
        TOOL_RELOAD_INTEGRATION: _tool_reload_integration,
        TOOL_SET_INTEGRATION_ENABLED: _tool_set_integration_enabled,
        TOOL_REMOVE_INTEGRATION: _tool_remove_integration,
        TOOL_SET_INTEGRATION_OPTIONS: _tool_set_integration_options,
        TOOL_REMOVE_DEVICE: _tool_remove_device,
        TOOL_REMOVE_ENTITY: _tool_remove_entity,
        TOOL_LIST_UPDATES: _tool_list_updates,
        TOOL_GET_RELEASE_NOTES: _tool_get_release_notes,
        TOOL_GET_BACKUPS: _tool_get_backups,
        TOOL_LIST_REPAIRS: _tool_list_repairs,
        TOOL_IGNORE_REPAIR: _tool_ignore_repair,
        TOOL_FIX_REPAIR: _tool_fix_repair,
        TOOL_LIST_APPS: _tool_list_apps,
        TOOL_GET_APP_LOGS: _tool_get_app_logs,
        TOOL_SEARCH_APP_STORE: _tool_search_app_store,
        TOOL_INSTALL_APP: _tool_install_app,
        TOOL_GET_APP_OPTIONS: _tool_get_app_options,
        TOOL_SET_APP_OPTIONS: _tool_set_app_options,
        TOOL_IMPORT_BLUEPRINT: _tool_import_blueprint,
        TOOL_DELETE_BLUEPRINT: _tool_delete_blueprint,
        TOOL_CHECK_SYSTEM: _tool_check_system,
        TOOL_FIRE_EVENT: _tool_fire_event,
        TOOL_GET_SYSTEM_HEALTH: _tool_get_system_health,
        TOOL_GET_DIAGNOSTICS: _tool_get_diagnostics,
        TOOL_GET_NETWORK_HEALTH: _tool_get_network_health,
        TOOL_RESTART_HOME_ASSISTANT: _tool_restart_home_assistant,
        TOOL_HACS_INSTALL: _tool_hacs_install,
        TOOL_HACS_REMOVE: _tool_hacs_remove,
        TOOL_HACS_ADD_REPOSITORY: _tool_hacs_add_repository,
        TOOL_LIST_DASHBOARD_RESOURCES: _tool_list_dashboard_resources,
        TOOL_ADD_DASHBOARD_RESOURCE: _tool_add_dashboard_resource,
        TOOL_REMOVE_DASHBOARD_RESOURCE: _tool_remove_dashboard_resource,
        TOOL_UPDATE_DASHBOARD_RESOURCE: _tool_update_dashboard_resource,
        TOOL_SET_DASHBOARD_STRATEGY: _tool_set_dashboard_strategy,
    }


async def _dispatch(
    hass: HomeAssistant,
    name: str,
    arguments: dict[str, Any],
    *,
    auth_ctx: SeloraAuthContext,
) -> list[MCPTextContent | MCPImageContent]:
    """Route a tool call to its handler and return its MCP content blocks."""
    result: dict[str, Any] | list[dict[str, Any]] | ToolImage
    try:
        _check_tool_access(auth_ctx, name)

        from ..helpers import caller_scope  # noqa: PLC0415

        handler = _get_tool_handlers().get(name)
        # _check_tool_access has already gated the tool itself; this carries the
        # caller's identity INTO the handler, for the per-object checks a tool
        # allowlist cannot express — a dashboard's own require_admin flag.
        # Asked of the same function that gates the call, across EVERY dashboard
        # mutation: a custom token with an explicit allowlist, or a JWT carrying
        # the write scope, may write without being an HA admin, and reporting
        # those dashboards read-only contradicted calls that then succeeded. Any
        # one of them is enough — an allowlist naming only `insert` authorises a
        # real editing workflow, and testing a single representative tool called
        # that credential read-only.
        with caller_scope(
            auth_ctx.is_admin,
            can_write=any(_can_access_tool(auth_ctx, name) for name in _dashboard_write_tools()),
        ):
            if handler is None:
                result = {"error": f"Unknown tool: {name}"}
            elif name in _NO_ARGS_TOOLS:
                result = await handler(hass)
            else:
                result = await handler(hass, arguments)
    except Unauthorized as exc:
        result = {"error": str(exc)}
    except Exception:
        _LOGGER.exception("Tool %s raised an exception", name)
        result = {"error": "Tool execution failed"}

    if isinstance(result, ToolImage):
        return [
            MCPTextContent(type="text", text=json.dumps(result.fields, ensure_ascii=False)),
            MCPImageContent(
                data=base64.b64encode(result.data).decode("ascii"), mimeType=result.mime_type
            ),
        ]
    return [MCPTextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
