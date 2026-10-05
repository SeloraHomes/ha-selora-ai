"""Tests for what the MCP server says about itself on ``initialize``."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.mcp_server import definitions as mcp_definitions
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server import protocol as mcp_protocol
from custom_components.selora_ai.selora_auth import SeloraAuthContext


def _hash_of(definitions: list[mcp_protocol.MCPTool]) -> str:
    mcp_dispatch._tool_set_hash.cache_clear()
    try:
        with patch.object(mcp_dispatch, "_TOOL_DEFINITIONS", definitions):
            return mcp_dispatch._tool_set_hash()
    finally:
        mcp_dispatch._tool_set_hash.cache_clear()


async def test_the_version_is_the_release_plus_the_tool_set(hass: HomeAssistant) -> None:
    ctx = SeloraAuthContext("user", None, True, "selora_jwt")

    result = await mcp_dispatch._jsonrpc_dispatch(hass, "initialize", None, ctx)

    release = (await async_get_integration(hass, DOMAIN)).version
    assert result["serverInfo"]["version"] == f"{release}+{mcp_dispatch._tool_set_hash()}"


def test_touching_any_tool_changes_the_version() -> None:
    """A client may keep its tool list while the version is unchanged, so
    adding, removing, rewording or re-schematising a tool must move it."""
    tools = list(mcp_definitions._TOOL_DEFINITIONS)
    first = tools[0]
    baseline = _hash_of(tools)

    assert _hash_of(tools[1:]) != baseline
    assert _hash_of([replace(first, description=first.description + "!"), *tools[1:]]) != baseline
    assert _hash_of([replace(first, inputSchema={"type": "object"}), *tools[1:]]) != baseline


def test_the_order_of_definitions_does_not_change_the_version() -> None:
    tools = list(mcp_definitions._TOOL_DEFINITIONS)

    assert _hash_of(list(reversed(tools))) == _hash_of(tools)
