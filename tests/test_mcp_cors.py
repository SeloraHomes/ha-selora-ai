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
    _validate_cors_origin,
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


class TestValidateCorsOrigin:
    """Tests for the CORS origin validation."""

    def test_allows_localhost(self) -> None:
        request = MagicMock(spec=web.Request)
        request.headers = {"Origin": "http://localhost:6274"}
        request.host = "homeassistant.local:8123"
        assert _validate_cors_origin(request) == "http://localhost:6274"

    def test_allows_loopback_ip(self) -> None:
        request = MagicMock(spec=web.Request)
        request.headers = {"Origin": "http://127.0.0.1:8123"}
        request.host = "homeassistant.local:8123"
        assert _validate_cors_origin(request) == "http://127.0.0.1:8123"

    def test_allows_mdns_local(self) -> None:
        request = MagicMock(spec=web.Request)
        request.headers = {"Origin": "http://homeassistant.local:8123"}
        request.host = "homeassistant.local:8123"
        assert _validate_cors_origin(request) == "http://homeassistant.local:8123"

    def test_allows_same_host(self) -> None:
        request = MagicMock(spec=web.Request)
        request.headers = {"Origin": "https://ha.example.com"}
        request.host = "ha.example.com:443"
        assert _validate_cors_origin(request) == "https://ha.example.com"

    def test_allows_private_ip(self) -> None:
        request = MagicMock(spec=web.Request)
        request.headers = {"Origin": "http://192.168.1.50:8123"}
        request.host = "192.168.1.100:8123"
        assert _validate_cors_origin(request) == "http://192.168.1.50:8123"

    def test_rejects_external_origin(self) -> None:
        request = MagicMock(spec=web.Request)
        request.headers = {"Origin": "https://evil.com"}
        request.host = "ha.example.com:8123"
        assert _validate_cors_origin(request) == ""

    def test_empty_without_origin(self) -> None:
        request = MagicMock(spec=web.Request)
        request.headers = {}
        request.host = "ha.example.com:8123"
        assert _validate_cors_origin(request) == ""


class TestPreflightHandler:
    """Tests for the OPTIONS preflight handler."""

    @pytest.mark.asyncio
    async def test_returns_204(self) -> None:
        request = MagicMock(spec=web.Request)
        request.headers = {"Origin": "http://localhost:6274"}
        request.host = "localhost:8123"
        resp = await _cors_preflight(request)
        assert resp.status == 204

    @pytest.mark.asyncio
    async def test_includes_cors_headers_for_trusted_origin(self) -> None:
        request = MagicMock(spec=web.Request)
        request.headers = {"Origin": "http://localhost:6274"}
        request.host = "localhost:8123"
        resp = await _cors_preflight(request)
        assert resp.headers["Access-Control-Allow-Origin"] == "http://localhost:6274"
        assert "Mcp-Protocol-Version" in resp.headers["Access-Control-Allow-Headers"]

    @pytest.mark.asyncio
    async def test_no_cors_headers_without_origin(self) -> None:
        request = MagicMock(spec=web.Request)
        request.headers = {}
        request.host = "localhost:8123"
        resp = await _cors_preflight(request)
        assert "Access-Control-Allow-Origin" not in resp.headers

    @pytest.mark.asyncio
    async def test_no_cors_headers_for_untrusted_origin(self) -> None:
        request = MagicMock(spec=web.Request)
        request.headers = {"Origin": "https://evil.com"}
        request.host = "ha.example.com:8123"
        resp = await _cors_preflight(request)
        assert "Access-Control-Allow-Origin" not in resp.headers


class TestAddCors:
    """Tests for _add_cors helper."""

    def test_preserves_status_for_trusted_origin(self) -> None:
        req = MagicMock(spec=web.Request)
        req.headers = {"Origin": "http://localhost:6274"}
        req.host = "localhost:8123"
        inner = web.Response(status=401, text="Unauthorized")
        result = _add_cors(req, inner)
        assert result.status == 401
        assert result.headers["Access-Control-Allow-Origin"] == "http://localhost:6274"

    def test_no_cors_for_untrusted_origin(self) -> None:
        req = MagicMock(spec=web.Request)
        req.headers = {"Origin": "https://evil.com"}
        req.host = "ha.example.com:8123"
        inner = web.Response(status=200, text="OK")
        result = _add_cors(req, inner)
        assert "Access-Control-Allow-Origin" not in result.headers
