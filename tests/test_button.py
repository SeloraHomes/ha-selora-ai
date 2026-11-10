"""The hub's action buttons."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.button import async_setup_entry
from custom_components.selora_ai.const import DOMAIN


async def test_no_button_removes_the_homes_integrations(hass: HomeAssistant) -> None:
    """ "Reset Everything" removed every integration and re-accepted every
    discovered device with a copied config: no button may do that, and a hub
    that had it loses the entity rather than keeping it unavailable."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    stale = registry.async_get_or_create(
        "button", DOMAIN, f"{DOMAIN}_reset", config_entry=entry
    ).entity_id
    added: list[Any] = []

    await async_setup_entry(hass, entry, lambda entities, **_kw: added.extend(entities))

    assert registry.async_get(stale) is None
    assert f"{DOMAIN}_reset" not in {button.unique_id for button in added}
    assert {button.unique_id for button in added} == {
        f"{DOMAIN}_discover",
        f"{DOMAIN}_auto_setup",
        f"{DOMAIN}_cleanup",
    }
