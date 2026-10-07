"""MCP wire types and the server's fixed URLs and limits."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

# ── Vendored MCP types (no pydantic dependency) ──────────────────────────────


@dataclass
class MCPTool:
    """MCP tool definition (replaces mcp.MCPTool)."""

    name: str
    description: str
    inputSchema: dict[str, Any]


@dataclass
class MCPTextContent:
    """MCP text content block (replaces mcp.MCPTextContent)."""

    type: str = "text"
    text: str = ""


@dataclass
class MCPImageContent:
    """MCP image content block: base64 ``data`` and its ``mimeType``."""

    data: str
    mimeType: str
    type: str = "image"


@dataclass
class ToolImage:
    """A tool result carrying an image, with the JSON fields sent beside it."""

    data: bytes
    mime_type: str
    fields: dict[str, Any]


_MCP_URL = "/api/selora_ai/mcp"
_PROTECTED_RESOURCE_URL = "/.well-known/oauth-protected-resource/api/selora_ai/mcp"
_OAUTH_AS_METADATA_URL = "/.well-known/oauth-authorization-server/api/selora_ai/mcp"
_OAUTH_TOKEN_PROXY_URL = "/api/selora_ai/oauth/token"
_TIMEOUT_SECS = 60


def request_timeout(method: Any, params: Any) -> float:
    """How long one request may run. A tool whose work outlasts the default —
    a HACS download (its own 180 s wait), adding a repository, a YAML edit that
    runs Home Assistant's config check before and after — gets longer: cut off
    at 60 s, the client is told it timed out while the work goes on (a retry
    starts a second download), or a YAML edit is left without its rollback."""
    from .names import TOOL_HACS_ADD_REPOSITORY, TOOL_HACS_INSTALL, TOOL_SET_CONFIG_YAML

    if method == "tools/call" and isinstance(params, dict):
        return {
            TOOL_HACS_INSTALL: 200.0,
            TOOL_HACS_ADD_REPOSITORY: 140.0,
            TOOL_SET_CONFIG_YAML: 180.0,
        }.get(str(params.get("name")), float(_TIMEOUT_SECS))
    return float(_TIMEOUT_SECS)


_CONTENT_TYPE_JSON = "application/json"
