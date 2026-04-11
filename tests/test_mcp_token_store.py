"""Tests for MCPTokenStore — local MCP API token management."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.selora_ai.const import (
    MCP_TOKEN_MAX_COUNT,
    MCP_TOKEN_PERMISSION_ADMIN,
    MCP_TOKEN_PERMISSION_CUSTOM,
    MCP_TOKEN_PERMISSION_READ_ONLY,
    MCP_TOKEN_PREFIX,
)
from custom_components.selora_ai.mcp_token_store import MCPTokenStore, _hash_token


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_hass() -> MagicMock:
    """Create a mock HomeAssistant instance."""
    hass = MagicMock()
    return hass


# ── _hash_token ──────────────────────────────────────────────────────────────


class TestHashToken:
    def test_deterministic(self) -> None:
        assert _hash_token("smt_abc123") == _hash_token("smt_abc123")

    def test_starts_with_sha256(self) -> None:
        assert _hash_token("smt_test").startswith("sha256:")

    def test_different_inputs(self) -> None:
        assert _hash_token("smt_a") != _hash_token("smt_b")


# ── MCPTokenStore ────────────────────────────────────────────────────────────


class TestMCPTokenStore:
    @pytest.fixture
    def store(self) -> MCPTokenStore:
        hass = _make_hass()
        s = MCPTokenStore(hass)
        # Bypass actual Store I/O
        s._store = MagicMock()
        s._store.async_load = AsyncMock(return_value=None)
        s._store.async_save = AsyncMock()
        return s

    @pytest.mark.asyncio
    async def test_create_token(self, store: MCPTokenStore) -> None:
        raw, meta = await store.async_create_token(
            name="Test Token",
            permission_level=MCP_TOKEN_PERMISSION_ADMIN,
            created_by_user_id="user-1",
        )
        assert raw.startswith(MCP_TOKEN_PREFIX)
        assert meta["name"] == "Test Token"
        assert meta["permission_level"] == MCP_TOKEN_PERMISSION_ADMIN
        assert meta["token_prefix"] == raw[:8]
        assert meta["allowed_tools"] is None
        assert meta["created_by_user_id"] == "user-1"
        assert meta["last_used_at"] is None

    @pytest.mark.asyncio
    async def test_create_token_with_custom_permissions(self, store: MCPTokenStore) -> None:
        tools = ["selora_list_automations", "selora_get_automation"]
        raw, meta = await store.async_create_token(
            name="Custom Token",
            permission_level=MCP_TOKEN_PERMISSION_CUSTOM,
            allowed_tools=tools,
            created_by_user_id="user-1",
        )
        assert meta["permission_level"] == MCP_TOKEN_PERMISSION_CUSTOM
        assert meta["allowed_tools"] == tools

    @pytest.mark.asyncio
    async def test_create_token_with_expiration(self, store: MCPTokenStore) -> None:
        expires = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        raw, meta = await store.async_create_token(
            name="Expiring Token",
            permission_level=MCP_TOKEN_PERMISSION_READ_ONLY,
            expires_at=expires,
            created_by_user_id="user-1",
        )
        assert meta["expires_at"] == expires

    @pytest.mark.asyncio
    async def test_validate_token(self, store: MCPTokenStore) -> None:
        raw, meta = await store.async_create_token(
            name="Valid Token",
            permission_level=MCP_TOKEN_PERMISSION_ADMIN,
            created_by_user_id="user-1",
        )
        result = await store.async_validate_token(raw)
        assert result is not None
        assert result["id"] == meta["id"]
        assert result["last_used_at"] is not None

    @pytest.mark.asyncio
    async def test_validate_invalid_token(self, store: MCPTokenStore) -> None:
        await store.async_create_token(
            name="Token",
            permission_level=MCP_TOKEN_PERMISSION_ADMIN,
            created_by_user_id="user-1",
        )
        result = await store.async_validate_token("smt_nonexistent_token_value")
        assert result is None

    @pytest.mark.asyncio
    async def test_validate_expired_token(self, store: MCPTokenStore) -> None:
        expired = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        raw, _ = await store.async_create_token(
            name="Expired Token",
            permission_level=MCP_TOKEN_PERMISSION_ADMIN,
            expires_at=expired,
            created_by_user_id="user-1",
        )
        result = await store.async_validate_token(raw)
        assert result is None

    @pytest.mark.asyncio
    async def test_revoke_token(self, store: MCPTokenStore) -> None:
        raw, meta = await store.async_create_token(
            name="To Revoke",
            permission_level=MCP_TOKEN_PERMISSION_ADMIN,
            created_by_user_id="user-1",
        )
        assert await store.async_revoke_token(meta["id"]) is True
        result = await store.async_validate_token(raw)
        assert result is None

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_token(self, store: MCPTokenStore) -> None:
        assert await store.async_revoke_token("nonexistent-id") is False

    @pytest.mark.asyncio
    async def test_list_tokens_excludes_hash(self, store: MCPTokenStore) -> None:
        await store.async_create_token(
            name="Listed Token",
            permission_level=MCP_TOKEN_PERMISSION_READ_ONLY,
            created_by_user_id="user-1",
        )
        tokens = await store.async_list_tokens()
        assert len(tokens) == 1
        assert "token_hash" not in tokens[0]
        assert tokens[0]["name"] == "Listed Token"

    @pytest.mark.asyncio
    async def test_max_token_count(self, store: MCPTokenStore) -> None:
        for i in range(MCP_TOKEN_MAX_COUNT):
            await store.async_create_token(
                name=f"Token {i}",
                permission_level=MCP_TOKEN_PERMISSION_READ_ONLY,
                created_by_user_id="user-1",
            )
        with pytest.raises(ValueError, match="Maximum"):
            await store.async_create_token(
                name="One too many",
                permission_level=MCP_TOKEN_PERMISSION_READ_ONLY,
                created_by_user_id="user-1",
            )

    @pytest.mark.asyncio
    async def test_invalid_permission_level(self, store: MCPTokenStore) -> None:
        with pytest.raises(ValueError, match="Invalid permission"):
            await store.async_create_token(
                name="Bad Token",
                permission_level="superadmin",
                created_by_user_id="user-1",
            )

    @pytest.mark.asyncio
    async def test_persistence_save_on_create(self, store: MCPTokenStore) -> None:
        await store.async_create_token(
            name="Token",
            permission_level=MCP_TOKEN_PERMISSION_ADMIN,
            created_by_user_id="user-1",
        )
        store._store.async_save.assert_called()

    @pytest.mark.asyncio
    async def test_persistence_save_on_revoke(self, store: MCPTokenStore) -> None:
        _, meta = await store.async_create_token(
            name="Token",
            permission_level=MCP_TOKEN_PERMISSION_ADMIN,
            created_by_user_id="user-1",
        )
        store._store.async_save.reset_mock()
        await store.async_revoke_token(meta["id"])
        store._store.async_save.assert_called()

    @pytest.mark.asyncio
    async def test_load_existing_data(self) -> None:
        hass = _make_hass()
        s = MCPTokenStore(hass)
        s._store = MagicMock()

        existing_data = {
            "tokens": {
                "tok-1": {
                    "id": "tok-1",
                    "name": "Existing",
                    "token_hash": _hash_token("smt_existing"),
                    "token_prefix": "smt_exis",
                    "permission_level": MCP_TOKEN_PERMISSION_ADMIN,
                    "allowed_tools": None,
                    "created_at": "2025-01-01T00:00:00+00:00",
                    "last_used_at": None,
                    "expires_at": None,
                    "created_by_user_id": "user-1",
                }
            }
        }
        s._store.async_load = AsyncMock(return_value=existing_data)
        await s.async_load()

        result = await s.async_validate_token("smt_existing")
        assert result is not None
        assert result["name"] == "Existing"
