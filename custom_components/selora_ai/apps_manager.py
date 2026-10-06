"""Apps (formerly add-ons): list, logs, find and install, options.

Apps exist only on an install with the Supervisor. The list is Home
Assistant's own (``get_apps_list``, ``get_addons_list`` on older cores); logs
come from the Supervisor's ``/addons/<slug>/logs`` through Home Assistant's
``hassio`` client, whose ``send_command`` refuses any path that normalizes to
something else. Starting, stopping and restarting are services
(``hassio.app_start`` …, ``hassio.addon_start`` … on older cores), callable
over MCP already.

Finding, installing and configuring go through Home Assistant's Supervisor
client (``get_supervisor_client``):

* **Installing asks first**, naming what the app may reach (full host access,
  the Docker socket, the host network): an app runs with whatever it
  declares. It runs in the background — the Supervisor's call returns only
  once the image is pulled or built, minutes later — and ``list_apps``
  reports it as installing, then installed or failed.
* **Options are merged over the current ones** before they are sent: the
  Supervisor replaces the whole set, so a partial change would wipe the rest
  — the passwords a read never shows included. They are validated by the
  Supervisor against the app's own schema before anything is saved. A
  password-type option is never read back, only whether it is set.

All admin-only, as Home Assistant's own app pages are: app logs carry
whatever the app prints, credentials included.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Final

from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_SLUG: Final = re.compile(r"^[a-z0-9_]{1,64}$")
_DEFAULT_LINES: Final = 100
_MAX_LINES: Final = 500
_MAX_LINE: Final = 500
_ANSI: Final = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CONTROL: Final = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _supervised(hass: HomeAssistant) -> bool:
    from homeassistant.helpers.hassio import is_hassio  # noqa: PLC0415

    return is_hassio(hass)


_NOT_READY: Final = "The Supervisor's app list is not ready yet; try again in a minute."


def _installed(hass: HomeAssistant) -> list[dict[str, Any]]:
    """The installed apps. Raises ``HomeAssistantError`` (``HassioNotReadyError``
    on newer cores) while the Supervisor's list has not loaded yet."""
    try:
        from homeassistant.components.hassio import get_apps_list as listing  # noqa: PLC0415
    except ImportError:
        from homeassistant.components.hassio import get_addons_list as listing  # noqa: PLC0415
    return listing(hass) or []


def async_list_apps(hass: HomeAssistant) -> dict[str, Any]:
    """Installed apps with their state and version."""
    if not _supervised(hass):
        return {"error": "This Home Assistant has no Supervisor, so it has no apps."}
    try:
        installed = _installed(hass)
    except HomeAssistantError:
        return {"error": _NOT_READY}
    apps = [
        {
            "slug": app.get("slug"),
            "name": sanitize_untrusted_text(app.get("name") or app.get("slug"), 80),
            "state": str(app.get("state") or "unknown"),
            "version": app.get("version"),
            **({"update_available": True} if app.get("update_available") else {}),
            **(
                {"version_latest": app.get("version_latest")} if app.get("update_available") else {}
            ),
        }
        for app in installed
    ]
    apps.sort(key=lambda a: str(a["name"]).casefold())
    result: dict[str, Any] = {"apps": apps}
    if installs := _installs(hass):
        result["installs"] = dict(installs)
    return {
        **result,
        "hint": (
            "Start, stop or restart one with execute_command service hassio.app_start / "
            "app_stop / app_restart and data {app: slug} (hassio.addon_* with "
            "{addon: slug} on older cores)."
        ),
    }


def _clean(text: str, lines: int) -> list[str]:
    rows = []
    for raw in text.splitlines()[-lines:]:
        line = _CONTROL.sub("", _ANSI.sub("", raw)).rstrip()
        if len(line) > _MAX_LINE:
            line = line[: _MAX_LINE - 3] + "..."
        rows.append(line)
    return rows


async def async_app_logs(hass: HomeAssistant, slug: str, lines: Any = None) -> dict[str, Any]:
    """The last lines an app logged."""
    if not _supervised(hass):
        return {"error": "This Home Assistant has no Supervisor, so it has no apps."}
    slug = str(slug or "").strip().lower()
    if not _SLUG.match(slug):
        return {"error": "Pass the app's slug, from list_apps."}
    try:
        installed = _installed(hass)
    except HomeAssistantError:
        return {"error": _NOT_READY}
    if slug not in {app.get("slug") for app in installed}:
        return {"error": f"No installed app {slug}. Call list_apps for its slug."}
    try:
        count = int(lines) if lines is not None else _DEFAULT_LINES
    except (TypeError, ValueError):
        count = _DEFAULT_LINES
    count = max(1, min(_MAX_LINES, count))

    client = hass.data.get("hassio")
    if client is None or not hasattr(client, "send_command"):
        return {"error": "The Supervisor cannot be reached from here."}
    try:
        text = await client.send_command(
            f"/addons/{slug}/logs", method="get", return_text=True, timeout=30
        )
    except (HomeAssistantError, RuntimeError) as exc:
        # The hassio client raises HassioAPIError, a RuntimeError.
        return {
            "error": f"The Supervisor did not return the logs: {sanitize_untrusted_text(str(exc), 200)}"
        }
    return {"slug": slug, "lines": _clean(str(text or ""), count)}


# ── Finding, installing and configuring ─────────────────────────────────────

_MAX_RESULTS: Final = 25
_CREDENTIAL: Final = re.compile(r"pass|secret|token|api_?key|private", re.IGNORECASE)
_REDACTED: Final = "***"


def _client(hass: HomeAssistant) -> Any:
    from homeassistant.components.hassio.handler import get_supervisor_client  # noqa: PLC0415

    return get_supervisor_client(hass)


def _supervisor_errors() -> tuple[type[Exception], ...]:
    from aiohasupervisor import SupervisorError  # noqa: PLC0415

    return (SupervisorError, HomeAssistantError)


def _failed(exc: Exception) -> dict[str, Any]:
    return {"error": f"The Supervisor refused: {sanitize_untrusted_text(str(exc), 200)}"}


def _installs(hass: HomeAssistant) -> dict[str, str]:
    """Installs started here: slug → installing / installed / failed: why."""
    return hass.data.setdefault(DOMAIN, {}).setdefault("_app_installs", {})


async def async_search_app_store(hass: HomeAssistant, query: str) -> dict[str, Any]:
    """Apps in the store (official and added repositories) matching *query*."""
    if not _supervised(hass):
        return {"error": "This Home Assistant has no Supervisor, so it has no apps."}
    words = [w for w in str(query or "").casefold().split() if w]
    try:
        store = await _client(hass).store.addons_list()
    except _supervisor_errors() as exc:
        return _failed(exc)

    def _matches(app: Any) -> bool:
        text = f"{app.slug} {app.name} {app.description}".casefold()
        return all(w in text for w in words)

    found = [app for app in store if _matches(app)]
    found.sort(key=lambda a: (not a.installed, str(a.name).casefold()))
    rows = [
        {
            "slug": app.slug,
            "name": sanitize_untrusted_text(app.name, 80),
            "description": sanitize_untrusted_text(app.description, 160),
            "repository": sanitize_untrusted_text(app.repository, 60),
            "version": app.version_latest,
            "installed": bool(app.installed),
            **({} if app.available else {"available": False}),
        }
        for app in found[:_MAX_RESULTS]
    ]
    result: dict[str, Any] = {"apps": rows}
    if len(found) > _MAX_RESULTS:
        result["omitted"] = len(found) - _MAX_RESULTS
    if not found:
        result["hint"] = (
            "Nothing in the store matches. An app from a repository not added yet "
            "needs that repository added in Settings → Apps → App store first."
        )
    return result


def _reach(info: Any) -> list[str]:
    """What an app may reach beyond itself — said on the confirmation.

    Every privilege the store reports before installing. Kernel capabilities
    and devices are not among them; ``rating`` (the Supervisor's own security
    rating, which counts them) is shown beside this list for that reason.
    """
    reach = []
    if getattr(info, "full_access", False):
        reach.append("full access to the host hardware")
    if getattr(info, "docker_api", False):
        reach.append("the Docker socket (control of every container)")
    if getattr(info, "host_network", False):
        reach.append("the host network")
    if getattr(info, "host_pid", False):
        reach.append("the host's processes")
    if getattr(info, "supervisor_api", False):
        role = str(getattr(info, "supervisor_role", "default") or "default")
        reach.append(
            "the Supervisor API"
            + (f" as {role}" if role not in ("default", "homeassistant") else "")
        )
    if getattr(info, "homeassistant_api", False):
        reach.append("Home Assistant's API")
    if getattr(info, "auth_api", False):
        reach.append("Home Assistant user logins")
    if str(getattr(info, "apparmor", "")) == "disable":
        reach.append("no AppArmor confinement")
    return reach


def _install_fingerprint(slug: str, info: Any, reach: list[str]) -> str:
    """The app as the confirmation showed it: a version or a privilege that
    changed since (a custom repository can update between the two calls) is
    not what the user agreed to."""
    import hashlib  # noqa: PLC0415
    import json  # noqa: PLC0415

    payload = json.dumps(
        [slug, getattr(info, "version_latest", None), reach, getattr(info, "rating", None)]
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


async def async_install_app(
    hass: HomeAssistant, slug: str, *, confirmed: bool, fingerprint: str | None = None
) -> dict[str, Any]:
    """Describe an app before installing it, then — once confirmed, for the
    app exactly as described — install it in the background."""
    if not _supervised(hass):
        return {"error": "This Home Assistant has no Supervisor, so it has no apps."}
    slug = str(slug or "").strip().lower()
    if not _SLUG.match(slug):
        return {"error": "Pass the app's slug, from search_app_store."}
    installs = _installs(hass)
    if installs.get(slug) == "installing":
        return {"status": "installing", "slug": slug, "hint": "Already installing; see list_apps."}
    if confirmed:
        # Reserved before the first await: a second confirmed call meanwhile
        # sees it and does not start another install.
        installs[slug] = "installing"
    client = _client(hass)

    def _release(response: dict[str, Any]) -> dict[str, Any]:
        if confirmed:
            installs.pop(slug, None)
        return response

    try:
        info = await client.store.addon_info(slug)
    except _supervisor_errors() as exc:
        return _release(_failed(exc))
    if info.installed:
        return _release(
            {"error": f"{sanitize_untrusted_text(info.name, 60)} is already installed."}
        )
    if not info.available:
        return _release(
            {"error": f"{sanitize_untrusted_text(info.name, 60)} cannot run on this system."}
        )

    reach = _reach(info)
    current = _install_fingerprint(slug, info, reach)
    if not confirmed or fingerprint != current:
        return _release(
            {
                "requires_confirmation": True,
                "slug": slug,
                "name": sanitize_untrusted_text(info.name, 80),
                "description": sanitize_untrusted_text(info.description, 300),
                "version": info.version_latest,
                "repository": sanitize_untrusted_text(info.repository, 60),
                "security_rating": getattr(info, "rating", None),
                **({"can_reach": reach} if reach else {}),
                "fingerprint": current,
                **(
                    {"changed": "The app is not what was shown before; confirm it again."}
                    if confirmed and fingerprint
                    else {}
                ),
                "hint": (
                    "Tell the user what this app is"
                    + (f" and that it gets {', '.join(reach)}" if reach else "")
                    + " (security rating "
                    + f"{getattr(info, 'rating', '?')}/8, lower is more privileged), and "
                    "only once they agree call again with confirmed=true and this fingerprint."
                ),
            }
        )

    async def _install() -> None:
        try:
            await client.store.install_addon(slug)
        except _supervisor_errors() as exc:
            installs[slug] = f"failed: {sanitize_untrusted_text(str(exc), 200)}"
            _LOGGER.warning("Installing app %s failed: %s", slug, exc)
        else:
            installs[slug] = "installed"

    hass.async_create_background_task(_install(), f"selora_ai install app {slug}")
    return {
        "status": "installing",
        "slug": slug,
        "hint": (
            "Installing in the background; it can take several minutes. list_apps "
            "says when it is done. Set its options before starting it."
        ),
    }


def _password_keys(schema: list[dict[str, Any]] | None, options: dict[str, Any]) -> set[str]:
    secret = {
        str(row.get("name"))
        for row in schema or []
        if isinstance(row, dict) and str(row.get("type")) == "password"
    }
    return secret | {k for k in options if _CREDENTIAL.search(k)}


def _shown(options: dict[str, Any], secret: set[str]) -> dict[str, Any]:
    return {
        key: ({"is_set": bool(value)} if key in secret else value) for key, value in options.items()
    }


async def _installed_info(hass: HomeAssistant, slug: str) -> Any:
    slug = str(slug or "").strip().lower()
    if not _SLUG.match(slug):
        return {"error": "Pass the app's slug, from list_apps."}
    try:
        return await _client(hass).addons.addon_info(slug)
    except _supervisor_errors() as exc:
        return _failed(exc)


async def async_get_app_options(hass: HomeAssistant, slug: str) -> dict[str, Any]:
    """An installed app's options and the schema they follow."""
    if not _supervised(hass):
        return {"error": "This Home Assistant has no Supervisor, so it has no apps."}
    info = await _installed_info(hass, slug)
    if isinstance(info, dict):
        return info
    options = dict(info.options or {})
    secret = _password_keys(info.schema, options)
    return {
        "slug": info.slug,
        "name": sanitize_untrusted_text(info.name, 80),
        "state": str(info.state),
        "options": _shown(options, secret),
        "schema": info.schema or [],
        "hint": (
            "Change them with set_app_options, passing only what changes. A password "
            "shows only whether it is set; leave it out to keep it."
        ),
    }


async def async_set_app_options(hass: HomeAssistant, slug: str, options: Any) -> dict[str, Any]:
    """Merge *options* over the app's current ones, validate, save."""
    from aiohasupervisor.models import AddonsOptions  # noqa: PLC0415

    if not _supervised(hass):
        return {"error": "This Home Assistant has no Supervisor, so it has no apps."}
    if not isinstance(options, dict) or not options:
        return {"error": "options is an object of the options to change, from get_app_options."}
    info = await _installed_info(hass, slug)
    if isinstance(info, dict):
        return info
    current = dict(info.options or {})
    secret = _password_keys(info.schema, current)
    # A password read back as {"is_set": …} and sent again is not a new password.
    changes = {
        k: v
        for k, v in options.items()
        if not (k in secret and isinstance(v, dict) and set(v) == {"is_set"})
    }
    merged = {**current, **changes}
    client = _client(hass)
    try:
        verdict = await client.addons.addon_config_validate(info.slug, merged)
        if not verdict.valid:
            return {
                "error": (
                    "The app refuses those options: "
                    f"{sanitize_untrusted_text(verdict.message, 300)}"
                )
            }
        await client.addons.set_addon_options(info.slug, AddonsOptions(config=merged))
    except _supervisor_errors() as exc:
        return _failed(exc)
    running = str(info.state) == "started"
    return {
        "status": "saved",
        "slug": info.slug,
        "changed": sorted(changes),
        **(
            {
                "hint": (
                    "The app is running and reads its options when it starts: restart it "
                    "(execute_command hassio.app_restart with {app: slug}) to apply them."
                )
            }
            if running
            else {}
        ),
    }
