"""Selora AI — Home Assistant Integration.

Self-contained HA custom integration.

    HA entity registry / state machine / recorder (SQLite)
        |
        v
    DataCollector  ──snapshot──>  LLMClient (Anthropic API or local Ollama)
        |                              |
        |                         suggestions
        |                              v
        v                    automations.yaml (disabled)
    logging + sensors              + reload

LLM Backends:
    Anthropic API  — Claude, cloud, recommended
    OpenAI API     — GPT models, cloud
    Ollama         — Llama 3.1, local, on-prem fallback
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
import logging
import re
from typing import TYPE_CHECKING, Any
import uuid

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import decorators
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
import voluptuous as vol
import yaml

if TYPE_CHECKING:
    from .automation_store import AutomationStore
    from .device_manager import DeviceManager
    from .pattern_store import PatternStore
    from .scene_store import SceneStore
    from .types import (
        AutomationDict,
        ChatMessage,
        EntitySnapshot,
        RiskAssessment,
        ServiceCallDict,
        SessionData,
        SessionSummary,
        ToolCallLog,
    )

from .automation_utils import suggestion_content_fingerprint
from .const import (
    AIGATEWAY_TOKEN_PATH,
    AUTOMATION_ID_PREFIX,
    AUTOMATION_STALE_DAYS,
    COLLECTOR_DOMAINS,
    CONF_AIGATEWAY_ACCESS_TOKEN,
    CONF_AIGATEWAY_CLIENT_ID,
    CONF_AIGATEWAY_EXPIRES_AT,
    CONF_AIGATEWAY_REFRESH_TOKEN,
    CONF_AIGATEWAY_USER_EMAIL,
    CONF_AIGATEWAY_USER_ID,
    CONF_ANTHROPIC_API_KEY,
    CONF_ANTHROPIC_MODEL,
    CONF_AUTO_PURGE_STALE,
    CONF_COLLECTOR_ENABLED,
    CONF_COLLECTOR_END_TIME,
    CONF_COLLECTOR_INTERVAL,
    CONF_COLLECTOR_MODE,
    CONF_COLLECTOR_START_TIME,
    CONF_DISCOVERY_ENABLED,
    CONF_DISCOVERY_END_TIME,
    CONF_DISCOVERY_INTERVAL,
    CONF_DISCOVERY_MODE,
    CONF_DISCOVERY_START_TIME,
    CONF_ENRICHMENT_INTERVAL,
    CONF_ENTRY_TYPE,
    CONF_GEMINI_API_KEY,
    CONF_GEMINI_MODEL,
    CONF_LLM_PRICING_OVERRIDES,
    CONF_LLM_PROVIDER,
    CONF_OLLAMA_HOST,
    CONF_OLLAMA_MODEL,
    CONF_OPENAI_API_KEY,
    CONF_OPENAI_MODEL,
    CONF_OPENROUTER_API_KEY,
    CONF_OPENROUTER_MODEL,
    CONF_PATTERN_ENABLED,
    CONF_RECORDER_LOOKBACK_DAYS,
    CONF_SELORA_CONNECT_ENABLED,
    CONF_SELORA_CONNECT_URL,
    CONF_SELORA_INSTALLATION_ID,
    CONF_SELORA_JWT_KEY,
    CONF_SELORA_LOCAL_HOST,
    CONF_SELORA_MCP_URL,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_AUTO_PURGE_STALE,
    DEFAULT_COLLECTOR_ENABLED,
    DEFAULT_COLLECTOR_END_TIME,
    DEFAULT_COLLECTOR_INTERVAL,
    DEFAULT_COLLECTOR_MODE,
    DEFAULT_COLLECTOR_START_TIME,
    DEFAULT_DISCOVERY_ENABLED,
    DEFAULT_DISCOVERY_END_TIME,
    DEFAULT_DISCOVERY_INTERVAL,
    DEFAULT_DISCOVERY_MODE,
    DEFAULT_DISCOVERY_START_TIME,
    DEFAULT_ENRICHMENT_INTERVAL,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_OPENROUTER_HOST,
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_RECORDER_LOOKBACK_DAYS,
    DEFAULT_SELORA_CONNECT_URL,
    DEFAULT_SELORA_LOCAL_HOST,
    DOMAIN,
    ENTITY_SNAPSHOT_ATTRS,
    ENTRY_TYPE_DEVICE,
    LLM_PROVIDER_ANTHROPIC,
    LLM_PROVIDER_GEMINI,
    LLM_PROVIDER_OLLAMA,
    LLM_PROVIDER_OPENAI,
    LLM_PROVIDER_OPENROUTER,
    LLM_PROVIDER_SELORA_CLOUD,
    LLM_PROVIDER_SELORA_LOCAL,
    MODE_SCHEDULED,
    PANEL_ICON,
    PANEL_NAME,
    PANEL_PATH,
    PANEL_TITLE,
    SIGNAL_ACTIVITY_LOG,
    SIGNAL_DEVICES_UPDATED,
    SIGNAL_PROACTIVE_SUGGESTIONS,
    SIGNAL_SCENE_DELETED,
)
from .scene_discovery import get_area_names

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["conversation", "sensor"]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_CONVERSATIONS_STORAGE_KEY = f"{DOMAIN}.conversations"
_CONVERSATIONS_STORAGE_VERSION = 1

# Maximum messages kept per session (older messages pruned from the middle,
# keeping the first message for context and the latest N-1 for recency).
_SESSION_MAX_MESSAGES = 100

# Maximum number of chat sessions retained.  When exceeded the oldest
# sessions (by updated_at) are evicted to stay within budget.
_SESSION_MAX_COUNT = 200


class ConversationStore:
    """Thin wrapper around HA's Store for persisting chat sessions."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass,
            version=_CONVERSATIONS_STORAGE_VERSION,
            key=_CONVERSATIONS_STORAGE_KEY,
        )
        self._data: dict[str, Any] | None = None

    async def _ensure_loaded(self) -> None:
        if self._data is None:
            raw = await self._store.async_load()
            self._data = raw if isinstance(raw, dict) else {"sessions": {}}

    async def list_sessions(self) -> list[SessionSummary]:
        """Return session summaries (no messages) sorted by updated_at descending."""
        await self._ensure_loaded()
        if self._data is None:
            raise RuntimeError("Session store failed to load")
        summaries = []
        for sid, session in self._data["sessions"].items():
            summaries.append(
                {
                    "id": sid,
                    "title": session.get("title", "Untitled"),
                    "created_at": session.get("created_at", ""),
                    "updated_at": session.get("updated_at", ""),
                    "message_count": len(session.get("messages", [])),
                }
            )
        summaries.sort(key=lambda s: s["updated_at"], reverse=True)
        return summaries

    async def get_session(self, session_id: str) -> SessionData | None:
        """Return a full session including messages, or None if not found."""
        await self._ensure_loaded()
        if self._data is None:
            raise RuntimeError("Session store failed to load")
        return self._data["sessions"].get(session_id)

    def _evict_oldest_sessions(self) -> None:
        """Remove oldest sessions when count exceeds the cap."""
        if self._data is None:
            return
        sessions = self._data["sessions"]
        if len(sessions) <= _SESSION_MAX_COUNT:
            return
        sorted_ids = sorted(
            sessions,
            key=lambda sid: sessions[sid].get("updated_at", ""),
        )
        to_remove = len(sessions) - _SESSION_MAX_COUNT
        for sid in sorted_ids[:to_remove]:
            del sessions[sid]

    async def create_session(self) -> SessionData:
        """Create a new empty session and persist it."""
        await self._ensure_loaded()
        if self._data is None:
            raise RuntimeError("Session store failed to load")
        now = dt_util.now().isoformat()
        session: SessionData = {
            "id": str(uuid.uuid4()),
            "title": "New conversation",
            "created_at": now,
            "updated_at": now,
            "messages": [],
        }
        self._data["sessions"][session["id"]] = session
        self._evict_oldest_sessions()
        await self._store.async_save(self._data)
        return session

    async def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        automation: AutomationDict | None = None,
        automation_yaml: str | None = None,
        description: str | None = None,
        automation_status: str | None = None,
        intent: str | None = None,
        calls: list[ServiceCallDict] | None = None,
        automation_id: str | None = None,
        risk_assessment: RiskAssessment | None = None,
        tool_calls: list[ToolCallLog] | None = None,
        scene: dict[str, Any] | None = None,
        scene_yaml: str | None = None,
        scene_id: str | None = None,
        scene_status: str | None = None,
        refine_scene_id: str | None = None,
        quick_actions: list[dict[str, Any]] | None = None,
    ) -> ChatMessage:
        """Append a message to a session, auto-create if missing, and persist."""
        await self._ensure_loaded()
        if self._data is None:
            raise RuntimeError("Session store failed to load")

        if session_id not in self._data["sessions"]:
            now = dt_util.now().isoformat()
            self._data["sessions"][session_id] = {
                "id": session_id,
                "title": "New conversation",
                "created_at": now,
                "updated_at": now,
                "messages": [],
            }
            self._evict_oldest_sessions()

        session = self._data["sessions"][session_id]
        message: ChatMessage = {
            "role": role,
            "content": content,
            "timestamp": dt_util.now().isoformat(),
        }
        if intent is not None:
            message["intent"] = intent
        if automation is not None:
            message["automation"] = automation
        if automation_yaml is not None:
            message["automation_yaml"] = automation_yaml
        if description is not None:
            message["description"] = description
        if automation_status is not None:
            message["automation_status"] = automation_status
        if calls is not None:
            message["calls"] = calls
        if automation_id is not None:
            message["automation_id"] = automation_id
        if risk_assessment is not None:
            message["risk_assessment"] = risk_assessment
        if tool_calls is not None:
            message["tool_calls"] = tool_calls
        if scene is not None:
            message["scene"] = scene
        if scene_yaml is not None:
            message["scene_yaml"] = scene_yaml
        if scene_id is not None:
            message["scene_id"] = scene_id
        if scene_status is not None:
            message["scene_status"] = scene_status
        if refine_scene_id is not None:
            message["refine_scene_id"] = refine_scene_id
        if quick_actions:
            message["quick_actions"] = quick_actions

        session["messages"].append(message)

        # Maintain a session-level scene index so scene context survives
        # message pruning.  Keyed by scene_id → {name, yaml}.
        if scene_id and scene_yaml:
            if "scenes" not in session:
                session["scenes"] = {}
            session["scenes"][scene_id] = {
                "name": (scene or {}).get("name", ""),
                "yaml": scene_yaml,
            }

        # Prune if too long (keep first + latest N-1)
        msgs = session["messages"]
        if len(msgs) > _SESSION_MAX_MESSAGES:
            session["messages"] = [msgs[0]] + msgs[-(_SESSION_MAX_MESSAGES - 1) :]

        # Update title from first user message
        if session["title"] == "New conversation":
            for m in session["messages"]:
                if m["role"] == "user":
                    session["title"] = m["content"][:60]
                    break

        session["updated_at"] = dt_util.now().isoformat()
        await self._store.async_save(self._data)
        return message

    async def set_automation_status(self, session_id: str, message_index: int, status: str) -> bool:
        """Update the automation_status field of a specific message."""
        await self._ensure_loaded()
        if self._data is None:
            raise RuntimeError("Session store failed to load")
        session = self._data["sessions"].get(session_id)
        if not session:
            return False
        msgs = session["messages"]
        if message_index < 0 or message_index >= len(msgs):
            return False
        msgs[message_index]["automation_status"] = status
        session["updated_at"] = dt_util.now().isoformat()
        await self._store.async_save(self._data)
        return True

    async def set_scene_status(
        self,
        session_id: str,
        message_index: int,
        status: str,
        *,
        scene_id: str | None = None,
        entity_id: str | None = None,
    ) -> bool:
        """Update the scene_status field of a specific message.

        Optionally sets scene_id when the scene is created on accept. When
        scene_id is set on a message that already has scene_yaml, mirror the
        entry into the session-level ``scenes`` index so ``_find_active_scenes``
        sees it regardless of which lookup path runs first.
        """
        await self._ensure_loaded()
        if self._data is None:
            raise RuntimeError("Session store failed to load")
        session = self._data["sessions"].get(session_id)
        if not session:
            return False
        msgs = session["messages"]
        if message_index < 0 or message_index >= len(msgs):
            return False
        message = msgs[message_index]
        message["scene_status"] = status
        if scene_id is not None:
            message["scene_id"] = scene_id
            scene_yaml = message.get("scene_yaml")
            if scene_yaml:
                if "scenes" not in session:
                    # Seed the index with every existing saved scene
                    # message. Sessions created before the index existed
                    # carry their scenes as message-level scene_id /
                    # scene_yaml fields; once the index path takes over
                    # in _find_active_scenes, those legacy entries
                    # would otherwise drop out of LLM context.
                    session["scenes"] = {}
                    for prior in msgs:
                        prior_sid = prior.get("scene_id")
                        prior_yaml = prior.get("scene_yaml")
                        if prior_sid and prior_yaml and prior_sid != scene_id:
                            session["scenes"][prior_sid] = {
                                "name": (prior.get("scene") or {}).get("name", ""),
                                "yaml": prior_yaml,
                            }
                session["scenes"][scene_id] = {
                    "name": (message.get("scene") or {}).get("name", ""),
                    "yaml": scene_yaml,
                }
        if entity_id is not None:
            # Persist the resolved entity_id (may differ from
            # scene.<scene_id> due to slugged alias or collision suffix)
            # so Activate works correctly after a reload.
            message["entity_id"] = entity_id
        session["updated_at"] = dt_util.now().isoformat()
        await self._store.async_save(self._data)
        return True

    async def update_session_title(self, session_id: str, title: str) -> bool:
        """Update a session's title and persist."""
        await self._ensure_loaded()
        if self._data is None:
            raise RuntimeError("Session store failed to load")
        session = self._data["sessions"].get(session_id)
        if not session:
            return False
        session["title"] = title
        session["updated_at"] = dt_util.now().isoformat()
        await self._store.async_save(self._data)
        return True

    async def remove_scene_from_sessions(self, scene_id: str) -> None:
        """Purge all traces of a scene from every session.

        Removes the scene from session-level indexes *and* scrubs
        ``scene_id`` / ``scene_yaml`` / ``scene`` fields from individual
        messages.  This prevents both ``_find_active_scenes`` (message-scan
        fallback) and ``_build_history_from_session`` from reattaching the
        deleted scene's YAML to future LLM turns.
        """
        await self._ensure_loaded()
        if self._data is None:
            return
        dirty = False
        for session in self._data["sessions"].values():
            # 1. Session-level scene index
            scene_index = session.get("scenes")
            if scene_index and scene_id in scene_index:
                del scene_index[scene_id]
                dirty = True
            # 2. Individual message fields
            for m in session.get("messages", []):
                if m.get("scene_id") == scene_id:
                    m.pop("scene_id", None)
                    m.pop("scene_yaml", None)
                    m.pop("scene", None)
                    dirty = True
        if dirty:
            await self._store.async_save(self._data)

    async def update_scene_in_sessions(self, scene_id: str, name: str, yaml_repr: str) -> None:
        """Update a scene's YAML in every session that already references it.

        Updates both the session-level ``scenes`` index *and* message-level
        ``scene_yaml`` fields so that ``_find_active_scenes`` (index path),
        ``_build_history_from_session`` (message path), and MCP history all
        serve the current definition instead of a stale snapshot.
        """
        await self._ensure_loaded()
        if self._data is None:
            return
        dirty = False
        for session in self._data["sessions"].values():
            # 1. Session-level scene index
            scene_index = session.get("scenes")
            if scene_index is not None and scene_id in scene_index:
                scene_index[scene_id] = {"name": name, "yaml": yaml_repr}
                dirty = True
            # 2. Message-level fields used by _build_history_from_session
            for m in session.get("messages", []):
                if m.get("scene_id") == scene_id and m.get("scene_yaml"):
                    m["scene_yaml"] = yaml_repr
                    if m.get("scene"):
                        m["scene"]["name"] = name
                    dirty = True
        if dirty:
            await self._store.async_save(self._data)

    async def add_scene_to_session(
        self, session_id: str, scene_id: str, name: str, yaml_repr: str
    ) -> None:
        """Add a scene to a specific session's scene index.

        Used by the restore path to rehydrate context for a scene that
        was previously purged by ``remove_scene_from_sessions``.
        """
        await self._ensure_loaded()
        if self._data is None:
            return
        session = self._data["sessions"].get(session_id)
        if session is None:
            return
        if "scenes" not in session:
            session["scenes"] = {}
        session["scenes"][scene_id] = {"name": name, "yaml": yaml_repr}
        await self._store.async_save(self._data)

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session. Returns True if it existed."""
        await self._ensure_loaded()
        if self._data is None:
            raise RuntimeError("Session store failed to load")
        if session_id not in self._data["sessions"]:
            return False
        del self._data["sessions"][session_id]
        await self._store.async_save(self._data)
        return True

    async def get_session_preview(self, session_id: str) -> str:
        """Return the session's first user message truncated to 60 chars, or empty string."""
        await self._ensure_loaded()
        if self._data is None:
            return ""
        session = self._data["sessions"].get(session_id)
        if not session:
            return ""
        for msg in session.get("messages", []):
            if msg.get("role") == "user":
                return msg.get("content", "")[:60]
        return session.get("title", "")[:60]


async def _handle_scheduled_intent(
    hass: HomeAssistant,
    intent_type: str,
    parsed: dict[str, Any],
    session_id: str,
) -> tuple[str, str, str | None]:
    """Handle delayed_command and cancel intents.

    Returns (updated_intent_type, response_text, schedule_id).
    """
    schedule_id: str | None = None
    response_text = parsed.get("response", "")

    if intent_type == "delayed_command":
        from .scheduled_actions import (
            ScheduledTaskTracker,
            validate_delay_seconds,
        )

        tracker: ScheduledTaskTracker = hass.data[DOMAIN].setdefault(
            "_scheduled_tasks", ScheduledTaskTracker(hass)
        )
        calls = parsed.get("calls", [])
        delay_seconds = parsed.get("delay_seconds")
        scheduled_time = parsed.get("scheduled_time")

        try:
            if delay_seconds is not None and calls:
                ok, reason = validate_delay_seconds(delay_seconds)
                if ok:
                    task = await tracker.schedule_delayed(
                        session_id, calls, delay_seconds, response_text
                    )
                    schedule_id = task.schedule_id
                else:
                    response_text = f"Could not schedule: {reason}"
                    intent_type = "answer"
            elif scheduled_time is not None and calls:
                task = await tracker.schedule_at_time(
                    session_id, calls, scheduled_time, response_text
                )
                schedule_id = task.schedule_id
            else:
                intent_type = "answer"
        except RuntimeError as exc:
            response_text = str(exc)
            intent_type = "answer"

    elif intent_type == "cancel":
        from .scheduled_actions import ScheduledTaskTracker

        tracker = hass.data[DOMAIN].get("_scheduled_tasks")
        if tracker:
            latest = tracker.get_latest_pending(session_id)
            if latest:
                cancelled = await tracker.async_cancel_task(latest.schedule_id)
                if cancelled:
                    response_text = parsed.get("response", f"Cancelled: {latest.description}")
                else:
                    response_text = (
                        "I couldn't cancel that action — the scheduled automation "
                        "could not be removed. Please check Settings → Automations."
                    )
            else:
                response_text = "No pending scheduled actions to cancel."
        else:
            response_text = "No pending scheduled actions to cancel."

    return intent_type, response_text, schedule_id


def _mask_api_key(key: str) -> str:
    """Return a safe display hint — first 8 chars + ellipsis."""
    if not key:
        return ""
    if len(key) <= 8:
        return "***"
    return f"{key[:8]}..."


def _collect_entity_states(hass: HomeAssistant) -> list[EntitySnapshot]:
    """Get current states of all entities for the LLM.

    Filters out unavailable/unknown entities to avoid sending stale or
    deleted entities as context (e.g. soft-deleted automations).
    Also filters disabled entities (e.g. switches converted to lights via
    HA's "Show as" feature) and restricts to COLLECTOR_DOMAINS + automation.
    Excludes non-controllable light entities (IR LEDs, camera illuminators).
    Each entity is annotated with its area name (resolved from the entity's
    direct area assignment or inherited from its device).
    """
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from .entity_capabilities import is_actionable_entity
    from .entity_filter import EntityFilter

    _SKIP_STATES = {"unavailable", "unknown"}
    _ALLOWED_DOMAINS = COLLECTOR_DOMAINS | {"automation"}
    all_states = hass.states.async_all()
    ef = EntityFilter(hass, [s.entity_id for s in all_states])

    entity_reg = er.async_get(hass)
    device_reg = dr.async_get(hass)
    area_reg = ar.async_get(hass)
    area_id_to_name: dict[str, str] = {area.id: area.name for area in area_reg.async_list_areas()}

    states: list[EntitySnapshot] = []
    for state in all_states:
        if state.state in _SKIP_STATES:
            continue
        domain = state.entity_id.split(".")[0]
        if domain not in _ALLOWED_DOMAINS:
            continue
        if not ef.is_active(state.entity_id):
            continue
        if not is_actionable_entity(state.entity_id):
            continue
        # Include key attributes for state-aware responses (#68)
        attrs: dict[str, Any] = {
            "friendly_name": state.attributes.get("friendly_name", ""),
        }
        for attr_key in ENTITY_SNAPSHOT_ATTRS:
            val = state.attributes.get(attr_key)
            if val is not None:
                attrs[attr_key] = val

        # Resolve area: entity direct assignment, then device fallback
        area_name = ""
        entry = entity_reg.async_get(state.entity_id)
        if entry:
            area_id = entry.area_id
            if not area_id and entry.device_id:
                device = device_reg.async_get(entry.device_id)
                if device:
                    area_id = device.area_id
            if area_id:
                area_name = area_id_to_name.get(area_id, "")

        snapshot: EntitySnapshot = {
            "entity_id": state.entity_id,
            "state": _format_entity_state(state.state),
            "attributes": attrs,
        }
        if area_name:
            snapshot["area_name"] = area_name
        states.append(snapshot)
    return states


def _format_entity_state(value: str) -> str:
    """Convert ISO 8601 timestamps to 12-hour AM/PM format."""
    from .helpers import format_entity_state

    return format_entity_state(value)


def _require_admin(
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> bool:
    """Ensure Selora websocket commands are only available to admins."""
    user = getattr(connection, "user", None)
    if user is not None and getattr(user, "is_admin", False):
        return True

    connection.send_error(
        msg["id"],
        "admin_required",
        "Selora AI panel actions require an administrator account",
    )
    return False


def _sanitize_history_text(value: Any, max_length: int = 200) -> str:
    """Normalize and truncate untrusted conversation context before sending it back to the LLM."""
    from .helpers import sanitize_untrusted_text

    return sanitize_untrusted_text(value, limit=max_length)


def _get_device_manager(hass: HomeAssistant) -> DeviceManager | None:
    """Find the DeviceManager from hass.data."""
    from .device_manager import DeviceManager

    for entry_data in hass.data.get(DOMAIN, {}).values():
        if isinstance(entry_data, dict) and isinstance(
            entry_data.get("device_manager"), DeviceManager
        ):
            return entry_data["device_manager"]
    return None


# ── Shared chat handler helpers ──────────────────────────────────────────────


def _find_llm(hass: HomeAssistant) -> Any:
    """Find the LLMClient from any active config entry, or None."""
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if isinstance(entry_data, dict) and "llm" in entry_data:
            return entry_data["llm"]
    return None


def _resolve_llm_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """Return the integration's LLM config entry, ignoring device-onboarding entries."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_DEVICE:
            continue
        return entry
    return None


def _aigateway_view(entry_data: dict[str, Any]) -> dict[str, Any]:
    """Return AI Gateway credentials normalized from flat or nested entry data.

    The interactive OAuth flow writes flat ``aigateway_*`` keys; the hub
    auto-provisioner writes a nested ``selora_ai_gateway`` object (per
    selora-hub-openapi.yaml). Flat values win when both are present so a
    fresh in-band refresh isn't masked by a stale provisioned blob.
    """
    nested = entry_data.get("selora_ai_gateway") or entry_data.get("ai_gateway")
    if not isinstance(nested, dict):
        nested = {}

    connect_url = entry_data.get(CONF_SELORA_CONNECT_URL) or ""
    if not connect_url:
        token_url = nested.get("token_url") or ""
        if token_url and AIGATEWAY_TOKEN_PATH in token_url:
            connect_url = token_url.split(AIGATEWAY_TOKEN_PATH, 1)[0]

    return {
        "access_token": entry_data.get(CONF_AIGATEWAY_ACCESS_TOKEN)
        or nested.get("access_token")
        or "",
        "refresh_token": entry_data.get(CONF_AIGATEWAY_REFRESH_TOKEN)
        or nested.get("refresh_token")
        or "",
        # Read expiry from the nested blob too: without it, a provisioned
        # access token paired with an unknown-expiry value (0.0) would
        # bypass _needs_refresh() and keep being used past expiry,
        # producing 401s instead of triggering a refresh.
        "expires_at": float(
            entry_data.get(CONF_AIGATEWAY_EXPIRES_AT) or nested.get("expires_at") or 0.0
        ),
        "user_email": entry_data.get(CONF_AIGATEWAY_USER_EMAIL) or nested.get("user_email") or "",
        "user_id": entry_data.get(CONF_AIGATEWAY_USER_ID) or nested.get("user_id") or "",
        "client_id": entry_data.get(CONF_AIGATEWAY_CLIENT_ID) or nested.get("client_id") or "",
        "connect_url": connect_url or DEFAULT_SELORA_CONNECT_URL,
    }


def _resolve_llm_provider(entry_data: dict[str, Any]) -> str:
    """Return the configured LLM provider id, inferring ``selora_cloud`` when needed.

    The hub auto-provisioner writes an ``ai_gateway`` block without setting
    ``llm_provider``. Treat the presence of a refresh token there as an
    implicit selection of Selora Cloud so the integration boots into the
    right provider without requiring the user to click "Link".
    """
    explicit = entry_data.get(CONF_LLM_PROVIDER)
    if explicit:
        return str(explicit)
    if _aigateway_view(entry_data)["refresh_token"]:
        return LLM_PROVIDER_SELORA_CLOUD
    return DEFAULT_LLM_PROVIDER


async def _resolve_or_create_session(
    store: ConversationStore,
    session_id: str,
) -> tuple[Any, str, bool]:
    """Resolve an existing session or create a new one.

    Returns (session_dict, session_id, created), where ``created`` is True
    iff this call created a brand-new session. Callers in the chat
    handlers use the flag to tear down the empty session if the LLM call
    fails before any message is appended — otherwise a transport error
    on the first turn would leave a ghost "New conversation" entry in
    the sidebar.
    """
    if session_id:
        session = await store.get_session(session_id)
        if session:
            return session, session_id, False
    session = await store.create_session()
    return session, session["id"], True


def _find_active_refining_yaml(
    stored_messages: list[dict[str, Any]],
    user_message: str,
) -> tuple[str, str] | None:
    """Return (sanitized_alias, yaml) for the active refining automation.

    Returns a result when the most recent automation status in the session
    is "refining" — meaning the user loaded an automation for refinement
    and all subsequent messages in the session are part of that refinement
    conversation.  A newer "pending", "saved", or "declined" status means
    the refinement ended.
    """
    del user_message
    for m in reversed(stored_messages):
        status = m.get("automation_status")
        if status in ("pending", "saved", "declined"):
            return None
        if status == "refining" and m.get("automation_yaml"):
            alias = (m.get("automation") or {}).get("alias", "")
            safe_alias = _sanitize_history_text(alias, max_length=100)
            return safe_alias, m["automation_yaml"]
    return None


def _find_active_refining_scene(
    stored_messages: list[dict[str, Any]],
) -> tuple[str, str] | None:
    """Return (sanitized_name, yaml) for the active refining scene.

    Returns a result when the most recent scene status in the session is
    "refining" — meaning the user loaded a scene for refinement and all
    subsequent messages are part of that refinement conversation.  A newer
    "pending", "saved", or "declined" status means the refinement ended.
    """
    for m in reversed(stored_messages):
        status = m.get("scene_status")
        if status in ("pending", "saved", "declined"):
            return None
        if status == "refining" and m.get("scene_yaml"):
            name = (m.get("scene") or {}).get("name", "")
            safe_name = _sanitize_history_text(name, max_length=100)
            return safe_name, m["scene_yaml"]
    return None


def _find_active_scenes(
    session: dict[str, Any] | None,
    stored_messages: list[dict[str, Any]],
) -> list[tuple[str, str, str]]:
    """Return the latest (scene_id, name, yaml) for every scene in the session.

    Reads from the session-level ``scenes`` index first (survives message
    pruning), falling back to scanning ``stored_messages`` for sessions
    created before the index was introduced.

    This context is injected into the current turn so that the LLM always has
    the scene_id and YAML available, even after older messages are pruned from
    the conversation history.
    """
    scene_index: dict[str, dict[str, str]] = (session or {}).get("scenes", {})
    if scene_index:
        return [
            (
                sid,
                _sanitize_history_text(data.get("name", ""), max_length=100),
                data.get("yaml", ""),
            )
            for sid, data in scene_index.items()
            if data.get("yaml")
        ]

    # Fallback: scan messages for sessions without the scenes index.
    latest: dict[str, tuple[str, str]] = {}
    for m in stored_messages:
        sid = m.get("scene_id")
        yaml_text = m.get("scene_yaml")
        if sid and yaml_text:
            name = _sanitize_history_text((m.get("scene") or {}).get("name", ""), max_length=100)
            latest[sid] = (name, yaml_text)
    return [(sid, name, yaml) for sid, (name, yaml) in latest.items()]


def _build_history_from_session(
    stored_messages: list[dict[str, Any]],
    *,
    skip_refining_yaml: bool = False,
    skip_refining_scene_yaml: bool = False,
) -> list[dict[str, str]]:
    """Build LLM history from stored session messages.

    For assistant messages that proposed an automation or scene, appends the
    YAML so the LLM has full context when the user asks to refine it.

    For each unique ``scene_id``, only the *latest* YAML version is attached
    so the LLM has current context for every scene in the session, while
    superseded versions (including renames) are omitted.
    """
    # For each unique scene_id, find the index of its latest YAML so
    # refinements (including renames) see the current version and
    # multi-scene sessions retain context for every scene. Pending /
    # refining proposals don't have a scene_id yet, so attach their YAML
    # in place too — otherwise the LLM loses the proposed entities the
    # moment the user asks to tweak the proposal.
    #
    # NOTE: we deliberately do not try to drop a "refining" message when
    # a later saved scene exists. The relationship between a refining
    # draft and a later saved scene cannot be determined from the
    # session state alone — the saved scene may be the accepted
    # refinement of this draft, or an unrelated scene the user happened
    # to save in the same session. Dropping based on that heuristic
    # regresses multi-draft sessions, so we keep all pending and
    # refining YAML in history. Cleaning up genuinely superseded drafts
    # would require explicit linkage between a refining message and the
    # saved scene that replaced it, which is a separate change.
    latest_scene_by_id: dict[str, int] = {}
    pending_scene_indices: set[int] = set()
    for i, m in enumerate(stored_messages):
        scene_yaml = m.get("scene_yaml")
        if not scene_yaml:
            continue
        sid = m.get("scene_id")
        if sid:
            latest_scene_by_id[sid] = i
        elif m.get("scene_status") in ("pending", "refining"):
            pending_scene_indices.add(i)
    latest_scene_indices: set[int] = set(latest_scene_by_id.values()) | pending_scene_indices

    history: list[dict[str, str]] = []
    for i, m in enumerate(stored_messages):
        if m.get("role") not in ("user", "assistant"):
            continue
        content = m["content"]
        if (
            m.get("automation_yaml")
            and m.get("automation_status") in ("pending", "refining")
            and not (skip_refining_yaml and m.get("automation_status") == "refining")
        ):
            alias = _sanitize_history_text((m.get("automation") or {}).get("alias", ""))
            description = _sanitize_history_text(m.get("description", ""))
            header = f"[Untrusted automation reference data for context only: {alias}"
            if description:
                header += f" — {description}"
            header += f"]\n{m['automation_yaml']}"
            content = f"{content}\n\n{header}"
        elif i in latest_scene_indices and not (
            skip_refining_scene_yaml and m.get("scene_status") == "refining"
        ):
            scene_name = _sanitize_history_text((m.get("scene") or {}).get("name", ""))
            sid = m.get("scene_id", "")
            qualifier = f"scene_id: {sid}" if sid else "pending proposal — not yet saved"
            header = f"[Untrusted scene reference data for context only: {scene_name} ({qualifier})]\n{m['scene_yaml']}"
            content = f"{content}\n\n{header}"
        history.append({"role": m["role"], "content": content})
    return history


def _collect_existing_automations(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Collect existing automation summaries for LLM context.

    Translates HA's raw ``on``/``off`` state into the ``enabled``/``disabled``
    wording the user sees in Settings → Automations. Passing the raw value
    through made the LLM report disabled automations as "off", which reads
    like an idle/inactive device rather than a turned-off rule.
    """
    state_label = {"on": "enabled", "off": "disabled"}
    return [
        {
            "entity_id": state.entity_id,
            "alias": state.attributes.get("friendly_name", state.entity_id),
            "state": state_label.get(state.state, state.state),
        }
        for state in hass.states.async_all("automation")
    ]


async def _execute_command_calls(
    hass: HomeAssistant,
    calls: list[dict[str, Any]],
) -> tuple[list[str], str]:
    """Execute HA service calls from an LLM command response.

    Returns (executed_services, error_suffix).
    """
    executed: list[str] = []
    error_suffix = ""
    for call in calls:
        service = call.get("service", "")
        if not service or "." not in service:
            continue
        domain_part, service_name = service.split(".", 1)
        target = call.get("target", {})
        data = call.get("data", {})
        try:
            await hass.services.async_call(
                domain_part, service_name, {**data, **target}, blocking=True
            )
            executed.append(service)
        except Exception as exc:  # noqa: BLE001 — third-party service handlers may raise beyond HA's hierarchy
            _LOGGER.error("Failed to execute %s: %s", service, exc)
            error_suffix += f" (Failed: {service}: {exc})"
    return executed, error_suffix


def _entity_ids_from_calls(calls: list[dict[str, Any]]) -> list[str]:
    """Return a deduplicated ordered list of entity_ids targeted by service calls."""
    seen: set[str] = set()
    result: list[str] = []
    for call in calls:
        target = call.get("target", {})
        raw = target.get("entity_id") or call.get("data", {}).get("entity_id")
        if raw is None:
            continue
        ids = [raw] if isinstance(raw, str) else list(raw)
        for eid in ids:
            if eid and eid not in seen:
                seen.add(eid)
                result.append(eid)
    return result


_ENTITY_MARKER_RE = re.compile(
    r"\[\[entity:([a-z_]+\.[a-z0-9_\-]+)"  # [[entity:<id>|…]]
    r"|\[\[entities:([a-z_]+\.[a-z0-9_\-]+(?:,\s*[a-z_]+\.[a-z0-9_\-]+)*)\]\]"  # [[entities:…]]
)


def _entity_ids_already_in_text(text: str) -> set[str]:
    """Return all entity_ids already referenced by tile markers in *text*."""
    found: set[str] = set()
    for m in _ENTITY_MARKER_RE.finditer(text):
        if m.group(1):
            found.add(m.group(1))
        elif m.group(2):
            found.update(eid.strip() for eid in m.group(2).split(","))
    return found


_AUTO_MARKER_MIN_NAME_LEN = 4
_AUTO_MARKER_MAX_IDS = 12

# Bullet line that is essentially "- <friendly_name>" with an optional
# trailing "— state" hint. Captures the name for friendly_name (or raw
# entity_id) lookup. Allows `**bold**` / `__bold__` wrappers around the
# name. The state separator may be em-dash, en-dash, or hyphen-minus —
# hyphen-minus only when preceded by whitespace, so friendly_names with
# internal hyphens like "A-frame House" aren't truncated at the first
# `-`. Underscore is allowed inside the name so raw entity_ids like
# `light.kitchen_lights` can be captured directly when the LLM omits
# the friendly_name and prints the id instead.
_BULLET_ENTITY_LINE_RE = re.compile(
    r"\s*[-•*]\s+"
    r"(?:\*\*|__)?\s*"
    r"(?P<name>[^*\n—–]+?)"
    r"\s*(?:\*\*|__)?"
    r"\s*(?:(?:[—–]|(?<=\s)-(?=\s))[^\n]*)?\s*"
)

_BULLET_PREFIX_RE = re.compile(r"\s*[-•*]\s+\S")

# "Lights (5 on):" / "**Devices** (3 total):" style headers. Match
# against the bold-stripped form so headers with bold wrapping only
# one word still match.
_LIST_HEADER_RE = re.compile(r"[A-Za-z][A-Za-z _-]*\s*\(\d+[^)]*\)\s*:?")

_ENTITY_ID_RE = re.compile(r"^[a-z_]+\.[a-z0-9_\-]+$")

# A line that consists ONLY of one entity marker (with optional
# surrounding whitespace). Used to detect a LLM-emitted trailing marker
# that we want to move inline.
_STANDALONE_MARKER_LINE_RE = re.compile(r"\s*\[\[entit(?:y|ies):[^\]\n]+\]\]\s*")

# A subheading describing one entity's state, e.g.
# "**Garage Door Status:**" or "Kitchen Lights details:". The captured
# name must match a known friendly_name for the strip to fire.
_STATUS_HEADING_RE = re.compile(
    r"(?P<name>[A-Za-z][\w +'-]*?)\s+"
    r"(?:Status|Details|State|Info|Information)\s*:?\s*"
)

# A "Label: value" style bullet describing one entity attribute, e.g.
# "- **Status:** Closed", "- Brightness: 80%".
_STATE_BULLET_RE = re.compile(
    r"\s*[-•*]\s+"
    r"(?:\*\*|__)?\s*[A-Za-z][\w +'-]*?\s*(?:\*\*|__)?"
    r"\s*:\s*\S.*"
)


def _strip_md_emphasis(line: str) -> str:
    """Drop ``**`` / ``__`` runs and surrounding whitespace from a line."""
    return line.replace("**", "").replace("__", "").strip()


def _name_search_forms(name: str) -> list[str]:
    """Lowercase search variants of a friendly_name for prose matching.

    LLMs spell device names naturally even when the friendly_name is
    CamelCased — "HeatPump" gets written as "heat pump" in answers.
    Yield both the raw lowercase form and a space-separated CamelCase
    split so word-boundary searches catch either spelling.
    """
    name = name.strip()
    lower = name.lower()
    forms = [lower]
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).lower()
    if spaced != lower:
        forms.append(spaced)
    return forms


def _normalized_name(name: str) -> str:
    """Strip whitespace, underscores, and hyphens so "Heat Pump",
    "HeatPump", and "heat_pump" all hash to the same key."""
    return re.sub(r"[\s_\-]+", "", name.lower())


def _inject_entity_markers(
    text: str,
    entities: list[EntitySnapshot],
) -> str:
    """Rewrite an LLM "answer" so devices it named in prose render as
    tile cards instead of duplicated markdown.

    The architect prompt asks the LLM to embed `[[entity:…]]` markers
    for every device it names, but cloud and local models alike often
    drift into one of three failure shapes:

      * **Bullet list of friendly names** (no markers at all) —
        "- **Kitchen Lights** — on (brightness: 180) …".
      * **Bullets + trailing marker** (worst of both) — same list AS
        WELL AS a `[[entities:…]]` block at the bottom. Both render.
      * **Plain prose mention** ("Yes, you have a garage door.") on
        short Q&A turns.

    Rewrite strategy:
      1. Detect bullet-entity runs regardless of any existing marker.
         Strip the run + preceding "Lights (5 on):" header when the
         entire run is entity bullets. Replace the stripped block with
         a single ``[[entities:…]]`` marker *in place*, but only if
         the run contains at least one entity_id not already marked
         elsewhere (otherwise the existing marker already covers it
         and we'd render duplicate tiles).
      2. Prose-only mention fallback — runs only when the response has
         neither markers nor stripped bullet runs. Without this guard,
         area sub-headings ("### Kitchen") match same-named entities
         (media_player.kitchen) and surface spurious tiles.

    Entities the LLM already wrapped in a marker are never double-marked.
    """
    if not text or not entities:
        return text
    already = _entity_ids_already_in_text(text)

    # Longest friendly_name first so "Garage Door" beats "Door" and we
    # don't double-match overlapping names. We do NOT filter by
    # `already` here — bullet capture still needs to see entities that
    # are also in an existing marker so we can strip the duplicate.
    candidates: list[tuple[str, str]] = []
    eid_set: set[str] = set()
    for ent in entities:
        eid = ent.get("entity_id")
        if not eid:
            continue
        eid_set.add(eid)
        name = ((ent.get("attributes") or {}).get("friendly_name") or "").strip()
        if len(name) < _AUTO_MARKER_MIN_NAME_LEN:
            continue
        candidates.append((eid, name))
    candidates.sort(key=lambda p: len(p[1]), reverse=True)
    # Key by normalized name (whitespace / underscores / hyphens
    # stripped, case-folded) so "HeatPump", "Heat Pump", and
    # "heat-pump" all hash to the same eid for bullet lookups.
    name_to_eid: dict[str, str] = {_normalized_name(name): eid for eid, name in candidates}

    lines = text.split("\n")

    # Index any standalone marker lines so we can move them inline if a
    # bullet run covers the same entities. ``standalone_markers[i]`` is
    # the set of entity_ids referenced by line *i* when that line is
    # nothing but a marker. LLM "bullets + trailing marker" responses
    # otherwise leave the marker stranded at the bottom of the bubble.
    standalone_markers: dict[int, set[str]] = {}
    for i, line in enumerate(lines):
        if _STANDALONE_MARKER_LINE_RE.fullmatch(line):
            ids = _entity_ids_already_in_text(line)
            if ids:
                standalone_markers[i] = ids

    # Pass 1a: every bullet line that resolves to a known entity.
    bullet_eid: dict[int, str] = {}
    captured_in_bullets: set[str] = set()
    for i, line in enumerate(lines):
        m = _BULLET_ENTITY_LINE_RE.fullmatch(line)
        if not m:
            continue
        name_part = m.group("name").strip()
        if not name_part:
            continue
        eid = name_to_eid.get(_normalized_name(name_part))
        if eid is None and _ENTITY_ID_RE.match(name_part) and name_part in eid_set:
            eid = name_part
        if not eid or eid in captured_in_bullets:
            continue
        bullet_eid[i] = eid
        captured_in_bullets.add(eid)

    # Pass 1b: group bullets into runs (contiguous, blanks-only-between).
    runs: list[list[int]] = []
    sorted_idxs = sorted(bullet_eid)
    if sorted_idxs:
        current = [sorted_idxs[0]]
        for idx in sorted_idxs[1:]:
            gap_only_blanks = all(not lines[k].strip() for k in range(current[-1] + 1, idx))
            if gap_only_blanks:
                current.append(idx)
            else:
                runs.append(current)
                current = [idx]
        runs.append(current)

    # Pass 1c: per run, compute the strip range, the insertion line,
    # and the marker payload. Skip the insertion entirely when every
    # entity in the run is already inside another marker — the existing
    # marker carries the tiles, so we just delete the bullets.
    lines_to_strip: set[int] = set()
    insertions: dict[int, list[str]] = {}
    for run in runs:
        first, last = run[0], run[-1]
        for k in range(first, last + 1):
            lines_to_strip.add(k)
        run_eids = [bullet_eid[i] for i in run]
        new_eids = [eid for eid in run_eids if eid not in already]

        insert_at = first
        h = first - 1
        while h >= 0 and not lines[h].strip():
            h -= 1
        if h >= 0 and _LIST_HEADER_RE.fullmatch(_strip_md_emphasis(lines[h])):
            # Only swallow the header if every bullet line under it is
            # an entity bullet we're stripping. Mixed lists with prose
            # bullets keep their header.
            k = h + 1
            saw_bullet = False
            all_consumed = True
            while k < len(lines):
                if not lines[k].strip():
                    k += 1
                    continue
                if not _BULLET_PREFIX_RE.match(lines[k]):
                    break
                saw_bullet = True
                if k not in lines_to_strip:
                    all_consumed = False
                    break
                k += 1
            if saw_bullet and all_consumed:
                lines_to_strip.add(h)
                for k in range(h + 1, first):
                    lines_to_strip.add(k)
                insert_at = h

        # If the LLM put a standalone marker elsewhere covering only
        # entities this run also captured, move it inline by stripping
        # that marker line and inserting a fresh marker at the run's
        # position. Without this the tiles render at the bottom of the
        # bubble (where the LLM placed the marker) instead of where the
        # user's eye expects them — between the lead-in and follow-up.
        run_set = set(run_eids)
        moved_marker = False
        for li, ids in list(standalone_markers.items()):
            if li in lines_to_strip:
                continue
            if ids and ids.issubset(run_set):
                lines_to_strip.add(li)
                moved_marker = True

        if new_eids or moved_marker:
            insertions[insert_at] = run_eids if moved_marker else new_eids

    # Pass 1d: single-entity status sections ("**Garage Door Status:**"
    # followed by "- **Status:** Closed" style bullets). These describe
    # one device's state in prose form — the LLM does this on Q&A turns
    # like "do I have a garage door?" where it didn't see a "list"
    # signal but still volunteered the state. Replace the whole section
    # with one inline marker so the tile lands where the breakdown was.
    for i, line in enumerate(lines):
        if i in lines_to_strip:
            continue
        bare = _strip_md_emphasis(line)
        m = _STATUS_HEADING_RE.fullmatch(bare)
        if not m:
            continue
        name = m.group("name").strip()
        eid = name_to_eid.get(_normalized_name(name))
        if not eid:
            continue
        j = i + 1
        section_lines: list[int] = []
        saw_bullet = False
        while j < len(lines):
            if not lines[j].strip():
                section_lines.append(j)
                j += 1
                continue
            if _STATE_BULLET_RE.fullmatch(lines[j]):
                section_lines.append(j)
                saw_bullet = True
                j += 1
                continue
            break
        if not saw_bullet:
            continue
        # Don't swallow the gap between the section and the following
        # paragraph — trim trailing blanks from the consumed lines.
        while section_lines and not lines[section_lines[-1]].strip():
            section_lines.pop()
        lines_to_strip.add(i)
        for k in section_lines:
            lines_to_strip.add(k)
        if eid not in already and eid not in captured_in_bullets:
            captured_in_bullets.add(eid)
            insertions[i] = [eid]

    # Pass 1d.5: strip any "- Label: value" state-info bullet runs.
    # The tile renders every attribute live (state, current/target
    # temperature, brightness, mode, battery, etc.), so the LLM's
    # prose breakdown is pure duplication of what the tile shows.
    # The architect prompt forbids these but compliance is imperfect.
    # We only fire when the response has at least one tile marker —
    # otherwise we'd strip legitimate "Steps: …" / capability lists
    # in responses that don't reference a specific device.
    has_marker_now = bool(already) or bool(insertions) or bool(standalone_markers)
    if has_marker_now:
        i = 0
        while i < len(lines):
            if i in lines_to_strip or not _STATE_BULLET_RE.fullmatch(lines[i]):
                i += 1
                continue
            run_first = i
            run_last = i
            j = i + 1
            while j < len(lines):
                if j in lines_to_strip:
                    j += 1
                    continue
                if not lines[j].strip():
                    # Tolerate blank lines inside the run — the LLM
                    # often puts an empty line between each attribute
                    # bullet. Don't claim the blank as part of the
                    # run yet; only commit it if another state bullet
                    # follows.
                    k = j + 1
                    while k < len(lines) and not lines[k].strip():
                        k += 1
                    if k < len(lines) and _STATE_BULLET_RE.fullmatch(lines[k]):
                        j = k
                        continue
                    break
                if _STATE_BULLET_RE.fullmatch(lines[j]):
                    run_last = j
                    j += 1
                    continue
                break
            for k in range(run_first, run_last + 1):
                lines_to_strip.add(k)
            i = run_last + 1

    # Pass 1e: reposition trailing standalone markers. Some models put
    # the `[[entity:…]]` block AFTER the follow-up sentence, which
    # makes the tile render at the bottom of the bubble instead of
    # inline next to the prose that introduces the device. Move each
    # trailing marker up to just after the first paragraph that
    # mentions one of its entity_ids by friendly_name or entity_id.
    trailing_marker_idxs: list[int] = []
    k = len(lines) - 1
    while k >= 0:
        if not lines[k].strip():
            k -= 1
            continue
        if k in standalone_markers and k not in lines_to_strip:
            trailing_marker_idxs.insert(0, k)
            k -= 1
            continue
        break

    for marker_idx in trailing_marker_idxs:
        marker_eids = standalone_markers[marker_idx]
        if not marker_eids:
            continue
        eid_friendly: dict[str, str] = {}
        for ent in entities:
            ent_id = ent.get("entity_id")
            if ent_id in marker_eids:
                fname = ((ent.get("attributes") or {}).get("friendly_name") or "").strip()
                if fname:
                    eid_friendly[ent_id] = fname

        target_line: int | None = None
        for li, line in enumerate(lines):
            if li in lines_to_strip or li == marker_idx or not line.strip():
                continue
            line_lower = line.lower()
            for fname in eid_friendly.values():
                for needle in _name_search_forms(fname):
                    pos = line_lower.find(needle)
                    if pos < 0:
                        continue
                    end = pos + len(needle)
                    before_ok = pos == 0 or not line_lower[pos - 1].isalnum()
                    after_ok = end >= len(line_lower) or not line_lower[end].isalnum()
                    if before_ok and after_ok:
                        target_line = li
                        break
                if target_line is not None:
                    break
            if target_line is None:
                for eid in marker_eids:
                    if re.search(rf"(?<![a-z0-9_.]){re.escape(eid)}(?![a-z0-9_.])", line):
                        target_line = li
                        break
            if target_line is not None:
                break

        if target_line is None:
            continue
        # Walk forward to the end of the target paragraph.
        para_end = target_line
        while (
            para_end + 1 < len(lines)
            and lines[para_end + 1].strip()
            and (para_end + 1) not in lines_to_strip
            and (para_end + 1) != marker_idx
        ):
            para_end += 1
        # Skip if the marker is already right after the target paragraph
        # (only blank lines separating them).
        only_blanks_between = all(not lines[m].strip() for m in range(para_end + 1, marker_idx))
        if only_blanks_between:
            continue
        lines_to_strip.add(marker_idx)
        # Merge with anything already queued at this insertion slot so
        # two trailing markers pointing at the same paragraph don't
        # overwrite each other (each was being repositioned to
        # `para_end + 1` independently — the second `insertions[...] =`
        # was dropping every entity from the first). Preserve order
        # and dedupe in case both markers list the same entity.
        existing = insertions.get(para_end + 1, [])
        merged = list(dict.fromkeys([*existing, *sorted(marker_eids)]))
        insertions[para_end + 1] = merged

    # Pass 2: prose mentions — last-resort fallback ONLY when the LLM
    # gave us no structural cue (no markers, no bullet runs). Without
    # this guard, area sub-headings like "### Kitchen" false-positive
    # match against media_player.kitchen and surface spurious tiles.
    prose_eids: list[str] = []
    fallback_mode = not already and not captured_in_bullets

    def _under_cap() -> bool:
        return len(captured_in_bullets) + len(prose_eids) < _AUTO_MARKER_MAX_IDS

    if fallback_mode:
        for i, line in enumerate(lines):
            if i in lines_to_strip or not _under_cap():
                continue
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if "[[entit" in line:
                continue
            line_lower = line.lower()
            consumed: list[tuple[int, int]] = []
            for eid, name in candidates:
                if (
                    eid in captured_in_bullets
                    or eid in prose_eids
                    or eid in already
                    or not _under_cap()
                ):
                    continue
                matched = False
                for needle in _name_search_forms(name):
                    idx = 0
                    while idx < len(line_lower):
                        pos = line_lower.find(needle, idx)
                        if pos < 0:
                            break
                        end = pos + len(needle)
                        before_ok = pos == 0 or not line_lower[pos - 1].isalnum()
                        after_ok = end >= len(line_lower) or not line_lower[end].isalnum()
                        overlap = any(pos < e and end > s for s, e in consumed)
                        if before_ok and after_ok and not overlap:
                            consumed.append((pos, end))
                            prose_eids.append(eid)
                            matched = True
                            break
                        idx = pos + 1
                    if matched:
                        break
            for ent in entities:
                if not _under_cap():
                    break
                eid = ent.get("entity_id")
                if not eid or eid in already or eid in captured_in_bullets or eid in prose_eids:
                    continue
                if re.search(rf"(?<![a-z0-9_.]){re.escape(eid)}(?![a-z0-9_.])", line):
                    prose_eids.append(eid)

    if not lines_to_strip and not prose_eids:
        return text

    # Wrap each inserted marker in blank lines on both sides so the
    # tile grid has consistent spacing above and below. The original
    # surrounding lines may or may not already have blanks (depends on
    # whether we stripped the header, the bullets, or a trailing
    # marker), and the asymmetry shows up as a noticeably bigger gap
    # on one side of the tile. The `\n{3,}` collapse below dedups any
    # extra blanks this produces.
    out: list[str] = []
    for i, line in enumerate(lines):
        if i in insertions:
            out.append("")
            out.append(f"[[entities:{','.join(insertions[i])}]]")
            out.append("")
        if i not in lines_to_strip:
            out.append(line)

    result = "\n".join(out)
    if prose_eids:
        result = result.rstrip() + f"\n\n[[entities:{','.join(prose_eids)}]]"
    return re.sub(r"\n{3,}", "\n\n", result).strip()


def _create_tool_executor(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
) -> Any:
    """Create a ToolExecutor if a DeviceManager is available."""
    from .tool_executor import ToolExecutor

    device_mgr = _get_device_manager(hass)
    is_admin = getattr(getattr(connection, "user", None), "is_admin", False)
    return ToolExecutor(hass, device_mgr, is_admin=is_admin) if device_mgr else None


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/chat",
        vol.Required("message"): str,
        vol.Optional("session_id"): str,
    }
)
async def _handle_websocket_chat(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Handle chat messages from the side panel.

    The LLM classifies the intent itself (command / automation / clarification / answer).
    Commands are executed immediately; automations are returned as proposals for user review.
    All turns are persisted to the session store so conversations can be resumed.
    """
    if not _require_admin(connection, msg):
        return

    llm = _find_llm(hass)
    if llm is None:
        connection.send_error(msg["id"], "not_initialized", "Selora AI LLM not initialized")
        return

    store: ConversationStore = hass.data[DOMAIN].setdefault("_conv_store", ConversationStore(hass))
    session, session_id, _session_created = await _resolve_or_create_session(
        store, msg.get("session_id") or ""
    )
    stored_messages = (session or {}).get("messages", [])
    user_message = msg["message"]

    # Reconcile scene store so session context reflects external edits
    scene_store = _get_scene_store(hass)
    await scene_store.async_reconcile_yaml()

    # Build history and collect context BEFORE appending the new user turn —
    # append_message() mutates stored_messages in place.
    refining = _find_active_refining_yaml(stored_messages, user_message)
    refining_scene = _find_active_refining_scene(stored_messages)
    scenes = _find_active_scenes(session, stored_messages)
    history = _build_history_from_session(
        stored_messages,
        skip_refining_yaml=refining is not None,
        skip_refining_scene_yaml=refining_scene is not None,
    )

    await store.append_message(session_id, "user", user_message)

    entities = _collect_entity_states(hass)
    automations = _collect_existing_automations(hass)
    tool_executor = _create_tool_executor(hass, connection)
    area_names = await get_area_names(hass)

    result = await llm.architect_chat(
        user_message,
        entities,
        existing_automations=automations,
        history=history,
        tool_executor=tool_executor,
        refining_context=refining,
        refining_scene_context=refining_scene,
        scene_context=scenes or None,
        areas=area_names,
    )

    if "error" in result and result.get("intent") != "answer":
        connection.send_error(msg["id"], "llm_error", result["error"])
        return

    intent_type = result.get("intent", "answer")
    response_text = result.get("response", "I'm not sure how to help with that.")

    # Execute immediate commands
    executed: list[str] = []
    schedule_id: str | None = None
    delay_val = result.get("delay_seconds")
    if (
        intent_type == "delayed_command"
        and not result.get("scheduled_time")
        and isinstance(delay_val, (int, float))
        and not isinstance(delay_val, bool)
        and delay_val <= 0
    ):
        intent_type = "command"
    if intent_type == "command":
        calls = result.get("calls", [])
        executed, error_suffix = await _execute_command_calls(hass, calls)
        response_text += error_suffix
        already_shown = _entity_ids_already_in_text(response_text)
        entity_ids = [e for e in _entity_ids_from_calls(calls) if e not in already_shown]
        if entity_ids:
            response_text += f"\n\n[[entities:{','.join(entity_ids)}]]"
    elif intent_type == "answer":
        response_text = _inject_entity_markers(response_text, entities)

    if intent_type in ("delayed_command", "cancel"):
        intent_type, response_text, schedule_id = await _handle_scheduled_intent(
            hass, intent_type, result, session_id
        )

    # Scenes are NOT created immediately — they are stored as proposals with
    # scene_status="pending" so the user reviews/accepts before the YAML is
    # written. This matches the streaming chat handler so both endpoints have
    # the same review flow.
    scene_payload = result.get("scene")
    scene_yaml_str = result.get("scene_yaml")
    refine_scene_id = result.get("refine_scene_id")

    # Persist the assistant response
    await store.append_message(
        session_id,
        "assistant",
        response_text,
        intent=intent_type,
        automation=result.get("automation"),
        automation_yaml=result.get("automation_yaml"),
        description=result.get("description"),
        automation_status="pending" if result.get("automation") else None,
        calls=result.get("calls") if intent_type == "command" else None,
        risk_assessment=result.get("risk_assessment"),
        tool_calls=result.get("tool_calls"),
        scene=scene_payload,
        scene_yaml=scene_yaml_str,
        scene_status="pending" if scene_payload else None,
        refine_scene_id=refine_scene_id if scene_payload else None,
    )

    updated_session = await store.get_session(session_id)
    assistant_message_index = len((updated_session or {}).get("messages", [])) - 1

    refining_automation_id = None
    for m in stored_messages:
        if m.get("automation_status") == "refining" and m.get("automation_id"):
            refining_automation_id = m["automation_id"]

    connection.send_result(
        msg["id"],
        {
            "session_id": session_id,
            "intent": intent_type,
            "response": response_text,
            "description": result.get("description"),
            "automation": result.get("automation"),
            "automation_yaml": result.get("automation_yaml"),
            "risk_assessment": result.get("risk_assessment"),
            "automation_message_index": assistant_message_index
            if result.get("automation")
            else None,
            "executed": executed,
            "schedule_id": schedule_id,
            "config_issue": result.get("config_issue", False),
            "validation_error": result.get("validation_error"),
            "validation_target": result.get("validation_target"),
            "refining_automation_id": refining_automation_id,
            "scene": scene_payload,
            "scene_yaml": scene_yaml_str,
            "scene_status": "pending" if scene_payload else None,
            "refine_scene_id": refine_scene_id,
            "scene_message_index": assistant_message_index if scene_payload else None,
        },
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/chat_stream",
        vol.Required("message"): str,
        vol.Optional("session_id"): str,
    }
)
async def _handle_websocket_chat_stream(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Stream chat responses to the frontend via subscription events.

    All turns are persisted to the session store so conversations can be resumed.

    Event types sent to the client:
      {"type": "token", "text": "..."}          — incremental text chunk
      {"type": "done", "response": ..., ...}    — final parsed result
      {"type": "error", "message": "..."}       — on failure
    """
    if not _require_admin(connection, msg):
        return

    # Set up subscription with cancellation support — when the frontend
    # calls _stopStreaming() the unsubscribe cancels the running task so
    # we stop consuming LLM tokens and never execute pending service calls.
    current_task = asyncio.current_task()

    def _cancel_stream() -> None:
        if current_task is not None and not current_task.done():
            current_task.cancel()

    connection.subscriptions[msg["id"]] = _cancel_stream
    connection.send_result(msg["id"])

    llm = _find_llm(hass)
    if llm is None:
        connection.send_message(
            websocket_api.event_message(
                msg["id"], {"type": "error", "message": "Selora AI LLM not initialized"}
            )
        )
        return

    store: ConversationStore = hass.data[DOMAIN].setdefault("_conv_store", ConversationStore(hass))
    session, session_id, session_created = await _resolve_or_create_session(
        store, msg.get("session_id") or ""
    )
    stored_messages = (session or {}).get("messages", [])
    user_message = msg["message"]
    # Tracks whether we successfully wrote any messages this turn. Used
    # below: if the LLM fails before any append AND we created the
    # session in this call, we delete it so the sidebar doesn't fill
    # with empty "New conversation" entries from failed retries.
    persisted_any = False

    # Reconcile scene store so session context reflects external edits
    scene_store = _get_scene_store(hass)
    await scene_store.async_reconcile_yaml()

    # Build history and collect context. Note: we deliberately do NOT
    # persist the user message yet — if the LLM call fails (transport
    # error, provider unreachable) we want the session to stay clean
    # rather than accumulate dead turns. The user-side append happens
    # below, just before the assistant turn, once we have a real reply.
    refining = _find_active_refining_yaml(stored_messages, user_message)
    refining_scene = _find_active_refining_scene(stored_messages)
    scenes = _find_active_scenes(session, stored_messages)
    history = _build_history_from_session(
        stored_messages,
        skip_refining_yaml=refining is not None,
        skip_refining_scene_yaml=refining_scene is not None,
    )

    async def _discard_empty_session_if_needed() -> None:
        """Drop a brand-new session that never got a message written.

        Only deletes when we created it in this call — pre-existing
        sessions (the user's prior conversation) are left alone even if
        their newest turn failed; only the failed-turn pair is missing.
        """
        if session_created and not persisted_any:
            try:
                await store.delete_session(session_id)
            except Exception:
                _LOGGER.debug(
                    "Failed to clean up empty session %s after error",
                    session_id,
                )

    # Inject active context (automation refinement, known scenes) into the
    # current turn so the LLM always sees it even if history gets trimmed.
    try:
        entities = _collect_entity_states(hass)
        automations = _collect_existing_automations(hass)
        tool_executor = _create_tool_executor(hass, connection)
        area_names = await get_area_names(hass)

        full_text = ""
        chunk_count = 0
        looks_like_json = False
        # Total characters already streamed to the client. Used to clip
        # the chunk that introduces the JSON-block opener so the prose
        # prefix still streams but the JSON tokens after it don't.
        sent_chars = 0
        async for chunk in llm.architect_chat_stream(
            user_message,
            entities,
            existing_automations=automations,
            history=history,
            tool_executor=tool_executor,
            refining_context=refining,
            refining_scene_context=refining_scene,
            scene_context=scenes or None,
            areas=area_names,
        ):
            full_text += chunk
            chunk_count += 1
            # Suppress streaming tokens when the LLM is emitting a
            # structural block the "done" event will carry as parsed data:
            # • Full-JSON response: entire response starts with `{`
            # • Fenced structural block: ```command / ```delayed_command /
            #   ```cancel opener detected mid-stream
            # We deliberately do NOT suppress on arbitrary `{"` in the
            # prose — normal answers often include inline JSON examples
            # (e.g. `{"state":"on"}`) that must still reach the client.
            if not looks_like_json:
                opener_idx = -1
                if full_text.lstrip().startswith("{"):
                    opener_idx = full_text.index("{")
                else:
                    for needle in (
                        "```command",
                        "```delayed_command",
                        "```cancel",
                    ):
                        idx = full_text.find(needle)
                        if idx >= 0 and (opener_idx < 0 or idx < opener_idx):
                            opener_idx = idx
                if opener_idx >= 0:
                    looks_like_json = True
                    # Stream only the prose portion of this chunk (if
                    # any) up to the opener position. Anything past it
                    # is JSON tokens the user shouldn't see.
                    send_until = max(0, opener_idx - sent_chars)
                    prefix = chunk[:send_until]
                    if prefix:
                        connection.send_message(
                            websocket_api.event_message(
                                msg["id"], {"type": "token", "text": prefix}
                            )
                        )
                        sent_chars += len(prefix)
                else:
                    connection.send_message(
                        websocket_api.event_message(msg["id"], {"type": "token", "text": chunk})
                    )
                    sent_chars += len(chunk)

        # Visibility for "stream ended cleanly but the bubble looks
        # truncated" diagnosis. Most useful when comparing Selora Cloud
        # (proxied via Connect / OpenRouter) against direct providers:
        # if `total_chars` differs sharply for the same prompt, the
        # truncation is upstream of HA, not in the integration.
        _LOGGER.info(
            "Chat stream complete: chunks=%d total_chars=%d provider=%s",
            chunk_count,
            len(full_text),
            getattr(llm._provider, "provider_type", "unknown"),
        )

        parsed = llm.parse_streamed_response(full_text, entities=entities)
        intent_type = parsed.get("intent", "answer")
        response_text = parsed.get("response", full_text)

        # Execute immediate commands
        executed: list[str] = []
        schedule_id: str | None = None
        # Normalise: if the LLM emits "delayed_command" with an explicit
        # delay_seconds of 0 or negative and no scheduled_time, treat it as
        # an immediate command. Missing delay_seconds is left to the scheduler
        # which already downgrades to "answer" — we must not execute those
        # immediately as the request may have been a genuine "later" command
        # with an incomplete LLM response.
        delay_val = parsed.get("delay_seconds")
        if (
            intent_type == "delayed_command"
            and not parsed.get("scheduled_time")
            and isinstance(delay_val, (int, float))
            and not isinstance(delay_val, bool)
            and delay_val <= 0
        ):
            intent_type = "command"
        if intent_type == "command":
            calls = parsed.get("calls", [])
            executed, error_suffix = await _execute_command_calls(hass, calls)
            response_text += error_suffix
            # Append entity tile markers only for devices the LLM didn't
            # already reference in its prose (the streaming prompt asks it
            # to embed [[entity:…]] markers; appending duplicates here would
            # render two tile cards for the same device).
            already_shown = _entity_ids_already_in_text(response_text)
            entity_ids = [e for e in _entity_ids_from_calls(calls) if e not in already_shown]
            if entity_ids:
                response_text += f"\n\n[[entities:{','.join(entity_ids)}]]"
        elif intent_type == "answer":
            response_text = _inject_entity_markers(response_text, entities)
        elif intent_type in ("delayed_command", "cancel"):
            intent_type, response_text, schedule_id = await _handle_scheduled_intent(
                hass, intent_type, parsed, session_id
            )

        # Scenes are NOT created immediately — they are stored as proposals
        # with scene_status="pending" so the user can review before applying.
        scene_payload = parsed.get("scene")
        scene_yaml_str = parsed.get("scene_yaml")
        refine_scene_id = parsed.get("refine_scene_id")

        # Persist user + assistant as a pair, only after the stream has
        # produced a usable reply. Splitting these would risk a half-turn
        # being saved if the assistant append fails, but they live in the
        # same list so the next reload sees a coherent conversation.
        await store.append_message(session_id, "user", user_message)
        await store.append_message(
            session_id,
            "assistant",
            response_text,
            intent=intent_type,
            automation=parsed.get("automation"),
            automation_yaml=parsed.get("automation_yaml"),
            description=parsed.get("description"),
            automation_status="pending" if parsed.get("automation") else None,
            calls=parsed.get("calls") if intent_type == "command" else None,
            risk_assessment=parsed.get("risk_assessment"),
            scene=scene_payload,
            scene_yaml=scene_yaml_str,
            scene_status="pending" if scene_payload else None,
            refine_scene_id=refine_scene_id if scene_payload else None,
            quick_actions=parsed.get("quick_actions"),
        )
        persisted_any = True

        updated_session = await store.get_session(session_id)
        assistant_message_index = len((updated_session or {}).get("messages", [])) - 1

        # Auto-generate a better title if still the default
        current_title = (updated_session or {}).get("title", "")
        is_default_title = current_title == "New conversation" or current_title == user_message[:60]
        if is_default_title:

            async def _generate_title() -> None:
                try:
                    title = await llm.generate_session_title(user_message, response_text)
                    await store.update_session_title(session_id, title)
                    _LOGGER.debug("Auto-titled session %s: %s", session_id, title)
                except Exception:
                    _LOGGER.debug("Background title generation failed for %s", session_id)

            hass.async_create_task(_generate_title())

        refining_automation_id = None
        for m in stored_messages:
            if m.get("automation_status") == "refining" and m.get("automation_id"):
                refining_automation_id = m["automation_id"]

        connection.send_message(
            websocket_api.event_message(
                msg["id"],
                {
                    "type": "done",
                    "session_id": session_id,
                    "intent": intent_type,
                    "response": response_text,
                    "automation": parsed.get("automation"),
                    "automation_yaml": parsed.get("automation_yaml"),
                    "risk_assessment": parsed.get("risk_assessment"),
                    "automation_message_index": assistant_message_index
                    if parsed.get("automation")
                    else None,
                    "validation_error": parsed.get("validation_error"),
                    "validation_target": parsed.get("validation_target"),
                    "refining_automation_id": refining_automation_id,
                    "executed": executed,
                    "schedule_id": schedule_id,
                    "scene": scene_payload,
                    "scene_yaml": scene_yaml_str,
                    "scene_status": "pending" if scene_payload else None,
                    "refine_scene_id": refine_scene_id,
                    "scene_message_index": assistant_message_index if scene_payload else None,
                    "quick_actions": parsed.get("quick_actions"),
                },
            )
        )
    except asyncio.CancelledError:
        _LOGGER.debug("Streaming chat cancelled by client")
        await _discard_empty_session_if_needed()
    except ConnectionError as exc:
        # Transport / provider failure — emit a transient error event so
        # the panel can surface it as a one-off message, but do NOT
        # persist anything to the session store. Reloading the chat
        # should not show stale "couldn't reach the LLM provider" bubbles.
        _LOGGER.warning("Streaming chat unreachable: %s", exc)
        connection.send_message(
            websocket_api.event_message(
                msg["id"],
                {
                    "type": "error",
                    "message": str(exc)
                    if str(exc)
                    else (
                        "Couldn't reach the LLM provider. Check your connection "
                        "in Settings, then try again."
                    ),
                },
            )
        )
        await _discard_empty_session_if_needed()
    except Exception as exc:
        _LOGGER.exception("Streaming chat failed")
        connection.send_message(
            websocket_api.event_message(msg["id"], {"type": "error", "message": str(exc)})
        )
        await _discard_empty_session_if_needed()


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_sessions",
    }
)
async def _handle_websocket_get_sessions(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return a list of conversation session summaries."""
    if not _require_admin(connection, msg):
        return

    store: ConversationStore = hass.data[DOMAIN].setdefault("_conv_store", ConversationStore(hass))
    sessions = await store.list_sessions()
    connection.send_result(msg["id"], sessions)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_session",
        vol.Required("session_id"): str,
    }
)
async def _handle_websocket_get_session(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the full message history for a session."""
    if not _require_admin(connection, msg):
        return

    store: ConversationStore = hass.data[DOMAIN].setdefault("_conv_store", ConversationStore(hass))
    session = await store.get_session(msg["session_id"])
    if not session:
        connection.send_error(msg["id"], "not_found", "Session not found")
        return
    connection.send_result(msg["id"], session)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/new_session",
    }
)
async def _handle_websocket_new_session(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create a new empty conversation session."""
    if not _require_admin(connection, msg):
        return

    store: ConversationStore = hass.data[DOMAIN].setdefault("_conv_store", ConversationStore(hass))
    session = await store.create_session()
    connection.send_result(msg["id"], {"session_id": session["id"]})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/rename_session",
        vol.Required("session_id"): str,
        vol.Required("title"): str,
    }
)
async def _handle_websocket_rename_session(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Rename a conversation session."""
    if not _require_admin(connection, msg):
        return

    store: ConversationStore = hass.data[DOMAIN].setdefault("_conv_store", ConversationStore(hass))
    ok = await store.update_session_title(msg["session_id"], msg["title"])
    if ok:
        connection.send_result(msg["id"], {"status": "ok"})
    else:
        connection.send_error(msg["id"], "not_found", "Session not found")


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/delete_session",
        vol.Required("session_id"): str,
    }
)
async def _handle_websocket_delete_session(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete a conversation session."""
    if not _require_admin(connection, msg):
        return

    store: ConversationStore = hass.data[DOMAIN].setdefault("_conv_store", ConversationStore(hass))
    deleted = await store.delete_session(msg["session_id"])
    if not deleted:
        connection.send_error(msg["id"], "not_found", "Session not found")
        return
    connection.send_result(msg["id"], {"status": "deleted"})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/set_automation_status",
        vol.Required("session_id"): str,
        vol.Required("message_index"): int,
        vol.Required("status"): str,
    }
)
async def _handle_websocket_set_automation_status(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Update the acceptance status of an automation proposal in a session."""
    if not _require_admin(connection, msg):
        return

    valid_statuses = {"pending", "accepted", "declined", "saved", "refining"}
    if msg["status"] not in valid_statuses:
        connection.send_error(
            msg["id"], "invalid_status", f"Status must be one of {valid_statuses}"
        )
        return

    store: ConversationStore = hass.data[DOMAIN].setdefault("_conv_store", ConversationStore(hass))
    ok = await store.set_automation_status(msg["session_id"], msg["message_index"], msg["status"])
    if not ok:
        connection.send_error(msg["id"], "not_found", "Session or message not found")
        return
    connection.send_result(msg["id"], {"status": "updated"})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/set_scene_status",
        vol.Required("session_id"): str,
        vol.Required("message_index"): int,
        vol.Required("status"): str,
    }
)
async def _handle_websocket_set_scene_status(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Update the acceptance status of a scene proposal in a session."""
    if not _require_admin(connection, msg):
        return

    valid_statuses = {"pending", "saved", "declined", "refining"}
    if msg["status"] not in valid_statuses:
        connection.send_error(
            msg["id"], "invalid_status", f"Status must be one of {valid_statuses}"
        )
        return

    store: ConversationStore = hass.data[DOMAIN].setdefault("_conv_store", ConversationStore(hass))
    ok = await store.set_scene_status(msg["session_id"], msg["message_index"], msg["status"])
    if not ok:
        connection.send_error(msg["id"], "not_found", "Session or message not found")
        return
    connection.send_result(msg["id"], {"status": "updated"})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/accept_scene",
        vol.Required("session_id"): str,
        vol.Required("message_index"): int,
    }
)
async def _handle_websocket_accept_scene(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Accept a scene proposal — actually create it in HA now.

    The refinement target is read from the stored proposal, never from the
    websocket payload, so a stale or crafted client cannot steer the accept
    into overwriting an unrelated Selora-managed scene.
    """
    if not _require_admin(connection, msg):
        return

    store: ConversationStore = hass.data[DOMAIN].setdefault("_conv_store", ConversationStore(hass))
    session = await store.get_session(msg["session_id"])
    if not session:
        connection.send_error(msg["id"], "not_found", "Session not found")
        return
    msgs = session.get("messages", [])
    mi = msg["message_index"]
    if mi < 0 or mi >= len(msgs):
        connection.send_error(msg["id"], "not_found", "Message not found")
        return
    chat_msg = msgs[mi]
    scene_data = chat_msg.get("scene")
    if not scene_data:
        connection.send_error(msg["id"], "no_scene", "No scene data on this message")
        return

    # Atomic check-and-reserve. Single-threaded asyncio guarantees no other
    # coroutine can interleave between this check and the in-memory status
    # mutation below — a concurrent accept (double-click, retry, stale tab,
    # second admin tab) will resume after this point and see "accepting",
    # so it cannot also reach the create path. The reservation is purely
    # in-memory; we only persist on success ("saved") or on failure
    # (restored to the original status), so a crash cannot leave a
    # message stuck in "accepting".
    current_status = chat_msg.get("scene_status")
    if chat_msg.get("scene_id") or current_status not in ("pending", "refining"):
        connection.send_error(
            msg["id"],
            "already_handled",
            f"Scene proposal is already {current_status or 'saved'}",
        )
        return
    chat_msg["scene_status"] = "accepting"

    try:
        from .scene_utils import async_create_scene  # noqa: PLC0415

        scenes_ctx = _find_active_scenes(session, msgs)
        # Always pass a concrete set (even empty) so async_create_scene cannot
        # fall back to its allow-any-Selora-scene path when the session has
        # no recorded scenes.
        session_scene_ids = {s[0] for s in scenes_ctx}
        scene_result = await async_create_scene(
            hass,
            scene_data,
            existing_scene_id=chat_msg.get("refine_scene_id"),
            session_scene_ids=session_scene_ids,
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to create scene on accept: %s", exc)
        chat_msg["scene_status"] = current_status
        connection.send_error(msg["id"], "create_failed", str(exc))
        return

    if scene_result is not None:
        try:
            scene_store = _get_scene_store(hass)
            await scene_store.async_add_scene(
                scene_result["scene_id"],
                scene_result["name"],
                scene_result["entity_count"],
                session_id=msg["session_id"],
                entity_id=scene_result.get("entity_id"),
                content_hash=scene_result.get("content_hash"),
            )
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Failed to record scene %s in store", scene_result["scene_id"])

    await store.set_scene_status(
        msg["session_id"],
        mi,
        "saved",
        scene_id=scene_result["scene_id"],
        entity_id=scene_result.get("entity_id"),
    )
    connection.send_result(
        msg["id"],
        {
            "scene_id": scene_result["scene_id"],
            "entity_id": scene_result.get("entity_id"),
        },
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/create_automation",
        vol.Required("automation"): dict,
        vol.Optional("session_id"): str,
    }
)
async def _handle_websocket_create_automation(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create a new automation from the side panel."""
    if not _require_admin(connection, msg):
        return

    automation_data = msg["automation"]

    # Basic validation
    has_trigger = automation_data.get("trigger") or automation_data.get("triggers")
    has_action = automation_data.get("action") or automation_data.get("actions")

    if not automation_data.get("alias") or not has_trigger or not has_action:
        connection.send_error(
            msg["id"],
            "invalid_format",
            "Invalid automation structure (missing alias, trigger, or action)",
        )
        return

    try:
        from .automation_utils import async_create_automation

        # Per project policy automations are always written disabled —
        # the user must review and enable them in Home Assistant. We
        # still surface risk_level so the panel can flag elevated-risk
        # payloads (shell_command, webhook trigger, etc.) for extra
        # caution before the user toggles them on.
        result = await async_create_automation(
            hass, automation_data, session_id=msg.get("session_id")
        )
        if result["success"]:
            connection.send_result(
                msg["id"],
                {
                    "status": "success",
                    "automation_id": result["automation_id"],
                    "risk_level": result.get("risk_level", "normal"),
                },
            )
        else:
            connection.send_error(
                msg["id"], "creation_failed", "Failed to write automation to file"
            )
    except Exception as exc:
        _LOGGER.exception("Error creating automation")
        connection.send_error(msg["id"], "error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/apply_automation_yaml",
        vol.Required("yaml_text"): str,
        vol.Optional("session_id"): str,
        vol.Optional("automation_id"): str,
    }
)
async def _handle_websocket_apply_automation_yaml(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Parse raw YAML text and create or update an automation."""
    if not _require_admin(connection, msg):
        return

    from .automation_utils import (
        _parse_automation_yaml,
        async_create_automation,
        async_update_automation,
    )

    parsed = await hass.async_add_executor_job(_parse_automation_yaml, msg["yaml_text"])
    if parsed is None:
        connection.send_error(msg["id"], "parse_error", "Invalid YAML — could not parse automation")
        return

    has_trigger = parsed.get("trigger") or parsed.get("triggers")
    has_action = parsed.get("action") or parsed.get("actions")
    if not parsed.get("alias") or not has_trigger or not has_action:
        connection.send_error(
            msg["id"], "invalid_format", "Automation must have alias, trigger, and action"
        )
        return

    automation_id = msg.get("automation_id")
    try:
        if automation_id:
            success = await async_update_automation(
                hass,
                automation_id,
                parsed,
                session_id=msg.get("session_id"),
                version_message="Refined via chat",
            )
            if success:
                connection.send_result(msg["id"], {"status": "updated"})
            else:
                connection.send_error(
                    msg["id"], "not_found", "Automation not found in automations.yaml"
                )
        else:
            # All chat-driven creates land disabled per project policy —
            # the user must enable manually after review. Pass risk_level
            # back so the panel can flag elevated-risk YAML.
            result = await async_create_automation(hass, parsed, session_id=msg.get("session_id"))
            if result["success"]:
                connection.send_result(
                    msg["id"],
                    {
                        "status": "created",
                        "automation_id": result["automation_id"],
                        "risk_level": result.get("risk_level", "normal"),
                    },
                )
            else:
                connection.send_error(msg["id"], "creation_failed", "Failed to write automation")
    except Exception as exc:
        _LOGGER.exception("Error applying automation YAML")
        connection.send_error(msg["id"], "error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/update_automation_yaml",
        vol.Required("automation_id"): str,
        vol.Required("yaml_text"): str,
        vol.Optional("session_id"): str,
        vol.Optional("version_message"): str,
    }
)
async def _handle_websocket_update_automation_yaml(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Parse raw YAML text and update an existing automation in automations.yaml."""
    if not _require_admin(connection, msg):
        return

    from .automation_utils import _parse_automation_yaml, async_update_automation

    parsed = await hass.async_add_executor_job(_parse_automation_yaml, msg["yaml_text"])
    if parsed is None:
        connection.send_error(msg["id"], "parse_error", "Invalid YAML — could not parse automation")
        return

    try:
        success = await async_update_automation(
            hass,
            msg["automation_id"],
            parsed,
            session_id=msg.get("session_id"),
            version_message=msg.get("version_message", "Updated via YAML editor"),
        )
        if success:
            connection.send_result(msg["id"], {"status": "updated"})
        else:
            connection.send_error(
                msg["id"], "not_found", "Automation not found in automations.yaml"
            )
    except Exception as exc:
        _LOGGER.exception("Error updating automation YAML")
        connection.send_error(msg["id"], "error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/rename_automation",
        vol.Required("automation_id"): str,
        vol.Required("alias"): str,
    }
)
async def _handle_websocket_rename_automation(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Rename an existing automation's alias in automations.yaml and reload."""
    if not _require_admin(connection, msg):
        return

    from pathlib import Path

    from .automation_utils import (
        AUTOMATIONS_YAML_LOCK,
        _read_automations_yaml,
        _write_automations_yaml,
    )

    automation_id = msg["automation_id"]
    new_alias = msg["alias"].strip()
    if not new_alias:
        connection.send_error(msg["id"], "invalid", "Alias cannot be empty")
        return

    automations_path = Path(hass.config.config_dir) / "automations.yaml"
    try:
        # Hold the shared lock for the full read/modify/write/reload span so a
        # concurrent collector run or chat-driven create cannot interleave.
        async with AUTOMATIONS_YAML_LOCK:
            existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)
            found = False
            for a in existing:
                if a.get("id") == automation_id:
                    a["alias"] = new_alias
                    found = True
                    break
            if not found:
                connection.send_error(msg["id"], "not_found", "Automation not found")
                return
            await hass.async_add_executor_job(_write_automations_yaml, automations_path, existing)
            await hass.services.async_call("automation", "reload", blocking=True)
        connection.send_result(msg["id"], {"status": "renamed"})
    except Exception as exc:
        _LOGGER.exception("Error renaming automation")
        connection.send_error(msg["id"], "error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_suggestions",
    }
)
async def _handle_websocket_get_suggestions(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return proactive pattern-based suggestions (fallback to collector suggestions)."""
    if not _require_admin(connection, msg):
        return

    suggestions = hass.data.get(DOMAIN, {}).get("proactive_suggestions", [])
    if not suggestions:
        suggestions = hass.data.get(DOMAIN, {}).get("latest_suggestions", [])
    connection.send_result(msg["id"], suggestions)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/generate_suggestions",
    }
)
async def _handle_websocket_generate_suggestions(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Trigger an on-demand LLM analysis + pattern scan to generate fresh suggestions."""
    if not _require_admin(connection, msg):
        return

    domain_data = hass.data.get(DOMAIN, {})

    # Find the runtime entry with collector and/or pattern engine
    runtime: dict[str, Any] | None = None
    for key, val in domain_data.items():
        if not isinstance(key, str) or key.startswith("_"):
            continue
        if isinstance(val, dict) and "collector" in val:
            runtime = val
            break

    if runtime is None:
        connection.send_error(msg["id"], "not_ready", "Selora AI is not fully initialized yet")
        return

    try:
        # 1. Run the fast, local-only pattern engine first (milliseconds)
        pattern_engine = runtime.get("pattern_engine")
        suggestion_generator = runtime.get("suggestion_generator")
        if pattern_engine and suggestion_generator:
            try:
                async with asyncio.timeout(15):
                    patterns = await pattern_engine.scan()
                    suggestions = await suggestion_generator.generate_from_patterns(patterns)
                    if suggestions:
                        existing = hass.data.get(DOMAIN, {}).get("proactive_suggestions", [])
                        existing.extend(suggestions)
                        hass.data[DOMAIN]["proactive_suggestions"] = existing[-50:]
                        async_dispatcher_send(hass, SIGNAL_PROACTIVE_SUGGESTIONS)
            except TimeoutError:
                _LOGGER.warning("Pattern scan timed out after 15s, continuing with LLM")

        # 2. Run the LLM analysis with a shorter interactive timeout (30s)
        collector = runtime.get("collector")
        if collector:
            try:
                async with asyncio.timeout(30):
                    await collector._collect_analyze_log(force=True)
            except TimeoutError:
                _LOGGER.warning(
                    "On-demand LLM analysis timed out after 30s — returning existing suggestions"
                )

        # 3. Build set of existing automation aliases to exclude from suggestions
        existing_aliases: set[str] = set()
        for state in hass.states.async_all("automation"):
            alias = (state.attributes.get("friendly_name") or "").strip().lower()
            if alias:
                existing_aliases.add(alias)

        # 4. Return combined results: proactive first, then collector — skip existing
        #    Deduplicate by both alias AND content fingerprint (#46)
        all_suggestions = []
        seen_aliases: set[str] = set()
        seen_fingerprints: set[str] = set()
        for s in list(hass.data.get(DOMAIN, {}).get("proactive_suggestions", [])):
            alias = (s.get("alias") or "").strip().lower()
            if alias and (alias in existing_aliases or alias in seen_aliases):
                continue
            auto_data = s.get("automation_data", s)
            fp = suggestion_content_fingerprint(auto_data)
            if fp in seen_fingerprints:
                continue
            all_suggestions.append(s)
            if alias:
                seen_aliases.add(alias)
            seen_fingerprints.add(fp)
        for s in hass.data.get(DOMAIN, {}).get("latest_suggestions", []):
            alias = (s.get("alias") or "").strip().lower()
            if alias and (alias in existing_aliases or alias in seen_aliases):
                continue
            auto_data = s.get("automation_data", s)
            fp = suggestion_content_fingerprint(auto_data)
            if fp in seen_fingerprints:
                continue
            all_suggestions.append(s)
            if alias:
                seen_aliases.add(alias)
            seen_fingerprints.add(fp)

        connection.send_result(msg["id"], all_suggestions)
    except Exception as exc:
        _LOGGER.exception("On-demand suggestion generation failed")
        connection.send_error(msg["id"], "analysis_failed", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/quick_create_automation",
        vol.Required("name"): str,
    }
)
async def _handle_websocket_quick_create_automation(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create an automation by name using the LLM — no chat session needed."""
    if not _require_admin(connection, msg):
        return

    name = msg["name"].strip()
    if not name:
        connection.send_error(msg["id"], "invalid_name", "Automation name is required")
        return

    llm = _find_llm(hass)
    if llm is None:
        connection.send_error(msg["id"], "no_llm", "No LLM provider configured")
        return

    try:
        entities = _collect_entity_states(hass)
        existing = [
            {
                "entity_id": s.entity_id,
                "alias": s.attributes.get("friendly_name", s.entity_id),
                "state": s.state,
            }
            for s in hass.states.async_all("automation")
        ]

        prompt = (
            f'Create a Home Assistant automation called "{name}". '
            "Infer the best trigger, conditions, and actions based on the name "
            "and the available entities. Keep it practical and useful. "
            "IMPORTANT: All trigger fields must have valid values — never use null. "
            "For time triggers use 'at' with HH:MM:SS format. "
            "For state triggers use string values for 'to'/'from'."
        )

        async with asyncio.timeout(30):
            result = await llm.architect_chat(prompt, entities, existing_automations=existing)

        automation = result.get("automation")
        if not automation:
            # Try parsing from response text
            parsed = llm.parse_streamed_response(result.get("response", ""), entities)
            automation = parsed.get("automation")

        if not automation:
            connection.send_error(
                msg["id"],
                "no_automation",
                "The AI could not generate an automation for that name. Try a more descriptive name.",
            )
            return

        # Ensure the alias matches what the user typed
        automation["alias"] = name

        # Validate before saving — reject broken automations
        from .automation_utils import async_create_automation, validate_automation_payload

        is_valid, reason, normalized = validate_automation_payload(automation, hass)
        if not is_valid or normalized is None:
            connection.send_error(
                msg["id"],
                "invalid_automation",
                f"Generated automation is invalid: {reason}. Try a more specific name.",
            )
            return

        # Sanitize triggers — strip null values that HA rejects
        triggers = normalized.get("triggers") or []
        for t in triggers if isinstance(triggers, list) else [triggers]:
            for key in list(t.keys()):
                if t[key] is None:
                    del t[key]

        # Per project policy all chat-driven automations land disabled and
        # must be reviewed/enabled by the user. The pre-MR quick-create code
        # auto-enabled by setting initial_state=True; that path was a
        # carve-out from the disabled-by-default rule and is now closed.
        create_result = await async_create_automation(hass, normalized)

        if create_result.get("success"):
            connection.send_result(
                msg["id"],
                {
                    "status": "created",
                    "automation_id": create_result["automation_id"],
                },
            )
        else:
            connection.send_error(msg["id"], "create_failed", "Failed to save automation")

    except TimeoutError:
        connection.send_error(msg["id"], "timeout", "Automation creation timed out")
    except Exception as exc:
        _LOGGER.exception("Quick create automation failed")
        connection.send_error(msg["id"], "error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/create_draft",
        vol.Required("alias"): str,
        vol.Required("session_id"): str,
    }
)
async def _handle_websocket_create_draft(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create a persisted draft automation linked to a chat session."""
    if not _require_admin(connection, msg):
        return

    store = _get_automation_store(hass)
    draft = await store.create_draft(msg["alias"], msg["session_id"])
    connection.send_result(msg["id"], draft)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_drafts",
    }
)
async def _handle_websocket_get_drafts(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return all draft automations."""
    if not _require_admin(connection, msg):
        return

    store = _get_automation_store(hass)
    drafts = await store.list_drafts()
    connection.send_result(msg["id"], drafts)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/remove_draft",
        vol.Required("draft_id"): str,
    }
)
async def _handle_websocket_remove_draft(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Remove a draft automation."""
    if not _require_admin(connection, msg):
        return

    store = _get_automation_store(hass)
    ok = await store.remove_draft(msg["draft_id"])
    if ok:
        connection.send_result(msg["id"], {"status": "ok"})
    else:
        connection.send_error(msg["id"], "not_found", "Draft not found")


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_automations",
    }
)
async def _handle_websocket_get_automations(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return all existing automations and flag Selora-managed ones, including full config.

    Each automation is enriched with AutomationStore metadata when available:
    version_count, current_version_id.
    """
    if not _require_admin(connection, msg):
        return

    try:
        from pathlib import Path

        from homeassistant.helpers import entity_registry as er

        from .automation_utils import _read_automations_yaml

        registry = er.async_get(hass)

        # Build a lookup from automation id → full config for flowchart rendering
        automations_path = Path(hass.config.config_dir) / "automations.yaml"
        yaml_automations = await hass.async_add_executor_job(
            _read_automations_yaml, automations_path
        )
        yaml_by_id: dict[str, dict[str, Any]] = {
            str(a.get("id")): a for a in yaml_automations if a.get("id")
        }
        yaml_by_alias: dict[str, dict[str, Any]] = {
            str(a.get("alias", "")): a for a in yaml_automations if a.get("id") and a.get("alias")
        }

        store = _get_automation_store(hass)

        automations = []

        for state in hass.states.async_all("automation"):
            entity_id = state.entity_id
            entry = registry.async_get(entity_id)

            is_selora = False
            automation_id = ""
            if entry and entry.unique_id:
                unique_id = str(entry.unique_id)
                is_selora = unique_id.startswith(AUTOMATION_ID_PREFIX)
                if unique_id in yaml_by_id:
                    automation_id = unique_id

            # Fallback to description check if unique_id doesn't match
            description = state.attributes.get("description") or ""
            if not is_selora and description and "[Selora AI]" in description:
                is_selora = True

            # Prefer explicit id attributes when available
            if not automation_id:
                state_id = state.attributes.get("id")
                if state_id is not None:
                    state_id_str = str(state_id)
                    if state_id_str in yaml_by_id:
                        automation_id = state_id_str

            if not automation_id and entry and entry.unique_id:
                unique_id_attr = state.attributes.get("unique_id")
                if unique_id_attr is not None:
                    unique_id_attr_str = str(unique_id_attr)
                    if unique_id_attr_str in yaml_by_id:
                        automation_id = unique_id_attr_str

            # Last fallback: alias match from automations.yaml
            if not automation_id:
                alias = str(state.attributes.get("friendly_name", ""))
                alias_match = yaml_by_alias.get(alias)
                if alias_match:
                    automation_id = str(alias_match.get("id", ""))

            # Merge full config (trigger/condition/action) from automations.yaml
            full_config = yaml_by_id.get(automation_id, {})

            # Serialise config as editable YAML text (omit internal HA fields)
            yaml_text = ""
            if full_config:
                try:
                    yaml_text = yaml.dump(full_config, allow_unicode=True, default_flow_style=False)
                except Exception:
                    yaml_text = ""

            # Merge lifecycle metadata from the store
            meta = await store.get_metadata(automation_id) if automation_id else None

            automations.append(
                {
                    "entity_id": entity_id,
                    "automation_id": automation_id,
                    "alias": state.attributes.get("friendly_name", entity_id),
                    "description": description or full_config.get("description", ""),
                    "state": state.state,
                    "is_selora": is_selora,
                    "last_triggered": state.attributes.get("last_triggered"),
                    "last_updated": state.last_updated.isoformat() if state.last_updated else None,
                    "persisted_enabled": (
                        full_config.get("initial_state")
                        if isinstance(full_config.get("initial_state"), bool)
                        else None
                    ),
                    "triggers": full_config.get("triggers") or full_config.get("trigger") or [],
                    "conditions": full_config.get("conditions")
                    or full_config.get("condition")
                    or [],
                    "actions": full_config.get("actions") or full_config.get("action") or [],
                    "yaml_text": yaml_text,
                    # Lifecycle metadata (None for automations not tracked by the store)
                    "version_count": meta["version_count"] if meta else None,
                    "current_version_id": meta["current_version_id"] if meta else None,
                }
            )

        # Sort by position in automations.yaml (newest appended last)
        yaml_order = {str(a.get("id")): i for i, a in enumerate(yaml_automations) if a.get("id")}
        automations.sort(key=lambda a: yaml_order.get(a["automation_id"], -1))

        connection.send_result(msg["id"], automations)
    except Exception as exc:
        _LOGGER.exception("Error in _handle_websocket_get_automations")
        connection.send_error(msg["id"], "unknown_error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_config",
    }
)
async def _handle_websocket_get_config(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the current integration config."""
    if not _require_admin(connection, msg):
        return

    entry = _resolve_llm_entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_configured", "Selora AI not configured")
        return

    # Merge entry data with options for a complete view
    config_data = {**entry.data, **entry.options}
    aigw = _aigateway_view(config_data)

    connection.send_result(
        msg["id"],
        {
            "llm_provider": _resolve_llm_provider(config_data),
            # Never send the raw key to the frontend — only a safe display hint.
            "anthropic_api_key_hint": _mask_api_key(config_data.get(CONF_ANTHROPIC_API_KEY, "")),
            "anthropic_api_key_set": bool(config_data.get(CONF_ANTHROPIC_API_KEY)),
            "anthropic_model": config_data.get(CONF_ANTHROPIC_MODEL, DEFAULT_ANTHROPIC_MODEL),
            "gemini_api_key_hint": _mask_api_key(config_data.get(CONF_GEMINI_API_KEY, "")),
            "gemini_api_key_set": bool(config_data.get(CONF_GEMINI_API_KEY)),
            "gemini_model": config_data.get(CONF_GEMINI_MODEL, DEFAULT_GEMINI_MODEL),
            "openai_api_key_hint": _mask_api_key(config_data.get(CONF_OPENAI_API_KEY, "")),
            "openai_api_key_set": bool(config_data.get(CONF_OPENAI_API_KEY)),
            "openai_model": config_data.get(CONF_OPENAI_MODEL, DEFAULT_OPENAI_MODEL),
            "openrouter_api_key_hint": _mask_api_key(config_data.get(CONF_OPENROUTER_API_KEY, "")),
            "openrouter_api_key_set": bool(config_data.get(CONF_OPENROUTER_API_KEY)),
            "openrouter_model": config_data.get(CONF_OPENROUTER_MODEL, DEFAULT_OPENROUTER_MODEL),
            "ollama_host": config_data.get(CONF_OLLAMA_HOST, DEFAULT_OLLAMA_HOST),
            "ollama_model": config_data.get(CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL),
            "selora_local_host": config_data.get(CONF_SELORA_LOCAL_HOST, DEFAULT_SELORA_LOCAL_HOST),
            # Background Services
            "collector_enabled": config_data.get(CONF_COLLECTOR_ENABLED, DEFAULT_COLLECTOR_ENABLED),
            "collector_mode": config_data.get(CONF_COLLECTOR_MODE, DEFAULT_COLLECTOR_MODE),
            "collector_interval": config_data.get(
                CONF_COLLECTOR_INTERVAL, DEFAULT_COLLECTOR_INTERVAL
            ),
            "collector_start_time": config_data.get(
                CONF_COLLECTOR_START_TIME, DEFAULT_COLLECTOR_START_TIME
            ),
            "collector_end_time": config_data.get(
                CONF_COLLECTOR_END_TIME, DEFAULT_COLLECTOR_END_TIME
            ),
            "auto_purge_stale": config_data.get(CONF_AUTO_PURGE_STALE, DEFAULT_AUTO_PURGE_STALE),
            "stale_days": AUTOMATION_STALE_DAYS,
            "discovery_enabled": config_data.get(CONF_DISCOVERY_ENABLED, DEFAULT_DISCOVERY_ENABLED),
            "discovery_mode": config_data.get(CONF_DISCOVERY_MODE, DEFAULT_DISCOVERY_MODE),
            "discovery_interval": config_data.get(
                CONF_DISCOVERY_INTERVAL, DEFAULT_DISCOVERY_INTERVAL
            ),
            "discovery_start_time": config_data.get(
                CONF_DISCOVERY_START_TIME, DEFAULT_DISCOVERY_START_TIME
            ),
            "discovery_end_time": config_data.get(
                CONF_DISCOVERY_END_TIME, DEFAULT_DISCOVERY_END_TIME
            ),
            "pattern_detection_enabled": config_data.get(CONF_PATTERN_ENABLED, True),
            # Developer settings
            "developer_mode": config_data.get("developer_mode", False),
            # Selora Connect
            "selora_connect_enabled": config_data.get(CONF_SELORA_CONNECT_ENABLED, False),
            "selora_connect_url": config_data.get(
                CONF_SELORA_CONNECT_URL, DEFAULT_SELORA_CONNECT_URL
            ),
            "selora_installation_id": config_data.get(CONF_SELORA_INSTALLATION_ID, ""),
            "selora_mcp_url": config_data.get(CONF_SELORA_MCP_URL, ""),
            # Selora Cloud (AI Gateway OAuth)
            "aigateway_linked": bool(aigw["refresh_token"]),
            "aigateway_user_email": aigw["user_email"],
            # LLM pricing overrides — shape: {provider: {model: [in_per_mtok, out_per_mtok]}}
            "llm_pricing_overrides": config_data.get(CONF_LLM_PRICING_OVERRIDES, {}),
        },
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/update_config",
        vol.Required("config"): dict,
    }
)
async def _handle_websocket_update_config(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Update the integration config and re-initialize."""
    if not _require_admin(connection, msg):
        return

    entry = _resolve_llm_entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_configured", "Selora AI not configured")
        return

    new_config = msg["config"]

    # Split into data and options
    data_keys = {
        CONF_LLM_PROVIDER,
        CONF_ANTHROPIC_API_KEY,
        CONF_ANTHROPIC_MODEL,
        CONF_GEMINI_API_KEY,
        CONF_GEMINI_MODEL,
        CONF_OPENAI_API_KEY,
        CONF_OPENAI_MODEL,
        CONF_OPENROUTER_API_KEY,
        CONF_OPENROUTER_MODEL,
        CONF_OLLAMA_HOST,
        CONF_OLLAMA_MODEL,
        CONF_SELORA_LOCAL_HOST,
        CONF_ENTRY_TYPE,
        CONF_SELORA_CONNECT_ENABLED,
        CONF_SELORA_CONNECT_URL,
        CONF_SELORA_INSTALLATION_ID,
        CONF_SELORA_JWT_KEY,
    }

    new_data = {k: v for k, v in new_config.items() if k in data_keys}
    new_options = {k: v for k, v in new_config.items() if k not in data_keys}

    # Never store a null/empty provider — fall back to the existing value.
    if CONF_LLM_PROVIDER in new_data and not new_data[CONF_LLM_PROVIDER]:
        new_data[CONF_LLM_PROVIDER] = entry.data.get(CONF_LLM_PROVIDER) or DEFAULT_LLM_PROVIDER

    # Only overwrite the stored API keys if the frontend sent a new non-empty value.
    # The frontend sends an empty string when the user hasn't touched the key field,
    # so we must not clobber the existing key in that case.
    for key in (
        CONF_ANTHROPIC_API_KEY,
        CONF_GEMINI_API_KEY,
        CONF_OPENAI_API_KEY,
        CONF_OPENROUTER_API_KEY,
    ):
        if key in new_data and not new_data[key]:
            new_data.pop(key, None)

    # Keys that only affect the frontend — no reload needed
    frontend_only_keys = {"developer_mode"}
    # Keys whose change can be applied live to the running LLMClient
    # without rebuilding it. Pricing overrides only impact cost reporting
    # for subsequent calls, so a hot update is enough.
    hot_option_keys = {CONF_LLM_PRICING_OVERRIDES}

    # Check if any backend-relevant keys actually changed
    old_data = {**entry.data}
    old_options = {**entry.options}
    needs_reload = False
    for k, v in new_data.items():
        if k not in frontend_only_keys and old_data.get(k) != v:
            needs_reload = True
            break
    if not needs_reload:
        for k, v in new_options.items():
            if k not in frontend_only_keys and k not in hot_option_keys and old_options.get(k) != v:
                needs_reload = True
                break

    # Update the entry
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, **new_data}, options={**entry.options, **new_options}
    )

    # Apply hot-reloadable option changes directly to the running client.
    if CONF_LLM_PRICING_OVERRIDES in new_options:
        llm = _find_llm(hass)
        if llm is not None and hasattr(llm, "set_pricing_overrides"):
            llm.set_pricing_overrides(new_options[CONF_LLM_PRICING_OVERRIDES] or {})

    # Send result BEFORE reload so the frontend gets a response
    connection.send_result(msg["id"], {"status": "success"})

    if needs_reload:
        # Schedule the reload as a background task so the WS response arrives first
        async def _reload() -> None:
            try:
                await hass.config_entries.async_reload(entry.entry_id)
            except Exception:
                _LOGGER.exception("Failed to reload entry after config update")

        hass.async_create_task(_reload())


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/validate_llm_key",
        vol.Required("provider"): str,
        vol.Optional("api_key"): str,
        vol.Optional("model"): str,
        vol.Optional("host"): str,
    }
)
async def _handle_websocket_validate_llm_key(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Validate an LLM provider key/connection without saving."""
    if not _require_admin(connection, msg):
        return

    from .providers import create_provider

    provider = msg["provider"]
    api_key = msg.get("api_key", "")
    model = msg.get("model", "")
    host = msg.get("host", "")

    # Apply defaults for missing model/host
    if provider == LLM_PROVIDER_ANTHROPIC:
        model = model or DEFAULT_ANTHROPIC_MODEL
    elif provider == LLM_PROVIDER_GEMINI:
        model = model or DEFAULT_GEMINI_MODEL
    elif provider == LLM_PROVIDER_OPENAI:
        model = model or DEFAULT_OPENAI_MODEL
    elif provider == LLM_PROVIDER_OPENROUTER:
        model = model or DEFAULT_OPENROUTER_MODEL
        host = host or DEFAULT_OPENROUTER_HOST
    elif provider == LLM_PROVIDER_OLLAMA:
        model = model or DEFAULT_OLLAMA_MODEL
        host = host or DEFAULT_OLLAMA_HOST
    elif provider == LLM_PROVIDER_SELORA_LOCAL:
        host = host or DEFAULT_SELORA_LOCAL_HOST

    try:
        llm_provider = create_provider(
            provider,
            hass,
            api_key=api_key,
            model=model,
            host=host,
        )
        valid = await llm_provider.health_check()
        if valid:
            connection.send_result(msg["id"], {"valid": True})
        else:
            connection.send_result(
                msg["id"],
                {"valid": False, "error": "API key invalid or provider unreachable."},
            )
    except Exception as exc:
        connection.send_result(
            msg["id"],
            {"valid": False, "error": str(exc) or "Validation failed."},
        )


def _get_automation_store(hass: HomeAssistant) -> AutomationStore:
    """Return (or lazily create) the AutomationStore from hass.data."""
    from .helpers import get_automation_store

    return get_automation_store(hass)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_automation_versions",
        vol.Required("automation_id"): str,
    }
)
async def _handle_websocket_get_automation_versions(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return ordered version history for an automation with session previews joined in."""
    if not _require_admin(connection, msg):
        return

    try:
        store = _get_automation_store(hass)
        versions = await store.get_versions(msg["automation_id"])

        conv_store: ConversationStore = hass.data[DOMAIN].setdefault(
            "_conv_store", ConversationStore(hass)
        )
        result = []
        for v in versions:
            entry = dict(v)
            sid = v.get("session_id")
            entry["session_preview"] = await conv_store.get_session_preview(sid) if sid else None
            result.append(entry)

        connection.send_result(msg["id"], result)
    except Exception as exc:
        _LOGGER.exception("Error in get_automation_versions")
        connection.send_error(msg["id"], "unknown_error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_automation_diff",
        vol.Required("automation_id"): str,
        vol.Required("version_id_a"): str,
        vol.Required("version_id_b"): str,
    }
)
async def _handle_websocket_get_automation_diff(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return a unified diff between two versions of an automation."""
    if not _require_admin(connection, msg):
        return

    try:
        store = _get_automation_store(hass)
        diff = await store.get_diff(msg["automation_id"], msg["version_id_a"], msg["version_id_b"])
        if diff is None:
            connection.send_error(msg["id"], "not_found", "One or both version IDs not found")
            return
        connection.send_result(msg["id"], {"diff": diff})
    except Exception as exc:
        _LOGGER.exception("Error in get_automation_diff")
        connection.send_error(msg["id"], "unknown_error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/toggle_automation",
        vol.Required("automation_id"): str,
        vol.Required("entity_id"): str,
        vol.Optional("enabled"): bool,
    }
)
async def _handle_websocket_toggle_automation(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Toggle an automation's enabled state, persisting initial_state in automations.yaml."""
    if not _require_admin(connection, msg):
        return

    try:
        from .automation_utils import async_toggle_automation

        entity_id = msg["entity_id"]
        requested_enabled = msg.get("enabled")
        if requested_enabled is None:
            state = hass.states.get(entity_id)
            currently_on = state and state.state == "on"
            enable = not currently_on
            status = "toggled"
        else:
            enable = bool(requested_enabled)
            status = "set"
        success = await async_toggle_automation(hass, msg["automation_id"], entity_id, enable)
        if success:
            connection.send_result(msg["id"], {"status": status, "enabled": enable})
        else:
            connection.send_error(msg["id"], "not_found", "Automation not found")
    except Exception as exc:
        _LOGGER.exception("Error in toggle_automation")
        connection.send_error(msg["id"], "unknown_error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/delete_automation",
        vol.Required("automation_id"): str,
    }
)
async def _handle_websocket_delete_automation(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Permanently delete a Selora-managed automation and all version history."""
    if not _require_admin(connection, msg):
        return

    try:
        from pathlib import Path

        from .automation_utils import _read_automations_yaml

        automation_id = msg["automation_id"]

        # Only allow deletion of Selora-managed automations
        automations_path = Path(hass.config.config_dir) / "automations.yaml"
        yaml_automations = await hass.async_add_executor_job(
            _read_automations_yaml, automations_path
        )
        target = next((a for a in yaml_automations if a.get("id") == automation_id), None)
        if not target:
            connection.send_error(msg["id"], "not_found", "Automation not found")
            return

        from .helpers import is_selora_automation

        if not is_selora_automation(target):
            connection.send_error(
                msg["id"], "not_allowed", "Only Selora-managed automations can be deleted"
            )
            return

        from .automation_utils import async_delete_automation

        success = await async_delete_automation(hass, automation_id)
        if success:
            connection.send_result(msg["id"], {"status": "deleted"})
        else:
            connection.send_error(msg["id"], "not_found", "Automation not found")
    except Exception as exc:
        _LOGGER.exception("Error in delete_automation")
        connection.send_error(msg["id"], "unknown_error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_automation_lineage",
        vol.Required("automation_id"): str,
    }
)
async def _handle_websocket_get_automation_lineage(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the lineage list for an automation with session previews joined in."""
    if not _require_admin(connection, msg):
        return

    try:
        store = _get_automation_store(hass)
        lineage = await store.get_automation_lineage(msg["automation_id"])

        conv_store: ConversationStore = hass.data[DOMAIN].setdefault(
            "_conv_store", ConversationStore(hass)
        )
        result = []
        for entry in lineage:
            enriched = dict(entry)
            sid = entry.get("session_id")
            if sid:
                session = await conv_store.get_session(sid)
                enriched["session_preview"] = await conv_store.get_session_preview(sid)
                enriched["session_title"] = (session or {}).get("title") or enriched[
                    "session_preview"
                ]
            else:
                enriched["session_preview"] = None
                enriched["session_title"] = None
            result.append(enriched)

        connection.send_result(msg["id"], result)
    except Exception as exc:
        _LOGGER.exception("Error in get_automation_lineage")
        connection.send_error(msg["id"], "unknown_error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_session_automations",
        vol.Required("session_id"): str,
    }
)
async def _handle_websocket_get_session_automations(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return all automations (with metadata) touched by a given session."""
    if not _require_admin(connection, msg):
        return

    try:
        store = _get_automation_store(hass)
        automation_ids = await store.get_session_automations(msg["session_id"])

        result = []
        for automation_id in automation_ids:
            meta = await store.get_metadata(automation_id)
            if meta:
                result.append(meta)

        connection.send_result(msg["id"], result)
    except Exception as exc:
        _LOGGER.exception("Error in get_session_automations")
        connection.send_error(msg["id"], "unknown_error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/load_automation_to_session",
        vol.Required("automation_id"): str,
        vol.Optional("session_id"): str,
    }
)
async def _handle_websocket_load_automation_to_session(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Open a chat session pre-loaded with the automation's current YAML for refinement.

    Inserts an assistant message that surfaces the automation YAML as context.
    Subsequent selora_ai/chat messages in this session refine it via the existing
    chat handler.
    """
    if not _require_admin(connection, msg):
        return

    from pathlib import Path

    from .automation_utils import _parse_automation_yaml, _read_automations_yaml

    automation_id = msg["automation_id"]

    # Resolve YAML — prefer the store's current version, fall back to live file
    store = _get_automation_store(hass)
    record = await store.get_record(automation_id)

    automation_data: dict[str, Any] | None = None
    yaml_text: str = ""

    if record:
        versions = record.get("versions", [])
        if versions:
            current_ver = next(
                (v for v in versions if v["version_id"] == record["current_version_id"]),
                versions[-1],
            )
            yaml_text = current_ver.get("yaml", "")
            version_data = current_ver.get("data")
            if isinstance(version_data, dict):
                automation_data = version_data

    if yaml_text and automation_data is None:
        parsed = await hass.async_add_executor_job(_parse_automation_yaml, yaml_text)
        if parsed:
            automation_data = parsed

    if not yaml_text:
        # Fall back to reading live automations.yaml
        automations_path = Path(hass.config.config_dir) / "automations.yaml"
        existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)
        for a in existing:
            if a.get("id") == automation_id:
                automation_data = a
                yaml_text = yaml.dump(a, allow_unicode=True, default_flow_style=False)
                break

    if not yaml_text or automation_data is None:
        connection.send_error(msg["id"], "not_found", "Automation not found")
        return

    conv_store: ConversationStore = hass.data[DOMAIN].setdefault(
        "_conv_store", ConversationStore(hass)
    )

    # Resolve or create session
    session_id = msg.get("session_id", "")
    if session_id:
        session = await conv_store.get_session(session_id)
        if not session:
            session = await conv_store.create_session()
            session_id = session["id"]
    else:
        session = await conv_store.create_session()
        session_id = session["id"]

    alias = automation_data.get("alias", automation_id)

    # Insert assistant context message so the LLM has the full automation on first turn
    await conv_store.append_message(
        session_id,
        "assistant",
        "Describe the changes",
        intent="automation",
        automation=automation_data,
        automation_yaml=yaml_text,
        automation_status="refining",
        automation_id=automation_id,
    )

    connection.send_result(
        msg["id"],
        {
            "session_id": session_id,
            "automation_id": automation_id,
            "alias": alias,
            "yaml_text": yaml_text,
        },
    )


# ── Proactive Suggestion Websocket Endpoints ─────────────────────────


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_proactive_suggestions",
        vol.Optional("status", default="pending"): str,
    }
)
async def _handle_websocket_get_proactive_suggestions(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return proactive suggestions from pattern detection."""
    if not _require_admin(connection, msg):
        return

    status = msg.get("status", "pending")
    pattern_store = _get_pattern_store(hass)
    if pattern_store:
        suggestions = await pattern_store.get_suggestions(status=status)
    else:
        all_suggestions = hass.data.get(DOMAIN, {}).get("proactive_suggestions", [])
        suggestions = [s for s in all_suggestions if s.get("status") == status]
    connection.send_result(msg["id"], suggestions)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/update_proactive_suggestion",
        vol.Required("suggestion_id"): str,
        vol.Required("action"): vol.In(["accepted", "dismissed", "snoozed"]),
        vol.Optional("snooze_hours"): vol.Coerce(float),
    }
)
async def _handle_websocket_update_proactive_suggestion(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Accept, dismiss, or snooze a proactive suggestion."""
    if not _require_admin(connection, msg):
        return

    pattern_store = _get_pattern_store(hass)
    if not pattern_store:
        connection.send_error(msg["id"], "no_store", "Pattern store not available")
        return

    suggestion_id = msg["suggestion_id"]
    action = msg["action"]
    suggestion = await pattern_store.get_suggestion(suggestion_id)
    if suggestion is None:
        connection.send_error(msg["id"], "not_found", "Suggestion not found")
        return

    if action == "accepted":
        automation_data = suggestion.get("automation_data")
        if not isinstance(automation_data, dict) or not automation_data:
            connection.send_error(
                msg["id"], "invalid_suggestion", "Suggestion has no automation payload"
            )
            return

        from .automation_utils import async_create_automation

        result = await async_create_automation(hass, automation_data)
        if not result.get("success", False):
            connection.send_error(
                msg["id"], "create_failed", "Failed to create automation from suggestion"
            )
            return

        await pattern_store.update_suggestion_status(suggestion_id, "accepted")
        connection.send_result(
            msg["id"],
            {
                "status": "accepted",
                "automation_created": True,
                "automation_id": result.get("automation_id"),
            },
        )
        return

    snooze_until = None
    if action == "snoozed":
        snooze_hours = msg.get("snooze_hours", 24.0)
        snooze_until = (datetime.now(UTC) + timedelta(hours=snooze_hours)).isoformat()

    updated = await pattern_store.update_suggestion_status(
        suggestion_id, action, snooze_until=snooze_until
    )
    if not updated:
        connection.send_error(msg["id"], "not_found", "Suggestion not found")
        return

    connection.send_result(msg["id"], {"status": action})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_suggestion_detail",
        vol.Required("suggestion_id"): str,
    }
)
async def _handle_websocket_get_suggestion_detail(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return full suggestion detail with YAML preview and pattern context."""
    if not _require_admin(connection, msg):
        return

    pattern_store = _get_pattern_store(hass)
    if not pattern_store:
        connection.send_error(msg["id"], "no_store", "Pattern store not available")
        return

    suggestion = await pattern_store.get_suggestion(msg["suggestion_id"])
    if not suggestion:
        connection.send_error(msg["id"], "not_found", "Suggestion not found")
        return

    # Enrich with pattern detail if available
    pattern_id = suggestion.get("pattern_id", "")
    pattern_detail = None
    if pattern_id:
        pattern_detail = await pattern_store.get_pattern_detail(pattern_id)

    connection.send_result(
        msg["id"],
        {
            **suggestion,
            "pattern_detail": pattern_detail,
        },
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/accept_suggestion_with_edits",
        vol.Required("suggestion_id"): str,
        vol.Required("automation_yaml"): str,
    }
)
async def _handle_websocket_accept_suggestion_with_edits(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Accept a suggestion with user-edited YAML (automations tab editing)."""
    if not _require_admin(connection, msg):
        return

    pattern_store = _get_pattern_store(hass)
    if not pattern_store:
        connection.send_error(msg["id"], "no_store", "Pattern store not available")
        return

    suggestion = await pattern_store.get_suggestion(msg["suggestion_id"])
    if not suggestion:
        connection.send_error(msg["id"], "not_found", "Suggestion not found")
        return

    # Parse the user-edited YAML
    try:
        automation_data = yaml.safe_load(msg["automation_yaml"])
    except yaml.YAMLError as exc:
        connection.send_error(msg["id"], "invalid_yaml", str(exc))
        return

    if not isinstance(automation_data, dict):
        connection.send_error(msg["id"], "invalid_yaml", "YAML must be a mapping")
        return

    from .automation_utils import async_create_automation, validate_automation_payload

    is_valid, reason, normalized = validate_automation_payload(automation_data, hass)
    if not is_valid or normalized is None:
        connection.send_error(msg["id"], "invalid_automation", reason or "Validation failed")
        return

    result = await async_create_automation(hass, normalized)
    if not result.get("success", False):
        connection.send_error(
            msg["id"], "create_failed", "Failed to create automation from suggestion"
        )
        return

    await pattern_store.update_suggestion_status(msg["suggestion_id"], "accepted")

    connection.send_result(
        msg["id"],
        {
            "status": "accepted",
            "automation_created": True,
            "automation_id": result.get("automation_id"),
        },
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/trigger_pattern_scan",
    }
)
async def _handle_websocket_trigger_pattern_scan(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Manually trigger a pattern scan (automations tab refresh)."""
    if not _require_admin(connection, msg):
        return

    domain_data = hass.data.get(DOMAIN, {})
    engine = None
    for key, val in domain_data.items():
        if key.startswith("_") or not isinstance(val, dict):
            continue
        e = val.get("pattern_engine")
        if e is not None:
            engine = e
            break

    if engine is None:
        connection.send_error(msg["id"], "no_engine", "Pattern engine not available")
        return

    new_patterns = await engine.scan()
    connection.send_result(
        msg["id"],
        {
            "patterns_found": len(new_patterns),
            "patterns": new_patterns,
        },
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_patterns",
        vol.Optional("status", default="active"): str,
        vol.Optional("pattern_type"): str,
    }
)
async def _handle_websocket_get_patterns(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return detected patterns for the automations tab with filtering."""
    if not _require_admin(connection, msg):
        return

    pattern_store = _get_pattern_store(hass)
    if not pattern_store:
        connection.send_error(msg["id"], "no_store", "Pattern store not available")
        return
    patterns = await pattern_store.get_patterns(
        status=msg.get("status"),
        pattern_type=msg.get("pattern_type"),
    )
    # Sort by confidence descending
    patterns.sort(key=lambda p: p.get("confidence", 0), reverse=True)
    connection.send_result(msg["id"], patterns)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_pattern_detail",
        vol.Required("pattern_id"): str,
    }
)
async def _handle_websocket_get_pattern_detail(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return a single pattern with full entity history context."""
    if not _require_admin(connection, msg):
        return

    pattern_store = _get_pattern_store(hass)
    if not pattern_store:
        connection.send_error(msg["id"], "no_store", "Pattern store not available")
        return
    detail = await pattern_store.get_pattern_detail(msg["pattern_id"])
    if detail is None:
        connection.send_error(msg["id"], "not_found", "Pattern not found")
        return
    connection.send_result(msg["id"], detail)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/update_pattern_status",
        vol.Required("pattern_id"): str,
        vol.Required("status"): vol.In(["active", "dismissed", "snoozed"]),
        vol.Optional("snooze_hours", default=24): int,
    }
)
async def _handle_websocket_update_pattern_status(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Update a pattern's status (dismiss or snooze)."""
    if not _require_admin(connection, msg):
        return

    pattern_store = _get_pattern_store(hass)
    if not pattern_store:
        connection.send_error(msg["id"], "no_store", "Pattern store not available")
        return

    snooze_until = None
    if msg["status"] == "snoozed":
        snooze_until = (
            datetime.now(UTC) + timedelta(hours=msg.get("snooze_hours", 24))
        ).isoformat()

    ok = await pattern_store.update_pattern_status(msg["pattern_id"], msg["status"], snooze_until)
    if not ok:
        connection.send_error(msg["id"], "not_found", "Pattern not found")
        return
    connection.send_result(msg["id"], {"status": msg["status"]})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_state_history_summary",
    }
)
async def _handle_websocket_get_state_history_summary(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return aggregated state history stats for the automations tab."""
    if not _require_admin(connection, msg):
        return

    pattern_store = _get_pattern_store(hass)
    if not pattern_store:
        connection.send_error(msg["id"], "no_store", "Pattern store not available")
        return
    summary = await pattern_store.get_history_summary()
    connection.send_result(msg["id"], summary)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_analytics",
        vol.Optional("entity_id"): cv.string,
    }
)
async def _handle_websocket_get_analytics(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return device analytics — summary or per-entity details."""
    if not _require_admin(connection, msg):
        return

    pattern_store = _get_pattern_store(hass)
    if not pattern_store:
        connection.send_error(msg["id"], "no_store", "Pattern store not available")
        return

    entity_id = msg.get("entity_id")
    if entity_id:
        usage_windows = await pattern_store.get_usage_windows(entity_id)
        state_transitions = await pattern_store.get_state_transition_counts(entity_id)
        connection.send_result(
            msg["id"],
            {
                "entity_id": entity_id,
                "usage_windows": usage_windows,
                "state_transitions": state_transitions,
            },
        )
    else:
        summary = await pattern_store.get_analytics_summary()
        connection.send_result(msg["id"], summary)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/usage/recent",
    }
)
async def _handle_websocket_get_recent_usage(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the recent LLM usage events from the in-memory ring buffer.

    Powers the panel's "Where tokens go" breakdown. The buffer is
    ephemeral (resets on HA restart) and capped at LLM_USAGE_BUFFER_SIZE.
    """
    if not _require_admin(connection, msg):
        return

    buffer = hass.data.get(DOMAIN, {}).get("llm_usage_events")
    events = list(buffer) if buffer else []
    connection.send_result(msg["id"], {"events": events})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/usage/pricing_defaults",
    }
)
async def _handle_websocket_get_pricing_defaults(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the built-in pricing table (USD per million tokens).

    Lets the panel show defaults next to the user override fields so the
    user can see what the integration would otherwise charge against.
    """
    if not _require_admin(connection, msg):
        return

    from .const import LLM_PRICING_USD_PER_MTOK  # noqa: PLC0415

    serialised = {
        provider: {model: list(price) for model, price in models.items()}
        for provider, models in LLM_PRICING_USD_PER_MTOK.items()
    }
    connection.send_result(msg["id"], {"pricing": serialised})


_USAGE_RANGE_KEYS = ("today", "7d", "30d", "month", "all")


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/usage/breakdown",
        vol.Optional("range", default="30d"): vol.In(_USAGE_RANGE_KEYS),
    }
)
async def _handle_websocket_get_usage_breakdown(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return per-(provider, model) usage totals from the persistent store.

    Powers the panel's "By provider" breakdown and the per-model filter so
    users can see which backend their tokens are going to even after HA
    restarts (the in-memory ring buffer can't help with that).
    """
    if not _require_admin(connection, msg):
        return

    from .usage_store import get_usage_store  # noqa: PLC0415

    store = get_usage_store(hass)
    breakdown = await store.get_breakdown(msg.get("range", "30d"))
    connection.send_result(
        msg["id"],
        {
            "range": msg.get("range", "30d"),
            "breakdown": breakdown,
        },
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/usage/totals",
        vol.Optional("range", default="30d"): vol.In(_USAGE_RANGE_KEYS),
        vol.Optional("provider"): str,
        vol.Optional("model"): str,
    }
)
async def _handle_websocket_get_usage_totals(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return flat usage sums for one range, optionally filtered.

    Backs the panel's Totals tiles and the three "By period" rows when a
    provider/model filter is active (statistics-based totals can't filter).
    """
    if not _require_admin(connection, msg):
        return

    from .usage_store import get_usage_store  # noqa: PLC0415

    store = get_usage_store(hass)
    provider = msg.get("provider") or None
    # ``model`` is preserved as-is so an explicit "" filters to the
    # no-model bucket (e.g. selora_local, which has no user-visible model
    # id). ``None`` from a missing field still means "any model".
    model = msg.get("model")
    totals = await store.get_totals(msg.get("range", "30d"), provider=provider, model=model)
    periods = await store.get_periods(provider=provider, model=model)
    connection.send_result(
        msg["id"],
        {
            "range": msg.get("range", "30d"),
            "provider": provider,
            "model": model,
            "totals": totals,
            "periods": periods,
        },
    )


def _get_pattern_store(hass: HomeAssistant) -> PatternStore | None:
    """Find the PatternStore from any active config entry."""
    from .pattern_store import get_pattern_store  # noqa: PLC0415

    return get_pattern_store(hass)


# ── Scene management websocket handlers ─────────────────────────────


def _get_scene_store(hass: HomeAssistant) -> SceneStore:
    """Return (or lazily create) the SceneStore from hass.data."""
    from .helpers import get_scene_store  # noqa: PLC0415

    return get_scene_store(hass)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_scenes",
    }
)
async def _handle_websocket_get_scenes(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return all Selora-managed scenes (force-reconciles with scenes.yaml).

    Each scene record is enriched with the parsed ``entities`` map and a
    rendered ``yaml`` string so the panel can display rich entity state
    targets and the YAML view without a second round-trip.
    """
    if not _require_admin(connection, msg):
        return

    store = _get_scene_store(hass)
    await store.async_reconcile_yaml(force=True)
    scenes = await store.async_list_scenes()

    from .scene_utils import _get_scenes_path, _read_scenes_yaml  # noqa: PLC0415

    scenes_path = _get_scenes_path(hass)
    try:
        yaml_entries = await hass.async_add_executor_job(_read_scenes_yaml, scenes_path)
    except Exception as exc:  # noqa: BLE001 — best-effort enrichment, never block the list
        _LOGGER.warning("Failed to read scenes.yaml for enrichment: %s", exc)
        yaml_entries = []

    yaml_by_id: dict[str, dict[str, Any]] = {
        entry["id"]: entry
        for entry in yaml_entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }

    import yaml as pyyaml  # noqa: PLC0415

    from .const import SCENE_ID_PREFIX  # noqa: PLC0415
    from .scene_utils import resolve_scene_entity_id  # noqa: PLC0415

    enriched: list[dict[str, Any]] = []
    for record in scenes:
        entry = yaml_by_id.get(record["scene_id"])
        if entry is None:
            enriched.append({**record, "entities": {}, "yaml": "", "source": "selora"})
            continue
        entities = entry.get("entities") or {}
        if not isinstance(entities, dict):
            entities = {}
        try:
            yaml_text = pyyaml.dump(
                {"name": entry.get("name", record["name"]), "entities": entities},
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        except Exception:  # noqa: BLE001 — fall back to empty YAML, never block the list
            yaml_text = ""
        enriched.append({**record, "entities": entities, "yaml": yaml_text, "source": "selora"})

    # Include non-Selora scenes from yaml (e.g. hand-crafted or other-integration scenes)
    covered_scene_ids: set[str] = {r["scene_id"] for r in enriched}
    for entry in yaml_entries:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("id")
        if not isinstance(sid, str) or sid.startswith(SCENE_ID_PREFIX):
            continue
        if sid in covered_scene_ids:
            continue
        covered_scene_ids.add(sid)
        raw_name = entry.get("name", "")
        if not isinstance(raw_name, str):
            raw_name = str(raw_name)
        name = raw_name.strip() or sid
        entities = entry.get("entities") or {}
        if not isinstance(entities, dict):
            entities = {}
        entity_id = resolve_scene_entity_id(hass, sid, raw_name)
        try:
            yaml_text = pyyaml.dump(
                {"name": name, "entities": entities},
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        except Exception:  # noqa: BLE001
            yaml_text = ""
        enriched.append(
            {
                "scene_id": sid,
                "name": name,
                "entity_id": entity_id,
                "entity_count": len(entities),
                "session_id": None,
                "created_at": None,
                "updated_at": None,
                "deleted_at": None,
                "entities": entities,
                "yaml": yaml_text,
                "source": "home_assistant",
            }
        )

    # Include any remaining HA scene states not covered by yaml at all
    # (UI-managed scenes stored in HA's own storage, or from other integrations)
    covered_entity_ids: set[str] = {r["entity_id"] for r in enriched if r.get("entity_id")}
    for state in hass.states.async_all("scene"):
        if state.entity_id in covered_entity_ids:
            continue
        object_id = state.entity_id.removeprefix("scene.")
        if object_id.startswith(SCENE_ID_PREFIX):
            continue
        name = state.attributes.get("friendly_name") or state.name or object_id
        enriched.append(
            {
                "scene_id": object_id,
                "name": name,
                "entity_id": state.entity_id,
                "entity_count": 0,
                "session_id": None,
                "created_at": None,
                "updated_at": None,
                "deleted_at": None,
                "entities": {},
                "yaml": "",
                "source": "home_assistant",
            }
        )

    connection.send_result(msg["id"], {"scenes": enriched})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/load_scene_to_session",
        vol.Required("scene_id"): str,
    }
)
async def _handle_websocket_load_scene_to_session(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Open a chat session pre-loaded with the scene's current entities for refinement.

    Inserts an assistant message showing the scene as a refining card.
    Subsequent selora_ai/chat messages refine it via the existing chat handler.
    """
    if not _require_admin(connection, msg):
        return

    scene_id = msg["scene_id"]

    # Resolve scene data — store first, then yaml, then HA states
    from .const import SCENE_ID_PREFIX  # noqa: PLC0415
    from .scene_utils import _get_scenes_path, _read_scenes_yaml  # noqa: PLC0415

    store = _get_scene_store(hass)
    await store.async_reconcile_yaml(force=True)
    record = await store.async_get_scene(scene_id)

    name: str = scene_id
    entities: dict[str, Any] = {}
    yaml_text: str = ""
    is_selora = scene_id.startswith(SCENE_ID_PREFIX)

    scenes_path = _get_scenes_path(hass)
    try:
        yaml_entries = await hass.async_add_executor_job(_read_scenes_yaml, scenes_path)
    except Exception:  # noqa: BLE001
        yaml_entries = []

    yaml_by_id: dict[str, dict[str, Any]] = {
        e["id"]: e for e in yaml_entries if isinstance(e, dict) and isinstance(e.get("id"), str)
    }

    if record:
        name = record.get("name") or scene_id
        entry = yaml_by_id.get(scene_id)
        if entry:
            entities = entry.get("entities") or {}
            if not isinstance(entities, dict):
                entities = {}
    else:
        # HA-owned scene — try yaml then HA state
        entry = yaml_by_id.get(scene_id)
        if entry:
            raw_name = entry.get("name", "")
            name = (raw_name.strip() if isinstance(raw_name, str) else scene_id) or scene_id
            entities = entry.get("entities") or {}
            if not isinstance(entities, dict):
                entities = {}
        else:
            state = hass.states.get(f"scene.{scene_id}")
            if state:
                name = state.attributes.get("friendly_name") or scene_id

    import yaml as pyyaml  # noqa: PLC0415

    if entities:
        try:
            yaml_text = pyyaml.dump(
                {"name": name, "entities": entities},
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        except Exception:  # noqa: BLE001
            yaml_text = ""

    conv_store: ConversationStore = hass.data[DOMAIN].setdefault(
        "_conv_store", ConversationStore(hass)
    )
    session = await conv_store.create_session()
    session_id = session["id"]

    await conv_store.update_session_title(session_id, f"Refining: {name}")

    await conv_store.append_message(
        session_id,
        "assistant",
        f"I've loaded the scene **{name}** for refinement. What changes would you like to make?",
        intent="scene",
        scene={"name": name, "entities": entities},
        scene_yaml=yaml_text or None,
        scene_id=scene_id if is_selora else None,
        refine_scene_id=scene_id if is_selora else None,
        scene_status="refining",
    )

    connection.send_result(
        msg["id"],
        {
            "session_id": session_id,
            "scene_id": scene_id,
            "name": name,
        },
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/delete_scene",
        vol.Required("scene_id"): str,
    }
)
async def _handle_websocket_delete_scene(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Soft-delete a Selora-managed scene and remove from scenes.yaml."""
    if not _require_admin(connection, msg):
        return

    scene_id = msg["scene_id"]
    store = _get_scene_store(hass)
    await store.async_reconcile_yaml(force=True)

    try:
        from .scene_utils import async_remove_scene_yaml  # noqa: PLC0415

        found, removed = await store.async_delete_with_yaml(
            scene_id,
            lambda sid: async_remove_scene_yaml(hass, sid),
        )
    except Exception as exc:  # noqa: BLE001 — propagate failure and undo soft-delete
        _LOGGER.warning("Failed to delete scene %s: %s", scene_id, exc)
        await store.async_restore(scene_id)
        connection.send_error(msg["id"], "delete_failed", str(exc))
        return

    if not found:
        connection.send_error(msg["id"], "not_found", "Scene not found")
        return

    if not removed:
        # The YAML entry is already gone (external edit, another tool, or
        # stale backfill record).  Force a scene.reload so HA unloads the
        # entity if it's still active from a prior load.
        _LOGGER.info("Scene %s already absent from scenes.yaml — reloading scenes", scene_id)
        try:
            await hass.services.async_call("scene", "reload", blocking=True)
        except Exception as exc:  # noqa: BLE001 — HA may still have the scene loaded
            _LOGGER.warning("scene.reload failed after confirming %s removal: %s", scene_id, exc)
            await store.async_restore(scene_id)
            connection.send_error(msg["id"], "reload_failed", f"Scene reload failed: {exc}")
            return

    # Purge the deleted scene from all persisted session data (indexes +
    # message fields) and notify in-memory Assist indexes via dispatcher.
    conv_store: ConversationStore = hass.data[DOMAIN].setdefault(
        "_conv_store", ConversationStore(hass)
    )
    await conv_store.remove_scene_from_sessions(scene_id)
    async_dispatcher_send(hass, SIGNAL_SCENE_DELETED, scene_id)

    connection.send_result(msg["id"], {"success": True})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/activate_scene",
        vol.Required("scene_id"): str,
    }
)
async def _handle_websocket_activate_scene(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Activate a Selora-managed scene by calling scene.turn_on."""
    if not _require_admin(connection, msg):
        return

    scene_id = msg["scene_id"]

    store = _get_scene_store(hass)
    await store.async_reconcile_yaml(force=True)
    record = await store.async_get_scene(scene_id)
    if record is None:
        connection.send_error(msg["id"], "not_found", "Scene not tracked by Selora")
        return
    if record.get("deleted_at") is not None:
        connection.send_error(msg["id"], "deleted", "Scene has been deleted")
        return

    # Always resolve the entity_id from the registry — the cached value may
    # be stale after a scene.reload reassigns slugs due to name collisions.
    from .scene_utils import resolve_scene_entity_id  # noqa: PLC0415

    entity_id = resolve_scene_entity_id(hass, scene_id, record.get("name"))
    if entity_id is None:
        # Fall back to the cached entity_id only if it still has a state
        cached = record.get("entity_id")
        if cached and hass.states.get(cached) is not None:
            entity_id = cached

    if entity_id is None:
        # Scene may exist in YAML but HA hasn't loaded it yet — reload and retry
        try:
            await hass.services.async_call("scene", "reload", blocking=True)
        except Exception:  # noqa: BLE001 — best-effort reload
            _LOGGER.debug("scene.reload failed before activation of %s", scene_id)
        entity_id = resolve_scene_entity_id(hass, scene_id, record.get("name"))

    if entity_id is None:
        connection.send_error(msg["id"], "not_found", "Scene entity not found in Home Assistant")
        return

    try:
        await hass.services.async_call("scene", "turn_on", {"entity_id": entity_id}, blocking=True)
        connection.send_result(msg["id"], {"success": True, "entity_id": entity_id})
    except Exception as exc:  # noqa: BLE001 — HA service handlers may raise beyond HA's hierarchy
        _LOGGER.error("Failed to activate scene %s: %s", scene_id, exc)
        connection.send_error(msg["id"], "activation_failed", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/exchange_connect_code",
        vol.Required("code"): str,
        vol.Required("code_verifier"): str,
        vol.Required("redirect_uri"): str,
        vol.Optional("connect_url", default=""): str,
    }
)
async def _handle_websocket_exchange_connect_code(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Exchange an OAuth authorization code for Connect installation credentials."""
    if not _require_admin(connection, msg):
        return

    entry = _resolve_llm_entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_configured", "Selora AI not configured")
        return

    connect_url = (
        msg["connect_url"] or entry.data.get(CONF_SELORA_CONNECT_URL, DEFAULT_SELORA_CONNECT_URL)
    ).rstrip("/")

    import aiohttp

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession() as session:
        # Step 1: Exchange authorization code for an access token
        try:
            async with session.post(
                f"{connect_url}/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": msg["code"],
                    "code_verifier": msg["code_verifier"],
                    "client_id": msg["redirect_uri"],
                    "redirect_uri": msg["redirect_uri"],
                },
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    _LOGGER.warning("Connect token exchange failed (%s): %s", resp.status, body)
                    connection.send_error(
                        msg["id"],
                        "token_exchange_failed",
                        f"Connect returned HTTP {resp.status}",
                    )
                    return
                token_data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            connection.send_error(msg["id"], "connect_unreachable", f"Cannot reach Connect: {err}")
            return

        access_token = token_data.get("access_token")
        if not access_token:
            connection.send_error(msg["id"], "token_exchange_failed", "No access_token in response")
            return

        # Step 2: Register this HA instance as an MCP device
        try:
            async with session.post(
                f"{connect_url}/api/v1/mcp/devices/register",
                json={"device_name": hass.config.location_name or "Home Assistant"},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    _LOGGER.warning(
                        "Connect device registration failed (%s): %s",
                        resp.status,
                        body,
                    )
                    connection.send_error(
                        msg["id"],
                        "registration_failed",
                        f"Device registration returned HTTP {resp.status}",
                    )
                    return
                device_data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            connection.send_error(
                msg["id"],
                "connect_unreachable",
                f"Cannot reach Connect for device registration: {err}",
            )
            return

        device_id = device_data.get("device_id")
        installation_id = device_data.get("installation_id")
        scope_id_from_device = device_data.get("scope_id")
        if not device_id:
            connection.send_error(
                msg["id"],
                "invalid_response",
                "Connect response missing device_id",
            )
            return

        # Step 3: Fetch installation MCP auth config (installation-scoped JWT key)
        # Claude's OAuth flow issues tokens signed with the installation key,
        # not the per-device key from registration.
        jwt_key = None
        scope_id = None
        if installation_id:
            try:
                async with session.get(
                    f"{connect_url}/api/v1/installations/{installation_id}/mcp-auth-config",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=timeout,
                ) as resp:
                    if resp.status == 200:
                        auth_config = await resp.json()
                        jwt_key = auth_config.get("jwt_key")
                        scope_id = auth_config.get("scope_id")
                    else:
                        _LOGGER.warning(
                            "Failed to fetch MCP auth config (%s), using device key",
                            resp.status,
                        )
            except (aiohttp.ClientError, TimeoutError) as err:
                _LOGGER.warning("Could not reach Connect for MCP auth config: %s", err)

    # Fall back to device key only when there is no installation
    if not jwt_key:
        jwt_key = device_data.get("jwt_key")

    if not jwt_key:
        connection.send_error(
            msg["id"],
            "invalid_response",
            "Connect response missing jwt_key",
        )
        return

    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_SELORA_CONNECT_ENABLED: True,
            CONF_SELORA_CONNECT_URL: connect_url,
            CONF_SELORA_INSTALLATION_ID: scope_id
            or scope_id_from_device
            or installation_id
            or device_id,
            CONF_SELORA_JWT_KEY: jwt_key,
        },
    )

    connection.send_result(msg["id"], {"status": "linked", "device_id": device_id})

    # Reload so the JWT validator picks up the new credentials
    async def _reload() -> None:
        try:
            await hass.config_entries.async_reload(entry.entry_id)
        except Exception:
            _LOGGER.exception("Failed to reload entry after Connect linking")

    hass.async_create_task(_reload())


@websocket_api.async_response
@decorators.websocket_command({vol.Required("type"): "selora_ai/unlink_connect"})
async def _handle_websocket_unlink_connect(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Remove Connect credentials from the config entry."""
    if not _require_admin(connection, msg):
        return

    entry = _resolve_llm_entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_configured", "Selora AI not configured")
        return

    new_data = {**entry.data}
    new_data.pop(CONF_SELORA_CONNECT_ENABLED, None)
    new_data.pop(CONF_SELORA_INSTALLATION_ID, None)
    new_data.pop(CONF_SELORA_JWT_KEY, None)
    # Keep CONF_SELORA_CONNECT_URL so the user doesn't have to re-enter it

    hass.config_entries.async_update_entry(entry, data=new_data)

    # Immediately clear the in-memory validator so Selora JWTs are rejected
    # right away, even if the scheduled reload below fails.
    hass.data.get(DOMAIN, {}).pop("selora_jwt_validator", None)

    connection.send_result(msg["id"], {"status": "unlinked"})

    async def _reload() -> None:
        try:
            await hass.config_entries.async_reload(entry.entry_id)
        except Exception:
            _LOGGER.exception("Failed to reload entry after Connect unlinking")

    hass.async_create_task(_reload())


# ── AI Gateway OAuth (Selora Cloud LLM) ──────────────────────────────────────


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    """Decode the unverified payload of a JWT.

    The token is verified by the AI Gateway service, not by us — we only
    read the payload to surface the user's email/sub for display.
    """
    import base64
    import json

    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/exchange_aigateway_code",
        vol.Required("code"): str,
        vol.Required("code_verifier"): str,
        vol.Required("redirect_uri"): str,
        vol.Required("client_id"): str,
        vol.Optional("connect_url", default=""): str,
    }
)
async def _handle_websocket_exchange_aigateway_code(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Exchange an AI Gateway OAuth code for access + refresh tokens."""
    if not _require_admin(connection, msg):
        return

    entry = _resolve_llm_entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_configured", "Selora AI not configured")
        return

    connect_url = (msg["connect_url"] or _aigateway_view(entry.data)["connect_url"]).rstrip("/")

    import time as _time

    import aiohttp

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                f"{connect_url}/oauth/aigw/token",
                data={
                    "grant_type": "authorization_code",
                    "code": msg["code"],
                    "code_verifier": msg["code_verifier"],
                    "client_id": msg["client_id"],
                    "redirect_uri": msg["redirect_uri"],
                },
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    _LOGGER.warning("AI Gateway token exchange failed (%s): %s", resp.status, body)
                    connection.send_error(
                        msg["id"],
                        "token_exchange_failed",
                        f"AI Gateway returned HTTP {resp.status}",
                    )
                    return
                token_data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            connection.send_error(msg["id"], "connect_unreachable", f"Cannot reach Connect: {err}")
            return

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = int(token_data.get("expires_in") or 0)
    if not access_token or not refresh_token:
        connection.send_error(
            msg["id"], "token_exchange_failed", "Missing access_token or refresh_token"
        )
        return

    claims = _decode_jwt_claims(access_token)
    user_email = claims.get("email") or ""
    user_id = str(claims.get("sub") or "")
    expires_at = _time.time() + expires_in if expires_in > 0 else 0.0

    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_LLM_PROVIDER: LLM_PROVIDER_SELORA_CLOUD,
            CONF_AIGATEWAY_ACCESS_TOKEN: access_token,
            CONF_AIGATEWAY_REFRESH_TOKEN: refresh_token,
            CONF_AIGATEWAY_EXPIRES_AT: expires_at,
            CONF_AIGATEWAY_USER_EMAIL: user_email,
            CONF_AIGATEWAY_USER_ID: user_id,
            CONF_AIGATEWAY_CLIENT_ID: msg["client_id"],
            CONF_SELORA_CONNECT_URL: connect_url,
        },
    )

    connection.send_result(
        msg["id"],
        {"status": "linked", "user_email": user_email},
    )

    async def _reload() -> None:
        try:
            await hass.config_entries.async_reload(entry.entry_id)
        except Exception:
            _LOGGER.exception("Failed to reload entry after AI Gateway linking")

    hass.async_create_task(_reload())


@websocket_api.async_response
@decorators.websocket_command({vol.Required("type"): "selora_ai/unlink_aigateway"})
async def _handle_websocket_unlink_aigateway(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Remove AI Gateway OAuth credentials from the config entry.

    Falls back to the default LLM provider so the integration keeps
    working after an unlink.
    """
    if not _require_admin(connection, msg):
        return

    entry = _resolve_llm_entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_configured", "Selora AI not configured")
        return

    new_data = {**entry.data}
    for key in (
        CONF_AIGATEWAY_ACCESS_TOKEN,
        CONF_AIGATEWAY_REFRESH_TOKEN,
        CONF_AIGATEWAY_EXPIRES_AT,
        CONF_AIGATEWAY_USER_EMAIL,
        CONF_AIGATEWAY_USER_ID,
        CONF_AIGATEWAY_CLIENT_ID,
    ):
        new_data.pop(key, None)
    # The hub auto-provisioner stores the same credentials under a nested
    # "selora_ai_gateway" object; drop it (and its legacy alias) too so
    # the user really is unlinked rather than silently re-linking on
    # next reload.
    new_data.pop("selora_ai_gateway", None)
    new_data.pop("ai_gateway", None)
    # Keep llm_provider on selora_cloud so the user stays in the same UI
    # state (set URL override, re-link). The provider will fail health
    # checks until re-linking, which is the expected mid-flow behaviour.

    hass.config_entries.async_update_entry(entry, data=new_data)
    connection.send_result(msg["id"], {"status": "unlinked"})

    async def _reload() -> None:
        try:
            await hass.config_entries.async_reload(entry.entry_id)
        except Exception:
            _LOGGER.exception("Failed to reload entry after AI Gateway unlinking")

    hass.async_create_task(_reload())


# ── MCP Token Management (WebSocket) ─────────────────────────────────────────


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/create_mcp_token",
        vol.Required("name"): str,
        vol.Required("permission_level"): str,
        vol.Optional("allowed_tools"): [str],
        vol.Optional("expires_in_days"): vol.Coerce(int),
    }
)
async def _handle_websocket_create_mcp_token(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create a new MCP token with the specified permissions."""
    if not _require_admin(connection, msg):
        return

    store = hass.data.get(DOMAIN, {}).get("mcp_token_store")
    if store is None:
        connection.send_error(msg["id"], "not_ready", "MCP token store not initialized")
        return

    from .const import MCP_TOKEN_PERMISSION_CUSTOM

    permission_level = msg["permission_level"]
    allowed_tools = msg.get("allowed_tools")

    # Validate: custom permission requires allowed_tools
    if permission_level == MCP_TOKEN_PERMISSION_CUSTOM and not allowed_tools:
        connection.send_error(
            msg["id"],
            "invalid_params",
            "Custom permission level requires 'allowed_tools' list",
        )
        return

    # Ignore allowed_tools for non-custom tokens (prevent privilege escalation)
    if permission_level != MCP_TOKEN_PERMISSION_CUSTOM:
        allowed_tools = None

    # Compute expiration
    expires_at: str | None = None
    expires_in_days = msg.get("expires_in_days")
    if expires_in_days is not None:
        from datetime import UTC, datetime, timedelta

        expires_at = (datetime.now(UTC) + timedelta(days=expires_in_days)).isoformat()

    user = getattr(connection, "user", None)
    user_id = getattr(user, "id", "unknown") if user else "unknown"

    try:
        raw_token, meta = await store.async_create_token(
            name=msg["name"],
            permission_level=permission_level,
            allowed_tools=allowed_tools,
            expires_at=expires_at,
            created_by_user_id=user_id,
        )
    except ValueError as exc:
        connection.send_error(msg["id"], "invalid_params", str(exc))
        return

    connection.send_result(
        msg["id"],
        {
            "token": raw_token,
            "id": meta["id"],
            "name": meta["name"],
            "permission_level": meta["permission_level"],
            "allowed_tools": meta["allowed_tools"],
            "expires_at": meta["expires_at"],
        },
    )


@websocket_api.async_response
@decorators.websocket_command({vol.Required("type"): "selora_ai/list_mcp_tokens"})
async def _handle_websocket_list_mcp_tokens(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List all MCP tokens (metadata only, no secrets)."""
    if not _require_admin(connection, msg):
        return

    store = hass.data.get(DOMAIN, {}).get("mcp_token_store")
    if store is None:
        connection.send_error(msg["id"], "not_ready", "MCP token store not initialized")
        return

    tokens = await store.async_list_tokens()
    connection.send_result(msg["id"], {"tokens": tokens})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/revoke_mcp_token",
        vol.Required("token_id"): str,
    }
)
async def _handle_websocket_revoke_mcp_token(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Revoke an MCP token by ID."""
    if not _require_admin(connection, msg):
        return

    store = hass.data.get(DOMAIN, {}).get("mcp_token_store")
    if store is None:
        connection.send_error(msg["id"], "not_ready", "MCP token store not initialized")
        return

    revoked = await store.async_revoke_token(msg["token_id"])
    if not revoked:
        connection.send_error(msg["id"], "not_found", "Token not found")
        return

    connection.send_result(msg["id"], {"success": True})


# ── Device Detail (WebSocket) ─────────────────────────────────────────────────


def _automation_references_device(obj: Any, identifiers: set[str]) -> bool:
    """Check if an automation dict references any of the given identifiers.

    *identifiers* should include both entity IDs and the device_id so that
    device-based triggers/actions (ZHA, remotes, MQTT) are caught too.

    Walks the dict/list structure checking string values:
    - Exact match catches bare ``entity_id`` / ``device_id`` fields.
    - Word-boundary regex catches references inside Jinja templates
      (``{{ is_state('light.kitchen', 'on') }}``) and comma-separated
      entity lists (``entity_id: light.kitchen, light.dining``).
    """
    if isinstance(obj, str):
        if obj in identifiers:
            return True
        # Word-boundary check for templates and CSV entity strings
        import re

        for ident in identifiers:
            if re.search(r"(?<![.\w])" + re.escape(ident) + r"(?![.\w])", obj):
                return True
        return False
    if isinstance(obj, dict):
        return any(_automation_references_device(v, identifiers) for v in obj.values())
    if isinstance(obj, list):
        return any(_automation_references_device(item, identifiers) for item in obj)
    return False


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_device_detail",
        vol.Required("device_id"): str,
    }
)
async def _handle_websocket_get_device_detail(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return full device detail with state history, linked automations, and patterns."""
    if not _require_admin(connection, msg):
        return

    try:
        device_id = msg["device_id"]

        # 1. Device metadata + entity states (reuse MCP tool)
        from .mcp_server import _tool_get_device

        device_data = await _tool_get_device(hass, {"device_id": device_id})
        if "error" in device_data:
            connection.send_error(msg["id"], "not_found", device_data["error"])
            return

        # 2. State history (last 24h for device entities)
        entity_ids = [e["entity_id"] for e in device_data.get("entities", [])]
        state_history: list[dict[str, Any]] = []
        if entity_ids:
            try:
                from homeassistant.components.recorder import get_instance
                from homeassistant.components.recorder.history import (
                    get_significant_states,
                )

                now = datetime.now(UTC)
                start = now - timedelta(hours=24)
                states = await get_instance(hass).async_add_executor_job(
                    get_significant_states, hass, start, now, entity_ids
                )
                for eid, eid_states in states.items():
                    for state in eid_states[-20:]:  # Last 20 per entity
                        state_history.append(
                            {
                                "entity_id": eid,
                                "state": state.state,
                                "last_changed": state.last_changed.isoformat()
                                if state.last_changed
                                else None,
                            }
                        )
            except (ImportError, KeyError):
                _LOGGER.debug("Recorder not available for device detail history")

        # 3. Linked automations (scan for entity references)
        linked_automations: list[dict[str, Any]] = []
        from pathlib import Path

        from .automation_utils import _read_automations_yaml

        automations_path = Path(hass.config.config_dir) / "automations.yaml"
        try:
            yaml_autos = await hass.async_add_executor_job(_read_automations_yaml, automations_path)
            identifiers = set(entity_ids) | {device_id}
            for auto in yaml_autos:
                if _automation_references_device(auto, identifiers):
                    linked_automations.append(
                        {
                            "id": auto.get("id", ""),
                            "alias": auto.get("alias", ""),
                            "description": auto.get("description", ""),
                        }
                    )
        except (FileNotFoundError, OSError):
            _LOGGER.debug("Could not read automations for device detail")

        # 4. Related patterns
        related_patterns: list[dict[str, Any]] = []
        pattern_store = _get_pattern_store(hass)
        if pattern_store:
            all_patterns = await pattern_store.get_patterns()
            entity_set = set(entity_ids)
            for p in all_patterns:
                pattern_entities = set(p.get("entity_ids", []))
                if pattern_entities & entity_set:
                    related_patterns.append(
                        {
                            "pattern_id": p.get("pattern_id", ""),
                            "type": p.get("type", ""),
                            "description": p.get("description", ""),
                            "confidence": p.get("confidence", 0),
                            "status": p.get("status", ""),
                        }
                    )

        connection.send_result(
            msg["id"],
            {
                **device_data,
                "state_history": state_history,
                "linked_automations": linked_automations,
                "related_patterns": related_patterns,
            },
        )
    except Exception as exc:
        _LOGGER.exception("Error in get_device_detail")
        connection.send_error(msg["id"], "unknown_error", str(exc))


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Selora AI component."""
    hass.data.setdefault(DOMAIN, {})

    # Register WebSocket API
    websocket_api.async_register_command(hass, _handle_websocket_chat)
    websocket_api.async_register_command(hass, _handle_websocket_chat_stream)
    websocket_api.async_register_command(hass, _handle_websocket_create_automation)
    websocket_api.async_register_command(hass, _handle_websocket_apply_automation_yaml)
    websocket_api.async_register_command(hass, _handle_websocket_update_automation_yaml)
    websocket_api.async_register_command(hass, _handle_websocket_rename_automation)
    websocket_api.async_register_command(hass, _handle_websocket_get_suggestions)
    websocket_api.async_register_command(hass, _handle_websocket_get_automations)
    websocket_api.async_register_command(hass, _handle_websocket_get_config)
    websocket_api.async_register_command(hass, _handle_websocket_update_config)
    websocket_api.async_register_command(hass, _handle_websocket_validate_llm_key)
    # Conversation sessions
    websocket_api.async_register_command(hass, _handle_websocket_get_sessions)
    websocket_api.async_register_command(hass, _handle_websocket_get_session)
    websocket_api.async_register_command(hass, _handle_websocket_new_session)
    websocket_api.async_register_command(hass, _handle_websocket_rename_session)
    websocket_api.async_register_command(hass, _handle_websocket_generate_suggestions)
    websocket_api.async_register_command(hass, _handle_websocket_quick_create_automation)
    websocket_api.async_register_command(hass, _handle_websocket_create_draft)
    websocket_api.async_register_command(hass, _handle_websocket_get_drafts)
    websocket_api.async_register_command(hass, _handle_websocket_remove_draft)
    websocket_api.async_register_command(hass, _handle_websocket_delete_session)
    websocket_api.async_register_command(hass, _handle_websocket_set_automation_status)
    websocket_api.async_register_command(hass, _handle_websocket_set_scene_status)
    websocket_api.async_register_command(hass, _handle_websocket_accept_scene)
    # Automation lifecycle
    websocket_api.async_register_command(hass, _handle_websocket_get_automation_versions)
    websocket_api.async_register_command(hass, _handle_websocket_get_automation_diff)
    websocket_api.async_register_command(hass, _handle_websocket_toggle_automation)
    websocket_api.async_register_command(hass, _handle_websocket_delete_automation)
    websocket_api.async_register_command(hass, _handle_websocket_get_automation_lineage)
    websocket_api.async_register_command(hass, _handle_websocket_get_session_automations)
    websocket_api.async_register_command(hass, _handle_websocket_load_automation_to_session)
    # Proactive suggestions
    websocket_api.async_register_command(hass, _handle_websocket_get_proactive_suggestions)
    websocket_api.async_register_command(hass, _handle_websocket_update_proactive_suggestion)
    websocket_api.async_register_command(hass, _handle_websocket_get_state_history_summary)
    websocket_api.async_register_command(hass, _handle_websocket_get_analytics)
    websocket_api.async_register_command(hass, _handle_websocket_get_recent_usage)
    websocket_api.async_register_command(hass, _handle_websocket_get_pricing_defaults)
    websocket_api.async_register_command(hass, _handle_websocket_get_usage_breakdown)
    websocket_api.async_register_command(hass, _handle_websocket_get_usage_totals)
    websocket_api.async_register_command(hass, _handle_websocket_get_patterns)
    websocket_api.async_register_command(hass, _handle_websocket_get_pattern_detail)
    websocket_api.async_register_command(hass, _handle_websocket_update_pattern_status)
    websocket_api.async_register_command(hass, _handle_websocket_get_suggestion_detail)
    websocket_api.async_register_command(hass, _handle_websocket_accept_suggestion_with_edits)
    websocket_api.async_register_command(hass, _handle_websocket_trigger_pattern_scan)
    websocket_api.async_register_command(hass, _handle_websocket_exchange_connect_code)
    websocket_api.async_register_command(hass, _handle_websocket_unlink_connect)
    websocket_api.async_register_command(hass, _handle_websocket_exchange_aigateway_code)
    websocket_api.async_register_command(hass, _handle_websocket_unlink_aigateway)
    # HA-mediated OAuth link (works inside Companion app WebViews)
    from .oauth_link import async_register as _register_oauth_link

    _register_oauth_link(hass)
    # MCP token management
    websocket_api.async_register_command(hass, _handle_websocket_create_mcp_token)
    websocket_api.async_register_command(hass, _handle_websocket_list_mcp_tokens)
    websocket_api.async_register_command(hass, _handle_websocket_revoke_mcp_token)
    # Device detail
    websocket_api.async_register_command(hass, _handle_websocket_get_device_detail)
    # Scene management
    websocket_api.async_register_command(hass, _handle_websocket_get_scenes)
    websocket_api.async_register_command(hass, _handle_websocket_load_scene_to_session)
    websocket_api.async_register_command(hass, _handle_websocket_delete_scene)
    websocket_api.async_register_command(hass, _handle_websocket_activate_scene)

    # Register static path for frontend
    # Modern way to register static paths (2024.7+)
    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    f"/api/{DOMAIN}/panel.js",
                    hass.config.path(f"custom_components/{DOMAIN}/frontend/panel.js"),
                    False,
                ),
                StaticPathConfig(
                    f"/api/{DOMAIN}/logo.png",
                    hass.config.path(f"custom_components/{DOMAIN}/brand/logo.png"),
                    True,
                ),
                StaticPathConfig(
                    f"/api/{DOMAIN}/logo-light.png",
                    hass.config.path(f"custom_components/{DOMAIN}/brand/logo-light.png"),
                    True,
                ),
            ]
        )
    except (ImportError, AttributeError):
        # Fallback for older versions
        hass.http.register_static_path(
            f"/api/{DOMAIN}/panel.js",
            hass.config.path(f"custom_components/{DOMAIN}/frontend/panel.js"),
            False,
        )
        hass.http.register_static_path(
            f"/api/{DOMAIN}/logo.png",
            hass.config.path(f"custom_components/{DOMAIN}/brand/logo.png"),
            True,
        )
        hass.http.register_static_path(
            f"/api/{DOMAIN}/logo-light.png",
            hass.config.path(f"custom_components/{DOMAIN}/brand/logo-light.png"),
            True,
        )

    # Register custom side panel in the sidebar
    from homeassistant.components import frontend

    # In recent HA, async_register_panel might be deprecated or renamed
    # We try both async_register_panel and async_register_built_in_panel
    if hasattr(frontend, "async_register_panel"):
        frontend.async_register_panel(
            hass,
            frontend_url_path=PANEL_PATH,
            webcomponent_name=PANEL_NAME,
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            module_url=f"/api/{DOMAIN}/panel.js",
            config={"domain": DOMAIN},
            require_admin=True,
        )
    elif hasattr(frontend, "async_register_built_in_panel"):
        try:
            frontend.async_register_built_in_panel(
                hass,
                component_name="custom",
                sidebar_title=PANEL_TITLE,
                sidebar_icon=PANEL_ICON,
                frontend_url_path=PANEL_PATH,
                config={
                    "_panel_custom": {
                        "name": PANEL_NAME,
                        "module_url": f"/api/{DOMAIN}/panel.js",
                    },
                    "domain": DOMAIN,
                },
                require_admin=True,
            )
        except ValueError as err:
            _LOGGER.warning("Panel already registered: %s", err)
    else:
        _LOGGER.warning(
            "Neither async_register_panel nor async_register_built_in_panel found in frontend"
        )

    _LOGGER.info("Selora AI initialized (awaiting entry)")

    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry to a new version."""
    from .migrations import async_migrate_entry as _migrate  # noqa: PLC0415

    return await _migrate(hass, entry)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Selora AI from a config entry."""
    # Device onboarding entries are records only — no runtime setup needed
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_DEVICE:
        _LOGGER.info("Selora AI device onboarding entry loaded: %s", entry.title)
        return True

    provider = _resolve_llm_provider(entry.data)

    lookback = entry.data.get(CONF_RECORDER_LOOKBACK_DAYS, DEFAULT_RECORDER_LOOKBACK_DAYS)
    pricing_overrides = entry.options.get(CONF_LLM_PRICING_OVERRIDES) or {}

    from .device_manager import DeviceManager
    from .llm_client import LLMClient, async_preload_prompts
    from .providers import create_provider

    await async_preload_prompts(hass)

    llm: LLMClient | None = None
    if provider == LLM_PROVIDER_ANTHROPIC:
        llm_provider = create_provider(
            provider,
            hass,
            api_key=entry.data.get(CONF_ANTHROPIC_API_KEY, ""),
            model=entry.data.get(CONF_ANTHROPIC_MODEL, DEFAULT_ANTHROPIC_MODEL),
        )
        llm = LLMClient(
            hass, llm_provider, lookback_days=lookback, pricing_overrides=pricing_overrides
        )
    elif provider == LLM_PROVIDER_GEMINI:
        llm_provider = create_provider(
            provider,
            hass,
            api_key=entry.data.get(CONF_GEMINI_API_KEY, ""),
            model=entry.data.get(CONF_GEMINI_MODEL, DEFAULT_GEMINI_MODEL),
        )
        llm = LLMClient(
            hass, llm_provider, lookback_days=lookback, pricing_overrides=pricing_overrides
        )
    elif provider == LLM_PROVIDER_OPENAI:
        llm_provider = create_provider(
            provider,
            hass,
            api_key=entry.data.get(CONF_OPENAI_API_KEY, ""),
            model=entry.data.get(CONF_OPENAI_MODEL, DEFAULT_OPENAI_MODEL),
        )
        llm = LLMClient(
            hass, llm_provider, lookback_days=lookback, pricing_overrides=pricing_overrides
        )
    elif provider == LLM_PROVIDER_OPENROUTER:
        llm_provider = create_provider(
            provider,
            hass,
            api_key=entry.data.get(CONF_OPENROUTER_API_KEY, ""),
            model=entry.data.get(CONF_OPENROUTER_MODEL, DEFAULT_OPENROUTER_MODEL),
        )
        llm = LLMClient(
            hass, llm_provider, lookback_days=lookback, pricing_overrides=pricing_overrides
        )
    elif provider == LLM_PROVIDER_OLLAMA:
        llm_provider = create_provider(
            provider,
            hass,
            host=entry.data.get(CONF_OLLAMA_HOST, DEFAULT_OLLAMA_HOST),
            model=entry.data.get(CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL),
        )
        llm = LLMClient(
            hass, llm_provider, lookback_days=lookback, pricing_overrides=pricing_overrides
        )
    elif provider == LLM_PROVIDER_SELORA_LOCAL:
        llm_provider = create_provider(
            provider,
            hass,
            host=entry.data.get(CONF_SELORA_LOCAL_HOST, DEFAULT_SELORA_LOCAL_HOST),
        )
        llm = LLMClient(
            hass, llm_provider, lookback_days=lookback, pricing_overrides=pricing_overrides
        )
    elif provider == LLM_PROVIDER_SELORA_CLOUD:
        aigw = _aigateway_view(entry.data)
        llm_provider = create_provider(
            provider,
            hass,
            access_token=aigw["access_token"],
            refresh_token=aigw["refresh_token"],
            expires_at=aigw["expires_at"],
            connect_url=aigw["connect_url"],
            client_id=aigw["client_id"],
            entry_id=entry.entry_id,
        )
        llm = LLMClient(
            hass, llm_provider, lookback_days=lookback, pricing_overrides=pricing_overrides
        )

    from .collector import DataCollector

    collector = DataCollector(hass, llm, lookback_days=lookback, settings=entry.options)
    device_mgr = DeviceManager(
        hass,
        api_key=entry.data.get(CONF_ANTHROPIC_API_KEY, "") if llm else "",
        model=entry.data.get(CONF_ANTHROPIC_MODEL, DEFAULT_ANTHROPIC_MODEL)
        if llm
        else DEFAULT_ANTHROPIC_MODEL,
    )

    # Store references for cleanup on unload
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "llm": llm,
        "collector": collector,
        "device_manager": device_mgr,
        "unsub_discovery": None,  # Will be set below
        "_background_tasks": [],  # Tracked for cancellation on unload
    }

    from .mcp_server import register_mcp_server

    register_mcp_server(hass)

    # Selora Connect JWT validator (enables OAuth 2.0 auth for MCP)
    jwt_key_b64 = entry.data.get(CONF_SELORA_JWT_KEY)
    installation_id = entry.data.get(CONF_SELORA_INSTALLATION_ID)
    connect_url = entry.data.get(CONF_SELORA_CONNECT_URL, DEFAULT_SELORA_CONNECT_URL)
    hass.data[DOMAIN]["selora_connect_url"] = connect_url
    if jwt_key_b64 and installation_id:
        from .selora_auth import SeloraJWTValidator, decode_jwt_key

        derived_key = decode_jwt_key(jwt_key_b64)
        hass.data[DOMAIN]["selora_jwt_validator"] = SeloraJWTValidator(
            derived_key=derived_key,
            installation_id=installation_id,
            issuer=connect_url,
        )
        _LOGGER.info("Selora Connect JWT validator initialized (%s)", connect_url)
    else:
        hass.data[DOMAIN]["selora_jwt_validator"] = None

    # Selora MCP token store (locally-created API keys with per-tool permissions)
    from .mcp_token_store import MCPTokenStore

    mcp_token_store = MCPTokenStore(hass)
    await mcp_token_store.async_load()
    hass.data[DOMAIN]["mcp_token_store"] = mcp_token_store

    # Schedule periodic discovery if enabled
    options = entry.options
    discovery_enabled = options.get(CONF_DISCOVERY_ENABLED, DEFAULT_DISCOVERY_ENABLED)
    pattern_enabled = options.get(CONF_PATTERN_ENABLED, True)

    async def _run_discovery(_now: datetime | None = None) -> None:
        """Run the discovery process and respect settings."""
        # Respect schedule window if not initial startup
        if _now is not None:
            mode = options.get(CONF_DISCOVERY_MODE, DEFAULT_DISCOVERY_MODE)
            if mode == MODE_SCHEDULED:
                start_str = options.get(CONF_DISCOVERY_START_TIME, "00:00")
                end_str = options.get(CONF_DISCOVERY_END_TIME, "23:59")

                # Inline _is_within_window logic
                try:
                    now_time = datetime.now().time()
                    start_time = datetime.strptime(start_str, "%H:%M").time()
                    end_time = datetime.strptime(end_str, "%H:%M").time()

                    within = False
                    if start_time <= end_time:
                        within = start_time <= now_time <= end_time
                    else:
                        within = now_time >= start_time or now_time <= end_time

                    if not within:
                        _LOGGER.debug(
                            "Outside discovery window (%s - %s), skipping", start_str, end_str
                        )
                        return
                except ValueError:
                    _LOGGER.error("Invalid discovery time format: %s or %s", start_str, end_str)

        try:
            result = await device_mgr.discover_network_devices()
            summary = result.get("summary", {})
            _LOGGER.info(
                "Network discovery: %d discovered, %d configured, %d available",
                summary.get("discovered_count", 0),
                summary.get("configured_count", 0),
                summary.get("available_count", 0),
            )
            # Auto-assign areas (safe)
            area_result = await device_mgr.auto_assign_areas()
            if area_result.get("assigned"):
                _LOGGER.info("Auto-assigned %d devices to areas", len(area_result["assigned"]))

            async_dispatcher_send(hass, SIGNAL_DEVICES_UPDATED)

            discovered_count = summary.get("discovered_count", 0)
            if discovered_count > 0:
                async_dispatcher_send(
                    hass,
                    SIGNAL_ACTIVITY_LOG,
                    f"Network discovery: {discovered_count} new devices found",
                    "discover",
                )
        except Exception:
            _LOGGER.exception("Discovery task failed")

    # Initial delayed discovery
    async def _delayed_discovery() -> None:
        await asyncio.sleep(30)
        if discovery_enabled:
            await _run_discovery()

    _bg = hass.data[DOMAIN][entry.entry_id]["_background_tasks"]
    _bg.append(hass.async_create_task(_delayed_discovery()))

    # Periodic discovery timer
    unsub_discovery = None
    if discovery_enabled:
        interval = options.get(CONF_DISCOVERY_INTERVAL, DEFAULT_DISCOVERY_INTERVAL)
        unsub_discovery = async_track_time_interval(
            hass, _run_discovery, timedelta(seconds=interval)
        )
        _LOGGER.info("Periodic discovery started (interval: %ss)", interval)
        hass.data[DOMAIN][entry.entry_id]["unsub_discovery"] = unsub_discovery

    # Set up entity platforms (sensor + button)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Verify LLM is healthy — runs after platform setup so usage sensors
    # are registered and can capture the tokens from the health-check call.
    if llm and not await llm.health_check():
        if not llm.is_configured:
            _LOGGER.info(
                "%s is not linked — chat and analysis will resume after re-linking in Settings",
                llm.provider_name,
            )
        else:
            _LOGGER.warning(
                "%s not reachable — will retry on next collection cycle",
                llm.provider_name,
            )

    # Start background collection + analysis
    if llm:
        await collector.async_start()
        _LOGGER.info("Selora AI started (%s)", llm.provider_name)
    else:
        _LOGGER.info("Selora AI started (unconfigured mode)")

    # One-time startup cleanup: remove orphaned entity registry entries
    async def _cleanup_orphaned_entities() -> None:
        try:
            from .automation_utils import async_cleanup_orphaned_entities

            orphaned = await async_cleanup_orphaned_entities(hass)
            if orphaned:
                _LOGGER.info(
                    "Startup cleanup removed %d orphaned entity entries: %s",
                    len(orphaned),
                    orphaned,
                )
        except Exception:
            _LOGGER.exception("Startup entity cleanup failed")

    _bg.append(hass.async_create_task(_cleanup_orphaned_entities()))

    # One-time startup cleanup: remove stale one-shot scheduled automations
    # whose fire date has passed (cleanup callback lost across restart).
    async def _cleanup_stale_schedules() -> None:
        try:
            from .scheduled_actions import ScheduledTaskTracker

            tracker: ScheduledTaskTracker = hass.data[DOMAIN].setdefault(
                "_scheduled_tasks", ScheduledTaskTracker(hass)
            )
            await tracker.async_cleanup_stale_automations()
        except Exception:
            _LOGGER.exception("Startup scheduled automation cleanup failed")

    _bg.append(hass.async_create_task(_cleanup_stale_schedules()))

    if pattern_enabled:
        from .pattern_store import PatternStore

        pattern_store = PatternStore(hass)

        async def _state_change_listener(event: Any) -> None:
            """Record state changes for pattern detection."""
            entity_id = event.data.get("entity_id", "")
            domain = entity_id.split(".")[0] if "." in entity_id else ""
            if domain not in COLLECTOR_DOMAINS:
                return
            new_state = event.data.get("new_state")
            old_state = event.data.get("old_state")
            if new_state is None or old_state is None:
                return
            if new_state.state == old_state.state:
                return
            await pattern_store.record_state_change(
                entity_id,
                new_state.state,
                old_state.state,
                new_state.last_changed.isoformat(),
            )

        unsub_state_listener = hass.bus.async_listen("state_changed", _state_change_listener)
        hass.data[DOMAIN][entry.entry_id]["pattern_store"] = pattern_store
        hass.data[DOMAIN][entry.entry_id]["unsub_state_listener"] = unsub_state_listener

        _bg.append(hass.async_create_task(pattern_store.backfill_from_recorder(hass, lookback)))

        from .pattern_engine import PatternEngine

        pattern_engine = PatternEngine(hass, pattern_store)
        hass.data[DOMAIN][entry.entry_id]["pattern_engine"] = pattern_engine

        from .suggestion_generator import SuggestionGenerator

        suggestion_generator = SuggestionGenerator(hass, pattern_store, llm)
        hass.data[DOMAIN][entry.entry_id]["suggestion_generator"] = suggestion_generator

        async def _on_patterns_detected(patterns: list[dict[str, Any]]) -> None:
            """Callback: convert new patterns into proactive suggestions."""
            suggestions = await suggestion_generator.generate_from_patterns(patterns)
            if suggestions:
                existing = hass.data[DOMAIN].get("proactive_suggestions", [])

                # Deduplicate against already-queued suggestions by content (#46)
                existing_fps: set[str] = set()
                for s in existing:
                    auto_data = s.get("automation_data", {})
                    if auto_data:
                        existing_fps.add(suggestion_content_fingerprint(auto_data))
                for s in suggestions:
                    fp = suggestion_content_fingerprint(s.get("automation_data", {}))
                    if fp not in existing_fps:
                        existing.append(s)
                        existing_fps.add(fp)

                hass.data[DOMAIN]["proactive_suggestions"] = existing[-50:]
                async_dispatcher_send(hass, SIGNAL_PROACTIVE_SUGGESTIONS)

        pattern_engine.on_patterns_detected = _on_patterns_detected
        await pattern_engine.async_start()

        enrichment_interval = entry.options.get(
            CONF_ENRICHMENT_INTERVAL, DEFAULT_ENRICHMENT_INTERVAL
        )

        async def _enrichment_cycle(_now: Any) -> None:
            enriched = await suggestion_generator.enrich_pending()
            if enriched:
                cache = hass.data[DOMAIN].get("proactive_suggestions", [])
                store_suggestions = {
                    s["suggestion_id"]: s
                    for s in await pattern_store.get_suggestions(status="pending")
                    if s.get("suggestion_id")
                }
                for cached in cache:
                    sid = cached.get("suggestion_id")
                    updated = store_suggestions.get(sid) if sid else None
                    if updated:
                        cached["description"] = updated.get(
                            "description", cached.get("description")
                        )
                        cached["source"] = updated.get("source", cached.get("source"))
                async_dispatcher_send(hass, SIGNAL_PROACTIVE_SUGGESTIONS)

        unsub_enrichment = async_track_time_interval(
            hass,
            _enrichment_cycle,
            timedelta(seconds=enrichment_interval),
        )
        hass.data[DOMAIN][entry.entry_id]["unsub_enrichment"] = unsub_enrichment

        _LOGGER.info(
            "Pattern detection + suggestion generation started (enrichment every %ds)",
            enrichment_interval,
        )
    else:
        _LOGGER.info("Pattern detection disabled")

    # Register update listener for options
    snapshots: dict[str, dict[str, Any]] = hass.data.setdefault(DOMAIN, {}).setdefault(
        "_entry_data_snapshots", {}
    )
    snapshots[entry.entry_id] = dict(entry.data)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


_AIGW_TOKEN_FIELDS = frozenset(
    {
        CONF_AIGATEWAY_ACCESS_TOKEN,
        CONF_AIGATEWAY_REFRESH_TOKEN,
        CONF_AIGATEWAY_EXPIRES_AT,
    }
)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options change.

    SeloraCloudProvider persists fresh access tokens back into the entry
    after every refresh. Reloading on that update tears the provider
    down mid-request and has been observed dropping the Authorization
    header on the in-flight call. Snapshot the last-seen entry data and
    skip the reload when only the AI Gateway token fields changed —
    the live provider already holds the new values.
    """
    snapshots: dict[str, dict[str, Any]] = hass.data.setdefault(DOMAIN, {}).setdefault(
        "_entry_data_snapshots", {}
    )
    previous = snapshots.get(entry.entry_id, {})
    current = dict(entry.data)
    snapshots[entry.entry_id] = current

    if previous:
        changed = {
            key for key in previous.keys() | current.keys() if previous.get(key) != current.get(key)
        }
        if changed and changed.issubset(_AIGW_TOKEN_FIELDS):
            return

    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload — stop background tasks, close sessions."""
    # Device onboarding entries have no runtime state to clean up
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_DEVICE:
        return True

    data = hass.data[DOMAIN].pop(entry.entry_id, {})

    collector = data.get("collector")
    unsub_discovery = data.get("unsub_discovery")

    if collector:
        await collector.async_stop()

    if unsub_discovery:
        unsub_discovery()

    # Cancel tracked background tasks and drain their cancellations so we
    # don't leak coroutine frames / traceback objects across reloads.
    pending_tasks = [t for t in data.get("_background_tasks", []) if not t.done()]
    for task in pending_tasks:
        task.cancel()
    for task in pending_tasks:
        with suppress(asyncio.CancelledError, Exception):
            await task

    # Stop pattern detection
    pattern_engine = data.get("pattern_engine")
    if pattern_engine:
        await pattern_engine.async_stop()
    unsub_state = data.get("unsub_state_listener")
    if unsub_state:
        unsub_state()
    unsub_enrichment = data.get("unsub_enrichment")
    if unsub_enrichment:
        unsub_enrichment()
    pattern_store = data.get("pattern_store")
    if pattern_store:
        await pattern_store.flush()

    # Flush pending writes and cancel MCP token store deferred-save timer
    mcp_token_store = hass.data[DOMAIN].get("mcp_token_store")
    if mcp_token_store is not None:
        await mcp_token_store.async_close()

    # Clear Selora Connect JWT validator so stale credentials can't be used
    hass.data[DOMAIN].pop("selora_jwt_validator", None)
    hass.data[DOMAIN].pop("mcp_token_store", None)

    # Cancel pending scheduled tasks so stale timers don't fire after teardown
    scheduled_tracker = hass.data[DOMAIN].pop("_scheduled_tasks", None)
    if scheduled_tracker:
        cancelled = scheduled_tracker.cancel_all_pending()
        if cancelled:
            _LOGGER.info("Cancelled %d pending scheduled tasks on unload", cancelled)

    # Clean up shared in-memory caches
    hass.data[DOMAIN].pop("proactive_suggestions", None)
    hass.data[DOMAIN].pop("latest_suggestions", None)
    hass.data[DOMAIN].pop("_conv_store", None)
    hass.data[DOMAIN].pop("_scene_store", None)

    # Unload entity platforms
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    _LOGGER.info("Selora AI stopped")
    return True
