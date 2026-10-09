"""Tests for the persistent chat-session store."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest

from custom_components.selora_ai.conversation_store import ConversationStore


@pytest.mark.asyncio
async def test_a_session_without_messages_is_not_listed(hass: HomeAssistant) -> None:
    """Opening a fresh chat creates the session before anything is sent; one the
    user walks away from must not sit in the sidebar as "New conversation"."""
    store = ConversationStore(hass)
    empty = await store.create_session()
    used = await store.create_session()
    await store.append_message(used["id"], "user", "turn off the kitchen lights")

    listed = [s["id"] for s in await store.list_sessions()]

    assert listed == [used["id"]]
    assert await store.get_session(empty["id"]) is not None


@pytest.mark.asyncio
async def test_creating_a_session_prunes_stale_empty_ones(hass: HomeAssistant) -> None:
    """Abandoned empty sessions would otherwise pile up and evict real
    conversations under the store's cap."""
    store = ConversationStore(hass)
    stale = await store.create_session()
    stale["created_at"] = (dt_util.now() - timedelta(hours=2)).isoformat()
    kept = await store.create_session()
    await store.append_message(kept["id"], "user", "hello")
    kept["created_at"] = stale["created_at"]

    await store.create_session()

    assert await store.get_session(stale["id"]) is None
    assert await store.get_session(kept["id"]) is not None


@pytest.mark.asyncio
async def test_a_just_opened_empty_session_survives_another_create(
    hass: HomeAssistant,
) -> None:
    """A chat opened moments ago in another tab is still in use."""
    store = ConversationStore(hass)
    recent = await store.create_session()

    await store.create_session()

    assert await store.get_session(recent["id"]) is not None


@pytest.mark.asyncio
async def test_a_pruned_session_is_recreated_by_its_first_message(
    hass: HomeAssistant,
) -> None:
    """A tab holding a pruned session id can still send into it."""
    store = ConversationStore(hass)
    stale = await store.create_session()
    stale["created_at"] = (dt_util.now() - timedelta(hours=2)).isoformat()
    await store.create_session()

    await store.append_message(stale["id"], "user", "turn on the porch light")

    listed = await store.list_sessions()
    assert [s["id"] for s in listed] == [stale["id"]]
    assert listed[0]["title"] == "turn on the porch light"
