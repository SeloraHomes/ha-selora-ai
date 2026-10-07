"""One look at what in the home needs attention — for "is anything wrong?".

Combines what Settings shows across five pages — integrations not working, open
repairs, updates waiting, backups, radio devices offline — from the managers the MCP tools use, so a
chat turn gets the answer in one call. One tool rather than four in the chat
schema: every cloud turn carries every tool's schema, and the question that
needs these is usually the same one.

Each section is capped and its text bounded; the per-topic MCP tools give the
full lists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_MAX_PER_SECTION: Final = 10


def _capped(rows: list[dict[str, Any]], keep: tuple[str, ...]) -> dict[str, Any]:
    trimmed = [{k: row[k] for k in keep if k in row} for row in rows[:_MAX_PER_SECTION]]
    return {
        "count": len(rows),
        "items": trimmed,
        **({"more": len(rows) - _MAX_PER_SECTION} if len(rows) > _MAX_PER_SECTION else {}),
    }


async def async_check_system(hass: HomeAssistant) -> dict[str, Any]:
    """Integrations not working, open repairs, pending updates, backup health."""
    from .backup_status import async_backup_status  # noqa: PLC0415
    from .integration_manager import async_list_integrations  # noqa: PLC0415
    from .network_health import network_health  # noqa: PLC0415
    from .repairs_manager import async_list_repairs  # noqa: PLC0415
    from .update_manager import async_list_updates  # noqa: PLC0415

    integrations = (await async_list_integrations(hass, problems_only=True))["integrations"]
    repairs = (await async_list_repairs(hass))["repairs"]
    updates = async_list_updates(hass)["updates"]
    backups = await async_backup_status(hass)

    result: dict[str, Any] = {
        "integrations_not_working": _capped(
            integrations, ("entry_id", "domain", "title", "state", "reason")
        ),
        "repairs": _capped(
            repairs, ("domain", "issue_id", "severity", "title", "description", "fixable")
        ),
        "updates": _capped(
            updates, ("entity_id", "title", "category", "installed_version", "latest_version")
        ),
    }
    if "error" in backups:
        result["backups"] = {"note": backups["error"]}
    else:
        result["backups"] = {
            "last_completed_automatic_backup": backups.get("last_completed_automatic_backup"),
            # The listing caps its rows; ``more`` carries the rest.
            "count": len(backups.get("backups", [])) + backups.get("more", 0),
            **({"warning": backups["warning"]} if backups.get("warning") else {}),
            # A failing location can sit beside a successful one, so it raises
            # no warning of its own — but it needs attention all the same.
            **(
                {"location_errors": backups["location_errors"]}
                if backups.get("location_errors")
                else {}
            ),
        }
    # Only networks with something down: a healthy mesh is not news, and the
    # weak-signal list is a diagnosis, not a fault (get_network_health has it).
    radio_down = [
        {
            "protocol": n["protocol"],
            "title": n["title"],
            **({"controller": n["controller"]} if "controller" in n else {}),
            "offline_count": n["offline_count"],
            "offline": [
                {k: d[k] for k in ("name", "area", "last_seen") if k in d}
                for d in n["attention"]
                if d["status"] == "offline"
            ][:_MAX_PER_SECTION],
        }
        for n in network_health(hass).get("networks", [])
        if n["offline_count"]
        or n.get("controller", {}).get("status") not in (None, "online", "ready", "unknown")
    ]
    if radio_down:
        result["radio_devices_offline"] = radio_down
    attention = (
        len(integrations)
        + sum(max(n["offline_count"], 1) for n in radio_down)
        + len(repairs)
        + (1 if result["backups"].get("warning") else 0)
        + len(result["backups"].get("location_errors", {}))
    )
    result["summary"] = (
        "Nothing needs attention."
        if not attention and not updates
        else f"{attention} thing(s) need attention; {len(updates)} update(s) available."
    )
    return result
