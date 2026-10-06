"""Changing a dashboard resource in place: a card's new version, a new type.

Resources could be added and removed, not changed, so moving a card to a new
version meant removing it and adding it again — and in between every card
using it renders "Custom element doesn't exist". An update keeps the resource
registered, behind the same gates as adding one.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import TOOL_UPDATE_DASHBOARD_RESOURCE

V1 = "/hacsfiles/button-card/button-card.js?hacstag=1"
V2 = "/hacsfiles/button-card/button-card.js?hacstag=2"


@pytest.fixture
async def lovelace(hass: HomeAssistant) -> HomeAssistant:
    assert await async_setup_component(hass, "lovelace", {"lovelace": {"mode": "storage"}})
    await hass.async_block_till_done()
    return hass


async def _call(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[f"selora_{tool}"](hass, arguments)


async def _resources(hass: HomeAssistant) -> list[dict[str, Any]]:
    return (await _call(hass, "list_dashboard_resources"))["resources"]


async def test_a_new_version_keeps_the_resource(lovelace: HomeAssistant) -> None:
    added = await _call(lovelace, "add_dashboard_resource", url=V1)

    result = await _call(lovelace, "update_dashboard_resource", resource=added["id"], url=V2)

    assert result["updated"] is True, result
    assert result["previous_url"] == V1
    assert [(r["id"], r["url"]) for r in await _resources(lovelace)] == [(added["id"], V2)]


async def test_found_by_its_current_url_and_its_type_changed(lovelace: HomeAssistant) -> None:
    await _call(lovelace, "add_dashboard_resource", url="/local/theme.css", type="module")

    result = await _call(
        lovelace, "update_dashboard_resource", resource="/local/theme.css", type="css"
    )

    assert result["type"] == "css"


async def test_a_new_external_url_waits_for_confirmation(lovelace: HomeAssistant) -> None:
    added = await _call(lovelace, "add_dashboard_resource", url=V1)
    url = "https://cdn.example.com/button-card.js"

    first = await _call(lovelace, "update_dashboard_resource", resource=added["id"], url=url)
    assert first["requires_confirmation"] is True
    assert [r["url"] for r in await _resources(lovelace)] == [V1]

    second = await _call(
        lovelace, "update_dashboard_resource", resource=added["id"], url=url, confirmed=True
    )
    assert second["updated"] is True


@pytest.mark.parametrize(
    ("url", "says"),
    [
        ("javascript:alert(1)", "https://"),
        ("/hacsfiles/../secret.js", "plain"),
        ("/selora_ai_resources/x.js", "recipes"),
        ("/local/other.js", "already registered"),
    ],
)
async def test_what_adding_refuses_updating_refuses(
    lovelace: HomeAssistant, url: str, says: str
) -> None:
    added = await _call(lovelace, "add_dashboard_resource", url=V1)
    await _call(lovelace, "add_dashboard_resource", url="/local/other.js")

    result = await _call(lovelace, "update_dashboard_resource", resource=added["id"], url=url)

    assert says in result["error"]
    assert V1 in [r["url"] for r in await _resources(lovelace)]


def test_updating_needs_admin() -> None:
    assert TOOL_UPDATE_DASHBOARD_RESOURCE in mcp_access._ADMIN_TOOLS
