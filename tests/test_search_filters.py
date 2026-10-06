"""Searching entities by area, floor, state and label.

``search_entities`` filtered by domain and device class only, so "what is
unavailable?", "which lights are on upstairs?" or "everything labelled
holiday" had no single call — a whole-home snapshot was the fallback, which is
what the search exists to avoid on a large home.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import TOOL_SEARCH_ENTITIES


@pytest.fixture
def home(hass: HomeAssistant) -> HomeAssistant:
    upstairs = fr.async_get(hass).async_create("Upstairs")
    bedroom = ar.async_get(hass).async_create("Bedroom")
    ar.async_get(hass).async_update(bedroom.id, floor_id=upstairs.floor_id)
    kitchen = ar.async_get(hass).async_create("Kitchen")
    holiday = lr.async_get(hass).async_create("holiday")

    config = MockConfigEntry(domain="hue")
    config.add_to_hass(hass)
    bulb = dr.async_get(hass).async_get_or_create(
        config_entry_id=config.entry_id, identifiers={("hue", "b1")}, name="Bulb"
    )
    dr.async_get(hass).async_update_device(bulb.id, area_id=bedroom.id, labels={holiday.label_id})
    registry = er.async_get(hass)
    # Its area through its device; the label too.
    registry.async_get_or_create(
        "light", "hue", "b1", device_id=bulb.id, suggested_object_id="bedside"
    )
    registry.async_get_or_create("light", "demo", "k", suggested_object_id="kitchen")
    registry.async_update_entity("light.kitchen", area_id=kitchen.id)
    registry.async_get_or_create("sensor", "demo", "s", suggested_object_id="porch_temp")
    registry.async_update_entity("sensor.porch_temp", labels={holiday.label_id})
    registry.async_get_or_create("input_text", "demo", "w", suggested_object_id="wifi")

    hass.states.async_set("light.bedside", "on")
    hass.states.async_set("light.kitchen", "off")
    hass.states.async_set("sensor.porch_temp", "unavailable")
    hass.states.async_set("input_text.wifi", "on", {"mode": "password"})
    return hass


async def _ids(hass: HomeAssistant, **arguments: Any) -> list[str]:
    result = await mcp_dispatch._get_tool_handlers()[TOOL_SEARCH_ENTITIES](hass, arguments)
    assert "error" not in result, result
    return [m["entity_id"] for m in result["matches"]]


async def test_by_area_including_through_the_device(home: HomeAssistant) -> None:
    assert await _ids(home, area="Bedroom") == ["light.bedside"]
    assert await _ids(home, area="kitchen") == ["light.kitchen"]


async def test_a_floor_covers_its_areas(home: HomeAssistant) -> None:
    assert await _ids(home, area="Upstairs") == ["light.bedside"]


async def test_by_state_alone_or_with_others(home: HomeAssistant) -> None:
    assert await _ids(home, state="unavailable") == ["sensor.porch_temp"]
    assert await _ids(home, state="on", domain="light") == ["light.bedside"]
    assert await _ids(home, state="on,off", domain="light") == ["light.bedside", "light.kitchen"]


async def test_a_password_field_never_matches_a_state(home: HomeAssistant) -> None:
    """Matching would confirm a guessed secret."""
    assert "input_text.wifi" not in await _ids(home, state="on")


async def test_by_label_on_the_entity_or_its_device(home: HomeAssistant) -> None:
    assert await _ids(home, label="holiday") == ["light.bedside", "sensor.porch_temp"]


async def test_a_labelled_area_brings_its_entities(home: HomeAssistant) -> None:
    """As a label_id target does — the device's area counts too."""
    garden = lr.async_get(home).async_create("garden")
    bedroom = ar.async_get(home).async_get_area_by_name("Bedroom")
    ar.async_get(home).async_update(bedroom.id, labels={garden.label_id})

    assert await _ids(home, label="garden") == ["light.bedside"]


async def test_filters_combine_with_a_query(home: HomeAssistant) -> None:
    assert await _ids(home, query="bedside", area="Kitchen") == []
    assert await _ids(home, query="bedside", area="Upstairs") == ["light.bedside"]


async def test_an_unknown_area_or_label_says_what_exists(home: HomeAssistant) -> None:
    handlers = mcp_dispatch._get_tool_handlers()
    area = await handlers[TOOL_SEARCH_ENTITIES](home, {"area": "Attic"})
    label = await handlers[TOOL_SEARCH_ENTITIES](home, {"label": "work"})

    assert "Bedroom" in area["error"]
    assert "holiday" in label["error"]
