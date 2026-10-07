"""Helper setups and options flows that run more than one form.

Each tool call answered one form and aborted the flow at the next, reporting
"the flow wants more": a helper whose setup asks two questions, or an
integration whose options start with a menu and go on over two pages, could
not be finished. A flow is now held open between calls and continued by its
``flow_id``, one step per call — and only flows started here, only for what
they were started for, and not forever.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import HomeAssistant, callback
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

from custom_components.selora_ai.flow_sessions import FLOW_TTL, async_close_all
from custom_components.selora_ai.helper_flow import async_create_flow_helper
from custom_components.selora_ai.integration_manager import async_integration_options

_LIMITS = vol.Schema({vol.Required("maximum"): int})


class _CounterFlow(ConfigFlow):
    """A helper whose setup asks two questions."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=vol.Schema({vol.Required("name"): str})
            )
        self._name = user_input["name"]
        return await self.async_step_limits()

    async def async_step_limits(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None or user_input["maximum"] < 1:
            return self.async_show_form(
                step_id="limits",
                data_schema=_LIMITS,
                errors={"maximum": "too_small"} if user_input else None,
            )
        return self.async_create_entry(title=self._name, data={}, options=user_input)

    @staticmethod
    @callback
    def async_get_options_flow(_entry: ConfigEntry) -> OptionsFlow:
        return _CounterOptions()


class _CounterOptions(OptionsFlow):
    """Options that open on a menu and run over two pages."""

    async def async_step_init(self, _user_input: dict[str, Any] | None = None) -> Any:
        return self.async_show_menu(step_id="init", menu_options=["basic", "advanced"])

    async def async_step_basic(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            return self.async_show_form(step_id="basic", data_schema=_LIMITS)
        self._maximum = user_input["maximum"]
        return await self.async_step_step()

    async def async_step_step(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            return self.async_show_form(
                step_id="step", data_schema=vol.Schema({vol.Required("step"): int})
            )
        return self.async_create_entry(data={"maximum": self._maximum, **user_input})


@pytest.fixture
def counter(hass: HomeAssistant) -> Iterator[None]:
    mock_integration(
        hass,
        MockModule(
            "counter_plus",
            async_setup_entry=AsyncMock(return_value=True),
            partial_manifest={"integration_type": "helper", "config_flow": True},
        ),
    )
    mock_platform(hass, "counter_plus.config_flow", None)
    with mock_config_flow("counter_plus", _CounterFlow):
        yield


async def test_a_two_form_setup_is_walked_by_flow_id(hass: HomeAssistant, counter: None) -> None:
    first = await async_create_flow_helper(hass, "counter_plus", None, {"name": "Laps"})
    retry = await async_create_flow_helper(
        hass, "counter_plus", None, {"maximum": 0}, first["flow_id"]
    )
    done = await async_create_flow_helper(
        hass, "counter_plus", None, {"maximum": 10}, first["flow_id"]
    )

    assert first["status"] == "needs_options"
    assert first["step"] == "limits"
    assert [f["name"] for f in first["fields"]] == ["maximum"]
    assert retry["errors"] == {"maximum": "too_small"}
    assert retry["flow_id"] == first["flow_id"]
    assert done["status"] == "created", done
    (entry,) = hass.config_entries.async_entries("counter_plus")
    assert entry.title == "Laps"
    assert entry.options == {"maximum": 10}
    assert not hass.config_entries.flow.async_progress()


async def test_continuing_without_an_answer_describes_the_step_again(
    hass: HomeAssistant, counter: None
) -> None:
    first = await async_create_flow_helper(hass, "counter_plus", None, None)
    again = await async_create_flow_helper(hass, "counter_plus", None, None, first["flow_id"])

    assert first["step"] == again["step"] == "user"
    assert again["flow_id"] == first["flow_id"]
    assert len(hass.config_entries.flow.async_progress()) == 1


async def test_a_flow_not_started_here_cannot_be_continued(
    hass: HomeAssistant, counter: None
) -> None:
    """The user's own flow in the UI, or a discovery, has a flow_id too."""
    theirs = await hass.config_entries.flow.async_init("counter_plus", context={"source": "user"})

    result = await async_create_flow_helper(
        hass, "counter_plus", None, {"name": "Hijack"}, theirs["flow_id"]
    )

    assert "not a flow in progress here" in result["error"]
    assert hass.config_entries.flow.async_get(theirs["flow_id"])["step_id"] == "user"


async def test_a_flow_left_alone_is_aborted(hass: HomeAssistant, counter: None) -> None:
    first = await async_create_flow_helper(hass, "counter_plus", None, None)

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=FLOW_TTL + 1))
    await hass.async_block_till_done()
    late = await async_create_flow_helper(
        hass, "counter_plus", None, {"name": "Late"}, first["flow_id"]
    )

    assert not hass.config_entries.flow.async_progress()
    assert "timed out" in late["error"]


async def test_unloading_aborts_every_open_flow(hass: HomeAssistant, counter: None) -> None:
    await async_create_flow_helper(hass, "counter_plus", None, None)
    await async_create_flow_helper(hass, "counter_plus", None, None)

    async_close_all(hass)

    assert not hass.config_entries.flow.async_progress()


async def test_options_over_a_menu_and_two_pages(hass: HomeAssistant, counter: None) -> None:
    entry = MockConfigEntry(domain="counter_plus", title="Laps", options={"maximum": 5})
    entry.add_to_hass(hass)

    menu = await async_integration_options(hass, entry.entry_id, None, None)
    page_one = await async_integration_options(hass, entry.entry_id, "basic", None, menu["flow_id"])
    page_two = await async_integration_options(
        hass, entry.entry_id, None, {"maximum": 20}, menu["flow_id"]
    )
    saved = await async_integration_options(
        hass, entry.entry_id, None, {"step": 2}, menu["flow_id"]
    )

    assert menu["status"] == "needs_type"
    assert menu["types"] == ["basic", "advanced"]
    assert page_one["step"] == "basic"
    assert page_two["step"] == "step"
    assert saved["status"] == "saved", saved
    assert entry.options == {"maximum": 20, "step": 2}


async def test_one_entry_options_flow_cannot_answer_for_another(
    hass: HomeAssistant, counter: None
) -> None:
    mine = MockConfigEntry(domain="counter_plus", title="Mine")
    other = MockConfigEntry(domain="counter_plus", title="Other")
    mine.add_to_hass(hass)
    other.add_to_hass(hass)

    menu = await async_integration_options(hass, mine.entry_id, None, None)
    crossed = await async_integration_options(hass, other.entry_id, "basic", None, menu["flow_id"])

    assert "not a flow in progress here" in crossed["error"]


class _ConfirmFlow(ConfigFlow):
    """A setup that ends on a confirmation with no fields."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=vol.Schema({vol.Required("name"): str})
            )
        self._name = user_input["name"]
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))
        return self.async_create_entry(title=self._name, data={})


async def test_a_confirmation_step_is_answered_with_empty_fields(hass: HomeAssistant) -> None:
    """Through the MCP tool itself: ``fields: {}`` must reach the flow."""
    from custom_components.selora_ai.mcp_server.scripts_helpers import _tool_create_helper

    mock_integration(
        hass,
        MockModule(
            "confirm_plus",
            async_setup_entry=AsyncMock(return_value=True),
            partial_manifest={"integration_type": "helper", "config_flow": True},
        ),
    )
    mock_platform(hass, "confirm_plus.config_flow", None)
    with mock_config_flow("confirm_plus", _ConfirmFlow):
        asked = await _tool_create_helper(
            hass, {"domain": "confirm_plus", "fields": {"name": "Porch"}}
        )
        done = await _tool_create_helper(
            hass, {"domain": "confirm_plus", "fields": {}, "flow_id": asked["flow_id"]}
        )

    assert asked["status"] == "needs_confirmation"
    assert "{} to confirm" in asked["hint"]
    assert done["status"] == "created", done
    assert hass.config_entries.async_entries("confirm_plus")[0].title == "Porch"


class _BrokenMenuFlow(ConfigFlow):
    """A setup whose menu choice raises — before the flow is held open."""

    VERSION = 1

    async def async_step_user(self, _user_input: dict[str, Any] | None = None) -> Any:
        return self.async_show_menu(step_id="user", menu_options=["broken"])

    async def async_step_broken(self, _user_input: dict[str, Any] | None = None) -> Any:
        raise ValueError("no such option")


async def test_a_flow_that_fails_on_its_first_menu_step_is_aborted(hass: HomeAssistant) -> None:
    mock_integration(
        hass,
        MockModule(
            "broken_plus",
            async_setup_entry=AsyncMock(return_value=True),
            partial_manifest={"integration_type": "helper", "config_flow": True},
        ),
    )
    mock_platform(hass, "broken_plus.config_flow", None)
    with mock_config_flow("broken_plus", _BrokenMenuFlow):
        result = await async_create_flow_helper(hass, "broken_plus", "broken", None)

    assert "error" in result
    assert not hass.config_entries.flow.async_progress()
