"""What Frigate has: its cameras with what each records and looks for, its
detectors, the optional features turned on, and its live rates and storage.

The one read of Frigate itself. The Frigate integration's entities say what is
on now; this says how each camera is configured, and also serves a home whose
Frigate has no integration entry (no entities at all).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from homeassistant.helpers import entity_registry as er

from ..helpers import sanitize_untrusted_text
from .cameras import model_labels, shown
from .client import FRIGATE_DOMAIN, NOT_SET_UP, FrigateClient, FrigateError, frigate_connection
from .events import _describes, _resolve_camera
from .objects import _looks_for

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_MAX_ZONES: Final = 20
_NO_INTEGRATION: Final = (
    "Frigate is reached from Selora AI's settings, without the Frigate integration: "
    "its cameras have no Home Assistant entities, so there are no recording or "
    "detection switches to turn and no camera to show on a dashboard. Installing "
    "the Frigate integration (HACS) adds them."
)


def _enabled(section: Any) -> bool | None:
    return section.get("enabled") if isinstance(section, dict) else None


def _retain_days(record: dict[str, Any]) -> dict[str, Any]:
    """Days each kind of recording is kept, as Frigate's version words it."""
    days: dict[str, Any] = {}
    for kind in ("retain", "alerts", "detections"):
        retain = record.get(kind)
        if isinstance(retain, dict) and kind != "retain":
            retain = retain.get("retain")
        if isinstance(retain, dict) and isinstance(retain.get("days"), int | float):
            days["continuous" if kind == "retain" else kind] = retain["days"]
    return days


def _streams(config: dict[str, Any], name: str) -> list[str]:
    """Where the camera is read from, without logins: its go2rtc sources, or
    its own inputs when it is not restreamed."""
    sources: list[str] = []
    streams = (config.get("go2rtc") or {}).get("streams") or {}
    for key in (name, f"{name}_sub"):
        value = streams.get(key)
        for source in value if isinstance(value, list) else [value]:
            if isinstance(source, str) and "://" in source:
                sources.append(shown(source.removeprefix("ffmpeg:").split("#", 1)[0]))
    if sources:
        return sources
    camera = (config.get("cameras") or {}).get(name) or {}
    return [
        shown(str(i["path"]))
        for i in (camera.get("ffmpeg") or {}).get("inputs") or []
        if isinstance(i, dict) and i.get("path") and "127.0.0.1" not in str(i["path"])
    ]


def _entity_ids(hass: HomeAssistant) -> dict[str, str]:
    """The Frigate integration's camera entity for each Frigate camera."""
    found: dict[str, str] = {}
    for entry in er.async_get(hass).entities.values():
        if entry.platform == FRIGATE_DOMAIN and entry.domain == "camera":
            # Keyed "<entry id>:camera:<camera>".
            kind, _, name = str(entry.unique_id).rpartition(":")
            if kind.endswith(":camera"):
                found.setdefault(name, entry.entity_id)
    return found


def _camera(
    hass: HomeAssistant,
    config: dict[str, Any],
    name: str,
    stats: dict[str, Any],
    entity_id: str | None,
) -> dict[str, Any]:
    camera = (config.get("cameras") or {}).get(name) or {}
    detect = camera.get("detect") or {}
    record = camera.get("record") or {}
    row: dict[str, Any] = {
        "name": name,
        "friendly_name": sanitize_untrusted_text(
            str(camera.get("friendly_name") or name.replace("_", " ").title()), 60
        ),
        "enabled": camera.get("enabled") is not False,
        "detect": detect.get("enabled") is not False,
        "looks_for": _looks_for(hass, config, name),
        "record": _enabled(record) is True,
        "snapshots": _enabled(camera.get("snapshots")) is True,
        "streams": _streams(config, name),
    }
    if detect.get("width") and detect.get("height"):
        row["detect_resolution"] = f"{detect['width']}x{detect['height']}"
    if row["record"] and (days := _retain_days(record)):
        row["record_days"] = days
    snapshot_days = ((camera.get("snapshots") or {}).get("retain") or {}).get("default")
    if row["snapshots"] and isinstance(snapshot_days, int | float):
        row["snapshot_days"] = snapshot_days
    if _enabled(camera.get("audio")) is True:
        row["audio"] = True
    if zones := list(camera.get("zones") or {})[:_MAX_ZONES]:
        row["zones"] = [sanitize_untrusted_text(str(z), 40) for z in zones]
    if entity_id:
        row["entity_id"] = entity_id
    rates = (stats.get("cameras") or {}).get(name)
    if isinstance(rates, dict):
        row["fps"] = {
            k: rates[k]
            for k in ("camera_fps", "detection_fps", "skipped_fps")
            if isinstance(rates.get(k), int | float)
        }
        if rates.get("camera_fps") == 0 and row["enabled"]:
            row["no_video"] = True
    return row


def _features(config: dict[str, Any]) -> dict[str, bool]:
    return {
        "semantic_search": _enabled(config.get("semantic_search")) is True,
        "genai_descriptions": _describes(config),
        "face_recognition": _enabled(config.get("face_recognition")) is True,
        "license_plates": _enabled(config.get("lpr")) is True,
        "birdseye": _enabled(config.get("birdseye")) is not False,
    }


def _storage(stats: dict[str, Any]) -> list[dict[str, Any]]:
    storage = ((stats.get("service") or {}).get("storage")) or {}
    return [
        {
            "path": sanitize_untrusted_text(str(path), 80),
            **{k: round(v) for k, v in usage.items() if k in ("total", "used", "free")},
        }
        for path, usage in storage.items()
        if isinstance(usage, dict) and str(path).startswith("/media")
    ]


async def async_frigate_status(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Frigate's cameras and setup, or one camera's when ``camera`` is given."""
    connection = frigate_connection(hass)
    client = FrigateClient.from_hass(hass)
    if connection is None or client is None:
        return {"error": NOT_SET_UP}
    try:
        config = await client.get_config()
        version = await client.version()
    except FrigateError as exc:
        return {"error": str(exc)}
    try:
        stats = await client.get_stats()
    except FrigateError:
        # Rates and storage are extras; the configuration answers without them.
        stats = {}
    cameras = config.get("cameras") or {}
    names = sorted(cameras)
    if given := str(arguments.get("camera") or "").strip():
        friendly = {n: str((cameras[n] or {}).get("friendly_name") or "") for n in names}
        name = _resolve_camera(hass, given, friendly)
        if name is None:
            return {"error": "Frigate has no such camera.", "frigate_cameras": names}
        names = [name]
    entities = _entity_ids(hass)
    result: dict[str, Any] = {
        "version": sanitize_untrusted_text(version, 40),
        "connected_through": connection.source,
        "cameras": [_camera(hass, config, n, stats, entities.get(n)) for n in names],
    }
    if given:
        return result
    detectors = config.get("detectors") or {}
    result["detectors"] = [
        {
            "name": sanitize_untrusted_text(str(name), 40),
            "type": sanitize_untrusted_text(str((d or {}).get("type") or ""), 40),
            **(
                {"inference_ms": round(speed, 1)}
                if isinstance(
                    speed := ((stats.get("detectors") or {}).get(name) or {}).get(
                        "inference_speed"
                    ),
                    int | float,
                )
                else {}
            ),
        }
        for name, d in detectors.items()
    ]
    if (labels := model_labels(config)) is not None:
        result["model_labels"] = sorted(labels)
    result["features"] = _features(config)
    if storage := _storage(stats):
        result["storage_mb"] = storage
    if connection.source == "settings":
        result["note"] = _NO_INTEGRATION
    return result
