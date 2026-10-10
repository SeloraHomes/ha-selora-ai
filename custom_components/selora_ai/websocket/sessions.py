"""Selora AI websocket handlers: sessions.

Extracted from __init__.py. Handlers reach shared integration
helpers via ``from .. import`` (safe: this module is imported
lazily at registration time, after the package has loaded).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import decorators
from homeassistant.core import HomeAssistant
import voluptuous as vol

from .. import (
    _find_llm,
    _require_admin,
    _resolve_llm_entry,
    _resolve_llm_provider,
)
from ..const import (
    DOMAIN,
)
from ..conversation_store import ConversationStore
from ..llm_client.lang_detect import resolve_reply_language
from ..next_prompt import build_transcript, next_prompt_enabled
from ..telemetry import record_activity

_LOGGER = logging.getLogger(__name__)


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
        vol.Required("type"): "selora_ai/predict_next_prompt",
        vol.Required("session_id"): str,
        vol.Optional("language"): vol.Any(str, None),
    }
)
async def _handle_websocket_predict_next_prompt(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Predict the user's next message in a session (see ``next_prompt``).

    Answers ``{"prompt": None}`` whenever there is nothing worth showing,
    including when the feature is off: the panel treats every reason alike.
    """
    if not _require_admin(connection, msg):
        return

    entry = _resolve_llm_entry(hass)
    llm = _find_llm(hass)
    if entry is None or llm is None:
        _LOGGER.debug("Next prompt skipped: no LLM entry")
        connection.send_result(msg["id"], {"prompt": None})
        return
    config_data = {**entry.data, **entry.options}
    provider = _resolve_llm_provider(config_data)
    if not next_prompt_enabled(config_data, provider):
        _LOGGER.debug("Next prompt skipped: off for provider %s", provider)
        connection.send_result(msg["id"], {"prompt": None})
        return

    store: ConversationStore = hass.data[DOMAIN].setdefault("_conv_store", ConversationStore(hass))
    session = await store.get_session(msg["session_id"])
    messages = (session or {}).get("messages", [])
    transcript = build_transcript(messages)
    if transcript is None:
        _LOGGER.debug(
            "Next prompt skipped: session %s does not end on a reply awaiting a message",
            msg["session_id"],
        )
        connection.send_result(msg["id"], {"prompt": None})
        return

    last_user = next(
        (m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"), ""
    )
    language = resolve_reply_language(last_user, msg.get("language"), hass.config.language)
    prompt = await llm.predict_next_prompt(transcript, language)
    connection.send_result(msg["id"], {"prompt": prompt})


def async_register(hass: HomeAssistant) -> None:
    """Register the sessions websocket commands."""
    from homeassistant.components import websocket_api

    websocket_api.async_register_command(hass, _handle_websocket_get_sessions)
    websocket_api.async_register_command(hass, _handle_websocket_get_session)
    websocket_api.async_register_command(hass, _handle_websocket_new_session)
    websocket_api.async_register_command(hass, _handle_websocket_rename_session)
    websocket_api.async_register_command(hass, _handle_websocket_delete_session)
    websocket_api.async_register_command(hass, _handle_websocket_record_chat_feedback)
    websocket_api.async_register_command(hass, _handle_websocket_predict_next_prompt)
