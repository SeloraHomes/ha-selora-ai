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
