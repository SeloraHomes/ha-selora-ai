"""Agent-activity steps must survive a session round-trip (persist → reload),
so the timeline is restored when a conversation is reopened. Mirrors the
kwargs the chat_stream websocket handler persists with."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai import ConversationStore


@pytest.mark.asyncio
async def test_steps_persist_and_survive_reload(hass: HomeAssistant) -> None:
    store = ConversationStore(hass)
    steps = [
        {
            "id": "tool-1",
            "kind": "tool",
            "label": "Searched your entities",
            "status": "done",
            "icon": "mdi:magnify",
        },
        {"id": "draft", "kind": "draft", "label": "Drafted the automation", "status": "done"},
        {
            "id": "validate",
            "kind": "validate",
            "label": "Validated the automation",
            "status": "done",
        },
    ]
    await store.append_message(
        "sess-steps",
        "assistant",
        "Here's the automation I built.",
        intent="automation",
        automation={"alias": "Doorbell"},
        steps=steps,
    )

    # Simulate a reload: a fresh store instance reads the persisted data.
    reloaded = ConversationStore(hass)
    session = await reloaded.get_session("sess-steps")
    assert session is not None
    msg = session["messages"][-1]
    assert msg.get("steps") == steps


@pytest.mark.asyncio
async def test_empty_steps_not_persisted(hass: HomeAssistant) -> None:
    # The handler passes ``steps or None`` — an empty timeline writes nothing.
    store = ConversationStore(hass)
    await store.append_message("sess-empty", "assistant", "hi", steps=None)
    session = await store.get_session("sess-empty")
    assert session is not None
    assert "steps" not in session["messages"][-1]
