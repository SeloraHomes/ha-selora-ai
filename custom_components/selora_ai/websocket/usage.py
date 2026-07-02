"""Selora AI websocket handlers: usage.

Extracted from __init__.py. Handlers reach shared integration
helpers via ``from .. import`` (safe: this module is imported
lazily at registration time, after the package has loaded).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import decorators
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .. import (
    _USAGE_RANGE_KEYS,
    _get_pattern_store,
    _require_admin,
)
from ..const import (
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_state_history_summary",
    }
)
async def _handle_websocket_get_state_history_summary(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return aggregated state history stats for the automations tab."""
    if not _require_admin(connection, msg):
        return

    pattern_store = _get_pattern_store(hass)
    if not pattern_store:
        connection.send_error(msg["id"], "no_store", "Pattern store not available")
        return
    summary = await pattern_store.get_history_summary()
    connection.send_result(msg["id"], summary)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_analytics",
        vol.Optional("entity_id"): cv.string,
    }
)
async def _handle_websocket_get_analytics(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return device analytics — summary or per-entity details."""
    if not _require_admin(connection, msg):
        return

    pattern_store = _get_pattern_store(hass)
    if not pattern_store:
        connection.send_error(msg["id"], "no_store", "Pattern store not available")
        return

    entity_id = msg.get("entity_id")
    if entity_id:
        usage_windows = await pattern_store.get_usage_windows(entity_id)
        state_transitions = await pattern_store.get_state_transition_counts(entity_id)
        connection.send_result(
            msg["id"],
            {
                "entity_id": entity_id,
                "usage_windows": usage_windows,
                "state_transitions": state_transitions,
            },
        )
    else:
        summary = await pattern_store.get_analytics_summary()
        connection.send_result(msg["id"], summary)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/usage/recent",
    }
)
async def _handle_websocket_get_recent_usage(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the recent LLM usage events from the in-memory ring buffer.

    Powers the panel's "Where tokens go" breakdown. The buffer is
    ephemeral (resets on HA restart) and capped at LLM_USAGE_BUFFER_SIZE.
    """
    if not _require_admin(connection, msg):
        return

    buffer = hass.data.get(DOMAIN, {}).get("llm_usage_events")
    events = list(buffer) if buffer else []
    connection.send_result(msg["id"], {"events": events})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/usage/pricing_defaults",
    }
)
async def _handle_websocket_get_pricing_defaults(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the built-in pricing table (USD per million tokens).

    Lets the panel show defaults next to the user override fields so the
    user can see what the integration would otherwise charge against.
    """
    if not _require_admin(connection, msg):
        return

    from ..const import LLM_PRICING_USD_PER_MTOK  # noqa: PLC0415

    serialised = {
        provider: {model: list(price) for model, price in models.items()}
        for provider, models in LLM_PRICING_USD_PER_MTOK.items()
    }
    connection.send_result(msg["id"], {"pricing": serialised})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/usage/breakdown",
        vol.Optional("range", default="30d"): vol.In(_USAGE_RANGE_KEYS),
    }
)
async def _handle_websocket_get_usage_breakdown(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return per-(provider, model) usage totals from the persistent store.

    Powers the panel's "By provider" breakdown and the per-model filter so
    users can see which backend their tokens are going to even after HA
    restarts (the in-memory ring buffer can't help with that).
    """
    if not _require_admin(connection, msg):
        return

    from ..usage_store import get_usage_store  # noqa: PLC0415

    store = get_usage_store(hass)
    breakdown = await store.get_breakdown(msg.get("range", "30d"))
    connection.send_result(
        msg["id"],
        {
            "range": msg.get("range", "30d"),
            "breakdown": breakdown,
        },
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/usage/totals",
        vol.Optional("range", default="30d"): vol.In(_USAGE_RANGE_KEYS),
        vol.Optional("provider"): str,
        vol.Optional("model"): str,
    }
)
async def _handle_websocket_get_usage_totals(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return flat usage sums for one range, optionally filtered.

    Backs the panel's Totals tiles and the three "By period" rows when a
    provider/model filter is active (statistics-based totals can't filter).
    """
    if not _require_admin(connection, msg):
        return

    from ..usage_store import get_usage_store  # noqa: PLC0415

    store = get_usage_store(hass)
    provider = msg.get("provider") or None
    # ``model`` is preserved as-is so an explicit "" filters to the
    # no-model bucket (e.g. selora_local, which has no user-visible model
    # id). ``None`` from a missing field still means "any model".
    model = msg.get("model")
    totals = await store.get_totals(msg.get("range", "30d"), provider=provider, model=model)
    periods = await store.get_periods(provider=provider, model=model)
    connection.send_result(
        msg["id"],
        {
            "range": msg.get("range", "30d"),
            "provider": provider,
            "model": model,
            "totals": totals,
            "periods": periods,
        },
    )


def async_register(hass: HomeAssistant) -> None:
    """Register the usage websocket commands."""
    from homeassistant.components import websocket_api

    websocket_api.async_register_command(hass, _handle_websocket_get_state_history_summary)
    websocket_api.async_register_command(hass, _handle_websocket_get_analytics)
    websocket_api.async_register_command(hass, _handle_websocket_get_recent_usage)
    websocket_api.async_register_command(hass, _handle_websocket_get_pricing_defaults)
    websocket_api.async_register_command(hass, _handle_websocket_get_usage_breakdown)
    websocket_api.async_register_command(hass, _handle_websocket_get_usage_totals)
