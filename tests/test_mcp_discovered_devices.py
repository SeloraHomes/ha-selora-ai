"""Tests for setting up a device Home Assistant discovered, over MCP.

An agent could add an integration from scratch but not see what Home Assistant
had found on the network, nor continue the flow it had started for it. The
listing now carries the Discovered section, and ``selora_add_integration``
takes over a discovery's own flow — which is never aborted, so a setup refused
or left alone stays under Discovered for the user.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigFlow
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockModule,
    async_fire_time_changed,
    mock_config_flow,
    mock_integration,
    mock_platform,
)
import voluptuous as vol

from custom_components.selora_ai.flow_sessions import FLOW_TTL, owner_of
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import (
    TOOL_ADD_INTEGRATION,
    TOOL_LIST_INTEGRATIONS,
)


class _LampFlow(ConfigFlow):
    """A lamp found over zeroconf, confirmed — or asked for its key — by the user."""

    VERSION = 1
    needs_key = False

    async def async_step_zeroconf(self, discovery_info: dict[str, Any]) -> Any:
        self._host = discovery_info["host"]
        await self.async_set_unique_id(discovery_info["serial"])
        self._abort_if_unique_id_configured()
        self.context["title_placeholders"] = {"name": discovery_info["name"]}
        return await self.async_step_confirm()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=vol.Schema({vol.Required("host"): str})
            )
        self._host = user_input["host"]
        return self.async_create_entry(title="Lamp", data={"host": self._host})

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            schema = vol.Schema({vol.Required("api_key"): str}) if self.needs_key else None
            return self.async_show_form(step_id="confirm", data_schema=schema)
        return self.async_create_entry(title="Desk Lamp", data={"host": self._host})


class _KeyedLampFlow(_LampFlow):
    needs_key = True


def _register(hass: HomeAssistant, domain: str) -> None:
    mock_integration(
        hass,
        MockModule(
            domain,
            async_setup_entry=AsyncMock(return_value=True),
            partial_manifest={"config_flow": True},
        ),
    )
    mock_platform(hass, f"{domain}.config_flow", None)


@pytest.fixture
def lamps(hass: HomeAssistant) -> Iterator[None]:
    _register(hass, "lamps")
    with mock_config_flow("lamps", _LampFlow):
        yield


async def _discover(hass: HomeAssistant, domain: str = "lamps") -> str:
    result = await hass.config_entries.flow.async_init(
        domain,
        context={"source": "zeroconf"},
        data={"host": "10.0.0.5", "serial": "lamp-1", "name": "Desk Lamp"},
    )
    return str(result["flow_id"])


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[tool](hass, arguments)


async def test_discovered_devices_are_listed_as_the_discovered_cards_name_them(
    hass: HomeAssistant, lamps: None
) -> None:
    flow_id = await _discover(hass)
    # A flow someone started by hand is not a discovery.
    await hass.config_entries.flow.async_init("lamps", context={"source": "user"})

    with patch(
        "custom_components.selora_ai.integration_manager.async_get_translations",
        AsyncMock(return_value={"component.lamps.config.flow_title": "{name} lamp"}),
    ):
        listed = await _mcp(hass, TOOL_LIST_INTEGRATIONS)

    assert listed["discovered"] == [
        {
            "flow_id": flow_id,
            "domain": "lamps",
            "integration": "lamps",
            "title": "Desk Lamp lamp",
            "source": "zeroconf",
        }
    ]


async def test_without_a_flow_title_the_integration_names_it(
    hass: HomeAssistant, lamps: None
) -> None:
    await _discover(hass)

    listed = await _mcp(hass, TOOL_LIST_INTEGRATIONS, domain="lamps")
    problems = await _mcp(hass, TOOL_LIST_INTEGRATIONS, problems_only=True)

    assert [d["title"] for d in listed["discovered"]] == ["lamps"]
    assert "discovered" not in problems
    assert (await _mcp(hass, TOOL_LIST_INTEGRATIONS, domain="other"))["discovered"] == []


async def test_a_discovered_device_is_set_up_through_its_own_flow(
    hass: HomeAssistant, lamps: None
) -> None:
    flow_id = await _discover(hass)

    first = await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps", flow_id=flow_id)
    done = await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps", flow_id=flow_id, fields={})

    assert first["status"] == "needs_confirmation", first
    assert first["flow_id"] == flow_id
    assert first["step"] == "confirm"
    assert done["status"] == "added", done
    (entry,) = hass.config_entries.async_entries("lamps")
    # What the discovery found is kept: nobody typed the address.
    assert entry.data == {"host": "10.0.0.5"}
    assert entry.unique_id == "lamp-1"
    assert not hass.config_entries.flow.async_progress()


async def test_the_first_call_on_a_discovery_only_describes(
    hass: HomeAssistant, lamps: None
) -> None:
    flow_id = await _discover(hass)

    first = await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps", flow_id=flow_id, fields={})

    assert first["status"] == "needs_confirmation", first
    assert not hass.config_entries.async_entries("lamps")


async def test_a_discovery_asking_for_a_login_is_refused_but_kept(hass: HomeAssistant) -> None:
    _register(hass, "lamps")
    with mock_config_flow("lamps", _KeyedLampFlow):
        flow_id = await _discover(hass)
        refused = await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps", flow_id=flow_id)

    assert "discovered" in refused["error"]
    assert "flow_id" not in refused
    assert [f["flow_id"] for f in hass.config_entries.flow.async_progress()] == [flow_id]
    assert owner_of(hass, flow_id) is None


async def test_a_discovery_left_alone_is_let_go_not_aborted(
    hass: HomeAssistant, lamps: None
) -> None:
    flow_id = await _discover(hass)
    await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps", flow_id=flow_id)

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=FLOW_TTL + 1))
    await hass.async_block_till_done()

    assert owner_of(hass, flow_id) is None
    assert [f["flow_id"] for f in hass.config_entries.flow.async_progress()] == [flow_id]


async def test_a_discovery_for_another_integration_is_not_taken_over(
    hass: HomeAssistant, lamps: None
) -> None:
    _register(hass, "teams")
    flow_id = await _discover(hass)

    result = await _mcp(hass, TOOL_ADD_INTEGRATION, domain="teams", flow_id=flow_id)

    assert "error" in result
    assert [f["flow_id"] for f in hass.config_entries.flow.async_progress()] == [flow_id]
