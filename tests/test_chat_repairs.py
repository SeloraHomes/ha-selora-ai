"""Tests for fixing and ignoring repairs from chat.

A fix is carded: the tool returns a destructive descriptor, the card is built
from the tool log, and confirming replays it through ``_apply_destructive_actions``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import (
    MockModule,
    mock_integration,
    mock_platform,
)
import voluptuous as vol

from custom_components.selora_ai import _apply_destructive_actions
from custom_components.selora_ai.llm_client.command_policy import _pending_destructive_from_log
from custom_components.selora_ai.tool_executor import ToolExecutor


class _AsksForInput(RepairsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
        return self.async_show_form(
            step_id="init", data_schema=vol.Schema({vol.Required("pin"): str})
        )


@pytest.fixture
async def fixable(hass: HomeAssistant) -> None:
    flows = {"stale_token": ConfirmRepairFlow, "needs_pin": _AsksForInput}

    async def _create(_hass: HomeAssistant, issue_id: str, _data: Any) -> Any:
        return flows[issue_id]()

    mock_integration(hass, MockModule("fixer"))
    mock_platform(hass, "fixer.repairs", Mock(async_create_fix_flow=AsyncMock(side_effect=_create)))
    assert await async_setup_component(hass, "fixer", {})
    assert await async_setup_component(hass, "repairs", {})
    for issue_id in flows:
        ir.async_create_issue(
            hass, "fixer", issue_id, is_fixable=True, severity="error", translation_key=issue_id
        )


def _executor(hass: HomeAssistant) -> ToolExecutor:
    return ToolExecutor(hass, MagicMock(), is_admin=True)


async def test_a_fix_is_carded_then_run_on_confirm(hass: HomeAssistant, fixable: None) -> None:
    executor = _executor(hass)
    result = await executor.execute("fix_repair", {"domain": "fixer", "issue_id": "stale_token"})

    assert result["requires_approval"] is True
    (action,) = _pending_destructive_from_log(executor.call_log)
    assert action["kind"] == "repair"
    assert ir.async_get(hass).async_get_issue("fixer", "stale_token") is not None

    applied, errors = await _apply_destructive_actions(
        hass, [{**action, "fingerprint": result["destructive"]["fingerprint"]}]
    )

    assert errors == []
    assert applied
    assert ir.async_get(hass).async_get_issue("fixer", "stale_token") is None


async def test_a_repair_raised_again_is_not_fixed_blind(hass: HomeAssistant, fixable: None) -> None:
    executor = _executor(hass)
    result = await executor.execute("fix_repair", {"domain": "fixer", "issue_id": "stale_token"})
    (action,) = _pending_destructive_from_log(executor.call_log)
    ir.async_delete_issue(hass, "fixer", "stale_token")
    ir.async_create_issue(
        hass, "fixer", "stale_token", is_fixable=True, severity="error", translation_key="x"
    )

    _applied, errors = await _apply_destructive_actions(
        hass, [{**action, "fingerprint": result["destructive"]["fingerprint"]}]
    )

    assert "raised again" in errors[0]


async def test_a_fix_that_asks_for_input_is_sent_to_settings(
    hass: HomeAssistant, fixable: None
) -> None:
    result = await _executor(hass).execute(
        "fix_repair", {"domain": "fixer", "issue_id": "needs_pin"}
    )

    assert "Settings → Repairs" in result["error"]


async def test_chat_ignores_a_repair_directly(hass: HomeAssistant, fixable: None) -> None:
    result = await _executor(hass).execute(
        "ignore_repair", {"domain": "fixer", "issue_id": "stale_token"}
    )

    assert result["status"] == "ignored"
    assert ir.async_get(hass).async_get_issue("fixer", "stale_token").dismissed_version


async def test_a_repair_updated_in_place_is_not_fixed_blind(
    hass: HomeAssistant, fixable: None
) -> None:
    """Same id, same creation time, new data: not the repair the card showed."""
    executor = _executor(hass)
    result = await executor.execute("fix_repair", {"domain": "fixer", "issue_id": "stale_token"})
    (action,) = _pending_destructive_from_log(executor.call_log)
    ir.async_create_issue(
        hass,
        "fixer",
        "stale_token",
        is_fixable=True,
        severity="error",
        translation_key="stale_token",
        data={"account": "someone-else"},
    )

    _applied, errors = await _apply_destructive_actions(
        hass, [{**action, "fingerprint": result["destructive"]["fingerprint"]}]
    )

    assert "raised again" in errors[0]
    assert ir.async_get(hass).async_get_issue("fixer", "stale_token") is not None


async def test_an_empty_form_that_leads_on_is_not_carded(hass: HomeAssistant) -> None:
    """An empty first form is not proof of one step — only HA's stock
    confirmation flow is."""

    class _ConfirmThenMore(RepairsFlow):
        async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
            if user_input is None:
                return self.async_show_form(step_id="init", data_schema=vol.Schema({}))
            return self.async_external_step(step_id="wait", url="https://example.com")

        async def async_step_wait(self, user_input: dict[str, Any] | None = None) -> Any:
            return self.async_external_step_done(next_step_id="init")

    mock_integration(hass, MockModule("migrator"))
    mock_platform(
        hass,
        "migrator.repairs",
        Mock(async_create_fix_flow=AsyncMock(return_value=_ConfirmThenMore())),
    )
    assert await async_setup_component(hass, "migrator", {})
    assert await async_setup_component(hass, "repairs", {})
    ir.async_create_issue(
        hass, "migrator", "legacy", is_fixable=True, severity="warning", translation_key="legacy"
    )

    result = await _executor(hass).execute(
        "fix_repair", {"domain": "migrator", "issue_id": "legacy"}
    )

    assert "Settings → Repairs" in result["error"]


async def test_previewing_a_fix_runs_none_of_it(hass: HomeAssistant) -> None:
    """Building the card must not start the integration's flow: its first
    step can already do the work."""
    ran: list[str] = []

    class _DoesWorkAtOnce(RepairsFlow):
        async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
            ran.append("init")
            return self.async_create_entry(data={})

    mock_integration(hass, MockModule("eager"))
    mock_platform(
        hass, "eager.repairs", Mock(async_create_fix_flow=AsyncMock(return_value=_DoesWorkAtOnce()))
    )
    assert await async_setup_component(hass, "eager", {})
    assert await async_setup_component(hass, "repairs", {})
    ir.async_create_issue(
        hass, "eager", "now", is_fixable=True, severity="warning", translation_key="now"
    )

    result = await _executor(hass).execute("fix_repair", {"domain": "eager", "issue_id": "now"})

    assert "Settings → Repairs" in result["error"]
    assert ran == []
    assert ir.async_get(hass).async_get_issue("eager", "now") is not None
