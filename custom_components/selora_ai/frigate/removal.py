"""Removing a camera from Frigate, behind a delete card.

The camera is named by its Frigate name, its friendly name, or the camera
entity the Frigate integration made for it. The card carries the Frigate name
and a fingerprint of the camera's inputs and streams, so a camera changed or
replaced under that name since is not removed blind.

Only what Selora writes when adding a camera is removed: ``cameras.<name>``
and its go2rtc streams. Frigate's recordings of it are Frigate's to keep or
expire.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from ..const import DOMAIN
from ..helpers import sanitize_untrusted_text
from .client import FRIGATE_DOMAIN, NOT_SET_UP, FrigateClient, FrigateError
from .discovery import SUGGESTION_CACHE

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_REMOVED_BY_LANG: Final[dict[str, str]] = {
    "en": "Removed {names} from Frigate.",
    "fr": "{names} retiré(s) de Frigate.",
    "de": "{names} aus Frigate entfernt.",
    "es": "{names} quitada(s) de Frigate.",
    "it": "{names} rimossa/e da Frigate.",
    "nl": "{names} verwijderd uit Frigate.",
    "hu": "{names} eltávolítva a Frigate-ből.",
    "zh": "已从 Frigate 中移除 {names}。",
    "pt": "{names} removida(s) do Frigate.",
    "ja": "{names} を Frigate から削除しました。",
    "ko": "{names}을(를) Frigate에서 제거했습니다.",
    "ru": "{names} удалены из Frigate.",
}


def _camera_name(hass: HomeAssistant, cameras: dict[str, Any], target: str) -> str | None:
    """Frigate's name for the camera *target* names, or None."""
    target = target.strip()
    if target in cameras:
        return target
    entry = er.async_get(hass).async_get(target.lower())
    if entry is not None and entry.platform == FRIGATE_DOMAIN and entry.domain == "camera":
        # The integration's unique id ends with the Frigate camera name.
        name = str(entry.unique_id).rsplit(":", 1)[-1]
        if name in cameras:
            return name
    wanted = slugify(target)
    for name, camera in cameras.items():
        friendly = str((camera or {}).get("friendly_name") or "")
        if wanted in (slugify(name), slugify(friendly)):
            return name
    return None


def _streams_of(config: dict[str, Any], name: str) -> list[str]:
    streams = (config.get("go2rtc") or {}).get("streams") or {}
    return [s for s in (name, f"{name}_sub") if s in streams]


def _fingerprint(config: dict[str, Any], name: str) -> str:
    """The camera as the card showed it: its inputs and its go2rtc streams.

    Frigate redacts credentials in what it reports, so none are hashed."""
    camera = (config.get("cameras") or {}).get(name) or {}
    streams = (config.get("go2rtc") or {}).get("streams") or {}
    shown = {
        "inputs": [i.get("path") for i in (camera.get("ffmpeg") or {}).get("inputs") or []],
        "streams": {s: streams.get(s) for s in _streams_of(config, name)},
    }
    return hashlib.sha256(json.dumps(shown, sort_keys=True, default=str).encode()).hexdigest()


async def async_preview_remove_camera(hass: HomeAssistant, target: str) -> dict[str, Any]:
    """A delete card for removing a camera from Frigate, or why not."""
    from ..removal_cards import removal_card  # noqa: PLC0415

    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": NOT_SET_UP}
    try:
        config = await client.get_config()
    except FrigateError as exc:
        return {"error": str(exc)}
    cameras = config.get("cameras") or {}
    name = _camera_name(hass, cameras, target)
    if name is None:
        return {
            "error": "Frigate has no such camera.",
            "frigate_cameras": sorted(cameras),
        }
    # The card names the camera; its kind line already says "Frigate camera".
    friendly = str((cameras[name] or {}).get("friendly_name") or name.replace("_", " ").title())
    return removal_card("frigate_camera", name, friendly, _fingerprint(config, name))


def removed_line(names: list[str], language: str | None) -> str:
    """The conversation's record of cameras taken out of Frigate."""
    base = str(language or "en").lower().split("-")[0]
    template = _REMOVED_BY_LANG.get(base, _REMOVED_BY_LANG["en"])
    return template.format(names=", ".join(sanitize_untrusted_text(n, 60) for n in names))


async def async_remove_camera_confirmed(
    hass: HomeAssistant, name: str, fingerprint: str
) -> dict[str, Any]:
    """Remove the camera the card named, if it is still the one it showed."""
    from .setup import async_reload_when_back  # noqa: PLC0415

    client = FrigateClient.from_hass(hass)
    if client is None:
        return {"error": NOT_SET_UP}
    try:
        config = await client.get_config()
        if name not in (config.get("cameras") or {}):
            return {"error": "Frigate no longer has that camera."}
        if _fingerprint(config, name) != fingerprint:
            return {"error": "The camera changed since it was shown; ask again."}
        # A null value is how Frigate's config API deletes a key.
        await client.set_config(
            {
                "cameras": {name: None},
                **(
                    {"go2rtc": {"streams": dict.fromkeys(streams)}}
                    if (streams := _streams_of(config, name))
                    else {}
                ),
            }
        )
    except FrigateError as exc:
        return {"error": str(exc)}
    hass.data.setdefault(DOMAIN, {}).pop(SUGGESTION_CACHE, None)
    try:
        await client.restart()
    except FrigateError as exc:
        _LOGGER.warning("Frigate removed the camera but did not restart: %s", exc)
        return {
            "removed": name,
            "restart_failed": True,
            "note": "Frigate could not restart itself; the camera stops with its next restart.",
        }
    hass.async_create_background_task(
        async_reload_when_back(hass, client), f"{DOMAIN}_frigate_reload"
    )
    return {"removed": name}
