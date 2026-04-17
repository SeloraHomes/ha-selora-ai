"""Tests for the analytics websocket handler and MCP tool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai import _handle_websocket_get_analytics
from custom_components.selora_ai.mcp_server import _tool_home_analytics

# Access the original coroutine behind the @async_response decorator
_analytics_handler = _handle_websocket_get_analytics.__wrapped__


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def mock_connection():
    """Create a mock websocket connection with admin permissions."""
    conn = MagicMock()
    conn.user.is_admin = True
    return conn


@pytest.fixture
def mock_pattern_store():
    """Create a mock PatternStore with analytics methods."""
    store = AsyncMock()
    store.get_usage_windows = AsyncMock(
        return_value=[
            {"hour": 8, "count": 5, "primary_state": "on"},
            {"hour": 20, "count": 3, "primary_state": "off"},
        ]
    )
    store.get_state_transition_counts = AsyncMock(
        return_value=[
            {"from": "off", "to": "on", "count": 10},
            {"from": "on", "to": "off", "count": 8},
        ]
    )
    store.get_analytics_summary = AsyncMock(
        return_value={
            "total_entities_tracked": 5,
            "total_state_changes": 100,
            "most_active": [
                {
                    "entity_id": "light.kitchen",
                    "change_count": 30,
                    "active_days": 7,
                    "domain": "light",
                }
            ],
            "busiest_hour": 8,
            "tracking_since": "2026-03-01T00:00:00+00:00",
        }
    )
    return store


# ── Websocket handler tests ──────────────────────────────────────────


class TestWebsocketGetAnalytics:
    """Tests for _handle_websocket_get_analytics."""

    @pytest.mark.asyncio
    async def test_summary_without_entity_id(
        self, hass: HomeAssistant, mock_connection, mock_pattern_store
    ):
        """Without entity_id, returns the home-wide summary."""
        msg = {"id": 1, "type": "selora_ai/get_analytics"}

        with patch(
            "custom_components.selora_ai._get_pattern_store",
            return_value=mock_pattern_store,
        ):
            await _analytics_handler(hass, mock_connection, msg)

        mock_pattern_store.get_analytics_summary.assert_awaited_once()
        mock_connection.send_result.assert_called_once()
        result = mock_connection.send_result.call_args[0][1]
        assert result["total_entities_tracked"] == 5
        assert result["busiest_hour"] == 8

    @pytest.mark.asyncio
    async def test_per_entity_analytics(
        self, hass: HomeAssistant, mock_connection, mock_pattern_store
    ):
        """With entity_id, returns usage windows and state transitions."""
        msg = {
            "id": 2,
            "type": "selora_ai/get_analytics",
            "entity_id": "light.kitchen",
        }

        with patch(
            "custom_components.selora_ai._get_pattern_store",
            return_value=mock_pattern_store,
        ):
            await _analytics_handler(hass, mock_connection, msg)

        mock_pattern_store.get_usage_windows.assert_awaited_once_with("light.kitchen")
        mock_pattern_store.get_state_transition_counts.assert_awaited_once_with(
            "light.kitchen"
        )
        result = mock_connection.send_result.call_args[0][1]
        assert result["entity_id"] == "light.kitchen"
        assert len(result["usage_windows"]) == 2
        assert len(result["state_transitions"]) == 2

    @pytest.mark.asyncio
    async def test_no_pattern_store(self, hass: HomeAssistant, mock_connection):
        """Returns error when pattern store is not available."""
        msg = {"id": 3, "type": "selora_ai/get_analytics"}

        with patch(
            "custom_components.selora_ai._get_pattern_store",
            return_value=None,
        ):
            await _analytics_handler(hass, mock_connection, msg)

        mock_connection.send_error.assert_called_once_with(
            3, "no_store", "Pattern store not available"
        )

    @pytest.mark.asyncio
    async def test_non_admin_rejected(self, hass: HomeAssistant):
        """Non-admin users are rejected."""
        conn = MagicMock()
        conn.user.is_admin = False
        msg = {"id": 4, "type": "selora_ai/get_analytics"}

        await _analytics_handler(hass, conn, msg)

        conn.send_error.assert_called_once()
        error_code = conn.send_error.call_args[0][1]
        assert error_code in ("unauthorized", "admin_required")


# ── MCP tool tests ───────────────────────────────────────────────────


class TestToolHomeAnalytics:
    """Tests for _tool_home_analytics MCP tool."""

    @pytest.mark.asyncio
    async def test_summary_without_entity_id(
        self, hass: HomeAssistant, mock_pattern_store
    ):
        """Without entity_id, returns the home-wide summary."""
        with patch(
            "custom_components.selora_ai.pattern_store.get_pattern_store",
            return_value=mock_pattern_store,
        ):
            result = await _tool_home_analytics(hass, {})

        mock_pattern_store.get_analytics_summary.assert_awaited_once()
        assert result["total_entities_tracked"] == 5

    @pytest.mark.asyncio
    async def test_per_entity_analytics(
        self, hass: HomeAssistant, mock_pattern_store
    ):
        """With entity_id, returns usage windows and state transitions."""
        with patch(
            "custom_components.selora_ai.pattern_store.get_pattern_store",
            return_value=mock_pattern_store,
        ):
            result = await _tool_home_analytics(
                hass, {"entity_id": "light.kitchen"}
            )

        mock_pattern_store.get_usage_windows.assert_awaited_once_with("light.kitchen")
        mock_pattern_store.get_state_transition_counts.assert_awaited_once_with(
            "light.kitchen"
        )
        assert result["entity_id"] == "light.kitchen"

    @pytest.mark.asyncio
    async def test_empty_entity_id_treated_as_summary(
        self, hass: HomeAssistant, mock_pattern_store
    ):
        """Whitespace-only entity_id falls back to summary."""
        with patch(
            "custom_components.selora_ai.pattern_store.get_pattern_store",
            return_value=mock_pattern_store,
        ):
            result = await _tool_home_analytics(hass, {"entity_id": "  "})

        mock_pattern_store.get_analytics_summary.assert_awaited_once()
        assert "total_entities_tracked" in result

    @pytest.mark.asyncio
    async def test_no_pattern_store(self, hass: HomeAssistant):
        """Returns error dict when pattern store is not available."""
        with patch(
            "custom_components.selora_ai.pattern_store.get_pattern_store",
            return_value=None,
        ):
            result = await _tool_home_analytics(hass, {})

        assert result == {"error": "Pattern store not available"}

    @pytest.mark.asyncio
    async def test_state_strings_are_sanitized(self, hass: HomeAssistant):
        """State strings in usage_windows and state_transitions are sanitized."""
        long_state = "x" * 300
        store = AsyncMock()
        store.get_usage_windows = AsyncMock(
            return_value=[{"hour": 8, "count": 1, "primary_state": long_state}]
        )
        store.get_state_transition_counts = AsyncMock(
            return_value=[{"from": long_state, "to": "on", "count": 1}]
        )

        with patch(
            "custom_components.selora_ai.pattern_store.get_pattern_store",
            return_value=store,
        ):
            result = await _tool_home_analytics(
                hass, {"entity_id": "sensor.test"}
            )

        # primary_state should be truncated (limit=64)
        assert len(result["usage_windows"][0]["primary_state"]) <= 64
        # transition "from" should be truncated
        assert len(result["state_transitions"][0]["from"]) <= 64
