"""Search, install, update and remove HACS repositories, in-process.

HACS has no service API: its own panel drives it over websocket commands
(``hacs/repositories/list``, ``hacs/repository/download`` …). Selora runs inside
Home Assistant, so it calls the handlers HACS registered for those commands
directly, with a stand-in connection that collects the reply — the same
command table ``helpers.registered_storage_collection`` reads. That is HACS's
frontend protocol, the most stable surface it has, rather than its private
Python objects; the commands' schemas still validate every message.

* **Only the commands in ``_COMMANDS``.** This is not a generic websocket
  client: an MCP tool picks an operation, never a command name.
* **Risk by category.** A card or theme is JavaScript/CSS from GitHub that runs
  in every browser — confirmed like an external dashboard resource. An
  integration (or AppDaemon app, python_script) is Python that runs inside
  Home Assistant with full access once it is restarted, and adding a custom
  repository trusts an arbitrary GitHub repo — confirmed with that said
  plainly. Removing runs at once, like other removals over MCP.
* **Everything HACS returns from GitHub is untrusted text** — names,
  descriptions, topics — and is sanitized on the way out.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Final

from homeassistant.core import Context

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LIST: Final = "hacs/repositories/list"
_INFO: Final = "hacs/repository/info"
_DOWNLOAD: Final = "hacs/repository/download"
_REMOVE: Final = "hacs/repository/remove"
_ADD: Final = "hacs/repositories/add"
_COMMANDS: Final = frozenset({_LIST, _INFO, _DOWNLOAD, _REMOVE, _ADD})

# A download fetches a release from GitHub; info can refresh one first.
_TIMEOUTS: Final = {_LIST: 30.0, _INFO: 60.0, _DOWNLOAD: 180.0, _REMOVE: 60.0, _ADD: 120.0}

FRONTEND_CATEGORIES: Final = frozenset({"plugin", "theme"})
CATEGORIES: Final = ("integration", "plugin", "theme", "appdaemon", "python_script", "template")
_MAX_RESULTS: Final = 25


class HacsError(Exception):
    """HACS is missing, refused a command, or did not answer."""


class _AdminUser:
    """The caller HACS sees: the MCP tools that reach this are admin-only."""

    id = "selora_ai_mcp"
    is_admin = True
    is_owner = False
    name = "Selora AI"


class _InProcessConnection:
    """Just enough of ``ActiveConnection`` for a handler to reply to.

    HACS handlers answer with ``send_message(result_message(...))`` or
    ``send_error``; ``async_response`` reports an exception through
    ``async_handle_exception``. Each settles the one future the caller awaits.
    """

    def __init__(self, reply: asyncio.Future[Any]) -> None:
        self.user = _AdminUser()
        self._reply = reply

    def context(self, msg: dict[str, Any]) -> Context:
        return Context(user_id=None)

    def send_message(self, message: Any) -> None:
        if self._reply.done() or not isinstance(message, dict):
            return
        if message.get("success", True):
            self._reply.set_result(message.get("result"))
        else:
            error = message.get("error") or {}
            self._reply.set_exception(HacsError(str(error.get("message") or "HACS refused it.")))

    def send_result(self, msg_id: int, result: Any | None = None) -> None:
        if not self._reply.done():
            self._reply.set_result(result)

    def send_error(self, msg_id: int, code: str, message: str, *args: Any, **kwargs: Any) -> None:
        if not self._reply.done():
            self._reply.set_exception(HacsError(f"{message} ({code})"))

    def async_handle_exception(self, msg: dict[str, Any], err: Exception) -> None:
        if not self._reply.done():
            self._reply.set_exception(HacsError(f"HACS failed: {err}"))


async def _call(hass: HomeAssistant, command: str, **fields: Any) -> Any:
    """Send one allowlisted HACS command and wait for its reply."""
    from homeassistant.components.websocket_api import (  # noqa: PLC0415
        DOMAIN as WEBSOCKET_DOMAIN,
    )
    import voluptuous as vol  # noqa: PLC0415

    if command not in _COMMANDS:
        raise HacsError(f"{command} is not a HACS command Selora sends.")
    handlers = hass.data.get(WEBSOCKET_DOMAIN)
    registered = handlers.get(command) if isinstance(handlers, dict) else None
    if not registered:
        raise HacsError(
            "HACS is not installed (or not loaded yet), so community cards, themes and "
            "integrations cannot be managed from here."
        )
    handler, schema = registered
    msg: dict[str, Any] = {"id": 1, "type": command, **fields}
    if schema is not None:
        try:
            msg = schema(msg)
        except vol.Invalid as exc:
            raise HacsError(f"HACS would refuse that request: {exc}") from exc
    reply: asyncio.Future[Any] = hass.loop.create_future()
    handler(hass, _InProcessConnection(reply), msg)
    try:
        return await asyncio.wait_for(reply, _TIMEOUTS[command])
    except TimeoutError as exc:
        raise HacsError(f"HACS did not answer {command} in time.") from exc


def _clean(text: Any, limit: int = 300) -> str:
    return sanitize_untrusted_text(str(text or ""), limit)


def _summary(repo: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(repo.get("id") or ""),
        "full_name": _clean(repo.get("full_name"), 120),
        "name": _clean(repo.get("name"), 120),
        "category": str(repo.get("category") or ""),
        "description": _clean(repo.get("description")),
        "installed": bool(repo.get("installed")),
        **(
            {"installed_version": _clean(repo.get("installed_version"), 40)}
            if repo.get("installed")
            else {}
        ),
        "available_version": _clean(repo.get("available_version"), 40),
        "pending_upgrade": bool(repo.get("pending_upgrade")),
        "stars": int(repo.get("stars") or 0),
        "custom": bool(repo.get("custom")),
    }


async def _all_repositories(hass: HomeAssistant, category: str | None) -> list[dict[str, Any]]:
    fields = {"categories": [category]} if category else {}
    result = await _call(hass, _LIST, **fields)
    return [r for r in result or [] if isinstance(r, dict)]


async def _resolve(hass: HomeAssistant, ref: str) -> dict[str, Any]:
    """The repository named by id or ``owner/repo`` (case-insensitive)."""
    ref = str(ref or "").strip()
    if not ref:
        raise HacsError("Name the repository by its id or owner/repo, from hacs search.")
    for repo in await _all_repositories(hass, None):
        if str(repo.get("id")) == ref or str(repo.get("full_name") or "").lower() == ref.lower():
            return repo
    raise HacsError(
        f"HACS knows no repository '{_clean(ref, 120)}'. Search for it, or add it as a "
        "custom repository first."
    )


async def async_search(
    hass: HomeAssistant, query: str | None, category: str | None, installed_only: bool
) -> dict[str, Any]:
    """Repositories matching ``query``, installed ones first, then by stars."""
    if category and category not in CATEGORIES:
        return {"error": f"category must be one of {', '.join(CATEGORIES)}."}
    try:
        repos = await _all_repositories(hass, category)
    except HacsError as exc:
        return {"error": str(exc)}
    terms = [t for t in str(query or "").lower().split() if t]

    def _matches(repo: dict[str, Any]) -> bool:
        haystack = (
            " ".join(str(repo.get(k) or "") for k in ("full_name", "name", "description")).lower()
            + " "
            + " ".join(str(t) for t in repo.get("topics") or []).lower()
        )
        return all(t in haystack for t in terms)

    hits = [r for r in repos if _matches(r) and (not installed_only or r.get("installed"))]
    hits.sort(key=lambda r: (not r.get("installed"), -int(r.get("stars") or 0)))
    result: dict[str, Any] = {"repositories": [_summary(r) for r in hits[:_MAX_RESULTS]]}
    if len(hits) > _MAX_RESULTS:
        result["omitted"] = len(hits) - _MAX_RESULTS
    return result


async def async_info(hass: HomeAssistant, ref: str) -> dict[str, Any]:
    """One repository's details, as HACS reports them."""
    try:
        repo = await _resolve(hass, ref)
        info = await _call(hass, _INFO, repository_id=str(repo["id"]))
    except HacsError as exc:
        return {"error": str(exc)}
    info = info if isinstance(info, dict) else {}
    releases = [_clean(r, 40) for r in (info.get("releases") or [])[:10]]
    return {
        **_summary({**repo, **info}),
        "authors": [_clean(a, 60) for a in (info.get("authors") or [])[:10]],
        "topics": [_clean(t, 40) for t in (info.get("topics") or [])[:20]],
        "releases": releases,
        "homeassistant": _clean(info.get("homeassistant"), 20),
    }


def _risk_note(category: str) -> str:
    if category in FRONTEND_CATEGORIES:
        return (
            "This installs third-party code from GitHub that every browser opening a "
            "dashboard will run."
        )
    return (
        "This installs third-party Python from GitHub that runs INSIDE Home Assistant "
        "with full access to the home once Home Assistant restarts."
    )


def _needs_confirmation(hass: HomeAssistant, confirmed: bool) -> bool:
    from .command_policy_options import resolve_command_policy_options  # noqa: PLC0415

    return not confirmed and resolve_command_policy_options(hass).approval_required


async def async_download(
    hass: HomeAssistant, ref: str, version: str | None, *, confirmed: bool
) -> dict[str, Any]:
    """Install a repository, or update it to ``version`` / its latest release."""
    try:
        repo = await _resolve(hass, ref)
    except HacsError as exc:
        return {"error": str(exc)}
    category = str(repo.get("category") or "")
    summary = _summary(repo)
    if _needs_confirmation(hass, confirmed):
        # `installed` stays the repository's real state (an update starts
        # from installed); requires_confirmation is what says nothing ran.
        return {
            **summary,
            "requires_confirmation": True,
            "reason": f"{_risk_note(category)} Ask the user; once they agree, call again "
            "with confirmed=true.",
        }
    fields: dict[str, Any] = {"repository": str(repo["id"])}
    if version:
        fields["version"] = str(version)
    try:
        await _call(hass, _DOWNLOAD, **fields)
    except HacsError as exc:
        return {"error": str(exc), **summary}
    result: dict[str, Any] = {**summary, "installed": True}
    if category == "plugin":
        result["note"] = (
            "HACS registers the card as a dashboard resource; reload the browser to use it."
        )
    elif category == "theme":
        result["note"] = "Select it under the user's profile, or set it with frontend.set_theme."
    else:
        result["note"] = "Home Assistant must be restarted before it loads."
    return result


async def async_remove(hass: HomeAssistant, ref: str) -> dict[str, Any]:
    """Uninstall a repository's files (HACS keeps knowing the repository)."""
    try:
        repo = await _resolve(hass, ref)
        if not repo.get("installed"):
            return {"error": f"{_clean(repo.get('full_name'), 120)} is not installed."}
        await _call(hass, _REMOVE, repository=str(repo["id"]))
    except HacsError as exc:
        return {"error": str(exc)}
    category = str(repo.get("category") or "")
    result: dict[str, Any] = {"removed": True, **_summary({**repo, "installed": False})}
    if category not in FRONTEND_CATEGORIES:
        result["note"] = "Restart Home Assistant to unload it."
    return result


async def async_add_repository(
    hass: HomeAssistant, repository: str, category: str, *, confirmed: bool
) -> dict[str, Any]:
    """Add a custom GitHub repository to HACS so it can be installed."""
    repository = str(repository or "").strip()
    if (
        repository.count("/") != 1
        or not all(repository.split("/"))
        or any(c.isspace() for c in repository)
    ):
        return {"error": "repository must be 'owner/repo', e.g. 'thomasloven/lovelace-card-mod'."}
    if category not in CATEGORIES:
        return {"error": f"category must be one of {', '.join(CATEGORIES)}."}
    if _needs_confirmation(hass, confirmed):
        return {
            "added": False,
            "requires_confirmation": True,
            "repository": _clean(repository, 120),
            "reason": (
                "A custom repository is not reviewed by HACS: it trusts whoever controls "
                f"{_clean(repository, 120)} on GitHub, and installing from it later is "
                "another confirmation. Ask the user; once they agree, call again with "
                "confirmed=true."
            ),
        }
    try:
        await _call(hass, _ADD, repository=repository, category=category)
    except HacsError as exc:
        return {"error": str(exc)}
    return {"added": True, "repository": _clean(repository, 120), "category": category}
