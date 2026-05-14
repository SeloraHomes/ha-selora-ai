"""Provider-agnostic tool definitions for LLM tool calling.

Each tool is defined once as a ToolDef and can be serialised to
Anthropic tool_use format or OpenAI/Ollama function-calling format
via to_anthropic() / to_openai().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolParam:
    """A single parameter for a tool."""

    name: str
    type: str  # JSON Schema type: "string", "boolean", "integer", etc.
    description: str
    required: bool = False
    enum: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ToolDef:
    """A tool the LLM can invoke during chat."""

    name: str
    description: str
    params: tuple[ToolParam, ...] = field(default_factory=tuple)
    requires_admin: bool = False
    # Skip this tool for providers with tight context windows
    # (provider.is_low_context). Used to keep the selora_local prompt small.
    large_context_only: bool = False

    def to_anthropic(self) -> dict[str, Any]:
        """Anthropic tool_use format: {name, description, input_schema}."""
        properties: dict[str, Any] = {}
        required: list[str] = []
        for p in self.params:
            prop: dict[str, Any] = {"type": p.type, "description": p.description}
            if p.enum:
                prop["enum"] = list(p.enum)
            properties[p.name] = prop
            if p.required:
                required.append(p.name)
        schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }

    def to_openai(self) -> dict[str, Any]:
        """OpenAI / Ollama tools format: {type, function: {name, description, parameters}}."""
        properties: dict[str, Any] = {}
        required: list[str] = []
        for p in self.params:
            prop: dict[str, Any] = {"type": p.type, "description": p.description}
            if p.enum:
                prop["enum"] = list(p.enum)
            properties[p.name] = prop
            if p.required:
                required.append(p.name)
        parameters: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            parameters["required"] = required
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }


# ── Tool Definitions ────────────────────────────────────────────────────────

TOOL_GET_HOME_SNAPSHOT = ToolDef(
    name="get_home_snapshot",
    description=(
        "Return current Home Assistant entity states grouped by area. "
        "Use this to understand what devices and entities exist in the home, "
        "their current states, and which areas they are assigned to."
    ),
)

TOOL_DISCOVER_DEVICES = ToolDef(
    name="discover_network_devices",
    description=(
        "Discover network devices and integration status. Returns three lists: "
        "discovered (pending config flows from mDNS/SSDP), configured (already set up), "
        "and available (known integrations not yet found). Use this to help users "
        "understand what devices are on their network and what can be set up."
    ),
)

TOOL_LIST_DISCOVERED = ToolDef(
    name="list_discovered_flows",
    description=(
        "Return all pending discovery config flows with their flow_id, handler, "
        "and current step. Use this to see what devices are waiting to be set up."
    ),
)

TOOL_START_DEVICE_FLOW = ToolDef(
    name="start_device_flow",
    description=(
        "Start an integration config flow for a specific device. Use this after "
        "discover_network_devices reveals a device the user wants to set up. "
        "Provide the integration domain (e.g. 'hue', 'cast') and optionally the host IP."
    ),
    params=(
        ToolParam(
            name="domain",
            type="string",
            description="Integration domain (e.g. 'hue', 'sonos', 'cast')",
            required=True,
        ),
        ToolParam(
            name="host",
            type="string",
            description="Host IP address if known",
        ),
    ),
    requires_admin=True,
)

TOOL_ACCEPT_FLOW = ToolDef(
    name="accept_device_flow",
    description=(
        "Accept and confirm a pending device discovery flow. Use this when a user "
        "wants to add a discovered device. Provide the flow_id from "
        "discover_network_devices or list_discovered_flows."
    ),
    params=(
        ToolParam(
            name="flow_id",
            type="string",
            description="The flow_id from a discovered device",
            required=True,
        ),
    ),
    requires_admin=True,
)

TOOL_LIST_DEVICES = ToolDef(
    name="list_devices",
    description=(
        "List Home Assistant devices tracked by Selora AI with their area, "
        "manufacturer, model, integration, and entity IDs. Use this when the user "
        "asks about their devices, wants to know what's in a room, or asks about "
        "device status. Supports optional area and domain filters."
    ),
    params=(
        ToolParam(
            name="area",
            type="string",
            description="Filter by area name (case-insensitive substring match)",
        ),
        ToolParam(
            name="domain",
            type="string",
            description="Filter by entity domain (e.g. light, climate, lock)",
        ),
    ),
)

TOOL_GET_DEVICE = ToolDef(
    name="get_device",
    description=(
        "Return full detail for a single Home Assistant device: metadata, "
        "all associated entities, and their current states and key attributes. "
        "Use this when the user asks about a specific device's state, configuration, "
        "or health. Requires a device_id from list_devices."
    ),
    params=(
        ToolParam(
            name="device_id",
            type="string",
            description="The HA device registry ID from list_devices",
            required=True,
        ),
    ),
)

TOOL_GET_ENTITY_STATE = ToolDef(
    name="get_entity_state",
    description=(
        "Return current state and key attributes for a single Home Assistant entity. "
        "Prefer this over get_home_snapshot for targeted state questions "
        "('is the kitchen light on?', 'what's the thermostat set to?'). "
        "Requires the full entity_id (e.g. 'light.kitchen')."
    ),
    params=(
        ToolParam(
            name="entity_id",
            type="string",
            description="Full entity_id (e.g. 'light.kitchen')",
            required=True,
        ),
    ),
)

TOOL_FIND_ENTITIES_BY_AREA = ToolDef(
    name="find_entities_by_area",
    description=(
        "Return entities located in a given area, optionally filtered by domain. "
        "Use this to pick the right entity_id before issuing a command "
        "(e.g. 'find lights in the kitchen'). Entity area is resolved via the "
        "entity registry first, then via its device. Area is a case-insensitive "
        "substring match."
    ),
    params=(
        ToolParam(
            name="area",
            type="string",
            description="Area name (case-insensitive substring match)",
            required=True,
        ),
        ToolParam(
            name="domain",
            type="string",
            description="Optional domain filter (e.g. 'light', 'climate')",
        ),
    ),
)

TOOL_VALIDATE_ACTION = ToolDef(
    name="validate_action",
    description=(
        "Validate a Home Assistant service call against Selora's safe-command "
        "policy WITHOUT executing it. Returns 'valid' (bool), 'errors', and "
        "'allowed_data_keys'. Call this before emitting a command if you are "
        "unsure about the service name, target domain, or which data parameters "
        "are accepted."
    ),
    params=(
        ToolParam(
            name="service",
            type="string",
            description="Service in '<domain>.<verb>' form (e.g. 'light.turn_on')",
            required=True,
        ),
        ToolParam(
            name="entity_id",
            type="string",
            description="Target entity_id (single string).",
            required=True,
        ),
        ToolParam(
            name="data",
            type="object",
            description=(
                "Optional service data payload — e.g. {'brightness_pct': 80} "
                "for light.turn_on, {'temperature': 21, 'hvac_mode': 'heat'} "
                "for climate.set_temperature, {'percentage': 50} for "
                "fan.set_percentage, {'position': 50} for cover.set_cover_position."
            ),
        ),
    ),
)

TOOL_EXECUTE_COMMAND = ToolDef(
    name="execute_command",
    description=(
        "Execute a Home Assistant service call within the safe-command "
        "allowlist (light, switch, fan, media_player, climate, cover, "
        "input_boolean, scene). Validates against the same policy as "
        "validate_action before invoking hass.services. Returns post-execution "
        "state. Prefer this over emitting JSON command intents when you have a "
        "known entity_id. Include the 'data' object for parameterized commands "
        "(brightness, temperature, volume, position, etc.)."
    ),
    params=(
        ToolParam(
            name="service",
            type="string",
            description="Service in '<domain>.<verb>' form (e.g. 'light.turn_on')",
            required=True,
        ),
        ToolParam(
            name="entity_id",
            type="string",
            description="Target entity_id (single string).",
            required=True,
        ),
        ToolParam(
            name="data",
            type="object",
            description=(
                "Service data payload. Required for parameterized commands. "
                "Examples: {'brightness_pct': 50} for dimming, "
                "{'temperature': 21} or {'temperature': 21, 'hvac_mode': 'heat'} "
                "for thermostats, {'percentage': 75} for fans, "
                "{'volume_level': 0.4} for media players, "
                "{'position': 30} for cover.set_cover_position. "
                "Omit when no parameters are needed (e.g. plain turn_on/turn_off)."
            ),
        ),
    ),
    requires_admin=True,
)

TOOL_ACTIVATE_SCENE = ToolDef(
    name="activate_scene",
    description=(
        "Activate a Home Assistant scene by entity_id (e.g. 'scene.movie_night'). "
        "Calls scene.turn_on. Use this when the user names a scene rather than "
        "individual devices."
    ),
    params=(
        ToolParam(
            name="entity_id",
            type="string",
            description="Scene entity_id (must start with 'scene.').",
            required=True,
        ),
    ),
    requires_admin=True,
)

TOOL_SEARCH_ENTITIES = ToolDef(
    name="search_entities",
    description=(
        "Fuzzy-search entities by free-text query across entity_id, friendly "
        "name, aliases, and area. Returns ranked matches. Use this when the "
        "user names a device informally and you need to resolve it to an "
        "entity_id before issuing a command."
    ),
    params=(
        ToolParam(
            name="query",
            type="string",
            description="Free-text search query (e.g. 'kitchen island light').",
            required=True,
        ),
        ToolParam(
            name="domain",
            type="string",
            description="Optional domain filter (e.g. 'light').",
        ),
    ),
    large_context_only=True,
)

TOOL_GET_ENTITY_HISTORY = ToolDef(
    name="get_entity_history",
    description=(
        "Return recent state changes for a single entity from the Home "
        "Assistant recorder. Use for temporal questions ('when did the front "
        "door last open?'). Bounded to 24h."
    ),
    params=(
        ToolParam(
            name="entity_id",
            type="string",
            description="Full entity_id (e.g. 'binary_sensor.front_door').",
            required=True,
        ),
        ToolParam(
            name="hours",
            type="number",
            description="Hours of history (0.25-24, default 6).",
        ),
    ),
    large_context_only=True,
)

TOOL_EVAL_TEMPLATE = ToolDef(
    name="eval_template",
    description=(
        "Evaluate a Home Assistant Jinja template using HA's sandbox. Use for "
        "time math, sun position, presence checks, and predicates that can't be "
        "derived from snapshots."
    ),
    params=(
        ToolParam(
            name="template",
            type="string",
            description="Jinja template (e.g. \"{{ states('sun.sun') }}\").",
            required=True,
        ),
    ),
    large_context_only=True,
)


TOOL_LIST_SUGGESTIONS = ToolDef(
    name="list_suggestions",
    description=(
        "List pending automation suggestions that Selora AI has generated "
        "based on observed device usage patterns. Use this when the user asks "
        "for ideas, suggestions, or what automations they could set up. "
        "Returns descriptions, confidence scores, and evidence summaries."
    ),
    params=(
        ToolParam(
            name="status",
            type="string",
            description="Filter by status. Default: pending.",
            enum=("pending", "accepted", "dismissed", "snoozed"),
        ),
    ),
)


TOOL_ACCEPT_SUGGESTION = ToolDef(
    name="accept_suggestion",
    description=(
        "Accept a pending automation suggestion and create the automation in Home Assistant. "
        "The automation is created disabled with a [Selora AI] prefix for user review. "
        "Use this when the user confirms they want a suggested automation set up."
    ),
    params=(
        ToolParam(
            name="suggestion_id",
            type="string",
            description="The suggestion_id from list_suggestions to accept.",
            required=True,
        ),
    ),
    requires_admin=True,
)

TOOL_DISMISS_SUGGESTION = ToolDef(
    name="dismiss_suggestion",
    description=(
        "Dismiss a pending automation suggestion the user does not want. "
        "Use when the user says no, declines, or indicates they are not interested."
    ),
    params=(
        ToolParam(
            name="suggestion_id",
            type="string",
            description="The suggestion_id from list_suggestions to dismiss.",
            required=True,
        ),
        ToolParam(
            name="reason",
            type="string",
            description="Brief reason for dismissal (e.g. 'not useful', 'already have this').",
        ),
    ),
    requires_admin=True,
)


# Single registry of all chat tools
CHAT_TOOLS: tuple[ToolDef, ...] = (
    TOOL_GET_HOME_SNAPSHOT,
    TOOL_DISCOVER_DEVICES,
    TOOL_LIST_DISCOVERED,
    TOOL_START_DEVICE_FLOW,
    TOOL_ACCEPT_FLOW,
    TOOL_LIST_DEVICES,
    TOOL_GET_DEVICE,
    TOOL_GET_ENTITY_STATE,
    TOOL_FIND_ENTITIES_BY_AREA,
    TOOL_VALIDATE_ACTION,
    TOOL_EXECUTE_COMMAND,
    TOOL_ACTIVATE_SCENE,
    TOOL_SEARCH_ENTITIES,
    TOOL_GET_ENTITY_HISTORY,
    TOOL_EVAL_TEMPLATE,
    TOOL_LIST_SUGGESTIONS,
    TOOL_ACCEPT_SUGGESTION,
    TOOL_DISMISS_SUGGESTION,
)

# Name → ToolDef lookup for admin checks in the executor
TOOL_MAP: dict[str, ToolDef] = {t.name: t for t in CHAT_TOOLS}


def get_tools_for_provider(provider: str) -> list[dict[str, Any]]:
    """Return tool definitions formatted for the given LLM provider.

    .. deprecated::
        Use ``LLMClient._get_tools_for_provider()`` or
        ``provider.format_tool()`` instead. Kept for backward compatibility.
    """
    if provider == "anthropic":
        return [t.to_anthropic() for t in CHAT_TOOLS]
    return [t.to_openai() for t in CHAT_TOOLS]
