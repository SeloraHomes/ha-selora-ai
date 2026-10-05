"""MCP tools for Selora's chat, sessions, patterns and suggestions."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant

from ..const import (
    DOMAIN,
)
from .automations import _tool_create_automation
from .common import (
    _get_conv_store,
    _get_llm,
    _is_selora,
    _read_yaml_automations,
    _sanitize,
    _sanitize_risk,
)

if TYPE_CHECKING:
    from .. import ConversationStore
    from ..collector import DataCollector
    from ..llm_client import LLMClient
    from ..types import (
        ArchitectResponse,
        AutomationDict,
        RiskAssessment,
    )


_LOGGER = logging.getLogger(__name__)


# ── Tool: selora_chat ─────────────────────────────────────────────────────────


async def _refining_context_for(
    hass: HomeAssistant,
    automation_id: str,
) -> tuple[tuple[str, str] | None, str | None]:
    """Return ((alias, yaml), None), or (None, error) when it cannot be refined.

    Shapes ``refine_automation_id`` into the ACTIVE REFINEMENT block the
    architect prompt understands. The YAML comes off automations.yaml, so the
    model edits what the home is actually running rather than whatever this
    session last said about it. ``id`` is dropped — the write path owns it, and
    a model that echoes it back would have it stripped anyway.

    A non-Selora automation is refused HERE. A refinement can leave a card the
    user accepts in the panel, and the panel saves through the proposal path —
    the one that reshapes what it accepts and so must not touch a user's
    automation. The caller is pointed at the direct route instead: read it with
    ``selora_get_automation``, then replace it with ``selora_create_automation``,
    which validates a user's automation with Home Assistant's own validator.
    """
    import yaml as _yaml  # noqa: PLC0415

    for entry in await _read_yaml_automations(hass):
        if not isinstance(entry, dict) or str(entry.get("id", "")) != automation_id:
            continue
        if not _is_selora(entry):
            return None, (
                f"Automation {_sanitize(automation_id)} was not created by Selora AI, so "
                "it is not refined through chat. Edit it directly: read it with "
                "selora_get_automation, change the YAML, and pass it to "
                "selora_create_automation with automation_id to replace it."
            )
        alias = _sanitize(str(entry.get("alias", "")) or automation_id, limit=100)
        body = {k: v for k, v in entry.items() if k != "id"}
        return (
            alias,
            _yaml.dump(body, allow_unicode=True, default_flow_style=False),
        ), None
    return None, f"Automation {_sanitize(automation_id)} not found"


async def _tool_chat(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Send a message to Selora's LLM and return the response.

    This is the primary suspension point for the external agent in the
    Coroutine Synthesis pattern: the external agent yields here and Selora's
    LLM advances the automation artifact using home-grounded generation.
    """

    message: str = str(arguments.get("message", "")).strip()
    if not message:
        return {"error": "message is required"}

    session_id: str | None = arguments.get("session_id")
    refine_automation_id: str | None = arguments.get("refine_automation_id")

    llm: LLMClient | None = _get_llm(hass)
    if llm is None:
        return {"error": "Selora AI LLM is not configured"}

    # The refine target is an automation that EXISTS in the home, named by the
    # id ``selora_list_automations`` / ``selora_create_automation`` hand back —
    # a proposal this tool returns has none yet. Refusing an unresolvable id is
    # the point: ignoring it silently turns "change the time to 7am" into a
    # brand-new automation alongside the one the caller meant to edit.
    #
    # Checked before a session can exist, because this is the refusal an agent
    # hits repeatedly — a stale or invented id — and a session created first is
    # left empty in the user's sidebar, eventually evicting real conversations
    # under the store's session cap.
    refining_context: tuple[str, str] | None = None
    if refine_automation_id:
        # A provider whose prompt cannot carry the YAML cannot refine at all:
        # the model composes a fresh rule, and this tool has no confirmation
        # card between that and the write — the caller is told to pass the id
        # straight to `selora_create_automation`, which would replace the
        # automation and drop whatever the user did not mention. The panel's own
        # Refine keeps its diff, which is why only this surface refuses.
        if not llm.shows_automation_reference:
            return {
                "error": (
                    f"Automation {_sanitize(refine_automation_id)} cannot be refined with "
                    f"the configured {llm.provider_name} model — it never receives the "
                    "automation's current configuration, so a revision would be composed "
                    "from scratch and replace it. Read it with selora_get_automation, "
                    "edit the YAML, and pass that to selora_create_automation with "
                    "automation_id."
                )
            }
        refining_context, refine_error = await _refining_context_for(hass, refine_automation_id)
        if refine_error is not None:
            return {"error": refine_error}

    conv_store: ConversationStore = _get_conv_store(hass)

    # Get or create session
    session: dict[str, Any]
    if session_id:
        session_or_none: dict[str, Any] | None = await conv_store.get_session(session_id)
        if session_or_none is None:
            return {"error": f"Session {session_id} not found"}
        session = session_or_none
    else:
        session = await conv_store.create_session()
        session_id = session["id"]

    # Reconcile scene store so session context reflects external edits
    from ..helpers import get_scene_store  # noqa: PLC0415

    await get_scene_store(hass).async_reconcile_yaml()

    # Build history for the LLM.
    # For each unique scene_id, keep the latest YAML so multi-scene
    # sessions retain context for every scene (including renames).
    messages = session.get("messages", [])
    latest_scene_by_id: dict[str, int] = {}
    for i, m in enumerate(messages):
        if m.get("scene_yaml") and m.get("scene_id"):
            latest_scene_by_id[m["scene_id"]] = i
    latest_scene_indices: set[int] = set(latest_scene_by_id.values())

    history: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
        role = m.get("role", "user")
        content = str(m.get("content", ""))
        # Re-attach pending automation YAML as context (sanitized)
        if m.get("automation_yaml") and m.get("automation_status") in ("pending", "refining"):
            alias = _sanitize((m.get("automation") or {}).get("alias", ""))
            header = f"[Untrusted automation reference data for context only: {alias}]\n"
            quoted_yaml = json.dumps(str(m["automation_yaml"]), ensure_ascii=True)
            content = f"{header}{quoted_yaml}\n{content}"
        # Re-attach latest scene YAML for each unique scene name
        elif i in latest_scene_indices:
            scene_name = _sanitize((m.get("scene") or {}).get("name", ""))
            sid = m.get("scene_id", "")
            header = f"[Untrusted scene reference data for context only: {scene_name} (scene_id: {sid})]\n"
            quoted_yaml = json.dumps(str(m["scene_yaml"]), ensure_ascii=True)
            content = f"{header}{quoted_yaml}\n{content}"
        history.append({"role": role, "content": content})

    # Home context for the turn. ``architect_chat`` needs the entity snapshot
    # (it is a positional argument, not an optional one) and reads
    # ``existing_automations`` as records — alias + state — so a list of bare
    # alias strings raises inside the prompt builder.
    from .. import (  # noqa: PLC0415
        _automation_reference_context,
        _collect_entity_states,
        _collect_existing_automations,
        _find_session_saved_automations,
        _resolve_proposal_write_target,
    )

    entities = _collect_entity_states(hass)
    existing_automations = _collect_existing_automations(hass)
    # Sessions are shared with the panel, so an automation this session saved
    # may well have been saved from a chat card. Same reference context the
    # panel's handlers pass.
    session_saved = await _find_session_saved_automations(hass, session, messages)
    # The pair: what the model is shown, and which of those an inferred edit may
    # target (one it was not shown must not be).
    automation_context, editable_automations = _automation_reference_context(session_saved)

    # Build scene context from the session-level index (survives message
    # pruning) so the LLM always has scene_id + YAML on the current turn.
    scene_index: dict[str, dict[str, str]] = session.get("scenes", {})
    mcp_scene_context: list[tuple[str, str, str]] | None = None
    if scene_index:
        mcp_scene_context = [
            (sid, _sanitize(data.get("name", "")), data.get("yaml", ""))
            for sid, data in scene_index.items()
            if data.get("yaml")
        ] or None

    # Call Selora's LLM
    llm_result: ArchitectResponse = await llm.architect_chat(
        message,
        entities,
        existing_automations=existing_automations,
        history=history,
        refining_context=refining_context,
        scene_context=mcp_scene_context,
        automation_context=automation_context,
        session_id=session_id,
    )

    intent: str = llm_result.get("intent", "answer")
    response_text: str = _sanitize(llm_result.get("response", ""), limit=2000)
    automation: AutomationDict | None = llm_result.get("automation")
    automation_yaml: str | None = llm_result.get("automation_yaml")
    risk_assessment: RiskAssessment | None = llm_result.get("risk_assessment")

    # The LLM includes refine_scene_id when modifying an existing scene.
    # Collect scene IDs known to this session so the validator can reject
    # hallucinated IDs that don't belong to the conversation.  Include
    # the session-level index (survives pruning) and message-level IDs.
    mcp_session_scene_ids: set[str] = set(scene_index.keys()) | {
        m["scene_id"] for m in messages if m.get("scene_id")
    }
    scene_result: dict[str, Any] | None = None
    if intent == "scene" and llm_result.get("scene"):
        try:
            from ..scene_utils import async_create_scene  # noqa: PLC0415

            scene_result = await async_create_scene(
                hass,
                llm_result["scene"],
                existing_scene_id=llm_result.get("refine_scene_id"),
                session_scene_ids=mcp_session_scene_ids,
            )
        except Exception as exc:  # noqa: BLE001 — HA service handlers may raise beyond HA's hierarchy
            _LOGGER.error("Failed to create scene via MCP: %s", exc)
            response_text += f" (Scene creation failed: {exc})"
            # Clear scene metadata so the caller doesn't think a scene was created
            llm_result.pop("scene", None)
            llm_result.pop("scene_yaml", None)
            intent = "answer"

        if scene_result is not None:
            try:
                from ..helpers import get_scene_store  # noqa: PLC0415

                scene_store = get_scene_store(hass)
                await scene_store.async_add_scene(
                    scene_result["scene_id"],
                    scene_result["name"],
                    scene_result["entity_count"],
                    session_id=session_id,
                    entity_id=scene_result.get("entity_id"),
                    content_hash=scene_result.get("content_hash"),
                )
            except Exception:  # noqa: BLE001 — store failure doesn't invalidate the created scene
                _LOGGER.warning("Failed to record scene %s in store", scene_result["scene_id"])

    # Which automation the returned YAML is meant to REPLACE, if any. The
    # proposal itself never carries an id — ``validate_automation_payload``
    # strips it and ``async_create_automation`` mints its own — so a caller
    # that took the payload's id as a write target would always create. The
    # target is the id the caller asked to refine, or the one the model named
    # off the session reference context, and it routes the caller to
    # ``selora_create_automation``'s ``automation_id`` instead of a second
    # automation under the same alias.
    #
    # Resolved BEFORE the append and persisted with the proposal, because
    # sessions are shared with the panel: a card this tool leaves pending is
    # one the user can open and accept there, and the panel reads the target
    # off the stored message. Same rule as the websocket handlers.
    refine_target: str | None = None
    if automation and automation_yaml:
        # The caller's own id is an explicit instruction and outranks anything
        # inferred. Failing that, the SHARED resolver — claim validated against
        # the session, then the alias — so an MCP follow-up that keeps the
        # automation's name resolves like a panel one instead of falling
        # through to a create. Restating either arm here is how the two
        # surfaces drift.
        # The caller's own id is an explicit instruction and outranks anything
        # inferred. Inference additionally requires that the model was shown
        # the automation, or it would be handed a rule composed without it.
        refine_target = refine_automation_id or (
            _resolve_proposal_write_target(
                automation, editable_automations, llm_result.get("refine_automation_id")
            )
            if llm.shows_automation_reference
            else None
        )

    # Persist messages
    await conv_store.append_message(session_id, "user", message)
    await conv_store.append_message(
        session_id,
        "assistant",
        response_text,
        automation=automation,
        automation_yaml=automation_yaml,
        automation_status="pending" if automation else None,
        refining_automation_id=refine_target,
        risk_assessment=risk_assessment,
        scene=scene_result["scene"] if scene_result else llm_result.get("scene"),
        scene_yaml=scene_result["scene_yaml"] if scene_result else llm_result.get("scene_yaml"),
        scene_id=scene_result["scene_id"] if scene_result else None,
    )

    # Generate session title if this is the first exchange
    if len(session.get("messages", [])) == 0 and llm:
        try:
            title = await llm.generate_session_title(message, response_text)
            if title:
                await conv_store.update_session_title(session_id, _sanitize(title))
        except Exception as exc:
            _LOGGER.debug("Session title generation failed for %s: %s", session_id, exc)

    response: dict[str, Any] = {
        "response": response_text,
        "intent": intent,
        "session_id": session_id,
    }
    if automation_yaml:
        response["automation_yaml"] = automation_yaml
    if refine_target:
        response["refine_automation_id"] = refine_target
    if risk_assessment:
        response["risk_assessment"] = _sanitize_risk(risk_assessment)
    if scene_result:
        response["scene_id"] = scene_result["scene_id"]
        response["scene_name"] = scene_result["name"]

    return response


# ── Tool: selora_list_sessions ────────────────────────────────────────────────


async def _tool_list_sessions(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Return recent conversation sessions (title + id, no messages)."""
    conv_store: ConversationStore = _get_conv_store(hass)
    sessions: list[dict[str, Any]] = await conv_store.list_sessions()
    return [
        {
            "session_id": s["id"],
            "title": _sanitize(s.get("title", "Untitled")),
            "updated_at": s.get("updated_at", ""),
            "message_count": s.get("message_count", 0),
        }
        for s in sessions
    ]


def _find_collector(hass: HomeAssistant) -> DataCollector | None:
    domain_data = hass.data.get(DOMAIN, {})
    for key, value in domain_data.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and "collector" in value:
            return value["collector"]
    return None


# Cap the in-memory suggestion-status overlay. One key is minted per
# suggestion digest ever seen, so without a bound it grows for the life of
# the process. Active suggestions are re-touched on status change and
# survive eviction longest; evicted stale entries simply fall back to
# "pending" if their suggestion ever reappears.
_MCP_SUGGESTION_STATUS_MAX = 500


def _get_suggestion_status_store(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    domain_data = hass.data.setdefault(DOMAIN, {})
    return domain_data.setdefault("_mcp_suggestion_status", {})


def _set_suggestion_status(
    status_store: dict[str, dict[str, Any]],
    suggestion_id: str,
    entry: dict[str, Any],
) -> None:
    """Insert/refresh a status entry, evicting the oldest (insertion-order)
    entries once the overlay exceeds its cap. Re-inserting moves the key to
    the end so a just-decided suggestion is the last thing evicted."""
    status_store.pop(suggestion_id, None)
    status_store[suggestion_id] = entry
    while len(status_store) > _MCP_SUGGESTION_STATUS_MAX:
        del status_store[next(iter(status_store))]


def _collect_entity_ids(value: Any) -> list[str]:
    from ..helpers import collect_entity_ids

    return sorted(collect_entity_ids(value))


def _suggestion_identity(raw: dict[str, Any], index: int) -> tuple[str, str]:
    automation_yaml: str = str(raw.get("automation_yaml", ""))
    alias: str = str(raw.get("alias", ""))
    digest_source: str = automation_yaml or json.dumps(
        {
            "alias": alias,
            "trigger": raw.get("trigger"),
            "triggers": raw.get("triggers"),
            "action": raw.get("action"),
            "actions": raw.get("actions"),
            "index": index,
        },
        sort_keys=True,
        default=str,
    )
    digest: str = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:16]
    return f"sugg_{digest}", f"pattern_{digest}"


def _normalize_suggestion(
    raw: dict[str, Any], *, index: int, status_store: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    suggestion_id: str
    fallback_pattern_id: str
    suggestion_id, fallback_pattern_id = _suggestion_identity(raw, index)
    persisted: dict[str, Any] = status_store.get(suggestion_id, {})
    status: str = persisted.get("status", "pending")
    created_at: str = (
        persisted.get("created_at") or raw.get("created_at") or datetime.now(UTC).isoformat()
    )
    automation_yaml: str = str(raw.get("automation_yaml", ""))
    description: str = _sanitize(raw.get("description", raw.get("alias", "")), limit=400)
    confidence_raw: Any = raw.get("confidence", 0.7)
    try:
        confidence: float = max(0.0, min(1.0, float(confidence_raw)))
    except (
        TypeError,
        ValueError,
    ):
        confidence = 0.7

    entity_ids: list[str] = _collect_entity_ids(raw.get("automation_data") or raw)
    evidence_summary: str = _sanitize(
        raw.get("evidence_summary") or raw.get("evidence") or description,
        limit=500,
    )

    risk_assessment: dict[str, Any] | None = raw.get("risk_assessment")
    risk: RiskAssessment
    if isinstance(risk_assessment, dict):
        risk = _sanitize_risk(risk_assessment)
    else:
        risk = {
            "level": "normal",
            "flags": [],
            "reasons": [],
            "scrutiny_tags": [],
            "summary": "No risk assessment available.",
        }

    suggestion: dict[str, Any] = {
        "suggestion_id": suggestion_id,
        "pattern_id": str(raw.get("pattern_id") or fallback_pattern_id),
        "description": description,
        "confidence": confidence,
        "automation_yaml": automation_yaml,
        "evidence_summary": evidence_summary,
        "risk_assessment": risk,
        "status": status,
        "created_at": created_at,
        "entity_ids": entity_ids,
    }

    if suggestion_id not in status_store:
        _set_suggestion_status(
            status_store,
            suggestion_id,
            {"status": status, "created_at": created_at},
        )

    return suggestion


async def _phase2_suggestions(hass: HomeAssistant) -> list[dict[str, Any]]:
    raw_items: list[Any] = hass.data.get(DOMAIN, {}).get("latest_suggestions", [])
    status_store: dict[str, dict[str, Any]] = _get_suggestion_status_store(hass)
    results: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        results.append(_normalize_suggestion(raw, index=index, status_store=status_store))
    return results


async def _tool_list_suggestions(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> list[dict[str, Any]]:
    status_filter: str = str(arguments.get("status", "")).strip()
    suggestions: list[dict[str, Any]] = await _phase2_suggestions(hass)
    if status_filter:
        suggestions = [s for s in suggestions if s.get("status") == status_filter]
    return [
        {
            "suggestion_id": s["suggestion_id"],
            "pattern_id": s["pattern_id"],
            "description": s["description"],
            "confidence": s["confidence"],
            "automation_yaml": s["automation_yaml"],
            "evidence_summary": s["evidence_summary"],
            "risk_assessment": s["risk_assessment"],
            "status": s["status"],
            "created_at": s["created_at"],
        }
        for s in suggestions
    ]


async def _tool_list_patterns(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> list[dict[str, Any]]:
    type_filter: str = str(arguments.get("type", "")).strip()
    status_filter: str = str(arguments.get("status", "")).strip()
    min_confidence_raw: Any = arguments.get("min_confidence")
    min_confidence: float | None = None
    if min_confidence_raw is not None:
        try:
            min_confidence = float(min_confidence_raw)
        except (
            TypeError,
            ValueError,
        ):
            min_confidence = None

    suggestions: list[dict[str, Any]] = await _phase2_suggestions(hass)
    patterns: dict[str, dict[str, Any]] = {}
    status_rank: dict[str, int] = {
        "pending": 4,
        "active": 4,
        "accepted": 3,
        "snoozed": 2,
        "dismissed": 1,
    }

    for suggestion in suggestions:
        pattern_id = suggestion["pattern_id"]
        pattern_type = "correlation"
        suggestion_status = str(suggestion.get("status", "pending"))
        if suggestion_status == "pending":
            suggestion_status = "active"
        if pattern_id not in patterns:
            patterns[pattern_id] = {
                "pattern_id": pattern_id,
                "type": pattern_type,
                "description": suggestion["description"],
                "confidence": suggestion["confidence"],
                "entity_ids": list(suggestion.get("entity_ids", [])),
                "evidence": {
                    "evidence_summary": suggestion["evidence_summary"],
                    "suggestion_ids": [suggestion["suggestion_id"]],
                },
                "status": suggestion_status,
                "detected_at": suggestion["created_at"],
                "last_seen": suggestion["created_at"],
                "occurrence_count": 1,
            }
            continue

        current = patterns[pattern_id]
        current["occurrence_count"] += 1
        current["confidence"] = max(float(current["confidence"]), float(suggestion["confidence"]))
        current["entity_ids"] = sorted(
            set(current["entity_ids"]) | set(suggestion.get("entity_ids", []))
        )
        current["last_seen"] = max(str(current["last_seen"]), str(suggestion["created_at"]))
        current["evidence"]["suggestion_ids"].append(suggestion["suggestion_id"])

        current_rank = status_rank.get(str(current["status"]), 0)
        candidate_rank = status_rank.get(suggestion_status, 0)
        if candidate_rank > current_rank:
            current["status"] = suggestion_status

    result: list[dict[str, Any]] = list(patterns.values())

    if type_filter:
        result = [p for p in result if p.get("type") == type_filter]
    if status_filter:
        result = [p for p in result if p.get("status") == status_filter]
    if min_confidence is not None:
        result = [p for p in result if float(p.get("confidence", 0.0)) >= min_confidence]

    return result


async def _tool_get_pattern(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    pattern_id: str = str(arguments.get("pattern_id", "")).strip()
    if not pattern_id:
        return {"error": "pattern_id is required"}

    patterns: list[dict[str, Any]] = await _tool_list_patterns(hass, {})
    pattern: dict[str, Any] | None = next(
        (p for p in patterns if p.get("pattern_id") == pattern_id), None
    )
    if pattern is None:
        return {"error": f"Pattern {pattern_id} not found"}

    suggestions: list[dict[str, Any]] = await _phase2_suggestions(hass)
    linked: list[dict[str, Any]] = [
        {
            "suggestion_id": s["suggestion_id"],
            "description": s["description"],
            "status": s["status"],
            "confidence": s["confidence"],
            "created_at": s["created_at"],
        }
        for s in suggestions
        if s.get("pattern_id") == pattern_id
    ]

    return {
        **pattern,
        "suggestions": linked,
    }


async def _tool_accept_suggestion(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    suggestion_id: str = str(arguments.get("suggestion_id", "")).strip()
    enabled: bool = bool(arguments.get("enabled", False))
    if not suggestion_id:
        return {"error": "suggestion_id is required"}

    suggestions: list[dict[str, Any]] = await _phase2_suggestions(hass)
    target: dict[str, Any] | None = next(
        (s for s in suggestions if s.get("suggestion_id") == suggestion_id), None
    )
    if target is None:
        return {"error": f"Suggestion {suggestion_id} not found"}

    if not target.get("automation_yaml"):
        return {"error": "Suggestion does not include automation_yaml"}

    created: dict[str, Any] = await _tool_create_automation(
        hass,
        {
            "yaml": target["automation_yaml"],
            "enabled": enabled,
            "version_message": f"Created from suggestion {suggestion_id}",
        },
    )
    if "error" in created:
        return created

    status_store = _get_suggestion_status_store(hass)
    _set_suggestion_status(
        status_store,
        suggestion_id,
        {
            "status": "accepted",
            "created_at": status_store.get(suggestion_id, {}).get(
                "created_at", target["created_at"]
            ),
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )

    return {
        "suggestion_id": suggestion_id,
        "status": "accepted",
        "automation_id": created.get("automation_id", ""),
        "risk_assessment": target.get("risk_assessment"),
    }


async def _tool_dismiss_suggestion(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    suggestion_id: str = str(arguments.get("suggestion_id", "")).strip()
    reason: str = _sanitize(arguments.get("reason", ""), limit=300)
    if not suggestion_id:
        return {"error": "suggestion_id is required"}

    suggestions: list[dict[str, Any]] = await _phase2_suggestions(hass)
    target: dict[str, Any] | None = next(
        (s for s in suggestions if s.get("suggestion_id") == suggestion_id), None
    )
    if target is None:
        return {"error": f"Suggestion {suggestion_id} not found"}

    now_iso: str = datetime.now(UTC).isoformat()
    dismissal_reason: str = reason if reason else "user-declined"

    # Update in-memory status overlay (used by phase-2 suggestion rendering)
    status_store: dict[str, dict[str, Any]] = _get_suggestion_status_store(hass)
    _set_suggestion_status(
        status_store,
        suggestion_id,
        {
            "status": "dismissed",
            "reason": dismissal_reason,
            "created_at": status_store.get(suggestion_id, {}).get(
                "created_at", target["created_at"]
            ),
            "updated_at": now_iso,
        },
    )

    # Persist to PatternStore so dismissal survives HA restarts (#43)
    pattern_store = hass.data.get(DOMAIN, {}).get("pattern_store")
    if pattern_store is not None:
        await pattern_store.update_suggestion_status(
            suggestion_id,
            status="dismissed",
            dismissed_at=now_iso,
            dismissal_reason=dismissal_reason,
        )
    else:
        _LOGGER.warning(
            "pattern_store not available — dismissal for %s not persisted to storage",
            suggestion_id,
        )

    return {
        "suggestion_id": suggestion_id,
        "status": "dismissed",
        "reason": dismissal_reason,
    }


async def _tool_trigger_scan(hass: HomeAssistant) -> dict[str, Any]:
    domain_data: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    now: datetime = datetime.now(UTC)
    last_scan_iso: str | None = domain_data.get("_mcp_last_scan_at")

    if isinstance(last_scan_iso, str):
        try:
            last_scan: datetime = datetime.fromisoformat(last_scan_iso)
            delta: float = (now - last_scan).total_seconds()
            if delta < 60:
                suggestions: list[dict[str, Any]] = await _phase2_suggestions(hass)
                return {
                    "patterns_detected": len({s["pattern_id"] for s in suggestions}),
                    "suggestions_generated": len(suggestions),
                    "scan_duration_ms": 0,
                    "cached": True,
                }
        except ValueError:
            pass

    collector: DataCollector | None = _find_collector(hass)
    if collector is None:
        return {"error": "No collector available — check LLM configuration"}

    started: datetime = datetime.now(UTC)
    await collector._collect_analyze_log(force=True)
    finished: datetime = datetime.now(UTC)

    domain_data["_mcp_last_scan_at"] = finished.isoformat()
    suggestions: list[dict[str, Any]] = await _phase2_suggestions(hass)

    return {
        "patterns_detected": len({s["pattern_id"] for s in suggestions}),
        "suggestions_generated": len(suggestions),
        "scan_duration_ms": int((finished - started).total_seconds() * 1000),
        "cached": False,
    }
