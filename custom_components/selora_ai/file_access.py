"""Temporary access for one MCP client to write browser code into one www/ folder.

Browser code is never written to ``www/`` on an agent's say-so
(``config_files``): it runs with every viewer's session, and a ``confirmed``
flag is set by the same agent that writes the file. An agent may instead ASK;
a person grants it from a Home Assistant login — the panel, reached from a
push or persistent notification — never through a tool. See
``docs/dev/file-access.md``.

An installer's request, made in a Connect support session, is theirs alone:
nobody in the home is notified, only the HA user they signed in as during the
session sees and answers it, and the grant ends with that user when the
session ends. The homeowner already lent the hub out as an admin. What it
asked, who answered and every file it wrote is reported to Connect against the
session (``support_activity``).

Requests, grants and the record of what each grant wrote are stored, so a
restart (an agent may restart Home Assistant after installing something) keeps
an unexpired grant running and its summary complete. Expiry is an absolute
time checked on every use; the timers only end grants and lapse requests on
time for the notifications.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging
import re
from typing import TYPE_CHECKING, Any, Final
import uuid

from homeassistant.components import persistent_notification
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import slugify

from .code_findings import Finding, summarize
from .const import DOMAIN, PANEL_PATH
from .helpers import CallerCredential, sanitize_untrusted_text
from .support_activity import async_record as async_record_activity

if TYPE_CHECKING:
    from homeassistant.auth.models import User

_LOGGER = logging.getLogger(__name__)

STORE_KEY: Final = f"{DOMAIN}.file_access"
STORE_VERSION: Final = 1
EVENT_UPDATED: Final = "selora_ai_file_access_updated"

MINUTE_CHOICES: Final = (15, 30, 60)
REQUEST_TTL: Final = timedelta(minutes=15)
# Notifications a credential may cause per hour: every request pushes to the
# admins' phones, and a looping agent must not turn that into spam.
_MAX_REQUESTS_PER_HOUR: Final = 3
_MAX_WRITES_KEPT: Final = 200
_MAX_GRANTS_KEPT: Final = 20
_KEEP_ENDED: Final = timedelta(days=7)

# The auth provider an installer signs in with during a support session
# (selorabox-nix scripts/selorahomes-remote-auth.sh); its username is the
# email Connect's token carries.
_SUPPORT_PROVIDER: Final = "command_line"

_SEGMENT_RE: Final = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")
_MAX_DEPTH: Final = 6

# How an asking credential is named to a person: its kind and a name they
# would recognise, never an internal id. The panel has its own copy in
# translations/ (`file_access_agent_*`); these serve the notifications.
_AGENT_BY_LANG: Final[dict[str, dict[str, str]]] = {
    "en": {
        "selora_account": "An AI assistant connected with your Selora account",
        "selora_account_named": "An AI assistant signed in as {name}",
        "mcp_token": "An AI assistant using a Selora key",
        "mcp_token_named": "An AI assistant using the key “{name}”",
        "ha_token": "An AI assistant using a Home Assistant token",
        "ha_token_named": "An AI assistant using {name}'s Home Assistant access",
    },
    "fr": {
        "selora_account": "Un assistant IA connecté avec votre compte Selora",
        "selora_account_named": "Un assistant IA connecté en tant que {name}",
        "mcp_token": "Un assistant IA utilisant une clé Selora",
        "mcp_token_named": "Un assistant IA utilisant la clé « {name} »",
        "ha_token": "Un assistant IA utilisant un jeton Home Assistant",
        "ha_token_named": "Un assistant IA utilisant l'accès Home Assistant de {name}",
    },
    "de": {
        "selora_account": "Ein KI-Assistent, verbunden mit Ihrem Selora-Konto",
        "selora_account_named": "Ein KI-Assistent, angemeldet als {name}",
        "mcp_token": "Ein KI-Assistent mit einem Selora-Schlüssel",
        "mcp_token_named": "Ein KI-Assistent mit dem Schlüssel „{name}“",
        "ha_token": "Ein KI-Assistent mit einem Home-Assistant-Token",
        "ha_token_named": "Ein KI-Assistent mit dem Home-Assistant-Zugang von {name}",
    },
    "es": {
        "selora_account": "Un asistente de IA conectado con tu cuenta de Selora",
        "selora_account_named": "Un asistente de IA conectado como {name}",
        "mcp_token": "Un asistente de IA que usa una clave de Selora",
        "mcp_token_named": "Un asistente de IA que usa la clave «{name}»",
        "ha_token": "Un asistente de IA que usa un token de Home Assistant",
        "ha_token_named": "Un asistente de IA que usa el acceso a Home Assistant de {name}",
    },
    "it": {
        "selora_account": "Un assistente IA collegato al tuo account Selora",
        "selora_account_named": "Un assistente IA connesso come {name}",
        "mcp_token": "Un assistente IA che usa una chiave Selora",
        "mcp_token_named": "Un assistente IA che usa la chiave «{name}»",
        "ha_token": "Un assistente IA che usa un token di Home Assistant",
        "ha_token_named": "Un assistente IA che usa l'accesso a Home Assistant di {name}",
    },
}

_TITLE_BY_LANG: Final = {
    "en": "An agent asks to write browser code",
    "fr": "Un agent demande à écrire du code navigateur",
    "de": "Ein Agent möchte Browser-Code schreiben",
    "es": "Un agente pide escribir código de navegador",
    "it": "Un agente chiede di scrivere codice del browser",
}
_BODY_BY_LANG: Final = {
    "en": "{agent} wants to write files that run in your browser, in {folder}, for {minutes} min.",
    "fr": "{agent} veut écrire des fichiers exécutés dans votre navigateur, dans {folder}, pendant {minutes} min.",
    "de": "{agent} möchte Dateien, die in Ihrem Browser laufen, in {folder} schreiben, für {minutes} Min.",
    "es": "{agent} quiere escribir archivos que se ejecutan en tu navegador, en {folder}, durante {minutes} min.",
    "it": "{agent} vuole scrivere file eseguiti nel tuo browser, in {folder}, per {minutes} min.",
}
_REVIEW_BY_LANG: Final = {
    "en": "Review the request",
    "fr": "Examiner la demande",
    "de": "Anfrage prüfen",
    "es": "Revisar la solicitud",
    "it": "Esamina la richiesta",
}
_ENDED_TITLE_BY_LANG: Final = {
    "en": "Agent access ended",
    "fr": "Accès de l'agent terminé",
    "de": "Agent-Zugriff beendet",
    "es": "Acceso del agente finalizado",
    "it": "Accesso dell'agente terminato",
}
_ENDED_BODY_BY_LANG: Final = {
    "en": "{agent} could write browser code to {folder}. Files written: {count}.",
    "fr": "{agent} pouvait écrire du code navigateur dans {folder}. Fichiers écrits : {count}.",
    "de": "{agent} durfte Browser-Code in {folder} schreiben. Geschriebene Dateien: {count}.",
    "es": "{agent} podía escribir código de navegador en {folder}. Archivos escritos: {count}.",
    "it": "{agent} poteva scrivere codice del browser in {folder}. File scritti: {count}.",
}
_SEE_FILES_BY_LANG: Final = {
    "en": "See what was written",
    "fr": "Voir ce qui a été écrit",
    "de": "Ansehen, was geschrieben wurde",
    "es": "Ver lo que se escribió",
    "it": "Vedi cosa è stato scritto",
}


class FileAccessError(Exception):
    """A refused request or decision; ``code`` is the websocket error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _lang(hass: HomeAssistant) -> str:
    from .llm_client.command_policy import _normalize_lang  # noqa: PLC0415

    lang = _normalize_lang(hass.config.language)
    return lang if lang in _TITLE_BY_LANG else "en"


def agent_label(kind: str | None, name: str | None, lang: str = "en") -> str:
    """The asking credential, named the way a person would recognise it."""
    phrases = _AGENT_BY_LANG.get(lang, _AGENT_BY_LANG["en"])
    kind = kind if kind in ("selora_account", "mcp_token", "ha_token") else "mcp_token"
    if name:
        return phrases[f"{kind}_named"].format(name=name)
    return phrases[kind]


def _record_label(record: dict[str, Any], lang: str) -> str:
    if record.get("agent_client"):
        return str(record["agent_client"])
    if "agent_kind" not in record:  # stored before kinds were
        return str(record.get("agent", ""))
    return agent_label(record["agent_kind"], record.get("agent_name"), lang)


def normalize_folder(raw: str) -> str:
    """``www/<folder>`` with plain segments, or FileAccessError.

    A subfolder, never ``www`` itself: the grant is meant to cover one card's
    files, not everything ``/local/`` serves.
    """
    folder = str(raw or "").strip().strip("/")
    parts = folder.split("/") if folder else []
    if len(parts) < 2 or parts[0] != "www":
        raise FileAccessError(
            "invalid_folder", "Name a folder inside www/, e.g. www/my-card — never www itself."
        )
    if len(parts) > _MAX_DEPTH or not all(_SEGMENT_RE.fullmatch(p) for p in parts):
        raise FileAccessError(
            "invalid_folder",
            "A folder is plain names — letters, digits, '.', '_' and '-', none starting "
            "with a dot.",
        )
    return folder


def _in_folder(rel: str, folder: str) -> bool:
    return rel.startswith(folder + "/")


_PRIVATE: Final = ("credential", "dismissed", "user_id", "support_email")


def _public(record: dict[str, Any]) -> dict[str, Any]:
    """A request or grant as the panel sees it — without the credential key."""
    return {k: v for k, v in record.items() if k not in _PRIVATE}


def _support_email(user: User) -> str | None:
    """The email a support-session user signed in with, or None for anyone else."""
    if user.is_owner or user.system_generated:
        return None
    for credential in user.credentials:
        username = credential.data.get("username")
        if credential.auth_provider_type == _SUPPORT_PROVIDER and isinstance(username, str):
            return username.strip().lower()
    return None


def visible_to(record: dict[str, Any], user: User) -> bool:
    """Whether ``user`` sees and answers this request or grant.

    The home's own requests are every admin's; an installer's is only the
    support user signed in with the installer's email.
    """
    target = record.get("support_email")
    return target is None or _support_email(user) == target


class FileAccessManager:
    """Requests, grants and their write records for browser code in www/."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, STORE_KEY)
        self._requests: dict[str, dict[str, Any]] = {}
        self._grants: dict[str, dict[str, Any]] = {}
        # credential key → times it asked, for the hourly cap. Kept apart from the
        # requests because an answered request is pruned and must still count.
        self._asks: dict[str, list[str]] = {}
        self._timers: dict[str, Callable[[], None]] = {}
        self._unsub_user_removed: Callable[[], None] | None = None
        # request id → the task pushing it to the phones, which clearing waits
        # on: a clear that overtook its push would leave the phone's alert up.
        self._pushes: dict[str, asyncio.Task[Any]] = {}
        self._unloaded = False

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def async_load(self) -> None:
        """Load stored state; end what lapsed while Home Assistant was down."""
        data = await self._store.async_load() or {}
        self._requests = dict(data.get("requests") or {})
        self._grants = dict(data.get("grants") or {})
        self._asks = {k: list(v) for k, v in (data.get("asks") or {}).items()}
        now = _now()
        changed = False
        for request in list(self._requests.values()):
            if request.get("status") != "pending":
                continue
            expires = _parse(request.get("expires_at"))
            if expires is None or expires <= now:
                self._lapse_request(request)
                changed = True
            else:
                self._arm(request["id"], expires, self._on_request_timer)
        for grant in list(self._grants.values()):
            if grant.get("ended_at"):
                continue
            expires = _parse(grant.get("expires_at"))
            if expires is None or expires <= now:
                self._end_grant(grant, "expired")
                changed = True
            else:
                self._arm(grant["id"], expires, self._on_grant_timer)
        if self._prune() or changed:
            await self._save()
        self._unsub_user_removed = self._hass.bus.async_listen(
            "user_removed", self._on_user_removed
        )

    async def async_unload(self) -> None:
        """Cancel the timers before anything else; state is already saved."""
        self._unloaded = True
        if self._unsub_user_removed is not None:
            self._unsub_user_removed()
            self._unsub_user_removed = None
        for cancel in self._timers.values():
            cancel()
        self._timers.clear()

    async def _save(self) -> None:
        await self._store.async_save(
            {"requests": self._requests, "grants": self._grants, "asks": self._asks}
        )

    @callback
    def _changed(self) -> None:
        self._hass.bus.async_fire(EVENT_UPDATED, {})

    def _arm(self, record_id: str, when: datetime, handler: Callable[[str], None]) -> None:
        if (cancel := self._timers.pop(record_id, None)) is not None:
            cancel()
        delay = max(0.0, (when - _now()).total_seconds())

        @callback
        def _fire(_now_: datetime) -> None:
            self._timers.pop(record_id, None)
            if not self._unloaded:
                handler(record_id)

        self._timers[record_id] = async_call_later(self._hass, delay, _fire)

    def _disarm(self, record_id: str) -> None:
        if (cancel := self._timers.pop(record_id, None)) is not None:
            cancel()

    @callback
    def _on_request_timer(self, request_id: str) -> None:
        request = self._requests.get(request_id)
        if request is None or request.get("status") != "pending":
            return
        self._lapse_request(request)
        self._hass.async_create_background_task(self._save(), "selora_file_access_save")
        self._changed()

    @callback
    def _on_grant_timer(self, grant_id: str) -> None:
        grant = self._grants.get(grant_id)
        if grant is None or grant.get("ended_at"):
            return
        self._end_grant(grant, "expired")
        self._hass.async_create_background_task(self._save(), "selora_file_access_save")
        self._changed()

    @callback
    def _on_user_removed(self, event: Event) -> None:
        """End an installer's grant when the support session's user goes."""
        user_id = event.data.get("user_id")
        ended = False
        for grant in self._grants.values():
            if (
                grant.get("support_email")
                and grant.get("user_id") == user_id
                and not grant.get("ended_at")
            ):
                self._disarm(grant["id"])
                self._end_grant(grant, "session_ended")
                ended = True
        if ended and not self._unloaded:
            self._hass.async_create_background_task(self._save(), "selora_file_access_save")
            self._changed()

    async def _report(self, record: dict[str, Any], event_type: str, **data: Any) -> None:
        """Queue an event of an installer's record for Connect; the home's own
        records belong to no support session and are not reported."""
        if (session := record.get("support_session")) is None:
            return
        await async_record_activity(
            self._hass,
            session,
            event_type,
            {"request_id": record["id"], "agent": record.get("agent"), **data},
        )

    def _prune(self) -> bool:
        """Drop answered requests and old dismissed grants; True if any went."""
        before = len(self._requests) + len(self._grants)
        self._requests = {
            rid: r for rid, r in self._requests.items() if r.get("status") == "pending"
        }
        cutoff = _now() - _KEEP_ENDED
        kept = {
            gid: g
            for gid, g in self._grants.items()
            if not g.get("ended_at")
            or not g.get("dismissed")
            or (_parse(g.get("ended_at")) or cutoff) > cutoff
        }
        if len(kept) > _MAX_GRANTS_KEPT:
            newest = sorted(kept.values(), key=lambda g: g.get("approved_at") or "", reverse=True)
            kept = {g["id"]: g for g in newest[:_MAX_GRANTS_KEPT]}
        self._grants = kept
        return len(self._requests) + len(self._grants) != before

    # ── Queries ──────────────────────────────────────────────────────────

    def grant_for(self, credential: CallerCredential | None, rel: str) -> dict[str, Any] | None:
        """The live grant letting ``credential`` write ``rel``, if there is one."""
        if credential is None:
            return None
        now = _now()
        for grant in self._grants.values():
            if (
                grant["credential"] == credential.key
                and not grant.get("ended_at")
                and (_parse(grant.get("expires_at")) or now) > now
                and _in_folder(rel, grant["folder"])
            ):
                return grant
        return None

    def pending_for(self, credential: CallerCredential | None, rel: str) -> dict[str, Any] | None:
        """``credential``'s unanswered request covering ``rel``, if any."""
        if credential is None:
            return None
        for request in self._requests.values():
            if (
                request["credential"] == credential.key
                and request.get("status") == "pending"
                and _in_folder(rel, request["folder"])
            ):
                return request
        return None

    def snapshot(self, viewer: User | None = None) -> dict[str, Any]:
        """Pending requests, and grants live or ended but not yet dismissed —
        those ``viewer`` may see, when one is given."""

        def shown(record: dict[str, Any]) -> bool:
            return viewer is None or visible_to(record, viewer)

        return {
            "requests": [
                _public(r)
                for r in self._requests.values()
                if r.get("status") == "pending" and shown(r)
            ],
            "grants": [
                _public(g)
                for g in sorted(
                    self._grants.values(), key=lambda g: g.get("approved_at") or "", reverse=True
                )
                if not g.get("dismissed") and shown(g)
            ],
        }

    def can_see(self, record_id: str, user: User) -> bool:
        """Whether ``user`` may act on this request or grant; an unknown id is
        left to the action to refuse."""
        record = self._grants.get(record_id) or self._requests.get(record_id)
        return record is None or visible_to(record, user)

    # ── Agent side ───────────────────────────────────────────────────────

    async def async_request(
        self, credential: CallerCredential | None, folder: str, reason: str, minutes: int
    ) -> dict[str, Any]:
        """Ask for access; notifies the admins. Repeating a pending ask is free."""
        if credential is None:
            raise FileAccessError(
                "not_mcp", "Only an MCP client can ask for this; Selora's own chat cannot."
            )
        folder = normalize_folder(folder)
        support_email = (credential.email or "").strip().lower() or None
        if credential.support_session is not None and support_email is None:
            raise FileAccessError(
                "no_email",
                "This support-session token names no email, so no one could approve it.",
            )
        minutes = min(max(int(minutes or 60), MINUTE_CHOICES[0]), MINUTE_CHOICES[-1])
        if grant := self.grant_for(credential, folder + "/x"):
            return {
                "status": "granted",
                "folder": grant["folder"],
                "expires_at": grant["expires_at"],
            }
        for request in self._requests.values():
            if (
                request["credential"] == credential.key
                and request.get("status") == "pending"
                and request["folder"] == folder
            ):
                return await self._pending_result(request)

        now = _now()
        hour_ago = _iso(now - timedelta(hours=1))
        asks = [t for t in self._asks.get(credential.key, []) if t > hour_ago]
        if len(asks) >= _MAX_REQUESTS_PER_HOUR:
            raise FileAccessError(
                "rate_limited",
                "Too many access requests in the last hour. Ask the user to look at the "
                "Selora AI panel, or try again later.",
            )
        self._asks = {
            key: kept
            for key, times in self._asks.items()
            if (kept := [t for t in times if t > hour_ago])
        }
        self._asks[credential.key] = [*asks, _iso(now)]

        request_id = uuid.uuid4().hex
        name = sanitize_untrusted_text(credential.name, 80) if credential.name else None
        client = sanitize_untrusted_text(credential.client, 40) if credential.client else None
        request: dict[str, Any] = {
            "id": request_id,
            "credential": credential.key,
            "agent": client or agent_label(credential.kind, name),
            "agent_kind": credential.kind,
            "agent_name": name,
            "agent_client": client,
            "folder": folder,
            "reason": sanitize_untrusted_text(reason, 300),
            "minutes": minutes,
            "created_at": _iso(now),
            "expires_at": _iso(now + REQUEST_TTL),
            "status": "pending",
        }
        if credential.support_session is not None:
            request["support_session"] = credential.support_session
            request["support_email"] = support_email
        self._requests[request_id] = request
        await self._save()
        self._arm(request_id, now + REQUEST_TTL, self._on_request_timer)
        _LOGGER.warning(
            "Selora AI: %s asks to write browser code to %s for %d min",
            request["agent"],
            folder,
            minutes,
        )
        if not request.get("support_email"):
            await self._notify_request(request)
        await self._report(
            request,
            "access_requested",
            folder=folder,
            reason=request["reason"],
            minutes=minutes,
        )
        self._changed()
        return await self._pending_result(request)

    async def _pending_result(self, request: dict[str, Any]) -> dict[str, Any]:
        result = {
            "status": "pending",
            "request_id": request["id"],
            "folder": request["folder"],
            "expires_at": request["expires_at"],
        }
        if not request.get("support_email"):
            result["next"] = (
                "The admins got a notification. Tell the user to approve it from the "
                "notification or the Selora AI panel, then retry the write. Nothing you "
                "send can approve it."
            )
            return result
        from .frigate.media import async_link_base  # noqa: PLC0415

        path = f"/{PANEL_PATH}?file_access={request['id']}"
        base = await async_link_base(self._hass)
        result["approve_url"] = f"{base}{path}" if base else path
        result["next"] = (
            "This is a support session, so nobody in the home was notified. Give the "
            "user approve_url: they approve it there, signed in to Home Assistant through "
            "remote support, then retry the write. Nothing you send can approve it."
        )
        return result

    @staticmethod
    def has_room(grant: dict[str, Any], rel: str) -> bool:
        """Whether a write to ``rel`` can still be recorded under ``grant``.

        A full record refuses new files rather than dropping old entries:
        otherwise a flood of harmless files would push a flagged one out.
        """
        writes = grant.get("writes", [])
        return len(writes) < _MAX_WRITES_KEPT or any(w["file"] == rel for w in writes)

    async def async_record_write(
        self,
        grant: dict[str, Any],
        rel: str,
        action: str,
        size: int,
        findings: list[Finding],
        sha256: str | None = None,
    ) -> None:
        """Note a write made under ``grant``; a file's latest write replaces its last."""
        writes: list[dict[str, Any]] = grant.setdefault("writes", [])
        earlier = next((w for w in writes if w["file"] == rel), None)
        if earlier is not None:
            writes.remove(earlier)
            if earlier["action"] == "created" and action == "replaced":
                action = "created"
            # A clean rewrite or a delete must not erase what an earlier
            # version did: it may already have run in a browser.
            now_found = {(f["kind"], f["detail"]) for f in findings}
            findings = [
                *findings,
                *(
                    {**f, "earlier": True}
                    for f in earlier.get("findings", [])
                    if (f["kind"], f["detail"]) not in now_found
                ),
            ]
        writes.append(
            {
                "file": rel,
                "url": "/local/" + rel.removeprefix("www/") if action != "deleted" else None,
                "size": size,
                "at": _iso(_now()),
                "action": action,
                "times": (earlier.get("times", 1) + 1) if earlier else 1,
                "sha256": sha256,
                "findings": findings,
            }
        )
        _LOGGER.warning(
            "Selora AI: %s %s %s under a granted access (%s)",
            grant["agent"],
            action,
            rel,
            summarize(findings),
        )
        await self._save()
        self._changed()
        # This version's findings: Connect keeps every version, so the
        # earlier ones were reported with their own write.
        found = [f for f in findings if not f.get("earlier")]
        await self._report(
            grant,
            "file_written",
            file=rel,
            action=action,
            size=size,
            sha256=sha256,
            summary=summarize(found),
            findings=found,
        )

    # ── Person side ──────────────────────────────────────────────────────

    async def async_decide(
        self, request_id: str, approve: bool, minutes: int | None, user_id: str, user_name: str
    ) -> dict[str, Any] | None:
        """Approve (returning the grant) or deny (returning None) a request."""
        request = self._requests.get(request_id)
        if request is None:
            raise FileAccessError("not_found", "That request does not exist any more.")
        if request.get("status") != "pending":
            raise FileAccessError("expired", "That request was already answered or has lapsed.")
        expires = _parse(request.get("expires_at"))
        if expires is None or expires <= _now():
            self._lapse_request(request)
            await self._save()
            self._changed()
            raise FileAccessError("expired", "That request has lapsed; the agent can ask again.")

        self._disarm(request_id)
        self._clear_request_notifications(request)
        if not approve:
            request["status"] = "denied"
            self._prune()
            await self._save()
            self._changed()
            _LOGGER.warning("Selora AI: %s denied %s", user_name, request["agent"])
            await self._report(request, "access_denied", answered_by=user_name)
            return None

        chosen = minutes if minutes in MINUTE_CHOICES else min(request["minutes"], 60)
        now = _now()
        grant: dict[str, Any] = {
            "id": request_id,
            "credential": request["credential"],
            "agent": request["agent"],
            "agent_kind": request.get("agent_kind"),
            "agent_name": request.get("agent_name"),
            "agent_client": request.get("agent_client"),
            "folder": request["folder"],
            "reason": request["reason"],
            "created_at": request["created_at"],
            "approved_by": sanitize_untrusted_text(user_name, 80),
            "user_id": user_id,
            "approved_at": _iso(now),
            "expires_at": _iso(now + timedelta(minutes=chosen)),
            "ended_at": None,
            "ended_reason": None,
            "writes": [],
        }
        if request.get("support_email"):
            grant["support_session"] = request.get("support_session")
            grant["support_email"] = request["support_email"]
        request["status"] = "approved"
        self._grants[request_id] = grant
        self._prune()
        await self._save()
        self._arm(request_id, now + timedelta(minutes=chosen), self._on_grant_timer)
        _LOGGER.warning(
            "Selora AI: %s let %s write browser code to %s for %d min",
            user_name,
            grant["agent"],
            grant["folder"],
            chosen,
        )
        self._changed()
        await self._report(
            grant,
            "access_granted",
            folder=grant["folder"],
            minutes=chosen,
            answered_by=grant["approved_by"],
            expires_at=grant["expires_at"],
        )
        return _public(grant)

    async def async_revoke(self, grant_id: str) -> dict[str, Any]:
        """End a grant now."""
        grant = self._grants.get(grant_id)
        if grant is None:
            raise FileAccessError("not_found", "That access does not exist any more.")
        if not grant.get("ended_at"):
            self._disarm(grant_id)
            self._end_grant(grant, "revoked")
            await self._save()
            self._changed()
        return _public(grant)

    async def async_dismiss(self, grant_id: str) -> None:
        """Hide an ended grant's summary."""
        grant = self._grants.get(grant_id)
        if grant is None:
            raise FileAccessError("not_found", "That access does not exist any more.")
        if not grant.get("ended_at"):
            raise FileAccessError("still_active", "Revoke the access before dismissing it.")
        grant["dismissed"] = True
        persistent_notification.async_dismiss(self._hass, f"selora_file_access_ended_{grant_id}")
        self._prune()
        await self._save()
        self._changed()

    # ── Transitions ──────────────────────────────────────────────────────

    def _lapse_request(self, request: dict[str, Any]) -> None:
        request["status"] = "expired"
        self._disarm(request["id"])
        self._clear_request_notifications(request)

    def _end_grant(self, grant: dict[str, Any], reason: str) -> None:
        grant["ended_at"] = _iso(_now())
        grant["ended_reason"] = reason
        _LOGGER.warning(
            "Selora AI: browser-code access for %s to %s ended (%s)",
            grant["agent"],
            grant["folder"],
            reason,
        )
        if grant.get("support_email"):
            # The installer's own record: no alert in the home; the panel, the
            # log and Connect's session record keep it.
            if not self._unloaded:
                self._hass.async_create_background_task(
                    self._report(
                        grant, "access_ended", reason=reason, files=len(grant.get("writes", []))
                    ),
                    "selora_file_access_report",
                )
            return
        lang = _lang(self._hass)
        lines = [
            _ENDED_BODY_BY_LANG[lang].format(
                agent=_record_label(grant, lang),
                folder=grant["folder"],
                count=len(grant.get("writes", [])),
            )
        ]
        lines.extend(
            f"- {w['file']} ({w['action']}): {summarize(w.get('findings', []))}"
            for w in grant.get("writes", [])[:10]
        )
        lines.append(f"\n[{_SEE_FILES_BY_LANG[lang]}](/{PANEL_PATH}?file_access={grant['id']})")
        persistent_notification.async_create(
            self._hass,
            "\n".join(lines),
            title=_ENDED_TITLE_BY_LANG[lang],
            notification_id=f"selora_file_access_ended_{grant['id']}",
        )

    # ── Notifications ────────────────────────────────────────────────────

    async def _admin_phone_services(self) -> list[str]:
        """``notify`` services of the companion apps of active admin users."""
        services = self._hass.services.async_services().get("notify", {})
        phones = []
        for entry in self._hass.config_entries.async_entries("mobile_app"):
            name = entry.data.get("device_name")
            user_id = entry.data.get("user_id")
            if not name or not user_id:
                continue
            user = await self._hass.auth.async_get_user(user_id)
            if user is None or not user.is_admin or not user.is_active:
                continue
            service = f"mobile_app_{slugify(str(name))}"
            if service in services:
                phones.append(service)
        return phones

    async def _notify_request(self, request: dict[str, Any]) -> None:
        lang = _lang(self._hass)
        link = f"/{PANEL_PATH}?file_access={request['id']}"
        title = _TITLE_BY_LANG[lang]
        body = _BODY_BY_LANG[lang].format(
            agent=_record_label(request, lang), folder=request["folder"], minutes=request["minutes"]
        )
        persistent_notification.async_create(
            self._hass,
            f"{body}\n\n[{_REVIEW_BY_LANG[lang]}]({link})",
            title=title,
            notification_id=f"selora_file_access_{request['id']}",
        )
        data = {
            "title": title,
            "message": body,
            "data": {
                # iOS opens `url`, Android `clickAction`.
                "url": link,
                "clickAction": link,
                "tag": f"selora_file_access_{request['id']}",
                "group": "selora_file_access",
                "push": {"interruption-level": "time-sensitive"},
            },
        }
        services = await self._admin_phone_services()
        if services:
            self._pushes[request["id"]] = self._hass.async_create_background_task(
                self._push_all(services, data),
                "selora_file_access_push",
            )

    async def _push_all(self, services: list[str], data: dict[str, Any]) -> None:
        await asyncio.gather(*(self._push(service, data) for service in services))

    async def _push(self, service: str, data: dict[str, Any]) -> None:
        try:
            await self._hass.services.async_call("notify", service, data, blocking=True)
        except HomeAssistantError as exc:  # one phone failing must not stop the rest
            _LOGGER.warning("Selora AI: could not notify %s: %s", service, exc)

    def _clear_request_notifications(self, request: dict[str, Any]) -> None:
        persistent_notification.async_dismiss(self._hass, f"selora_file_access_{request['id']}")
        if not self._unloaded:
            self._hass.async_create_background_task(
                self._clear_phones(request["id"]), "selora_file_access_clear"
            )

    async def _clear_phones(self, request_id: str) -> None:
        if (push := self._pushes.pop(request_id, None)) is not None:
            await push
        tag = f"selora_file_access_{request_id}"
        for service in await self._admin_phone_services():
            await self._push(service, {"message": "clear_notification", "data": {"tag": tag}})
