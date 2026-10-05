"""Tests for reading and changing the Energy dashboard's configuration over MCP."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import (
    TOOL_GET_ENERGY_PREFS,
    TOOL_SET_ENERGY_PREFS,
)

FRIDGE = {"stat_consumption": "sensor.fridge_energy", "name": "Fridge"}
SOLAR = {
    "type": "solar",
    "stat_energy_from": "sensor.solar_energy",
    "config_entry_solar_forecast": None,
}


@pytest.fixture(autouse=True)
def _enable_custom_component(recorder_db_url: str, enable_custom_integrations: None) -> None:
    """The energy integration needs the recorder, whose database must be set up
    before ``hass`` — which the repo-wide autouse fixtures would start first."""


@pytest.fixture
async def energy(recorder_mock: Any, hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "energy", {})
    await hass.async_block_till_done()


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[tool](hass, arguments)


async def test_an_empty_configuration_reads_as_empty_lists(
    hass: HomeAssistant, energy: None
) -> None:
    result = await _mcp(hass, TOOL_GET_ENERGY_PREFS)

    assert result["prefs"] == {
        "energy_sources": [],
        "device_consumption": [],
        "device_consumption_water": [],
    }
    assert result["config_hash"]
    assert result["issues"] == []


async def test_a_change_replaces_only_the_lists_given(hass: HomeAssistant, energy: None) -> None:
    first = await _mcp(hass, TOOL_GET_ENERGY_PREFS)
    saved = await _mcp(
        hass,
        TOOL_SET_ENERGY_PREFS,
        config_hash=first["config_hash"],
        energy_sources=[SOLAR],
    )
    assert saved["status"] == "saved", saved

    second = await _mcp(
        hass,
        TOOL_SET_ENERGY_PREFS,
        config_hash=saved["config_hash"],
        device_consumption=[FRIDGE],
    )

    assert second["changed"] == ["device_consumption"]
    prefs = (await _mcp(hass, TOOL_GET_ENERGY_PREFS))["prefs"]
    assert [s["type"] for s in prefs["energy_sources"]] == ["solar"]
    assert prefs["device_consumption"][0]["stat_consumption"] == "sensor.fridge_energy"


async def test_the_issues_name_what_the_dashboard_will_warn_about(
    hass: HomeAssistant, energy: None
) -> None:
    first = await _mcp(hass, TOOL_GET_ENERGY_PREFS)

    saved = await _mcp(
        hass,
        TOOL_SET_ENERGY_PREFS,
        config_hash=first["config_hash"],
        device_consumption=[FRIDGE],
    )

    assert {(i["where"], i["issue"], tuple(i["entities"])) for i in saved["issues"]} == {
        ("device_consumption[0]", "entity_not_defined", ("sensor.fridge_energy",)),
        ("device_consumption[0]", "statistics_not_defined", ("sensor.fridge_energy",)),
    }


async def test_a_change_made_meanwhile_is_not_overwritten(
    hass: HomeAssistant, energy: None
) -> None:
    stale = await _mcp(hass, TOOL_GET_ENERGY_PREFS)
    await _mcp(
        hass,
        TOOL_SET_ENERGY_PREFS,
        config_hash=stale["config_hash"],
        device_consumption=[FRIDGE],
    )

    result = await _mcp(
        hass,
        TOOL_SET_ENERGY_PREFS,
        config_hash=stale["config_hash"],
        device_consumption=[],
    )

    assert "changed since it was read" in result["error"]
    prefs = (await _mcp(hass, TOOL_GET_ENERGY_PREFS))["prefs"]
    assert prefs["device_consumption"][0]["name"] == "Fridge"


async def test_what_home_assistant_would_refuse_is_refused(
    hass: HomeAssistant, energy: None
) -> None:
    first = await _mcp(hass, TOOL_GET_ENERGY_PREFS)

    result = await _mcp(
        hass,
        TOOL_SET_ENERGY_PREFS,
        config_hash=first["config_hash"],
        energy_sources=[{"type": "nuclear"}],
    )

    assert "would refuse" in result["error"]


async def test_a_change_needs_the_hash_and_something_to_change(
    hass: HomeAssistant, energy: None
) -> None:
    assert (
        "config_hash"
        in (await _mcp(hass, TOOL_SET_ENERGY_PREFS, device_consumption=[FRIDGE]))["error"]
    )
    first = await _mcp(hass, TOOL_GET_ENERGY_PREFS)
    assert (
        "Nothing to change"
        in (await _mcp(hass, TOOL_SET_ENERGY_PREFS, config_hash=first["config_hash"]))["error"]
    )


async def test_a_release_without_water_devices_still_works(
    hass: HomeAssistant, energy: None
) -> None:
    """2025.1, the oldest release supported, has no device_consumption_water."""
    from homeassistant.components.energy.data import EnergyManager
    from homeassistant.components.energy.validate import EnergyPreferencesValidation

    def _older_defaults() -> dict[str, list[Any]]:
        return {"energy_sources": [], "device_consumption": []}

    older_validation = EnergyPreferencesValidation()
    del older_validation.device_consumption_water
    with (
        patch.object(EnergyManager, "default_preferences", staticmethod(_older_defaults)),
        patch(
            "homeassistant.components.energy.validate.async_validate",
            return_value=older_validation,
        ),
    ):
        first = await _mcp(hass, TOOL_GET_ENERGY_PREFS)
        assert first["issues"] == []
        refused = await _mcp(
            hass,
            TOOL_SET_ENERGY_PREFS,
            config_hash=first["config_hash"],
            device_consumption_water=[FRIDGE],
        )
        saved = await _mcp(
            hass,
            TOOL_SET_ENERGY_PREFS,
            config_hash=first["config_hash"],
            device_consumption=[FRIDGE],
        )

    assert "has no device_consumption_water" in refused["error"]
    assert saved["status"] == "saved", saved


async def test_reading_is_open_and_changing_needs_admin() -> None:
    assert TOOL_GET_ENERGY_PREFS in mcp_access._READ_ONLY_TOOLS
    assert TOOL_SET_ENERGY_PREFS in mcp_access._ADMIN_TOOLS
