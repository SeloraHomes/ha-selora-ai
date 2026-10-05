"""Read and change the Energy dashboard's configuration.

Through Home Assistant's own energy manager, as its ``energy/get_prefs`` and
``energy/save_prefs`` websocket commands do: the update is validated with the
schemas those commands use, and the manager replaces only the top-level lists
it is given. A save is checked against a hash of what the caller last read, so
an edit made in the Energy settings meanwhile is not overwritten.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Final

from homeassistant.exceptions import HomeAssistantError
import voluptuous as vol

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.components.energy.data import EnergyManager
    from homeassistant.components.energy.validate import EnergyPreferencesValidation
    from homeassistant.core import HomeAssistant

# Every list a release may have; ``device_consumption_water`` is newer than
# the oldest Home Assistant this integration supports, so the lists actually
# in use are the ones the manager's defaults name.
_KEYS: Final = ("energy_sources", "device_consumption", "device_consumption_water")


def _supported_keys(manager: EnergyManager) -> tuple[str, ...]:
    defaults = manager.default_preferences()
    return tuple(key for key in _KEYS if key in defaults)


def _prefs_hash(prefs: Any) -> str:
    payload = json.dumps(prefs, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


async def _manager(hass: HomeAssistant) -> EnergyManager | str:
    if "energy" not in hass.config.components:
        return "The energy integration is not loaded. It is part of default_config."
    from homeassistant.components.energy.data import async_get_manager  # noqa: PLC0415

    return await async_get_manager(hass)


def _issues(validation: EnergyPreferencesValidation) -> list[dict[str, Any]]:
    """Home Assistant's validation, flattened to one row per issue.

    Walked rather than taken from ``as_dict``, whose affected entities are a
    set of (entity_id, detail) pairs that do not survive JSON.
    """
    rows: list[dict[str, Any]] = []
    for key in _KEYS:
        for index, issues in enumerate(getattr(validation, key, None) or ()):
            for issue in issues.issues.values():
                rows.append(
                    {
                        "where": f"{key}[{index}]",
                        "issue": issue.type,
                        "entities": sorted({str(eid) for eid, _ in issue.affected_entities}),
                    }
                )
    return rows


async def _validation(hass: HomeAssistant) -> list[dict[str, Any]]:
    from homeassistant.components.energy.validate import async_validate  # noqa: PLC0415

    return _issues(await async_validate(hass))


async def async_get_energy_prefs(hass: HomeAssistant) -> dict[str, Any]:
    """The configuration, what Home Assistant finds wrong with it, and its hash."""
    manager = await _manager(hass)
    if isinstance(manager, str):
        return {"error": manager}
    prefs = manager.data or manager.default_preferences()
    return {
        "prefs": prefs,
        "config_hash": _prefs_hash(prefs),
        "issues": await _validation(hass),
    }


async def async_set_energy_prefs(
    hass: HomeAssistant, update: dict[str, Any], config_hash: str | None
) -> dict[str, Any]:
    """Replace the lists given in *update*, if nothing changed since the read."""
    from homeassistant.components.energy.data import (  # noqa: PLC0415
        DEVICE_CONSUMPTION_SCHEMA,
        ENERGY_SOURCE_SCHEMA,
    )

    if not config_hash:
        return {
            "error": (
                "Pass the config_hash get_energy_prefs returned, so a change made "
                "meanwhile is not overwritten."
            )
        }
    manager = await _manager(hass)
    if isinstance(manager, str):
        return {"error": manager}
    keys = _supported_keys(manager)
    unsupported = [k for k in _KEYS if k not in keys and update.get(k) is not None]
    if unsupported:
        return {
            "error": (
                f"This Home Assistant version has no {', '.join(unsupported)} in its "
                "energy configuration."
            )
        }
    supplied = {key: update[key] for key in keys if update.get(key) is not None}
    if not supplied:
        return {"error": f"Nothing to change. Pass {', '.join(keys[:-1])} or {keys[-1]}."}

    schema = vol.Schema(
        {
            vol.Optional("energy_sources"): ENERGY_SOURCE_SCHEMA,
            **{vol.Optional(key): [DEVICE_CONSUMPTION_SCHEMA] for key in keys[1:]},
        }
    )
    try:
        validated = schema(supplied)
    except vol.Invalid as exc:
        return {
            "error": (
                "Home Assistant would refuse that energy configuration: "
                f"{sanitize_untrusted_text(str(exc), 300)}"
            )
        }
    # Checked with no await before the update replaces the data, so nothing
    # can land in between.
    if _prefs_hash(manager.data or manager.default_preferences()) != config_hash:
        return {
            "error": (
                "The energy configuration changed since it was read. Call "
                "get_energy_prefs again and apply the change to what it returns."
            )
        }
    try:
        await manager.async_update(validated)
    except (HomeAssistantError, ValueError) as exc:
        return {"error": (f"Home Assistant refused it: {sanitize_untrusted_text(str(exc), 200)}")}
    return {
        "status": "saved",
        "changed": sorted(supplied),
        "config_hash": _prefs_hash(manager.data),
        # Saving does not check that the statistics exist or have the right
        # unit; Home Assistant's validation does, and this is what the Energy
        # dashboard would now warn about.
        "issues": await _validation(hass),
    }
