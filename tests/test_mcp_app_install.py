"""Finding, installing and configuring apps (formerly add-ons) over MCP.

An app could be listed, started and read, but not found in the store,
installed, or configured — "set up the Mosquitto broker" ended at "do it in
Settings". Installing asks first and says what the app may reach; options are
merged over the current ones, since the Supervisor replaces the whole set,
and a password is never read back.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from aiohasupervisor import SupervisorBadRequestError
from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai import apps_manager
from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import (
    TOOL_GET_APP_OPTIONS,
    TOOL_INSTALL_APP,
    TOOL_LIST_APPS,
    TOOL_SEARCH_APP_STORE,
    TOOL_SET_APP_OPTIONS,
)


def _store_app(slug: str, name: str, **extra: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "slug": slug,
        "name": name,
        "description": f"{name} for your home",
        "repository": "core",
        "version_latest": "1.0",
        "installed": False,
        "available": True,
        "full_access": False,
        "docker_api": False,
        "host_network": False,
        "homeassistant_api": False,
        "host_pid": False,
        "supervisor_api": False,
        "supervisor_role": "default",
        "auth_api": False,
        "apparmor": "default",
        "rating": 6,
    }
    return SimpleNamespace(**{**fields, **extra})


MOSQUITTO = _store_app("core_mosquitto", "Mosquitto broker", host_network=True)
EDITOR = _store_app("core_configurator", "File editor", installed=True)


@pytest.fixture
def supervisor(hass: HomeAssistant) -> Any:
    client = SimpleNamespace(
        store=SimpleNamespace(
            addons_list=AsyncMock(return_value=[MOSQUITTO, EDITOR]),
            addon_info=AsyncMock(return_value=MOSQUITTO),
            install_addon=AsyncMock(),
        ),
        addons=SimpleNamespace(
            addon_info=AsyncMock(
                return_value=SimpleNamespace(
                    slug="core_mosquitto",
                    name="Mosquitto broker",
                    state="started",
                    options={"logins": [], "require_certificate": False, "password": "s3cr3t"},
                    schema=[
                        {"name": "logins", "type": "schema"},
                        {"name": "require_certificate", "type": "boolean"},
                        {"name": "password", "type": "password", "optional": True},
                    ],
                )
            ),
            addon_config_validate=AsyncMock(return_value=SimpleNamespace(valid=True, message="")),
            set_addon_options=AsyncMock(),
        ),
    )
    with (
        patch.object(apps_manager, "_supervised", return_value=True),
        patch.object(apps_manager, "_installed", return_value=[]),
        patch.object(apps_manager, "_client", return_value=client),
    ):
        yield client


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[tool](hass, arguments)


async def _confirm(hass: HomeAssistant, slug: str = "core_mosquitto") -> dict[str, Any]:
    asked = await _mcp(hass, TOOL_INSTALL_APP, slug=slug)
    return await _mcp(
        hass, TOOL_INSTALL_APP, slug=slug, confirmed=True, fingerprint=asked["fingerprint"]
    )


async def test_the_store_is_searched_by_words(hass: HomeAssistant, supervisor: Any) -> None:
    found = await _mcp(hass, TOOL_SEARCH_APP_STORE, query="mosquitto broker")
    everything = await _mcp(hass, TOOL_SEARCH_APP_STORE)

    assert [a["slug"] for a in found["apps"]] == ["core_mosquitto"]
    # Installed first.
    assert [a["slug"] for a in everything["apps"]] == ["core_configurator", "core_mosquitto"]


async def test_installing_asks_first_and_says_what_the_app_reaches(
    hass: HomeAssistant, supervisor: Any
) -> None:
    asked = await _mcp(hass, TOOL_INSTALL_APP, slug="core_mosquitto")

    assert asked["requires_confirmation"] is True
    assert asked["can_reach"] == ["the host network"]
    assert asked["security_rating"] == 6
    supervisor.store.install_addon.assert_not_called()


async def test_every_privilege_the_store_reports_is_named(
    hass: HomeAssistant, supervisor: Any
) -> None:
    supervisor.store.addon_info.return_value = _store_app(
        "x_tool",
        "Tool",
        host_pid=True,
        supervisor_api=True,
        supervisor_role="manager",
        auth_api=True,
        apparmor="disable",
        rating=1,
    )

    asked = await _mcp(hass, TOOL_INSTALL_APP, slug="x_tool")

    assert asked["can_reach"] == [
        "the host's processes",
        "the Supervisor API as manager",
        "Home Assistant user logins",
        "no AppArmor confinement",
    ]
    assert asked["security_rating"] == 1


async def test_an_app_that_changed_since_it_was_shown_is_shown_again(
    hass: HomeAssistant, supervisor: Any
) -> None:
    asked = await _mcp(hass, TOOL_INSTALL_APP, slug="core_mosquitto")
    supervisor.store.addon_info.return_value = _store_app(
        "core_mosquitto", "Mosquitto broker", host_network=True, docker_api=True
    )

    confirmed = await _mcp(
        hass,
        TOOL_INSTALL_APP,
        slug="core_mosquitto",
        confirmed=True,
        fingerprint=asked["fingerprint"],
    )
    no_fingerprint = await _mcp(hass, TOOL_INSTALL_APP, slug="core_mosquitto", confirmed=True)

    assert confirmed["requires_confirmation"] is True
    assert "not what was shown" in confirmed["changed"]
    assert "the Docker socket (control of every container)" in confirmed["can_reach"]
    assert no_fingerprint["requires_confirmation"] is True
    supervisor.store.install_addon.assert_not_called()
    assert (await _mcp(hass, TOOL_LIST_APPS)).get("installs") is None


async def test_two_confirmed_calls_at_once_install_once(
    hass: HomeAssistant, supervisor: Any
) -> None:
    asked = await _mcp(hass, TOOL_INSTALL_APP, slug="core_mosquitto")
    arguments = {"slug": "core_mosquitto", "confirmed": True, "fingerprint": asked["fingerprint"]}
    release = asyncio.Event()

    async def _lookup(_slug: str) -> Any:
        await asyncio.sleep(0)  # the Supervisor round trip both calls wait on
        return MOSQUITTO

    async def _slow(_slug: str) -> None:
        await release.wait()

    supervisor.store.addon_info.side_effect = _lookup
    supervisor.store.install_addon.side_effect = _slow

    first, second = await asyncio.gather(
        _mcp(hass, TOOL_INSTALL_APP, **arguments), _mcp(hass, TOOL_INSTALL_APP, **arguments)
    )
    release.set()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert first["status"] == second["status"] == "installing"
    supervisor.store.install_addon.assert_called_once_with("core_mosquitto")


async def test_a_confirmed_install_runs_in_the_background(
    hass: HomeAssistant, supervisor: Any
) -> None:
    release = asyncio.Event()

    async def _slow(_slug: str) -> None:
        await release.wait()

    supervisor.store.install_addon.side_effect = _slow

    started = await _confirm(hass)
    while_running = await _mcp(hass, TOOL_LIST_APPS)
    again = await _mcp(hass, TOOL_INSTALL_APP, slug="core_mosquitto", confirmed=True)
    release.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    after = await _mcp(hass, TOOL_LIST_APPS)

    assert started["status"] == "installing"
    assert while_running["installs"] == {"core_mosquitto": "installing"}
    assert again["status"] == "installing"
    supervisor.store.install_addon.assert_called_once_with("core_mosquitto")
    assert after["installs"] == {"core_mosquitto": "installed"}


async def test_a_failed_install_is_reported(hass: HomeAssistant, supervisor: Any) -> None:
    supervisor.store.install_addon.side_effect = SupervisorBadRequestError("no space left")

    await _confirm(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    listed = await _mcp(hass, TOOL_LIST_APPS)
    assert listed["installs"]["core_mosquitto"].startswith("failed: no space left")


async def test_what_cannot_be_installed_is_refused(hass: HomeAssistant, supervisor: Any) -> None:
    supervisor.store.addon_info.return_value = EDITOR
    installed = await _mcp(hass, TOOL_INSTALL_APP, slug="core_configurator", confirmed=True)
    after = await _mcp(hass, TOOL_LIST_APPS)
    bad_slug = await _mcp(hass, TOOL_INSTALL_APP, slug="../etc", confirmed=True)

    assert "already installed" in installed["error"]
    # The reservation a confirmed call takes is released on a refusal.
    assert after.get("installs") is None
    assert "slug" in bad_slug["error"]
    supervisor.store.install_addon.assert_not_called()


async def test_options_never_show_a_password(hass: HomeAssistant, supervisor: Any) -> None:
    result = await _mcp(hass, TOOL_GET_APP_OPTIONS, slug="core_mosquitto")

    assert "s3cr3t" not in str(result)
    assert result["options"]["password"] == {"is_set": True}
    assert result["options"]["require_certificate"] is False


async def test_a_change_is_merged_validated_then_saved(
    hass: HomeAssistant, supervisor: Any
) -> None:
    result = await _mcp(
        hass,
        TOOL_SET_APP_OPTIONS,
        slug="core_mosquitto",
        # The password read back unchanged is not a new password.
        options={"require_certificate": True, "password": {"is_set": True}},
    )

    assert result["status"] == "saved", result
    assert result["changed"] == ["require_certificate"]
    assert "restart" in result["hint"]
    sent = supervisor.addons.set_addon_options.call_args.args[1].config
    assert sent == {"logins": [], "require_certificate": True, "password": "s3cr3t"}


async def test_options_the_app_refuses_are_not_saved(hass: HomeAssistant, supervisor: Any) -> None:
    supervisor.addons.addon_config_validate.return_value = SimpleNamespace(
        valid=False, message="Missing option 'logins'"
    )

    result = await _mcp(hass, TOOL_SET_APP_OPTIONS, slug="core_mosquitto", options={"x": 1})

    assert "Missing option" in result["error"]
    supervisor.addons.set_addon_options.assert_not_called()


def test_every_new_app_tool_needs_admin() -> None:
    for tool in (
        TOOL_SEARCH_APP_STORE,
        TOOL_INSTALL_APP,
        TOOL_GET_APP_OPTIONS,
        TOOL_SET_APP_OPTIONS,
    ):
        assert tool in mcp_access._ADMIN_TOOLS, tool


async def test_nested_passwords_are_hidden_and_kept(hass: HomeAssistant, supervisor: Any) -> None:
    """Mosquitto's logins list nests a password-typed field."""
    supervisor.addons.addon_info.return_value = SimpleNamespace(
        slug="core_mosquitto",
        name="Mosquitto broker",
        state="stopped",
        options={"logins": [{"username": "bob", "password": "hunter2"}], "wifi": {"psk": "abc"}},
        schema=[
            {
                "name": "logins",
                "type": "schema",
                "multiple": True,
                "schema": [
                    {"name": "username", "type": "string"},
                    {"name": "password", "type": "password"},
                ],
            },
        ],
    )

    shown = await _mcp(hass, TOOL_GET_APP_OPTIONS, slug="core_mosquitto")
    assert "hunter2" not in str(shown) and "abc" not in str(shown)
    assert shown["options"]["logins"][0] == {"username": "bob", "password": {"is_set": True}}

    # The listing sent back with a new user beside it keeps bob's password.
    logins = [*shown["options"]["logins"], {"username": "amy", "password": "s3"}]
    await _mcp(hass, TOOL_SET_APP_OPTIONS, slug="core_mosquitto", options={"logins": logins})
    sent = supervisor.addons.set_addon_options.call_args.args[1].config
    assert sent["logins"] == [
        {"username": "bob", "password": "hunter2"},
        {"username": "amy", "password": "s3"},
    ]


async def test_a_hidden_password_follows_its_own_entry_not_its_position(
    hass: HomeAssistant, supervisor: Any
) -> None:
    """Removing the first login must not hand its password to the second."""
    schema = [
        {
            "name": "logins",
            "type": "schema",
            "multiple": True,
            "schema": [
                {"name": "username", "type": "string"},
                {"name": "password", "type": "password"},
            ],
        }
    ]
    supervisor.addons.addon_info.return_value = SimpleNamespace(
        slug="core_mosquitto",
        name="Mosquitto broker",
        state="stopped",
        options={
            "logins": [
                {"username": "bob", "password": "bob-pw"},
                {"username": "amy", "password": "amy-pw"},
            ]
        },
        schema=schema,
    )

    await _mcp(
        hass,
        TOOL_SET_APP_OPTIONS,
        slug="core_mosquitto",
        options={"logins": [{"username": "amy", "password": {"is_set": True}}]},
    )
    sent = supervisor.addons.set_addon_options.call_args.args[1].config
    assert sent["logins"] == [{"username": "amy", "password": "amy-pw"}]

    renamed = await _mcp(
        hass,
        TOOL_SET_APP_OPTIONS,
        slug="core_mosquitto",
        options={"logins": [{"username": "zoe", "password": {"is_set": True}}]},
    )
    assert "could not be matched" in renamed["error"]
