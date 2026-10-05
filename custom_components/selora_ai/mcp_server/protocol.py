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


_MCP_URL = "/api/selora_ai/mcp"
_PROTECTED_RESOURCE_URL = "/.well-known/oauth-protected-resource/api/selora_ai/mcp"
_OAUTH_AS_METADATA_URL = "/.well-known/oauth-authorization-server/api/selora_ai/mcp"
_OAUTH_TOKEN_PROXY_URL = "/api/selora_ai/oauth/token"
_TIMEOUT_SECS = 60
_CONTENT_TYPE_JSON = "application/json"
