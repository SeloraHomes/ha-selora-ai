"""Tests for creating storage helpers (``input_*``, counter, timer) over MCP.

Chat proposes these for the panel to create; MCP has no panel, so it creates
them through the collection behind each component's ``<domain>/create``
command. That layout is Home Assistant's, not an API, so the first test pins it
for every supported domain against the installed core.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai import helper_manager
from custom_components.selora_ai.helper_manager import CREATABLE_HELPER_DOMAINS
from custom_components.selora_ai.mcp_server import scripts_helpers as mcp_scripts_helpers


@pytest.fixture
async def helpers_loaded(hass: HomeAssistant) -> None:
    for domain in CREATABLE_HELPER_DOMAINS:
        assert await async_setup_component(hass, domain, {})
    await hass.async_block_till_done()


async def _create(hass: HomeAssistant, **arguments: Any) -> dict[str, Any]:
    return await mcp_scripts_helpers._tool_create_helper(hass, arguments)


@pytest.mark.parametrize("domain", CREATABLE_HELPER_DOMAINS)
async def test_every_collection_is_reachable_on_this_core(
    hass: HomeAssistant, helpers_loaded: None, domain: str
) -> None:
    assert helper_manager._helper_collection(hass, domain) is not None


async def test_a_toggle_is_created_and_named_by_its_entity_id(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    result = await _create(hass, domain="input_boolean", name="Guest mode", icon="mdi:account")

    assert result["status"] == "created", result
    assert result["entity_id"] == "input_boolean.guest_mode"
    state = hass.states.get("input_boolean.guest_mode")
    assert state is not None
    assert state.attributes["friendly_name"] == "Guest mode"
    assert state.attributes["icon"] == "mdi:account"


async def test_a_dropdown_keeps_its_options(hass: HomeAssistant, helpers_loaded: None) -> None:
    result = await _create(
        hass, domain="input_select", name="House mode", options=["Home", "Away", "Night"]
    )

    state = hass.states.get(result["entity_id"])
    assert state.attributes["options"] == ["Home", "Away", "Night"]


async def test_a_timer_takes_its_duration(hass: HomeAssistant, helpers_loaded: None) -> None:
    result = await _create(hass, domain="timer", name="Laundry", duration="0:45:00")

    assert hass.states.get(result["entity_id"]).attributes["duration"] == "0:45:00"


async def test_counter_bounds_take_the_counter_spelling(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    result = await _create(hass, domain="counter", name="Visits", min=0, max=10)

    attributes = hass.states.get(result["entity_id"]).attributes
    assert attributes["minimum"] == 0
    assert attributes["maximum"] == 10


async def test_a_name_in_use_is_refused(hass: HomeAssistant, helpers_loaded: None) -> None:
    """HA would suffix the id and leave two helpers of one name."""
    await _create(hass, domain="input_boolean", name="Guest mode")

    result = await _create(hass, domain="input_boolean", name="guest  MODE")

    assert "already exists as input_boolean.guest_mode" in result["error"]
    assert len(hass.states.async_all("input_boolean")) == 1


async def test_what_ha_would_reject_is_refused(hass: HomeAssistant, helpers_loaded: None) -> None:
    result = await _create(hass, domain="input_select", name="Empty", options=[])

    assert "would refuse" in result["error"]
    assert hass.states.async_all("input_select") == []


async def test_an_unrecognised_layout_reports_the_limitation(
    hass: HomeAssistant, helpers_loaded: None
) -> None:
    hass.data["websocket_api"]["input_boolean/create"] = (lambda *_: None, None)

    result = await _create(hass, domain="input_boolean", name="Guest mode")

    assert "Settings → Devices & services → Helpers" in result["error"]
    assert hass.states.async_all("input_boolean") == []


async def test_a_password_text_helper_is_listed_without_its_value(hass: HomeAssistant) -> None:
    from custom_components.selora_ai.registry_manager import helper_overview

    hass.states.async_set("input_text.wifi", "hunter2-secret", {"mode": "password"})

    listed = await helper_overview(hass)

    assert "hunter2-secret" not in str(listed)
