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
    # Only offered where the Selora panel is the surface. The work is performed
    # BY the panel, so Assist — which renders no `command_approval` card and has
    # no way to resolve one — would let the model call it and produce a proposal
    # nothing can act on. A description saying "panel only" does not stop that;
    # withholding the schema does.
    panel_only: bool = False

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
        "individual devices. To CREATE a scene there is deliberately no tool: "
        "emit the scene JSON block (intent 'scene') and the user gets a card to "
        "accept it. Not finding a create-scene tool does not mean you cannot "
        "make one — never tell the user scene creation is unavailable."
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
        "List every Lovelace dashboard. Returns {url_path, title, editable}; "
        "url_path is null for the default dashboard. Call this first to learn "
        "the url_path to pass as dashboard_target when EDITING one that exists. "
        "It is not a picker for a request to CREATE a dashboard: finding a "
        "plausible existing board here and adding a page to it is not what was "
        "asked for — use create_dashboard. editable false means it can be "
        "read but not changed — a YAML dashboard, or one Home Assistant is still "
        "generating because nobody has taken control of it yet — so do not offer "
        "to edit it."
    ),
    params=(),
)

TOOL_INSERT_DASHBOARD_CARD = ToolDef(
    name="insert_dashboard_card",
    description=(
        "Add a Lovelace card to a dashboard view. Use when the user wants a "
        "tap target / card for an entity (e.g. a helper a recipe created). "
        "Compose a standard card config in 'card' (type + entity + any "
        "options). Call list_dashboards first to choose 'dashboard_target'. "
        "Idempotent: re-calling with the same 'tag' replaces the prior card "
        "rather than duplicating it. Appends to the END of the view — use "
        "move_dashboard_card to reposition it, and group_dashboard_cards to put "
        "cards side by side."
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
        ToolParam(
            name="remaining_intent",
            type="string",
            description=(
                "What you still have to do AFTER the user confirms, in one short "
                "phrase — 'recreate it with the new schedule'. The turn ENDS at "
                "the card, so this is the only thing that brings you back; set it "
                "whenever the deletion is a step rather than the whole request. "
                "It is shown on the card, so the user approves the plan and not "
                "just the deletion. Leave it out when deleting IS the request."
            ),
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
        "To CREATE a scene there is no tool — emit the scene JSON block "
        "(intent 'scene') and the user gets a card to accept it. Only "
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
        ToolParam(
            name="remaining_intent",
            type="string",
            description=(
                "What you still have to do AFTER the user confirms, in one short "
                "phrase — 'recreate it with the new schedule'. The turn ENDS at "
                "the card, so this is the only thing that brings you back; set it "
                "whenever the deletion is a step rather than the whole request. "
                "It is shown on the card, so the user approves the plan and not "
                "just the deletion. Leave it out when deleting IS the request."
            ),
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
        ToolParam(
            name="remaining_intent",
            type="string",
            description=(
                "What you still have to do AFTER the user confirms, in one short "
                "phrase — 'recreate it with the new schedule'. The turn ENDS at "
                "the card, so this is the only thing that brings you back; set it "
                "whenever the deletion is a step rather than the whole request. "
                "It is shown on the card, so the user approves the plan and not "
                "just the deletion. Leave it out when deleting IS the request."
            ),
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
        ToolParam(
            name="remaining_intent",
            type="string",
            description=(
                "What you still have to do AFTER the user confirms, in one short "
                "phrase — 'recreate it with the new schedule'. The turn ENDS at "
                "the card, so this is the only thing that brings you back; set it "
                "whenever the deletion is a step rather than the whole request. "
                "It is shown on the card, so the user approves the plan and not "
                "just the deletion. Leave it out when deleting IS the request."
            ),
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_LIST_FLOORS = ToolDef(
    name="list_floors",
    description=(
        "Every floor of the home with the areas standing on it, ordered by level. "
        "Also names any areas that have no floor. Call this before create_floor so "
        "you do not add a second floor for a storey that already exists."
    ),
    params=(),
    large_context_only=True,
)

TOOL_CREATE_FLOOR = ToolDef(
    name="create_floor",
    description=(
        "Create a floor. A floor groups areas; it holds no entities itself. If one "
        "with the name already exists this reports it rather than making a second."
    ),
    params=(
        ToolParam(name="name", type="string", description="Floor name.", required=True),
        ToolParam(
            name="level",
            type="integer",
            description="Storey number — 0 ground, 1 first, -1 basement. Orders the list.",
        ),
        ToolParam(name="icon", type="string", description="mdi icon."),
        ToolParam(name="aliases", type="array", description="Alternative names for voice."),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_UPDATE_FLOOR = ToolDef(
    name="update_floor",
    description=(
        "Rename a floor or change its level, icon, or aliases. Areas on it keep "
        "their place — a rename does not move anything."
    ),
    params=(
        ToolParam(
            name="floor",
            type="string",
            description="The floor to change, by name or floor_id.",
            required=True,
        ),
        ToolParam(name="new_name", type="string", description="New name."),
        ToolParam(name="level", type="integer", description="New storey number."),
        ToolParam(name="icon", type="string", description="New mdi icon."),
        ToolParam(name="aliases", type="array", description="Replaces the alias list."),
        ToolParam(
            name="clear",
            type="array",
            description=(
                "Fields to REMOVE from the floor: 'icon' and/or 'level'. Use this "
                "rather than passing an empty string, which is read as 'not set'."
            ),
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_DELETE_FLOOR = ToolDef(
    name="delete_floor",
    description=(
        "Delete a floor. The areas on it are NOT deleted — they simply stop having "
        "a floor. The user gets a confirmation card naming them."
    ),
    params=(
        ToolParam(
            name="floor",
            type="string",
            description="The floor to delete, by name or floor_id.",
            required=True,
        ),
        ToolParam(
            name="remaining_intent",
            type="string",
            description=(
                "What you still have to do AFTER the user confirms, in one short "
                "phrase — 'recreate it with the new schedule'. The turn ENDS at "
                "the card, so this is the only thing that brings you back; set it "
                "whenever the deletion is a step rather than the whole request. "
                "It is shown on the card, so the user approves the plan and not "
                "just the deletion. Leave it out when deleting IS the request."
            ),
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_LIST_BLUEPRINTS = ToolDef(
    name="list_blueprints",
    description=(
        "Blueprints installed on this home — parameterised automation, script and "
        "template blueprints. Returns each one's domain, path and the names of the "
        "inputs it asks for. Only a blueprint whose domain is 'automation' can be "
        "turned into an automation: call get_blueprint for its input details, then "
        "create_automation with use_blueprint. A script or template blueprint is "
        "readable here but cannot be used that way."
    ),
    params=(
        ToolParam(
            name="domain",
            type="string",
            description="Limit to one kind. Omit for all.",
            enum=("automation", "script", "template"),
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_GET_BLUEPRINT = ToolDef(
    name="get_blueprint",
    description=(
        "One blueprint's inputs in full — name, description, selector and default "
        "for each, and whether it is required. Read this before create_automation "
        "(automation-domain blueprints only): use_blueprint.input must name inputs "
        "the blueprint declares, and the selector says what shape each value takes."
    ),
    params=(
        ToolParam(
            name="domain",
            type="string",
            description="automation, script, or template.",
            required=True,
            enum=("automation", "script", "template"),
        ),
        ToolParam(
            name="path",
            type="string",
            description="The blueprint path from list_blueprints.",
            required=True,
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_LIST_CATEGORIES = ToolDef(
    name="list_categories",
    description=(
        "Categories file automations, scripts, scenes and helpers into named lists "
        "on their Home Assistant pages. Returns them with how many entities each "
        "holds, plus the scopes already in use. Omit scope to see every scope."
    ),
    params=(
        ToolParam(
            name="scope",
            type="string",
            description="Limit to one list. Omit to see all of them.",
            enum=("automation", "script", "scene", "helper"),
        ),
    ),
    large_context_only=True,
)

TOOL_CREATE_CATEGORY = ToolDef(
    name="create_category",
    description=(
        "Create a category within one list. If one with the name already exists "
        "there this reports it rather than making a second."
    ),
    params=(
        ToolParam(
            name="scope",
            type="string",
            description=(
                "Which list the category belongs to. A scope no Home Assistant page "
                "reads gives a category that exists but appears nowhere."
            ),
            required=True,
            enum=("automation", "script", "scene", "helper"),
        ),
        ToolParam(name="name", type="string", description="Category name.", required=True),
        ToolParam(name="icon", type="string", description="mdi icon."),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_ASSIGN_CATEGORY = ToolDef(
    name="assign_category",
    description=(
        "File entities under a category within one list, or omit category to take "
        "them out of it. An entity holds one category per list; other lists are "
        "left alone. Create the category first if it does not exist."
    ),
    params=(
        ToolParam(
            name="entity_ids",
            type="array",
            description="Entities to file.",
            required=True,
        ),
        ToolParam(
            name="scope",
            type="string",
            description=(
                "Which list the category belongs to. A scope no Home Assistant page "
                "reads gives a category that exists but appears nowhere."
            ),
            required=True,
            enum=("automation", "script", "scene", "helper"),
        ),
        ToolParam(
            name="category",
            type="string",
            description="Category name or id. Omit to remove them from their category.",
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_DELETE_CATEGORY = ToolDef(
    name="delete_category",
    description=(
        "Delete a category. Entities filed under it are NOT deleted — they simply "
        "stop being categorised. The user gets a confirmation card."
    ),
    params=(
        ToolParam(
            name="scope",
            type="string",
            description=(
                "Which list the category belongs to. A scope no Home Assistant page "
                "reads gives a category that exists but appears nowhere."
            ),
            required=True,
            enum=("automation", "script", "scene", "helper"),
        ),
        ToolParam(
            name="category",
            type="string",
            description="The category to delete, by name or id.",
            required=True,
        ),
        ToolParam(
            name="remaining_intent",
            type="string",
            description=(
                "What you still have to do AFTER the user confirms, in one short "
                "phrase — 'recreate it with the new schedule'. The turn ENDS at "
                "the card, so this is the only thing that brings you back; set it "
                "whenever the deletion is a step rather than the whole request. "
                "It is shown on the card, so the user approves the plan and not "
                "just the deletion. Leave it out when deleting IS the request."
            ),
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
        ToolParam(
            name="remaining_intent",
            type="string",
            description=(
                "What you still have to do AFTER the user confirms, in one short "
                "phrase — 'recreate it with the new schedule'. The turn ENDS at "
                "the card, so this is the only thing that brings you back; set it "
                "whenever the deletion is a step rather than the whole request. "
                "It is shown on the card, so the user approves the plan and not "
                "just the deletion. Leave it out when deleting IS the request."
            ),
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
        ToolParam(
            name="remaining_intent",
            type="string",
            description=(
                "What you still have to do AFTER the user confirms, in one short "
                "phrase — 'recreate it with the new schedule'. The turn ENDS at "
                "the card, so this is the only thing that brings you back; set it "
                "whenever the deletion is a step rather than the whole request. "
                "It is shown on the card, so the user approves the plan and not "
                "just the deletion. Leave it out when deleting IS the request."
            ),
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


# ── Dashboards ──────────────────────────────────────────────────────────────
#
# ``create_dashboard`` is ``panel_only`` because a dashboard ENTRY needs
# ``DashboardsCollection``, which lovelace keeps as a local in ``async_setup``
# and publishes only to its admin-only websocket commands — the same wall the
# ``input_*`` helpers hit. Not reachable in-process; reachable by an
# authenticated websocket CLIENT, which the panel is, so it proposes and the
# panel executes.
#
# Which means ``add_dashboard_view`` must NOT describe itself as the way to get
# a new dashboard, in either direction. Claiming to be the substitute had a
# request for a dashboard answered by appending a page to an unrelated one;
# denying that a dashboard can be created at all does the same thing, because
# the denial contradicts the tool sitting next to it and the model routes
# around both by adding the page anyway. It says what it does, and points at
# ``create_dashboard`` for the rest.

TOOL_GET_DASHBOARD = ToolDef(
    name="get_dashboard",
    description=(
        "Read a dashboard: its views (pages) with card counts, and — when you name "
        "a view — the cards on it. ALWAYS call this before adding, moving, or "
        "changing a card: it is the only way to learn which views exist and what is "
        "already there, and every edit tool addresses cards by the index this "
        "returns. Also the tool for answering questions about a user's dashboard "
        "or advising on how to organise it. Works on YAML dashboards too (read-only)."
    ),
    params=(
        ToolParam(
            name="dashboard_target",
            type="string",
            description="url_path from list_dashboards. Omit for the default dashboard.",
        ),
        ToolParam(
            name="view",
            type="string",
            description=(
                "A view index ('0'), path, or title. Omit to get the view list without cards."
            ),
        ),
    ),
    large_context_only=True,
)

TOOL_GET_DASHBOARD_CARD = ToolDef(
    name="get_dashboard_card",
    description=(
        "Return one card's complete configuration plus its fingerprint. Call this "
        "before update_dashboard_card — that tool REPLACES a card outright, so you "
        "need the current config to keep the parts you are not changing."
    ),
    params=(
        ToolParam(
            name="dashboard_target", type="string", description="url_path, or omit for the default."
        ),
        ToolParam(
            name="view", type="string", description="View index, path, or title.", required=True
        ),
        ToolParam(
            name="card_index",
            type="integer",
            description="The card's index, from get_dashboard.",
            required=True,
        ),
    ),
    large_context_only=True,
)

TOOL_CREATE_DASHBOARD = ToolDef(
    name="create_dashboard",
    description=(
        "Create a whole new dashboard, with its own sidebar entry. THIS is the "
        "tool for 'create a dashboard' / 'make me a new dashboard' — do not add a "
        "page to an existing dashboard instead, which is not what was asked for "
        "and leaves the user hunting for it. CALL IT when asked for a dashboard: "
        "calling it IS how the user is asked, because the Create button rides on "
        "the card it returns. Do NOT describe the dashboard you would make and "
        "wait to be told to go ahead — that promises a card the user never gets "
        "and creates nothing. The create happens in their browser under their own "
        "account, so do NOT say the dashboard exists until the result comes back, "
        "and do not call add_dashboard_view or insert_dashboard_card for it in this "
        "reply — it does not exist yet. Say what still has to go on it in "
        "`remaining_intent` and you will be brought back to do it the moment it "
        "does. This tool "
        "is only ever offered where the panel is connected, so if you can see it "
        "the user is in the panel and the card will reach them — never tell them "
        "to open the panel, or to go to Settings, and never doubt that this works. "
        "Use add_dashboard_view for a page on a dashboard that already exists."
    ),
    params=(
        ToolParam(name="title", type="string", description="Dashboard title.", required=True),
        ToolParam(
            name="url_path",
            type="string",
            description="URL slug. Derived from the title when omitted.",
        ),
        ToolParam(name="icon", type="string", description="mdi icon for the sidebar."),
        ToolParam(
            name="require_admin",
            type="boolean",
            description="Hide it from non-admin users. Default false.",
        ),
        ToolParam(
            name="show_in_sidebar",
            type="boolean",
            description="Show it in the sidebar. Default true.",
        ),
        ToolParam(
            name="remaining_intent",
            type="string",
            description=(
                "What you still have to do AFTER the user taps Create, in one short "
                "phrase — 'add the Office lights, climate and sensors'. Setting it "
                "is what makes the rest of the request happen: the turn ends at the "
                "card, and this is the only thing that brings you back once the "
                "dashboard exists. Leave it out when creating the dashboard IS the "
                "whole request. It is shown on the card, so the user approves the "
                "plan and not just the first step."
            ),
        ),
    ),
    requires_admin=True,
    large_context_only=True,
    panel_only=True,
)

TOOL_DELETE_DASHBOARD = ToolDef(
    name="delete_dashboard",
    description=(
        "Delete a whole dashboard and everything on it. The user is shown a "
        "confirmation card naming how many views and cards go with it, and it "
        "happens in their browser under their own account — so do NOT say it is "
        "gone until the result comes back, and do not ask 'are you sure?' in "
        "prose, the card is the asking. The default dashboard cannot be deleted "
        "and a YAML dashboard has to be removed from configuration.yaml. To "
        "remove one PAGE rather than the whole dashboard, use "
        "remove_dashboard_view."
    ),
    params=(
        ToolParam(
            name="dashboard_target",
            type="string",
            description="url_path from list_dashboards.",
            required=True,
        ),
        ToolParam(
            name="remaining_intent",
            type="string",
            description=(
                "What you still have to do AFTER the user confirms, in one short "
                "phrase. The turn ends at the card, so this is the only thing "
                "that brings you back. Leave it out when deleting IS the request."
            ),
        ),
    ),
    requires_admin=True,
    large_context_only=True,
    panel_only=True,
)

TOOL_ADD_DASHBOARD_VIEW = ToolDef(
    name="add_dashboard_view",
    description=(
        "Add a view (a PAGE) to a dashboard that already exists. This does NOT "
        "create a dashboard: if the user asked for a NEW dashboard, use "
        "create_dashboard instead — appending a page here is not what they asked "
        "for and they will not find it where they expect. Always name which "
        "dashboard you added the page to and give the url from the result, or they "
        "will go looking for it in the wrong place. A new view is EMPTY and shows "
        "nothing until insert_dashboard_card puts something on it."
    ),
    params=(
        ToolParam(
            name="dashboard_target", type="string", description="url_path, or omit for the default."
        ),
        ToolParam(
            name="title",
            type="string",
            description="The page title shown in the tab bar.",
            required=True,
        ),
        ToolParam(name="path", type="string", description="URL slug for the view, e.g. 'garage'."),
        ToolParam(name="icon", type="string", description="Optional mdi icon for the tab."),
        ToolParam(
            name="sections",
            type="boolean",
            description=(
                "Use the newer sections layout instead of the classic masonry one. Off by default."
            ),
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_UPDATE_DASHBOARD_VIEW = ToolDef(
    name="update_dashboard_view",
    description=(
        "Rename a view or change its URL path or icon. Cards are untouched. Pass "
        "the view's fingerprint from get_dashboard so the edit cannot land on a "
        "different page if the dashboard changed meanwhile."
    ),
    params=(
        ToolParam(
            name="dashboard_target", type="string", description="url_path, or omit for the default."
        ),
        ToolParam(
            name="view", type="string", description="View index, path, or title.", required=True
        ),
        ToolParam(name="title", type="string", description="New page title."),
        ToolParam(name="path", type="string", description="New URL slug."),
        ToolParam(name="icon", type="string", description="New mdi icon."),
        ToolParam(
            name="clear",
            type="array",
            description=(
                "Fields to REMOVE from the view: 'icon' and/or 'path'. Use this "
                "rather than passing an empty string, which is read as 'not set'."
            ),
        ),
        ToolParam(
            name="expected_fingerprint",
            type="string",
            description="The view's fingerprint from get_dashboard.",
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_REMOVE_DASHBOARD_VIEW = ToolDef(
    name="remove_dashboard_view",
    description=(
        "Delete a whole view and every card on it. The user gets a confirmation card "
        "showing how many cards go with it."
    ),
    params=(
        ToolParam(
            name="dashboard_target", type="string", description="url_path, or omit for the default."
        ),
        ToolParam(
            name="view", type="string", description="View index, path, or title.", required=True
        ),
        ToolParam(
            name="expected_fingerprint",
            type="string",
            description="The view's fingerprint from get_dashboard.",
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_UPDATE_DASHBOARD_CARD = ToolDef(
    name="update_dashboard_card",
    description=(
        "Replace one card with a new config. This REPLACES it outright — call "
        "get_dashboard_card first and send back the whole card with your changes "
        "applied. Pass the fingerprint you were given so the edit cannot land on a "
        "different card if the dashboard changed meanwhile."
    ),
    params=(
        ToolParam(
            name="dashboard_target", type="string", description="url_path, or omit for the default."
        ),
        ToolParam(
            name="view", type="string", description="View index, path, or title.", required=True
        ),
        ToolParam(
            name="card_index",
            type="integer",
            description="Card index from get_dashboard.",
            required=True,
        ),
        ToolParam(
            name="card",
            type="object",
            description="The complete replacement card config, including 'type'.",
            required=True,
        ),
        ToolParam(
            name="expected_fingerprint",
            type="string",
            description="The fingerprint from get_dashboard/get_dashboard_card.",
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_REMOVE_DASHBOARD_CARD = ToolDef(
    name="remove_dashboard_card",
    description=(
        "Remove one card from a view. The removed card's config comes back in the "
        "result, so it can be put straight back if the user changes their mind."
    ),
    params=(
        ToolParam(
            name="dashboard_target", type="string", description="url_path, or omit for the default."
        ),
        ToolParam(
            name="view", type="string", description="View index, path, or title.", required=True
        ),
        ToolParam(
            name="card_index",
            type="integer",
            description="Card index from get_dashboard.",
            required=True,
        ),
        ToolParam(
            name="expected_fingerprint",
            type="string",
            description="The fingerprint from get_dashboard/get_dashboard_card.",
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)


TOOL_MOVE_DASHBOARD_CARD = ToolDef(
    name="move_dashboard_card",
    description=(
        "Move a card to a different position in the same view — this is the ONLY "
        "way to reorder. Use it for 'keep the garage door at the top'. Indices come "
        "from get_dashboard. The card itself is moved untouched, so nothing about "
        "what it shows can change."
    ),
    params=(
        ToolParam(
            name="dashboard_target", type="string", description="url_path, or omit for the default."
        ),
        ToolParam(
            name="view", type="string", description="View index, path, or title.", required=True
        ),
        ToolParam(
            name="from_index",
            type="integer",
            description="The card's current index.",
            required=True,
        ),
        ToolParam(
            name="to_index",
            type="integer",
            description="The index to move it to. 0 is the top.",
            required=True,
        ),
        ToolParam(
            name="expected_fingerprint",
            type="string",
            description="The fingerprint from get_dashboard, so the move cannot hit a different card.",
        ),
        ToolParam(
            name="expected_view_fingerprint",
            type="string",
            description="The view's fingerprint from get_dashboard.",
        ),
    ),
    requires_admin=True,
    large_context_only=True,
)

TOOL_GROUP_DASHBOARD_CARDS = ToolDef(
    name="group_dashboard_cards",
    description=(
        "Move existing cards into a container card so they sit together — this "
        "is how cards end up side by side, since a masonry view has no rows. "
        'Give the container card config you want, e.g. {"type": "grid", '
        '"columns": 3} or {"type": "horizontal-stack"}; its \'cards\' is '
        "filled in for you. The cards themselves are moved untouched."
    ),
    params=(
        ToolParam(
            name="dashboard_target", type="string", description="url_path, or omit for the default."
        ),
        ToolParam(
            name="view", type="string", description="View index, path, or title.", required=True
        ),
        ToolParam(
            name="card_indices",
            type="array",
            description="Indices of the cards to group, from get_dashboard.",
            required=True,
        ),
        ToolParam(
            name="container",
            type="object",
            description="The container card config. Its 'cards' is set for you.",
            required=True,
        ),
        ToolParam(
            name="expected_view_fingerprint",
            type="string",
            description=(
                "The view's fingerprint from get_dashboard. Every card index above is "
                "relative to the view as you read it, so pass this."
            ),
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
    TOOL_LIST_FLOORS,
    TOOL_CREATE_FLOOR,
    TOOL_UPDATE_FLOOR,
    TOOL_DELETE_FLOOR,
    TOOL_LIST_BLUEPRINTS,
    TOOL_GET_BLUEPRINT,
    TOOL_LIST_CATEGORIES,
    TOOL_CREATE_CATEGORY,
    TOOL_ASSIGN_CATEGORY,
    TOOL_DELETE_CATEGORY,
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
    TOOL_GET_DASHBOARD,
    TOOL_GET_DASHBOARD_CARD,
    TOOL_CREATE_DASHBOARD,
    TOOL_DELETE_DASHBOARD,
    TOOL_ADD_DASHBOARD_VIEW,
    TOOL_UPDATE_DASHBOARD_VIEW,
    TOOL_REMOVE_DASHBOARD_VIEW,
    TOOL_UPDATE_DASHBOARD_CARD,
    TOOL_REMOVE_DASHBOARD_CARD,
    TOOL_MOVE_DASHBOARD_CARD,
    TOOL_GROUP_DASHBOARD_CARDS,
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
        # And create_area, for the same reason in the other direction:
        # "make me an Office area" is command-shaped, and a user who is told
        # Selora cannot create areas will not try again. The intent detector
        # is what usually routes this to the config lane, but it is one regex
        # and it has been wrong here before — being in both lanes means a
        # phrasing it misses costs a slightly larger schema, not the feature.
        "create_area",
        "delete_floor",
        "delete_category",
        # Dashboard tools sit in BOTH lanes, deliberately.
        #
        # "Add a thermostat card to my dashboard" classifies as a command;
        # "reorganise my dashboard" classifies as config; "what's on my
        # dashboard?" is a question. The same seven tools serve all three, and
        # this family has no lane of its own to fall back to — before this they
        # were in NO lane, so a command-classified turn could not see
        # insert_dashboard_card at all and the request simply failed.
        #
        # Duplicating them is the cheap fix and the robust one: the alternative
        # is another vocabulary heuristic deciding which lane a dashboard
        # request belongs to, and that decision has been wrong repeatedly.
        "list_dashboards",
        "get_dashboard",
        "get_dashboard_card",
        "insert_dashboard_card",
        "create_dashboard",
        "delete_dashboard",
        "add_dashboard_view",
        "update_dashboard_view",
        "remove_dashboard_view",
        "update_dashboard_card",
        "remove_dashboard_card",
        "move_dashboard_card",
        "group_dashboard_cards",
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
        "list_floors",
        "list_blueprints",
        "get_blueprint",
        "list_categories",
        "create_category",
        "assign_category",
        "delete_category",
        "create_floor",
        "update_floor",
        "delete_floor",
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
        # Dashboard tools sit in BOTH lanes, deliberately.
        #
        # "Add a thermostat card to my dashboard" classifies as a command;
        # "reorganise my dashboard" classifies as config; "what's on my
        # dashboard?" is a question. The same seven tools serve all three, and
        # this family has no lane of its own to fall back to — before this they
        # were in NO lane, so a command-classified turn could not see
        # insert_dashboard_card at all and the request simply failed.
        #
        # Duplicating them is the cheap fix and the robust one: the alternative
        # is another vocabulary heuristic deciding which lane a dashboard
        # request belongs to, and that decision has been wrong repeatedly.
        "list_dashboards",
        "get_dashboard",
        "get_dashboard_card",
        "insert_dashboard_card",
        "create_dashboard",
        "delete_dashboard",
        "add_dashboard_view",
        "update_dashboard_view",
        "remove_dashboard_view",
        "update_dashboard_card",
        "remove_dashboard_card",
        "move_dashboard_card",
        "group_dashboard_cards",
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
