"""Tests for the tool list the custom-token picker is built from.

The settings panel carried its own copy of the MCP tool list, and every tool
added to the server afterwards could not be granted to a custom token. The
list now comes from the server, with the token list.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant

from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import definitions as mcp_definitions
from custom_components.selora_ai.websocket import tokens as tokens_ws

_list_tokens = tokens_ws._handle_websocket_list_mcp_tokens.__wrapped__


def test_every_tool_is_grantable_with_its_admin_flag() -> None:
    catalog = mcp_definitions.mcp_tool_catalog()

    assert [t["name"] for t in catalog] == [t.name for t in mcp_definitions._TOOL_DEFINITIONS]
    for tool in catalog:
        assert tool["admin"] is (tool["name"] in mcp_access._ADMIN_TOOLS), tool["name"]


async def test_the_token_list_carries_the_tools(hass: HomeAssistant) -> None:
    store = MagicMock()
    store.async_list_tokens = AsyncMock(return_value=[])
    hass.data.setdefault(DOMAIN, {})["mcp_token_store"] = store
    connection = MagicMock()
    connection.user.is_admin = True

    await _list_tokens(hass, connection, {"id": 1, "type": "selora_ai/list_mcp_tokens"})

    (_, payload), _ = connection.send_result.call_args
    assert payload["tokens"] == []
    assert payload["tools"] == mcp_definitions.mcp_tool_catalog()
    names = {t["name"] for t in payload["tools"]}
    assert {"selora_create_dashboard", "selora_update_helper"} <= names
