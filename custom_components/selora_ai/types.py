"""Shared type definitions for the Selora AI integration.

Provides TypedDict classes for the core data structures that flow between
modules.  Using these instead of ``dict[str, Any]`` enables IDE autocompletion,
catches key typos at type-check time, and documents the expected shape of data.

Convention: all new code must import from here rather than using bare
``dict[str, Any]`` for these structures.
"""

from __future__ import annotations

from typing import Any, Required, TypedDict

# ── Automation structures ──────────────────────────────────────────────


class AutomationTrigger(TypedDict, total=False):
    """A single HA automation trigger."""

    platform: Required[str]
    entity_id: str
    to: str
    # NOTE: 'from' is a reserved word; use ``trigger["from"]`` at runtime
    event: str
    at: str
    offset: str


class AutomationAction(TypedDict, total=False):
    """A single HA automation action (service call)."""

    action: str
    service: str  # legacy alias for 'action'
    target: dict[str, Any]
    data: dict[str, Any]


class AutomationCondition(TypedDict, total=False):
    """A single HA automation condition."""

    condition: str
    entity_id: str
    state: str
    weekday: list[str]
    after: str
    before: str


class AutomationDict(TypedDict, total=False):
    """A full HA automation as stored in automations.yaml."""

    id: str
    alias: Required[str]
    description: str
    triggers: list[AutomationTrigger]
    conditions: list[AutomationCondition]
    actions: list[AutomationAction]
    mode: str
    initial_state: bool
    # Legacy singular keys (pre-2024 HA format)
    trigger: list[AutomationTrigger] | AutomationTrigger
    condition: list[AutomationCondition] | AutomationCondition
    action: list[AutomationAction] | AutomationAction


class ValidationResult(TypedDict):
    """Return type of validate_automation_payload."""

    is_valid: bool
    reason: str
    normalized: AutomationDict | None


class RiskAssessment(TypedDict):
    """Result of assess_automation_risk."""

    level: str  # "normal" | "elevated"
    flags: list[str]
    summary: str
    reasons: list[str]
    scrutiny_tags: list[str]


class AutomationCreateResult(TypedDict):
    """Return type of async_create_automation."""

    success: bool
    automation_id: str | None


# ── Scene structures ─────────────────────────────────────────────────


class ScenePayload(TypedDict):
    """A validated scene payload from the LLM."""

    name: str
    entities: dict[str, dict[str, Any]]


class SceneRecord(TypedDict):
    """A single Selora-managed scene lifecycle record."""

    scene_id: str
    name: str
    entity_count: int
    entity_id: str | None
    session_id: str | None
    created_at: str
    updated_at: str
    deleted_at: str | None


class SceneStoreData(TypedDict):
    """Top-level data layout for SceneStore."""

    scenes: dict[str, SceneRecord]


# ── Version & lineage structures ──────────────────────────────────────


class AutomationVersion(TypedDict):
    """A single immutable version snapshot."""

    version_id: str
    automation_id: str
    created_at: str
    yaml: str
    data: dict[str, Any]
    message: str
    session_id: str | None


class LineageEntry(TypedDict):
    """A single lineage event for an automation."""

    version_id: str
    session_id: str | None
    message_index: int | None
    action: str  # "created" | "updated" | "restored" | "refined"
    timestamp: str


class AutomationRecord(TypedDict):
    """A full automation record in the store."""

    automation_id: str
    current_version_id: str
    versions: list[AutomationVersion]
    lineage: list[LineageEntry]


class AutomationMetadata(TypedDict):
    """Lightweight metadata returned by get_metadata."""

    automation_id: str
    version_count: int
    current_version_id: str


class DraftAutomation(TypedDict):
    """A draft automation linked to a chat session."""

    draft_id: str
    alias: str
    session_id: str
    created_at: str


# ── Pattern structures ────────────────────────────────────────────────


class StateChange(TypedDict):
    """A single state history entry."""

    state: str
    prev: str
    ts: str


class PatternEvidence(TypedDict, total=False):
    """Evidence payload for a detected pattern."""

    _signature: str
    time_slot: str
    is_weekday: bool
    target_state: str
    occurrences: int
    total_days: int
    timestamps: list[str]
    trigger_entity: str
    trigger_state: str
    response_entity: str
    response_state: str
    avg_delay_seconds: float
    co_occurrences: int
    window_minutes: int
    delay_stddev: float
    directionality: float
    trigger_from: str
    trigger_to: str


class PatternDict(TypedDict, total=False):
    """A detected pattern as stored in PatternStore."""

    pattern_id: str | None
    type: Required[str]  # "time_based" | "correlation" | "sequence"
    entity_ids: Required[list[str]]
    description: str
    evidence: PatternEvidence
    confidence: Required[float]
    detected_at: str
    last_seen: str
    occurrence_count: int
    status: str  # "active" | "dismissed" | "snoozed" | "accepted" | "rejected"
    snooze_until: str | None


# ── Suggestion structures ─────────────────────────────────────────────


class SuggestionDict(TypedDict, total=False):
    """A proactive automation suggestion."""

    suggestion_id: str
    pattern_id: str
    source: str  # "pattern" | "hybrid"
    confidence: float
    automation_data: AutomationDict
    automation_yaml: str
    description: str
    evidence_summary: str
    created_at: str
    status: str  # "pending" | "accepted" | "dismissed" | "snoozed"
    snooze_until: str | None
    dismissed_at: str | None
    dismissal_reason: str | None


# ── LLM usage tracking ────────────────────────────────────────────────


class LLMUsageInfo(TypedDict, total=False):
    """Token usage extracted from a single LLM response.

    Fields are optional so providers that don't expose a dimension (e.g.
    Ollama lacks cost) can omit it. ``input_tokens``/``output_tokens`` are
    the universal pair; the cache fields are Anthropic-specific.
    """

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


class LLMUsageEvent(TypedDict, total=False):
    """One enriched LLM usage record, stored in the ring buffer.

    ``kind`` is the call site (e.g. ``"chat"``, ``"suggestions"``,
    ``"command"``). ``intent`` is the parsed architect intent when
    available (only set for chat after the response is parsed).
    """

    timestamp: Required[str]  # ISO 8601 UTC
    kind: Required[str]
    intent: str
    provider: Required[str]
    model: Required[str]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


# ── Device structures ─────────────────────────────────────────────────


class DeviceInfo(TypedDict, total=False):
    """Device entry from device_manager discovery."""

    name: str
    integration: str
    area: str | None
    state: str | None
    entity_id: str | None


class DiscoveredFlow(TypedDict, total=False):
    """A pending discovery config flow."""

    flow_id: str
    handler: str
    step_id: str
    context: dict[str, Any]
    known: KnownIntegrationMeta | None


class KnownIntegrationMeta(TypedDict):
    """Metadata from KNOWN_INTEGRATIONS for annotating flows."""

    name: str
    category: str
    discovery: str
    source: str
    brands: tuple[str, ...]


class ConfiguredIntegration(TypedDict, total=False):
    """An already-configured integration entry."""

    domain: str
    title: str
    entry_id: str
    known: KnownIntegrationMeta | None


class AvailableIntegration(TypedDict, total=False):
    """A known integration not yet discovered or configured."""

    domain: str
    name: str
    category: str
    discovery: str
    source: str
    brands: tuple[str, ...]
    notes: str


class NetworkDiscoveryResult(TypedDict):
    """Return type of discover_network_devices."""

    discovered: list[DiscoveredFlow]
    configured: list[ConfiguredIntegration]
    available: list[AvailableIntegration]
    active_initiated: list[dict[str, Any]]
    summary: NetworkDiscoverySummary


class NetworkDiscoverySummary(TypedDict):
    """Summary counts from network discovery."""

    discovered_count: int
    configured_count: int
    available_count: int
    active_initiated_count: int


class FlowResult(TypedDict, total=False):
    """Normalized config flow result."""

    type: str
    flow_id: str
    step_id: str
    title: str
    description_placeholders: dict[str, str]
    errors: dict[str, str]
    entry_id: str


class AutoSetupResult(TypedDict):
    """Return type of auto_setup_discovered."""

    accepted: list[dict[str, str]]
    skipped: list[dict[str, str]]
    failed: list[dict[str, str]]


class AreaAssignmentResult(TypedDict):
    """Return type of auto_assign_areas."""

    assigned: list[dict[str, str]]


class ResetResult(TypedDict):
    """Return type of reset_integrations."""

    removed_integrations: list[str]


class CleanupResult(TypedDict):
    """Return type of cleanup_mirror_devices."""

    removed_devices: list[str]
    removed_entities: list[str]


# ── Chat / LLM response structures ───────────────────────────────────


class ArchitectResponse(TypedDict, total=False):
    """Structured response from the LLM architect chat."""

    intent: Required[
        str
    ]  # "command" | "automation" | "clarification" | "answer" | "delayed_command" | "cancel" | "scene"
    response: Required[str]
    automation: AutomationDict
    automation_yaml: str
    description: str
    risk_assessment: RiskAssessment
    calls: list[ServiceCallDict]
    error: str
    config_issue: bool
    validation_error: str
    tool_calls: list[ToolCallLog]
    # Delayed/scheduled command fields
    delay_seconds: int | float
    scheduled_time: str
    schedule_id: str


class ServiceCallDict(TypedDict, total=False):
    """An HA service call from a command intent."""

    service: str
    target: dict[str, Any]
    data: dict[str, Any]


class ToolCallLog(TypedDict):
    """Log entry for a tool call during chat."""

    tool: str
    arguments: dict[str, Any]


# ── Entity snapshot structures ────────────────────────────────────────


class EntitySnapshot(TypedDict, total=False):
    """An entity state as included in home snapshots."""

    entity_id: str
    state: str
    area_name: str
    attributes: dict[str, Any]


class HomeSnapshot(TypedDict, total=False):
    """The home data snapshot sent to the LLM for analysis."""

    devices: list[dict[str, Any]]
    entity_states: list[EntitySnapshot]
    automations: list[dict[str, Any]]
    recorder_history: list[dict[str, Any]]
    _feedback_summary: str


# ── MCP Token structures ─────────────────────────────────────────────


class MCPTokenMeta(TypedDict, total=False):
    """Metadata for a stored MCP token (excludes the hash)."""

    id: str
    name: str
    token_hash: str
    token_prefix: str
    permission_level: str  # "read_only" | "admin" | "custom"
    allowed_tools: list[str] | None
    created_at: str
    last_used_at: str | None
    expires_at: str | None
    created_by_user_id: str


class DeletedHashEntry(TypedDict):
    """A stored deletion hash for suppressing re-suggestions."""

    hash: str
    alias: str
    deleted_at: str


# ── Analytics query results ──────────────────────────────────────────


class EntityActivity(TypedDict):
    """A single entity's activity ranking entry."""

    entity_id: str
    change_count: int
    active_days: int
    domain: str


class UsageWindow(TypedDict):
    """Hourly usage bucket for a single entity."""

    hour: int
    count: int
    primary_state: str


StateTransitionCount = TypedDict(
    "StateTransitionCount",
    {"from": str, "to": str, "count": int},
)


class EntityAnalytics(TypedDict):
    """Per-entity analytics response (usage windows + transitions)."""

    entity_id: str
    usage_windows: list[UsageWindow]
    state_transitions: list[StateTransitionCount]


class AnalyticsSummary(TypedDict):
    """Home-wide analytics summary."""

    total_entities_tracked: int
    total_state_changes: int
    most_active: list[EntityActivity]
    busiest_hour: int | None
    tracking_since: str | None


# ── Pattern Store top-level data ──────────────────────────────────────


class PatternStoreData(TypedDict):
    """Top-level data layout for PatternStore."""

    state_history: dict[str, list[StateChange]]
    patterns: dict[str, PatternDict]
    suggestions: dict[str, SuggestionDict]
    deleted_hashes: dict[str, DeletedHashEntry]
    meta: dict[str, str]


# ── Automation Store top-level data ───────────────────────────────────


class AutomationStoreData(TypedDict):
    """Top-level data layout for AutomationStore."""

    records: dict[str, AutomationRecord]
    session_index: dict[str, list[str]]
    drafts: dict[str, DraftAutomation]


# ── History summary ────────────────────────────────────────────────────


class TopState(TypedDict):
    """A state and its occurrence count."""

    state: str
    count: int


class HistorySummary(TypedDict):
    """Per-entity history summary for the automations tab."""

    entity_id: str
    change_count: int
    active_days: int
    first_seen: str | None
    last_seen: str | None
    top_states: list[TopState]


# ── Stale automation ──────────────────────────────────────────────────


class StaleAutomation(TypedDict):
    """A stale Selora automation entry."""

    automation_id: str
    entity_id: str
    alias: str
    last_triggered: str | None


# ── Feedback summary ──────────────────────────────────────────────────


class FeedbackSummary(TypedDict):
    """User accept/decline feedback for the LLM prompt."""

    accepted: list[dict[str, str]]
    declined: list[dict[str, str]]


# ── Conversation / session structures ─────────────────────────────────


class SessionSummary(TypedDict):
    """Session metadata without messages."""

    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int


class ChatMessage(TypedDict, total=False):
    """A single chat message in a session."""

    role: Required[str]  # "user" | "assistant"
    content: Required[str]
    timestamp: str
    automation: AutomationDict
    automation_yaml: str
    description: str
    automation_status: str
    intent: str
    calls: list[ServiceCallDict]
    automation_id: str
    risk_assessment: RiskAssessment
    tool_calls: list[ToolCallLog]
    devices: list[dict[str, Any]]
    scene: ScenePayload
    scene_yaml: str
    scene_id: str


class SessionData(TypedDict):
    """A full chat session with messages."""

    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[ChatMessage]
