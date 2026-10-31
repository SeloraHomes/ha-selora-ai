"""Tests for adding an integration over MCP through its own config flow.

A user asked a chat to track two more teams in Team Tracker; the MCP surface
could list, reload and reconfigure integrations but not add an entry, so the
model could only say it lacked the tool. Logins never reach the model, so a
setup that asks for one is refused and its flow aborted.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

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

from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.flow_sessions import keep_open
from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import TOOL_ADD_INTEGRATION


class _TeamFlow(ConfigFlow):
    """A team tracker: a league, then a team in it."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=vol.Schema({vol.Required("league"): str})
            )
        self._league = user_input["league"]
        return await self.async_step_team()

    async def async_step_team(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            return self.async_show_form(
                step_id="team", data_schema=vol.Schema({vol.Required("team"): str})
            )
        await self.async_set_unique_id(f"{self._league}-{user_input['team']}")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=user_input["team"], data={"league": self._league, **user_input}
        )


class _LoginFlow(ConfigFlow):
    """A cloud account, asked for its password up front."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {vol.Required("username"): str, vol.Required("password"): str}
                ),
            )
        return self.async_create_entry(title=user_input["username"], data=user_input)


class _SignInFlow(ConfigFlow):
    """An account signed into in the browser."""

    VERSION = 1

    async def async_step_user(self, _user_input: dict[str, Any] | None = None) -> Any:
        return self.async_external_step(step_id="user", url="https://example.com/auth")


def _register(hass: HomeAssistant, domain: str, **manifest: Any) -> None:
    mock_integration(
        hass,
        MockModule(
            domain,
            async_setup_entry=AsyncMock(return_value=True),
            partial_manifest={"config_flow": True, **manifest},
        ),
    )
    mock_platform(hass, f"{domain}.config_flow", None)


@pytest.fixture
def teams(hass: HomeAssistant) -> Iterator[None]:
    _register(hass, "teams")
    with mock_config_flow("teams", _TeamFlow):
        yield


@pytest.fixture
def cloud(hass: HomeAssistant) -> Iterator[None]:
    _register(hass, "cloudy")
    with mock_config_flow("cloudy", _LoginFlow):
        yield


async def _add(hass: HomeAssistant, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[TOOL_ADD_INTEGRATION](hass, arguments)


async def test_an_integration_is_added_one_step_per_call(hass: HomeAssistant, teams: None) -> None:
    first = await _add(hass, domain="teams")
    second = await _add(hass, domain="teams", flow_id=first["flow_id"], fields={"league": "MLB"})
    done = await _add(hass, domain="teams", flow_id=first["flow_id"], fields={"team": "TB"})

    assert first["status"] == "needs_options"
    assert [f["name"] for f in first["fields"]] == ["league"]
    assert second["step"] == "team"
    assert done["status"] == "added", done
    assert done["domain"] == "teams"
    assert done["title"] == "TB"
    assert done["state"] == "loaded"
    (entry,) = hass.config_entries.async_entries("teams")
    assert entry.data == {"league": "MLB", "team": "TB"}
    assert not hass.config_entries.flow.async_progress()


async def test_a_second_entry_of_the_same_integration_is_added(
    hass: HomeAssistant, teams: None
) -> None:
    MockConfigEntry(domain="teams", unique_id="NFL-GB", title="GB").add_to_hass(hass)

    first = await _add(hass, domain="teams")
    await _add(hass, domain="teams", flow_id=first["flow_id"], fields={"league": "NFL"})
    done = await _add(hass, domain="teams", flow_id=first["flow_id"], fields={"team": "TB"})

    assert done["status"] == "added", done
    assert len(hass.config_entries.async_entries("teams")) == 2


async def test_an_entry_already_there_says_so(hass: HomeAssistant, teams: None) -> None:
    MockConfigEntry(domain="teams", unique_id="NFL-TB", title="TB").add_to_hass(hass)

    first = await _add(hass, domain="teams")
    await _add(hass, domain="teams", flow_id=first["flow_id"], fields={"league": "NFL"})
    again = await _add(hass, domain="teams", flow_id=first["flow_id"], fields={"team": "TB"})

    assert "already_configured" in again["error"]
    assert not hass.config_entries.flow.async_progress()


async def test_the_first_call_only_describes(hass: HomeAssistant, teams: None) -> None:
    first = await _add(hass, domain="teams", fields={"league": "MLB"})

    assert first["step"] == "user", first
    assert first["status"] == "needs_options"


async def test_a_setup_asking_for_a_login_is_refused_and_aborted(
    hass: HomeAssistant, cloud: None
) -> None:
    result = await _add(hass, domain="cloudy")

    assert "Settings" in result["error"]
    assert "flow_id" not in result
    assert not hass.config_entries.flow.async_progress()
    assert not hass.config_entries.async_entries("cloudy")


async def test_a_browser_sign_in_is_refused_and_aborted(hass: HomeAssistant) -> None:
    _register(hass, "signin")
    with mock_config_flow("signin", _SignInFlow):
        result = await _add(hass, domain="signin")

    assert "browser" in result["error"]
    assert not hass.config_entries.flow.async_progress()


async def test_a_login_in_the_values_is_refused_unsent(hass: HomeAssistant, teams: None) -> None:
    first = await _add(hass, domain="teams")

    refused = await _add(
        hass, domain="teams", flow_id=first["flow_id"], fields={"league": "MLB", "api_key": "x"}
    )

    assert "Settings" in refused["error"]
    assert not hass.config_entries.flow.async_progress()


async def test_a_login_refusal_leaves_a_flow_held_for_something_else(
    hass: HomeAssistant, teams: None
) -> None:
    other = await hass.config_entries.flow.async_init("teams", context={"source": "user"})
    keep_open(hass, hass.config_entries.flow, other["flow_id"], ("helper", "teams"), other)

    await _add(hass, domain="teams", flow_id=other["flow_id"], fields={"password": "x"})

    assert [f["flow_id"] for f in hass.config_entries.flow.async_progress()] == [other["flow_id"]]


async def test_what_is_not_added_here(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN).add_to_hass(hass)
    _register(hass, "meter", integration_type="helper")
    _register(hass, "yaml_only", config_flow=False)

    assert "already set up" in (await _add(hass, domain=DOMAIN))["error"]
    assert "hacs_install" in (await _add(hass, domain="not_installed"))["error"]
    assert "create_helper" in (await _add(hass, domain="meter"))["error"]
    assert "configuration.yaml" in (await _add(hass, domain="yaml_only"))["error"]


@pytest.mark.parametrize(
    ("name", "secret"),
    [
        ("password", True),
        ("api_key", True),
        ("access_token", True),
        ("otp", True),
        ("verification_code", True),
        ("code", True),
        ("pin", True),
        ("station_code", False),
        ("country_code", False),
        ("bypass_cache", False),
        ("spinner", False),
        ("username", False),
        ("refreshToken", True),
        ("clientSecret", True),
        ("APIKey", True),
        ("2fa_code", True),
        ("stationCode", False),
        ("bindkey", True),
        ("pincode", True),
        ("noise_psk", True),
        ("key_id", True),
        ("keep_alive", False),
    ],
)
def test_a_credential_is_told_by_its_name_parts(name: str, secret: bool) -> None:
    from custom_components.selora_ai.helper_flow import is_credential_name

    assert is_credential_name(name) is secret


def test_adding_needs_admin() -> None:
    assert TOOL_ADD_INTEGRATION in mcp_access._ADMIN_TOOLS
