"""Turning a camera's snapshots on or off in Frigate, and how long they are kept.

A snapshot is the still Frigate saves of each object it tracks: what an event
card shows, what a person is added from (``faces.async_add_event_photo``).
Frigate's Snapshots switch changes them at runtime only when the camera's
configuration enables them (``controls.py``), so keeping them on is
configuration: ``cameras.<name>.snapshots``, behind a card, and Frigate
restarts to apply it.

Confirming re-reads the camera and writes only if its snapshots are still
what the card was built from.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any, Final

from ..const import DOMAIN
from ..helpers import sanitize_untrusted_text
from .client import NOT_SET_UP, FrigateClient, FrigateError
from .objects import _friendly
from .removal import _camera_name

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_WRITE_LOCK: Final = "_frigate_snapshots_lock"
_MAX_DAYS: Final = 365


def _snapshots(config: dict[str, Any], name: str) -> tuple[bool, float | None]:
    """Whether the camera's configuration keeps snapshots, and for how many
    days. ``enabled_in_config`` over ``enabled``: the switch changes the
    running one, and only the configuration lasts a restart."""
    snapshots = ((config.get("cameras") or {}).get(name) or {}).get("snapshots") or {}
    enabled = snapshots.get("enabled_in_config", snapshots.get("enabled"))
    days = (snapshots.get("retain") or {}).get("default")
    return enabled is True, days if isinstance(days, int | float) else None


def _fingerprint(name: str, enabled: bool, days: float | None) -> str:
    """The camera and its snapshots when the card was shown."""
    return hashlib.sha256(json.dumps([name, enabled, days]).encode()).hexdigest()


def _wanted(arguments: dict[str, Any]) -> tuple[bool, int | None] | str:
    """The answer and the days asked for, or why they cannot be taken."""
    from ..tool_executor import _opt_bool  # noqa: PLC0415

    enabled = _opt_bool(arguments.get("enabled"))
    if enabled is None:
        return "Say whether to keep snapshots: enabled true or false."
    days = arguments.get("retain_days")
    if days is None or not enabled:
        return enabled, None
    try:
        days = int(days)
    except (TypeError, ValueError):
        return "retain_days is a number of days."
    if not 1 <= days <= _MAX_DAYS:
        return f"retain_days is between 1 and {_MAX_DAYS}."
    return enabled, days


def _days(days: float | None) -> str:
    if days is None:
        return ""
    shown = int(days) if float(days).is_integer() else days
    return f", kept {shown} day{'' if shown == 1 else 's'}"


async def async_preview_set_snapshots(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """A card for a camera's snapshots, or why there is nothing to change."""
    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": NOT_SET_UP}
    wanted = _wanted(arguments)
    if isinstance(wanted, str):
        return {"error": wanted}
    enabled, days = wanted
    try:
        config = await client.get_config()
    except FrigateError as exc:
        return {"error": str(exc)}
    cameras = config.get("cameras") or {}
    name = _camera_name(hass, cameras, str(arguments.get("camera") or ""))
    if name is None:
        return {"error": "Frigate has no such camera.", "frigate_cameras": sorted(cameras)}
    friendly = _friendly(config, name)
    current, current_days = _snapshots(config, name)
    if enabled == current and (days is None or days == current_days):
        state = f"keeps snapshots{_days(current_days)}" if current else "keeps no snapshots"
        return {
            "unchanged": True,
            "camera": name,
            "note": f"{friendly} already {state}; nothing to change.",
        }
    if enabled:
        label = f"Keep a snapshot of everything {friendly} tracks{_days(days or current_days)}"
    else:
        label = (
            f"Stop keeping snapshots of what {friendly} tracks; the ones it kept stay "
            "until their retention ends"
        )
    return {
        "requires_approval": True,
        "destructive": {
            "kind": "frigate_snapshots",
            "verb": "set_snapshots",
            "target_id": name,
            "entity_id": "",
            "name": friendly,
            "label": sanitize_untrusted_text(f"{label} (Frigate restarts)", 300),
            "fingerprint": _fingerprint(name, current, current_days),
        },
    }


async def async_set_snapshots_confirmed(
    hass: HomeAssistant, name: str, fingerprint: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Write what the card showed, if the camera's snapshots are still as shown."""
    from .setup import async_reload_when_back  # noqa: PLC0415

    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": NOT_SET_UP}
    wanted = _wanted(arguments)
    if isinstance(wanted, str):
        return {"error": wanted}
    enabled, days = wanted
    lock: asyncio.Lock = hass.data.setdefault(DOMAIN, {}).setdefault(_WRITE_LOCK, asyncio.Lock())
    async with lock:
        try:
            config = await client.get_config()
            if name not in (config.get("cameras") or {}):
                return {"error": "Frigate no longer has that camera."}
            if _fingerprint(name, *_snapshots(config, name)) != fingerprint:
                return {"error": "The camera's snapshots changed since it was shown; ask again."}
            snapshots: dict[str, Any] = {"enabled": enabled}
            if days is not None:
                snapshots["retain"] = {"default": days}
            await client.set_config({"cameras": {name: {"snapshots": snapshots}}})
        except FrigateError as exc:
            return {"error": str(exc)}
    friendly = _friendly(config, name)
    done = (
        f"{friendly} keeps a snapshot of everything it tracks{_days(days)}"
        if enabled
        else f"{friendly} no longer keeps snapshots"
    )
    try:
        await client.restart()
    except FrigateError as exc:
        _LOGGER.warning("Frigate saved the camera's snapshots but did not restart: %s", exc)
        return {
            "camera": name,
            "name": friendly,
            "restart_failed": True,
            "done": f"Saved: {done} once Frigate restarts. It could not restart itself.",
        }
    hass.async_create_background_task(
        async_reload_when_back(hass, client), f"{DOMAIN}_frigate_reload"
    )
    return {
        "camera": name,
        "name": friendly,
        "done": f"{done}. Frigate is restarting, about a minute.",
    }
