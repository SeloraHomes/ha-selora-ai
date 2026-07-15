"""Persistent chat-session storage.

Extracted from ``__init__.py``: a thin wrapper around HA's ``Store`` that
persists conversation sessions, messages, and per-session scene indexes.
Re-exported from the package root for backwards compatibility.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

if TYPE_CHECKING:
    from .types import (
        AutomationDict,
        ChatMessage,
        RiskAssessment,
        ServiceCallDict,
        SessionData,
        SessionSummary,
        ToolCallLog,
    )

_CONVERSATIONS_STORAGE_KEY = f"{DOMAIN}.conversations"
_CONVERSATIONS_STORAGE_VERSION = 1

# Maximum messages kept per session (older messages pruned from the middle,
# keeping the first message for context and the latest N-1 for recency).
_SESSION_MAX_MESSAGES = 100

# Maximum number of chat sessions retained.  When exceeded the oldest
# sessions (by updated_at) are evicted to stay within budget.
_SESSION_MAX_COUNT = 200

# Maximum length of the per-session searchable text blob returned in
# summaries.  Message contents are concatenated so the sidebar can fuzzy
# search and extract snippets client-side; the cap keeps the summary
# payload bounded across all sessions.
_SESSION_SEARCH_TEXT_MAX = 4000


def _bounded_search_text(messages: list[ChatMessage]) -> str:
    """Build the capped searchable blob for a session.

    Concatenates non-empty message contents. When the result exceeds the cap
    we keep a head *and* a tail slice (joined by an ellipsis) rather than the
    leading prefix alone: the head preserves the opening turn that states the
    goal, and the tail preserves the most recent turns — the same recency the
    store deliberately retains — so a search for recent content still hits.
    """
    text = " ".join(content for m in messages if (content := (m.get("content") or "").strip()))
    if len(text) <= _SESSION_SEARCH_TEXT_MAX:
        return text
    half = _SESSION_SEARCH_TEXT_MAX // 2
    return f"{text[:half]} … {text[-half:]}"


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
            messages = session.get("messages", [])
            search_text = _bounded_search_text(messages)
            summaries.append(
                {
                    "id": sid,
                    "title": session.get("title", "Untitled"),
                    "created_at": session.get("created_at", ""),
                    "updated_at": session.get("updated_at", ""),
                    "message_count": len(messages),
                    "search_text": search_text,
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
        command_approval: dict[str, Any] | None = None,
        approval_status: str | None = None,
        steps: list[dict[str, Any]] | None = None,
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
        if command_approval is not None:
            message["command_approval"] = command_approval
        if approval_status is not None:
            message["approval_status"] = approval_status
        if steps:
            message["steps"] = steps

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

    async def set_automation_status(
        self,
        session_id: str,
        message_index: int,
        status: str,
        *,
        automation_id: str | None = None,
    ) -> bool:
        """Update the automation_status field of a specific message.

        Optionally persists ``automation_id`` so the chat card can resolve
        the freshly-created automation (e.g. to offer an inline Enable
        action without redirecting the user to the Automations tab).
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
        msgs[message_index]["automation_status"] = status
        if automation_id is not None:
            msgs[message_index]["automation_id"] = automation_id
        session["updated_at"] = dt_util.now().isoformat()
        await self._store.async_save(self._data)
        return True

    async def set_approval_status(self, session_id: str, message_index: int, status: str) -> bool:
        """Update the approval_status field of a command_approval message.

        Called from the resolve_approval WS handler so reloading the
        session shows the proposal as resolved (and the chat UI hides
        the action buttons).
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
        msgs[message_index]["approval_status"] = status
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
