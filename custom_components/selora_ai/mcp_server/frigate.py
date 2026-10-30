"""MCP tools that change Frigate's cameras, and read its setup and snapshots.

Chat proposes each change on a card the user confirms. MCP has no card, so the
first call returns what would change with ``requires_confirmation`` and a token
for exactly that change; the client confirms with the user and calls again
with ``confirmed`` and the token. Confirming re-checks the camera against the
token, as the card's confirm does, so a camera changed in between is refused.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final
from urllib.parse import quote

from ..frigate.client import NOT_SET_UP, FrigateClient, FrigateError
from ..helpers import sanitize_untrusted_text
from .cameras import _IMAGE_TYPES, _side
from .protocol import ToolImage

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_CONFIRM: Final = (
    "Nothing has changed yet. Tell the user what this does, and only once they "
    "agree call again with the same arguments, confirmed=true and this confirm_token."
)
_NO_TOKEN: Final = "Pass the confirm_token the first call returned, with confirmed=true."
# A network camera's login is typed into the panel's card and never passes
# through a model, here an outside one; see docs/dev/frigate.md.
_NEEDS_LOGIN: Final = (
    "These network cameras need their login, which is never passed through an "
    "assistant: the user adds them from Selora AI's own chat, where the login is "
    "typed into the card ('add the camera at <address> to Frigate'). Leave them "
    "out (entity_ids for Home Assistant's cameras, stream_urls for one that needs "
    "no login) to add the others here."
)


def _confirmed(arguments: dict[str, Any]) -> bool:
    from ..tool_executor import _opt_bool  # noqa: PLC0415

    return _opt_bool(arguments.get("confirmed")) is True


def _token(arguments: dict[str, Any]) -> str:
    return str(arguments.get("confirm_token") or "").strip()


def _ask(card: dict[str, Any], **facts: Any) -> dict[str, Any]:
    """A card's preview, worded for an MCP client to confirm."""
    return {
        "requires_confirmation": True,
        "camera": card["target_id"],
        "name": card["name"],
        "change": card["label"],
        **facts,
        "confirm_token": card["fingerprint"],
        "note": _CONFIRM,
    }


async def _tool_get_frigate_status(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    from ..frigate.status import async_frigate_status  # noqa: PLC0415

    return await async_frigate_status(hass, arguments)


async def _tool_set_camera_objects(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    from ..frigate.objects import (  # noqa: PLC0415
        async_preview_set_objects,
        async_set_objects_confirmed,
    )

    preview = await async_preview_set_objects(hass, arguments)
    card = preview.get("destructive")
    if card is None:
        return preview
    if not _confirmed(arguments):
        return _ask(card)
    if not (token := _token(arguments)):
        return {"error": _NO_TOKEN}
    return await async_set_objects_confirmed(hass, card["target_id"], token, arguments)


async def _tool_move_frigate_camera(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    from ..frigate.moving import (  # noqa: PLC0415
        async_move_camera_confirmed,
        async_preview_move_camera,
    )

    preview = await async_preview_move_camera(hass, arguments)
    card = preview.get("destructive")
    if card is None:
        return preview
    if not _confirmed(arguments):
        return _ask(card)
    if not (token := _token(arguments)):
        return {"error": _NO_TOKEN}
    return await async_move_camera_confirmed(hass, card["target_id"], token, arguments)


async def _tool_remove_frigate_camera(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    from ..frigate.removal import (  # noqa: PLC0415
        async_preview_remove_camera,
        async_remove_camera_confirmed,
    )

    preview = await async_preview_remove_camera(hass, str(arguments.get("camera") or ""))
    card = preview.get("delete")
    if card is None:
        return preview
    if not _confirmed(arguments):
        return _ask(
            {**card, "label": f"Remove {card['name']} from Frigate (Frigate restarts)"},
            removes="The camera's Frigate configuration and its go2rtc streams. "
            "Its recordings stay on disk until Frigate's retention removes them.",
        )
    if not (token := _token(arguments)):
        return {"error": _NO_TOKEN}
    return await async_remove_camera_confirmed(hass, card["target_id"], token)


async def _tool_add_cameras_to_frigate(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    from ..frigate.setup import (  # noqa: PLC0415
        async_add_cameras_confirmed,
        async_preview_add_cameras,
    )

    if _confirmed(arguments):
        if not (plan_id := _token(arguments)):
            return {"error": _NO_TOKEN}
        # The plan holds the cameras and settings that were shown; the
        # arguments of this call are not read again.
        return await async_add_cameras_confirmed(hass, plan_id, {})
    preview = await async_preview_add_cameras(hass, arguments)
    action = preview.get("client_action")
    if action is None:
        return preview
    rows = [dict(row) for row in action["cameras"]]
    if needs_login := [
        {"name": row["name"], "address": row["address"]}
        for row in rows
        if row["source"] == "network" and "stream" not in row
    ]:
        return {
            "error": "Some cameras need a login.",
            "needs_login": needs_login,
            "hint": _NEEDS_LOGIN,
        }
    for row in rows:
        # A stream the user gave is added as given, with no login.
        row.pop("needs_login", None)
    return {
        "requires_confirmation": True,
        "change": action["label"],
        "cameras": rows,
        "skipped": preview.get("skipped") or [],
        **{k: preview[k] for k in ("possibly_moved", "moved_hint") if k in preview},
        "confirm_token": action["plan_id"],
        "note": _CONFIRM + " The token is good for half an hour.",
    }


async def _tool_get_frigate_snapshot(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any] | ToolImage:
    """A camera's latest frame, or a tracked object's snapshot, from Frigate itself."""
    from ..frigate.events import _EVENT_ID, _resolve_camera  # noqa: PLC0415

    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": NOT_SET_UP}
    event_id = str(arguments.get("event_id") or "").strip()
    given = str(arguments.get("camera") or "").strip()
    if not event_id and not given:
        return {"error": "Pass a camera or an event_id."}
    height = _side(arguments.get("height"), 720, 120, 2160)
    try:
        if event_id:
            if not _EVENT_ID.match(event_id):
                return {"error": "Not an event id: pass one selora_find_camera_events returned."}
            fields: dict[str, Any] = {"event_id": event_id}
            try:
                data, content_type = await client.get_image(
                    f"/api/events/{event_id}/snapshot.jpg?bbox=1"
                )
            except FrigateError:
                # Snapshots are off on the camera: its thumbnail is all there is.
                data, content_type = await client.get_image(f"/api/events/{event_id}/thumbnail.jpg")
                fields["thumbnail_only"] = True
        else:
            config = await client.get_config()
            cameras = config.get("cameras") or {}
            friendly = {n: str((c or {}).get("friendly_name") or "") for n, c in cameras.items()}
            name = _resolve_camera(hass, given, friendly)
            if name is None:
                return {"error": "Frigate has no such camera.", "frigate_cameras": sorted(cameras)}
            data, content_type = await client.get_image(f"/api/{quote(name)}/latest.jpg?h={height}")
            fields = {"camera": name}
    except FrigateError as exc:
        return {"error": str(exc)}
    mime_type = content_type.split(";", 1)[0].strip().lower()
    if mime_type not in _IMAGE_TYPES:
        return {"error": f"Frigate sent {sanitize_untrusted_text(mime_type, 40)}, not an image."}
    return ToolImage(
        data=data,
        mime_type=mime_type,
        fields={**fields, "content_type": mime_type, "bytes": len(data)},
    )
