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
    # Element type for ``type="array"`` params. Required in practice: Gemini's
    # function-declaration schema rejects an ARRAY without ``items``, so a bare
    # array param would break tool calling on that provider only.
    items_type: str | None = None

    def to_schema(self) -> dict[str, Any]:
        """Render this parameter as a JSON Schema property."""
        prop: dict[str, Any] = {"type": self.type, "description": self.description}
        if self.enum:
            prop["enum"] = list(self.enum)
        if self.type == "array":
            prop["items"] = {"type": self.items_type or "string"}
        return prop


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
            properties[p.name] = p.to_schema()
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
            properties[p.name] = p.to_schema()
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
        "hardware connection identifiers, all associated entities, and their "
        "current states and key attributes. Use this when the user asks about a "
        "specific device's state, configuration, or health. Also use it to get "
        "the Zigbee IEEE address (returned as `zha_ieee`) needed to build a "
        "`zha_event` button-press trigger. Requires a device_id from list_devices."
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

TOOL_GET_DEVICE_TRIGGERS = ToolDef(
    name="get_device_triggers",
    description=(
        "Return the ready-to-use `platform: device` trigger blocks that Home "
        "Assistant offers for a device — button presses, scene-controller "
        "events, etc. ALWAYS call this when building an automation triggered by "
        "a button, remote, or scene controller (ZHA, Z-Wave JS, deCONZ, Shelly, "
        "…): the returned blocks already carry the correct domain, device_id, "
        "type, and subtype, so drop one straight into the automation's triggers "
        "list instead of hand-assembling an event trigger or guessing a raw "
        "node/IEEE id. An empty list means the device exposes no device "
        "triggers — fall back to an event or state trigger. Requires a "
        "device_id from list_devices."
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

TOOL_LIST_DASHBOARDS = ToolDef(
    name="list_dashboards",
    description=(
        "List the user's writable (storage-mode) Lovelace dashboards. "
        "Returns a list of {url_path, title}; url_path is null for the "
        "default dashboard. Call this before insert_dashboard_card so you "
        "place the card on a dashboard that actually exists and can be "
        "edited (YAML-mode dashboards are read-only and not listed)."
    ),
    params=(),
    requires_admin=True,
)

TOOL_INSERT_DASHBOARD_CARD = ToolDef(
    name="insert_dashboard_card",
    description=(
        "Add a Lovelace card to a dashboard view. Use when the user wants a "
        "tap target / card for an entity (e.g. a helper a recipe created). "
        "Compose a standard card config in 'card' (type + entity + any "
        "options). Call list_dashboards first to choose 'dashboard_target'. "
        "Idempotent: re-calling with the same 'tag' replaces the prior card "
        "rather than duplicating it."
    ),
    params=(
        ToolParam(
            name="card",
            type="object",
            description=(
                "Complete Lovelace card config. Must include 'type' "
                "(e.g. 'button', 'entity', 'entities') and the relevant "
                "entity/entities. Example: "
                "{'type': 'button', 'entity': 'input_boolean.baby_sleeping', "
                "'name': 'Baby sleeping', 'icon': 'mdi:sleep'}."
            ),
            required=True,
        ),
        ToolParam(
            name="dashboard_target",
            type="string",
            description=(
                "url_path of the target dashboard from list_dashboards. "
                "Omit for the default dashboard."
            ),
        ),
        ToolParam(
            name="view",
            type="string",
            description=(
                "View to append to — a view title/path, or a numeric index "
                "as a string ('0' = first view). Omit for the first view."
            ),
        ),
        ToolParam(
            name="tag",
            type="string",
            description=(
                "Ownership tag so the card can be replaced/removed later. "
                "Use the recipe slug when placing a recipe's card."
            ),
        ),
    ),
    requires_admin=True,
)

TOOL_SEARCH_ENTITIES = ToolDef(
    name="search_entities",
    description=(
        "Fuzzy-search entities by free-text query across entity_id, friendly "
        "name, aliases, and area. Returns ranked matches (with total_scored so "
        "you can gauge confidence). Use this when the user names a device — or "
        "a SCENE — informally and you need to resolve it to an entity_id before "
        "issuing a command or building an automation. To resolve a named scene "
        "('Stores at 50%'), search with domain='scene' and use the top match's "
        "entity_id verbatim; NEVER guess a scene.<slug> id — a wrong id fails "
        "validation. If the top match looks weak (low score, several close "
        "candidates), broaden the query or ask the user which one."
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
        "The automation is created disabled and tagged with the `selora_ai` label so the user can "
        "review and enable it. Use this when the user confirms they want a suggested automation set up."
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

TOOL_DELETE_AUTOMATION = ToolDef(
    name="delete_automation",
    description=(
        "Delete a yaml-managed Home Assistant automation. Use when the user "
        "asks to delete, remove, or get rid of an automation. Identify the "
        "target by its entity_id (e.g. 'automation.evening_lights') — resolve "
        "the name to an entity_id with search_entities first if the user names "
        "it informally — or by its automation_id. "
        "This does NOT delete immediately: it surfaces a confirmation card the "
        "user must tap to approve, so do not ask 'are you sure?' in prose — "
        "just call the tool and the card handles confirmation. Only "
        "yaml-managed automations can be deleted; storage/UI-managed ones must "
        "be removed from the Home Assistant UI."
    ),
    params=(
        ToolParam(
            name="entity_id",
            type="string",
            description="Automation entity_id (e.g. 'automation.evening_lights').",
        ),
        ToolParam(
            name="automation_id",
            type="string",
            description="The automation id from automations.yaml, if known.",
        ),
    ),
    requires_admin=True,
)

TOOL_DELETE_SCENE = ToolDef(
    name="delete_scene",
    description=(
        "Delete a yaml-managed Home Assistant scene. Use when the user asks to "
        "delete, remove, or get rid of a scene. Identify the target by its "
        "entity_id (e.g. 'scene.movie_night') — resolve the name to an "
        "entity_id with search_entities using domain='scene' first if the user "
        "names it informally — or by a Selora scene_id. "
        "This does NOT delete immediately: it surfaces a confirmation card the "
        "user must tap to approve, so do not ask 'are you sure?' in prose — "
        "just call the tool and the card handles confirmation. Only "
        "yaml-managed scenes can be deleted; storage/UI-managed ones must be "
        "removed from the Home Assistant UI."
    ),
    params=(
        ToolParam(
            name="entity_id",
            type="string",
            description="Scene entity_id (must start with 'scene.').",
        ),
        ToolParam(
            name="scene_id",
            type="string",
            description="The Selora SceneStore scene_id, if known.",
        ),
    ),
    requires_admin=True,
)

# ── Group helpers ───────────────────────────────────────────────────────────
#
# A group gives an automation ONE entity_id that fans out to many devices, so
# the automation never has to be rewritten when membership changes. These four
# tools are the reason the model can offer that instead of pasting a long
# entity list into every automation it writes.

TOOL_LIST_GROUPS = ToolDef(
    name="list_groups",
    description=(
        "List the home's existing groups with their members, live state, and "
        "entity_id. Call this BEFORE creating a group so you extend an existing "
        "one instead of making a near-duplicate, and to get the entity_id or "
        "entry_id that update_group / delete_group need. "
        "'read_only_yaml_groups' in the result are group.* entities defined in "
        "YAML — they can be referenced in automations but NOT edited here. "
        "'members' is capped for very large groups: 'member_count' is always the "
        "true total, and 'members_omitted' says how many were left out."
    ),
    params=(
        ToolParam(
            name="group_type",
            type="string",
            description=(
                "Optional filter by member domain, e.g. 'light', 'switch', 'cover', "
                "'lock', 'media_player', 'binary_sensor', 'sensor'."
            ),
        ),
    ),
)

TOOL_CREATE_GROUP = ToolDef(
    name="create_group",
    description=(
        "Create a group: one entity_id that controls many devices at once. "
        "Returns the new entity_id (e.g. 'light.evening_lights') — target THAT "
        "in automations and scenes instead of listing every member, so the user "
        "can later add or remove devices without the automation changing. "
        "Prefer this when the user asks to control several devices together as a "
        "unit, or when an automation would otherwise repeat the same 4+ entity "
        "list. Do NOT create a group just to run a one-off command on several "
        "devices — execute_command already accepts multiple entity_ids. "
        "All members must share ONE domain (a group is per-domain), except that "
        "sensor/number/input_number combine into a numeric group; for a mixed "
        "request create one group per domain. Resolve names to real entity_ids "
        "with search_entities or find_entities_by_area first — unknown "
        "entity_ids are rejected."
    ),
    params=(
        ToolParam(
            name="name",
            type="string",
            description="Human-readable group name, e.g. 'Downstairs Lights'.",
            required=True,
        ),
        ToolParam(
            name="entities",
            type="array",
            description=(
                "Member entity_ids. Must all exist and share one domain "
                "(e.g. ['light.lamp', 'light.ceiling'])."
            ),
            required=True,
            items_type="string",
        ),
        ToolParam(
            name="requires_all_members",
            type="boolean",
            description=(
                "Light/switch/binary_sensor groups only: when true the group reads "
                "'on' only if EVERY member is on. Default false (on if any member is)."
            ),
        ),
        ToolParam(
            name="hide_members",
            type="boolean",
            description=(
                "Hide the individual member entities in the Home Assistant UI so only "
                "the group shows. Default false — only set it if the user asks."
            ),
        ),
        ToolParam(
            name="statistic",
            type="string",
            description=(
                "ONLY for numeric groups whose members are sensor/number/input_number "
                "entities: how their values combine into one number. Omit entirely for "
                "light, switch, cover, lock, fan and every other type — those have no "
                "numeric state. Omit to use the mean."
            ),
            enum=(
                "last",
                "first_available",
                "max",
                "mean",
                "median",
                "min",
                "product",
                "range",
                "stdev",
                "sum",
            ),
        ),
    ),
    requires_admin=True,
)

TOOL_UPDATE_GROUP = ToolDef(
    name="update_group",
    description=(
        "Change which devices belong to an existing group, or rename it. Use "
        "this — not create_group — when the user wants to add or remove a device "
        "from a group they already have. Identify the group by entity_id "
        "(preferred), entry_id, or group_name. Use add_entities / "
        "remove_entities for a delta; use entities only to replace the whole "
        "member list. New members must match the group's existing domain. "
        "Every automation targeting the group picks the change up immediately, "
        "with no automation edit needed."
    ),
    params=(
        ToolParam(
            name="entity_id",
            type="string",
            description="The group's entity_id, e.g. 'light.evening_lights'.",
        ),
        ToolParam(
            name="entry_id",
            type="string",
            description="The group's config entry_id, as returned by list_groups.",
        ),
        ToolParam(
            name="group_name",
            type="string",
            description="The group's current name, if you have no id for it.",
        ),
        ToolParam(
            name="new_name",
            type="string",
            description="Rename the group to this.",
        ),
        ToolParam(
            name="add_entities",
            type="array",
            description="entity_ids to ADD to the current members.",
            items_type="string",
        ),
        ToolParam(
            name="remove_entities",
            type="array",
            description="entity_ids to REMOVE from the current members.",
            items_type="string",
        ),
        ToolParam(
            name="entities",
            type="array",
            description=(
                "Replace the ENTIRE member list with these entity_ids. Prefer "
                "add_entities/remove_entities unless the user restated the whole list."
            ),
            items_type="string",
        ),
        ToolParam(
            name="requires_all_members",
            type="boolean",
            description=(
                "Light/switch/binary_sensor groups only: require EVERY member to be on "
                "for the group to read 'on'."
            ),
        ),
    ),
    requires_admin=True,
)

TOOL_DELETE_GROUP = ToolDef(
    name="delete_group",
    description=(
        "Delete a group. The member devices are NOT affected — only the grouping "
        "is removed. Identify it by entity_id (preferred), entry_id, or "
        "group_name. Only helper groups can be deleted; YAML-defined group.* "
        "entities cannot. "
        "This does NOT delete immediately: it surfaces a confirmation card the "
        "user must tap to approve, so do not ask 'are you sure?' in prose — just "
        "call the tool and the card handles confirmation, including warning the "
        "user when automations still target the group."
    ),
    params=(
        ToolParam(
            name="entity_id",
            type="string",
            description="The group's entity_id, e.g. 'light.evening_lights'.",
        ),
        ToolParam(
            name="entry_id",
            type="string",
            description="The group's config entry_id, as returned by list_groups.",
        ),
        ToolParam(
            name="group_name",
            type="string",
            description="The group's name, if you have no id for it.",
        ),
    ),
    requires_admin=True,
)


# ── Registry management (areas, floors, entities, devices) ──────────────────
#
# Every tool below is ``large_context_only``. Two reasons, and the second is
# the load-bearing one: the low-context path in ``architect_chat`` sets
# ``tool_executor = None`` outright, so Selora AI Local never receives a tool
# schema at all — but ``_get_tools_for_provider`` is also reachable from the
# Assist conversation path, and a 1.7B model handed a registry-editing schema
# will call it. Config surgery is not something the on-device model should be
# offered; it has no way to confirm and no way to undo.

TOOL_LIST_AREAS = ToolDef(
    name="list_areas",
    description=(
        "List every area and floor in the home with how many entities and devices "
        "each holds. Use this before assigning something to an area, to check the "
        "area exists and to get its exact name."
    ),
    params=(
        ToolParam(
            name="include_entities",
            type="boolean",
            description="Include each area's entity_ids (capped per area). Off by default — counts only.",
        ),
    ),
    large_context_only=True,
)

TOOL_ASSIGN_AREA = ToolDef(
    name="assign_area",
    description=(
        "Put one or more entities and/or devices into an area. This is how you answer "
        "'assign the living room lights to the Living Room' or 'move the hallway sensor "
        "upstairs'. Resolve entity_ids with search_entities first. Prefer assigning the "
        "DEVICE when the user names a physical thing — its entities follow it."
    ),
    params=(
        ToolParam(
            name="area",
            type="string",
            description="Target area name or area_id. Must already exist — create it with create_area.",
            required=True,
        ),
        ToolParam(
            name="entity_ids",
            type="array",
            items_type="string",
            description="Entity ids to place in the area.",
        ),
        ToolParam(
            name="device_ids",
            type="array",
            items_type="string",
            description="Device ids to place in the area. Their entities move too.",
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_CREATE_AREA = ToolDef(
    name="create_area",
    description=(
        "Create a new area (room). If an area with that name already exists this "
        "reports it instead of creating a duplicate. Naming a floor that does not "
        "exist creates the floor too."
    ),
    params=(
        ToolParam(
            name="name",
            type="string",
            description="Area name as the user would say it, e.g. 'Living Room'.",
            required=True,
        ),
        ToolParam(
            name="floor",
            type="string",
            description="Floor name or floor_id, e.g. 'Upstairs'. Created if new.",
        ),
        ToolParam(name="icon", type="string", description="Optional mdi icon, e.g. 'mdi:sofa'."),
        ToolParam(
            name="aliases",
            type="array",
            items_type="string",
            description="Alternative names Assist should also recognise for this area.",
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_UPDATE_AREA = ToolDef(
    name="update_area",
    description=(
        "Rename an area, move it to a floor, or change its icon or aliases. "
        "Does not move entities — use assign_area for that."
    ),
    params=(
        ToolParam(
            name="area",
            type="string",
            description="The area to change, by name or area_id.",
            required=True,
        ),
        ToolParam(name="new_name", type="string", description="New name for the area."),
        ToolParam(
            name="floor", type="string", description="Floor name or floor_id. Created if new."
        ),
        ToolParam(name="icon", type="string", description="Optional mdi icon."),
        ToolParam(
            name="aliases",
            type="array",
            items_type="string",
            description=(
                "Replacement alias list for the area. Send an empty array to "
                "remove all aliases; omit the argument to leave them unchanged."
            ),
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_DELETE_AREA = ToolDef(
    name="delete_area",
    description=(
        "Delete an area. Entities and devices in it are NOT deleted — they become "
        "unassigned — but automations targeting the area silently stop matching. "
        "The user gets a confirmation card showing what is affected."
    ),
    params=(
        ToolParam(
            name="area",
            type="string",
            description="The area to delete, by name or area_id.",
            required=True,
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_UPDATE_ENTITY = ToolDef(
    name="update_entity",
    description=(
        "Change one entity's registry settings: friendly name, Assist aliases, icon, "
        "whether it is hidden or disabled, and whether Assist can see it. Use new_name "
        "to rename what the user sees — that is what 'rename this to X' means. "
        "new_entity_id changes the underlying id and is refused when automations, "
        "scripts, scenes, or groups reference it."
    ),
    params=(
        ToolParam(
            name="entity_id",
            type="string",
            description="The entity to change.",
            required=True,
        ),
        ToolParam(name="new_name", type="string", description="New friendly (display) name."),
        ToolParam(
            name="aliases",
            type="array",
            items_type="string",
            description=(
                "Replacement alias list — alternative names Assist should recognise. "
                "Send an empty array to remove all aliases; omit it to leave them unchanged."
            ),
        ),
        ToolParam(name="icon", type="string", description="Optional mdi icon."),
        ToolParam(
            name="hidden",
            type="boolean",
            description="Hide the entity from dashboards and Assist without disabling it.",
        ),
        ToolParam(
            name="disabled",
            type="boolean",
            description="Disable the entity — it stops updating and leaves the state machine.",
        ),
        ToolParam(
            name="expose_to_assist",
            type="boolean",
            description="Whether Home Assistant's Assist voice agent can control this entity.",
        ),
        ToolParam(
            name="new_entity_id",
            type="string",
            description="Rename the entity_id itself. Same domain only; refused if anything references it.",
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_UPDATE_DEVICE = ToolDef(
    name="update_device",
    description=(
        "Rename a device, move it to an area, or disable it. Renaming a device does "
        "not rename its entities. Moving a device carries its entities with it, except "
        "any that were individually assigned elsewhere."
    ),
    params=(
        ToolParam(
            name="device",
            type="string",
            description="Device id, or the device's current name.",
            required=True,
        ),
        ToolParam(name="new_name", type="string", description="New name for the device."),
        ToolParam(name="area", type="string", description="Area name or area_id to move it to."),
        ToolParam(
            name="disabled",
            type="boolean",
            description="Disable the device and all its entities.",
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_LIST_SERVICES = ToolDef(
    name="list_services",
    description=(
        "List the services Home Assistant can call, and the fields each accepts. "
        "Pass a domain (e.g. 'light', 'vacuum', 'media_player') to get that domain's "
        "services with their fields — without one you get domain names only. Use this "
        "when you are unsure a service exists or what arguments it takes."
    ),
    params=(
        ToolParam(
            name="domain",
            type="string",
            description="Service domain to expand, e.g. 'climate'. Omit for the domain list.",
        ),
    ),
    large_context_only=True,
)


# ── Scripts, labels, helpers, diagnostics ───────────────────────────────────

TOOL_LIST_SCRIPTS = ToolDef(
    name="list_scripts",
    description=(
        "List the home's scripts — named, reusable action sequences with no trigger. "
        "Use this before writing an automation that repeats steps another script "
        "already performs, or when the user asks what scripts they have."
    ),
    large_context_only=True,
)

TOOL_GET_SCRIPT = ToolDef(
    name="get_script",
    description=(
        "Return one script's full configuration, including its action sequence. "
        "Call this before set_script when editing — set_script REPLACES the whole "
        "script, so you need the current sequence to preserve the parts being kept. "
        "The sequence is returned whole or not at all: if 'editable' is false the "
        "script is too large to return, and you must NOT replace it — say so and "
        "point the user at Settings → Automations & scenes → Scripts."
    ),
    params=(
        ToolParam(
            name="script",
            type="string",
            description="Script entity_id, object_id, or its exact name.",
            required=True,
        ),
    ),
    large_context_only=True,
)

TOOL_SET_SCRIPT = ToolDef(
    name="set_script",
    description=(
        "Create a script, or replace an existing one entirely. A script is the right "
        "answer when several automations need the same steps, or the user wants a "
        "button they can press ('Movie Night', 'Leaving the House'). The sequence uses "
        "the same action syntax as an automation's actions. This REPLACES the script's "
        "sequence — call get_script first when editing. Settings this tool has no "
        "parameter for (fields, variables, max, trace) are carried over unchanged."
    ),
    params=(
        ToolParam(
            name="alias",
            type="string",
            description="The script's display name, e.g. 'Movie Night'.",
            required=True,
        ),
        ToolParam(
            name="sequence",
            type="array",
            items_type="object",
            description="Ordered list of action steps, same syntax as automation actions.",
            required=True,
        ),
        ToolParam(
            name="object_id",
            type="string",
            description="Existing script's object_id when editing. Omit to create; derived from alias.",
        ),
        ToolParam(name="description", type="string", description="What the script does."),
        ToolParam(
            name="mode",
            type="string",
            description="Run mode when re-triggered while already running.",
            enum=("single", "restart", "queued", "parallel"),
        ),
        ToolParam(name="icon", type="string", description="Optional mdi icon."),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_DELETE_SCRIPT = ToolDef(
    name="delete_script",
    description=(
        "Delete a script. Automations and other scripts that call it will break, so "
        "the user gets a confirmation card naming what depends on it."
    ),
    params=(
        ToolParam(
            name="script",
            type="string",
            description="Script entity_id, object_id, or exact name.",
            required=True,
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_LIST_LABELS = ToolDef(
    name="list_labels",
    description=(
        "List the home's labels with how many entities, devices, and areas carry each. "
        "A label is a cross-cutting tag ('holiday', 'kids', 'battery-powered') that "
        "automations can target directly — the way to group things that span rooms "
        "and domains, where an area or a group helper cannot."
    ),
    large_context_only=True,
)

TOOL_CREATE_LABEL = ToolDef(
    name="create_label",
    description=(
        "Create a label. Reports the existing one if the name is taken rather than "
        "making a duplicate. You do not need to call this before assign_labels — that "
        "creates unknown labels itself."
    ),
    params=(
        ToolParam(name="name", type="string", description="Label name.", required=True),
        ToolParam(name="icon", type="string", description="Optional mdi icon."),
        ToolParam(name="color", type="string", description="Optional HA colour name, e.g. 'blue'."),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_ASSIGN_LABELS = ToolDef(
    name="assign_labels",
    description=(
        "Add and/or remove labels on entities, devices, and areas. Labels not yet in "
        "use are created. This applies deltas — labels already on the target that you "
        "do not name are left alone."
    ),
    params=(
        ToolParam(
            name="add_labels",
            type="array",
            items_type="string",
            description="Label names to add. Created if they do not exist.",
        ),
        ToolParam(
            name="remove_labels",
            type="array",
            items_type="string",
            description="Label names to remove.",
        ),
        ToolParam(
            name="entity_ids", type="array", items_type="string", description="Entities to label."
        ),
        ToolParam(
            name="device_ids", type="array", items_type="string", description="Devices to label."
        ),
        ToolParam(
            name="areas",
            type="array",
            items_type="string",
            description="Area names or area_ids to label.",
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_DELETE_LABEL = ToolDef(
    name="delete_label",
    description=(
        "Delete a label. It is stripped from everything carrying it, and automations "
        "targeting it stop matching. The user gets a confirmation card."
    ),
    params=(
        ToolParam(
            name="label",
            type="string",
            description="Label name or label_id.",
            required=True,
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_LIST_HELPERS = ToolDef(
    name="list_helpers",
    description=(
        "List the home's helper entities — input_boolean, input_number, timer, "
        "counter, schedule, and the config-entry helpers. Use this to find an existing "
        "toggle or counter to wire an automation to. Creating a helper is not possible "
        "from chat; direct the user to Settings → Devices & services → Helpers."
    ),
    params=(
        ToolParam(
            name="domain",
            type="string",
            description="Restrict to one helper domain, e.g. 'input_boolean'.",
        ),
    ),
    large_context_only=True,
)

TOOL_GET_LOGS = ToolDef(
    name="get_logs",
    description=(
        "Return recent errors and warnings from Home Assistant's system log, "
        "deduplicated with a repeat count. Use this when the user reports something "
        "broken, an integration is misbehaving, or a device went unavailable."
    ),
    params=(
        ToolParam(
            name="level",
            type="string",
            description="Only entries at this level.",
            enum=("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"),
        ),
        ToolParam(
            name="contains",
            type="string",
            description="Only entries whose message or logger name contains this text.",
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_GET_AUTOMATION_TRACES = ToolDef(
    name="get_automation_traces",
    description=(
        "Return the most recent runs of one automation: when it triggered, whether a "
        "condition stopped it, and where it ended. This is how to answer 'why didn't "
        "my automation run?' — do not guess from the YAML when a trace exists."
    ),
    params=(
        ToolParam(
            name="automation",
            type="string",
            description="Automation entity_id or its exact name.",
            required=True,
        ),
    ),
    requires_admin=True,
    large_context_only=True,
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
    TOOL_GET_DEVICE_TRIGGERS,
    TOOL_GET_ENTITY_STATE,
    TOOL_FIND_ENTITIES_BY_AREA,
    TOOL_VALIDATE_ACTION,
    TOOL_EXECUTE_COMMAND,
    TOOL_ACTIVATE_SCENE,
    TOOL_LIST_DASHBOARDS,
    TOOL_INSERT_DASHBOARD_CARD,
    TOOL_SEARCH_ENTITIES,
    TOOL_GET_ENTITY_HISTORY,
    TOOL_EVAL_TEMPLATE,
    TOOL_LIST_SUGGESTIONS,
    TOOL_ACCEPT_SUGGESTION,
    TOOL_DISMISS_SUGGESTION,
    TOOL_DELETE_AUTOMATION,
    TOOL_DELETE_SCENE,
    TOOL_LIST_GROUPS,
    TOOL_CREATE_GROUP,
    TOOL_UPDATE_GROUP,
    TOOL_DELETE_GROUP,
    TOOL_LIST_AREAS,
    TOOL_ASSIGN_AREA,
    TOOL_CREATE_AREA,
    TOOL_UPDATE_AREA,
    TOOL_DELETE_AREA,
    TOOL_UPDATE_ENTITY,
    TOOL_UPDATE_DEVICE,
    TOOL_LIST_SERVICES,
    TOOL_LIST_SCRIPTS,
    TOOL_GET_SCRIPT,
    TOOL_SET_SCRIPT,
    TOOL_DELETE_SCRIPT,
    TOOL_LIST_LABELS,
    TOOL_CREATE_LABEL,
    TOOL_ASSIGN_LABELS,
    TOOL_DELETE_LABEL,
    TOOL_LIST_HELPERS,
    TOOL_GET_LOGS,
    TOOL_GET_AUTOMATION_TRACES,
)

# Name → ToolDef lookup for admin checks in the executor
TOOL_MAP: dict[str, ToolDef] = {t.name: t for t in CHAT_TOOLS}

# Tools needed to resolve + execute a plain device-control command.
# Used to trim the tool schema on command-intent turns so the model
# isn't handed device-discovery / suggestion / history tools it can't
# use for "lock the door" — smaller schema = less prefill latency.
# The delete tools are included because "get rid of the Movie Night scene"
# classifies as a command intent, and without them the trimmed schema would
# hide the very operation the request needs.
# The group tools are included for the same reason, and it bites harder there:
# _classify_chat_intent FALLS THROUGH to "command" for anything that isn't a
# question or an automation pattern, so "group my bedroom lights" and "add the
# lamp to the downstairs group" both arrive here. Omitting them would make
# group management invisible on exactly the phrasings that ask for it.
COMMAND_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "execute_command",
        "activate_scene",
        "find_entities_by_area",
        "search_entities",
        "get_entity_state",
        "validate_action",
        "delete_automation",
        "delete_scene",
        "list_groups",
        "create_group",
        "update_group",
        "delete_group",
        # Same reason the automation/scene deletes are here: "get rid of the
        # Movie Night script" and "drop the holiday label" fall through
        # ``_classify_chat_intent`` to ``command``, and the config detector
        # only claims the phrasings that name the noun explicitly. Without
        # these two the trimmed schema hides the delete on exactly the
        # wording that asks for it.
        "delete_script",
        "delete_label",
        "delete_area",
    }
)

# Tools for a turn that reconfigures the home rather than operating it —
# "put the lamp in the Study", "call this one the Reading Lamp", "hide that
# sensor from Assist".
#
# This is a SECOND lane rather than more entries in COMMAND_TOOL_NAMES, and
# the reason is that the two sets barely overlap. Config phrasings fall
# through ``_classify_chat_intent`` to ``command`` exactly the way group
# phrasings do, so without a lane of their own the registry tools would be
# invisible on the requests that need them — but folding them INTO the command
# lane would hand every "turn off the kitchen light" turn eight registry-editing
# tools it can never use, and the command lane exists precisely to keep that
# schema small. A config turn does not need execute_command; a command turn
# does not need update_entity. What both need is entity resolution, so the
# search/read tools appear in both.
CONFIG_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "search_entities",
        "get_entity_state",
        "find_entities_by_area",
        "list_devices",
        "get_device",
        "list_areas",
        "assign_area",
        "create_area",
        "update_area",
        "delete_area",
        "update_entity",
        "update_device",
        "list_services",
        "list_labels",
        "create_label",
        "assign_labels",
        "delete_label",
        "list_helpers",
        "list_scripts",
        "get_script",
        "set_script",
        "delete_script",
    }
)

# intent hint → the tool subset that turn is trimmed to. An intent absent from
# this map (or ``None``) gets the full schema.
#
# Scripts and diagnostics deliberately have NO lane. A script request is
# automation-shaped ("make me a Movie Night routine") and a diagnostic one is a
# question ("why didn't it run?"); both classify to intents that already get the
# full schema, so giving them a lane would only create a way to accidentally
# trim a tool out of a turn that needs it.
TOOL_LANES: dict[str, frozenset[str]] = {
    "command": COMMAND_TOOL_NAMES,
    "config": CONFIG_TOOL_NAMES,
}


def get_tools_for_provider(provider: str) -> list[dict[str, Any]]:
    """Return tool definitions formatted for the given LLM provider.

    .. deprecated::
        Use ``LLMClient._get_tools_for_provider()`` or
        ``provider.format_tool()`` instead. Kept for backward compatibility.
    """
    if provider == "anthropic":
        return [t.to_anthropic() for t in CHAT_TOOLS]
    return [t.to_openai() for t in CHAT_TOOLS]
