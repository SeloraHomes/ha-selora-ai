"""MCP tool for a camera snapshot, returned as an image the client can see."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from homeassistant.exceptions import HomeAssistantError

from ..helpers import sanitize_untrusted_text
from .protocol import ToolImage

if TYPE_CHECKING:
    from homeassistant.components.camera import Image
    from homeassistant.core import HomeAssistant

# What a snapshot is scaled to fit unless the caller asks for more: enough to
# see who is at the door, at a fraction of a 4K frame's size. Home Assistant
# scales only a JPEG, and only when given both sides, so both are always passed.
_DEFAULT_SIZE: Final = (1280, 720)
_MIN_SIZE: Final = (160, 120)
_MAX_SIZE: Final = (3840, 2160)
# Base64 adds a third; past this the response is too large to be worth sending,
# and the caller is told to ask for a smaller one.
_MAX_IMAGE_BYTES: Final = 4 * 1024 * 1024
_IMAGE_TYPES: Final = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})


def _side(value: Any, default: int, low: int, high: int) -> int:
    """A requested width or height, clamped; the default when absent or unreadable."""
    if value is None or isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


async def _fetch_image(hass: HomeAssistant, entity_id: str, width: int, height: int) -> Image:
    """The snapshot, through Home Assistant's own camera helper.

    Imported here: the camera component pulls in its image libraries, which
    only an install with the camera integration loaded is sure to have.
    """
    from homeassistant.components.camera import async_get_image  # noqa: PLC0415

    return await async_get_image(hass, entity_id, width=width, height=height)


async def _tool_get_camera_image(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any] | ToolImage:
    """The camera's current snapshot, scaled where the camera allows."""
    entity_id = str(arguments.get("entity_id") or "").strip().lower()
    shown = sanitize_untrusted_text(entity_id, 80)
    if not entity_id.startswith("camera."):
        return {"error": f"'{shown}' is not a camera. Pass a camera.* entity_id."}
    if hass.states.get(entity_id) is None:
        return {"error": f"No camera {shown}. Call search_entities for its entity_id."}
    if "camera" not in hass.config.components:
        return {"error": "The camera integration is not loaded, so no camera can be asked."}

    width = _side(arguments.get("width"), _DEFAULT_SIZE[0], _MIN_SIZE[0], _MAX_SIZE[0])
    height = _side(arguments.get("height"), _DEFAULT_SIZE[1], _MIN_SIZE[1], _MAX_SIZE[1])
    try:
        image = await _fetch_image(hass, entity_id, width, height)
    except HomeAssistantError as exc:
        return {
            "error": (
                f"{shown} gave no image: {sanitize_untrusted_text(str(exc), 200)}. It may "
                "be off, offline, or a camera that only streams."
            )
        }

    mime_type = image.content_type.split(";", 1)[0].strip().lower()
    # Not a registered type, but what many cameras (HA's demo one included)
    # report; a client checks the type it is handed.
    if mime_type == "image/jpg":
        mime_type = "image/jpeg"
    if mime_type not in _IMAGE_TYPES:
        return {"error": f"{shown} sent {sanitize_untrusted_text(mime_type, 40)}, not an image."}
    if len(image.content) > _MAX_IMAGE_BYTES:
        return {
            "error": (
                f"{shown}'s snapshot is {len(image.content) // 1024} KB, too large to "
                "send. Ask for a smaller width and height; a camera that sends no JPEG "
                "cannot be scaled."
            )
        }
    return ToolImage(
        data=image.content,
        mime_type=mime_type,
        fields={"entity_id": entity_id, "content_type": mime_type, "bytes": len(image.content)},
    )
