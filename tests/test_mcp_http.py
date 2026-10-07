"""How long one MCP request may run.

Every request was cut at 60 s, while a HACS download waits up to 180 s and a
YAML edit runs Home Assistant's config check before and after its write. Cut
off, the client was told "timed out" while the download went on (a retry
started a second one), or a YAML edit lost its rollback.
"""

from __future__ import annotations

import pytest

from custom_components.selora_ai.mcp_server.names import (
    TOOL_GET_ENTITY_STATE,
    TOOL_HACS_ADD_REPOSITORY,
    TOOL_HACS_INSTALL,
    TOOL_SET_CONFIG_YAML,
)
from custom_components.selora_ai.mcp_server.protocol import _TIMEOUT_SECS, request_timeout


@pytest.mark.parametrize(
    ("tool", "outlasts"),
    [(TOOL_HACS_INSTALL, 180), (TOOL_HACS_ADD_REPOSITORY, 120), (TOOL_SET_CONFIG_YAML, 120)],
)
def test_long_tools_outlast_their_own_waits(tool: str, outlasts: float) -> None:
    assert request_timeout("tools/call", {"name": tool}) > outlasts


def test_everything_else_keeps_the_default() -> None:
    assert request_timeout("tools/call", {"name": TOOL_GET_ENTITY_STATE}) == _TIMEOUT_SECS
    assert request_timeout("tools/list", None) == _TIMEOUT_SECS
    assert request_timeout("tools/call", ["not", "an", "object"]) == _TIMEOUT_SECS
