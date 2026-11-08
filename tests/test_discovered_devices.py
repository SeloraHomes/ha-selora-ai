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
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import SOURCE_IGNORE, ConfigFlow
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
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
from custom_components.selora_ai.integration_manager import (
    _found_better,
    async_list_discovered,
    async_setup_card,
)
from custom_components.selora_ai.llm_client.command_policy import (
    _pending_client_actions_from_log,
    _pending_hint_for,
)
from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import definitions as mcp_definitions
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


async def test_the_chat_card_shows_the_steps_and_asks_for_no_typing(
    hass: HomeAssistant,
) -> None:
    """The steps go on the card the chat shows, and the line above it says
    to do them on the device: nothing is typed, unlike a sign-in."""
    _register(hass, "lamps")
    with mock_config_flow("lamps", _PairingLampFlow), _texts():
        flow_id = await _discover(hass)
        chat = _chat(hass)
        await chat.execute("add_integration", {"domain": "lamps", "flow_id": flow_id})
        await chat.execute("add_integration", {"domain": "lamps", "flow_id": flow_id, "fields": {}})
        handed = await chat.execute(
            "add_integration",
            {"domain": "lamps", "flow_id": flow_id, "steps": ["Open the Home app"]},
        )

    (action,) = _pending_client_actions_from_log([{"tool": "add_integration", "result": handed}])
    assert action["steps"] == ["Open the Home app"]
    assert "on the device" in _pending_hint_for([action], None)
    assert "type" not in _pending_hint_for([action], None)
    assert "type below" in _pending_hint_for([{**action, "steps": []}], None)


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
        added = await _mcp(
            hass, TOOL_ADD_INTEGRATION, domain="lamps", flow_id=discovered, fields={}
        )

    # The first answer already says which devices Home Assistant found.
    assert [d["flow_id"] for d in first["discovered"]] == [discovered]
    assert step["flow_id"] == discovered, step
    assert step["status"] == "needs_confirmation"
    assert "already setting this device up" in step["note"]
    assert added["status"] == "added", added
    # What the discovery found is kept.
    (entry,) = hass.config_entries.async_entries("lamps")
    assert entry.data == {"host": "10.0.0.5"}


async def test_an_added_device_names_the_entities_to_show(hass: HomeAssistant, lamps: None) -> None:
    """The added card shows the device's entities as tiles; a diagnostic or
    hidden one is not what the user set it up for."""
    flow_id = await _discover(hass)
    await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps", flow_id=flow_id)
    registry = er.async_get(hass)
    with patch(
        "custom_components.selora_ai.integration_manager.er.async_entries_for_config_entry",
        lambda _reg, _entry_id: [
            registry.async_get_or_create("light", "lamps", "lamp-1", suggested_object_id="desk"),
            registry.async_get_or_create(
                "sensor",
                "lamps",
                "rssi",
                suggested_object_id="desk_rssi",
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
        ],
    ):
        added = await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps", flow_id=flow_id, fields={})

    assert added["status"] == "added", added
    assert added["entity_ids"] == ["light.desk"]


@pytest.mark.parametrize("devices", [0, 1, 2])
async def test_an_added_device_can_be_opened(
    hass: HomeAssistant, lamps: None, devices: int
) -> None:
    """The card opens the one device set up; with none (a media server) or
    several (a bridge), the entry's own page, which lists them."""
    flow_id = await _discover(hass)
    await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps", flow_id=flow_id)
    found = [MagicMock(id=f"dev-{i}") for i in range(devices)]
    with patch(
        "custom_components.selora_ai.integration_manager.dr.async_entries_for_config_entry",
        lambda _reg, _entry_id: found,
    ):
        added = await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps", flow_id=flow_id, fields={})

    assert added["status"] == "added", added
    assert added.get("device_id") == ("dev-0" if devices == 1 else None)
    assert added["entry_id"]


async def test_a_setup_says_what_is_already_set_up(hass: HomeAssistant, lamps: None) -> None:
    """A device already set up is no longer among a step's choices: the model
    told so does not pick another device in its place."""
    MockConfigEntry(domain="lamps", title="Desk Lamp").add_to_hass(hass)
    MockConfigEntry(domain="lamps", title="Old Lamp", source="ignore").add_to_hass(hass)

    first = await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps")

    assert [c["title"] for c in first["configured"]] == ["Desk Lamp"]
    assert "add nothing" in first["configured_hint"]


class _PickLampFlow(_LampFlow):
    """Set up by hand, the user picks one of the lamps Home Assistant found —
    DLNA's list of renderers."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({vol.Optional("host"): vol.In(["Hall Lamp", "Porch Lamp"])}),
            )
        return self.async_create_entry(title=user_input["host"], data=user_input)


async def test_which_found_device_to_set_up_is_the_user_s_choice(hass: HomeAssistant) -> None:
    _register(hass, "lamps")
    with mock_config_flow("lamps", _PickLampFlow):
        step = await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps")

    assert step["status"] == "needs_user", step
    assert "setup_url" in step
    assert not hass.config_entries.async_entries("lamps")


async def test_a_found_device_is_never_picked_for_the_user(hass: HomeAssistant) -> None:
    """With a discovery waiting, the list goes to the model so it can continue
    that setup; an answer picking from the list is not sent."""
    _register(hass, "lamps")
    with mock_config_flow("lamps", _PickLampFlow):
        await _discover(hass)
        first = await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps")
        picked = await _mcp(
            hass,
            TOOL_ADD_INTEGRATION,
            domain="lamps",
            flow_id=first["flow_id"],
            fields={"host": "Porch Lamp"},
        )

    assert first["status"] == "needs_options"
    assert first["discovered"]
    assert picked["status"] == "needs_user", picked
    assert not hass.config_entries.async_entries("lamps")


class _TvFlow(_LampFlow):
    """A TV's own integration, which found it too."""


async def test_a_tv_is_set_up_with_its_own_integration_not_dlna(hass: HomeAssistant) -> None:
    """Found by DLNA and by its maker's integration, a TV is offered once:
    with the maker's integration, as Home Assistant's DLNA flow itself holds
    back the TVs it knows a better integration for."""
    _register(hass, "dlna_dmr")
    _register(hass, "samsungtv")
    with mock_config_flow("dlna_dmr", _LampFlow), mock_config_flow("samsungtv", _TvFlow):
        await hass.config_entries.flow.async_init(
            "dlna_dmr",
            context={"source": "zeroconf"},
            data={"host": "10.0.0.9", "serial": "uuid:tv-1", "name": "[TV] Samsung Q6 Series (82)"},
        )
        own = await hass.config_entries.flow.async_init(
            "samsungtv",
            context={"source": "zeroconf"},
            data={
                "host": "10.0.0.9",
                "serial": "tv-1",
                "name": "Samsung Q6 Series (82) (QN82Q6FNA)",
            },
        )
        speaker = await hass.config_entries.flow.async_init(
            "dlna_dmr",
            context={"source": "zeroconf"},
            data={"host": "10.0.0.7", "serial": "uuid:sonos", "name": "Sonos Era 100"},
        )
        with patch(
            "custom_components.selora_ai.integration_manager.async_get_translations",
            AsyncMock(
                return_value={
                    "component.dlna_dmr.config.flow_title": "{name}",
                    "component.samsungtv.config.flow_title": "{name}",
                }
            ),
        ):
            rows = await async_list_discovered(hass)
            # Matched by name too: Samsung's integration may key the TV by
            # another UDN than DLNA's.
            hass.config_entries.flow.async_get(own["flow_id"])["context"]["unique_id"] = "other"
            assert (
                hass.config_entries.flow.async_get(own["flow_id"])["context"]["unique_id"]
                == "other"
            )
            by_name = await async_list_discovered(hass)

    assert {r["flow_id"] for r in rows} == {own["flow_id"], speaker["flow_id"]}
    assert {r["flow_id"] for r in by_name} == {own["flow_id"], speaker["flow_id"]}


async def test_a_host_asked_for_comes_with_the_addresses_already_known(
    hass: HomeAssistant, lamps: None
) -> None:
    """A TV set up with DLNA is at the same address its maker's integration
    asks for: the model is given it rather than asking the user for an IP."""
    MockConfigEntry(
        domain="dlna_dmr",
        title="[TV] Samsung Q6 Series (82)",
        data={"url": "http://10.0.0.9:9197/dmr", "mac": "aa:bb:cc:dd:ee:ff"},
    ).add_to_hass(hass)
    MockConfigEntry(
        domain="hue", title="Bridge", data={"host": "10.0.0.2", "api_key": "s3cret"}
    ).add_to_hass(hass)

    step = await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps")

    assert step["known_hosts"] == [
        {"title": "[TV] Samsung Q6 Series (82)", "host": "10.0.0.9"},
        {"title": "Bridge", "host": "10.0.0.2"},
    ]
    # Nothing else an entry stores.
    assert "s3cret" not in str(step)
    assert "aa:bb" not in str(step)


async def test_an_unknown_flow_id_is_answered_with_what_is_found(
    hass: HomeAssistant, lamps: None
) -> None:
    """A suggestion's bubble carries no ids: a model that made one up, or
    guessed the integration, is shown each found device's own domain and
    flow_id to call again with, rather than told to start over."""
    flow_id = await _discover(hass)

    made_up = await _chat(hass).execute(
        "add_integration", {"domain": "lamps", "flow_id": "discovered flow id unavailable"}
    )

    assert "call again" in made_up["error"]
    assert [(r["domain"], r["flow_id"]) for r in made_up["discovered"]] == [("lamps", flow_id)]
    # Nothing was started or answered.
    (flow,) = hass.config_entries.flow.async_progress()
    assert flow["flow_id"] == flow_id
    assert flow["step_id"] == "confirm"


def test_a_device_named_inside_another_s_name_is_another_device() -> None:
    """``Living Room`` found by DLNA is not the ``Living Room Speaker`` another
    integration found; ``[TV] Samsung Q6`` is the ``Samsung Q6 (QN82)``."""

    def row(domain: str, title: str, unique_id: str) -> dict[str, Any]:
        return {"domain": domain, "title": title, "unique_id": unique_id}

    living = row("dlna_dmr", "Living Room", "uuid:a")
    tv = row("dlna_dmr", "[TV] Samsung Q6", "uuid:b")
    rows = [
        living,
        tv,
        row("cast", "Living Room Speaker", "c"),
        row("samsungtv", "Samsung Q6 (QN82)", "d"),
    ]

    assert not _found_better(living, rows)
    assert _found_better(tv, rows)


class _SelectorPickLampFlow(_LampFlow):
    """By hand, the found lamps offered with Home Assistant's select selector,
    each by its id and labelled with its name."""

    translation_key: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            config = SelectSelectorConfig(
                options=[
                    SelectOptionDict(value="lamp-2", label="Hall Lamp"),
                    SelectOptionDict(value="lamp-3", label="Porch Lamp"),
                ],
                **({"translation_key": self.translation_key} if self.translation_key else {}),
            )
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({vol.Required("host"): SelectSelector(config)}),
            )
        return self.async_create_entry(title=user_input["host"], data=user_input)


class _SelectorSettingLampFlow(_SelectorPickLampFlow):
    translation_key = "host"


@pytest.mark.parametrize(
    ("flow", "handed"),
    [(_SelectorPickLampFlow, True), (_SelectorSettingLampFlow, False)],
    ids=["found-devices", "translated-setting"],
)
async def test_a_selector_choice_among_found_devices_is_the_user_s(
    hass: HomeAssistant, flow: type[ConfigFlow], handed: bool
) -> None:
    """Found devices offered with a select selector go to the user like a
    ``vol.In`` list; a setting translated by its key stays the model's."""
    _register(hass, "lamps")
    with mock_config_flow("lamps", flow):
        step = await _mcp(hass, TOOL_ADD_INTEGRATION, domain="lamps")

    assert (step["status"] == "needs_user") is handed, step
    assert not hass.config_entries.async_entries("lamps")
