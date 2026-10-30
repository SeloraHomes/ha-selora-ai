"""Reaching Frigate without its integration, offering its tools only when it is
reachable, and changing its cameras over MCP in two calls."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.const import (
    CONF_FRIGATE_PASSWORD,
    CONF_FRIGATE_URL,
    CONF_FRIGATE_USERNAME,
    DOMAIN,
)
from custom_components.selora_ai.frigate import client as frigate_client
from custom_components.selora_ai.frigate.client import (
    FrigateClient,
    frigate_available,
    frigate_connection,
    normalize_frigate_url,
)
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.access import FRIGATE_TOOLS
from custom_components.selora_ai.mcp_server.names import (
    TOOL_ADD_CAMERAS_TO_FRIGATE,
    TOOL_REMOVE_FRIGATE_CAMERA,
    TOOL_SET_CAMERA_OBJECTS,
)
from custom_components.selora_ai.selora_auth import SeloraAuthContext
from custom_components.selora_ai.tool_registry import FRIGATE_TOOL_NAMES

ADMIN = SeloraAuthContext("admin", None, True, "ha_token")


def _integration(hass: HomeAssistant, url: str = "http://frigate.local:5000") -> None:
    entry = MockConfigEntry(domain="frigate", data={"url": url})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)


def _settings(hass: HomeAssistant, url: str = "http://192.168.1.10:5000") -> None:
    MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_FRIGATE_URL: url,
            CONF_FRIGATE_USERNAME: "admin",
            CONF_FRIGATE_PASSWORD: "secret",
        },
    ).add_to_hass(hass)


async def _call(hass: HomeAssistant, name: str, **arguments: Any) -> dict[str, Any]:
    result = await mcp_dispatch._jsonrpc_dispatch(
        hass, "tools/call", {"name": name, "arguments": arguments}, ADMIN
    )
    return json.loads(result["content"][0]["text"])


async def _listed(hass: HomeAssistant) -> set[str]:
    result = await mcp_dispatch._jsonrpc_dispatch(hass, "tools/list", None, ADMIN)
    return {t["name"] for t in result["tools"]}


def _chat_tools(hass: HomeAssistant) -> set[str]:
    from custom_components.selora_ai.llm_client.client import LLMClient

    llm = MagicMock(spec=LLMClient)
    llm._hass = hass
    llm._provider = MagicMock(is_low_context=False)
    llm._provider.format_tool = lambda t: {"name": t.name}
    return {t["name"] for t in LLMClient._get_tools_for_provider(llm, panel_available=True)}


# ── The connection ────────────────────────────────────────────────────────────


async def test_without_frigate_there_is_no_connection(hass: HomeAssistant) -> None:
    assert frigate_connection(hass) is None
    assert FrigateClient.from_hass(hass) is None


async def test_the_settings_reach_a_frigate_without_its_integration(hass: HomeAssistant) -> None:
    _settings(hass)

    connection = frigate_connection(hass)

    assert connection == ("http://192.168.1.10:5000", "admin", "secret", "settings")


async def test_the_integration_wins_over_the_settings(hass: HomeAssistant) -> None:
    """On a hub the integration points at the host's proxy, and its camera
    entities are what the rest of Selora reads."""
    _settings(hass)
    _integration(hass)

    connection = frigate_connection(hass)

    assert connection is not None
    assert (connection.url, connection.source) == ("http://frigate.local:5000", "integration")


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("http://192.168.1.10:5000/", "http://192.168.1.10:5000"),
        (" https://frigate.lan/proxy/ ", "https://frigate.lan/proxy"),
        ("192.168.1.10:5000", None),
        ("ftp://frigate.lan", None),
        ("http://admin:pw@frigate.lan", None),
        ("http://frigate.lan:99999", None),
        ("http://frigate.lan/?x=1", None),
    ],
)
def test_only_an_http_address_is_frigate_s(given: str, expected: str | None) -> None:
    assert normalize_frigate_url(given) == expected


# ── Which tools are offered ───────────────────────────────────────────────────


async def test_frigate_s_tools_are_offered_only_with_frigate(hass: HomeAssistant) -> None:
    assert not FRIGATE_TOOL_NAMES & _chat_tools(hass)
    assert not FRIGATE_TOOLS & await _listed(hass)

    _settings(hass)

    assert _chat_tools(hass) >= FRIGATE_TOOL_NAMES
    assert await _listed(hass) >= FRIGATE_TOOLS


async def test_the_server_version_moves_with_frigate(hass: HomeAssistant) -> None:
    """A client keeps the tool list while the version is unchanged."""
    before = await mcp_dispatch._async_server_version(hass)
    _integration(hass)

    assert await mcp_dispatch._async_server_version(hass) == f"{before}.frigate"


# ── Changes over MCP ──────────────────────────────────────────────────────────

_OBJECTS_CARD = {
    "requires_approval": True,
    "destructive": {
        "kind": "frigate_objects",
        "verb": "set_objects",
        "target_id": "driveway",
        "entity_id": "",
        "name": "Driveway",
        "label": "Driveway starts looking for car (Frigate restarts)",
        "fingerprint": "abc",
    },
}


async def test_a_change_first_says_what_it_does(hass: HomeAssistant) -> None:
    _settings(hass)
    confirm = AsyncMock()
    with (
        patch(
            "custom_components.selora_ai.frigate.objects.async_preview_set_objects",
            AsyncMock(return_value=_OBJECTS_CARD),
        ),
        patch("custom_components.selora_ai.frigate.objects.async_set_objects_confirmed", confirm),
    ):
        result = await _call(hass, TOOL_SET_CAMERA_OBJECTS, camera="Driveway", add=["car"])

    assert result["requires_confirmation"] is True
    assert result["change"] == "Driveway starts looking for car (Frigate restarts)"
    assert result["confirm_token"] == "abc"
    confirm.assert_not_awaited()


async def test_a_confirmed_change_is_checked_against_its_token(hass: HomeAssistant) -> None:
    """The token, not a fresh preview, is what the confirm checks: a camera
    changed since the user agreed is refused."""
    _settings(hass)
    confirm = AsyncMock(return_value={"camera": "driveway", "done": "ok"})
    with (
        patch(
            "custom_components.selora_ai.frigate.objects.async_preview_set_objects",
            AsyncMock(return_value=_OBJECTS_CARD),
        ),
        patch("custom_components.selora_ai.frigate.objects.async_set_objects_confirmed", confirm),
    ):
        missing = await _call(
            hass, TOOL_SET_CAMERA_OBJECTS, camera="Driveway", add=["car"], confirmed=True
        )
        done = await _call(
            hass,
            TOOL_SET_CAMERA_OBJECTS,
            camera="Driveway",
            add=["car"],
            confirmed=True,
            confirm_token="shown-earlier",
        )

    assert "confirm_token" in missing["error"]
    assert done == {"camera": "driveway", "done": "ok"}
    assert confirm.await_args.args[1:3] == ("driveway", "shown-earlier")


async def test_removing_a_camera_is_two_calls(hass: HomeAssistant) -> None:
    _settings(hass)
    card = {
        "requires_approval": True,
        "delete": {"target_id": "garage", "name": "Garage", "label": "Garage", "fingerprint": "f"},
    }
    remove = AsyncMock(return_value={"removed": "garage"})
    with (
        patch(
            "custom_components.selora_ai.frigate.removal.async_preview_remove_camera",
            AsyncMock(return_value=card),
        ),
        patch("custom_components.selora_ai.frigate.removal.async_remove_camera_confirmed", remove),
    ):
        asked = await _call(hass, TOOL_REMOVE_FRIGATE_CAMERA, camera="Garage")
        done = await _call(
            hass, TOOL_REMOVE_FRIGATE_CAMERA, camera="Garage", confirmed=True, confirm_token="f"
        )

    assert asked["requires_confirmation"] is True
    assert done == {"removed": "garage"}
    remove.assert_awaited_once()


def _add_card(*rows: dict[str, Any]) -> dict[str, Any]:
    return {
        "requires_approval": True,
        "client_action": {
            "kind": "add_frigate_cameras",
            "plan_id": "plan1",
            "label": "Add",
            "cameras": list(rows),
        },
        "cameras": list(rows),
        "skipped": [],
    }


async def test_a_network_camera_s_login_never_goes_through_mcp(hass: HomeAssistant) -> None:
    _settings(hass)
    card = _add_card(
        {
            "key": "1.2.3.4",
            "source": "network",
            "name": "E1",
            "address": "192.168.1.42",
            "needs_login": True,
        }
    )
    with patch(
        "custom_components.selora_ai.frigate.setup.async_preview_add_cameras",
        AsyncMock(return_value=card),
    ):
        result = await _call(hass, TOOL_ADD_CAMERAS_TO_FRIGATE, hosts=["192.168.1.42"])

    assert result["needs_login"] == [{"name": "E1", "address": "192.168.1.42"}]
    assert "requires_confirmation" not in result


async def test_home_assistant_s_cameras_are_added_with_the_plan_shown(
    hass: HomeAssistant,
) -> None:
    _settings(hass)
    card = _add_card({"key": "porch", "source": "home_assistant", "name": "Porch"})
    confirm = AsyncMock(return_value={"status": "added", "cameras": ["Porch"]})
    with (
        patch(
            "custom_components.selora_ai.frigate.setup.async_preview_add_cameras",
            AsyncMock(return_value=card),
        ),
        patch("custom_components.selora_ai.frigate.setup.async_add_cameras_confirmed", confirm),
    ):
        asked = await _call(hass, TOOL_ADD_CAMERAS_TO_FRIGATE)
        done = await _call(
            hass, TOOL_ADD_CAMERAS_TO_FRIGATE, confirmed=True, confirm_token=asked["confirm_token"]
        )

    assert asked["confirm_token"] == "plan1"
    assert done["status"] == "added"
    # No login is ever passed: only the plan the user was shown.
    confirm.assert_awaited_once_with(hass, "plan1", {})


async def test_a_refused_login_names_the_setting(hass: HomeAssistant) -> None:
    _settings(hass)
    client = frigate_client.FrigateClient.from_hass(hass)
    assert client is not None
    response = MagicMock(status=401)
    session = MagicMock()
    session.post.return_value.__aenter__ = AsyncMock(return_value=response)
    session.post.return_value.__aexit__ = AsyncMock(return_value=False)
    client._session = session

    with pytest.raises(frigate_client.FrigateError, match="refused the login"):
        await client.version()


async def test_frigate_is_available_from_the_settings(hass: HomeAssistant) -> None:
    assert not frigate_available(hass)
    _settings(hass)
    assert frigate_available(hass)


# ── The saved password stays with its address ─────────────────────────────────


def _admin() -> MagicMock:
    conn = MagicMock()
    conn.user.is_admin = True
    return conn


async def _save(hass: HomeAssistant, **config: str) -> dict[str, Any]:
    from custom_components.selora_ai.websocket.linking import _handle_websocket_update_config

    await _handle_websocket_update_config.__wrapped__(
        hass, _admin(), {"id": 1, "type": "selora_ai/update_config", "config": config}
    )
    return dict(hass.config_entries.async_entries(DOMAIN)[0].data)


async def test_a_blank_password_keeps_the_saved_one(hass: HomeAssistant) -> None:
    _settings(hass)

    data = await _save(
        hass, frigate_url="http://192.168.1.10:5000/", frigate_username="admin", frigate_password=""
    )

    assert data[CONF_FRIGATE_PASSWORD] == "secret"


async def test_a_new_address_does_not_keep_the_password(hass: HomeAssistant) -> None:
    """Kept, it would be sent to the new host on the next login."""
    _settings(hass)

    data = await _save(
        hass, frigate_url="http://elsewhere.lan:5000", frigate_username="admin", frigate_password=""
    )

    assert data[CONF_FRIGATE_URL] == "http://elsewhere.lan:5000"
    assert data[CONF_FRIGATE_PASSWORD] == ""


async def test_clearing_the_address_clears_the_login(hass: HomeAssistant) -> None:
    _settings(hass)

    data = await _save(hass, frigate_url="")

    assert (data[CONF_FRIGATE_URL], data[CONF_FRIGATE_USERNAME], data[CONF_FRIGATE_PASSWORD]) == (
        "",
        "",
        "",
    )


async def _test_connection(hass: HomeAssistant, url: str) -> MagicMock:
    from custom_components.selora_ai.websocket import frigate as ws_frigate

    made = MagicMock()
    made.return_value.version = AsyncMock(return_value="0.16.0")
    made.return_value.get_config = AsyncMock(return_value={"cameras": {}})
    with patch.object(ws_frigate, "FrigateClient", made):
        await ws_frigate._handle_websocket_frigate_test_connection.__wrapped__(
            hass,
            _admin(),
            {"id": 1, "url": url, "username": "admin", "password": ""},
        )
    return made


async def test_testing_the_saved_address_uses_the_saved_password(hass: HomeAssistant) -> None:
    _settings(hass)

    made = await _test_connection(hass, "http://192.168.1.10:5000")

    assert made.call_args.args[1:] == ("http://192.168.1.10:5000", "admin", "secret")


async def test_testing_another_address_never_sends_the_saved_password(
    hass: HomeAssistant,
) -> None:
    _settings(hass)

    made = await _test_connection(hass, "http://attacker.lan:5000")

    assert made.call_args.args[1:] == ("http://attacker.lan:5000", "admin", None)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://10.42.42.2:5000", "hub"),
        # The host is the signal, so a port change keeps it.
        ("http://10.42.42.2:8971", "hub"),
        ("http://192.168.1.10:5000", "integration"),
    ],
)
async def test_settings_say_who_connects_frigate(
    hass: HomeAssistant, url: str, expected: str
) -> None:
    """Never where: the address cannot be changed on that page. Selora OS
    adds the entry through the config flow, so its source is "user" like a
    hand-made one."""
    from custom_components.selora_ai.websocket.linking import _frigate_view

    entry = MockConfigEntry(domain="frigate", source="user", data={"url": url})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)

    view = _frigate_view(hass, {})

    assert view["frigate_source"] == expected
    assert url not in json.dumps(view)
