"""Test the _resolve_or_create_session helper's `created` flag contract."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.selora_ai import _resolve_or_create_session


class TestResolveOrCreateSession:
    """Contract: returns (session, session_id, created) where `created` is
    True iff a new session was just persisted in this call. The chat
    handler uses the flag to discard empty sessions when the LLM call
    fails before any message can be appended."""

    @pytest.mark.asyncio
    async def test_creates_new_when_no_session_id(self) -> None:
        store = AsyncMock()
        store.create_session.return_value = {"id": "new-1", "messages": []}
        session, session_id, created = await _resolve_or_create_session(store, "")
        assert created is True
        assert session_id == "new-1"
        store.create_session.assert_awaited_once()
        store.get_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolves_existing_session(self) -> None:
        store = AsyncMock()
        store.get_session.return_value = {"id": "existing-7", "messages": [{"role": "user"}]}
        session, session_id, created = await _resolve_or_create_session(
            store, "existing-7"
        )
        assert created is False
        assert session_id == "existing-7"
        store.get_session.assert_awaited_once_with("existing-7")
        store.create_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_when_session_id_not_found(self) -> None:
        # Stale session id — get_session returns None, fall back to create.
        store = AsyncMock()
        store.get_session.return_value = None
        store.create_session.return_value = {"id": "new-2", "messages": []}
        _session, session_id, created = await _resolve_or_create_session(
            store, "stale-id"
        )
        assert created is True
        assert session_id == "new-2"
