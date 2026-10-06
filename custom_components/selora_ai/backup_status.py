"""The home's backups, and whether automatic backups are working.

Read from Home Assistant's backup manager as its ``backup/info`` command does.
The manager's shape changed across the cores supported (2025.1 introduced it;
later ones added its state and the next scheduled run), so only the common
part is required and the rest is read when present. Creating a backup is a
service (``backup.create_automatic`` / ``backup.create``, or
``hassio.backup_full`` on a supervised install), callable over MCP already;
restoring is deliberately not offered.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any, Final

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_MAX_BACKUPS: Final = 50
# An automatic backup older than this, or one attempted after the last
# success, is worth saying out loud.
_STALE_AFTER: Final = datetime.timedelta(days=7)


def _when(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return str(value)


def _row(backup: Any) -> dict[str, Any]:
    agents = getattr(backup, "agents", None) or {}
    sizes = [getattr(status, "size", 0) or 0 for status in agents.values()]
    size = max(sizes) if sizes else getattr(backup, "size", None)
    row: dict[str, Any] = {
        "backup_id": getattr(backup, "backup_id", None),
        "name": sanitize_untrusted_text(getattr(backup, "name", "") or "", 80),
        "date": _when(getattr(backup, "date", None)),
        "locations": sorted(agents),
        # None when Home Assistant cannot tell (an imported or older backup).
        "automatic": getattr(backup, "with_automatic_settings", None),
        "homeassistant_version": getattr(backup, "homeassistant_version", None),
        "database_included": getattr(backup, "database_included", None),
    }
    if size:
        row["size_mb"] = round(size / 1_000_000, 1)
    failed = getattr(backup, "failed_agent_ids", None)
    if failed:
        row["failed_locations"] = sorted(failed)
    return row


def _instant(value: Any) -> datetime.datetime | None:
    """A timestamp as an aware UTC instant, so offsets compare correctly."""
    parsed = dt_util.parse_datetime(_when(value) or "")
    return dt_util.as_utc(parsed) if parsed is not None else None


def _health(last_attempted: Any, last_completed: Any, state: str | None) -> str | None:
    """A sentence when automatic backups look broken, else None.

    A backup being made right now has been attempted and not yet completed —
    Home Assistant records the attempt when it starts — so that is not a
    failure.
    """
    if state == "create_backup":
        return None
    attempted = _instant(last_attempted)
    completed = _instant(last_completed)
    if attempted and (completed is None or attempted > completed):
        return (
            "The last automatic backup failed: it was attempted after the last one that completed."
        )
    if completed and dt_util.utcnow() - completed > _STALE_AFTER:
        return f"No automatic backup has completed for {(dt_util.utcnow() - completed).days} days."
    return None


async def async_backup_status(hass: HomeAssistant) -> dict[str, Any]:
    """Backups, newest first, and how automatic backups are doing."""
    if "backup" not in hass.config.components:
        return {"error": "The backup integration is not loaded. It is part of default_config."}
    manager = hass.data.get("backup")
    if manager is None or not hasattr(manager, "async_get_backups"):
        return {"error": "This Home Assistant's backups cannot be read from here."}
    try:
        backups, agent_errors = await manager.async_get_backups()
    except HomeAssistantError as exc:
        return {
            "error": f"The backups could not be listed: {sanitize_untrusted_text(str(exc), 200)}"
        }
    oldest = datetime.datetime.min.replace(tzinfo=datetime.UTC)
    rows = sorted(
        (_row(b) for b in backups.values()),
        key=lambda r: _instant(r["date"]) or oldest,
        reverse=True,
    )

    data = getattr(getattr(manager, "config", None), "data", None)
    last_attempted = getattr(data, "last_attempted_automatic_backup", None)
    last_completed = getattr(data, "last_completed_automatic_backup", None)
    schedule = getattr(data, "schedule", None)
    result: dict[str, Any] = {
        "backups": rows[:_MAX_BACKUPS],
        **({"more": len(rows) - _MAX_BACKUPS} if len(rows) > _MAX_BACKUPS else {}),
        "last_completed_automatic_backup": _when(last_completed),
        "last_attempted_automatic_backup": _when(last_attempted),
    }
    if (next_run := getattr(schedule, "next_automatic_backup", None)) is not None:
        result["next_automatic_backup"] = _when(next_run)
    state = getattr(manager, "state", None)
    state = str(state) if state is not None else None
    if state is not None:
        result["state"] = state
    if agent_errors:
        result["location_errors"] = {
            str(agent): sanitize_untrusted_text(str(err), 200)
            for agent, err in agent_errors.items()
        }
    if warning := _health(last_attempted, last_completed, state):
        result["warning"] = warning
    return result
