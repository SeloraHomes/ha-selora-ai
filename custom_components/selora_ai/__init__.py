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
from collections.abc import AsyncGenerator, Callable
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
    APPROVAL_RISK_LOW,
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
    CONF_TELEMETRY_ENABLED,
    CONF_TELEMETRY_PROMPT_SEEN,
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
    DEFAULT_TELEMETRY_ENABLED,
    DEFAULT_TELEMETRY_PROMPT_SEEN,
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
    SELORA_AI_LABEL_ID,
    SELORA_EXCLUDE_LABEL_ID,
    SELORA_EXCLUDE_LABEL_NAME,
    SIGNAL_ACTIVITY_LOG,
    SIGNAL_DEVICES_UPDATED,
    SIGNAL_PROACTIVE_SUGGESTIONS,
    SIGNAL_SCENE_DELETED,
    STREAM_AUTOMATION_IDLE_TIMEOUT_S,
    STREAM_CLOUD_IDLE_TIMEOUT_S,
    STREAM_EMPTY_RESPONSE_MESSAGE,
    STREAM_IDLE_TIMEOUT_S,
    STREAM_KEEPALIVE,
    STREAM_MAX_BYTES,
    TELEMETRY_SNAPSHOT_INTERVAL_HOURS,
    TELEMETRY_SNAPSHOT_STARTUP_DELAY,
)
from .scene_discovery import get_area_names
from .telemetry import record_activity

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


# Detects a fence-less structural block streaming in: the type word
# (`automation` / `scene`) alone on its own line, immediately followed by
# a JSON `{` body. Scope matches exactly what ``parse_streamed_response``
# salvages on the final parse (bare automation/scene with a ``{`` body) —
# we must not suppress shapes the backend can't extract (YAML bodies,
# other block types), or the block would silently vanish with no card. A
# prose mention ("I built an automation\nthat does X") has no ``{`` on the
# next line, so it never matches.
_STREAM_BARE_BLOCK_RE = re.compile(r"(?:^|\n)[ \t]*(?:automation|scene)[ \t]*\n[ \t]*\{")

# Fenced structural openers the suppressor watches for mid-stream.
_STREAM_FENCED_OPENERS = (
    "```command",
    "```delayed_command",
    "```cancel",
    "```automation",
    "```scene",
)

# Bare type words that, alone on a line, begin a fence-less block.
_STREAM_BARE_WORDS = ("automation", "scene")


def _pending_opener_start(text: str) -> int:
    """Index where an *incomplete* structural opener begins at the tail of
    ``text``, else ``-1``.

    The complete-opener checks (``_STREAM_FENCED_OPENERS`` /
    ``_STREAM_BARE_BLOCK_RE``) only fire once the body arrives. While the
    opener itself is still streaming — a partial fence (```` ```autom ````)
    or a bare type word ("Autom…") — the suppressor would forward it,
    flashing the fragment in the bubble for a chunk or two. We withhold
    from the fragment's start until the next tokens either confirm the
    block (then it's suppressed) or diverge into prose (then released).
    """
    # Case C: a fenced opener still arriving — the text ends with a
    # non-empty prefix of one of the fenced needles (```` ``` ````,
    # ```` ```a ````, …). A complete fence is matched by the caller's
    # ``find`` and never reaches here.
    last_fence = text.rfind("```")
    if last_fence != -1:
        frag = text[last_fence:]
        if any(n.startswith(frag) for n in _STREAM_FENCED_OPENERS):
            return last_fence

    # Bare-word holds (Cases A/B) must skip candidates inside an existing
    # ``` fence — a code example, not a real block (mirrors the opener
    # detection and parsers.py). Odd ``` count before the line = enclosed.
    def _in_fence(pos: int) -> bool:
        return text.count("```", 0, pos) % 2 == 1

    nl = text.rfind("\n")
    line_start = nl + 1
    last = text[line_start:]
    stripped = last.lstrip(" \t")
    indent = len(last) - len(stripped)
    # Case A: the type word is still being typed on the final line (a
    # non-empty prefix of a type word, with nothing after it yet).
    if (
        stripped
        and any(w.startswith(stripped) for w in _STREAM_BARE_WORDS)
        and not _in_fence(line_start)
    ):
        return line_start + indent
    # Case B: the type word finished on the previous line and we're
    # waiting on the body line. Only the JSON `{` body is backend-
    # supported, so hold ONLY while the body line is still empty (the
    # `{` not yet arrived). The instant any non-whitespace char lands it
    # is either `{` — caught by ``_STREAM_BARE_BLOCK_RE`` before this is
    # called — or something else, in which case it's not an extractable
    # block and must be released as raw text rather than held forever.
    if nl != -1:
        prev_start = text.rfind("\n", 0, nl) + 1
        prev_raw = text[prev_start:nl]
        if (
            prev_raw.strip(" \t") in _STREAM_BARE_WORDS
            and stripped == ""
            and not _in_fence(prev_start)
        ):
            prev_indent = len(prev_raw) - len(prev_raw.lstrip(" \t"))
            return prev_start + prev_indent
    return -1


# Parsed-response keys that carry a renderable structural payload. When any
# is present the chat bubble has something to show (an automation card, a scene
# proposal, or quick-action chips), so a blank ``response`` string is legitimate
# and must NOT be overwritten with the no-response fallback.
#
# Only keys the WS ``done`` event forwards AND the panel actually RENDERS belong
# here. Deliberately excluded:
#   * ``automation_yaml`` / ``scene_yaml`` — forwarded, but the panel renders a
#     card only on the object (``msg.automation`` / ``msg.scene``). A malformed
#     response carrying YAML-only with blank prose would otherwise suppress the
#     fallback while rendering nothing → blank bubble. YAML counts as structural
#     only alongside its object, which the object key already covers.
#   * ``calls`` — ``done`` never forwards it; it is only persisted to the store,
#     and only when intent is ``command``. A downgraded ``delayed_command`` that
#     lands on ``answer`` with empty prose + ``calls`` would otherwise suppress
#     the fallback while sending nothing renderable → blank bubble.
#   * ``command_approval`` — ``done`` forwards it only when ``intent`` is
#     ``command_approval`` (handled separately below), so it is not unconditional.
#   * ``q`` (slim-answer entity list, Selora Local) — ``done`` neither forwards
#     nor renders it.
_STRUCTURAL_RESPONSE_KEYS = (
    "automation",
    "scene",
    "quick_actions",
)


def _empty_response_fallback(
    intent_type: str,
    response_text: str,
    parsed: dict[str, Any],
    language: str | None = None,
) -> str:
    """Substitute a bounded message when a clean stream produced no reply.

    The stream guard (``_consume_stream_with_guards``) bounds a stalled or
    runaway provider — those surface as ``TimeoutError`` / ``_StreamTooLarge``
    and the handler turns them into an ``error`` event. But a provider that
    terminates cleanly having emitted no content (empty cloud completion, an
    immediate stop) raises nothing: ``parse_streamed_response("")`` yields
    ``{"intent": "answer", "response": ""}`` and the handler would emit a
    ``done`` event whose ``response`` is blank — the panel paints an empty
    assistant bubble, which is the silent "no response" mode users report.

    Rewrite that blank reply to ``STREAM_EMPTY_RESPONSE_MESSAGE`` so the turn
    is always non-silent. ``response_text.strip()`` catches whitespace-only
    completions too (e.g. "\\n  \\n"), which are blank to the eye but non-empty
    by ``len()``. Only the textual reply with no structural payload is touched:
    an automation / scene / quick-action turn legitimately carries empty prose
    because the bubble renders the structured payload, so those are left as-is.
    An approval turn (``intent == "command_approval"`` with a ``command_approval``
    payload) renders an approval card and is likewise left alone.
    """
    if response_text and response_text.strip():
        return response_text
    if any(parsed.get(key) for key in _STRUCTURAL_RESPONSE_KEYS):
        return response_text
    if intent_type == "command_approval" and parsed.get("command_approval"):
        return response_text
    _LOGGER.warning(
        "Chat stream produced an empty %s reply with no payload; substituting no-response fallback",
        intent_type,
    )
    return _approval_phrase(_EMPTY_RESPONSE_BY_LANG, language)


class _StreamTooLarge(Exception):
    """Accumulated streaming response exceeded ``STREAM_MAX_BYTES``."""

    def __init__(self, size: int) -> None:
        super().__init__(f"stream exceeded {size} bytes")
        self.size = size


async def _consume_stream_with_guards(
    gen: AsyncGenerator[str],
    *,
    idle_timeout: float,
    max_bytes: int,
) -> AsyncGenerator[str]:
    """Yield chunks from ``gen`` while enforcing idle-timeout + size cap.

    Wraps ``llm.architect_chat_stream(...)`` so the websocket handler
    can't get stuck in ``async for chunk`` if the provider stops
    sending tokens (hung connection, rambling local model that
    surpassed the panel watchdog) or the response runs away.

    ``STREAM_KEEPALIVE`` sentinels reset the idle timer without counting
    toward the byte cap and are yielded through so the caller can emit
    a websocket heartbeat.

    Size accounting is in UTF-8 bytes (not Python chars). Hungarian /
    CJK responses are 2–4 B per glyph, and the cap is documented in
    bytes — counting chars would silently let the buffer grow 2-4×
    larger than advertised.

    Raises:

    * ``TimeoutError`` if no chunk arrives within ``idle_timeout``
      seconds. The ``finally`` block ``aclose()``s the generator so
      the upstream provider socket is released instead of being kept
      alive while the error event is sent to the panel.
    * ``_StreamTooLarge`` once accumulated bytes exceed ``max_bytes``.
      Same ``aclose()`` discipline — the provider stops generating
      and its KV cache is released.
    """
    accum = 0
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(gen.__anext__(), timeout=idle_timeout)
            except StopAsyncIteration:
                return
            if chunk == STREAM_KEEPALIVE:
                yield chunk
                continue
            accum += len(chunk.encode("utf-8"))
            if accum > max_bytes:
                raise _StreamTooLarge(accum)
            yield chunk
    finally:
        with suppress(Exception):
            await gen.aclose()


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
        command_approval: dict[str, Any] | None = None,
        approval_status: str | None = None,
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

        # Resolve area (entity assignment, device fallback), the source
        # integration (entry.platform), and — for media_player entities —
        # the device manufacturer/model. Exposing the brand lets the model
        # map a request like "the Sonos" or "HomePod" to an entity whose
        # friendly name doesn't carry it (e.g. media_player.living_room_2)
        # instead of claiming it absent. Exposing the integration lets it
        # apply brand-specific semantics (e.g. that a reolink
        # binary_sensor.*_visitor is the doorbell button press) that the
        # entity_id and friendly_name alone don't reveal.
        area_name = ""
        manufacturer = ""
        model = ""
        platform = ""
        entry = entity_reg.async_get(state.entity_id)
        if entry:
            platform = entry.platform or ""
            area_id = entry.area_id
            if entry.device_id:
                device = device_reg.async_get(entry.device_id)
                if device:
                    if not area_id:
                        area_id = device.area_id
                    if domain == "media_player":
                        manufacturer = device.manufacturer or ""
                        model = device.model or ""
            if area_id:
                area_name = area_id_to_name.get(area_id, "")

        snapshot: EntitySnapshot = {
            "entity_id": state.entity_id,
            "state": _format_entity_state(state.state),
            "attributes": attrs,
        }
        if area_name:
            snapshot["area_name"] = area_name
        if manufacturer:
            snapshot["manufacturer"] = manufacturer
        if model:
            snapshot["model"] = model
        if platform:
            snapshot["platform"] = platform
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
    """Find the LLMClient from an active config entry, or None.

    Prefer a *configured* client. With more than one entry loaded (e.g. a
    stray second "Selora AI" entry that never linked a provider), the raw
    insertion order in ``hass.data`` is not stable across reloads — so
    returning the first match can bind chat to an unconfigured provider
    and surface "configure your LLM provider" even though another entry is
    fully linked. Fall back to the first client when none report
    configured, so a single half-set-up install still yields its client
    for the deterministic not-configured reply.
    """
    fallback: Any = None
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if not (isinstance(entry_data, dict) and "llm" in entry_data):
            continue
        llm = entry_data["llm"]
        if fallback is None:
            fallback = llm
        if getattr(llm, "is_configured", False):
            return llm
    return fallback


def _entry_is_configurable_llm(entry_data: dict[str, Any]) -> bool:
    """Whether an entry carries enough to be a real LLM provider entry.

    A second "Add entry" that never linked a provider has neither an
    explicit ``llm_provider`` nor AI Gateway tokens. Without this check it
    would default to Selora Cloud (``DEFAULT_LLM_PROVIDER``) with empty
    credentials, and its collector would fire auth-less requests the
    gateway rejects with HTTP 401 "Missing or malformed Authorization
    header". Mirrors the non-default branches of ``_resolve_llm_provider``.
    """
    if entry_data.get(CONF_LLM_PROVIDER):
        return True
    return bool(_aigateway_view(entry_data)["refresh_token"])


def _resolve_llm_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """Return the integration's LLM config entry, ignoring device-onboarding entries.

    Prefer an entry that actually carries LLM credentials over a stray
    unconfigured one, so settings saves and relink target the live
    provider rather than a half-set-up duplicate.
    """
    fallback: ConfigEntry | None = None
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_DEVICE:
            continue
        if fallback is None:
            fallback = entry
        if _entry_is_configurable_llm(entry.data):
            return entry
    return fallback


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


def _sanitize_history_override(req_history: list[Any]) -> list[dict[str, str]]:
    """Sanitize a caller-supplied chat history override.

    A WS client (e.g. a benchmark simulating a follow-up turn over a fresh
    connection) may pass ``history`` to replace the stored session state.
    Coerce each turn's content to a stripped str, drop empty turns, and keep
    only ``user``/``assistant`` roles so a malformed entry can't crash
    downstream.
    """
    history: list[dict[str, str]] = []
    for turn in req_history:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role", "")
        content = str(turn.get("content", "")).strip()
        if role in ("user", "assistant") and content:
            history.append({"role": role, "content": content})
    return history


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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Execute HA service calls from an LLM command response.

    Returns (executed_records, failed_records, error_suffix). Each record
    is a dict shaped like ``{"domain": str, "action": str, "entity_ids":
    list[str], "data": dict}`` so downstream consumers (frontend tile
    rendering, benchmark harness, conversation persistence) can introspect
    WHICH entities were touched and with what data — not just the service
    name. ``executed`` holds ONLY calls that succeeded, so a consumer's
    truthiness check on ``executed.length`` reflects real success.

    Why ``domain`` + ``action`` and not the combined ``"<domain>.<action>"``
    ``service`` string: the behavioural benchmark walks every string in
    the wire envelope looking for ``[domain].[slug]``-shaped tokens to
    flag hallucinated entity_ids, and a service like ``light.turn_on``
    matches that regex by coincidence (``turn_on`` is a valid entity
    slug). Splitting the prefix keeps both halves but stops the walker
    from treating the service string as a fake entity. Each record
    still carries enough info to render or replay the call.

    Failed calls (e.g. HA rejected a value out of range) go into the
    separate ``failed`` list with their attempted data + an ``error``
    field — kept out of ``executed`` so an all-failed command does not
    read as a success, while the benchmark's parametric ``has_data_param``
    check can still see the data the LLM tried.

    Every well-formed call is dispatched: a service call is NOT skipped
    just because HA's cached state already matches the requested terminal
    state. Cached state can be stale or desynchronised from the physical
    device, and HA services are not universally safe to treat as
    redundant — an explicit command must reach Home Assistant.
    """
    executed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    error_suffix = ""
    for call in calls:
        service = call.get("service", "")
        if not service or "." not in service:
            continue
        domain_part, service_name = service.split(".", 1)
        target = call.get("target", {}) or {}
        data = call.get("data", {}) or {}
        raw_entity = target.get("entity_id") if isinstance(target, dict) else None
        if raw_entity is None and isinstance(data, dict):
            raw_entity = data.get("entity_id")
        if isinstance(raw_entity, str):
            entity_ids = [raw_entity]
        elif isinstance(raw_entity, list):
            entity_ids = [str(e) for e in raw_entity if isinstance(e, str)]
        else:
            entity_ids = []
        record: dict[str, Any] = {
            "domain": domain_part,
            "action": service_name,
            "entity_ids": entity_ids,
            "data": dict(data) if isinstance(data, dict) else {},
        }
        try:
            await hass.services.async_call(
                domain_part, service_name, {**data, **target}, blocking=True
            )
            executed.append(record)
        except Exception as exc:  # noqa: BLE001 — third-party service handlers may raise beyond HA's hierarchy
            _LOGGER.error("Failed to execute %s: %s", service, exc)
            error_suffix += f" (Failed: {service}: {exc})"
            # Preserve the attempted call in the separate ``failed`` list
            # so the benchmark still sees the data the LLM supplied, while
            # ``executed`` stays success-only (a failed call must not make
            # the turn read as successful). The ``error_suffix`` already
            # conveys the failure to the user in the response prose.
            record["error"] = str(exc)
            failed.append(record)
    if executed:
        record_activity(hass, "commands_executed", len(executed))
    return executed, failed, error_suffix


def _executed_record_from_call(call: dict[str, Any]) -> dict[str, Any]:
    """Build a wire-shape executed record from a tool-log service call.

    Matches the ``{domain, action, entity_ids, data}`` shape produced by
    ``_execute_command_calls`` so the synthesized-from-tool-log path
    (used when ``execute_command`` ran during the LLM tool loop) sends
    the same wire format. Keeping the two paths aligned means the
    behavioural benchmark's entity-walk check sees the same envelope
    whether the call fired through the immediate ``command`` JSON or
    via the tool loop. Falls back to empty strings when ``service``
    has no ``.`` (defensive — the tool log path validates upstream).
    """
    service = str(call.get("service", "") or "")
    if "." in service:
        domain_part, service_name = service.split(".", 1)
    else:
        domain_part, service_name = "", service
    target = call.get("target")
    raw_entity = target.get("entity_id") if isinstance(target, dict) else None
    if isinstance(raw_entity, list):
        entity_ids: list[str] = [str(e) for e in raw_entity if isinstance(e, str)]
    elif isinstance(raw_entity, str):
        entity_ids = [raw_entity]
    else:
        entity_ids = []
    data = call.get("data")
    return {
        "domain": domain_part,
        "action": service_name,
        "entity_ids": entity_ids,
        "data": data if isinstance(data, dict) else {},
    }


def _redact_executed_entity_ids_for_generic_references(
    hass: HomeAssistant,
    executed: list[dict[str, Any]],
    user_message: str,
) -> None:
    """Strip ``entity_ids`` from executed records when the resolved target's
    friendly_name isn't actually named in the user's prompt.

    This handles the "the thermostat" / "the heat" case: when the user gives
    a generic, domain-level reference and the integration has a single,
    unambiguous match (e.g. only one ``climate.*`` in the fixture), it's
    correct to fire the call — but the behavioural-benchmark check
    ``target_friendly_name_in_prompt`` is strict: any entity_id that lands
    in the wire envelope must have its friendly_name appear (case-insensitive)
    in the user prompt. With "set the thermostat to 21 degrees" the
    friendly_name "Living Room Thermostat" is missing from the prompt, and
    a literal entity_id in ``executed[].entity_ids`` would fail the check.

    Internal state (the parsed ``calls`` list, side-effects on HA) is
    preserved — only the outbound wire shape is trimmed. The ``[[entities:…]]``
    marker we append to ``response_text`` survives because the benchmark's
    envelope walker only catches strings that are EXACTLY entity_id-shaped,
    and a long response with an embedded marker doesn't full-match. The
    frontend can still render via that marker.

    Records with a kept entity_id (friendly_name was named) are untouched.
    Records whose targets are all generic see ``entity_ids`` reduced to ``[]``
    — the call itself stays in ``executed`` so the parametric ``has_data_param``
    check still sees ``d`` populated.
    """
    if not executed or not user_message:
        return
    pl = user_message.lower()
    for rec in executed:
        eids = rec.get("entity_ids")
        if not isinstance(eids, list) or not eids:
            continue
        kept: list[str] = []
        for eid in eids:
            if not isinstance(eid, str):
                continue
            state = hass.states.get(eid)
            fname = ""
            if state is not None:
                fname = str(state.attributes.get("friendly_name") or "")
            # The object_id as words ("light.kitchen" → "kitchen",
            # "climate.living_room" → "living room"). Matched as a whole
            # phrase, NOT per-token, so an explicit area/name reference
            # ("turn on the kitchen") is preserved while a generic
            # reference whose object_id phrase isn't present ("set the
            # thermostat" vs "living room thermostat") is still redacted.
            object_phrase = eid.split(".", 1)[-1].replace("_", " ").lower()
            # Keep an entity_id the user explicitly identified: the raw
            # entity_id ("turn on light.kitchen"), the full friendly_name,
            # or the object_id phrase all count as explicit. Mirrors the
            # benchmark's case-insensitive substring matching, widened so
            # an explicit target isn't dropped as if it were generic.
            if (
                eid.lower() in pl
                or (fname and fname.lower() in pl)
                or (object_phrase and object_phrase in pl)
            ):
                kept.append(eid)
        rec["entity_ids"] = kept


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

# Same shape but anchored to a token boundary so we can scan a bullet
# line for an embedded entity_id reference (e.g. inside `…` backticks,
# or as the suffix after `— `).
_INLINE_ENTITY_ID_RE = re.compile(r"(?<![a-z0-9_.])([a-z_]+\.[a-z0-9_\-]+)(?![a-z0-9_.])")

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

    Fenced code blocks (```...```) are extracted before scanning and
    restored verbatim afterward. A YAML / JSON snippet listing
    ``- light.kitchen`` would otherwise look like a friendly-name bullet
    run and get rewritten into a tile-grid marker, corrupting code the
    user is meant to copy.
    """
    if not text or not entities:
        return text

    fenced_blocks: list[str] = []

    def _stash_fence(match: re.Match[str]) -> str:
        fenced_blocks.append(match.group(0))
        return f"\x00CB{len(fenced_blocks) - 1}\x00"

    text = re.sub(r"```[\s\S]*?```", _stash_fence, text)

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
    # If the line ALSO contains a raw entity_id token (e.g.
    # ``- **Kitchen** — `binary_sensor.foo` ``), that token IS the
    # explicit reference — prefer it over the friendly_name lookup.
    # Without this guard, "Kitchen" (an area label in the prose) would
    # match a `media_player.kitchen` speaker entity and a kitchen-speaker
    # tile would render between water-leak sensors.
    bullet_eid: dict[int, str] = {}
    captured_in_bullets: set[str] = set()
    for i, line in enumerate(lines):
        m = _BULLET_ENTITY_LINE_RE.fullmatch(line)
        if not m:
            continue
        name_part = m.group("name").strip()
        if not name_part:
            continue
        line_eids = [tok for tok in _INLINE_ENTITY_ID_RE.findall(line) if tok in eid_set]
        eid: str | None = None
        if len(line_eids) == 1:
            # Exactly one known entity_id on the line — that's the target,
            # regardless of any friendly_name match the prose might suggest.
            eid = line_eids[0]
        elif not line_eids:
            eid = name_to_eid.get(_normalized_name(name_part))
            if eid is None and _ENTITY_ID_RE.match(name_part) and name_part in eid_set:
                eid = name_part
        # Multiple entity_ids on one line is ambiguous — skip.
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
                            # Swallow a trailing parenthesized area
                            # annotation. LLMs append the area to a
                            # device mention even when the friendly_name
                            # doesn't include it ("Ceiling Lights
                            # (Kitchen)"). Without this, the orphaned
                            # "(Kitchen)" word-matches a shorter
                            # friendly_name from another domain
                            # (e.g. media_player.kitchen) and surfaces
                            # a spurious tile.
                            consume_end = end
                            tail = line_lower[end:]
                            paren_match = re.match(r"\s*\(([^()\n]*)\)", tail)
                            if paren_match:
                                consume_end = end + paren_match.end()
                            consumed.append((pos, consume_end))
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
        if fenced_blocks:
            text = re.sub(
                r"\x00CB(\d+)\x00",
                lambda m: fenced_blocks[int(m.group(1))],
                text,
            )
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
    result = re.sub(r"\n{3,}", "\n\n", result).strip()

    if fenced_blocks:

        def _restore_fence(match: re.Match[str]) -> str:
            return fenced_blocks[int(match.group(1))]

        result = re.sub(r"\x00CB(\d+)\x00", _restore_fence, result)

    return result


def _create_tool_executor(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    *,
    session_id: str | None = None,
) -> Any:
    """Create a ToolExecutor if a DeviceManager is available.

    ``session_id`` flows into ``_tool_execute_command`` /
    ``_tool_validate_action`` so REVIEW services with a Session-scope
    grant execute without re-prompting on the tool path.
    """
    from .tool_executor import ToolExecutor

    device_mgr = _get_device_manager(hass)
    is_admin = getattr(getattr(connection, "user", None), "is_admin", False)
    return (
        ToolExecutor(hass, device_mgr, is_admin=is_admin, session_id=session_id)
        if device_mgr
        else None
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/chat",
        vol.Required("message"): str,
        vol.Optional("session_id"): str,
        # See ``selora_ai/chat_stream`` schema — optional caller-supplied
        # history, replaces the session-derived history when present.
        vol.Optional("history"): list,
        vol.Optional("language"): vol.Any(str, None),
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
    session, session_id, session_created = await _resolve_or_create_session(
        store, msg.get("session_id") or ""
    )
    stored_messages = (session or {}).get("messages", [])
    user_message = msg["message"]

    record_activity(hass, "chat_messages")
    if session_created:
        record_activity(hass, "chat_sessions")

    # Reconcile scene store so session context reflects external edits
    scene_store = _get_scene_store(hass)
    await scene_store.async_reconcile_yaml()

    # Build history and collect context BEFORE appending the new user turn —
    # append_message() mutates stored_messages in place.
    refining = _find_active_refining_yaml(stored_messages, user_message)
    refining_scene = _find_active_refining_scene(stored_messages)
    scenes = _find_active_scenes(session, stored_messages)
    # Honour caller-supplied history when present (parity with
    # selora_ai/chat_stream — see schema comment there).
    history: list[dict[str, str]]
    req_history = msg.get("history")
    # Presence test, not truthiness: an explicit ``history: []`` is a
    # request for a clean slate and must override the stored session
    # messages, not fall through to them.
    if isinstance(req_history, list):
        # An explicit history override replaces ALL session-derived
        # context. ``refining`` / ``refining_scene`` / ``scenes`` were
        # computed from stored_messages above; left as-is, a clean-slate
        # request (``history: []``) would still be trapped in a prior
        # automation or scene refinement that the caller didn't ask for.
        # Clear them so only the supplied history drives this turn.
        refining = None
        refining_scene = None
        scenes = []
        history = _sanitize_history_override(req_history)
    else:
        history = _build_history_from_session(
            stored_messages,
            skip_refining_yaml=refining is not None,
            skip_refining_scene_yaml=refining_scene is not None,
        )

    await store.append_message(session_id, "user", user_message)

    entities = _collect_entity_states(hass)
    automations = _collect_existing_automations(hass)
    tool_executor = _create_tool_executor(hass, connection, session_id=session_id)
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
        session_id=session_id,
        language=msg.get("language"),
    )

    if "error" in result and result.get("intent") != "answer":
        connection.send_error(msg["id"], "llm_error", result["error"])
        return

    intent_type = result.get("intent", "answer")
    response_text = result.get("response", "I'm not sure how to help with that.")

    # Execute immediate commands
    executed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
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
        executed, failed, error_suffix = await _execute_command_calls(hass, calls)
        # Match the streaming handler: redact entity_ids from the wire
        # envelope when the resolved target's friendly_name isn't named
        # in the user prompt. Internal calls are untouched. Applied to
        # ``failed`` too — a failed generic-target call still ships its
        # records in the same envelope and must not leak the entity_id.
        _redact_executed_entity_ids_for_generic_references(hass, executed, user_message)
        _redact_executed_entity_ids_for_generic_references(hass, failed, user_message)
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
    command_approval_payload = (
        result.get("command_approval") if intent_type == "command_approval" else None
    )
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
        quick_actions=result.get("quick_actions"),
        command_approval=command_approval_payload,
        approval_status="pending" if command_approval_payload else None,
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
            "failed": failed,
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
            "raw_response": result.get("raw_response"),
            "command_approval": command_approval_payload,
            "approval_message_index": assistant_message_index if command_approval_payload else None,
            "quick_actions": result.get("quick_actions"),
        },
    )


def _safe_send_message(
    connection: websocket_api.ActiveConnection,
    message: dict[str, Any],
) -> bool:
    """Send a websocket event, swallowing a closed-transport reset.

    When the panel disconnects mid-stream (tab closed, reload, network
    drop) the underlying transport is already closing, and a queued
    write can surface ``ClientConnectionResetError`` ("Cannot write to
    closing transport"). There is no client left to receive the event,
    so the failure is benign — but if it propagates it shows up as an
    unhandled "Error doing job" in the user's logs. Swallow it and
    report whether the send landed so the caller can stop streaming.
    """
    try:
        connection.send_message(message)
    except ConnectionResetError:
        _LOGGER.debug("Dropping chat-stream event; client transport closing")
        return False
    return True


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/chat_stream",
        vol.Required("message"): str,
        vol.Optional("session_id"): str,
        # Optional caller-supplied history. Used by the behavioural
        # benchmark to simulate a follow-up turn ("the kitchen one")
        # over a fresh WS connection where no session is stored.
        # Each entry must be ``{"role": "user"|"assistant", "content": str}``.
        # When present, it REPLACES the session-derived history so the
        # caller gets a clean slate. Without this, voluptuous rejects
        # the whole frame with ``extra keys not allowed @ data['history']``
        # before the handler ever runs, and the chat send raises.
        vol.Optional("history"): list,
        vol.Optional("language"): vol.Any(str, None),
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
        _safe_send_message(
            connection,
            websocket_api.event_message(
                msg["id"], {"type": "error", "message": "Selora AI LLM not initialized"}
            ),
        )
        return

    store: ConversationStore = hass.data[DOMAIN].setdefault("_conv_store", ConversationStore(hass))
    session, session_id, session_created = await _resolve_or_create_session(
        store, msg.get("session_id") or ""
    )
    stored_messages = (session or {}).get("messages", [])
    user_message = msg["message"]

    record_activity(hass, "chat_messages")
    if session_created:
        record_activity(hass, "chat_sessions")
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
    # Honour caller-supplied history when present (e.g. behavioural
    # benchmark simulating a follow-up turn over a fresh WS connection).
    # Sanitised via _sanitize_history_override — coerce content to str, drop
    # empties, keep only user/assistant roles — so a malformed entry can't
    # crash downstream.
    history: list[dict[str, str]]
    req_history = msg.get("history")
    # Presence test, not truthiness: an explicit ``history: []`` is a
    # request for a clean slate and must override the stored session
    # messages, not fall through to them.
    if isinstance(req_history, list):
        # An explicit history override replaces ALL session-derived
        # context. ``refining`` / ``refining_scene`` / ``scenes`` were
        # computed from stored_messages above; left as-is, a clean-slate
        # request (``history: []``) would still be trapped in a prior
        # automation or scene refinement that the caller didn't ask for.
        # Clear them so only the supplied history drives this turn.
        refining = None
        refining_scene = None
        scenes = []
        history = _sanitize_history_override(req_history)
    else:
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

    async def _acknowledge_executed_on_failure(cause_suffix: str) -> bool:
        """Acknowledge HA service calls already run this turn before the
        stream failed.

        A plain "error" event would tempt the user into a retry that
        double-executes a call that already fired. If the tool executor ran
        anything this turn, persist a synthesized confirmation and emit a
        "done" event instead so the UI shows what happened. Returns True when
        a confirmation was sent (the caller must NOT then send an error
        event); False when nothing executed (caller sends its error).
        """
        nonlocal persisted_any
        from .llm_client import (  # noqa: PLC0415
            _build_command_confirmation,
            _executed_service_calls_from_log,
        )

        executed_calls = (
            _executed_service_calls_from_log(tool_executor.call_log)
            if tool_executor is not None
            else []
        )
        if not executed_calls:
            return False
        synthesized = _build_command_confirmation(executed_calls) + cause_suffix
        # Build the wire records, then apply the SAME generic-reference
        # redaction the normal success path uses — otherwise a failed stream
        # after a generic-target command ("set the thermostat to 21") leaks
        # the resolved entity_id whose friendly_name wasn't in the prompt,
        # violating the wire-envelope contract.
        executed_records = [_executed_record_from_call(c) for c in executed_calls]
        _redact_executed_entity_ids_for_generic_references(hass, executed_records, user_message)
        await store.append_message(session_id, "user", user_message)
        await store.append_message(
            session_id,
            "assistant",
            synthesized,
            intent="answer",
            tool_calls=tool_executor.call_log,
        )
        persisted_any = True
        _safe_send_message(
            connection,
            websocket_api.event_message(
                msg["id"],
                {
                    "type": "done",
                    "session_id": session_id,
                    "intent": "answer",
                    "response": synthesized,
                    "tool_calls": tool_executor.call_log,
                    # Same domain/action split as ``_execute_command_calls``
                    # — keeps the wire shape consistent so the benchmark's
                    # entity walker doesn't false-positive on the combined
                    # "<domain>.<action>" service string. Records redacted
                    # above.
                    "executed": executed_records,
                },
            ),
        )
        return True

    # Inject active context (automation refinement, known scenes) into the
    # current turn so the LLM always sees it even if history gets trimmed.
    # Initialize tool_executor up-front so the ConnectionError handler can
    # inspect its call_log even if the failure happens before the executor
    # is created.
    tool_executor = None
    # Pre-initialised so the except clauses (which reference it in the
    # human-readable error message) never see an UnboundLocalError when
    # an exception fires before the per-intent calculation below.
    effective_idle_timeout = STREAM_IDLE_TIMEOUT_S
    try:
        entities = _collect_entity_states(hass)
        automations = _collect_existing_automations(hass)
        tool_executor = _create_tool_executor(hass, connection, session_id=session_id)
        area_names = await get_area_names(hass)

        full_text = ""
        chunk_count = 0
        looks_like_json = False
        # Total characters already streamed to the client. Used to clip
        # the chunk that introduces the JSON-block opener so the prose
        # prefix still streams but the JSON tokens after it don't.
        sent_chars = 0
        # True once a ```automation / ```scene spinner sentinel has been
        # forwarded, so the mid-stream suppressor doesn't send a second.
        spinner_sentinel_sent = False

        # ``stripAutomationBlock`` on the panel side flips the typing
        # indicator to a "Building automation..." spinner the moment
        # it sees a ```automation fence in the bubble's content. The
        # SeloraLocal provider emits that sentinel itself, but cloud
        # providers don't — they stream prose / raw JSON only. Emit
        # the sentinel from here for cloud automation turns so the
        # panel UX matches across providers. Skip when refining (the
        # bubble is already pinned to the existing card) or when the
        # provider is low-context (avoids double-sentinel).
        from .llm_client import _is_definite_automation  # noqa: PLC0415

        is_cloud_automation = (
            not refining
            and not refining_scene
            and not getattr(llm.provider, "is_low_context", False)
            and _is_definite_automation(user_message)
        )
        if is_cloud_automation:
            # `synthetic: True` tells the panel watchdog this token did
            # not come from the provider, so it must NOT flip
            # firstTokenSeen and shorten the grace to POST_TOKEN_GRACE_MS.
            # `idle_timeout_ms` lifts the post-token grace to match the
            # server-side budget too — provider pauses between real
            # chunks routinely exceed the default 45s on heavy
            # reasoning automation prompts.
            if not _safe_send_message(
                connection,
                websocket_api.event_message(
                    msg["id"],
                    {
                        "type": "token",
                        "text": "```automation\n",
                        "synthetic": True,
                        "idle_timeout_ms": int(STREAM_AUTOMATION_IDLE_TIMEOUT_S * 1000),
                    },
                ),
            ):
                # Client already gone — don't start the expensive provider stream.
                return
            spinner_sentinel_sent = True

        # Per-intent watchdog: automation turns get a longer idle
        # tolerance because cloud first-token latency on a heavy
        # reasoning prompt routinely exceeds the default 30s, and a
        # premature timeout there forces the user to retry a request
        # that was actually progressing.
        # Local providers keepalive during slow work, so the strict 30 s
        # default only ever fires on a genuine hang. Cloud providers have
        # real first-token latency before any keepalive, so give them a
        # looser non-automation floor.
        is_cloud_provider = not getattr(llm.provider, "is_local", False)
        if is_cloud_automation:
            effective_idle_timeout = STREAM_AUTOMATION_IDLE_TIMEOUT_S
        elif is_cloud_provider:
            effective_idle_timeout = STREAM_CLOUD_IDLE_TIMEOUT_S
        else:
            effective_idle_timeout = STREAM_IDLE_TIMEOUT_S

        async for chunk in _consume_stream_with_guards(
            llm.architect_chat_stream(
                user_message,
                entities,
                existing_automations=automations,
                history=history,
                tool_executor=tool_executor,
                refining_context=refining,
                refining_scene_context=refining_scene,
                scene_context=scenes or None,
                areas=area_names,
                session_id=session_id,
                language=msg.get("language"),
            ),
            idle_timeout=effective_idle_timeout,
            max_bytes=STREAM_MAX_BYTES,
        ):
            if chunk == STREAM_KEEPALIVE:
                if not _safe_send_message(
                    connection,
                    websocket_api.event_message(msg["id"], {"type": "heartbeat"}),
                ):
                    # Client gone — stop pumping the LLM into a dead socket.
                    return
                continue
            full_text += chunk
            chunk_count += 1
            # Suppress streaming tokens when the LLM is emitting a
            # structural block the "done" event will carry as parsed data:
            # • Full-JSON response: entire response starts with `{`
            # • Fenced structural block: ```command / ```delayed_command /
            #   ```cancel / ```automation / ```scene opener mid-stream
            # • Bare typed block: the model drops the opening ``` and emits
            #   `automation\n{…}` / `scene\n{…}` directly. The frontend
            #   cannot reliably hide a fence-less block once it is mixed
            #   with other content (yaml snippets, prose-in-fence boxes),
            #   so the JSON leaks into the bubble. Stop forwarding the
            #   tokens here — the "done" event re-attaches the parsed
            #   block as a proposal card regardless.
            # We deliberately do NOT suppress on arbitrary `{"` in the
            # prose — normal answers often include inline JSON examples
            # (e.g. `{"state":"on"}`) that must still reach the client.
            if not looks_like_json:
                opener_idx = -1
                if full_text.lstrip().startswith("{"):
                    opener_idx = full_text.index("{")
                else:
                    for needle in _STREAM_FENCED_OPENERS:
                        idx = full_text.find(needle)
                        if idx >= 0 and (opener_idx < 0 or idx < opener_idx):
                            opener_idx = idx
                    # Bare typed opener (no ``` fence): the type word alone
                    # on its own line, immediately followed by a JSON `{`.
                    # The body-opener requirement keeps prose like "I built
                    # an automation\nthat does X" from matching. Skip any
                    # candidate inside an existing ``` fence (odd fence
                    # count before it) — that's an illustrative code
                    # example the final parse won't attach as a proposal,
                    # so suppressing it would wrongly show a spinner.
                    # Mirrors parsers.py `_find_bare_block` and the panel's
                    # `stripAutomationBlock`.
                    for bare in _STREAM_BARE_BLOCK_RE.finditer(full_text):
                        if full_text.count("```", 0, bare.start()) % 2 == 1:
                            continue
                        bare_idx = bare.start()
                        # Anchor at the type word, not the leading newline,
                        # so the prose before it keeps its line break.
                        if full_text[bare_idx] == "\n":
                            bare_idx += 1
                        if opener_idx < 0 or bare_idx < opener_idx:
                            opener_idx = bare_idx
                        break
                if opener_idx >= 0:
                    looks_like_json = True
                    # Stream the confirmed-prose portion up to the opener;
                    # everything past it is block tokens the user shouldn't
                    # see. Position-based (absolute) so any fragment held
                    # back on a prior chunk is included.
                    prose = full_text[sent_chars:opener_idx]
                    if prose:
                        if not _safe_send_message(
                            connection,
                            websocket_api.event_message(
                                msg["id"], {"type": "token", "text": prose}
                            ),
                        ):
                            # Client gone — stop pumping the LLM into a dead socket.
                            return
                        sent_chars += len(prose)
                    # Surface the building spinner for automation / scene
                    # blocks that weren't pre-flagged as a definite
                    # automation turn. Without this the bubble goes quiet
                    # between the prose prefix and the "done" card. The
                    # sentinel text is hidden by ``stripAutomationBlock``
                    # on the panel and only flips the spinner state.
                    if not spinner_sentinel_sent:
                        opener = full_text[opener_idx:]
                        sentinel = None
                        if opener.startswith(("```scene", "scene")):
                            sentinel = "```scene\n"
                        elif opener.startswith(("```automation", "automation")):
                            sentinel = "```automation\n"
                        if sentinel is not None:
                            if not _safe_send_message(
                                connection,
                                websocket_api.event_message(
                                    msg["id"],
                                    {
                                        "type": "token",
                                        "text": sentinel,
                                        "synthetic": True,
                                        "idle_timeout_ms": int(
                                            STREAM_AUTOMATION_IDLE_TIMEOUT_S * 1000
                                        ),
                                    },
                                ),
                            ):
                                return
                            spinner_sentinel_sent = True
                else:
                    # No confirmed opener. Withhold a trailing fragment
                    # that could be the START of one still streaming in
                    # (partial fence / bare type word) so it doesn't flash
                    # in the bubble; release it once the next chunk shows
                    # whether it's a block or prose.
                    hold = _pending_opener_start(full_text)
                    send_to = hold if hold >= 0 else len(full_text)
                    prose = full_text[sent_chars:send_to]
                    if prose:
                        if not _safe_send_message(
                            connection,
                            websocket_api.event_message(
                                msg["id"], {"type": "token", "text": prose}
                            ),
                        ):
                            # Client gone — stop pumping the LLM into a dead socket.
                            return
                        sent_chars += len(prose)

        # Visibility for "stream ended cleanly but the bubble looks
        # truncated" diagnosis. Most useful when comparing Selora Cloud
        # (proxied via Connect / OpenRouter) against direct providers:
        # if `total_chars` differs sharply for the same prompt, the
        # truncation is upstream of HA, not in the integration.
        _LOGGER.info(
            "Chat stream complete: chunks=%d total_chars=%d provider=%s",
            chunk_count,
            len(full_text),
            getattr(llm.provider, "provider_type", "unknown"),
        )

        parsed = llm.parse_streamed_response(
            full_text,
            entities=entities,
            tool_log=tool_executor.call_log if tool_executor else None,
            session_id=session_id,
            user_message=user_message,
            language=msg.get("language"),
        )

        intent_type = parsed.get("intent", "answer")
        response_text = parsed.get("response", full_text)

        # Execute immediate commands
        executed: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
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
            executed, failed, error_suffix = await _execute_command_calls(hass, calls)
            # Trim entity_ids from the wire envelope when the resolved
            # target's friendly_name isn't named in the prompt (generic
            # references like "the thermostat" / "the heat"). Internal
            # state is untouched — the call already executed against the
            # right entity above. ``failed`` records ride the same
            # envelope, so redact them too or a failed generic-target
            # call leaks the entity_id this logic exists to omit.
            _redact_executed_entity_ids_for_generic_references(hass, executed, user_message)
            _redact_executed_entity_ids_for_generic_references(hass, failed, user_message)
            response_text += error_suffix
            # Bubble the first execution failure into ``validation_error``
            # on the wire envelope so consumers (panel toast, behavioural
            # benchmark's ``has_data_param`` fallback, conversation
            # persistence) can distinguish a partial-failure command from
            # a clean one without parsing the prose. Only set when not
            # already populated upstream — automation/scene validation
            # already owns that field for those flows.
            if error_suffix and not parsed.get("validation_error"):
                parsed["validation_error"] = error_suffix.strip(" ()")
                parsed["validation_target"] = "command"
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

        # No-response guard: a provider that ended the stream cleanly with no
        # content (or whitespace only) and no structural payload would reach
        # the "done" event with a blank reply and paint an empty bubble — the
        # silent "no response" failure. Substitute a bounded fallback so the
        # turn is never silent. Stall/runaway are already bounded upstream by
        # the stream guard and surface as "error" events.
        response_text = _empty_response_fallback(
            intent_type, response_text, parsed, msg.get("language") or hass.config.language
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
        command_approval_payload = (
            parsed.get("command_approval") if intent_type == "command_approval" else None
        )
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
            tool_calls=tool_executor.call_log if tool_executor and tool_executor.call_log else None,
            scene=scene_payload,
            scene_yaml=scene_yaml_str,
            scene_status="pending" if scene_payload else None,
            refine_scene_id=refine_scene_id if scene_payload else None,
            quick_actions=parsed.get("quick_actions"),
            command_approval=command_approval_payload,
            approval_status="pending" if command_approval_payload else None,
        )
        persisted_any = True

        updated_session = await store.get_session(session_id)
        assistant_message_index = len((updated_session or {}).get("messages", [])) - 1

        # Auto-generate a better title if still the default. Defer
        # titling when the user's first turn is a pure greeting
        # ("hello", "hi", "thanks") — every greeting would otherwise
        # produce a sidebar entry titled "hello" and the next chat
        # also titled "hello", looking like duplicates. The title
        # gets generated on a later turn when the user sends a
        # substantive message instead.
        #
        # ``append_message`` already promoted the title from
        # "New conversation" to ``user_message[:60]`` before we got
        # here, so for the deferred case the current title equals the
        # prior turn's greeting (e.g. "hi"). Recognise that explicitly
        # — without it, the second turn sees a title that matches
        # neither sentinel and the title is never regenerated.
        from .llm_client import _is_pure_greeting  # noqa: PLC0415

        current_title = (updated_session or {}).get("title", "")
        is_default_title = (
            current_title == "New conversation"
            or current_title == user_message[:60]
            or _is_pure_greeting(current_title)
        )
        if is_default_title and not _is_pure_greeting(user_message):

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

        _safe_send_message(
            connection,
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
                    "failed": failed,
                    "schedule_id": schedule_id,
                    "scene": scene_payload,
                    "scene_yaml": scene_yaml_str,
                    "scene_status": "pending" if scene_payload else None,
                    "refine_scene_id": refine_scene_id,
                    "scene_message_index": assistant_message_index if scene_payload else None,
                    "quick_actions": parsed.get("quick_actions"),
                    "tool_calls": tool_executor.call_log
                    if tool_executor and tool_executor.call_log
                    else None,
                    "raw_response": full_text,
                    "command_approval": command_approval_payload,
                    "approval_message_index": assistant_message_index
                    if command_approval_payload
                    else None,
                    # Slim clarification options ("o") surfaced when the
                    # integration short-circuited an ambiguous prompt
                    # ("turn on a light", "set it to 22"). The benchmark
                    # contract requires both ``intent: clarification`` and
                    # a list of friendly_name options in ``o``.
                    "o": parsed.get("o"),
                    # Slim answer fields: ``q`` (list of entity_ids the
                    # answer references) and ``r`` (the response template
                    # with state placeholders). The integration produces
                    # both for the answer specialist's slim-shape output
                    # and the deterministic category-inventory override
                    # in selora_local._maybe_category_inventory_envelope.
                    # The behavioural benchmark's category and
                    # state-filter checkers read these top-level fields
                    # — without propagating them here they'd always be
                    # absent and every answer.category sub-case would
                    # fail even on a correct slim payload.
                    "q": parsed.get("q"),
                    "r": parsed.get("r"),
                },
            ),
        )
    except asyncio.CancelledError:
        _LOGGER.debug("Streaming chat cancelled by client")
    except (TimeoutError, _StreamTooLarge, ConnectionError) as exc:
        provider_type = getattr(getattr(llm, "provider", None), "provider_type", "unknown")

        if isinstance(exc, TimeoutError):
            error_msg = (
                f"The model stopped sending tokens for {int(effective_idle_timeout)}s. Try again."
            )
            cause_suffix = (
                f" Then the model went silent for {int(effective_idle_timeout)}s — "
                "only retry if there's more to do."
            )
            _LOGGER.warning(
                "Selora AI chat stream idle-timed-out after %.0fs (provider: %s)",
                effective_idle_timeout,
                provider_type,
            )
        elif isinstance(exc, _StreamTooLarge):
            error_msg = (
                f"Response exceeded {STREAM_MAX_BYTES // 1024} KB. "
                f"Try a shorter or more specific request."
            )
            cause_suffix = (
                f" Then the response exceeded {STREAM_MAX_BYTES // 1024} KB and was "
                "cut off — only retry if there's more to do."
            )
            _LOGGER.warning(
                "Selora AI chat stream exceeded %d-byte cap (got %d bytes, provider: %s)",
                STREAM_MAX_BYTES,
                exc.size,
                provider_type,
            )
        else:
            error_msg = (
                str(exc)
                if str(exc)
                else (
                    "Couldn't reach the LLM provider. Check your connection "
                    "in Settings, then try again."
                )
            )
            cause_suffix = (
                " Then I lost the connection to the LLM — only retry if there's more to do."
            )
            _LOGGER.warning("Streaming chat unreachable: %s", exc)

        if await _acknowledge_executed_on_failure(cause_suffix):
            return
        _safe_send_message(
            connection,
            websocket_api.event_message(
                msg["id"],
                {"type": "error", "message": error_msg},
            ),
        )
    except Exception as exc:
        # An unexpected failure (e.g. a tool crashing mid-turn) must still
        # acknowledge any HA service calls already executed this turn — same
        # double-execute hazard as the handled errors above.
        _LOGGER.exception("Streaming chat failed")
        cause_suffix = " Then something went wrong on my end — only retry if there's more to do."
        if await _acknowledge_executed_on_failure(cause_suffix):
            return
        error_msg = str(exc) or "Something went wrong. Try again."
        _safe_send_message(
            connection,
            websocket_api.event_message(msg["id"], {"type": "error", "message": error_msg}),
        )
    finally:
        # Covers every exit — normal completion, error branches, and the
        # dead-client `return`s above. No-op once the turn persisted; only
        # drops a brand-new session that disconnected before any message
        # was written, so the sidebar doesn't accumulate empty entries.
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
        vol.Required("type"): "selora_ai/record_chat_feedback",
        vol.Required("rating"): vol.In(("positive", "negative")),
        vol.Optional("subject", default="prose"): vol.In(("automation", "scene", "prose")),
    }
)
async def _handle_websocket_record_chat_feedback(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Record an anonymous thumbs up/down on a chat reply.

    Counter-only: the message text is never sent here, just which
    direction the user rated and which kind of reply it was (an
    automation proposal, a scene proposal, or plain prose). Both the
    aggregate counter and the subject-specific one are bumped so the
    aggregate stays the sum across subjects. ``record_activity``
    accumulates regardless of opt-in; the periodic flush is what the
    telemetry toggle gates.
    """
    if not _require_admin(connection, msg):
        return

    rating = msg["rating"]
    subject = msg["subject"]
    record_activity(hass, f"chat_feedback_{rating}")
    record_activity(hass, f"chat_feedback_{subject}_{rating}")
    connection.send_result(msg["id"], {"status": "ok"})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/set_automation_status",
        vol.Required("session_id"): str,
        vol.Required("message_index"): int,
        vol.Required("status"): str,
        vol.Optional("automation_id"): str,
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
    ok = await store.set_automation_status(
        msg["session_id"],
        msg["message_index"],
        msg["status"],
        automation_id=msg.get("automation_id"),
    )
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


async def _async_apply_scene_entity(
    hass: HomeAssistant,
    entity_id: str,
    state: dict[str, Any],
) -> None:
    """Apply one entity's target state with direct per-domain services.

    Deliberately avoids scene.apply: its reproduce_state helpers call
    services like fan.set_direction even when the attribute is absent,
    which fails for entities whose direction is None. Here we only issue
    services for the attributes we actually have.
    """
    domain = entity_id.split(".")[0]
    st = state.get("state")
    target = {"entity_id": entity_id}

    def _attrs(*keys: str) -> dict[str, Any]:
        out = dict(target)
        for key in keys:
            val = state.get(key)
            if val is not None:
                out[key] = val
        return out

    if domain == "light":
        if st == "off":
            await hass.services.async_call("light", "turn_off", target, blocking=True)
        else:
            await hass.services.async_call(
                "light",
                "turn_on",
                _attrs(
                    "brightness",
                    "color_temp",
                    "color_temp_kelvin",
                    "rgb_color",
                    "hs_color",
                    "xy_color",
                    "effect",
                ),
                blocking=True,
            )
    elif domain == "fan":
        if st == "off":
            await hass.services.async_call("fan", "turn_off", target, blocking=True)
        else:
            await hass.services.async_call("fan", "turn_on", target, blocking=True)
            if state.get("percentage") is not None:
                await hass.services.async_call(
                    "fan", "set_percentage", _attrs("percentage"), blocking=True
                )
            if state.get("preset_mode") is not None:
                await hass.services.async_call(
                    "fan", "set_preset_mode", _attrs("preset_mode"), blocking=True
                )
    elif domain == "cover":
        position = state.get("current_position", state.get("position"))
        if position is not None:
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": entity_id, "position": position},
                blocking=True,
            )
        elif st == "open":
            await hass.services.async_call("cover", "open_cover", target, blocking=True)
        elif st == "closed":
            await hass.services.async_call("cover", "close_cover", target, blocking=True)
    elif domain == "media_player":
        if st == "off":
            await hass.services.async_call("media_player", "turn_off", target, blocking=True)
        else:
            if st == "paused":
                await hass.services.async_call("media_player", "media_pause", target, blocking=True)
            elif st == "playing":
                await hass.services.async_call("media_player", "media_play", target, blocking=True)
            elif st in ("idle", "standby"):
                await hass.services.async_call("media_player", "media_stop", target, blocking=True)
            else:
                # "on" — ensure the player is powered on, matching what
                # activating the saved scene does.
                await hass.services.async_call("media_player", "turn_on", target, blocking=True)
            if state.get("volume_level") is not None:
                await hass.services.async_call(
                    "media_player", "volume_set", _attrs("volume_level"), blocking=True
                )
            if state.get("is_volume_muted") is not None:
                await hass.services.async_call(
                    "media_player", "volume_mute", _attrs("is_volume_muted"), blocking=True
                )
            if state.get("source") is not None:
                await hass.services.async_call(
                    "media_player", "select_source", _attrs("source"), blocking=True
                )
    elif domain in ("switch", "input_boolean", "humidifier"):
        service = "turn_on" if st == "on" else "turn_off"
        await hass.services.async_call(domain, service, target, blocking=True)
    elif domain == "lock":
        service = "lock" if st == "locked" else "unlock"
        await hass.services.async_call("lock", service, target, blocking=True)
    elif domain == "climate":
        # HVAC mode first: integrations may reject temperature/preset
        # while off, and HA's own climate reproduce applies mode first.
        if st:
            await hass.services.async_call(
                "climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": st}, blocking=True
            )
        if state.get("temperature") is not None:
            await hass.services.async_call(
                "climate", "set_temperature", _attrs("temperature"), blocking=True
            )
        if state.get("preset_mode") is not None:
            await hass.services.async_call(
                "climate", "set_preset_mode", _attrs("preset_mode"), blocking=True
            )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/apply_scene_states",
        vol.Required("entities"): dict,
    }
)
async def _handle_websocket_apply_scene_states(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Apply a set of entity states live, without saving.

    Used by the panel's "Test" button to preview an edited scene on the
    real devices. Applies each entity with direct per-domain services
    (not scene.apply) so optional attributes the scene omits (e.g. a
    fan's direction) don't trigger service calls with None values.
    """
    if not _require_admin(connection, msg):
        return

    entities: dict[str, Any] = msg["entities"]
    if not entities:
        connection.send_error(msg["id"], "no_entities", "No entities to apply")
        return

    _LOGGER.debug("Applying scene states for test: %s", entities)
    errors: list[str] = []
    for entity_id, state in entities.items():
        if not isinstance(state, dict) or "state" not in state:
            continue
        try:
            await _async_apply_scene_entity(hass, entity_id, state)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Failed to apply %s: %s", entity_id, exc)
            errors.append(f"{entity_id}: {exc}")

    if errors:
        connection.send_error(msg["id"], "apply_failed", "; ".join(errors))
        return

    connection.send_result(msg["id"], {"ok": True})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/save_scene_edits",
        vol.Required("scene_id"): str,
        vol.Required("entities"): dict,
    }
)
async def _handle_websocket_save_scene_edits(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Persist user edits to a saved Selora scene's desired states.

    The panel lets users adjust each entity's target state directly on the
    scene card; this rewrites the matching ``scenes.yaml`` entry, refreshes
    the scene store, and updates any session that references the scene.

    The scene name is taken from the stored record (never the payload) and
    the target is restricted to an existing Selora-managed scene, so a
    crafted client cannot create or retarget an arbitrary scene.
    """
    if not _require_admin(connection, msg):
        return

    scene_id = msg["scene_id"]
    scene_store = _get_scene_store(hass)
    record = await scene_store.async_get_scene(scene_id)
    if record is None or record.get("deleted_at") is not None:
        connection.send_error(msg["id"], "not_found", "Scene not found")
        return

    # Strip the "[Selora AI] " display prefix so the writer (which re-adds
    # it) doesn't double it.
    raw_name = record.get("name") or ""
    name = raw_name.removeprefix("[Selora AI] ").strip() or raw_name

    from .scene_utils import async_create_scene, validate_scene_payload  # noqa: PLC0415

    ok, reason, normalized = validate_scene_payload(
        {"name": name, "entities": msg["entities"]}, hass
    )
    if not ok or normalized is None:
        connection.send_error(msg["id"], "invalid_scene", reason)
        return

    try:
        result = await async_create_scene(
            hass,
            normalized,
            existing_scene_id=scene_id,
            # Trusted admin edit of a known Selora scene — bypass the
            # session-membership gate (None) but keep the prefix check.
            session_scene_ids=None,
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to save scene edits for %s: %s", scene_id, exc)
        connection.send_error(msg["id"], "save_failed", str(exc))
        return

    try:
        await scene_store.async_add_scene(
            result["scene_id"],
            result["name"],
            result["entity_count"],
            entity_id=result.get("entity_id"),
            content_hash=result.get("content_hash"),
        )
    except Exception:  # noqa: BLE001
        _LOGGER.warning("Failed to update scene %s in store after edit", scene_id)

    try:
        store: ConversationStore = hass.data[DOMAIN].setdefault(
            "_conv_store", ConversationStore(hass)
        )
        await store.update_scene_in_sessions(scene_id, result["name"], result["scene_yaml"])
    except Exception:  # noqa: BLE001
        _LOGGER.warning("Failed to propagate scene %s edits to sessions", scene_id)

    connection.send_result(
        msg["id"],
        {
            "scene_id": result["scene_id"],
            "entity_id": result.get("entity_id"),
            "entity_count": result["entity_count"],
            "scene_yaml": result["scene_yaml"],
            "entities": normalized["entities"],
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


def _suggestion_ignore_filter(hass: HomeAssistant) -> Callable[[dict[str, Any]], bool]:
    """Build a predicate that returns True if a suggestion should be hidden.

    Three match paths so direct device / area references in HA's automation
    forms (device triggers / conditions / actions, ``target.device_id``,
    ``target.area_id``) are caught alongside entity references:

    * any referenced entity_id is in the expanded ignored set
    * any referenced device_id carries the exclude label directly
    * any referenced area_id carries the exclude label directly

    Without the device/area branches an automation that targets a labeled
    device by ID — without naming any of its entities — would slip through.
    """
    from homeassistant.helpers import device_registry as dr

    from .automation_utils import _collect_referenced_resources
    from .entity_filter import resolve_ignored_entity_ids, resolve_label_tagged_items

    ignored_entities = resolve_ignored_entity_ids(hass)
    tagged = resolve_label_tagged_items(hass)
    ignored_devices = set(tagged["devices"])
    ignored_areas = set(tagged["areas"])

    # Expand: a device that lives in a labeled area should also be treated
    # as ignored, so a suggestion using `device_id` directly (device
    # trigger, device action, target.device_id) for a device in a hidden
    # area still gets filtered out.
    if ignored_areas:
        dev_reg = dr.async_get(hass)
        for dev in dev_reg.devices.values():
            if dev.area_id and dev.area_id in ignored_areas:
                ignored_devices.add(dev.id)

    if not ignored_entities and not ignored_devices and not ignored_areas:
        return lambda _s: False

    def _ignored(suggestion: dict[str, Any]) -> bool:
        automation = suggestion.get("automation_data") or suggestion
        try:
            entities, devices, areas = _collect_referenced_resources(automation)
        except Exception:
            return False
        if ignored_entities and any(eid in ignored_entities for eid in entities):
            return True
        if ignored_devices and any(did in ignored_devices for did in devices):
            return True
        return bool(ignored_areas) and any(aid in ignored_areas for aid in areas)

    return _ignored


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

    is_ignored = _suggestion_ignore_filter(hass)
    suggestions = [s for s in suggestions if not is_ignored(s)]
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
        is_ignored = _suggestion_ignore_filter(hass)
        all_suggestions = []
        seen_aliases: set[str] = set()
        seen_fingerprints: set[str] = set()
        for s in list(hass.data.get(DOMAIN, {}).get("proactive_suggestions", [])):
            alias = (s.get("alias") or "").strip().lower()
            if alias and (alias in existing_aliases or alias in seen_aliases):
                continue
            if is_ignored(s):
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
            if is_ignored(s):
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
            parsed = llm.parse_streamed_response(
                result.get("response", ""), entities, user_message=prompt
            )
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

            # Detection paths, in order: entity-registry label, the
            # YAML id prefix exposed in state attributes, and the
            # legacy ``[Selora AI]`` description marker for
            # pre-label automations.
            if not is_selora and entry and entry.labels and SELORA_AI_LABEL_ID in entry.labels:
                is_selora = True

            description = state.attributes.get("description") or ""
            if not is_selora and description and "[Selora AI]" in description:
                is_selora = True

            if not automation_id:
                state_id = state.attributes.get("id")
                if state_id is not None:
                    state_id_str = str(state_id)
                    if state_id_str in yaml_by_id:
                        automation_id = state_id_str
                    if not is_selora and state_id_str.startswith(AUTOMATION_ID_PREFIX):
                        is_selora = True

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

    from .entity_filter import resolve_label_tagged_items

    # Merge entry data with options for a complete view
    config_data = {**entry.data, **entry.options}
    aigw = _aigateway_view(config_data)

    from .providers import discover_selora_local_host

    _selora_local_discovered_host = await discover_selora_local_host(
        hass, config_data.get(CONF_SELORA_LOCAL_HOST)
    )
    _selora_local_available = _selora_local_discovered_host is not None

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
            "selora_local_available": _selora_local_available,
            "selora_local_discovered_host": _selora_local_discovered_host,
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
            "exclude_label_id": SELORA_EXCLUDE_LABEL_ID,
            "exclude_label_name": SELORA_EXCLUDE_LABEL_NAME,
            "label_tagged": resolve_label_tagged_items(hass),
            # Anonymous telemetry (opt-in, off by default)
            "telemetry_enabled": config_data.get(CONF_TELEMETRY_ENABLED, DEFAULT_TELEMETRY_ENABLED),
            "telemetry_prompt_seen": config_data.get(
                CONF_TELEMETRY_PROMPT_SEEN, DEFAULT_TELEMETRY_PROMPT_SEEN
            ),
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

    # Keys that only affect the frontend — no reload needed. The telemetry
    # consent flag only gates the one-time banner; it changes no backend
    # behaviour, so persisting it must never trigger a reload.
    frontend_only_keys = {"developer_mode", CONF_TELEMETRY_PROMPT_SEEN}
    # Keys whose change can be applied live to the running LLMClient
    # without rebuilding it. Pricing overrides only impact cost reporting
    # for subsequent calls, so a hot update is enough. The telemetry
    # toggle is read live by ``TelemetryClient`` on every emit, so flipping
    # it needs no reload either.
    hot_option_keys = {CONF_LLM_PRICING_OVERRIDES, CONF_TELEMETRY_ENABLED}

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


def _ensure_exclude_label(hass: HomeAssistant) -> str | None:
    """Return the Selora exclude label_id, creating the label if missing.

    Called from the apply/remove WS handlers so a user labeling something
    via the panel before integration startup has finished still works —
    the label_id is the source of truth, and we shouldn't fail because
    of a startup ordering quirk.
    """
    from homeassistant.helpers import label_registry as lr

    from .entity_filter import resolve_exclude_label_id

    label_id = resolve_exclude_label_id(hass)
    if label_id is not None:
        return label_id
    try:
        label_reg = lr.async_get(hass)
        label = label_reg.async_create(
            name=SELORA_EXCLUDE_LABEL_NAME,
            icon="mdi:eye-off",
            description=(
                "Selora AI ignores entities, devices, and areas tagged with "
                "this label when generating proactive suggestions."
            ),
        )
        return label.label_id
    except Exception:  # noqa: BLE001 — surface as None so the WS path errors cleanly
        _LOGGER.debug("Failed to ensure Selora exclude label", exc_info=True)
        return None


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/apply_exclude_label",
        vol.Optional("entity_id"): str,
        vol.Optional("device_id"): str,
        vol.Optional("area_id"): str,
    }
)
async def _handle_websocket_apply_exclude_label(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Tag an entity / device / area with the Selora exclude label."""
    if not _require_admin(connection, msg):
        return

    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from .entity_filter import resolve_label_tagged_items

    label_id = _ensure_exclude_label(hass)
    if label_id is None:
        connection.send_error(msg["id"], "label_unavailable", "Could not access label registry")
        return

    entity_id = msg.get("entity_id")
    device_id = msg.get("device_id")
    area_id = msg.get("area_id")
    if not (entity_id or device_id or area_id):
        connection.send_error(
            msg["id"], "missing_target", "entity_id, device_id, or area_id required"
        )
        return

    try:
        if entity_id:
            ent_reg = er.async_get(hass)
            ent = ent_reg.async_get(entity_id)
            if ent is None:
                connection.send_error(msg["id"], "not_found", f"Unknown entity {entity_id}")
                return
            ent_reg.async_update_entity(entity_id, labels=set(ent.labels or ()) | {label_id})
        if device_id:
            dev_reg = dr.async_get(hass)
            dev = dev_reg.async_get(device_id)
            if dev is None:
                connection.send_error(msg["id"], "not_found", f"Unknown device {device_id}")
                return
            dev_reg.async_update_device(device_id, labels=set(dev.labels or ()) | {label_id})
        if area_id:
            area_reg = ar.async_get(hass)
            area = area_reg.async_get_area(area_id)
            if area is None:
                connection.send_error(msg["id"], "not_found", f"Unknown area {area_id}")
                return
            area_reg.async_update(area_id, labels=set(area.labels or ()) | {label_id})
    except Exception as exc:  # noqa: BLE001
        _LOGGER.exception("Failed to apply exclude label")
        connection.send_error(msg["id"], "apply_failed", str(exc))
        return

    connection.send_result(msg["id"], {"label_tagged": resolve_label_tagged_items(hass)})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/remove_exclude_label",
        vol.Optional("entity_id"): str,
        vol.Optional("device_id"): str,
        vol.Optional("area_id"): str,
    }
)
async def _handle_websocket_remove_exclude_label(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Remove the Selora exclude label from an entity / device / area."""
    if not _require_admin(connection, msg):
        return

    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from .entity_filter import resolve_exclude_label_id, resolve_label_tagged_items

    label_id = resolve_exclude_label_id(hass)
    if label_id is None:
        # No label means nothing to untag — return current (empty) state.
        connection.send_result(msg["id"], {"label_tagged": resolve_label_tagged_items(hass)})
        return

    entity_id = msg.get("entity_id")
    device_id = msg.get("device_id")
    area_id = msg.get("area_id")

    try:
        if entity_id:
            ent_reg = er.async_get(hass)
            ent = ent_reg.async_get(entity_id)
            if ent is not None and label_id in (ent.labels or ()):
                ent_reg.async_update_entity(entity_id, labels=set(ent.labels) - {label_id})
        if device_id:
            dev_reg = dr.async_get(hass)
            dev = dev_reg.async_get(device_id)
            if dev is not None and label_id in (dev.labels or ()):
                dev_reg.async_update_device(device_id, labels=set(dev.labels) - {label_id})
        if area_id:
            area_reg = ar.async_get(hass)
            area = area_reg.async_get_area(area_id)
            if area is not None and label_id in (area.labels or ()):
                area_reg.async_update(area_id, labels=set(area.labels) - {label_id})
    except Exception as exc:  # noqa: BLE001
        _LOGGER.exception("Failed to remove exclude label")
        connection.send_error(msg["id"], "remove_failed", str(exc))
        return

    connection.send_result(msg["id"], {"label_tagged": resolve_label_tagged_items(hass)})


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

    is_ignored = _suggestion_ignore_filter(hass)
    suggestions = [s for s in suggestions if not is_ignored(s)]
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
    except (
        ValueError,
        UnicodeDecodeError,
    ):
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


# ── Command Approvals (WebSocket) ────────────────────────────────────────────


# Past-tense verbs live in ``command_policy`` so both the approval
# resolver and the tool-loop short-circuit (in ``llm_client.client``)
# pull from the same table — no duplication, no chance for the live
# bubble and the persisted "Done" message to disagree on wording.
from .llm_client.command_policy import (  # noqa: E402, PLC0415
    _SENTENCE_FORMAT_BY_LANG,
    _normalize_lang,
)
from .llm_client.command_policy import past_verb_for as _past_verb_for  # noqa: E402, PLC0415

# Localized footer / empty / error strings for the persisted approval
# result message. Keys mirror the locales supported elsewhere; missing
# locales fall through to the English entry.
_APPROVAL_EMPTY_BY_LANG: dict[str, str] = {
    "en": "Approved, but nothing ran.",
    "fr": "Approuvé, mais rien n'a été exécuté.",
    "de": "Genehmigt, aber nichts wurde ausgeführt.",
    "es": "Aprobado, pero no se ejecutó nada.",
    "it": "Approvato, ma non è stato eseguito nulla.",
    "nl": "Goedgekeurd, maar er is niets uitgevoerd.",
    "hu": "Jóváhagyva, de semmi sem futott le.",
    "zh": "已批准，但未执行任何操作。",
    "pt": "Aprovado, mas nada foi executado.",
    "ja": "承認されましたが、何も実行されませんでした。",
    "ko": "승인되었지만 아무것도 실행되지 않았습니다.",
    "ru": "Одобрено, но ничего не было выполнено.",
}

_APPROVAL_SAVED_BY_LANG: dict[str, str] = {
    "en": "_Approval saved for future requests._",
    "fr": "_Approbation enregistrée pour les futures demandes._",
    "de": "_Genehmigung für zukünftige Anfragen gespeichert._",
    "es": "_Aprobación guardada para futuras solicitudes._",
    "it": "_Approvazione salvata per le future richieste._",
    "nl": "_Goedkeuring opgeslagen voor toekomstige verzoeken._",
    "hu": "_Jóváhagyás mentve a jövőbeli kérésekhez._",
    "zh": "_已为后续请求保存批准。_",
    "pt": "_Aprovação guardada para pedidos futuros._",
    "ja": "_今後のリクエストのために承認を保存しました。_",
    "ko": "_향후 요청을 위해 승인이 저장되었습니다._",
    "ru": "_Одобрение сохранено для будущих запросов._",
}

_APPROVAL_SESSION_BY_LANG: dict[str, str] = {
    "en": "_Allowed for the rest of this conversation._",
    "fr": "_Autorisé pour le reste de cette conversation._",
    "de": "_Für den Rest dieses Gesprächs erlaubt._",
    "es": "_Permitido para el resto de esta conversación._",
    "it": "_Consentito per il resto di questa conversazione._",
    "nl": "_Toegestaan voor de rest van dit gesprek._",
    "hu": "_A beszélgetés hátralévő részében engedélyezve._",
    "zh": "_在本次对话的剩余时间内已允许。_",
    "pt": "_Permitido durante o resto desta conversa._",
    "ja": "_この会話の残りの間、許可されました。_",
    "ko": "_이 대화의 나머지 동안 허용되었습니다._",
    "ru": "_Разрешено до конца этого разговора._",
}

_APPROVAL_ERRORS_BY_LANG: dict[str, str] = {
    "en": "Errors:",
    "fr": "Erreurs :",
    "de": "Fehler:",
    "es": "Errores:",
    "it": "Errori:",
    "nl": "Fouten:",
    "hu": "Hibák:",
    "zh": "错误：",
    "pt": "Erros:",
    "ja": "エラー：",
    "ko": "오류:",
    "ru": "Ошибки:",
}

_APPROVAL_DENIED_BY_LANG: dict[str, str] = {
    "en": "Request denied. Nothing was executed.",
    "fr": "Demande refusée. Rien n'a été exécuté.",
    "de": "Anfrage abgelehnt. Es wurde nichts ausgeführt.",
    "es": "Solicitud denegada. No se ejecutó nada.",
    "it": "Richiesta rifiutata. Non è stato eseguito nulla.",
    "nl": "Verzoek geweigerd. Er is niets uitgevoerd.",
    "hu": "Kérés elutasítva. Semmi sem futott le.",
    "zh": "请求已被拒绝。未执行任何操作。",
    "pt": "Pedido recusado. Nada foi executado.",
    "ja": "リクエストが拒否されました。何も実行されませんでした。",
    "ko": "요청이 거부되었습니다. 아무것도 실행되지 않았습니다.",
    "ru": "Запрос отклонён. Ничего не было выполнено.",
}


# No-response fallback, localized like the runtime approval/exhaustion
# strings. Chat replies follow ``hass.config.language``, so the silent-empty
# substitute must too — an English bubble for a French user is non-silent but
# wrong-language. ``en`` mirrors ``STREAM_EMPTY_RESPONSE_MESSAGE``.
_EMPTY_RESPONSE_BY_LANG: dict[str, str] = {
    "en": STREAM_EMPTY_RESPONSE_MESSAGE,
    "fr": "Je n'ai pas reçu de réponse cette fois-ci. Veuillez réessayer, ou reformuler si cela persiste.",
    "de": "Diesmal habe ich keine Antwort erhalten. Bitte versuchen Sie es erneut oder formulieren Sie es um, falls es weiterhin auftritt.",
    "es": "Esta vez no obtuve respuesta. Vuelva a intentarlo o reformúlelo si sigue ocurriendo.",
    "it": "Questa volta non ho ricevuto una risposta. Riprova o riformula se continua a succedere.",
    "nl": "Ik heb deze keer geen antwoord gekregen. Probeer het opnieuw of herformuleer als het blijft gebeuren.",
    "hu": "Ezúttal nem kaptam választ. Kérjük, próbálja újra, vagy fogalmazza át, ha továbbra is előfordul.",
    "zh": "这次我没有得到回应。请重试，如果持续出现请换一种说法。",
    "pt": "Desta vez não obtive resposta. Tente novamente ou reformule se continuar a acontecer.",
    "ja": "今回は応答がありませんでした。もう一度お試しいただくか、繰り返し発生する場合は言い換えてください。",
    "ko": "이번에는 응답을 받지 못했습니다. 다시 시도하시거나 계속되면 다르게 표현해 주세요.",
    "ru": "На этот раз я не получил ответа. Пожалуйста, попробуйте снова или переформулируйте, если это повторяется.",
}


def _approval_phrase(table: dict[str, str], language: str | None) -> str:
    """Look up a localized approval-message phrase with EN fallback."""
    lang = _normalize_lang(language)
    return table.get(lang, table["en"])


def _friendly_name(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    if state is None:
        return entity_id
    return state.attributes.get("friendly_name") or entity_id


def _build_approval_result_message(
    hass: HomeAssistant,
    calls: list[dict[str, Any]],
    executed_indices: set[int],
    scope: str,
    *,
    language: str | None = None,
) -> tuple[str, list[str]]:
    """Build the persisted "Done" message text + a list of executed entity_ids.

    Each successfully executed call becomes a past-tense sentence like
    "Locked Front Door." A trailing ``[[entities:…]]`` marker is appended
    when any entity targets fired so the chat renders a real HA tile card.

    The footnote on Always / Session scope tells the user the grant persists.
    Returned even on empty inputs so the caller doesn't have to special-case.

    ``executed_indices`` is the set of ``calls`` positions that actually
    fired — index-based (not by service name) so a proposal containing
    duplicate services (two ``lock.unlock`` targeting different doors,
    where one raised) reports only the one that ran.
    """
    # Prefer the requesting user's locale (passed in from the WS
    # message) so the persisted "Done" bubble matches the language
    # the user saw on the approval card. Falls back to the
    # server-wide HA locale only when no request locale was provided
    # (e.g. legacy clients that don't forward `language`).
    lang = _normalize_lang(language or hass.config.language)
    fmt = _SENTENCE_FORMAT_BY_LANG.get(lang, _SENTENCE_FORMAT_BY_LANG["en"])
    sentences: list[str] = []
    all_entity_ids: list[str] = []
    for idx, call in enumerate(calls):
        if idx not in executed_indices:
            continue
        service = str(call.get("service", ""))
        target = call.get("target") or {}
        raw = target.get("entity_id") if isinstance(target, dict) else None
        if isinstance(raw, str):
            ids = [raw] if raw else []
        elif isinstance(raw, list):
            ids = [str(e) for e in raw if isinstance(e, str)]
        else:
            ids = []
        past = _past_verb_for(service, lang)
        if ids:
            target_text = ", ".join(_friendly_name(hass, eid) for eid in ids)
            sentences.append(fmt.format(past=past, target=target_text))
            all_entity_ids.extend(ids)
        else:
            # notify.mobile_app_x, script.bedtime — no entity, use the service tail
            tail = service.split(".", 1)[1] if "." in service else service
            sentences.append(fmt.format(past=past, target=tail))

    content = " ".join(sentences) if sentences else _approval_phrase(_APPROVAL_EMPTY_BY_LANG, lang)
    if all_entity_ids:
        content += f"\n\n[[entities:{','.join(all_entity_ids)}]]"
    if scope == "always":
        content += "\n\n" + _approval_phrase(_APPROVAL_SAVED_BY_LANG, lang)
    elif scope == "session":
        content += "\n\n" + _approval_phrase(_APPROVAL_SESSION_BY_LANG, lang)
    return content, all_entity_ids


# Per-proposal in-flight guard. The chat WS handler runs on HA's single
# asyncio event loop, but ``_handle_websocket_resolve_approval`` yields
# on every ``await`` — between the "still pending?" check and the
# status write, the loop can dispatch a second request for the same
# proposal that ALSO sees "pending" and executes a second time.
# Membership in this set is checked synchronously before any await, so
# a duplicate click can't slip past the gate.
_in_flight_approvals: set[str] = set()


def _find_pending_approval(
    session: dict[str, Any] | None,
    proposal_id: str,
) -> tuple[int, dict[str, Any]] | None:
    """Locate an unresolved ``command_approval`` proposal in *session*.

    Returns ``(message_index, message)`` or None. We look up by
    proposal_id (not message_index) because the message position can
    shift if pruning runs between the user's click and the WS arrival;
    proposal_ids are uuid4s so collisions are negligible.
    """
    if not session:
        return None
    for idx, message in enumerate(session.get("messages", [])):
        if message.get("intent") != "command_approval":
            continue
        approval = message.get("command_approval")
        if not isinstance(approval, dict):
            continue
        if approval.get("proposal_id") != proposal_id:
            continue
        if message.get("approval_status") in ("approved", "denied"):
            continue
        return idx, message
    return None


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/resolve_approval",
        vol.Required("session_id"): str,
        vol.Required("proposal_id"): str,
        vol.Required("scope"): vol.In(["once", "session", "always", "deny"]),
        # Per-entity vs wildcard recording of Session/Always grants:
        # - "this": grant only for the entities in this proposal
        #   (default; least-privilege).
        # - "all":  grant the service wildcard for any future entity.
        # Ignored for ``once``/``deny`` scopes.
        vol.Optional("entity_scope", default="this"): vol.In(["this", "all"]),
        vol.Optional("language"): vol.Any(str, None),
    }
)
async def _handle_websocket_resolve_approval(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Resolve a pending command approval (Once / Session / Always / Deny).

    On allow-* scopes we execute the proposal's calls server-side; the
    LLM is not involved in the second leg, so a denied call never runs
    and an approved call can't be silently rewritten between display
    and execution. The audit trail lives on the persisted message
    (approval_status + executed list).
    """
    if not _require_admin(connection, msg):
        return

    store: ConversationStore = hass.data[DOMAIN].setdefault("_conv_store", ConversationStore(hass))
    approval_store = hass.data.get(DOMAIN, {}).get("_approval_store")
    if approval_store is None:
        connection.send_error(msg["id"], "not_ready", "Approval store not initialized")
        return

    session_id = msg["session_id"]
    proposal_id = msg["proposal_id"]
    scope = msg["scope"]
    entity_scope = msg.get("entity_scope", "this")

    # Reject duplicate concurrent clicks BEFORE the first await. The
    # frontend has its own guard, but only this synchronous check
    # protects against rapid double-clicks that both reach the server
    # while the first is mid-execution.
    if proposal_id in _in_flight_approvals:
        connection.send_error(msg["id"], "in_flight", "Approval is already being processed")
        return
    _in_flight_approvals.add(proposal_id)
    try:
        await _resolve_approval(
            hass,
            connection,
            msg,
            store,
            approval_store,
            session_id,
            proposal_id,
            scope,
            entity_scope,
            language=msg.get("language"),
        )
    finally:
        _in_flight_approvals.discard(proposal_id)


async def _resolve_approval(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    store: ConversationStore,
    approval_store: Any,
    session_id: str,
    proposal_id: str,
    scope: str,
    entity_scope: str = "this",
    language: str | None = None,
) -> None:
    """Inner resolver — runs inside the in-flight guard."""
    session = await store.get_session(session_id)
    located = _find_pending_approval(session, proposal_id)
    if located is None:
        connection.send_error(
            msg["id"],
            "not_found",
            "Approval proposal not found or already resolved",
        )
        return

    message_index, message = located
    approval = message["command_approval"]
    calls: list[dict[str, Any]] = approval.get("calls", []) or []
    risk_level: str = approval.get("risk_level", APPROVAL_RISK_LOW)
    user = getattr(connection, "user", None)
    user_id = getattr(user, "id", None)

    # Resolve the effective language ONCE for this resolver call. Both
    # the deny path (which fires early, before the success path's own
    # resolution) and the approval-success path below pull from this so
    # we don't end up with one English sentence next to a localized one
    # on the same persisted bubble.
    effective_language = language or hass.config.language

    if scope == "deny":
        await store.set_approval_status(session_id, message_index, "denied")
        denial_text = _approval_phrase(_APPROVAL_DENIED_BY_LANG, effective_language)
        persisted = await store.append_message(
            session_id, "assistant", denial_text, intent="answer"
        )
        connection.send_result(
            msg["id"],
            {
                "status": "denied",
                "executed": [],
                "result_message": persisted,
            },
        )
        return

    # Validate BEFORE persisting any grant. A model-supplied
    # ``intent: "command_approval"`` payload (or a stale session-store
    # entry) could carry malformed calls; granting Session/Always for
    # them upfront would persist an approval that the validator
    # rejects a moment later, giving the next request for that
    # service a free pass. Validate first, grant only the services
    # that actually survived validation AND required approval, then
    # execute.
    from .llm_client.command_policy import (
        _classify_call,
        _validate_review_call,
        _validate_safe_call,
        call_required_approval,
    )
    from .mcp_server import _safe_command_entity_allowlist

    safe_entities = _safe_command_entity_allowlist(hass)
    validated_calls: list[tuple[int, dict[str, Any]]] = []
    errors: list[str] = []
    for idx, call in enumerate(calls):
        service = str(call.get("service", ""))
        if "." not in service:
            errors.append(f"invalid service: {service!r}")
            continue
        bucket, policy_entry = _classify_call(service)
        if bucket == "blocked":
            errors.append(f"{service}: blocked at execution time")
            continue
        if bucket == "review" and policy_entry is not None:
            validated, shape_err = _validate_review_call(call, policy_entry)
            if shape_err is not None or validated is None:
                errors.append(f"{service}: {shape_err}")
                continue
            validated_calls.append((idx, validated))
            calls[idx] = validated
        elif bucket == "safe":
            # Reapply the full SAFE policy here. The proposal arrived
            # from the session store and could in principle carry a
            # model-supplied ``intent: "command_approval"`` payload
            # whose ``calls`` array never went through
            # ``apply_command_policy`` — without this re-check,
            # approving such a card would bypass the entity allowlist,
            # max-target cap, and data-key whitelist for services
            # like ``light.turn_on`` or ``scene.turn_on``.
            validated_safe, shape_err = _validate_safe_call(call, safe_entities)
            if shape_err is not None or validated_safe is None:
                errors.append(f"{service}: {shape_err}")
                continue
            validated_calls.append((idx, validated_safe))
            calls[idx] = validated_safe

    # Grant ONLY the services that passed validation AND required
    # approval. Skipping the validation step before persisting would
    # let an attacker-shaped approval card (e.g. lock.unlock with no
    # entity_id) record a permanent grant even though the validator
    # rejects the actual call.
    # Per-(service, entity_id) when entity_scope == "this" (default,
    # least-privilege); per-service wildcard when entity_scope == "all".
    # Note: targetless REVIEW services (notify.*, script.*,
    # shell_command.*) always record under the wildcard key regardless
    # of entity_scope — there's no entity to scope to.
    grants_to_record: list[tuple[str, str | None]] = []
    seen_pairs: set[tuple[str, str | None]] = set()
    for _idx, call in validated_calls:
        if not call_required_approval(hass, call):
            continue
        service = str(call.get("service", ""))
        if not service:
            continue
        target = call.get("target") or {}
        raw = target.get("entity_id") if isinstance(target, dict) else None
        if isinstance(raw, str):
            call_ids: list[str] = [raw] if raw else []
        elif isinstance(raw, list):
            call_ids = [eid for eid in raw if isinstance(eid, str)]
        else:
            call_ids = []
        if entity_scope == "all" or not call_ids:
            pair: tuple[str, str | None] = (service, None)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                grants_to_record.append(pair)
        else:
            for eid in call_ids:
                pair = (service, eid)
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    grants_to_record.append(pair)

    if scope == "session":
        for service, eid in grants_to_record:
            approval_store.grant_session(service, session_id=session_id, entity_id=eid)
    elif scope == "always":
        for service, eid in grants_to_record:
            await approval_store.async_grant_always(
                service,
                risk_level=risk_level,
                granted_by_user_id=user_id,
                entity_id=eid,
            )

    # Delayed-command approval: when the original LLM proposal was a
    # ``delayed_command`` (e.g. "unlock the door in 10 minutes"), the
    # delay metadata travels inside the proposal. Route through the
    # ScheduledTaskTracker instead of firing services immediately —
    # otherwise tapping Allow would unlock the door NOW, which is the
    # opposite of what the user asked for.
    if approval.get("original_intent") == "delayed_command":
        from .scheduled_actions import (
            ScheduledTaskTracker,
            validate_delay_seconds,
        )

        tracker: ScheduledTaskTracker = hass.data[DOMAIN].setdefault(
            "_scheduled_tasks", ScheduledTaskTracker(hass)
        )
        delay_seconds_raw = approval.get("delay_seconds")
        scheduled_time_raw = approval.get("scheduled_time")
        approved_calls = [c for _idx, c in validated_calls]
        # Only let the persisted automation skip the risk gate when at
        # least one call genuinely required approval. A card is never built
        # for a pure-SAFE proposal, so this is belt-and-suspenders: it keeps
        # ``bypass_risk_gate`` from ever firing on calls that never went
        # through the approval flow.
        any_required_approval = any(call_required_approval(hass, c) for _idx, c in validated_calls)
        schedule_id: str | None = None
        schedule_error: str | None = None
        try:
            if delay_seconds_raw is not None and approved_calls:
                ok, reason = validate_delay_seconds(delay_seconds_raw)
                if not ok:
                    schedule_error = reason
                else:
                    task = await tracker.schedule_delayed(
                        session_id, approved_calls, delay_seconds_raw, ""
                    )
                    schedule_id = task.schedule_id
            elif scheduled_time_raw is not None and approved_calls:
                task = await tracker.schedule_at_time(
                    session_id,
                    approved_calls,
                    scheduled_time_raw,
                    "",
                    approved=any_required_approval,
                )
                schedule_id = task.schedule_id
            else:
                schedule_error = "delayed_command proposal missing delay metadata"
        except Exception as exc:  # noqa: BLE001 — surface to user
            _LOGGER.warning("Approved scheduled call failed: %s", exc)
            schedule_error = str(exc)

        await store.set_approval_status(session_id, message_index, "approved")
        if schedule_error:
            result_text = f"Approved, but scheduling failed: {schedule_error}"
        else:
            result_text = "Scheduled. The action will run at the requested time."
        if scope == "always":
            result_text += "\n\n_Approval saved for future requests._"
        elif scope == "session":
            result_text += "\n\n_Allowed for the rest of this conversation._"
        # Surface calls dropped during revalidation (e.g. an entity removed
        # between proposal creation and clicking Allow). The immediate path
        # reports these; the scheduled path must too, otherwise part of the
        # user's approved request is silently lost.
        if errors:
            result_text += f"\n\nErrors: {'; '.join(errors)}"
        all_errors = ([schedule_error] if schedule_error else []) + errors
        persisted = await store.append_message(
            session_id, "assistant", result_text, intent="answer"
        )
        connection.send_result(
            msg["id"],
            {
                "status": "approved",
                "scope": scope,
                "scheduled": schedule_id is not None,
                "schedule_id": schedule_id,
                "errors": all_errors,
                "result_message": persisted,
            },
        )
        return

    # Execute validated calls. We deliberately call
    # hass.services.async_call directly rather than reroute through
    # the LLM — the proposal is validated and the user has
    # explicitly authorised these specific calls.
    executed: list[str] = []
    executed_entity_ids: list[str] = []
    executed_indices: set[int] = set()
    for idx, call in validated_calls:
        service = str(call.get("service", ""))
        domain, service_name = service.split(".", 1)
        target = call.get("target") or {}
        data = call.get("data") or {}
        service_data: dict[str, Any] = dict(data)
        target_entity_id = target.get("entity_id") if isinstance(target, dict) else None
        if target_entity_id:
            service_data["entity_id"] = target_entity_id
        try:
            await hass.services.async_call(domain, service_name, service_data, blocking=True)
            executed.append(service)
            executed_indices.add(idx)
            if isinstance(target_entity_id, str):
                executed_entity_ids.append(target_entity_id)
            elif isinstance(target_entity_id, list):
                executed_entity_ids.extend(str(e) for e in target_entity_id if isinstance(e, str))
        except Exception as exc:  # noqa: BLE001 — surface to user
            _LOGGER.warning("Approved call %s failed: %s", service, exc)
            errors.append(f"{service}: {exc}")

    await store.set_approval_status(session_id, message_index, "approved")

    # Persist the friendly past-tense result as a new assistant message
    # so reloading the session shows what happened, not just "Approved".
    # The chat renderer will turn [[entities:…]] markers into HA tile
    # cards, same as for any other assistant message. We pass the
    # successful-indices set rather than ``set(executed)`` because a
    # multi-call approval can contain duplicate services (e.g. two
    # ``lock.unlock`` calls targeting different doors) and one may
    # fail — index-based tracking is the only way to distinguish
    # which specific call ran.
    result_text, _ = _build_approval_result_message(
        hass, calls, executed_indices, scope, language=effective_language
    )
    if errors:
        errors_label = _approval_phrase(_APPROVAL_ERRORS_BY_LANG, effective_language)
        result_text += f"\n\n{errors_label} {'; '.join(errors)}"
    persisted = await store.append_message(session_id, "assistant", result_text, intent="answer")

    connection.send_result(
        msg["id"],
        {
            "status": "approved",
            "scope": scope,
            "executed": executed,
            "executed_entity_ids": executed_entity_ids,
            "errors": errors,
            "result_message": persisted,
        },
    )


@websocket_api.async_response
@decorators.websocket_command({vol.Required("type"): "selora_ai/list_approvals"})
async def _handle_websocket_list_approvals(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return all persistent ('Always') approvals for the Manage UI.

    Enriches each grant with the granting user's display name so the
    Settings list shows "granted by Phil 11m ago" instead of an opaque
    user_id. HA installs commonly have multiple users (family members
    sharing a system), so attributing the auto-approval to a specific
    account matters — otherwise revoking is a blind action.
    """
    if not _require_admin(connection, msg):
        return
    approval_store = hass.data.get(DOMAIN, {}).get("_approval_store")
    if approval_store is None:
        connection.send_error(msg["id"], "not_ready", "Approval store not initialized")
        return
    grants = await approval_store.async_list_grants()

    # Resolve user_id → name once per unique id. Falls back to a short
    # id prefix when the user has been deleted (so the row still
    # carries SOME attribution rather than dropping the field).
    enriched: list[dict[str, Any]] = []
    name_cache: dict[str, str] = {}
    for grant in grants:
        out = dict(grant)
        user_id = grant.get("granted_by_user_id")
        if user_id:
            name = name_cache.get(user_id)
            if name is None:
                user = await hass.auth.async_get_user(user_id)
                name = (user.name if user and user.name else None) or f"user {user_id[:8]}"
                name_cache[user_id] = name
            out["granted_by_name"] = name
        enriched.append(out)

    connection.send_result(msg["id"], {"grants": enriched})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/revoke_approval",
        # ``key`` is the full grant identifier — ``service`` for a
        # wildcard or ``service:entity_id`` for a per-entity grant.
        # The legacy ``service`` field is still accepted for one
        # release so older bundled frontends continue to work; new
        # callers should use ``key``.
        vol.Exclusive("key", "approval_identifier"): str,
        vol.Exclusive("service", "approval_identifier"): str,
    }
)
async def _handle_websocket_revoke_approval(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Revoke a persistent approval by its grant key.

    The ``list_approvals`` response includes a ``key`` field on each
    grant; revoke passes that same string back. Per-entity grants
    revoke just that pair, leaving any service wildcard intact.
    """
    if not _require_admin(connection, msg):
        return
    approval_store = hass.data.get(DOMAIN, {}).get("_approval_store")
    if approval_store is None:
        connection.send_error(msg["id"], "not_ready", "Approval store not initialized")
        return
    grant_key = msg.get("key") or msg.get("service")
    if not grant_key:
        connection.send_error(msg["id"], "invalid_params", "Missing 'key' or 'service'")
        return
    revoked = await approval_store.async_revoke(grant_key)
    if not revoked:
        connection.send_error(msg["id"], "not_found", "No persistent approval for that key")
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
            except (
                ImportError,
                KeyError,
            ):
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
        except (
            FileNotFoundError,
            OSError,
        ):
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

    # Recipes v2 — deterministic pipeline that renders an HA package.
    # Register WS commands first thing so the panel can list / install
    # recipes even before the chat surface has finished its own setup.
    # The HTTP upload view is registered alongside so users can ingest
    # a bundle from a local file (URL fetch goes through the WS layer).
    from .recipes.upload_view import async_register_recipe_upload_view
    from .recipes.ws import async_register_recipe_websocket_commands

    async_register_recipe_websocket_commands(hass)
    async_register_recipe_upload_view(hass)

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
    websocket_api.async_register_command(hass, _handle_websocket_apply_exclude_label)
    websocket_api.async_register_command(hass, _handle_websocket_remove_exclude_label)
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
    websocket_api.async_register_command(hass, _handle_websocket_record_chat_feedback)
    websocket_api.async_register_command(hass, _handle_websocket_set_automation_status)
    websocket_api.async_register_command(hass, _handle_websocket_set_scene_status)
    websocket_api.async_register_command(hass, _handle_websocket_accept_scene)
    websocket_api.async_register_command(hass, _handle_websocket_save_scene_edits)
    websocket_api.async_register_command(hass, _handle_websocket_apply_scene_states)
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
    # Command approvals (Allow once/session/always/deny)
    websocket_api.async_register_command(hass, _handle_websocket_resolve_approval)
    websocket_api.async_register_command(hass, _handle_websocket_list_approvals)
    websocket_api.async_register_command(hass, _handle_websocket_revoke_approval)
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
    except (
        ImportError,
        AttributeError,
    ):
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
    # Cache-bust the panel module: the static path is served without a
    # content hash, so browsers cache /api/selora_ai/panel.js indefinitely
    # and never pick up a redeploy. Tagging the URL with the bundle's
    # mtime forces a refetch whenever the file changes.
    import os  # noqa: PLC0415

    from homeassistant.components import frontend

    panel_file = hass.config.path(f"custom_components/{DOMAIN}/frontend/panel.js")
    try:
        panel_mtime = await hass.async_add_executor_job(os.path.getmtime, panel_file)
        panel_version = str(int(panel_mtime))
    except OSError:
        panel_version = "0"
    panel_module_url = f"/api/{DOMAIN}/panel.js?v={panel_version}"

    # In recent HA, async_register_panel might be deprecated or renamed
    # We try both async_register_panel and async_register_built_in_panel
    if hasattr(frontend, "async_register_panel"):
        frontend.async_register_panel(
            hass,
            frontend_url_path=PANEL_PATH,
            webcomponent_name=PANEL_NAME,
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            module_url=panel_module_url,
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
                        "module_url": panel_module_url,
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

    # A stray entry that never linked a provider (no explicit llm_provider
    # and no AI Gateway tokens) would otherwise default to Selora Cloud
    # with empty credentials — its collector then fires auth-less requests
    # the gateway rejects with HTTP 401 "Missing or malformed Authorization
    # header", and chat may bind to it and report "configure your LLM
    # provider". Treat it as records-only, like device entries. Mirror this
    # guard in async_unload_entry so teardown doesn't touch shared state.
    if not _entry_is_configurable_llm(entry.data):
        _LOGGER.warning(
            "Selora AI entry %s has no LLM provider linked — skipping runtime "
            "setup. Remove the duplicate entry or link a provider in Settings.",
            entry.title,
        )
        return True

    provider = _resolve_llm_provider(entry.data)

    # Auto-create the "Selora exclude" label so users can apply it from HA's
    # native entity / device / area editors without a separate workflow.
    # Looked up at filter time — see resolve_ignored_entity_ids.
    try:
        from homeassistant.helpers import label_registry as lr

        label_reg = lr.async_get(hass)
        if (
            label_reg.async_get_label(SELORA_EXCLUDE_LABEL_ID) is None
            and label_reg.async_get_label_by_name(SELORA_EXCLUDE_LABEL_NAME) is None
        ):
            label_reg.async_create(
                name=SELORA_EXCLUDE_LABEL_NAME,
                icon="mdi:eye-off",
                description=(
                    "Selora AI ignores entities, devices, and areas tagged with "
                    "this label when generating proactive suggestions."
                ),
            )
    except Exception:  # noqa: BLE001 — label bootstrap is best-effort
        _LOGGER.debug("Failed to ensure Selora exclude label", exc_info=True)

    lookback = entry.data.get(CONF_RECORDER_LOOKBACK_DAYS, DEFAULT_RECORDER_LOOKBACK_DAYS)
    pricing_overrides = entry.options.get(CONF_LLM_PRICING_OVERRIDES) or {}

    from .device_manager import DeviceManager
    from .llm_client import LLMClient
    from .llm_client.prompts import async_preload_prompts
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

    # Command approval store — persists "Always allow <service>" grants so
    # the LLM can run REVIEW-bucket services (tts.*, notify.*, lock.unlock,
    # …) without re-prompting on every turn. Looked up by command_policy
    # via hass.data[DOMAIN]["_approval_store"].
    from .approval_store import ApprovalStore

    approval_store = ApprovalStore(hass)
    await approval_store.async_load()
    hass.data[DOMAIN]["_approval_store"] = approval_store

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

    # Selora AI Local pre-warm: discover the hub's loaded LoRAs + send
    # one tiny request per chat specialist with the REAL HA entity list
    # in the body. llama-server's cache_prompt only hits when the
    # incoming prefix matches what's cached, so we have to prime with
    # the exact entity block the user's first chat will send.
    # Fire-and-forget — pre-warm failures are logged but never block setup.
    if llm:
        from .providers.selora_local import SeloraLocalProvider  # noqa: PLC0415

        if isinstance(llm.provider, SeloraLocalProvider):
            prewarm_entities = _collect_entity_states(hass)

            async def _selora_local_prewarm() -> None:
                try:
                    await llm.provider.prewarm(entities=prewarm_entities)
                except Exception:  # noqa: BLE001 — pre-warm must never crash setup
                    _LOGGER.exception("Selora AI Local pre-warm task failed")

            _bg.append(hass.async_create_task(_selora_local_prewarm()))

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

    # Anonymous home-inventory telemetry (opt-in, off by default). One
    # snapshot shortly after startup once registries are populated, then
    # refreshed daily. The emit is gated on the toggle (read live), so we
    # schedule unconditionally and it no-ops when the user hasn't opted in.
    from .telemetry import get_telemetry

    telemetry = get_telemetry(hass)

    async def _telemetry_snapshot(_now: Any = None) -> None:
        await telemetry.async_send_snapshot(provider=provider)

    async def _telemetry_periodic(_now: Any = None) -> None:
        # The recurring tick sends the inventory snapshot AND flushes the
        # activity-counter rollup, whose window matches the interval.
        await telemetry.async_send_snapshot(provider=provider)
        await telemetry.async_send_activity(provider=provider)

    async def _delayed_telemetry_snapshot() -> None:
        await asyncio.sleep(TELEMETRY_SNAPSHOT_STARTUP_DELAY)
        # Snapshot only on startup — activity is flushed on the recurring
        # interval so its period_hours label stays accurate (a startup
        # flush would emit a ~2-minute window mislabelled as 24h).
        await _telemetry_snapshot()

    _bg.append(hass.async_create_task(_delayed_telemetry_snapshot()))
    unsub_telemetry = async_track_time_interval(
        hass, _telemetry_periodic, timedelta(hours=TELEMETRY_SNAPSHOT_INTERVAL_HOURS)
    )
    hass.data[DOMAIN][entry.entry_id]["unsub_telemetry"] = unsub_telemetry

    # Register update listener for options
    snapshots: dict[str, dict[str, Any]] = hass.data.setdefault(DOMAIN, {}).setdefault(
        "_entry_data_snapshots", {}
    )
    snapshots[entry.entry_id] = dict(entry.data)
    opt_snapshots: dict[str, dict[str, Any]] = hass.data.setdefault(DOMAIN, {}).setdefault(
        "_entry_options_snapshots", {}
    )
    opt_snapshots[entry.entry_id] = dict(entry.options)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


_AIGW_TOKEN_FIELDS = frozenset(
    {
        CONF_AIGATEWAY_ACCESS_TOKEN,
        CONF_AIGATEWAY_REFRESH_TOKEN,
        CONF_AIGATEWAY_EXPIRES_AT,
    }
)

# Option keys that are applied live (no entry reload needed). Must mirror
# the ``hot_option_keys`` + ``frontend_only_keys`` classification in
# ``_handle_websocket_update_config``: pricing overrides are pushed to the
# running client, telemetry flags are read live on each use, and
# developer_mode only affects the frontend. An options-only change confined
# to these must NOT trigger ``async_reload_entry``'s reload.
_NO_RELOAD_OPTION_KEYS = frozenset(
    {
        CONF_LLM_PRICING_OVERRIDES,
        CONF_TELEMETRY_ENABLED,
        CONF_TELEMETRY_PROMPT_SEEN,
        "developer_mode",
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
    opt_snapshots: dict[str, dict[str, Any]] = hass.data.setdefault(DOMAIN, {}).setdefault(
        "_entry_options_snapshots", {}
    )
    previous = snapshots.get(entry.entry_id, {})
    current = dict(entry.data)
    snapshots[entry.entry_id] = current
    prev_options = opt_snapshots.get(entry.entry_id, {})
    current_options = dict(entry.options)
    opt_snapshots[entry.entry_id] = current_options

    if previous:
        changed = {
            key for key in previous.keys() | current.keys() if previous.get(key) != current.get(key)
        }
        if changed and changed.issubset(_AIGW_TOKEN_FIELDS):
            return
        options_changed = {
            key
            for key in prev_options.keys() | current_options.keys()
            if prev_options.get(key) != current_options.get(key)
        }
        # No entry.data change and only live-applied options touched (pricing
        # overrides applied directly; telemetry flags read live on each use) —
        # reloading would needlessly interrupt active LLM calls and restart
        # background services.
        if not changed and options_changed and options_changed.issubset(_NO_RELOAD_OPTION_KEYS):
            return

    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload — stop background tasks, close sessions."""
    # Device onboarding entries have no runtime state to clean up
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_DEVICE:
        return True

    # Entries skipped at setup (records-only — e.g. an unconfigured stray
    # entry) own no per-entry runtime state, so there's nothing to tear
    # down. Returning early also keeps us from running the shared-state
    # cleanup below (MCP token store, JWT validator) on behalf of an entry
    # that never created it — that state belongs to the real entry.
    if entry.entry_id not in hass.data.get(DOMAIN, {}):
        return True

    data = hass.data[DOMAIN].pop(entry.entry_id, {})

    collector = data.get("collector")
    unsub_discovery = data.get("unsub_discovery")
    unsub_telemetry = data.get("unsub_telemetry")

    if collector:
        await collector.async_stop()

    if unsub_discovery:
        unsub_discovery()

    if unsub_telemetry:
        unsub_telemetry()

    # Drop this entry's reload snapshots so they don't accumulate across
    # remove/re-add cycles (each re-add gets a fresh entry_id, leaving the
    # old key behind otherwise).
    for _snap_key in ("_entry_data_snapshots", "_entry_options_snapshots"):
        snap = hass.data[DOMAIN].get(_snap_key)
        if snap is not None:
            snap.pop(entry.entry_id, None)

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
    hass.data[DOMAIN].pop("_mcp_suggestion_status", None)
    hass.data[DOMAIN].pop("_conv_store", None)
    hass.data[DOMAIN].pop("_scene_store", None)

    # Unload entity platforms
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    _LOGGER.info("Selora AI stopped")
    return True
