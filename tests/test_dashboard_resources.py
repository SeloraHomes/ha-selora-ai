"""Tests for listing, adding and removing dashboard resources over MCP.

A resource is code every user's browser runs, so where it comes from decides
what it takes: a path on the hub is added directly, an external URL only once
confirmed, anything else never.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai import mcp_server
from custom_components.selora_ai.command_policy_options import CommandPolicyOptions

BUTTON_CARD = "/hacsfiles/button-card/button-card.js"


@pytest.fixture
async def lovelace(hass: HomeAssistant) -> HomeAssistant:
    assert await async_setup_component(hass, "lovelace", {"lovelace": {"mode": "storage"}})
    await hass.async_block_till_done()
    return hass


async def _call(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await mcp_server._get_tool_handlers()[f"selora_{tool}"](hass, arguments)


async def _urls(hass: HomeAssistant) -> list[str]:
    listed = await _call(hass, "list_dashboard_resources")
    return [r["url"] for r in listed["resources"]]


async def test_a_hacs_card_is_added_and_listed(lovelace: HomeAssistant) -> None:
    result = await _call(lovelace, "add_dashboard_resource", url=BUTTON_CARD)

    assert result["added"] is True, result
    assert result["type"] == "module"
    listed = await _call(lovelace, "list_dashboard_resources")
    assert listed["editable"] is True
    assert [r["url"] for r in listed["resources"]] == [BUTTON_CARD]


async def test_an_external_url_waits_for_confirmation(lovelace: HomeAssistant) -> None:
    url = "https://cdn.example.com/card.js"

    first = await _call(lovelace, "add_dashboard_resource", url=url)

    assert first["requires_confirmation"] is True
    assert await _urls(lovelace) == []

    second = await _call(lovelace, "add_dashboard_resource", url=url, confirmed=True)

    assert second["added"] is True, second
    assert await _urls(lovelace) == [url]


async def test_an_install_without_approvals_is_not_asked(lovelace: HomeAssistant) -> None:
    with patch(
        "custom_components.selora_ai.command_policy_options.resolve_command_policy_options",
        return_value=CommandPolicyOptions(approval_required=False),
    ):
        result = await _call(
            lovelace, "add_dashboard_resource", url="https://cdn.example.com/card.js"
        )

    assert result["added"] is True, result


@pytest.mark.parametrize(
    "url",
    [
        "http://cdn.example.com/card.js",
        "javascript:alert(1)",
        "data:text/javascript,alert(1)",
        "//cdn.example.com/card.js",
        "/local/my card.js",
        "",
        # Each of these a browser resolves somewhere else than it reads: a
        # backslash is a slash (this loads from evil.example), %2e a dot.
        "/\\evil.example/card.js",
        "https:/\\evil.example/card.js",
        "/local/%2e%2e/card.js",
        "/local/../hacsfiles/card.js",
        "/local/./card.js",
        "/local//card.js",
        # urlparse raises on these; refused, not "Tool execution failed".
        "https://[",
        "https://[foo]/card.js",
    ],
)
async def test_unsafe_urls_are_refused_even_when_confirmed(
    lovelace: HomeAssistant, url: str
) -> None:
    result = await _call(lovelace, "add_dashboard_resource", url=url, confirmed=True)

    assert "error" in result
    assert await _urls(lovelace) == []


async def test_a_duplicate_is_refused_whatever_its_cache_buster(lovelace: HomeAssistant) -> None:
    """Home Assistant does not deduplicate, and a module loaded twice throws."""
    await _call(lovelace, "add_dashboard_resource", url=f"{BUTTON_CARD}?hacstag=1")

    result = await _call(lovelace, "add_dashboard_resource", url=f"{BUTTON_CARD}?hacstag=2")

    assert "already registered" in result["error"]
    assert len(await _urls(lovelace)) == 1


async def test_recipe_resources_are_left_to_recipes(lovelace: HomeAssistant) -> None:
    recipe_url = "/selora_ai_resources/toothbrush-card-v1.js"
    added = await _call(lovelace, "add_dashboard_resource", url=recipe_url)
    assert "managed by installing or removing the recipe" in added["error"]
    # Not through a path that resolves into it either.
    sneaked = await _call(
        lovelace, "add_dashboard_resource", url="/local/../selora_ai_resources/x.js"
    )
    assert "error" in sneaked

    from custom_components.selora_ai.recipes.resources import _lovelace_resources

    await _lovelace_resources(lovelace).async_create_item({"res_type": "module", "url": recipe_url})
    listed = await _call(lovelace, "list_dashboard_resources")
    assert listed["resources"][0]["managed_by_recipe"] is True

    removed = await _call(lovelace, "remove_dashboard_resource", resource=recipe_url)
    assert "belongs to an installed recipe" in removed["error"]
    assert await _urls(lovelace) == [recipe_url]


async def test_a_resource_is_removed_by_id_or_url(lovelace: HomeAssistant) -> None:
    first = await _call(lovelace, "add_dashboard_resource", url=BUTTON_CARD)
    await _call(lovelace, "add_dashboard_resource", url="/local/theme.css", type="css")

    by_id = await _call(lovelace, "remove_dashboard_resource", resource=first["id"])
    by_url = await _call(lovelace, "remove_dashboard_resource", resource="/local/theme.css")

    assert by_id["removed"] is True
    assert by_url["removed"] is True
    assert await _urls(lovelace) == []


async def test_yaml_resources_are_refused_with_where_to_edit(hass: HomeAssistant) -> None:
    assert await async_setup_component(
        hass, "lovelace", {"lovelace": {"mode": "yaml", "resources": []}}
    )
    await hass.async_block_till_done()

    result = await _call(hass, "add_dashboard_resource", url=BUTTON_CARD)
    listed = await _call(hass, "list_dashboard_resources")

    assert "defined in YAML" in result["error"]
    assert listed["editable"] is False


def test_the_writes_are_admin_and_the_list_is_not() -> None:
    assert "selora_add_dashboard_resource" in mcp_server._ADMIN_TOOLS
    assert "selora_remove_dashboard_resource" in mcp_server._ADMIN_TOOLS
    assert "selora_list_dashboard_resources" in mcp_server._READ_ONLY_TOOLS
