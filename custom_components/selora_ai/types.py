"""Shared type definitions for the Selora AI integration.

Provides TypedDict classes for the core data structures that flow between
modules.  Using these instead of ``dict[str, Any]`` enables IDE autocompletion,
catches key typos at type-check time, and documents the expected shape of data.

Convention: all new code must import from here rather than using bare
``dict[str, Any]`` for these structures.
"""

from __future__ import annotations

from typing import Any, NotRequired, Required, TypedDict

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


class AutomationCreateResult(TypedDict, total=False):
    """Return type of async_create_automation."""

    success: bool
    automation_id: str | None
    risk_level: str
    forced_disabled: bool


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


class RecorderHistoryRecord(TypedDict):
    """A single recorder state-change record as the collector materialises it."""

    entity_id: str
    state: str
    last_changed: str | None


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
    status: str  # active | dismissed | snoozed | accepted | rejected | quality_rejected
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


# ── Chat attachments ──────────────────────────────────────────────────


class ImageAttachment(TypedDict):
    """A base64-encoded image the user attached to a chat message.

    ``data`` is the raw base64 payload (no ``data:`` URL prefix);
    ``mime_type`` is one of ``CHAT_ATTACHMENT_MIME_TYPES`` in const.py.
    Attachments ride the current turn only — they are never persisted to
    the conversation store or replayed in history.
    """

    mime_type: str
    data: str


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
    # Backing model reported by the response, used when the provider itself
    # has no fixed model (e.g. a gateway that routes server-side). Falls back
    # to the provider's configured model when absent.
    model: str


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


class QuickAction(TypedDict, total=False):
    """A clickable action offered to the user after an AI message.

    Modes:
      - "suggestion": full-width chip (empty-state quick start)
      - "choice": grid card with title + description (AI-suggested options)
      - "confirmation": inline button row (Apply / Modify / Cancel)
    """

    label: Required[str]
    value: str  # text sent as user message when selected
    mode: str  # "suggestion" | "choice" | "confirmation"
    description: str  # subtitle for choice cards
    icon: str  # mdi icon name
    primary: bool  # highlight as primary action (confirmation mode)


class CommandApprovalProposal(TypedDict, total=False):
    """A pending command waiting on user approval.

    Created by ``command_policy.apply_command_policy`` when one or more
    proposed calls fall in the REVIEW bucket (e.g. ``tts.*``, ``notify.*``,
    ``script.*``, ``lock.unlock``). The chat handler persists this on the
    assistant message; the user grants Once / Session / Always — or Deny —
    and the chat WS handler then executes the calls.
    """

    proposal_id: Required[str]  # uuid, stable across grant scopes
    risk_level: Required[str]  # "low" | "medium" | "high"
    risk_reasons: list[str]  # human-readable reasons displayed above the card
    calls: Required[list[ServiceCallDict]]  # validated service calls to run on approval
    # Scheduling metadata carried through when the LLM proposed a
    # ``delayed_command`` that needed approval. ``original_intent``
    # is "delayed_command"; the resolver dispatches via the
    # ScheduledTaskTracker instead of running calls immediately.
    original_intent: str
    delay_seconds: int | float
    scheduled_time: str


class ArchitectResponse(TypedDict, total=False):
    """Structured response from the LLM architect chat."""

    intent: Required[
        str
    ]  # "command" | "automation" | "clarification" | "answer" | "delayed_command" | "cancel" | "scene" | "command_approval"
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
    quick_actions: list[QuickAction]
    # Delayed/scheduled command fields
    delay_seconds: int | float
    scheduled_time: str
    schedule_id: str
    # Approval flow (set when intent == "command_approval")
    command_approval: CommandApprovalProposal


class ServiceCallDict(TypedDict, total=False):
    """An HA service call from a command intent."""

    service: str
    target: dict[str, Any]
    data: dict[str, Any]


class EntityStateSnapshot(TypedDict):
    """Post-execution entity state captured by ``_tool_execute_command``."""

    entity_id: str
    state: str


class OpenAIChatPayload(TypedDict, total=False):
    """Request body for OpenAI-compatible chat-completion endpoints.

    Used by ``OpenAICompatibleProvider`` and the OpenRouter / Selora
    Cloud subclasses. ``messages`` items vary by role (system / user /
    assistant / tool) and may carry ``tool_calls`` / ``tool_call_id``;
    typing them precisely would explode for marginal gain, so they're
    left as ``dict[str, Any]`` inside the list. The rest of the
    request body is enumerated so the build_payload chain has a
    stable contract.
    """

    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    tool_choice: Any  # "auto" | "required" | "none" | {"type":"function",...}
    stream: bool
    stream_options: dict[str, Any]
    max_tokens: int
    temperature: float
    reasoning: dict[str, Any]
    provider: dict[str, Any]  # OpenRouter routing prefs (e.g. {"sort": "latency"})


class ToolWriteResult(TypedDict, total=False):
    """Return shape for write-action tools (execute_command,
    activate_scene). Used by the tool-loop short-circuit and the
    duplicate-execution guard to detect successful side-effecting
    calls without poking at ``dict[str, Any]``.

    ``execute_command`` populates ``executed``/``service``/``entity_ids``/
    ``states`` on success and ``executed``/``error`` on failure;
    ``activate_scene`` populates ``status: "activated"`` and the
    resolved ``entity_id``. ``requires_approval`` is set by both when
    the validator held the call back pending user approval.
    """

    # execute_command success
    executed: bool
    service: str
    entity_ids: list[str]
    states: list[EntityStateSnapshot]
    # activate_scene success
    entity_id: str
    status: str
    # validator output (rejection / approval gate)
    valid: bool
    errors: list[str]
    requires_approval: bool
    risk_level: str
    approval_reason: str
    # failure path
    error: str


class ToolCallLog(TypedDict, total=False):
    """Log entry for a tool call during chat.

    ``result`` is optional and present when the dispatcher recorded the
    handler's return value (used by the duplicate-execution guard to
    distinguish actual service execution from validation/exec failures).
    """

    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any]


# ── Entity snapshot structures ────────────────────────────────────────


class EntitySnapshot(TypedDict, total=False):
    """An entity state as included in home snapshots."""

    entity_id: str
    state: str
    area_name: str
    manufacturer: str
    model: str
    platform: str
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
    # Concatenated message contents (capped) for client-side sidebar search.
    search_text: str


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
    # Agent-activity timeline entries (the "what's happening" steps). See
    # agent_steps.AgentStep — kept as plain dicts here to avoid a cross-module
    # import cycle; the websocket layer owns the canonical shape.
    steps: list[dict[str, Any]]
    devices: list[dict[str, Any]]
    scene: ScenePayload
    scene_yaml: str
    scene_id: str
    scene_status: str  # "pending" | "saved" | "declined" | "refining"
    quick_actions: list[QuickAction]
    command_approval: CommandApprovalProposal
    # "pending" | "approved" | "denied" | "expired" — set on assistant messages
    # whose intent is "command_approval". The chat WS handler updates this
    # when the user clicks one of the approval-row buttons.
    approval_status: str


class SessionData(TypedDict):
    """A full chat session with messages."""

    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[ChatMessage]


# ── Insights: Layer 1 (health signals) ────────────────────────────────


class HealthSignal(TypedDict):
    """A single deduplicated health observation about a device/entity/integration.

    Produced by ``health_monitor`` (Layer 1), persisted in ``health_store``.
    Deterministic, LLM-free, and safe to hand to the Selora OS host. The
    ``signal_id`` is derived from ``(kind, target)`` so re-detection of the
    same problem upserts one record and bumps ``count`` rather than piling up
    duplicates.
    """

    signal_id: str
    kind: str  # unavailable|flapping|silent|stale|battery_low|integration_error
    severity: str  # info|warning|critical
    target: str  # entity_id | device_id | integration domain
    target_kind: str  # "entity" | "device" | "integration"
    device_id: NotRequired[str | None]  # entity target's device; None otherwise
    area_name: str
    evidence: dict[str, Any]
    first_seen: str  # ISO-8601
    last_seen: str  # ISO-8601
    count: int
    status: str  # active|resolved|acknowledged


class HealthStoreData(TypedDict):
    """Persisted shape of ``health_store``."""

    signals: dict[str, HealthSignal]
    meta: dict[str, Any]  # export_sequence, last_scan, ...


# ── Insights: Layer 2 (advisor) ───────────────────────────────────────


class Insight(TypedDict):
    """A user-facing insight: a reported issue, a suggested fix, or an
    improvement idea. Produced by ``insights`` (Layer 2) from health signals,
    detected patterns, and the home snapshot.
    """

    insight_id: str
    kind: str  # "issue" | "fix" | "improvement"
    severity: str  # info|warning|critical
    title: str
    detail: str
    linked_signals: list[str]  # HealthSignal.signal_id values
    suggested_action: dict[str, Any]  # automation payload / config action / doc link
    source: str  # "deterministic" | "llm"
    created_at: str  # ISO-8601
    status: str  # new|acknowledged|resolved|dismissed


class Finding(TypedDict, total=False):
    """A deterministic Insights check result, rendered as an audit card.

    Produced by ``insights_checks`` with NO model involvement — the message is
    templated and the entities are exact ground truth (contrast the retired
    free-form LLM audit, which speculated). Field names match the audit
    recommendation card so findings render through the existing surface.
    """

    check_id: str  # which check produced it (e.g. "duplicate_automations")
    severity: str  # critical | warning | info
    category: str  # issue | fix | improvement
    title: str
    detail: str
    entities: list[str]  # clickable entity_ids, exact
    action: str | None  # short imperative, or None
    device_id: str | None  # device this concerns (enables per-device Ignore)
    link: str  # optional deep-link URL (e.g. an integration's Settings page)
    link_label: str  # label for ``link``


class CheckResult(TypedDict):
    """One check's outcome, so the panel can show the full assessment — every
    check that ran, whether it's clear, and what it found."""

    check_id: str
    title: str  # what the check assesses, user-facing
    kind: str  # "deterministic" | "model"
    status: str  # "clear" | "issues"
    findings: list[Finding]


# ── Insights: export handoff to the Selora OS host ────────────────────


class InsightsArtifactRef(TypedDict):
    """Pointer + integrity metadata for the immutable export artifact."""

    path: str  # relative to the manifest directory
    sha256: str
    size_bytes: int
    encoding: str  # "gzip" | "none"


class InsightsExportManifest(TypedDict):
    """The atomic pointer the host reads first. Written last (commit point).

    Answers, for the host: *which file, is it OK, what schema, is it fresh.*
    """

    schema_version: int
    sequence: int
    status: str  # "complete" | "partial"
    generated_at: str  # ISO-8601 (VM clock)
    cadence_seconds: int
    next_expected_at: str  # ISO-8601 (VM clock)
    artifact: InsightsArtifactRef
    summary: dict[str, int]
    producer: dict[str, str]  # integration_version, ha_version, installation_id?
    collection: dict[str, Any]  # status, partial_reason


class InsightsEnvelope(TypedDict):
    """The full payload inside the gzipped artifact — both layers + context."""

    schema_version: int
    sequence: int
    generated_at: str
    signals: list[HealthSignal]
    insights: list[Insight]
    inventory: dict[str, int]
    roster: HomeRoster
    collection: dict[str, Any]


# ── Insights: the full home roster (schema v2) ────────────────────────
# A complete "what's running, what's not" inventory for the Selora OS host /
# Connect. Unlike the anonymous PostHog telemetry (counts only), this is the
# user's own home going to their own account, keyed by installation_id — so it
# carries identities + state. SAFE_ATTRIBUTES filtering still applies.


class RosterIntegration(TypedDict):
    """One configured integration and whether it loaded."""

    domain: str
    name: str  # human manifest name (e.g. "National Weather Service (NWS)")
    title: str
    state: str  # loaded | setup_error | setup_retry | not_loaded | migration_error
    devices: int
    entities: int
    has_issue: bool  # an active HA repair issue targets this domain
    custom: bool  # custom component (not built into Home Assistant)
    url: str  # manifest documentation URL (e.g. integration docs page), "" if unknown


class RosterDevice(TypedDict):
    """One device and its availability rollup."""

    id: str
    name: str
    manufacturer: str
    model: str
    area: str
    integration: str
    disabled: bool
    entities: int
    unavailable_entities: int  # ENABLED, visible entities with no usable state
    disabled_entities: int  # intentionally-off entities (neutral, not broken)
    url: str  # device configuration_url (e.g. add-on homepage), "" if none


class RosterEntity(TypedDict):
    """One entity with its current state and availability."""

    entity_id: str
    name: str
    domain: str
    device_class: str
    area: str
    state: str
    available: bool  # False when unavailable/unknown/disabled/no-state
    last_changed: str | None
    disabled: bool
    hidden: bool
    device_id: str | None  # device this entity belongs to, or None


class RosterAutomation(TypedDict):
    """One automation and whether it's running."""

    entity_id: str
    name: str
    enabled: bool
    selora: bool  # Selora-generated (by id prefix / alias)
    last_triggered: str | None


class RosterScript(TypedDict):
    """One script entity."""

    entity_id: str
    name: str
    state: str
    last_triggered: str | None


class RosterScene(TypedDict):
    """One scene entity."""

    entity_id: str
    name: str


class HomeRoster(TypedDict):
    """The complete device-plane inventory shipped in the export envelope."""

    integrations: list[RosterIntegration]
    devices: list[RosterDevice]
    entities: list[RosterEntity]
    automations: list[RosterAutomation]
    scripts: list[RosterScript]
    scenes: list[RosterScene]
    truncated: bool  # True when entity rows hit INSIGHTS_ROSTER_MAX_ENTITIES
    unavailable_total: int  # enabled, visible entities with no usable state
    disabled_total: int  # intentionally-off entities across the home
