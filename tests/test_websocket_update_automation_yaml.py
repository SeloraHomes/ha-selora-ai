"""Tests for the update_automation_yaml websocket handler's preservation default.

The endpoint is an authoritative YAML update by default (its historical contract):
a submitted `initial_state` is honored. Refinement callers opt IN to preservation by
sending `preserve_enabled_state: true`. Defaulting to authoritative keeps older/cached
panel clients that change `initial_state` working across an integration upgrade.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.selora_ai.websocket.automations import (
    _handle_websocket_apply_automation_yaml,
    _handle_websocket_update_automation_yaml,
)

# The registered handlers are wrapped by @async_response (a sync callback that
# schedules the coroutine); __wrapped__ is the underlying awaitable we can drive.
_handler = _handle_websocket_update_automation_yaml.__wrapped__
_apply_handler = _handle_websocket_apply_automation_yaml.__wrapped__


async def _invoke(hass: Any, msg: dict[str, Any], handler: Any = _handler) -> AsyncMock:
    """Call the handler with a stub update fn; return the patched AsyncMock."""
    connection = MagicMock()
    update = AsyncMock(return_value=True)
    with (
        patch(
            "custom_components.selora_ai.websocket.automations._require_admin",
            return_value=True,
        ),
        patch(
            "custom_components.selora_ai.automation_utils.async_update_automation",
            update,
        ),
    ):
        await handler(hass, connection, msg)
    return update


@pytest.mark.asyncio
async def test_defaults_to_authoritative_when_flag_omitted(hass: MagicMock) -> None:
    """Omitting the flag → preserve_enabled_state=False (honor submitted value)."""
    update = await _invoke(
        hass,
        {
            "id": 1,
            "type": "selora_ai/update_automation_yaml",
            "automation_id": "selora_ai_x",
            "yaml_text": "alias: X\ninitial_state: false\n",
        },
    )
    update.assert_awaited_once()
    assert update.await_args.kwargs["preserve_enabled_state"] is False


@pytest.mark.asyncio
async def test_honors_explicit_preservation_request(hass: MagicMock) -> None:
    """A refinement caller can opt in with preserve_enabled_state=True."""
    update = await _invoke(
        hass,
        {
            "id": 1,
            "type": "selora_ai/update_automation_yaml",
            "automation_id": "selora_ai_x",
            "yaml_text": "alias: X\ninitial_state: false\n",
            "preserve_enabled_state": True,
        },
    )
    update.assert_awaited_once()
    assert update.await_args.kwargs["preserve_enabled_state"] is True


_VALID_YAML = (
    "alias: X\n"
    "initial_state: false\n"
    "trigger:\n  - platform: sun\n    event: sunset\n"
    "action:\n  - action: light.turn_on\n"
)


@pytest.mark.asyncio
async def test_apply_update_defaults_to_authoritative(hass: MagicMock) -> None:
    """apply_automation_yaml with an automation_id honors the submitted value by
    default (general create-or-update contract)."""
    update = await _invoke(
        hass,
        {
            "id": 1,
            "type": "selora_ai/apply_automation_yaml",
            "automation_id": "selora_ai_x",
            "yaml_text": _VALID_YAML,
        },
        handler=_apply_handler,
    )
    update.assert_awaited_once()
    assert update.await_args.kwargs["preserve_enabled_state"] is False


@pytest.mark.asyncio
async def test_apply_update_honors_explicit_preservation(hass: MagicMock) -> None:
    """A refinement caller can opt in on apply_automation_yaml too."""
    update = await _invoke(
        hass,
        {
            "id": 1,
            "type": "selora_ai/apply_automation_yaml",
            "automation_id": "selora_ai_x",
            "yaml_text": _VALID_YAML,
            "preserve_enabled_state": True,
        },
        handler=_apply_handler,
    )
    update.assert_awaited_once()
    assert update.await_args.kwargs["preserve_enabled_state"] is True
