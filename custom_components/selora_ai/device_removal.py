"""Deleting a device from Home Assistant, the way HA's own device page does.

A device registry entry is owned by its config entry, so dropping it from the
registry alone is not deletion: the owning integration re-creates it on its next
refresh. HA's supported path is to ask that integration to release the device
(``async_remove_config_entry_device``), then detach the entry — the registry
deletes the device once its last entry is gone, and the entity registry follows
by dropping its entities. That is what :func:`async_remove_device` mirrors (see
``homeassistant.components.config.device_registry``).

Two devices are refused up front by :func:`device_is_removable`, so the Health
card never offers a deletion that would fail or half-succeed: one whose
integration doesn't implement the hook (HA hides its own delete button for the
same reason), and one with several owners, which cannot be released atomically.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant import loader
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class DeviceRespondingError(HomeAssistantError):
    """The device answered again before the removal could be committed."""


def device_is_removable(hass: HomeAssistant, device_id: str) -> bool:
    """True when deleting this device would actually make it disappear — in one
    step, with nothing left behind if it doesn't.

    Its owning config entry must support device removal, and there must be only
    one. Releasing several owners cannot be atomic: the hook *is* the removal, so
    there is no way to ask an integration whether it would agree without it
    acting, and the first release cannot be undone when a later owner refuses —
    which it may, `supports_remove_device` only says the hook exists, not that it
    returns True for this device. That would leave a half-deleted device behind a
    reported failure. HA's own device page deletes per config entry for the same
    reason; a shared device belongs there, where the user picks an owner.
    """
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return False
    entry_ids = device.config_entries
    if not entry_ids:
        # No owner to re-create it — a registry-only entry can just be dropped.
        return True
    if len(entry_ids) > 1:
        return False
    entry = hass.config_entries.async_get_entry(next(iter(entry_ids)))
    return entry is not None and bool(entry.supports_remove_device)


async def _release_from_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device: dr.DeviceEntry,
    still_gone: Callable[[], bool] | None,
) -> None:
    """Ask ``entry``'s integration to release the device, then detach the entry."""
    dev_reg = dr.async_get(hass)
    device_id = device.id
    try:
        integration = await loader.async_get_integration(hass, entry.domain)
        component = await integration.async_get_component()
    except (ImportError, loader.IntegrationNotFound) as err:
        raise HomeAssistantError(f"Could not load the {entry.domain} integration") from err

    remove = getattr(component, "async_remove_config_entry_device", None)
    if remove is None:
        raise HomeAssistantError(f"{entry.domain} does not support deleting devices")
    # Last possible moment. Loading the integration above awaits — on a cold
    # import, for a while — so whatever the caller checked before calling is
    # already stale, and a device that answered again in that window would be
    # deleted. This is as tight as it gets rather than airtight: the hook itself
    # awaits, and HA offers no lock that would hold a device still.
    if still_gone is not None and not still_gone():
        raise DeviceRespondingError("The device started responding again")
    if not await remove(hass, entry, device):
        raise HomeAssistantError(f"{entry.domain} refused to delete the device")

    # The integration may have removed the device itself — that is fine, and
    # detaching an entry from a device that no longer exists would raise.
    if dev_reg.async_get(device_id) is not None:
        dev_reg.async_update_device(device_id, remove_config_entry_id=entry.entry_id)


async def async_remove_device(
    hass: HomeAssistant,
    device_id: str,
    *,
    still_gone: Callable[[], bool] | None = None,
) -> str:
    """Delete a device and its entities. Returns the deleted device's name.

    Exactly one owner is released, so the operation either deletes the device or
    changes nothing — see :func:`device_is_removable` for why several owners are
    refused rather than released in turn.

    ``still_gone`` is re-evaluated immediately before the integration's hook
    fires, after the awaits that load it, and aborts with
    :class:`DeviceRespondingError` if it returns False. Pass it whenever the
    device is being deleted *because* it is offline: a caller's own check is
    stale by the time the destructive call happens.

    Raises :class:`HomeAssistantError` when the device is unknown, has several
    owners, its integration won't release it, or it outlives the removal (still
    attached to an entry) — the caller reports that to the user rather than
    claiming a deletion that didn't happen.
    """
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if device is None:
        raise HomeAssistantError("Unknown device")

    name = device.name_by_user or device.name or device_id
    entry_ids = list(device.config_entries)
    if not entry_ids:
        dev_reg.async_remove_device(device_id)
        _LOGGER.info("Deleted registry-only device %s (%s)", name, device_id)
        return name
    if len(entry_ids) > 1:
        raise HomeAssistantError(
            "That device is shared by several integrations — delete it from "
            "Settings, one integration at a time"
        )

    entry = hass.config_entries.async_get_entry(entry_ids[0])
    if entry is None:
        # Its owner is gone but the device outlived it. HA removes a device with
        # its last config entry, so this is a registry we don't understand —
        # report it rather than inventing a force-removal for an unexplained state.
        raise HomeAssistantError("That device's integration is no longer set up")

    await _release_from_entry(hass, entry, device, still_gone)
    if dev_reg.async_get(device_id) is not None:
        raise HomeAssistantError("The device is still claimed by an integration")

    _LOGGER.info("Deleted device %s (%s)", name, device_id)
    return name
