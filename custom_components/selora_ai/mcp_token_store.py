"""MCPTokenStore — manages locally-created MCP API tokens.

Tokens are opaque random strings (prefixed ``smt_``) that grant access to
Selora MCP tools with configurable permission levels.  Only SHA-256 hashes
are persisted — the plaintext token is returned exactly once at creation time.

Data layout::

    {
        "tokens": {
            "<token_id>": {
                "id": str,
                "name": str,
                "token_hash": str,          # "sha256:<hex>"
                "token_prefix": str,        # first 8 chars for display
                "permission_level": "read_only" | "admin" | "custom",
                "allowed_tools": [str] | null,
                "created_at": str,          # ISO-8601
                "last_used_at": str | null, # ISO-8601
                "expires_at": str | null,   # ISO-8601
                "created_by_user_id": str,
            }
        }
    }
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
import hmac
import logging
import secrets
from typing import Any
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    MCP_TOKEN_MAX_COUNT,
    MCP_TOKEN_PREFIX,
    MCP_TOKEN_STORE_KEY,
    MCP_TOKEN_STORE_VERSION,
    MCP_TOKEN_VALID_PERMISSIONS,
)

_LOGGER = logging.getLogger(__name__)


def _hash_token(raw_token: str) -> str:
    """Return ``sha256:<hex>`` digest of a raw token string."""
    return "sha256:" + hashlib.sha256(raw_token.encode()).hexdigest()


_LAST_USED_SAVE_DELAY = 30  # seconds — debounce last_used_at writes


class MCPTokenStore:
    """Persistent store for Selora MCP tokens."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store = Store(hass, version=MCP_TOKEN_STORE_VERSION, key=MCP_TOKEN_STORE_KEY)
        self._data: dict[str, Any] | None = None
        self._hash_index: dict[str, str] = {}  # token_hash → token_id
        self._save_timer: asyncio.TimerHandle | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def async_load(self) -> None:
        """Load persisted tokens and build the hash index."""
        raw = await self._store.async_load()
        if isinstance(raw, dict):
            self._data = raw
            self._data.setdefault("tokens", {})
        else:
            self._data = {"tokens": {}}
        self._rebuild_hash_index()

    def _rebuild_hash_index(self) -> None:
        self._hash_index = {
            meta["token_hash"]: tid for tid, meta in (self._data or {}).get("tokens", {}).items()
        }

    async def _save(self) -> None:
        if self._data is not None:
            await self._store.async_save(self._data)

    def _schedule_deferred_save(self) -> None:
        """Schedule a save after a delay, coalescing multiple updates."""
        if self._save_timer is not None:
            self._save_timer.cancel()
        loop = self._hass.loop
        self._save_timer = loop.call_later(
            _LAST_USED_SAVE_DELAY,
            lambda: self._hass.async_create_task(self._deferred_save()),
        )

    async def _deferred_save(self) -> None:
        self._save_timer = None
        await self._save()

    # ── CRUD ─────────────────────────────────────────────────────────────

    async def async_create_token(
        self,
        name: str,
        permission_level: str,
        *,
        allowed_tools: list[str] | None = None,
        expires_at: str | None = None,
        created_by_user_id: str,
    ) -> tuple[str, dict[str, Any]]:
        """Create a new MCP token.

        Returns ``(plaintext_token, metadata_dict)``.  The plaintext token is
        **not** persisted and must be shown to the user exactly once.
        """
        if self._data is None:
            await self.async_load()
        assert self._data is not None

        tokens = self._data["tokens"]
        if len(tokens) >= MCP_TOKEN_MAX_COUNT:
            msg = f"Maximum of {MCP_TOKEN_MAX_COUNT} MCP tokens reached"
            raise ValueError(msg)

        if permission_level not in MCP_TOKEN_VALID_PERMISSIONS:
            msg = f"Invalid permission level: {permission_level}"
            raise ValueError(msg)

        raw_token = MCP_TOKEN_PREFIX + secrets.token_urlsafe(32)
        token_hash = _hash_token(raw_token)
        token_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()

        meta: dict[str, Any] = {
            "id": token_id,
            "name": name,
            "token_hash": token_hash,
            "token_prefix": raw_token[:8],
            "permission_level": permission_level,
            "allowed_tools": allowed_tools,
            "created_at": now,
            "last_used_at": None,
            "expires_at": expires_at,
            "created_by_user_id": created_by_user_id,
        }
        tokens[token_id] = meta
        self._hash_index[token_hash] = token_id
        await self._save()

        _LOGGER.info("Created MCP token %s (%s) for user %s", token_id, name, created_by_user_id)
        return raw_token, meta

    async def async_list_tokens(self) -> list[dict[str, Any]]:
        """Return metadata for all tokens (hashes excluded)."""
        if self._data is None:
            await self.async_load()
        assert self._data is not None

        result = []
        for meta in self._data["tokens"].values():
            entry = {k: v for k, v in meta.items() if k != "token_hash"}
            result.append(entry)
        return result

    async def async_revoke_token(self, token_id: str) -> bool:
        """Revoke a token by ID.  Returns ``True`` if the token existed."""
        if self._data is None:
            await self.async_load()
        assert self._data is not None

        meta = self._data["tokens"].pop(token_id, None)
        if meta is None:
            return False

        self._hash_index.pop(meta["token_hash"], None)
        await self._save()
        _LOGGER.info("Revoked MCP token %s (%s)", token_id, meta.get("name", "?"))
        return True

    # ── Validation (called on every MCP request) ─────────────────────────

    async def async_validate_token(self, raw_token: str) -> dict[str, Any] | None:
        """Validate a raw token string.

        Returns token metadata if valid, ``None`` otherwise.
        Updates ``last_used_at`` on success.
        """
        if self._data is None:
            await self.async_load()
        assert self._data is not None

        incoming_hash = _hash_token(raw_token)

        # Timing-safe lookup: compare against each stored hash
        matched_id: str | None = None
        for stored_hash, tid in self._hash_index.items():
            if hmac.compare_digest(incoming_hash, stored_hash):
                matched_id = tid
                break

        if matched_id is None:
            return None

        meta = self._data["tokens"].get(matched_id)
        if meta is None:
            return None

        # Check expiration
        expires_at = meta.get("expires_at")
        if expires_at is not None:
            try:
                exp_dt = datetime.fromisoformat(expires_at)
                if datetime.now(UTC) > exp_dt:
                    _LOGGER.debug("MCP token %s has expired", matched_id)
                    return None
            except (ValueError, TypeError):
                return None

        # Update last_used_at with debounced persistence
        meta["last_used_at"] = datetime.now(UTC).isoformat()
        self._schedule_deferred_save()
        return meta
