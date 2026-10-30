"""The heading a guided conversation opens with (``selora_ai/new_session``)."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
import pytest
import voluptuous as vol

from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.conversation_store import ConversationStore
from custom_components.selora_ai.websocket import sessions as sessions_ws

GUIDE = {
    "icon": "mdi:face-recognition",
    "title": "Recognize the people you know on your cameras",
    "subtitle": "Frigate can tell who is at the door once it knows their faces.",
    "banner": "frigate",
}


async def _new_session(hass: HomeAssistant, **extra: object) -> tuple[ConversationStore, str]:
    store = ConversationStore(hass)
    hass.data.setdefault(DOMAIN, {})["_conv_store"] = store
    handler = inspect.unwrap(sessions_ws._handle_websocket_new_session)
    connection = MagicMock()
    connection.user.is_admin = True
    msg = sessions_ws._handle_websocket_new_session._ws_schema(
        {"id": 1, "type": "selora_ai/new_session", **extra}
    )
    await handler(hass, connection, msg)
    return store, connection.send_result.call_args[0][1]["session_id"]


async def test_a_guided_session_keeps_its_heading_for_a_reopen(hass: HomeAssistant) -> None:
    store, session_id = await _new_session(hass, guide=GUIDE)

    assert (await store.get_session(session_id))["guide"] == GUIDE


async def test_an_ordinary_session_has_no_heading(hass: HomeAssistant) -> None:
    store, session_id = await _new_session(hass)

    assert "guide" not in await store.get_session(session_id)


@pytest.mark.parametrize(
    "bad",
    [
        {"banner": "https://example.com/tracker.png"},
        {"icon": "javascript:alert(1)"},
        {"title": "x" * 201},
    ],
)
def test_a_heading_names_a_known_picture_and_an_icon(bad: dict[str, str]) -> None:
    # The panel draws the banner as a CSS url(): only a name it maps itself.
    schema = sessions_ws._handle_websocket_new_session._ws_schema
    with pytest.raises(vol.Invalid):
        schema({"id": 1, "type": "selora_ai/new_session", "guide": {**GUIDE, **bad}})
