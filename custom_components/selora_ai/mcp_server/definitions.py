"""MCP tool definitions: the schemas tools/list returns."""

from __future__ import annotations

import logging
from typing import Any

from ..group_manager import (
    SENSOR_STATISTICS as _SENSOR_STATISTIC_ENUM,
)
from ..group_manager import (
    SUPPORTED_GROUP_TYPES as _GROUP_TYPE_ENUM,
)
from .access import _ADMIN_TOOLS
from .entities import _HISTORY_MAX_CHANGES, _HISTORY_MAX_HOURS, _TEMPLATE_MAX_CHARS
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
    TOOL_DELETE_AUTOMATION,
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
    TOOL_GET_AUTOMATION,
    TOOL_GET_AUTOMATION_TRACES,
    TOOL_GET_BLUEPRINT,
    TOOL_GET_CAMERA_IMAGE,
    TOOL_GET_CONFIG_YAML,
    TOOL_GET_DASHBOARD,
    TOOL_GET_DASHBOARD_CARD,
    TOOL_GET_DEVICE,
    TOOL_GET_DEVICE_TRIGGERS,
    TOOL_GET_ENTITY_HISTORY,
    TOOL_GET_ENTITY_STATE,
    TOOL_GET_HOME_SNAPSHOT,
    TOOL_GET_LOGS,
    TOOL_GET_PATTERN,
    TOOL_GET_SCENE,
    TOOL_GET_SCRIPT,
    TOOL_GROUP_DASHBOARD_CARDS,
    TOOL_HACS_ADD_REPOSITORY,
    TOOL_HACS_INFO,
    TOOL_HACS_INSTALL,
    TOOL_HACS_REMOVE,
    TOOL_HACS_SEARCH,
    TOOL_HOME_ANALYTICS,
    TOOL_INSERT_DASHBOARD_CARD,
    TOOL_LIST_AREAS,
    TOOL_LIST_AUTOMATIONS,
    TOOL_LIST_BLUEPRINTS,
    TOOL_LIST_CATEGORIES,
    TOOL_LIST_DASHBOARD_RESOURCES,
    TOOL_LIST_DASHBOARDS,
    TOOL_LIST_DEVICES,
    TOOL_LIST_FILES,
    TOOL_LIST_FLOORS,
    TOOL_LIST_GROUPS,
    TOOL_LIST_HELPERS,
    TOOL_LIST_LABELS,
    TOOL_LIST_PATTERNS,
    TOOL_LIST_SCENES,
    TOOL_LIST_SCRIPTS,
    TOOL_LIST_SERVICES,
    TOOL_LIST_SESSIONS,
    TOOL_LIST_SUGGESTIONS,
    TOOL_MOVE_DASHBOARD_CARD,
    TOOL_READ_FILE,
    TOOL_REMOVE_DASHBOARD_CARD,
    TOOL_REMOVE_DASHBOARD_RESOURCE,
    TOOL_REMOVE_DASHBOARD_VIEW,
    TOOL_SEARCH_ENTITIES,
    TOOL_SET_CONFIG_YAML,
    TOOL_SET_SCRIPT,
    TOOL_TRIGGER_AUTOMATION,
    TOOL_TRIGGER_SCAN,
    TOOL_UPDATE_AREA,
    TOOL_UPDATE_DASHBOARD,
    TOOL_UPDATE_DASHBOARD_CARD,
    TOOL_UPDATE_DASHBOARD_VIEW,
    TOOL_UPDATE_DEVICE,
    TOOL_UPDATE_ENTITY,
    TOOL_UPDATE_FLOOR,
    TOOL_UPDATE_GROUP,
    TOOL_UPDATE_HELPER,
    TOOL_VALIDATE_ACTION,
    TOOL_VALIDATE_AUTOMATION,
    TOOL_VALIDATE_SCENE,
    TOOL_WRITE_FILE,
)
from .protocol import MCPTool

_LOGGER = logging.getLogger(__name__)


# ── Tool definitions (MCP schema) ─────────────────────────────────────────────


_TOOL_DEFINITIONS: list[MCPTool] = [
    MCPTool(
        name=TOOL_LIST_AUTOMATIONS,
        description=(
            "List all Home Assistant automations (yaml + storage-managed) with their "
            "entity_id, status, and source. Each entry includes a 'selora_managed' flag; "
            "Selora-managed entries also carry version metadata and a risk assessment. "
            "Pass selora_only=true to return only Selora-managed automations."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "enabled", "disabled"],
                    "description": "Filter by automation status. Omit to return all.",
                },
                "selora_only": {
                    "type": "boolean",
                    "default": False,
                    "description": "When true, return only Selora-managed automations.",
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_GET_AUTOMATION,
        description=(
            "Return full detail for any HA automation. Pass automation_id (the id from "
            "state.attributes.id) or entity_id (e.g. 'automation.morning_routine'). "
            "YAML, version history, and risk assessment are populated for yaml-managed "
            "Selora automations; for others those fields are empty/null."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "automation_id": {
                    "type": "string",
                    "description": "Automation id (from state.attributes.id).",
                },
                "entity_id": {
                    "type": "string",
                    "description": "Entity id (e.g. 'automation.morning_routine').",
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_VALIDATE_AUTOMATION,
        description=(
            "Validate and risk-assess a YAML string representing a Home Assistant automation "
            "WITHOUT creating or modifying anything. Use this to check externally-generated "
            "YAML before committing. Returns validation errors and a risk assessment."
        ),
        inputSchema={
            "type": "object",
            "required": ["yaml"],
            "properties": {
                "yaml": {
                    "type": "string",
                    "description": "Raw YAML string for a Home Assistant automation.",
                }
            },
        },
    ),
    MCPTool(
        name=TOOL_CREATE_AUTOMATION,
        description=(
            "Create a new Home Assistant automation from a YAML string, or REPLACE an "
            "existing one by passing automation_id — any automation saved in "
            "automations.yaml, whoever created it. A replacement is the WHOLE "
            "automation: read it with selora_get_automation first and send it back "
            "with your changes. One Selora did not create is validated by Home "
            "Assistant and written exactly as given. "
            "Server-side validation and risk assessment run unconditionally. "
            "New automations are created DISABLED by default — set enabled=true to "
            "override; a replacement keeps the automation's current enabled state, "
            "unless the revision raises its risk to elevated, in which case it is "
            "disabled for review and the result says so (forced_disabled). "
            "Pass the automation_id returned by selora_chat's refine_automation_id (or "
            "by an earlier create) when the YAML is a revision of an automation that "
            "already exists — creating it again writes a second automation under the "
            "same name, which Home Assistant will load and run alongside the first. "
            "Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "required": ["yaml"],
            "properties": {
                "yaml": {"type": "string", "description": "Raw YAML for the automation."},
                "automation_id": {
                    "type": "string",
                    "description": (
                        "Replace this automation (any one in automations.yaml) instead "
                        "of creating a new one. Omit to create."
                    ),
                },
                "enabled": {
                    "type": "boolean",
                    "default": False,
                    "description": "Whether to enable the automation immediately. Defaults to false.",
                },
                "version_message": {
                    "type": "string",
                    "description": (
                        "Optional note recorded in the version history (Selora's automations only)."
                    ),
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_ACCEPT_AUTOMATION,
        description=(
            "Enable or disable any HA automation. For yaml-managed automations, "
            "persists initial_state across restarts (and creates a version record for "
            "Selora-managed ones). For storage-managed automations, calls "
            "automation.turn_on/off. Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "automation_id": {"type": "string"},
                "entity_id": {"type": "string"},
                "enabled": {
                    "type": "boolean",
                    "default": False,
                    "description": "Set true to enable, false to disable.",
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_DELETE_AUTOMATION,
        description=(
            "Delete any yaml-managed HA automation permanently. Storage-managed "
            "(UI/integration) automations cannot be removed through this tool. "
            "Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "automation_id": {"type": "string"},
                "entity_id": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name=TOOL_TRIGGER_AUTOMATION,
        description=(
            "Trigger any HA automation immediately via the automation.trigger service. "
            "By default the automation's conditions are skipped; pass skip_condition=false "
            "to honour them. Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "automation_id": {"type": "string"},
                "entity_id": {"type": "string"},
                "skip_condition": {
                    "type": "boolean",
                    "default": True,
                    "description": "When true (default), bypass the automation's condition block.",
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_GET_HOME_SNAPSHOT,
        description=(
            "Return current Home Assistant entity states grouped by area. "
            "Call this first to understand what entities and areas exist before "
            "generating or requesting any automation."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    MCPTool(
        name=TOOL_CHAT,
        description=(
            "Send a natural-language message to Selora's internal LLM in the context of "
            "the current home state. Returns a response and, where applicable, a proposed "
            "automation with YAML and risk assessment. "
            "Pass session_id to continue an existing conversation. "
            "Pass refine_automation_id — an id from selora_list_automations or an earlier "
            "selora_create_automation — to revise an automation that already exists: the "
            "model is given its current YAML and edits that instead of composing a new "
            "rule. When the returned YAML revises an existing automation, the response "
            "carries refine_automation_id; pass it to selora_create_automation's "
            "automation_id to replace that automation rather than adding a second one "
            "under the same name. "
            "This is the primary Coroutine Synthesis suspension point: the external agent "
            "yields here and Selora advances the automation artifact using home-grounded "
            "generation. Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "required": ["message"],
            "properties": {
                "message": {"type": "string"},
                "session_id": {
                    "type": "string",
                    "description": "Continue an existing session. Omit to start a new one.",
                },
                "refine_automation_id": {
                    "type": "string",
                    "description": (
                        "Revise this existing automation instead of composing a new one. "
                        "Refused when no automation carries that id."
                    ),
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_LIST_SESSIONS,
        description="Return recent Selora chat sessions (title, id, timestamp). No messages included.",
        inputSchema={"type": "object", "properties": {}},
    ),
    MCPTool(
        name=TOOL_LIST_PATTERNS,
        description=(
            "List detected behavior patterns derived from Selora suggestions. "
            "Supports filtering by type, confidence, and status."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["time_based", "correlation", "sequence"],
                },
                "min_confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "dismissed", "snoozed", "accepted"],
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_GET_PATTERN,
        description="Return full detail for one pattern, including linked suggestions.",
        inputSchema={
            "type": "object",
            "required": ["pattern_id"],
            "properties": {"pattern_id": {"type": "string"}},
        },
    ),
    MCPTool(
        name=TOOL_LIST_SUGGESTIONS,
        description=(
            "List proactive automation suggestions with YAML previews and risk assessment. "
            "Supports status filtering."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "accepted", "dismissed", "snoozed"],
                }
            },
        },
    ),
    MCPTool(
        name=TOOL_ACCEPT_SUGGESTION,
        description=(
            "Create an automation from a pending suggestion and mark it accepted. "
            "Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "required": ["suggestion_id"],
            "properties": {
                "suggestion_id": {"type": "string"},
                "enabled": {
                    "type": "boolean",
                    "default": False,
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_DISMISS_SUGGESTION,
        description=(
            "Mark a suggestion as dismissed. Optionally include a reason. Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "required": ["suggestion_id"],
            "properties": {
                "suggestion_id": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name=TOOL_TRIGGER_SCAN,
        description=(
            "Trigger an immediate suggestion scan. Rate-limited to 60 seconds and returns "
            "cached metadata when called too frequently. Requires admin access."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    # ── Phase 2: Device data ──
    MCPTool(
        name=TOOL_LIST_DEVICES,
        description=(
            "List Home Assistant devices tracked by Selora AI with their area, manufacturer, "
            "model, integration, and entity IDs. Supports optional area and domain filters."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "area": {
                    "type": "string",
                    "description": "Filter by area name (case-insensitive substring match).",
                },
                "domain": {
                    "type": "string",
                    "description": ("Filter by entity domain (e.g. light, climate, lock)."),
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_GET_DEVICE,
        description=(
            "Return full detail for a single Home Assistant device: metadata, "
            "all associated entities, and their current states and key attributes."
        ),
        inputSchema={
            "type": "object",
            "required": ["device_id"],
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "The HA device registry ID.",
                }
            },
        },
    ),
    MCPTool(
        name=TOOL_GET_ENTITY_STATE,
        description=(
            "Return current state and key attributes for a single Home Assistant entity. "
            "Use this for targeted state questions ('is the kitchen light on?', 'what's the "
            "thermostat set to?') instead of pulling the full home snapshot. "
            "Requires the entity_id (e.g. 'light.kitchen')."
        ),
        inputSchema={
            "type": "object",
            "required": ["entity_id"],
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "Full entity_id (e.g. 'light.kitchen').",
                }
            },
        },
    ),
    MCPTool(
        name=TOOL_FIND_ENTITIES_BY_AREA,
        description=(
            "Return entities located in a given area, optionally filtered by domain. "
            "Entity area is resolved via the entity registry first, then via its device. "
            "Use this to pick the right entity_id before issuing a command (e.g. 'find "
            "lights in the kitchen'). Area is a case-insensitive substring match."
        ),
        inputSchema={
            "type": "object",
            "required": ["area"],
            "properties": {
                "area": {
                    "type": "string",
                    "description": "Area name (case-insensitive substring match).",
                },
                "domain": {
                    "type": "string",
                    "description": "Optional domain filter (e.g. 'light', 'climate').",
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_VALIDATE_ACTION,
        description=(
            "Check a Home Assistant service call WITHOUT running it: whether the "
            "service and entities exist, its risk level, and whether "
            "selora_execute_command would ask for confirmation first. Gives exactly "
            "the verdict selora_execute_command acts on."
        ),
        inputSchema={
            "type": "object",
            "required": ["service"],
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Service in '<domain>.<verb>' form (e.g. 'light.turn_on').",
                },
                "entity_id": {
                    "description": (
                        "Target entity_id (string or list of strings). Omit for a "
                        "service that targets no entity (notify, script)."
                    ),
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                },
                "data": {
                    "type": "object",
                    "description": "Optional service data payload (e.g. {'brightness_pct': 80}).",
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_EXECUTE_COMMAND,
        description=(
            "Call any Home Assistant service — lights, locks, scripts, notifications, "
            "to-do lists, buttons, number and select entities, and the rest. A "
            "low-risk call runs at once. A riskier one (a lock, the alarm, a garage "
            "door, a script, a service Selora does not know) comes back with "
            "requires_confirmation, its risk_level and the reason, and runs nothing: "
            "ask the user, and only once they agree call again with confirmed=true. "
            "Restarting Home Assistant, purging history and rebooting the host are "
            "refused. A service that returns data (todo.get_items, "
            "calendar.get_events, weather.get_forecasts) returns it as `response`. "
            "Returns the targets' states afterwards. Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "required": ["service"],
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Service in '<domain>.<verb>' form (e.g. 'light.turn_on').",
                },
                "entity_id": {
                    "description": (
                        "Target entity_id (string or list of strings). Omit for a "
                        "service that targets no entity (notify, script)."
                    ),
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                },
                "data": {
                    "type": "object",
                    "description": "Optional service data (e.g. {'brightness_pct': 80}).",
                },
                "confirmed": {
                    "type": "boolean",
                    "description": (
                        "Set ONLY after the user has agreed to a call that came back "
                        "with requires_confirmation."
                    ),
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_SEARCH_ENTITIES,
        description=(
            "Fuzzy-search entities by free-text query across entity_id, friendly "
            "name, registered aliases, area name, and the entity's DEVICE (name, "
            "manufacturer, model) — so a brand or model query ('IKEA', 'Aqara', "
            "'TRADFRI') resolves the entities that device owns, even though the "
            "brand appears in no entity name. Returns ranked matches (score = "
            "number of query terms found). Use this when the user names a device "
            "informally ('kitchen island light', 'master bedroom fan') and you "
            "need to resolve it to an entity_id before issuing a command. "
            "`domain` and `device_class` may each be used ALONE, with no query: "
            "domain='camera' lists every camera, device_class='battery' every "
            "battery entity — the way to find battery levels, which are "
            "diagnostic entities and so absent from the home snapshot. An empty "
            "result is a failed name lookup, not proof the device is absent."
        ),
        inputSchema={
            "type": "object",
            "required": [],
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Free-text search query (e.g. 'kitchen island light', "
                        "'IKEA'). Required unless domain or device_class is "
                        "given."
                    ),
                },
                "domain": {
                    "type": "string",
                    "description": (
                        "Optional domain filter (e.g. 'light', 'camera'). "
                        "Works with no query, to list the whole domain."
                    ),
                },
                "device_class": {
                    "type": "string",
                    "description": (
                        "Optional device-class filter (e.g. 'battery', "
                        "'temperature', 'motion'). Works with no query."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "Max results (default 10, up to 25 for a query; a "
                        "device_class-only listing defaults to all matches, up "
                        "to 50). `omitted` reports anything left out."
                    ),
                    "minimum": 1,
                    "maximum": 50,
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_GET_ENTITY_HISTORY,
        description=(
            "Return recent state changes for a single entity from the Home "
            "Assistant recorder. Use this for temporal questions ('when did the "
            "front door last open?', 'how long has the heat been on?'). "
            f"Window is bounded to {_HISTORY_MAX_HOURS}h and "
            f"{_HISTORY_MAX_CHANGES} changes."
        ),
        inputSchema={
            "type": "object",
            "required": ["entity_id"],
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "Full entity_id (e.g. 'binary_sensor.front_door').",
                },
                "hours": {
                    "type": "number",
                    "description": f"Hours of history (0.25-{_HISTORY_MAX_HOURS}, default 6).",
                    "minimum": 0.25,
                    "maximum": float(_HISTORY_MAX_HOURS),
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_EVAL_TEMPLATE,
        description=(
            "Evaluate a Home Assistant Jinja template using HA's sandbox. Use this "
            "for time math, sun position, presence checks, and arbitrary state "
            "predicates the LLM cannot derive from snapshots alone. Example: "
            "{{ states('sensor.outdoor_temp') }}, "
            "{{ as_timestamp(state_attr('sun.sun','next_setting')) }}. "
            f"Template is capped at {_TEMPLATE_MAX_CHARS} characters."
        ),
        inputSchema={
            "type": "object",
            "required": ["template"],
            "properties": {
                "template": {
                    "type": "string",
                    "description": "Jinja template string (HA sandbox).",
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_HOME_ANALYTICS,
        description=(
            "Get analytics about device usage patterns and state changes. "
            "Without entity_id returns a home-wide summary (top entities, busiest hour, totals). "
            "With entity_id returns hourly usage windows and state transition counts for that entity."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": (
                        "Optional entity ID. If provided, returns per-entity analytics. "
                        "If omitted, returns a home-wide summary."
                    ),
                }
            },
        },
    ),
    # ── Phase 3: Scenes ──
    MCPTool(
        name=TOOL_LIST_SCENES,
        description=(
            "List all Home Assistant scenes (yaml + storage-managed) with their entity_id, "
            "name, source, and a 'selora_managed' flag. Selora-managed entries also expose "
            "scene_id, lifecycle metadata, and rendered YAML. Pass selora_only=true to "
            "return only Selora-managed scenes."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "selora_only": {
                    "type": "boolean",
                    "default": False,
                    "description": "When true, return only Selora-managed scenes.",
                }
            },
        },
    ),
    MCPTool(
        name=TOOL_GET_SCENE,
        description=(
            "Return full detail for any HA scene. Pass scene_id (Selora SceneStore ID) "
            "or entity_id (e.g. 'scene.movie_night'). Selora-managed scenes carry full "
            "lifecycle metadata; others expose entity_id, name, and YAML when yaml-managed."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "scene_id": {
                    "type": "string",
                    "description": "The Selora scene ID (selora_ai_scene_<hex>).",
                },
                "entity_id": {
                    "type": "string",
                    "description": "Scene entity_id (e.g. 'scene.movie_night').",
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_VALIDATE_SCENE,
        description=(
            "Validate a scene payload (name + entities) WITHOUT creating anything. "
            "Runs the same payload validation, security checks, and name sanitization "
            "that selora_create_scene applies. Returns errors and a normalized YAML on success."
        ),
        inputSchema={
            "type": "object",
            "required": ["name", "entities"],
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Human-readable scene name (e.g. 'Movie Night').",
                },
                "entities": {
                    "type": "object",
                    "description": (
                        "Mapping of entity_id → state object. Each state object must include "
                        "a 'state' key (string, bool, or number) plus optional attributes."
                    ),
                    "additionalProperties": {
                        "type": "object",
                        "required": ["state"],
                        "properties": {"state": {}},
                    },
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_CREATE_SCENE,
        description=(
            "Create a new Selora-managed scene from a name and an entities map. "
            "Server-side validation, security checks, and name sanitization run "
            "unconditionally. The scene is written to scenes.yaml and tracked in "
            "the SceneStore. Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "required": ["name", "entities"],
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Human-readable scene name.",
                },
                "entities": {
                    "type": "object",
                    "description": (
                        "Mapping of entity_id → state object. Each state object must include "
                        "a 'state' key plus optional attributes (e.g. brightness, color_temp)."
                    ),
                    "additionalProperties": {
                        "type": "object",
                        "required": ["state"],
                        "properties": {"state": {}},
                    },
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_DELETE_SCENE,
        description=(
            "Delete any yaml-managed HA scene from scenes.yaml. Storage-managed "
            "(UI/integration) scenes cannot be removed through this tool. "
            "Selora-managed scenes are also soft-deleted in the SceneStore and "
            "purged from chat sessions. Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "scene_id": {"type": "string"},
                "entity_id": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name=TOOL_ACTIVATE_SCENE,
        description=(
            "Activate any Home Assistant scene by calling scene.turn_on. "
            "Pass entity_id (e.g. 'scene.movie_night') to target any HA scene, or "
            "scene_id to target a Selora-managed scene by its store ID. "
            "Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "Scene entity_id (must start with 'scene.'). Targets any HA scene.",
                },
                "scene_id": {
                    "type": "string",
                    "description": "Selora scene store ID. Resolved to an entity_id via the SceneStore.",
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_LIST_GROUPS,
        description=(
            "List Home Assistant group helpers with their members, live state, and "
            "config entry_id. Optionally filter by group_type. Also reports "
            "read_only_yaml_groups: group.* entities defined in YAML, which cannot be "
            "edited through this API. 'members' is capped for very large groups: "
            "'member_count' is always the true total, and 'members_omitted' says how "
            "many were left out."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "group_type": {
                    "type": "string",
                    "enum": list(_GROUP_TYPE_ENUM),
                    "description": "Only return groups of this type (e.g. 'light').",
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_CREATE_GROUP,
        description=(
            "Create a Home Assistant group helper: one entity_id that controls many "
            "devices. All members must share a domain (a group helper is per-domain); "
            "sensor/number/input_number may be combined into a numeric 'sensor' group. "
            "group_type is inferred from the members. Returns the new entity_id "
            "(e.g. 'light.evening_lights'), which is what automations should target. "
            "Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Human-readable group name (e.g. 'Evening Lights').",
                },
                "entities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Member entity_ids. Must all exist and share one domain.",
                },
                "group_type": {
                    "type": "string",
                    "enum": list(_GROUP_TYPE_ENUM),
                    "description": (
                        "Optional assertion of the group's domain. Inferred from the "
                        "members when omitted; a mismatch is an error."
                    ),
                },
                "hide_members": {
                    "type": "boolean",
                    "description": "Hide the individual members from the HA UI. Default false.",
                    "default": False,
                },
                "requires_all_members": {
                    "type": "boolean",
                    "description": (
                        "binary_sensor/light/switch only: when true the group is 'on' "
                        "only if ALL members are on, instead of any. Default false."
                    ),
                    "default": False,
                },
                "statistic": {
                    "type": "string",
                    "enum": list(_SENSOR_STATISTIC_ENUM),
                    "description": (
                        "ONLY for numeric groups whose members are sensor/number/"
                        "input_number entities: how their values combine into one "
                        "number. Omit entirely for light, switch, cover, lock, fan and "
                        "every other type — those have no numeric state. Omit to use "
                        "the mean."
                    ),
                },
            },
            "required": ["name", "entities"],
        },
    ),
    MCPTool(
        name=TOOL_UPDATE_GROUP,
        description=(
            "Change a group helper's members and/or name. Identify the group by "
            "entity_id, entry_id, or group_name. Use add_entities/remove_entities for a "
            "delta, or entities to replace the whole member list. Members cannot change "
            "domain. Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "The group's entity_id (e.g. 'light.evening_lights').",
                },
                "entry_id": {
                    "type": "string",
                    "description": "The group's config entry_id, from list_groups.",
                },
                "group_name": {
                    "type": "string",
                    "description": "The group's current name, if no id is known.",
                },
                "new_name": {
                    "type": "string",
                    "description": "Rename the group to this.",
                },
                "entities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Replace the member list entirely with these entity_ids.",
                },
                "add_entities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Add these entity_ids to the existing members.",
                },
                "remove_entities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Remove these entity_ids from the existing members.",
                },
                "requires_all_members": {
                    "type": "boolean",
                    "description": (
                        "binary_sensor/light/switch only: require ALL members to be on "
                        "for the group to read 'on'."
                    ),
                },
            },
        },
    ),
    MCPTool(
        name=TOOL_DELETE_GROUP,
        description=(
            "Delete a Home Assistant group helper, identified by entry_id, entity_id, or "
            "group_name. Members themselves are untouched, but automations targeting the "
            "group will break. Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "entry_id": {
                    "type": "string",
                    "description": "The group's config entry_id, from list_groups.",
                },
                "entity_id": {
                    "type": "string",
                    "description": "The group's entity_id (e.g. 'light.evening_lights').",
                },
                "group_name": {
                    "type": "string",
                    "description": "The group's name, if no id is known.",
                },
            },
        },
    ),
]


# These tools carry the same schema on both surfaces, so the MCP definitions are
# derived from the chat ``ToolDef``s rather than restated. A second hand-written
# copy is a schema that drifts the next time a parameter is added, and the
# failure is quiet in the worst way: the MCP client rejects an argument chat
# accepts, on a tool that looks identical in both listings.
# Populated on first use by _dashboard_write_tools(); importing TOOL_MAP at
# module scope here would be circular.
_DASHBOARD_WRITE_TOOLS: frozenset[str] | None = None

_DERIVED_MCP_TOOLS: dict[str, str] = {
    # Has had a handler since it was written but no definition, so it never
    # appeared in tools/list and no MCP client could reach it. Deriving it here
    # is the whole fix; see ``test_every_mcp_handler_is_declared``.
    TOOL_GET_DEVICE_TRIGGERS: "get_device_triggers",
    TOOL_LIST_AREAS: "list_areas",
    TOOL_ASSIGN_AREA: "assign_area",
    TOOL_CREATE_AREA: "create_area",
    TOOL_UPDATE_AREA: "update_area",
    TOOL_DELETE_AREA: "delete_area",
    TOOL_LIST_BLUEPRINTS: "list_blueprints",
    TOOL_GET_BLUEPRINT: "get_blueprint",
    TOOL_LIST_CATEGORIES: "list_categories",
    TOOL_CREATE_CATEGORY: "create_category",
    TOOL_ASSIGN_CATEGORY: "assign_category",
    TOOL_DELETE_CATEGORY: "delete_category",
    TOOL_LIST_FLOORS: "list_floors",
    TOOL_CREATE_FLOOR: "create_floor",
    TOOL_UPDATE_FLOOR: "update_floor",
    TOOL_DELETE_FLOOR: "delete_floor",
    TOOL_UPDATE_ENTITY: "update_entity",
    TOOL_UPDATE_DEVICE: "update_device",
    TOOL_LIST_SERVICES: "list_services",
    TOOL_LIST_SCRIPTS: "list_scripts",
    TOOL_GET_SCRIPT: "get_script",
    TOOL_SET_SCRIPT: "set_script",
    TOOL_DELETE_SCRIPT: "delete_script",
    TOOL_LIST_LABELS: "list_labels",
    TOOL_CREATE_LABEL: "create_label",
    TOOL_ASSIGN_LABELS: "assign_labels",
    TOOL_DELETE_LABEL: "delete_label",
    TOOL_LIST_HELPERS: "list_helpers",
    TOOL_UPDATE_HELPER: "update_helper",
    TOOL_DELETE_HELPER: "delete_helper",
    TOOL_GET_LOGS: "get_logs",
    TOOL_GET_AUTOMATION_TRACES: "get_automation_traces",
    TOOL_FIND_REFERENCES: "find_references",
    TOOL_LIST_DASHBOARDS: "list_dashboards",
    TOOL_INSERT_DASHBOARD_CARD: "insert_dashboard_card",
    TOOL_GET_DASHBOARD: "get_dashboard",
    TOOL_GET_DASHBOARD_CARD: "get_dashboard_card",
    TOOL_ADD_DASHBOARD_VIEW: "add_dashboard_view",
    TOOL_UPDATE_DASHBOARD_VIEW: "update_dashboard_view",
    TOOL_REMOVE_DASHBOARD_VIEW: "remove_dashboard_view",
    TOOL_UPDATE_DASHBOARD_CARD: "update_dashboard_card",
    TOOL_REMOVE_DASHBOARD_CARD: "remove_dashboard_card",
    TOOL_MOVE_DASHBOARD_CARD: "move_dashboard_card",
    TOOL_GROUP_DASHBOARD_CARDS: "group_dashboard_cards",
    TOOL_CREATE_DASHBOARD: "create_dashboard",
    TOOL_DELETE_DASHBOARD: "delete_dashboard",
    TOOL_UPDATE_DASHBOARD: "update_dashboard",
}


# Chat descriptions written around the panel's confirmation card — the Create
# button, "do not say it exists until the result comes back" — are false over
# MCP, where the handler acts at once. Restating that as a suffix leaves both
# halves in one schema, and a contradiction there does not resolve as the newer
# half winning, so these replace the chat text outright. Parameters are still
# derived.
_MCP_DESCRIPTIONS: dict[str, str] = {
    "create_dashboard": (
        "Create a whole new dashboard, with its own sidebar entry, immediately. "
        "THIS is the tool for 'create a dashboard' / 'make me a new dashboard' — do "
        "not add a page to an existing dashboard instead. The new dashboard has no "
        "pages: give it its first one straight away with selora_add_dashboard_view, "
        "passing the cards, rather than asking the user to. Use "
        "selora_add_dashboard_view alone for a page on a dashboard that already "
        "exists."
    ),
    "delete_dashboard": (
        "Delete a whole dashboard and everything on it. This runs IMMEDIATELY and "
        "cannot be undone, and the result names how many views and cards went with "
        "it — confirm with the user yourself before calling it. The default "
        "dashboard cannot be deleted and a YAML dashboard has to be removed from "
        "configuration.yaml. To remove one PAGE rather than the whole dashboard, use "
        "selora_remove_dashboard_view."
    ),
}


# Chat parameters with no meaning on MCP. `remaining_intent` is the resumption
# trigger: the panel carries it on the proposal and re-enters the turn once the
# user taps the card. MCP has no card and no panel, so a client that passes it
# is answered by nothing at all — an advertised parameter that silently does
# nothing is worse than an absent one, because an agent will use it and then
# wait for the continuation it was promised.
_PANEL_ONLY_PARAMS = frozenset({"remaining_intent"})


def _mcp_tool_from_chat_tool(mcp_name: str, chat_name: str) -> MCPTool:
    """Render a chat ToolDef as an MCP tool definition.

    The shared description is written for chat, where a delete or other
    irreversible write returns a preview and the user taps a confirmation card.
    MCP has no card — the handler runs the write on the spot — so a description
    promising one reads as a guarantee an agent can act on, and the view is gone
    before anyone is asked. The correction is derived from the same allowlists
    the chat previews use, so a tool added there cannot forget it.

    Panel-only parameters are dropped for the same reason: the shared definition
    is written for the surface that has a panel, and deriving it verbatim
    advertises controls the MCP handler cannot honour.
    """
    from ..llm_client.command_policy import (  # noqa: PLC0415
        _DELETE_TOOLS,
        _DESTRUCTIVE_TOOLS,
    )
    from ..tool_registry import TOOL_MAP  # noqa: PLC0415

    definition = TOOL_MAP[chat_name].to_anthropic()
    description = definition["description"]
    if chat_name in _MCP_DESCRIPTIONS:
        description = _MCP_DESCRIPTIONS[chat_name]
    elif chat_name in _DELETE_TOOLS or chat_name in _DESTRUCTIVE_TOOLS:
        description = (
            f"{description} NOTE: any confirmation card described above is the "
            f"chat surface. Over MCP this runs IMMEDIATELY and cannot be undone — "
            f"confirm with the user yourself before calling it."
        )
    if mcp_name in _ADMIN_TOOLS:
        description = f"{description} Requires admin access."
    schema = definition["input_schema"]
    properties = schema.get("properties") or {}
    if _PANEL_ONLY_PARAMS & set(properties):
        schema = {
            **schema,
            "properties": {
                name: spec for name, spec in properties.items() if name not in _PANEL_ONLY_PARAMS
            },
        }
        if required := schema.get("required"):
            schema["required"] = [n for n in required if n not in _PANEL_ONLY_PARAMS]
    return MCPTool(
        name=mcp_name,
        description=description,
        inputSchema=schema,
    )


_TOOL_DEFINITIONS.extend(
    _mcp_tool_from_chat_tool(mcp_name, chat_name)
    for mcp_name, chat_name in _DERIVED_MCP_TOOLS.items()
)


def _create_helper_definition() -> MCPTool:
    """``create_helper`` as MCP runs it — see ``TOOL_CREATE_HELPER_DIRECT``."""
    from ..tool_registry import TOOL_CREATE_HELPER_DIRECT  # noqa: PLC0415

    definition = TOOL_CREATE_HELPER_DIRECT.to_anthropic()
    return MCPTool(
        name=TOOL_CREATE_HELPER,
        description=f"{definition['description']} Requires admin access.",
        inputSchema=definition["input_schema"],
    )


_TOOL_DEFINITIONS.append(_create_helper_definition())

_CONFIG_FILE_PARAM: dict[str, Any] = {
    "type": "string",
    "description": (
        "configuration.yaml (default), a package file '<packages folder>/<name>.yaml', "
        "or a theme file 'themes/<name>.yaml'."
    ),
}

_TOOL_DEFINITIONS.extend(
    [
        MCPTool(
            name=TOOL_GET_CONFIG_YAML,
            description=(
                "Read Home Assistant's YAML configuration: a file's top-level keys "
                "(and, for configuration.yaml, the package and theme files there are), "
                "or one key's YAML with yaml_path. Credentials are masked; !secret and "
                "!include references are shown as written. Read before editing with "
                "selora_set_config_yaml. Requires admin access."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file": _CONFIG_FILE_PARAM,
                    "yaml_path": {
                        "type": "string",
                        "description": "A top-level key to show. Omit to list the keys.",
                    },
                },
            },
        ),
        MCPTool(
            name=TOOL_SET_CONFIG_YAML,
            description=(
                "Edit Home Assistant's YAML configuration — LAST RESORT, for what no "
                "other tool reaches: YAML-only integrations (rest, command_line, "
                "shell_command, notify platforms, template, mqtt, knx …), package files, "
                "and themes. Use the dedicated tools for automations, scripts, scenes, "
                "helpers and dashboards. yaml_path is one top-level key from the "
                "allowlist (automation/script/scene only in a package file), a theme's "
                "name in a theme file, or 'frontend.themes' with content "
                "'!include_dir_merge_named themes' to make the themes folder load. "
                "TWO STEPS: a call without confirm_token writes NOTHING and returns the "
                "diff and a confirm_token — show the user the diff, and once they agree "
                "repeat the exact call with the token. The file is backed up first, and "
                "an edit that makes Home Assistant's configuration check fail is rolled "
                "back. The result says whether a reload made it live or a restart is "
                "needed. Requires admin access."
            ),
            inputSchema={
                "type": "object",
                "required": ["yaml_path", "action"],
                "properties": {
                    "file": _CONFIG_FILE_PARAM,
                    "yaml_path": {
                        "type": "string",
                        "description": "The top-level key, theme name, or 'frontend.themes'.",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["add", "replace", "remove"],
                        "description": (
                            "add: create the key, or append to its list / add new "
                            "entries to its mapping. replace: overwrite the key. "
                            "remove: delete it."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "YAML for the value under yaml_path (not including the key "
                            "itself). Required for add and replace."
                        ),
                    },
                    "confirm_token": {
                        "type": "string",
                        "description": "The token from the preview, once the user agreed.",
                    },
                },
            },
        ),
    ]
)

_TOOL_DEFINITIONS.extend(
    [
        MCPTool(
            name=TOOL_LIST_DASHBOARD_RESOURCES,
            description=(
                "List the dashboard resources: the JavaScript and CSS files custom "
                "cards (custom:button-card, card-mod, mushroom …) need loaded before "
                "they render. A custom: card whose resource is missing shows 'Custom "
                "element doesn't exist'. editable false means they are defined in YAML."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        MCPTool(
            name=TOOL_ADD_DASHBOARD_RESOURCE,
            description=(
                "Register a dashboard resource so a custom card can load. A path on "
                "this Home Assistant (a HACS card at /hacsfiles/<card>/<card>.js, or a "
                "file in /local/) is added at once. An external https:// URL loads "
                "third-party code into every user's browser, so it comes back with "
                "requires_confirmation and adds nothing: ask the user, and only once "
                "they agree call again with confirmed=true. The page must be reloaded "
                "for a new resource to load. Requires admin access."
            ),
            inputSchema={
                "type": "object",
                "required": ["url"],
                "properties": {
                    "url": {
                        "type": "string",
                        "description": (
                            "'/hacsfiles/button-card/button-card.js', '/local/x.js', "
                            "or an https:// URL."
                        ),
                    },
                    "type": {
                        "type": "string",
                        "enum": ["module", "js", "css"],
                        "description": "module (default, for nearly every card), js or css.",
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": (
                            "Set ONLY after the user agreed to an external URL that "
                            "came back with requires_confirmation."
                        ),
                    },
                },
            },
        ),
        MCPTool(
            name=TOOL_REMOVE_DASHBOARD_RESOURCE,
            description=(
                "Unregister a dashboard resource. Every card that uses it stops "
                "rendering. Runs IMMEDIATELY — confirm with the user first. Resources "
                "a recipe installed are removed by removing the recipe. Requires "
                "admin access."
            ),
            inputSchema={
                "type": "object",
                "required": ["resource"],
                "properties": {
                    "resource": {
                        "type": "string",
                        "description": "The resource's id or URL, from list_dashboard_resources.",
                    },
                },
            },
        ),
    ]
)


def mcp_tool_catalog() -> list[dict[str, Any]]:
    """Every MCP tool a token can be granted, with whether it needs admin.

    The settings panel builds its custom-token picker from this. It carried
    its own list once, and every tool added after it could not be granted
    to a custom token at all.
    """
    return [{"name": tool.name, "admin": tool.name in _ADMIN_TOOLS} for tool in _TOOL_DEFINITIONS]


def _dashboard_write_tools() -> frozenset[str]:
    """Every MCP tool that mutates a dashboard.

    Derived rather than listed, so a mutation added to the family is covered by
    the editability report without anyone remembering to name it here — the
    failure otherwise is quiet, a credential told it cannot edit while its calls
    succeed.
    """
    from ..tool_registry import TOOL_MAP  # noqa: PLC0415

    global _DASHBOARD_WRITE_TOOLS
    if _DASHBOARD_WRITE_TOOLS is None:
        _DASHBOARD_WRITE_TOOLS = frozenset(
            mcp_name
            for mcp_name, chat_name in _DERIVED_MCP_TOOLS.items()
            if "dashboard" in chat_name and TOOL_MAP[chat_name].requires_admin
        )
    return _DASHBOARD_WRITE_TOOLS


_FILE_PARAM: dict[str, Any] = {
    "type": "string",
    "description": (
        "A path under www/, themes/, custom_templates/ or dashboards/ (and blueprints/ "
        "for reading), e.g. 'www/pool/background.svg'. Files in www/ are served at "
        "/local/<the rest>."
    ),
}

_TOOL_DEFINITIONS.extend(
    [
        MCPTool(
            name=TOOL_LIST_FILES,
            description=(
                "List one folder's files and subfolders under www/, themes/, "
                "custom_templates/, dashboards/ or blueprints/. Requires admin access."
            ),
            inputSchema={
                "type": "object",
                "required": ["folder"],
                "properties": {
                    "folder": {"type": "string", "description": "e.g. 'www' or 'www/pool'."},
                    "pattern": {"type": "string", "description": "Optional glob, e.g. '*.js'."},
                },
            },
        ),
        MCPTool(
            name=TOOL_READ_FILE,
            description=(
                "Read a text file under www/, themes/, custom_templates/, dashboards/ or "
                "blueprints/. Long files come in chunks: pass next_offset back as offset. "
                "configuration.yaml and packages are read with selora_get_config_yaml. "
                "Requires admin access."
            ),
            inputSchema={
                "type": "object",
                "required": ["file"],
                "properties": {
                    "file": _FILE_PARAM,
                    "offset": {
                        "type": "integer",
                        "description": "Byte offset to continue from: the next_offset a read returned.",
                    },
                },
            },
        ),
        MCPTool(
            name=TOOL_WRITE_FILE,
            description=(
                "Create a text file under www/, themes/, custom_templates/ or dashboards/ "
                "— card files, theme assets, Jinja macros, YAML dashboards. Replacing an "
                "existing file needs overwrite=true, and the old version is backed up. "
                "A file a browser runs (www/ .js, .html, .svg …) comes back with "
                "requires_confirmation and writes nothing: show the user what it does, and "
                "only once they agree call again with confirmed=true. Edit "
                "configuration.yaml and packages with selora_set_config_yaml. Requires "
                "admin access."
            ),
            inputSchema={
                "type": "object",
                "required": ["file", "content"],
                "properties": {
                    "file": _FILE_PARAM,
                    "content": {"type": "string", "description": "The file's full text."},
                    "overwrite": {
                        "type": "boolean",
                        "description": "Replace the file if it exists. Off by default.",
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": (
                            "Set ONLY after the user agreed to a file that came back "
                            "with requires_confirmation."
                        ),
                    },
                },
            },
        ),
        MCPTool(
            name=TOOL_DELETE_FILE,
            description=(
                "Delete a text file under www/, themes/, custom_templates/ or dashboards/. "
                "Runs IMMEDIATELY (a backup is kept) — confirm with the user first. "
                "Requires admin access."
            ),
            inputSchema={
                "type": "object",
                "required": ["file"],
                "properties": {"file": _FILE_PARAM},
            },
        ),
    ]
)

_HACS_REPOSITORY_PARAM: dict[str, Any] = {
    "type": "string",
    "description": "The repository's id or 'owner/repo', from selora_hacs_search.",
}
_HACS_CATEGORY_PARAM: dict[str, Any] = {
    "type": "string",
    "enum": ["integration", "plugin", "theme", "appdaemon", "python_script", "template"],
    "description": "plugin = dashboard cards; theme; integration = Python integrations.",
}
_CONFIRMED_PARAM: dict[str, Any] = {
    "type": "boolean",
    "description": "Set ONLY after the user agreed to a call that came back with "
    "requires_confirmation.",
}

_TOOL_DEFINITIONS.extend(
    [
        MCPTool(
            name=TOOL_HACS_SEARCH,
            description=(
                "Search HACS (the Home Assistant Community Store) for dashboard cards "
                "(category plugin — button-card, card-mod, mushroom …), themes and "
                "integrations, installed ones first. Requires HACS and admin access."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Words to match, e.g. 'card mod'."},
                    "category": _HACS_CATEGORY_PARAM,
                    "installed_only": {
                        "type": "boolean",
                        "description": "Only what is installed.",
                    },
                },
            },
        ),
        MCPTool(
            name=TOOL_HACS_INFO,
            description=(
                "One HACS repository's details: versions, releases, whether it is "
                "installed, authors. Requires HACS and admin access."
            ),
            inputSchema={
                "type": "object",
                "required": ["repository"],
                "properties": {"repository": _HACS_REPOSITORY_PARAM},
            },
        ),
        MCPTool(
            name=TOOL_HACS_INSTALL,
            description=(
                "Install a HACS repository, or update it (to version, or its latest "
                "release). Always comes back first with requires_confirmation and "
                "installs nothing: a card or theme is third-party code every browser runs; "
                "an integration is third-party Python running inside Home Assistant. Tell "
                "the user which, and only once they agree call again with confirmed=true. "
                "A card is registered as a dashboard resource by HACS; an integration "
                "needs a restart. Requires HACS and admin access."
            ),
            inputSchema={
                "type": "object",
                "required": ["repository"],
                "properties": {
                    "repository": _HACS_REPOSITORY_PARAM,
                    "version": {
                        "type": "string",
                        "description": "A release from selora_hacs_info. Omit for the latest.",
                    },
                    "confirmed": _CONFIRMED_PARAM,
                },
            },
        ),
        MCPTool(
            name=TOOL_HACS_REMOVE,
            description=(
                "Uninstall a HACS repository. Runs IMMEDIATELY — confirm with the user "
                "first; dashboards using a removed card stop rendering it. Requires HACS "
                "and admin access."
            ),
            inputSchema={
                "type": "object",
                "required": ["repository"],
                "properties": {"repository": _HACS_REPOSITORY_PARAM},
            },
        ),
        MCPTool(
            name=TOOL_HACS_ADD_REPOSITORY,
            description=(
                "Add a custom GitHub repository to HACS so it can be installed — for "
                "something not in HACS's default list. It is not reviewed by HACS, so this "
                "always comes back first with requires_confirmation: tell the user who "
                "they would be trusting, and only once they agree call again with "
                "confirmed=true. Requires HACS and admin access."
            ),
            inputSchema={
                "type": "object",
                "required": ["repository", "category"],
                "properties": {
                    "repository": {
                        "type": "string",
                        "description": "'owner/repo' on GitHub.",
                    },
                    "category": _HACS_CATEGORY_PARAM,
                    "confirmed": _CONFIRMED_PARAM,
                },
            },
        ),
    ]
)

_TOOL_DEFINITIONS.append(
    MCPTool(
        name=TOOL_GET_CAMERA_IMAGE,
        description=(
            "A snapshot from a camera, returned as an image you can look at — who is at "
            "the door, whether the garage closed, a package on the porch. Scaled to fit "
            "width × height (1280 × 720 unless you ask for more) where the camera allows; "
            "ask for more only to read detail. Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "required": ["entity_id"],
            "properties": {
                "entity_id": {"type": "string", "description": "e.g. 'camera.front_door'."},
                "width": {"type": "integer", "minimum": 160, "maximum": 3840},
                "height": {"type": "integer", "minimum": 120, "maximum": 2160},
            },
        },
    )
)
