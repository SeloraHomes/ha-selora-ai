"""List what can be updated, and read an update's release notes.

Updates are ``update.*`` entities: Home Assistant itself (core, OS,
supervisor), apps, HACS repositories, device firmware and the rest. Listing
reads their states; release notes come from the entity, as Home Assistant's
admin-only ``update/release_notes`` command fetches them. Over MCP, installing,
skipping and un-skipping are services (``update.install``, ``update.skip``,
``update.clear_skipped``), gated by risk there; chat installs through a
confirmation card pinned to the version it showed (below).

Release notes and summaries are written by whoever publishes the update, so
they are untrusted text: bounded, and stripped of control characters.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Final

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, State

_HOME_ASSISTANT_PREFIXES: Final = (
    "home_assistant_core_",
    "home_assistant_os_",
    "home_assistant_supervisor_",
)
_NOTES_LIMIT: Final = 8000
_CONTROL: Final = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _category(entry: er.RegistryEntry | None) -> str:
    """Where an update comes from, grouped as Home Assistant's own UI groups them."""
    if entry is None:
        return "other"
    if entry.platform == "hassio":
        return (
            "home_assistant" if str(entry.unique_id).startswith(_HOME_ASSISTANT_PREFIXES) else "app"
        )
    if entry.platform == "hacs":
        return "hacs"
    if entry.device_id:
        return "device"
    return "other"


def _version(value: Any) -> str | None:
    return sanitize_untrusted_text(value, 60) if value not in (None, "") else None


def _row(state: State, entry: er.RegistryEntry | None) -> dict[str, Any]:
    attrs = state.attributes
    row: dict[str, Any] = {
        "entity_id": state.entity_id,
        "title": sanitize_untrusted_text(attrs.get("title") or state.name, 80),
        "category": _category(entry),
        # Set by whoever publishes the update, like the summary below.
        "installed_version": _version(attrs.get("installed_version")),
        "latest_version": _version(attrs.get("latest_version")),
    }
    if attrs.get("skipped_version"):
        row["skipped"] = True
    if attrs.get("in_progress"):
        row["in_progress"] = True
    if attrs.get("auto_update"):
        row["auto_update"] = True
    if summary := attrs.get("release_summary"):
        row["release_summary"] = sanitize_untrusted_text(summary, 255)
    if url := attrs.get("release_url"):
        row["release_url"] = sanitize_untrusted_text(url, 300)
    return row


def async_list_updates(hass: HomeAssistant, *, include_skipped: bool = False) -> dict[str, Any]:
    """Updates waiting to be installed, grouped by where they come from."""
    registry = er.async_get(hass)
    rows = []
    for state in hass.states.async_all("update"):
        skipped = bool(state.attributes.get("skipped_version"))
        if state.state != "on" and not (include_skipped and skipped):
            continue
        rows.append(_row(state, registry.async_get(state.entity_id)))
    order = {"home_assistant": 0, "app": 1, "hacs": 2, "device": 3, "other": 4}
    rows.sort(key=lambda r: (order[r["category"]], r["title"].casefold()))
    return {
        "updates": rows,
        "hint": (
            "Install with execute_command service update.install (backup: true where "
            "offered); hide one with update.skip."
        )
        if rows
        else "Everything is up to date.",
    }


def _clean_notes(text: str) -> str:
    """Release notes keep their lines (they are markdown) but nothing else
    that could pass for structure: control characters go, long runs of blank
    lines shrink, and the length is bounded."""
    text = _CONTROL.sub("", text.replace("\r\n", "\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > _NOTES_LIMIT:
        text = text[: _NOTES_LIMIT - 3] + "..."
    return text


async def async_release_notes(hass: HomeAssistant, entity_id: str) -> dict[str, Any]:
    """The full release notes of one update, where its integration provides them."""
    from homeassistant.components.update import UpdateEntityFeature  # noqa: PLC0415

    entity_id = str(entity_id or "").strip().lower()
    shown = sanitize_untrusted_text(entity_id, 80)
    if not entity_id.startswith("update."):
        return {"error": f"'{shown}' is not an update. Pass an update.* entity_id."}
    component = hass.data.get("update")
    get_entity = getattr(component, "get_entity", None)
    entity = get_entity(entity_id) if get_entity else None
    if entity is None:
        return {"error": f"No update {shown}. Call list_updates for its entity_id."}
    if UpdateEntityFeature.RELEASE_NOTES not in entity.supported_features:
        summary = hass.states.get(entity_id)
        attrs = summary.attributes if summary else {}
        return {
            "entity_id": entity_id,
            "release_notes": None,
            "hint": "This update has no full release notes here; see release_summary or release_url.",
            **(
                {"release_summary": sanitize_untrusted_text(attrs["release_summary"], 255)}
                if attrs.get("release_summary")
                else {}
            ),
            **(
                {"release_url": sanitize_untrusted_text(attrs["release_url"], 300)}
                if attrs.get("release_url")
                else {}
            ),
        }
    if entity.available is False:
        return {"error": f"{shown} is unavailable, so its release notes cannot be read."}
    try:
        notes = await entity.async_release_notes()
    except HomeAssistantError as exc:
        return {
            "error": f"The release notes could not be read: {sanitize_untrusted_text(str(exc), 200)}"
        }
    return {"entity_id": entity_id, "release_notes": _clean_notes(notes) if notes else None}


# ── Chat: installing behind a confirmation card ───────────────────────────

# UpdateEntityFeature values, read off the state's supported_features.
_INSTALL: Final = 1
_SPECIFIC_VERSION: Final = 2
_BACKUP: Final = 8


def _installable(hass: HomeAssistant, entity_id: str) -> tuple[State, int] | str:
    entity_id = str(entity_id or "").strip().lower()
    shown = sanitize_untrusted_text(entity_id, 80)
    state = hass.states.get(entity_id) if entity_id.startswith("update.") else None
    if state is None:
        return f"No update {shown}. check_system lists the ones waiting."
    features = int(state.attributes.get("supported_features") or 0)
    if not features & _INSTALL:
        return f"{shown} cannot be installed from Home Assistant."
    if state.attributes.get("in_progress"):
        return f"{shown} is already being installed."
    if state.state != "on":
        return f"{shown} has no update waiting."
    return state, features


def _promise(registry_id: str, version: str, backup: bool) -> str:
    return json.dumps({"id": registry_id, "version": version, "backup": backup}, sort_keys=True)


async def async_preview_install(hass: HomeAssistant, entity_id: str) -> dict[str, Any]:
    """A confirmation card for installing an update, pinned to its version."""
    found = _installable(hass, entity_id)
    if isinstance(found, str):
        return {"error": found}
    state, features = found
    entry = er.async_get(hass).async_get(state.entity_id)
    row = _row(state, entry)
    label = (
        f"Install {row['title']} {row.get('installed_version') or '?'} → "
        f"{row.get('latest_version') or '?'}"
    )
    if features & _BACKUP:
        label += ", after a backup"
    if row["category"] == "home_assistant":
        label += " — Home Assistant restarts"
    if summary := row.get("release_summary"):
        label += f". {summary}"
    return {
        "requires_approval": True,
        "destructive": {
            "kind": "update",
            "verb": "install",
            "target_id": state.entity_id,
            "entity_id": state.entity_id,
            "name": row["title"],
            "label": sanitize_untrusted_text(label, 300),
            # What the user approves: THIS update (its registry entry — an
            # entity_id can be taken by another), at the version named, with
            # the backup promised. Each is re-checked on confirm.
            "fingerprint": _promise(
                entry.id if entry else "",
                str(state.attributes.get("latest_version") or ""),
                bool(features & _BACKUP),
            ),
        },
    }


async def async_install_confirmed(
    hass: HomeAssistant, entity_id: str, promise: str
) -> dict[str, Any]:
    """Install exactly what the card named: this update, at that version, with
    the backup it promised — or nothing."""
    try:
        approved = json.loads(promise)
        expected_version = str(approved["version"])
    except (ValueError, KeyError, TypeError):
        return {"error": "The confirmation is missing what it approved; ask again."}
    found = _installable(hass, entity_id)
    if isinstance(found, str):
        return {"error": found}
    state, features = found
    entry = er.async_get(hass).async_get(state.entity_id)
    if (entry.id if entry else "") != approved.get("id", ""):
        return {"error": "That is not the update that was shown; ask again."}
    if approved.get("backup") and not features & _BACKUP:
        return {
            "error": "The backup the card promised can no longer be made, so nothing was installed."
        }
    if str(state.attributes.get("latest_version") or "") != expected_version:
        return {
            "error": (
                f"A different version ({sanitize_untrusted_text(state.attributes.get('latest_version'), 40)}) "
                "is offered now than the one shown; ask again."
            )
        }
    data: dict[str, Any] = {"entity_id": state.entity_id}
    if approved.get("backup"):
        data["backup"] = True
    if features & _SPECIFIC_VERSION:
        data["version"] = expected_version
    # Not awaited to completion: an install can take minutes, and a core
    # update restarts Home Assistant under the request. Its progress shows on
    # the update entity (and in check_system).
    await hass.services.async_call("update", "install", data, blocking=False)
    return {"status": "started", "entity_id": state.entity_id, "version": expected_version}
