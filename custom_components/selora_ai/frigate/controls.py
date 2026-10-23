"""Why a Frigate switch did not turn on.

Frigate lets recording, detection and snapshots be switched at runtime only
when the camera's configuration enables them: with ``record.enabled: false``
the recording switch takes the command and stays off. Frigate says so per
section as ``enabled_in_config``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from homeassistant.helpers import entity_registry as er

from .client import FRIGATE_DOMAIN, FrigateClient, FrigateError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# The Frigate integration's switch name suffix → the camera config section.
_SECTIONS: Final = {
    "recordings": "record",
    "detect": "detect",
    "snapshots": "snapshots",
    "motion": "motion",
    "audio": "audio",
}


def _switch_section(entry: er.RegistryEntry) -> tuple[str, str] | None:
    """``(camera, section)`` for a Frigate switch, from its unique id
    ``<entry_id>:switch:<camera>_<name>``."""
    if entry.platform != FRIGATE_DOMAIN or entry.domain != "switch":
        return None
    name = str(entry.unique_id).rsplit(":", 1)[-1]
    for suffix, section in _SECTIONS.items():
        if name.endswith(f"_{suffix}"):
            return name[: -len(suffix) - 1], section
    return None


async def async_disabled_in_config(hass: HomeAssistant, entity_ids: list[str]) -> list[str]:
    """The Frigate switches among ``entity_ids`` whose feature the camera's
    config disables, so no command can turn them on."""
    ent_reg = er.async_get(hass)
    switches = {
        eid: found
        for eid in entity_ids
        if (entry := ent_reg.async_get(eid)) is not None
        and (found := _switch_section(entry)) is not None
    }
    if not switches or (client := FrigateClient.from_hass(hass)) is None:
        return []
    try:
        cameras = (await client.get_config()).get("cameras") or {}
    except FrigateError:
        return []
    return [
        eid
        for eid, (camera, section) in switches.items()
        if ((cameras.get(camera) or {}).get(section) or {}).get("enabled_in_config") is False
    ]
