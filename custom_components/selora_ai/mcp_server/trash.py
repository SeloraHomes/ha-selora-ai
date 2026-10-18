"""MCP tool for Recently deleted (trash.py): find and restore."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant


async def _tool_restore_deleted(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Restore the item a ``trash_id`` names; without one, list the matches.

    MCP has no card to pick from, so the caller shows the matches to the user
    and comes back with the one they chose.
    """
    from ..trash import (  # noqa: PLC0415
        TrashRestoreError,
        async_restore,
        async_restore_candidates,
    )

    trash_id = str(arguments.get("trash_id") or "").strip()
    if not trash_id:
        result = await async_restore_candidates(hass, arguments, full=True)
        result["note"] = (
            "Nothing was restored. Show the user these matches and call again with "
            "the trash_id they choose."
        )
        return result
    try:
        item = await async_restore(hass, trash_id)
    except TrashRestoreError as exc:
        messages = {
            "not_found": "Not in Recently deleted (restored, deleted for good, or expired).",
            "in_use": "Another automation or scene now uses its id or name; not restored.",
        }
        return {"error": messages.get(exc.code, f"Restore failed: {exc}")}
    return {"status": "restored", "kind": item["kind"], "name": item["name"]}
