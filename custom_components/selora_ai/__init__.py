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
from datetime import UTC, datetime, timedelta
import logging
from typing import Any
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

from .const import (
    AUTOMATION_ID_PREFIX,
    COLLECTOR_DOMAINS,
    CONF_ANTHROPIC_API_KEY,
    CONF_ANTHROPIC_MODEL,
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
    CONF_ENTRY_TYPE,
    CONF_LLM_PROVIDER,
    CONF_OLLAMA_HOST,
    CONF_OLLAMA_MODEL,
    CONF_OPENAI_API_KEY,
    CONF_OPENAI_MODEL,
    CONF_PATTERN_ENABLED,
    CONF_RECORDER_LOOKBACK_DAYS,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_DISCOVERY_ENABLED,
    DEFAULT_DISCOVERY_INTERVAL,
    DEFAULT_DISCOVERY_MODE,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_RECORDER_LOOKBACK_DAYS,
    DOMAIN,
    ENTRY_TYPE_DEVICE,
    LIGHT_ENTITY_EXCLUDE_PATTERNS,
    LLM_PROVIDER_ANTHROPIC,
    LLM_PROVIDER_OLLAMA,
    LLM_PROVIDER_OPENAI,
    MODE_SCHEDULED,
    PANEL_ICON,
    PANEL_NAME,
    PANEL_PATH,
    PANEL_TITLE,
    SIGNAL_ACTIVITY_LOG,
    SIGNAL_DEVICES_UPDATED,
    SIGNAL_PROACTIVE_SUGGESTIONS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["conversation"]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_CONVERSATIONS_STORAGE_KEY = f"{DOMAIN}.conversations"
_CONVERSATIONS_STORAGE_VERSION = 1

# Maximum messages kept per session (older messages pruned from the middle,
# keeping the first message for context and the latest N-1 for recency).
_SESSION_MAX_MESSAGES = 100


class ConversationStore:
    """Thin wrapper around HA's Store for persisting chat sessions."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store = Store(
            hass,
            version=_CONVERSATIONS_STORAGE_VERSION,
            key=_CONVERSATIONS_STORAGE_KEY,
        )
        self._data: dict[str, Any] | None = None

    async def _ensure_loaded(self) -> None:
        if self._data is None:
            raw = await self._store.async_load()
            self._data = raw if isinstance(raw, dict) else {"sessions": {}}

    async def list_sessions(self) -> list[dict[str, Any]]:
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

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return a full session including messages, or None if not found."""
        await self._ensure_loaded()
        if self._data is None:
            raise RuntimeError("Session store failed to load")
        return self._data["sessions"].get(session_id)

    async def create_session(self) -> dict[str, Any]:
        """Create a new empty session and persist it."""
        await self._ensure_loaded()
        if self._data is None:
            raise RuntimeError("Session store failed to load")
        now = dt_util.now().isoformat()
        session: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "title": "New conversation",
            "created_at": now,
            "updated_at": now,
            "messages": [],
        }
        self._data["sessions"][session["id"]] = session
        await self._store.async_save(self._data)
        return session

    async def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        automation: dict[str, Any] | None = None,
        automation_yaml: str | None = None,
        description: str | None = None,
        automation_status: str | None = None,
        intent: str | None = None,
        calls: list[dict[str, Any]] | None = None,
        automation_id: str | None = None,
        risk_assessment: dict[str, Any] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
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

        session = self._data["sessions"][session_id]
        message: dict[str, Any] = {
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

        session["messages"].append(message)

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


def _mask_api_key(key: str) -> str:
    """Return a safe display hint — first 8 chars + ellipsis."""
    if not key:
        return ""
    if len(key) <= 8:
        return "***"
    return f"{key[:8]}..."


def _collect_entity_states(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Get current states of all entities for the LLM.

    Filters out unavailable/unknown entities to avoid sending stale or
    deleted entities as context (e.g. soft-deleted automations).
    Also restricts to COLLECTOR_DOMAINS + automation, and excludes
    non-controllable light entities (IR LEDs, camera illuminators, etc.).
    """
    _SKIP_STATES = {"unavailable", "unknown"}
    _ALLOWED_DOMAINS = COLLECTOR_DOMAINS | {"automation"}
    states = []
    for state in hass.states.async_all():
        if state.state in _SKIP_STATES:
            continue
        domain = state.entity_id.split(".")[0]
        if domain not in _ALLOWED_DOMAINS:
            continue
        if domain == "light" and any(
            pat in state.entity_id for pat in LIGHT_ENTITY_EXCLUDE_PATTERNS
        ):
            continue
        states.append(
            {
                "entity_id": state.entity_id,
                "state": _format_entity_state(state.state),
                "attributes": {
                    "friendly_name": state.attributes.get("friendly_name", ""),
                },
            }
        )
    return states


def _format_entity_state(value: str) -> str:
    """Convert ISO 8601 timestamps to 12-hour AM/PM format.

    Non-timestamp values are returned as-is.
    """
    from datetime import datetime

    stripped = value.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            dt = datetime.strptime(stripped, fmt)
            return dt.strftime("%I:%M %p").lstrip("0")
        except ValueError:
            continue
    return value


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
    text = " ".join(str(value or "").split())
    if len(text) > max_length:
        return text[:max_length] + "..."
    return text


def _get_device_manager(hass: HomeAssistant):
    """Find the DeviceManager from hass.data."""
    from .device_manager import DeviceManager

    for entry_data in hass.data.get(DOMAIN, {}).values():
        if isinstance(entry_data, dict) and isinstance(
            entry_data.get("device_manager"), DeviceManager
        ):
            return entry_data["device_manager"]
    return None


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

    from .llm_client import LLMClient

    llm: LLMClient | None = None
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if isinstance(entry_data, dict) and "llm" in entry_data:
            llm = entry_data["llm"]
            break

    if llm is None:
        connection.send_error(msg["id"], "not_initialized", "Selora AI LLM not initialized")
        return

    store: ConversationStore = hass.data[DOMAIN].setdefault("_conv_store", ConversationStore(hass))

    # Resolve or create session
    session_id = msg.get("session_id") or ""
    if session_id:
        session = await store.get_session(session_id)
        if not session:
            # Stale id — start fresh
            session = await store.create_session()
            session_id = session["id"]
    else:
        session = await store.create_session()
        session_id = session["id"]

    # Build history from stored messages.
    # For assistant messages that proposed an automation, append the YAML so the
    # LLM has full context when the user asks to refine it.
    stored_messages = (session or {}).get("messages", [])
    history = []
    for m in stored_messages:
        if m.get("role") not in ("user", "assistant"):
            continue
        content = m["content"]
        if m.get("automation_yaml") and m.get("automation_status") in ("pending", "refining"):
            alias = _sanitize_history_text((m.get("automation") or {}).get("alias", ""))
            description = _sanitize_history_text(m.get("description", ""))
            header = f"[Untrusted automation reference data for context only: {alias}"
            if description:
                header += f" — {description}"
            header += f"]\n{m['automation_yaml']}"
            content = f"{content}\n\n{header}"
        history.append({"role": m["role"], "content": content})

    # Persist the user's message
    user_message = msg["message"]
    await store.append_message(session_id, "user", user_message)

    # Gather home context
    entities = _collect_entity_states(hass)
    automations = []
    for state in hass.states.async_all("automation"):
        automations.append(
            {
                "entity_id": state.entity_id,
                "alias": state.attributes.get("friendly_name", state.entity_id),
                "state": state.state,
            }
        )

    # Create tool executor for device snapshot / integration management
    from .tool_executor import ToolExecutor

    device_mgr = _get_device_manager(hass)
    is_admin = getattr(getattr(connection, "user", None), "is_admin", False)
    tool_executor = ToolExecutor(hass, device_mgr, is_admin=is_admin) if device_mgr else None

    result = await llm.architect_chat(
        user_message,
        entities,
        existing_automations=automations,
        history=history,
        tool_executor=tool_executor,
    )

    if "error" in result and result.get("intent") != "answer":
        connection.send_error(msg["id"], "llm_error", result["error"])
        return

    intent_type = result.get("intent", "answer")
    response_text = result.get("response", "I'm not sure how to help with that.")

    # --- Execute immediate commands ---
    executed: list[str] = []
    if intent_type == "command":
        calls = result.get("calls", [])
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
            except Exception as exc:
                _LOGGER.error("Failed to execute %s: %s", service, exc)
                response_text += f" (Failed: {service}: {exc})"

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
    )

    # Retrieve index of the assistant message just appended (for status updates)
    updated_session = await store.get_session(session_id)
    assistant_message_index = len((updated_session or {}).get("messages", [])) - 1

    # Check if this session is refining an existing automation
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
            "config_issue": result.get("config_issue", False),
            "validation_error": result.get("validation_error"),
            "refining_automation_id": refining_automation_id,
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

    from .llm_client import LLMClient

    # Set up subscription pattern
    connection.subscriptions[msg["id"]] = lambda: None
    connection.send_result(msg["id"])

    # Find the LLM client
    llm: LLMClient | None = None
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if isinstance(entry_data, dict) and "llm" in entry_data:
            llm = entry_data["llm"]
            break

    if llm is None:
        connection.send_message(
            websocket_api.event_message(
                msg["id"], {"type": "error", "message": "Selora AI LLM not initialized"}
            )
        )
        return

    # ---- Session management ----
    store: ConversationStore = hass.data[DOMAIN].setdefault("_conv_store", ConversationStore(hass))

    session_id = msg.get("session_id") or ""
    if session_id:
        session = await store.get_session(session_id)
        if not session:
            session = await store.create_session()
            session_id = session["id"]
    else:
        session = await store.create_session()
        session_id = session["id"]

    # Build history from stored messages (same logic as non-streaming chat)
    stored_messages = (session or {}).get("messages", [])
    history: list[dict[str, str]] = []
    for m in stored_messages:
        if m.get("role") not in ("user", "assistant"):
            continue
        content = m["content"]
        if m.get("automation_yaml") and m.get("automation_status") in ("pending", "refining"):
            alias = _sanitize_history_text((m.get("automation") or {}).get("alias", ""))
            description = _sanitize_history_text(m.get("description", ""))
            header = f"[Untrusted automation reference data for context only: {alias}"
            if description:
                header += f" — {description}"
            header += f"]\n{m['automation_yaml']}"
            content = f"{content}\n\n{header}"
        history.append({"role": m["role"], "content": content})

    # Persist the user message
    user_message = msg["message"]
    await store.append_message(session_id, "user", user_message)

    try:
        entities = _collect_entity_states(hass)

        automations = []
        for state in hass.states.async_all("automation"):
            automations.append(
                {
                    "entity_id": state.entity_id,
                    "alias": state.attributes.get("friendly_name", state.entity_id),
                    "state": state.state,
                }
            )

        # --- Streaming path (with tool support) ---
        from .tool_executor import ToolExecutor

        device_mgr = _get_device_manager(hass)
        is_admin = getattr(getattr(connection, "user", None), "is_admin", False)
        tool_executor = ToolExecutor(hass, device_mgr, is_admin=is_admin) if device_mgr else None

        full_text = ""
        async for chunk in llm.architect_chat_stream(
            user_message,
            entities,
            existing_automations=automations,
            history=history,
            tool_executor=tool_executor,
        ):
            full_text += chunk
            connection.send_message(
                websocket_api.event_message(msg["id"], {"type": "token", "text": chunk})
            )

        parsed = llm.parse_streamed_response(full_text, entities=entities)
        intent_type = parsed.get("intent", "answer")
        response_text = parsed.get("response", full_text)

        await store.append_message(
            session_id,
            "assistant",
            response_text,
            intent=intent_type,
            automation=parsed.get("automation"),
            automation_yaml=parsed.get("automation_yaml"),
            description=parsed.get("description"),
            automation_status="pending" if parsed.get("automation") else None,
            risk_assessment=parsed.get("risk_assessment"),
        )

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
                    "response": response_text,
                    "automation": parsed.get("automation"),
                    "automation_yaml": parsed.get("automation_yaml"),
                    "risk_assessment": parsed.get("risk_assessment"),
                    "automation_message_index": assistant_message_index
                    if parsed.get("automation")
                    else None,
                    "validation_error": parsed.get("validation_error"),
                    "refining_automation_id": refining_automation_id,
                },
            )
        )
    except Exception as exc:
        _LOGGER.exception("Streaming chat failed")
        connection.send_message(
            websocket_api.event_message(msg["id"], {"type": "error", "message": str(exc)})
        )


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

        result = await async_create_automation(
            hass, automation_data, session_id=msg.get("session_id")
        )
        if result["success"]:
            connection.send_result(
                msg["id"], {"status": "success", "automation_id": result["automation_id"]}
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
            result = await async_create_automation(hass, parsed, session_id=msg.get("session_id"))
            if result["success"]:
                connection.send_result(
                    msg["id"], {"status": "created", "automation_id": result["automation_id"]}
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

    from .automation_utils import _read_automations_yaml, _write_automations_yaml

    automation_id = msg["automation_id"]
    new_alias = msg["alias"].strip()
    if not new_alias:
        connection.send_error(msg["id"], "invalid", "Alias cannot be empty")
        return

    automations_path = Path(hass.config.config_dir) / "automations.yaml"
    try:
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
        await hass.services.async_call("automation", "reload")
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
                    await collector._collect_analyze_log()
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
        all_suggestions = []
        seen_aliases: set[str] = set()
        for s in list(hass.data.get(DOMAIN, {}).get("proactive_suggestions", [])):
            alias = (s.get("alias") or "").strip().lower()
            if alias in existing_aliases or alias in seen_aliases:
                continue
            all_suggestions.append(s)
            seen_aliases.add(alias)
        for s in hass.data.get(DOMAIN, {}).get("latest_suggestions", []):
            alias = (s.get("alias") or "").strip().lower()
            if alias in existing_aliases or alias in seen_aliases:
                continue
            all_suggestions.append(s)
            seen_aliases.add(alias)

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

    from .llm_client import LLMClient

    llm: LLMClient | None = None
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if isinstance(entry_data, dict) and "llm" in entry_data:
            llm = entry_data["llm"]
            break

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
        automation["initial_state"] = True

        # Validate before saving — reject broken automations
        from .automation_utils import async_create_automation, validate_automation_payload

        is_valid, reason, normalized = validate_automation_payload(automation)
        if not is_valid or normalized is None:
            connection.send_error(
                msg["id"],
                "invalid_automation",
                f"Generated automation is invalid: {reason}. Try a more specific name.",
            )
            return

        # Sanitize triggers — strip null values that HA rejects
        triggers = normalized.get("trigger") or normalized.get("triggers") or []
        for t in triggers if isinstance(triggers, list) else [triggers]:
            for key in list(t.keys()):
                if t[key] is None:
                    del t[key]

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
                    "persisted_enabled": (
                        full_config.get("initial_state")
                        if isinstance(full_config.get("initial_state"), bool)
                        else None
                    ),
                    "trigger": full_config.get("trigger") or full_config.get("triggers") or [],
                    "condition": full_config.get("condition")
                    or full_config.get("conditions")
                    or [],
                    "action": full_config.get("action") or full_config.get("actions") or [],
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

    # We find the first config entry for our domain
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        connection.send_error(msg["id"], "not_configured", "Selora AI not configured")
        return

    entry = entries[0]
    # Merge entry data with options for a complete view
    config_data = {**entry.data, **entry.options}

    connection.send_result(
        msg["id"],
        {
            "llm_provider": config_data.get(CONF_LLM_PROVIDER),
            # Never send the raw key to the frontend — only a safe display hint.
            "anthropic_api_key_hint": _mask_api_key(config_data.get(CONF_ANTHROPIC_API_KEY, "")),
            "anthropic_api_key_set": bool(config_data.get(CONF_ANTHROPIC_API_KEY)),
            "anthropic_model": config_data.get(CONF_ANTHROPIC_MODEL, DEFAULT_ANTHROPIC_MODEL),
            "openai_api_key_hint": _mask_api_key(config_data.get(CONF_OPENAI_API_KEY, "")),
            "openai_api_key_set": bool(config_data.get(CONF_OPENAI_API_KEY)),
            "openai_model": config_data.get(CONF_OPENAI_MODEL, DEFAULT_OPENAI_MODEL),
            "ollama_host": config_data.get(CONF_OLLAMA_HOST, DEFAULT_OLLAMA_HOST),
            "ollama_model": config_data.get(CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL),
            # Background Services
            "collector_enabled": config_data.get(CONF_COLLECTOR_ENABLED, True),
            "collector_mode": config_data.get(CONF_COLLECTOR_MODE, "continuous"),
            "collector_interval": config_data.get(CONF_COLLECTOR_INTERVAL, 3600),
            "collector_start_time": config_data.get(CONF_COLLECTOR_START_TIME, "09:00"),
            "collector_end_time": config_data.get(CONF_COLLECTOR_END_TIME, "17:00"),
            "discovery_enabled": config_data.get(CONF_DISCOVERY_ENABLED, True),
            "discovery_mode": config_data.get(CONF_DISCOVERY_MODE, "continuous"),
            "discovery_interval": config_data.get(CONF_DISCOVERY_INTERVAL, 14400),
            "discovery_start_time": config_data.get(CONF_DISCOVERY_START_TIME, "00:00"),
            "discovery_end_time": config_data.get(CONF_DISCOVERY_END_TIME, "23:59"),
            # Developer settings
            "developer_mode": config_data.get("developer_mode", False),
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

    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        connection.send_error(msg["id"], "not_configured", "Selora AI not configured")
        return

    entry = entries[0]
    new_config = msg["config"]

    # Split into data and options
    data_keys = {
        CONF_LLM_PROVIDER,
        CONF_ANTHROPIC_API_KEY,
        CONF_ANTHROPIC_MODEL,
        CONF_OPENAI_API_KEY,
        CONF_OPENAI_MODEL,
        CONF_OLLAMA_HOST,
        CONF_OLLAMA_MODEL,
        CONF_ENTRY_TYPE,
    }

    new_data = {k: v for k, v in new_config.items() if k in data_keys}
    new_options = {k: v for k, v in new_config.items() if k not in data_keys}

    # Only overwrite the stored API keys if the frontend sent a new non-empty value.
    # The frontend sends an empty string when the user hasn't touched the key field,
    # so we must not clobber the existing key in that case.
    for key in (CONF_ANTHROPIC_API_KEY, CONF_OPENAI_API_KEY):
        if key in new_data and not new_data[key]:
            new_data.pop(key, None)

    # Keys that only affect the frontend — no reload needed
    frontend_only_keys = {"developer_mode"}

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
            if k not in frontend_only_keys and old_options.get(k) != v:
                needs_reload = True
                break

    # Update the entry
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, **new_data}, options={**entry.options, **new_options}
    )

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


def _get_automation_store(hass: HomeAssistant):
    """Return (or lazily create) the AutomationStore from hass.data."""
    from .automation_store import AutomationStore

    domain_data = hass.data.setdefault(DOMAIN, {})
    if "_automation_store" not in domain_data:
        domain_data["_automation_store"] = AutomationStore(hass)
    return domain_data["_automation_store"]


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

        aid = str(target.get("id", ""))
        desc = str(target.get("description", ""))
        alias = str(target.get("alias", ""))
        is_selora = (
            aid.startswith(AUTOMATION_ID_PREFIX)
            or "[Selora AI]" in desc
            or alias.startswith("[Selora AI]")
        )
        if not is_selora:
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
        f"I've loaded the automation **{alias}** for refinement. "
        "What changes would you like to make?",
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

    is_valid, reason, normalized = validate_automation_payload(automation_data)
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

    try:
        automation_data = yaml.safe_load(msg["automation_yaml"])
    except yaml.YAMLError as exc:
        connection.send_error(msg["id"], "invalid_yaml", str(exc))
        return

    if not isinstance(automation_data, dict):
        connection.send_error(msg["id"], "invalid_yaml", "YAML must be a mapping")
        return

    from .automation_utils import async_create_automation, validate_automation_payload

    is_valid, reason, normalized = validate_automation_payload(automation_data)
    if not is_valid or normalized is None:
        connection.send_error(msg["id"], "invalid_automation", reason or "Validation failed")
        return

    result = await async_create_automation(hass, normalized)
    await pattern_store.update_suggestion_status(msg["suggestion_id"], "accepted")

    connection.send_result(
        msg["id"],
        {
            "status": "accepted",
            "automation_created": result.get("success", False),
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


def _get_pattern_store(hass: HomeAssistant):
    """Find the PatternStore from any active config entry."""
    domain_data = hass.data.get(DOMAIN, {})
    for key, val in domain_data.items():
        if key.startswith("_") or not isinstance(val, dict):
            continue
        store = val.get("pattern_store")
        if store is not None:
            return store
    return None


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
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
    websocket_api.async_register_command(hass, _handle_websocket_get_patterns)
    websocket_api.async_register_command(hass, _handle_websocket_get_pattern_detail)
    websocket_api.async_register_command(hass, _handle_websocket_update_pattern_status)
    websocket_api.async_register_command(hass, _handle_websocket_get_suggestion_detail)
    websocket_api.async_register_command(hass, _handle_websocket_accept_suggestion_with_edits)
    websocket_api.async_register_command(hass, _handle_websocket_trigger_pattern_scan)

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
                    f"/api/{DOMAIN}/card.js",
                    hass.config.path(f"custom_components/{DOMAIN}/frontend/card.js"),
                    False,
                ),
                StaticPathConfig(
                    f"/api/{DOMAIN}/logo.png",
                    hass.config.path(f"custom_components/{DOMAIN}/brand/logo.png"),
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
            f"/api/{DOMAIN}/card.js",
            hass.config.path(f"custom_components/{DOMAIN}/frontend/card.js"),
            False,
        )
        hass.http.register_static_path(
            f"/api/{DOMAIN}/logo.png",
            hass.config.path(f"custom_components/{DOMAIN}/brand/logo.png"),
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

    # Register card.js as a Lovelace resource so HA loads it as a custom card
    card_url = f"/api/{DOMAIN}/card.js"
    if hasattr(frontend, "async_register_extra_module_url"):
        frontend.async_register_extra_module_url(hass, card_url)
    else:
        _LOGGER.warning(
            "async_register_extra_module_url not available; card may not load automatically"
        )

    _LOGGER.info("Selora AI initialized (awaiting entry)")

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Selora AI from a config entry."""
    # Device onboarding entries are records only — no runtime setup needed
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_DEVICE:
        _LOGGER.info("Selora AI device onboarding entry loaded: %s", entry.title)
        return True

    provider = entry.data.get(CONF_LLM_PROVIDER, DEFAULT_LLM_PROVIDER)

    lookback = entry.data.get(CONF_RECORDER_LOOKBACK_DAYS, DEFAULT_RECORDER_LOOKBACK_DAYS)

    from .device_manager import DeviceManager
    from .llm_client import LLMClient

    if provider == LLM_PROVIDER_ANTHROPIC:
        llm = LLMClient(
            hass,
            provider=provider,
            api_key=entry.data.get(CONF_ANTHROPIC_API_KEY, ""),
            model=entry.data.get(CONF_ANTHROPIC_MODEL, DEFAULT_ANTHROPIC_MODEL),
            lookback_days=lookback,
        )
    elif provider == LLM_PROVIDER_OPENAI:
        llm = LLMClient(
            hass,
            provider=provider,
            api_key=entry.data.get(CONF_OPENAI_API_KEY, ""),
            model=entry.data.get(CONF_OPENAI_MODEL, DEFAULT_OPENAI_MODEL),
            lookback_days=lookback,
        )
    elif provider == LLM_PROVIDER_OLLAMA:
        llm = LLMClient(
            hass,
            provider=provider,
            host=entry.data.get(CONF_OLLAMA_HOST, DEFAULT_OLLAMA_HOST),
            model=entry.data.get(CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL),
            lookback_days=lookback,
        )
    else:
        # Provider is NONE (skipped)
        llm = None

    # Verify LLM is healthy on startup
    if llm and not await llm.health_check():
        _LOGGER.warning(
            "%s not reachable — will retry on next collection cycle",
            llm.provider_name,
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
    }

    from .mcp_server import register_mcp_server

    try:
        register_mcp_server(hass)
    except ValueError as err:
        _LOGGER.warning("MCP server already registered: %s", err)

    # One-time cleanup: remove the legacy Hub device and its entities if present
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    hub_device = dev_reg.async_get_device(identifiers={(DOMAIN, "selora_ai_hub")})
    if hub_device:
        for entity in er.async_entries_for_device(
            ent_reg, hub_device.id, include_disabled_entities=True
        ):
            ent_reg.async_remove(entity.entity_id)
        dev_reg.async_remove_device(hub_device.id)
        _LOGGER.info("Removed legacy Selora AI Hub device and its entities")

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

    hass.async_create_task(_delayed_discovery())

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

    hass.async_create_task(_cleanup_orphaned_entities())

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

        hass.async_create_task(pattern_store.backfill_from_recorder(hass, lookback))

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
                existing.extend(suggestions)
                hass.data[DOMAIN]["proactive_suggestions"] = existing[-50:]
                async_dispatcher_send(hass, SIGNAL_PROACTIVE_SUGGESTIONS)

        pattern_engine.on_patterns_detected = _on_patterns_detected
        await pattern_engine.async_start()
        _LOGGER.info("Pattern detection + suggestion generation started")
    else:
        _LOGGER.info("Pattern detection disabled")

    # Register update listener for options
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options change."""
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

    # Stop pattern detection
    pattern_engine = data.get("pattern_engine")
    if pattern_engine:
        await pattern_engine.async_stop()
    unsub_state = data.get("unsub_state_listener")
    if unsub_state:
        unsub_state()
    pattern_store = data.get("pattern_store")
    if pattern_store:
        await pattern_store.flush()

    # Unload entity platforms
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    _LOGGER.info("Selora AI stopped")
    return True
