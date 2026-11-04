"""Tests for the devices Home Assistant discovered: listing, setting up and
ignoring them over MCP and in chat, and the chat home's suggestion to set them up.

An agent could add an integration from scratch but not see what Home Assistant
had found on the network, nor continue the flow it had started for it. The
listing now carries the Discovered section, and ``add_integration`` takes over
a discovery's own flow — which is never aborted, so a setup refused or left
alone stays under Discovered for the user. Chat confirmed a discovery by
submitting an empty form, which only finished one-step setups.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_IGNORE, ConfigFlow
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

from custom_components.selora_ai.device_manager import DeviceManager
from custom_components.selora_ai.flow_sessions import FLOW_TTL, owner_of
from custom_components.selora_ai.home_suggestions import (
    async_home_suggestions,
    get_home_suggestion_store,
)
from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import definitions as mcp_definitions
from custom_components.selora_ai.integration_manager import async_setup_card
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import (
    TOOL_ADD_INTEGRATION,
    TOOL_IGNORE_DISCOVERED,
    TOOL_LIST_INTEGRATIONS,
)
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.types import HomeSuggestion


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


class _PairingLampFlow(_LampFlow):
    """A lamp that cannot pair a protocol once confirmed: its step only
    explains, and goes on without it when confirmed — Apple TV's
    ``protocol_disabled``, which fails with nothing paired."""

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            return self.async_show_form(step_id="confirm")
        return await self.async_step_protocol_disabled()

    async def async_step_protocol_disabled(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            return self.async_show_form(
                step_id="protocol_disabled", description_placeholders={"protocol": "AirPlay"}
            )
        return self.async_abort(reason="setup_failed")


class _RefusedLampFlow(_LampFlow):
    """A lamp whose setup fails once confirmed."""

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            return self.async_show_form(step_id="confirm")
        return self.async_abort(reason="setup_failed")


_LAMP_TEXTS = {
    "component.lamps.config.step.protocol_disabled.title": "Pairing not possible",
    "component.lamps.config.step.protocol_disabled.description": (
        "Pairing is required for `{protocol}` but it is disabled on the device.\n\n"
        "You may continue without pairing this protocol."
    ),
    "component.lamps.config.abort.setup_failed": "Failed to set up device.",
}


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


async def _discover(
    hass: HomeAssistant, domain: str = "lamps", serial: str = "lamp-1", name: str = "Desk Lamp"
) -> str:
    result = await hass.config_entries.flow.async_init(
        domain,
        context={"source": "zeroconf"},
        data={"host": "10.0.0.5", "serial": serial, "name": name},
    )
    return str(result["flow_id"])


def _chat(hass: HomeAssistant) -> ToolExecutor:
    return ToolExecutor(hass, DeviceManager(hass), is_admin=True)


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


def _texts() -> Any:
    return patch(
        "homeassistant.helpers.translation.async_get_translations",
        AsyncMock(return_value=_LAMP_TEXTS),
    )


async def test_a_step_for_the_user_to_do_goes_to_them_in_its_words(
    hass: HomeAssistant,
) -> None:
    """A step with nothing to fill in, reached by answering one, asks the user
    to act on the device or to go on without it: confirming it for them
    would skip a pairing they could still allow."""
    _register(hass, "lamps")
    with mock_config_flow("lamps", _PairingLampFlow), _texts():
        flow_id = await _discover(hass)
        await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps", flow_id=flow_id)
        step = await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps", flow_id=flow_id, fields={})
        handed = await _mcp(
            hass,
            TOOL_ADD_INTEGRATION,
            domain="lamps",
            flow_id=flow_id,
            steps=["Open the Home app", "", "Allow access: everyone"],
        )

    # Home Assistant says what to allow, not where: the steps are asked for.
    assert step["status"] == "needs_steps", step
    assert step["title"] == "Pairing not possible"
    assert step["description"] == (
        "Pairing is required for `AirPlay` but it is disabled on the device. "
        "You may continue without pairing this protocol."
    )
    assert handed["status"] == "needs_user", handed
    assert handed["setup_url"].endswith(f"?setup_flow={flow_id}")
    # Left for the user to continue, not answered or aborted.
    (flow,) = hass.config_entries.flow.async_progress()
    assert flow["step_id"] == "protocol_disabled"
    # The card a link opens shows the steps.
    card = await async_setup_card(hass, flow_id)
    assert card["steps"] == ["Open the Home app", "Allow access: everyone"]


async def test_a_setup_that_fails_says_so_in_its_words(hass: HomeAssistant) -> None:
    _register(hass, "lamps")
    with mock_config_flow("lamps", _RefusedLampFlow), _texts():
        flow_id = await _discover(hass)
        await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps", flow_id=flow_id)
        ended = await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps", flow_id=flow_id, fields={})

    assert ended["error"] == (
        "It was not completed (setup_failed). Home Assistant says: Failed to set up device."
    )


async def test_the_first_call_on_a_discovery_only_describes(
    hass: HomeAssistant, lamps: None
) -> None:
    flow_id = await _discover(hass)

    first = await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps", flow_id=flow_id, fields={})

    assert first["status"] == "needs_confirmation", first
    assert not hass.config_entries.async_entries("lamps")


async def test_a_discovery_asking_for_a_login_is_handed_to_the_user(
    hass: HomeAssistant,
) -> None:
    _register(hass, "lamps")
    with mock_config_flow("lamps", _KeyedLampFlow):
        flow_id = await _discover(hass)
        over_mcp = await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps", flow_id=flow_id)
        in_chat = await _chat(hass).execute(
            "add_integration", {"domain": "lamps", "flow_id": flow_id}
        )

    assert over_mcp["setup_url"].endswith(f"?setup_flow={flow_id}")
    # In chat, a card the user fills in: the same discovery, never aborted.
    assert in_chat["requires_approval"] is True
    assert in_chat["client_action"] == {
        "kind": "integration_setup",
        "flow_id": flow_id,
        "domain": "lamps",
        "label": "lamps",
    }
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


async def test_a_discovered_device_is_ignored_as_home_assistant_does(
    hass: HomeAssistant, lamps: None
) -> None:
    flow_id = await _discover(hass)

    result = await _mcp(hass, TOOL_IGNORE_DISCOVERED, flow_id=flow_id)

    assert result["status"] == "ignored", result
    assert result["title"] == "lamps"
    (entry,) = hass.config_entries.async_entries("lamps")
    assert entry.source == SOURCE_IGNORE
    assert entry.unique_id == "lamp-1"
    assert not hass.config_entries.flow.async_progress()
    # Found again, it is not offered again.
    await _discover(hass)
    assert (await _mcp(hass, TOOL_LIST_INTEGRATIONS))["discovered"] == []


async def test_only_a_discovery_is_ignored(hass: HomeAssistant, lamps: None) -> None:
    by_hand = await hass.config_entries.flow.async_init("lamps", context={"source": "user"})

    result = await _mcp(hass, TOOL_IGNORE_DISCOVERED, flow_id=by_hand["flow_id"])

    assert "error" in result
    assert not hass.config_entries.async_entries("lamps")


def test_the_mcp_tools_are_the_chat_tools() -> None:
    derived = mcp_definitions._DERIVED_MCP_TOOLS
    assert derived[TOOL_ADD_INTEGRATION] == "add_integration"
    assert derived[TOOL_IGNORE_DISCOVERED] == "ignore_discovered_device"
    assert {TOOL_ADD_INTEGRATION, TOOL_IGNORE_DISCOVERED} <= mcp_access._ADMIN_TOOLS


async def test_chat_lists_discoveries_named_and_nothing_else(
    hass: HomeAssistant, lamps: None
) -> None:
    flow_id = await _discover(hass)
    await hass.config_entries.flow.async_init("lamps", context={"source": "user"})

    result = await _chat(hass).execute("discover_network_devices", {})

    assert result["discovered"] == [
        {
            "flow_id": flow_id,
            "domain": "lamps",
            "integration": "lamps",
            "title": "lamps",
            "source": "zeroconf",
        }
    ]
    assert result["summary"]["discovered_count"] == 1


async def test_chat_sets_up_a_discovered_device_step_by_step(
    hass: HomeAssistant, lamps: None
) -> None:
    flow_id = await _discover(hass)
    chat = _chat(hass)

    first = await chat.execute("add_integration", {"domain": "lamps", "flow_id": flow_id})
    done = await chat.execute(
        "add_integration", {"domain": "lamps", "flow_id": flow_id, "fields": {}}
    )

    assert first["status"] == "needs_confirmation", first
    assert done["status"] == "added", done
    assert hass.config_entries.async_entries("lamps")[0].data == {"host": "10.0.0.5"}


async def test_chat_ignores_a_discovered_device(hass: HomeAssistant, lamps: None) -> None:
    flow_id = await _discover(hass)

    result = await _chat(hass).execute("ignore_discovered_device", {"flow_id": flow_id})

    assert result["status"] == "ignored", result


async def test_the_chat_home_offers_one_discovered_device_at_a_time(
    hass: HomeAssistant, lamps: None
) -> None:
    await _discover(hass)
    await _discover(hass, serial="lamp-2", name="Hall Lamp")

    async def offered() -> list[HomeSuggestion]:
        return [s for s in await async_home_suggestions(hass) if s["type"] == "set_up_discovered"]

    # A chat asked to set up a dozen devices runs out of steps; one device
    # per chat, and the row says how many are waiting after it.
    assert await offered() == [
        {
            # Named by the device, not its flow, which a restart replaces.
            "suggestion_id": "discovered:lamps:lamp-1",
            "type": "set_up_discovered",
            "entities": [{"entity_id": "", "name": "lamps"}],
            "collection": {"source": "discovery", "name": "lamps", "ref": ""},
            "waiting": 1,
        }
    ]

    await get_home_suggestion_store(hass).add("dismissed", ["discovered:lamps:lamp-1"])

    [after] = await offered()
    assert after["suggestion_id"] == "discovered:lamps:lamp-2"
    assert after["waiting"] == 0


async def test_nothing_discovered_suggests_nothing(hass: HomeAssistant, lamps: None) -> None:
    assert not [s for s in await async_home_suggestions(hass) if s["type"] == "set_up_discovered"]


class _SameLampByHandFlow(_LampFlow):
    """Set up by hand, the lamp is the discovered one: Home Assistant refuses a
    second setup of it while the discovery's is open."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=vol.Schema({vol.Required("host"): str})
            )
        await self.async_set_unique_id("lamp-1")
        return self.async_create_entry(title="Lamp", data=user_input)


async def test_a_setup_already_in_progress_continues_the_discovered_one(
    hass: HomeAssistant,
) -> None:
    """Asked to set up a discovered device without its flow_id, the setup
    Home Assistant refuses as already in progress is the discovery's own."""
    _register(hass, "lamps")
    with mock_config_flow("lamps", _SameLampByHandFlow):
        discovered = await _discover(hass)
        first = await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps")
        step = await _mcp(
            hass,
            TOOL_ADD_INTEGRATION,
            domain="lamps",
            flow_id=first["flow_id"],
            fields={"host": "10.0.0.5"},
        )
        added = await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps", flow_id=discovered, fields={})

    # The first answer already says which devices Home Assistant found.
    assert [d["flow_id"] for d in first["discovered"]] == [discovered]
    assert step["flow_id"] == discovered, step
    assert step["status"] == "needs_confirmation"
    assert "already setting this device up" in step["note"]
    assert added["status"] == "added", added
    # What the discovery found is kept.
    (entry,) = hass.config_entries.async_entries("lamps")
    assert entry.data == {"host": "10.0.0.5"}
