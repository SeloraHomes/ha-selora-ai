"""List integrations, and reload, enable, disable, remove or reconfigure one.

An integration here is a config entry: what Settings → Devices & services shows
as one card. Everything goes through ``hass.config_entries``, as that page's
websocket commands do. Options are changed by driving the entry's own options
flow the way ``helper_flow`` drives a config flow: a call without ``options``
describes the form, a call with them submits it, so Home Assistant's own
validation decides.

Selora AI's own entry is refused for every change: disabling, removing or
reloading it would cut the connection the request came in on, and its options
hold the AI provider's credentials, which are never configured automatically.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.config_entries import ConfigEntryDisabler, OperationNotAllowed, UnknownEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .helper_flow import _FLOW_ERRORS
from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# States that mean the integration is not working.
_PROBLEM_STATES: Final = frozenset(
    {"setup_error", "setup_retry", "migration_error", "failed_unload"}
)


def _counts(hass: HomeAssistant, entry_id: str) -> tuple[int, int]:
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry_id)
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry_id)
    return len(devices), len(entities)


def _row(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    devices, entities = _counts(hass, entry.entry_id)
    row: dict[str, Any] = {
        "entry_id": entry.entry_id,
        "domain": entry.domain,
        # Titles are often set from the account or device they connect to.
        "title": sanitize_untrusted_text(entry.title or "", 80),
        "state": entry.state.value,
        "devices": devices,
        "entities": entities,
        "supports_options": entry.supports_options,
    }
    if entry.reason:
        row["reason"] = sanitize_untrusted_text(str(entry.reason), 200)
    if entry.disabled_by:
        row["disabled_by"] = str(entry.disabled_by.value)
    return row


async def async_list_integrations(
    hass: HomeAssistant, domain: str | None = None, *, problems_only: bool = False
) -> dict[str, Any]:
    """Every config entry, or one domain's, or only those not working."""
    wanted = str(domain or "").strip().lower()
    rows = [
        _row(hass, entry)
        for entry in hass.config_entries.async_entries()
        if (not wanted or entry.domain == wanted)
        and (not problems_only or entry.state.value in _PROBLEM_STATES)
    ]
    rows.sort(key=lambda r: (r["domain"], r["title"]))
    return {
        "integrations": rows,
        "not_working": sum(1 for r in rows if r["state"] in _PROBLEM_STATES),
    }


def _entry(hass: HomeAssistant, entry_id: str, verb: str) -> ConfigEntry | str:
    entry = hass.config_entries.async_get_entry(str(entry_id or "").strip())
    if entry is None:
        return (
            f"No integration with entry_id {sanitize_untrusted_text(str(entry_id), 40)}. "
            "Call list_integrations for it."
        )
    if entry.domain == DOMAIN:
        return f"Selora AI does not {verb} itself. Do it under Settings → Devices & services."
    return entry


async def async_reload_integration(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    """Reload an integration, and say what state it came back in."""
    entry = _entry(hass, entry_id, "reload")
    if isinstance(entry, str):
        return {"error": entry}
    if entry.disabled_by:
        return {"error": "That integration is disabled. Enable it instead."}
    try:
        reloaded = await hass.config_entries.async_reload(entry.entry_id)
    except (OperationNotAllowed, UnknownEntry, HomeAssistantError) as exc:
        return {"error": f"It could not be reloaded: {sanitize_untrusted_text(str(exc), 200)}"}
    return {"status": "reloaded" if reloaded else "not_reloaded", **_row(hass, entry)}


async def async_set_integration_enabled(
    hass: HomeAssistant, entry_id: str, enabled: bool
) -> dict[str, Any]:
    """Enable or disable an integration; its devices and entities follow."""
    entry = _entry(hass, entry_id, "enable or disable")
    if isinstance(entry, str):
        return {"error": entry}
    try:
        reloaded = await hass.config_entries.async_set_disabled_by(
            entry.entry_id, None if enabled else ConfigEntryDisabler.USER
        )
    except (OperationNotAllowed, UnknownEntry, HomeAssistantError) as exc:
        return {"error": f"It could not be changed: {sanitize_untrusted_text(str(exc), 200)}"}
    # Home Assistant's own command reads a failed unload or load the same way:
    # the setting is saved, and takes effect on the next restart.
    return {
        "status": "enabled" if enabled else "disabled",
        **({"require_restart": True} if not reloaded else {}),
        **_row(hass, entry),
    }


async def async_remove_integration(
    hass: HomeAssistant, entry_id: str, *, confirmed: bool = False
) -> dict[str, Any]:
    """Remove an integration with its devices and entities, once confirmed."""
    entry = _entry(hass, entry_id, "remove")
    if isinstance(entry, str):
        return {"error": entry}
    row = _row(hass, entry)
    if not confirmed:
        return {
            "requires_confirmation": True,
            "integration": row,
            "hint": (
                f"This removes {row['title'] or entry.domain} with its {row['devices']} "
                f"device(s) and {row['entities']} entit(ies); automations using them "
                "stop working. Tell the user, and only once they agree call again with "
                "confirmed=true."
            ),
        }
    try:
        # Shielded as Home Assistant's own endpoint does: a dropped request must
        # not leave the entry removed from memory but not from storage.
        result = await asyncio.shield(
            hass.async_create_task(
                hass.config_entries.async_remove(entry.entry_id),
                f"selora_ai remove {entry.domain} entry",
            )
        )
    except (OperationNotAllowed, UnknownEntry, HomeAssistantError) as exc:
        return {"error": f"It could not be removed: {sanitize_untrusted_text(str(exc), 200)}"}
    return {
        "status": "removed",
        "domain": entry.domain,
        "title": row["title"],
        "require_restart": bool(result.get("require_restart")),
    }


async def async_integration_options(
    hass: HomeAssistant,
    entry_id: str,
    kind: str | None,
    options: dict[str, Any] | None,
    flow_id: str | None = None,
) -> dict[str, Any]:
    """Describe the step an integration's options flow is on, or answer it.

    Held open between calls like a helper's setup (``flow_sessions``), so a
    multi-step options flow is walked one step per call by ``flow_id``.
    ``None`` describes; an empty mapping is a submission — some options forms
    take ``{}`` on purpose (clearing optional credentials).
    """
    from .flow_sessions import async_drive  # noqa: PLC0415

    entry = _entry(hass, entry_id, "change the options of")
    if isinstance(entry, str):
        return {"error": entry}
    if not entry.supports_options:
        return {"error": f"{entry.domain} has no options to change."}

    manager = hass.config_entries.options
    outcome = await async_drive(
        hass,
        manager,
        owner=("options", entry.entry_id),
        start=lambda: manager.async_init(entry.entry_id),
        flow_id=flow_id,
        choice=kind,
        values=options,
        values_param="options",
        errors=_FLOW_ERRORS,
    )
    if "done" not in outcome:
        return {"entry_id": entry.entry_id, **outcome}
    await hass.async_block_till_done()
    return {"status": "saved", **_row(hass, entry)}
