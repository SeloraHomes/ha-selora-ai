"""An entity's "Show as" and its display settings.

Both are in every entity's settings dialog, and neither had a tool: "show the
garage door sensor as a garage door" or "show the outdoor temperature in °F
with one decimal" ended at "do it in Settings". Home Assistant stores both
without checking them and ignores a wrong value later, so each is checked here
against what the dialog offers and the tables Home Assistant reads.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import TOOL_UPDATE_ENTITY


def _entity(hass: HomeAssistant, domain: str, uid: str, **kwargs: Any) -> str:
    entry = er.async_get(hass).async_get_or_create(
        domain, "demo", uid, suggested_object_id=uid, **kwargs
    )
    hass.states.async_set(entry.entity_id, "on")
    return entry.entity_id


async def _update(hass: HomeAssistant, entity_id: str, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[TOOL_UPDATE_ENTITY](
        hass, {"entity_id": entity_id, **arguments}
    )


def _entry(hass: HomeAssistant, entity_id: str) -> er.RegistryEntry:
    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None
    return entry


async def test_a_door_sensor_shows_as_a_window_and_back(hass: HomeAssistant) -> None:
    door = _entity(hass, "binary_sensor", "front", original_device_class="door")

    shown = await _update(hass, door, show_as="window")
    assert shown["changed"] == ["device_class"]
    assert _entry(hass, door).device_class == "window"

    await _update(hass, door, clear=["show_as"])
    assert _entry(hass, door).device_class is None


async def test_only_within_its_kind(hass: HomeAssistant) -> None:
    door = _entity(hass, "binary_sensor", "front", original_device_class="door")
    plug = _entity(hass, "switch", "plug")
    blind = _entity(hass, "cover", "lounge", original_device_class="shutter")

    other_kind = await _update(hass, door, show_as="motion")
    switch = await _update(hass, plug, show_as="light")
    cover = await _update(hass, blind, show_as="blind")

    assert "window, door, garage_door, opening" in other_kind["error"]
    assert "switch_as_x" in switch["error"]
    assert cover["changed"] == ["device_class"]


async def test_a_sensor_is_shown_in_another_unit_and_precision(hass: HomeAssistant) -> None:
    outdoor = _entity(
        hass, "sensor", "outdoor", original_device_class="temperature", unit_of_measurement="°C"
    )

    result = await _update(
        hass, outdoor, settings={"unit_of_measurement": "°F", "display_precision": 1}
    )
    assert result["settings"] == {"unit_of_measurement": "°F", "display_precision": 1}
    assert _entry(hass, outdoor).options["sensor"] == {
        "unit_of_measurement": "°F",
        "display_precision": 1,
    }

    reset = await _update(hass, outdoor, settings={"display_precision": None})
    assert reset["settings"] == {"unit_of_measurement": "°F"}


@pytest.mark.parametrize(
    ("settings", "says"),
    [
        ({"unit_of_measurement": "kWh"}, "can be shown in"),
        ({"display_precision": 9}, "0 to 6"),
        ({"brightness": 3}, "settings are unit_of_measurement, display_precision"),
    ],
)
async def test_a_setting_that_would_be_ignored_is_refused(
    hass: HomeAssistant, settings: dict[str, Any], says: str
) -> None:
    outdoor = _entity(
        hass, "sensor", "outdoor", original_device_class="temperature", unit_of_measurement="°C"
    )

    result = await _update(hass, outdoor, settings=settings)

    assert says in result["error"]
    assert not _entry(hass, outdoor).options.get("sensor")


async def test_a_sensor_in_a_unit_home_assistant_cannot_convert_says_so(
    hass: HomeAssistant,
) -> None:
    odd = _entity(
        hass, "sensor", "odd", original_device_class="temperature", unit_of_measurement="degrees"
    )

    result = await _update(hass, odd, settings={"unit_of_measurement": "°F"})

    assert "cannot convert" in result["error"]


async def test_a_unitless_sensor_says_so(hass: HomeAssistant) -> None:
    count = _entity(hass, "sensor", "visitors")

    result = await _update(hass, count, settings={"unit_of_measurement": "°C"})

    assert "cannot be changed" in result["error"]


async def test_other_domains_settings(hass: HomeAssistant) -> None:
    hass.states.async_set("zone.work", "0")
    phone = _entity(hass, "device_tracker", "phone", capabilities={"tracking_type": "connection"})
    gps = _entity(hass, "device_tracker", "car", capabilities={"tracking_type": "position"})
    agenda = _entity(hass, "calendar", "family")
    forecast = _entity(hass, "weather", "home")

    assert (await _update(hass, phone, settings={"associated_zone": "zone.work"}))["settings"]
    assert (await _update(hass, agenda, settings={"color": "#ff8800"}))["settings"]
    assert (await _update(hass, forecast, settings={"wind_speed_unit": "km/h"}))["settings"]
    # A GPS tracker places itself and never reads an associated zone.
    gps_zone = await _update(hass, gps, settings={"associated_zone": "zone.work"})
    assert "GPS" in gps_zone["error"]
    assert (
        "zone that exists"
        in (await _update(hass, phone, settings={"associated_zone": "zone.nowhere"}))["error"]
    )


async def test_a_lock_code_is_not_set_from_here(hass: HomeAssistant) -> None:
    door = _entity(hass, "lock", "front")

    result = await _update(hass, door, settings={"default_code": "1234"})

    assert "code itself" in result["error"]
    assert "lock" not in _entry(hass, door).options


async def test_a_light_has_no_display_settings(hass: HomeAssistant) -> None:
    lamp = _entity(hass, "light", "lamp")

    result = await _update(hass, lamp, settings={"color": "red"})

    assert "no display settings" in result["error"]


async def test_a_disable_card_is_not_offered_for_a_call_that_would_be_refused(
    hass: HomeAssistant,
) -> None:
    from unittest.mock import MagicMock

    from custom_components.selora_ai.tool_executor import ToolExecutor

    door = _entity(hass, "binary_sensor", "front", original_device_class="door")

    result = await ToolExecutor(hass, MagicMock(), is_admin=True).execute(
        "update_entity", {"entity_id": door, "disabled": True, "show_as": "motion"}
    )

    assert "window, door" in result["error"]
    assert not result.get("requires_approval")
