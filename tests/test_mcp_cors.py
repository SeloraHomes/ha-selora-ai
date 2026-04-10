"""Tests for MCP server CORS support."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp import web
from custom_components.selora_ai.mcp_server import (
    _MCP_CORS_HEADERS,
    _cors_headers,
    _cors_preflight,
    _add_cors,
)


class TestCorsHeaders:
    """Tests for _cors_headers helper."""

    def test_reflects_origin(self) -> None:
        hdrs = _cors_headers("http://localhost:6274")
        assert hdrs["Access-Control-Allow-Origin"] == "http://localhost:6274"

    def test_includes_mcp_protocol_version(self) -> None:
        hdrs = _cors_headers("http://localhost:6274")
        assert "Mcp-Protocol-Version" in hdrs["Access-Control-Allow-Headers"]

    def test_includes_standard_headers(self) -> None:
        hdrs = _cors_headers("*")
        for name in ("Authorization", "Content-Type", "Accept"):
            assert name in hdrs["Access-Control-Allow-Headers"]

    def test_allows_post(self) -> None:
        hdrs = _cors_headers("*")
        assert "POST" in hdrs["Access-Control-Allow-Methods"]


class TestPreflightHandler:
    """Tests for the OPTIONS preflight handler."""

    @pytest.mark.asyncio
    async def test_returns_204(self) -> None:
        request = MagicMock(spec=web.Request)
        request.headers = {"Origin": "http://localhost:6274"}
        resp = await _cors_preflight(request)
        assert resp.status == 204

    @pytest.mark.asyncio
    async def test_includes_cors_headers(self) -> None:
        request = MagicMock(spec=web.Request)
        request.headers = {"Origin": "http://localhost:6274"}
        resp = await _cors_preflight(request)
        assert resp.headers["Access-Control-Allow-Origin"] == "http://localhost:6274"
        assert "Mcp-Protocol-Version" in resp.headers["Access-Control-Allow-Headers"]

    @pytest.mark.asyncio
    async def test_wildcard_without_origin(self) -> None:
        request = MagicMock(spec=web.Request)
        request.headers = {}
        resp = await _cors_preflight(request)
        assert resp.headers["Access-Control-Allow-Origin"] == "*"


class TestAddCors:
    """Tests for _add_cors helper."""

    def test_preserves_status(self) -> None:
        req = MagicMock(spec=web.Request)
        req.headers = {"Origin": "https://inspector.example.com"}
        inner = web.Response(status=401, text="Unauthorized")
        result = _add_cors(req, inner)
        assert result.status == 401
        assert result.headers["Access-Control-Allow-Origin"] == "https://inspector.example.com"
