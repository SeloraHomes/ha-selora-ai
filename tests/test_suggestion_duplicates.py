"""Background suggestions must not re-propose an automation the home has.

The model only saw aliases. An installed recipe's automation ("Water Leak
Alert — leak detected") does not name its sensor, so the analysis suggested
"Wet Basement Alert" on the very sensor the recipe already watches, and the
alias-only filter let it through.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai.collector import DataCollector
from custom_components.selora_ai.llm_client.prompts import build_analysis_prompt
from custom_components.selora_ai.types import AutomationSnapshot

_LEAK_SENSOR = "binary_sensor.basement_floor_wet"

_RECIPE: AutomationSnapshot = {
    "entity_id": "automation.water_leak_alert_leak_detected",
    "alias": "Water Leak Alert — leak detected",
    "entities": [_LEAK_SENSOR, "media_player.bedroom"],
}


def _suggestion(trigger: str | None, *targets: str) -> dict[str, Any]:
    triggers = (
        [{"platform": "state", "entity_id": trigger, "to": "on"}]
        if trigger
        else [{"platform": "sun", "event": "sunset"}]
    )
    actions: list[dict[str, Any]] = [
        {"action": "notify.persistent_notification", "data": {"message": "Water!"}}
    ]
    actions += [{"action": "light.turn_on", "target": {"entity_id": t}} for t in targets]
    return {"alias": "Wet Basement Alert", "triggers": triggers, "actions": actions}


def _collector(hass: Any) -> DataCollector:
    collector = DataCollector.__new__(DataCollector)
    collector._hass = hass
    return collector


async def test_snapshot_carries_each_automations_entities(hass: HomeAssistant) -> None:
    """Read through HA, so YAML/package automations count, not just automations.yaml."""
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "alias": "Water Leak Alert — leak detected",
                "triggers": {"trigger": "state", "entity_id": _LEAK_SENSOR, "to": "on"},
                "actions": {
                    "action": "media_player.media_pause",
                    "entity_id": "media_player.bedroom",
                },
            }
        },
    )
    await hass.async_block_till_done()

    [automation] = _collector(hass)._collect_automations()

    assert automation["alias"] == "Water Leak Alert — leak detected"
    assert automation["entities"] == ["binary_sensor.basement_floor_wet", "media_player.bedroom"]


def test_driving_only_what_one_automation_uses_is_covered() -> None:
    suggestion = _suggestion(_LEAK_SENSOR, "media_player.bedroom")
    covering = _collector(MagicMock())._covering_automation(suggestion, [_RECIPE])
    assert covering == "Water Leak Alert — leak detected"


def test_a_suggestion_that_adds_a_device_is_not_covered() -> None:
    suggestion = _suggestion(_LEAK_SENSOR, "light.basement")
    assert _collector(MagicMock())._covering_automation(suggestion, [_RECIPE]) is None


def test_a_notification_on_a_used_sensor_is_not_covered() -> None:
    """Sharing only the trigger says nothing about behaviour: "notify on
    motion" is not "lights on motion". The prompt carries those duplicates."""
    assert (
        _collector(MagicMock())._covering_automation(_suggestion(_LEAK_SENSOR), [_RECIPE]) is None
    )


def test_a_suggestion_without_an_entity_trigger_is_never_covered() -> None:
    """A sunset routine using a device some automation also uses is a new routine."""
    suggestion = _suggestion(None, "media_player.bedroom")
    assert _collector(MagicMock())._covering_automation(suggestion, [_RECIPE]) is None


async def test_filter_drops_the_covered_suggestion(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    hass.states.async_set(_LEAK_SENSOR, "off")
    hass.states.async_set("light.basement", "off")
    collector = _collector(hass)
    collector._pattern_store = None
    snapshot: Any = {"automations": [_RECIPE], "recorder_history": []}

    kept = await collector._filter_and_score_suggestions(
        [
            _suggestion(_LEAK_SENSOR, "media_player.bedroom"),
            _suggestion(_LEAK_SENSOR, "light.basement"),
        ],
        snapshot,
        dynamic_cap=10,
    )

    assert caplog.text.count("already covers it") == 1
    targets = [a["target"]["entity_id"] for s in kept for a in s["actions"] if "target" in a]
    assert "media_player.bedroom" not in targets


def test_prompt_lists_what_each_automation_uses() -> None:
    many: AutomationSnapshot = {
        "alias": "Everything",
        "entities": [f"light.l{i}" for i in range(10)],
    }
    prompt = build_analysis_prompt(
        {"automations": [_RECIPE, many]}, max_suggestions=5, lookback_days=7
    )
    assert (
        "Water Leak Alert — leak detected (uses: binary_sensor.basement_floor_wet, "
        "media_player.bedroom)" in prompt
    )
    assert "light.l7, +2 more)" in prompt


def test_acting_on_its_own_trigger_entity_can_be_covered() -> None:
    """A switch left on too long, then turned off, drives its own trigger."""
    switch = "switch.heater"
    existing: AutomationSnapshot = {"alias": "Heater timeout", "entities": [switch]}
    suggestion = {
        "alias": "Heater auto-off",
        "triggers": [{"platform": "state", "entity_id": switch, "to": "on", "for": "02:00:00"}],
        "actions": [{"action": "switch.turn_off", "target": {"entity_id": switch}}],
    }
    assert _collector(MagicMock())._covering_automation(suggestion, [existing]) == "Heater timeout"


def test_fingerprint_changes_when_an_automation_changes_its_entities() -> None:
    collector = _collector(MagicMock())
    before: Any = {"automations": [_RECIPE]}
    after: Any = {"automations": [{**_RECIPE, "entities": [_LEAK_SENSOR]}]}
    assert collector._snapshot_fingerprint(before) != collector._snapshot_fingerprint(after)
