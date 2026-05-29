"""ApprovalStore — persists the user's command-approval grants.

When the LLM proposes a service call outside the SAFE allowlist
(``light``, ``switch``, ``scene``…) but inside the REVIEW bucket
(``tts.*``, ``notify.*``, ``script.*``, ``lock.unlock``…), the user is
prompted in chat with Allow once / Session / Always / Deny.  Persistent
grants ("Always") land here; session grants live on the in-memory cache
keyed by ``session_id``.

Grant keys are scoped two ways:

- ``"<service>"`` — wildcard for that service. ``lock.unlock``
  approved this way unlocks ANY lock without re-prompting.
- ``"<service>:<entity_id>"`` — per-entity. ``lock.unlock:lock.front_door``
  unlocks only the front door; the back door still prompts.

``is_approved`` checks per-entity first, then the wildcard. This way an
older v1-style service-only grant keeps working (it's just a wildcard
under the new model), and the user can grant ``lock.unlock`` for the
front door without implicitly approving every lock.

Data layout (mirrors ``mcp_token_store.py`` for consistency)::

    {
        "grants": {
            "<key>": {
                "service": str,                # "tts.cloud_say"
                "entity_id": str | null,       # null = wildcard, str = per-entity
                "granted_at": str,             # ISO-8601
                "granted_by_user_id": str | null,
                "risk_level": str,             # "low" | "medium" | "high"
            }
        }
    }
"""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import APPROVAL_STORE_KEY, APPROVAL_STORE_VERSION

_LOGGER = logging.getLogger(__name__)


def _grant_key(service: str, entity_id: str | None) -> str:
    """Compose the ``grants`` dict key for a (service, entity_id) pair.

    ``entity_id is None`` produces the wildcard key. Both the per-entity
    and wildcard keys can coexist for the same service.
    """
    return f"{service}:{entity_id}" if entity_id else service


class ApprovalStore:
    """Persistent store for chat command approvals (Always-scope grants).

    Session-scoped grants are intentionally NOT persisted here — they live
    on ``_session_grants`` in memory keyed by ``session_id`` and disappear
    on integration unload, matching the user's mental model of "this
    conversation only".
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store = Store(hass, version=APPROVAL_STORE_VERSION, key=APPROVAL_STORE_KEY)
        self._data: dict[str, Any] | None = None
        # session_id → set of approved grant keys ("tts.cloud_say" or
        # "lock.unlock:lock.front_door"). Same key format as the
        # persistent store so ``is_approved`` can hit both with one
        # lookup helper.
        self._session_grants: dict[str, set[str]] = {}

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def async_load(self) -> None:
        raw = await self._store.async_load()
        if isinstance(raw, dict):
            self._data = raw
            self._data.setdefault("grants", {})
        else:
            self._data = {"grants": {}}

    async def _save(self) -> None:
        if self._data is not None:
            await self._store.async_save(self._data)

    # ── Lookups (hot path: called from command_policy on every command) ──

    def _has_key(self, key: str, session_id: str | None) -> bool:
        if self._data is not None and key in self._data["grants"]:
            return True
        return bool(session_id and key in self._session_grants.get(session_id, set()))

    def is_approved(
        self,
        service: str,
        *,
        entity_id: str | None = None,
        session_id: str | None = None,
    ) -> bool:
        """True iff *service* (optionally on *entity_id*) is approved.

        Resolution order:
        1. ``service:entity_id`` — per-entity grant (strictest).
        2. ``service`` — wildcard (all entities of this service).

        Per-entity wins so a user can later revoke just the front-door
        grant without losing a broader "all locks" grant.
        """
        if not service:
            return False
        if entity_id and self._has_key(_grant_key(service, entity_id), session_id):
            return True
        return self._has_key(_grant_key(service, None), session_id)

    def grant_source(
        self,
        service: str,
        *,
        entity_id: str | None = None,
        session_id: str | None = None,
    ) -> str | None:
        """Return ``"always"`` / ``"session"`` / ``None`` for the lookup.

        Mirrors ``is_approved`` resolution: per-entity grant wins over
        wildcard; persistent grant wins over session. Useful for the
        chat handler to surface "executed under prior approval" hints
        and for the audit log.
        """
        for key in (_grant_key(service, entity_id), _grant_key(service, None)):
            if self._data is not None and key in self._data["grants"]:
                return "always"
            if session_id and key in self._session_grants.get(session_id, set()):
                return "session"
        return None

    # ── Mutations ────────────────────────────────────────────────────────

    async def async_grant_always(
        self,
        service: str,
        *,
        risk_level: str,
        granted_by_user_id: str | None,
        entity_id: str | None = None,
    ) -> None:
        """Persist an approval for all future sessions.

        ``entity_id=None`` records a service-wide wildcard; passing an
        entity_id records a grant scoped to that one entity.
        """
        if self._data is None:
            await self.async_load()
        assert self._data is not None
        key = _grant_key(service, entity_id)
        self._data["grants"][key] = {
            "service": service,
            "entity_id": entity_id,
            "granted_at": datetime.now(UTC).isoformat(),
            "granted_by_user_id": granted_by_user_id,
            "risk_level": risk_level,
        }
        await self._save()
        _LOGGER.info(
            "Granted persistent command approval for %s (risk=%s) by user %s",
            key,
            risk_level,
            granted_by_user_id,
        )

    def grant_session(
        self,
        service: str,
        *,
        session_id: str,
        entity_id: str | None = None,
    ) -> None:
        """Approve a service (optionally scoped to one entity) for the
        remaining lifetime of *session_id*.

        Not persisted — vanishes on integration unload, matching the
        "this conversation only" mental model.
        """
        key = _grant_key(service, entity_id)
        self._session_grants.setdefault(session_id, set()).add(key)
        _LOGGER.info("Granted session-scoped approval for %s in session %s", key, session_id)

    async def async_revoke(self, grant_key: str) -> bool:
        """Remove a persistent grant by its full key.

        ``grant_key`` is the same string used in the data layout —
        ``"service"`` for wildcard, ``"service:entity_id"`` for the
        per-entity row. The Settings UI passes back whatever the
        ``list_grants`` call returned, so it always has the matching
        key in hand.
        """
        if self._data is None:
            await self.async_load()
        assert self._data is not None
        if grant_key not in self._data["grants"]:
            return False
        self._data["grants"].pop(grant_key)
        await self._save()
        _LOGGER.info("Revoked persistent command approval for %s", grant_key)
        return True

    async def async_list_grants(self) -> list[dict[str, Any]]:
        """Return all persistent grants for the Manage Approvals UI.

        Each entry carries its key, the matching ``service`` and
        optional ``entity_id``, and the audit metadata. Sorted by
        ``granted_at`` so the most recent approval lands at the end —
        matches what users expect from an "activity log" reading.
        """
        if self._data is None:
            await self.async_load()
        assert self._data is not None
        out: list[dict[str, Any]] = []
        for key, meta in self._data["grants"].items():
            entry = dict(meta)
            entry["key"] = key
            out.append(entry)
        return sorted(out, key=lambda g: g.get("granted_at", ""))

    def clear_session(self, session_id: str) -> None:
        """Drop session-scoped grants when a chat session is deleted."""
        self._session_grants.pop(session_id, None)
