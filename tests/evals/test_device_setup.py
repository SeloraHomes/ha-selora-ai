"""Does a real model set up the device the user asked for, the way they meant?

Each case is a setup that went wrong on a hub: a Samsung TV added with DLNA
instead of Samsung's own integration, the model asking for the IP of a TV
Home Assistant already had an address for, and DLNA's list of renderers
answered with a Sonos and then a receiver nobody asked for. Graded on what
the turn did to Home Assistant — which entries exist after it — and which
tools it called, never on the wording.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigFlow
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_config_flow,
    mock_integration,
    mock_platform,
)
import voluptuous as vol

from ..chat_harness import ChatHarness, ChatTurn

pytestmark = pytest.mark.eval

TV_HOST = "192.168.2.50"
TV_NAME = "Samsung Q6 Series (82) (QN82Q6FNA)"
DLNA_TV = "[TV] Samsung Q6 Series (82)"
RENDERERS = ("Sonos Era 100", "VSX-44")


class _DeviceFlow(ConfigFlow):
    """Found over zeroconf and confirmed, or set up by hand from its address."""

    VERSION = 1

    async def async_step_zeroconf(self, discovery_info: dict[str, Any]) -> Any:
        self._host = discovery_info["host"]
        self._name = discovery_info["name"]
        await self.async_set_unique_id(discovery_info["serial"])
        self._abort_if_unique_id_configured()
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            return self.async_show_form(step_id="confirm")
        return self.async_create_entry(title=self._name, data={"host": self._host})

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=vol.Schema({vol.Required("host"): str})
            )
        return self.async_create_entry(title=TV_NAME, data=user_input)


class _DlnaFlow(_DeviceFlow):
    """By hand, DLNA offers the renderers it found, by name."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({vol.Optional("host"): vol.In(list(RENDERERS))}),
            )
        return self.async_create_entry(title=user_input["host"], data=user_input)


def _register(hass: HomeAssistant, domain: str, name: str) -> None:
    mock_integration(
        hass,
        MockModule(
            domain,
            async_setup_entry=AsyncMock(return_value=True),
            partial_manifest={"config_flow": True, "name": name},
        ),
    )
    mock_platform(hass, f"{domain}.config_flow", None)


@pytest.fixture
def tv(hass: HomeAssistant) -> Iterator[None]:
    """Samsung Smart TV and DLNA, as a home with that TV has them."""
    _register(hass, "samsungtv", "Samsung Smart TV")
    _register(hass, "dlna_dmr", "DLNA Digital Media Renderer")
    _register(hass, "dlna_dms", "DLNA Digital Media Server")
    titles = {
        "component.samsungtv.config.flow_title": "{name}",
        "component.dlna_dmr.config.flow_title": "{name}",
        "component.dlna_dms.config.flow_title": "{name}",
    }
    with ExitStack() as stack:
        stack.enter_context(mock_config_flow("samsungtv", _DeviceFlow))
        stack.enter_context(mock_config_flow("dlna_dmr", _DlnaFlow))
        stack.enter_context(mock_config_flow("dlna_dms", _DeviceFlow))
        stack.enter_context(
            patch(
                "custom_components.selora_ai.integration_manager.async_get_translations",
                AsyncMock(return_value=titles),
            )
        )
        yield


async def _found(hass: HomeAssistant, domain: str, serial: str, name: str, host: str) -> None:
    await hass.config_entries.flow.async_init(
        domain,
        context={"source": "zeroconf"},
        data={"host": host, "serial": serial, "name": name},
    )


async def _tv_found_by_both(hass: HomeAssistant) -> None:
    await _found(hass, "dlna_dmr", "uuid:tv-1", DLNA_TV, TV_HOST)
    await _found(hass, "samsungtv", "tv-1", TV_NAME, TV_HOST)
    await _found(hass, "dlna_dmr", "uuid:sonos", RENDERERS[0], "192.168.2.60")


async def _server_found(hass: HomeAssistant) -> None:
    await _found(hass, "dlna_dms", "uuid:giga", "Giga Hub", "192.168.2.70")
    await _tv_found_by_both(hass)


async def _tv_set_up_with_dlna(hass: HomeAssistant) -> None:
    MockConfigEntry(
        domain="dlna_dmr",
        title=DLNA_TV,
        unique_id="uuid:tv-1",
        data={"url": f"http://{TV_HOST}:9197/dmr", "mac": "aa:bb:cc:dd:ee:ff"},
    ).add_to_hass(hass)


@dataclass(frozen=True)
class Case:
    id: str
    message: str
    # Each "domain=count": entries of that domain after the turn, at most.
    at_most: tuple[str, ...] = ()
    # Each "domain=count": entries of that domain after the turn, at least.
    at_least: tuple[str, ...] = ()
    # The reply asks for none of these (casefolded).
    never_asks: tuple[str, ...] = ()
    # add_integration is never called for this domain.
    never_domain: str = ""


CASES = (
    # The TV is found by DLNA and by Samsung's integration: Samsung's is the
    # one with power, remote and apps.
    Case(
        "tv-own-integration",
        "Set up my Samsung TV",
        at_most=("dlna_dmr=0",),
        never_domain="dlna_dmr",
    ),
    # Nothing found since a restart, but the TV's DLNA entry has its address.
    Case(
        "tv-known-host",
        "Set up my Samsung TV with the Samsung Smart TV integration",
        at_least=("samsungtv=1",),
        never_asks=("ip address", "what is the ip", "what's the ip", "address of"),
    ),
    # Asked again, DLNA lists only renderers the user did not mean.
    Case(
        "no-wrong-renderer",
        "Set up my TV with DLNA",
        at_most=("dlna_dmr=1",),
    ),
    # The home suggestion's own prompt: the integration's name, no ids. The
    # model guessed the renderer's domain and made up a flow_id.
    Case(
        "suggested-server",
        "Set up Giga Hub\n\n[[details:Set up Giga Hub, which Home Assistant "
        "discovered through DLNA Digital Media Server.]]",
        at_least=("dlna_dms=1",),
        at_most=("dlna_dmr=0", "samsungtv=0"),
    ),
)

_HOMES = {
    "tv-own-integration": _tv_found_by_both,
    "suggested-server": _server_found,
}


def _counts(spec: tuple[str, ...]) -> list[tuple[str, int]]:
    return [(d, int(n)) for d, n in (s.split("=", 1) for s in spec)]


def _grade(hass: HomeAssistant, case: Case, turn: ChatTurn) -> list[str]:
    response = str(turn.done.get("response") or "").casefold()
    failures: list[str] = []
    for domain, most in _counts(case.at_most):
        titles = [e.title for e in hass.config_entries.async_entries(domain)]
        if len(titles) > most:
            failures.append(f"{domain} has {titles}")
    for domain, least in _counts(case.at_least):
        if len(hass.config_entries.async_entries(domain)) < least:
            failures.append(f"no {domain} entry")
    failures += [f"asked {a!r}" for a in case.never_asks if a in response]
    if case.never_domain:
        failures += [
            f"add_integration {c['arguments']}"
            for c in turn.tool_calls
            if c["tool"] == "add_integration"
            and (c.get("arguments") or {}).get("domain") == case.never_domain
        ]
    return failures


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_the_device_meant_is_set_up_its_own_way(
    hass: HomeAssistant, home: ChatHarness, tv: None, case: Case, run: int
) -> None:
    await _HOMES.get(case.id, _tv_set_up_with_dlna)(hass)
    await hass.async_block_till_done()

    turn = await home.live(case.message)

    assert not turn.errors, turn.errors
    assert not [e for e in turn.events if e.get("type") == "error"], turn.events[-1]
    failures = _grade(hass, case, turn)
    tools = [(c["tool"], c["arguments"]) for c in turn.tool_calls]
    assert not failures, f"{failures}\ntools: {tools}\nreply: {turn.done.get('response')}"
