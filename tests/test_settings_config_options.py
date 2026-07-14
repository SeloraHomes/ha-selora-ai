"""Regression: the Insights (Health) controls are exposed via get_config.

The Health subsystem is default-on. If its toggle isn't returned by the config
websocket, the panel can't render (or persist) it and users can't disable the
background workload — which is exactly what a prior review flagged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.const import CONF_INSIGHTS_ENABLED, CONF_INSIGHTS_INTERVAL, DOMAIN
from custom_components.selora_ai.websocket.linking import _handle_websocket_get_config

_get_config = _handle_websocket_get_config.__wrapped__


@pytest.mark.asyncio
async def test_get_config_exposes_insights_toggle_and_interval(hass: HomeAssistant) -> None:
    """The saved insights_enabled / insights_interval options round-trip to the
    panel, so the Health toggle reflects and controls the real setting."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={CONF_INSIGHTS_ENABLED: False, CONF_INSIGHTS_INTERVAL: 300},
    )
    entry.add_to_hass(hass)
    conn = MagicMock()
    conn.user.is_admin = True

    with (
        patch(
            "custom_components.selora_ai.providers.discover_selora_local_host",
            AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.selora_ai.entity_filter.resolve_label_tagged_items",
            return_value=[],
        ),
    ):
        await _get_config(hass, conn, {"id": 1})

    result = conn.send_result.call_args[0][1]
    assert result["insights_enabled"] is False  # user's disable choice is surfaced
    assert result["insights_interval"] == 300


@pytest.mark.asyncio
async def test_get_config_insights_defaults_on(hass: HomeAssistant) -> None:
    """With no option set, the toggle defaults to on (matching setup)."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    conn = MagicMock()
    conn.user.is_admin = True

    with (
        patch(
            "custom_components.selora_ai.providers.discover_selora_local_host",
            AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.selora_ai.entity_filter.resolve_label_tagged_items",
            return_value=[],
        ),
    ):
        await _get_config(hass, conn, {"id": 1})

    result = conn.send_result.call_args[0][1]
    assert result["insights_enabled"] is True


def test_sanitize_insights_interval_rejects_invalid() -> None:
    """A cleared (null), zero, negative, sub-minimum, or non-numeric interval
    falls back to the default so timer creation can't crash and silently
    disable scanning; valid positive values (incl. numeric strings) pass."""
    from custom_components.selora_ai import _sanitize_insights_interval
    from custom_components.selora_ai.const import DEFAULT_INSIGHTS_INTERVAL as D

    assert _sanitize_insights_interval(300) == 300
    assert _sanitize_insights_interval("120") == 120
    assert _sanitize_insights_interval(60) == 60
    assert _sanitize_insights_interval(None) == D  # cleared field → null
    assert _sanitize_insights_interval(0) == D
    assert _sanitize_insights_interval(-5) == D
    assert _sanitize_insights_interval(30) == D  # below the 60s floor
    assert _sanitize_insights_interval("abc") == D


@pytest.mark.asyncio
async def test_update_config_drops_unknown_option_keys(hass: HomeAssistant) -> None:
    """update_config persists only allowlisted option keys, so a crafted payload
    can't bloat the entry or flip an unlisted (e.g. future safety) option."""
    from unittest.mock import AsyncMock

    from custom_components.selora_ai.websocket.linking import _handle_websocket_update_config

    handler = _handle_websocket_update_config.__wrapped__
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    conn = MagicMock()
    conn.user.is_admin = True

    captured: dict = {}

    def _capture(_entry, **kwargs):
        captured.update(kwargs)

    with (
        patch.object(hass.config_entries, "async_update_entry", side_effect=_capture),
        patch.object(hass.config_entries, "async_reload", AsyncMock()),
    ):
        await handler(
            hass,
            conn,
            {
                "id": 1,
                "config": {
                    "insights_enabled": False,
                    "collector_enabled": True,
                    "evil_key": "x",
                },
            },
        )
        await hass.async_block_till_done()

    opts = captured["options"]
    assert opts.get("insights_enabled") is False  # allowlisted → persisted
    assert opts.get("collector_enabled") is True
    assert "evil_key" not in opts  # unknown → dropped
    conn.send_result.assert_called_once()
