"""List the installed apps (formerly add-ons) and read one's logs.

Apps exist only on an install with the Supervisor. The list is Home
Assistant's own (``get_apps_list``, ``get_addons_list`` on older cores); logs
come from the Supervisor's ``/addons/<slug>/logs`` through Home Assistant's
``hassio`` client, whose ``send_command`` refuses any path that normalizes to
something else. Starting, stopping and restarting are services
(``hassio.app_start`` …, ``hassio.addon_start`` … on older cores), callable
over MCP already.

Both are admin-only, as Home Assistant's own app pages are: app logs carry
whatever the app prints, credentials included.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Final

from homeassistant.exceptions import HomeAssistantError

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

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
    return {
        "apps": apps,
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
