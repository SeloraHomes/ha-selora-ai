"""Tests for the automation cap check in DataCollector._collect_analyze_log."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.selora_ai.collector import DataCollector


def _make_collector(hass) -> DataCollector:
    """Create a DataCollector with a mock LLM client."""
    llm = MagicMock()
    llm.analyze_home_data = AsyncMock(return_value=[])
    llm._max_suggestions = 3
    return DataCollector(hass, llm)


@pytest.mark.asyncio
async def test_cap_reached_skips_llm(hass) -> None:
    """When the automation cap is reached, the LLM cycle is skipped."""
    collector = _make_collector(hass)

    with (
        patch(
            "custom_components.selora_ai.collector.get_selora_automation_cap",
            return_value=10,
        ),
        patch(
            "custom_components.selora_ai.collector.count_selora_automations",
            return_value=10,
        ),
        patch(
            "custom_components.selora_ai.collector.find_stale_automations",
            return_value=[],
        ),
    ):
        await collector._collect_analyze_log()

    # LLM should NOT have been called
    collector._llm.analyze_home_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_cap_reached_no_stale_skips_notification(hass) -> None:
    """When cap is reached but no stale automations, no notification is sent."""
    collector = _make_collector(hass)

    notification_sent = False

    async def _handler(call):
        nonlocal notification_sent
        notification_sent = True

    hass.services.async_register("persistent_notification", "create", _handler)

    with (
        patch(
            "custom_components.selora_ai.collector.get_selora_automation_cap",
            return_value=5,
        ),
        patch(
            "custom_components.selora_ai.collector.count_selora_automations",
            return_value=5,
        ),
        patch(
            "custom_components.selora_ai.collector.find_stale_automations",
            return_value=[],
        ),
    ):
        await collector._collect_analyze_log()

    assert not notification_sent


@pytest.mark.asyncio
async def test_cap_reached_with_stale_sends_notification(hass) -> None:
    """When cap is reached and stale automations exist, a notification is sent."""
    collector = _make_collector(hass)
    stale = [
        {
            "automation_id": "selora_ai_aaa",
            "entity_id": "automation.a",
            "alias": "Stale one",
            "last_triggered": None,
        }
    ]

    notification_data: dict = {}

    async def _handler(call):
        notification_data["title"] = call.data.get("title")
        notification_data["notification_id"] = call.data.get("notification_id")

    hass.services.async_register("persistent_notification", "create", _handler)

    with (
        patch(
            "custom_components.selora_ai.collector.get_selora_automation_cap",
            return_value=5,
        ),
        patch(
            "custom_components.selora_ai.collector.count_selora_automations",
            return_value=5,
        ),
        patch(
            "custom_components.selora_ai.collector.find_stale_automations",
            return_value=stale,
        ),
    ):
        await collector._collect_analyze_log()

    assert notification_data.get("title") == "Selora AI: automation cap reached"
    assert notification_data.get("notification_id") == "selora_ai_stale_automations"
