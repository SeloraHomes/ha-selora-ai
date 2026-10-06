"""System health and an integration's diagnostics, over MCP.

"Why is Zigbee flaky?" ends in the integration's diagnostics, and "what
version / install is this?" in system health — both one page in Settings,
neither readable by a tool. Home Assistant's own components gather them; only
the integration below is stubbed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_integration,
    mock_platform,
)

from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import (
    TOOL_GET_DIAGNOSTICS,
    TOOL_GET_SYSTEM_HEALTH,
)

DUMP = {
    "config": {"host": "192.168.1.5", "api_key": "abc123", "nested": {"token": "t0k"}},
    "radio": {"channel": 15, "lqi": [200, 180]},
}


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[tool](hass, arguments)


async def test_system_health_reports_each_integration(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "system_health", {})
    assert await async_setup_component(hass, "homeassistant", {})
    await hass.async_block_till_done()

    result = await _mcp(hass, TOOL_GET_SYSTEM_HEALTH)

    assert "version" in result["system_health"]["homeassistant"]


async def test_one_hung_check_does_not_cost_the_others(
    hass: HomeAssistant, monkeypatch: Any
) -> None:
    import asyncio

    from custom_components.selora_ai import diagnostics_reader

    monkeypatch.setattr(diagnostics_reader, "_HEALTH_TIMEOUT", 0.05)

    async def _never() -> str:
        await asyncio.Event().wait()
        return "never"

    async def _sections(_hass: HomeAssistant) -> list[tuple[str, dict[str, Any]]]:
        return [("good", {"info": {"version": "1.0"}}), ("stuck", {"info": {"reach": _never()}})]

    hass.config.components.add("system_health")
    monkeypatch.setattr(diagnostics_reader, "_registrations", _sections)

    result = await _mcp(hass, TOOL_GET_SYSTEM_HEALTH)

    assert result["system_health"] == {"good": {"version": "1.0"}, "stuck": {"reach": "Timed out"}}


@pytest.fixture
async def radio(hass: HomeAssistant) -> MockConfigEntry:
    platform = Mock(
        async_get_config_entry_diagnostics=AsyncMock(return_value=DUMP),
        async_get_device_diagnostics=AsyncMock(return_value={"node": 7}),
    )
    mock_integration(hass, MockModule("radio"))
    mock_platform(hass, "radio.diagnostics", platform)
    entry = MockConfigEntry(domain="radio")
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "diagnostics", {})
    # Loading the integration is what registers its diagnostics platform.
    assert await async_setup_component(hass, "radio", {})
    await hass.async_block_till_done()
    return entry


async def test_diagnostics_come_back_with_credentials_redacted(
    hass: HomeAssistant, radio: MockConfigEntry
) -> None:
    result = await _mcp(hass, TOOL_GET_DIAGNOSTICS, entry_id=radio.entry_id)

    assert result["domain"] == "radio"
    config = result["diagnostics"]["config"]
    assert config["host"] == "192.168.1.5"
    assert config["api_key"] == "**REDACTED**"
    assert config["nested"]["token"] == "**REDACTED**"
    assert "abc123" not in str(result) and "t0k" not in str(result)


async def test_a_device_has_its_own_dump(hass: HomeAssistant, radio: MockConfigEntry) -> None:
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=radio.entry_id, identifiers={("radio", "n7")}
    )
    other = MockConfigEntry(domain="other")
    other.add_to_hass(hass)
    foreign = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("other", "x")}
    )

    own = await _mcp(hass, TOOL_GET_DIAGNOSTICS, entry_id=radio.entry_id, device_id=device.id)
    refused = await _mcp(hass, TOOL_GET_DIAGNOSTICS, entry_id=radio.entry_id, device_id=foreign.id)

    assert own["diagnostics"] == {"node": 7}
    assert "does not belong" in refused["error"]


async def test_fields_narrow_a_dump(hass: HomeAssistant, radio: MockConfigEntry) -> None:
    narrowed = await _mcp(hass, TOOL_GET_DIAGNOSTICS, entry_id=radio.entry_id, fields=["radio"])
    unknown = await _mcp(hass, TOOL_GET_DIAGNOSTICS, entry_id=radio.entry_id, fields=["nope"])

    assert narrowed["diagnostics"] == {"radio": {"channel": 15, "lqi": [200, 180]}}
    assert unknown["fields"] == ["config", "radio"]


async def test_a_large_dump_lists_its_parts(
    hass: HomeAssistant, radio: MockConfigEntry, monkeypatch: Any
) -> None:
    from custom_components.selora_ai import diagnostics_reader

    monkeypatch.setattr(diagnostics_reader, "_MAX_CHARS", 50)

    result = await _mcp(hass, TOOL_GET_DIAGNOSTICS, entry_id=radio.entry_id)

    assert result["too_large"] is True
    assert set(result["fields"]) == {"config", "radio"}
    assert "diagnostics" not in result


async def test_a_mapping_that_is_not_a_dict_is_still_redacted(
    hass: HomeAssistant, radio: MockConfigEntry
) -> None:
    from types import MappingProxyType

    hass.data["diagnostics"].platforms["radio"].config_entry_diagnostics = AsyncMock(
        return_value=MappingProxyType({"config": MappingProxyType({"password": "pw1"})})
    )

    result = await _mcp(hass, TOOL_GET_DIAGNOSTICS, entry_id=radio.entry_id)

    assert result["diagnostics"] == {"config": {"password": "**REDACTED**"}}


async def test_the_size_summary_is_bounded(
    hass: HomeAssistant, radio: MockConfigEntry, monkeypatch: Any
) -> None:
    from custom_components.selora_ai import diagnostics_reader

    monkeypatch.setattr(diagnostics_reader, "_MAX_CHARS", 100)
    hass.data["diagnostics"].platforms["radio"].config_entry_diagnostics = AsyncMock(
        return_value={f"k{i}": i for i in range(500)}
    )

    result = await _mcp(hass, TOOL_GET_DIAGNOSTICS, entry_id=radio.entry_id)

    assert len(result["fields"]) == 50
    assert result["fields_omitted"] == 450


def test_both_need_admin() -> None:
    assert TOOL_GET_SYSTEM_HEALTH in mcp_access._ADMIN_TOOLS
    assert TOOL_GET_DIAGNOSTICS in mcp_access._ADMIN_TOOLS
