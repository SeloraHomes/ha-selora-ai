"""Selora AI MCP Server — exposes the Selora intelligence layer over MCP.

Endpoint: POST /api/selora_ai/mcp
Protocol: Model Context Protocol, Streamable HTTP (stateless)
Auth:     HA Bearer token, Selora Connect JWT, or a Selora MCP token

Security
────────
  - Write tools need admin (``access``); a custom MCP token is limited to the
    tools it was granted.
  - Every user-controlled string in a response passes through
    ``sanitize_untrusted_text`` (the prompt-injection boundary).
  - Risky writes are previewed or confirmed: deletes and destructive edits run
    at once over MCP but say so in their description, service calls are gated
    by risk (``mcp_service_call``), configuration YAML is two-step
    (``config_yaml``).

Adding a tool touches, in this package: the name constant (``names``), its
access class (``access``), the handler in the domain module, its entry in the
handler table (``dispatch``), and its schema (``definitions`` — derived from
the chat ``ToolDef`` where one exists).
"""

from __future__ import annotations

from .http import register_mcp_server

__all__ = ["register_mcp_server"]
