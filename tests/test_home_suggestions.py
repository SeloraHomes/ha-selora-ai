"""Tests for suggestions drawn from how the home is organised.

Each test builds a small home: registry entities created in order (the
creation date is what separates a newcomer from one left out on purpose),
real group helpers through HA's own flow, and an ``automations.yaml``.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import yaml

from custom_components.selora_ai import group_manager as gm
from custom_components.selora_ai.home_suggestions import (
    HomeSuggestionNotifier,
    async_dismiss_home_suggestion,
    async_home_suggestions,
)


@pytest.fixture
async def home(hass: HomeAssistant) -> HomeAssistant:
    assert await async_setup_component(hass, "group", {})
    await hass.async_block_till_done()
    return hass


def _add(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    entity_id: str,
    device_class: str | None = None,
    area_id: str | None = None,
) -> str:
    """Register an entity a minute after the previous one, with a state."""
    freezer.tick(timedelta(minutes=1))
    domain, object_id = entity_id.split(".")
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        domain,
        "test",
        object_id,
        suggested_object_id=object_id,
        original_device_class=device_class,
    )
    assert entry.entity_id == entity_id
    if area_id:
        registry.async_update_entity(entity_id, area_id=area_id)
    hass.states.async_set(entity_id, "off", {"friendly_name": object_id.replace("_", " ")})
    return entity_id


async def _group(hass: HomeAssistant, name: str, members: list[str]) -> str:
    result = await gm.async_create_group(hass, name=name, entities=members)
    await hass.async_block_till_done()
    assert "error" not in result, result
    return result["entity_id"]


def _write_automations(hass: HomeAssistant, automations: list[dict[str, Any]]) -> None:
    Path(hass.config.config_dir, "automations.yaml").write_text(yaml.safe_dump(automations))


def _leak_alert(*sensors: str) -> dict[str, Any]:
    return {
        "id": "leak_alert",
        "alias": "Water Leak Alert",
        "triggers": [{"trigger": "state", "entity_id": s, "to": "on"} for s in sensors],
        "actions": [{"action": "notify.notify", "data": {"message": "Leak"}}],
    }


def _of_type(suggestions: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [s for s in suggestions if s["type"] == kind]


def _added(suggestions: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {
        (s["entities"][0]["entity_id"], s["collection"]["ref"])
        for s in _of_type(suggestions, "add_to_collection")
    }


async def test_a_new_leak_sensor_belongs_in_the_group_of_every_leak_sensor(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    a = _add(home, freezer, "binary_sensor.leak_a", "moisture")
    b = _add(home, freezer, "binary_sensor.leak_b", "moisture")
    group = await _group(home, "Water Leak Sensors", [a, b])
    new = _add(home, freezer, "binary_sensor.leak_new", "moisture")

    [suggestion] = await async_home_suggestions(home)

    assert _added([suggestion]) == {(new, group)}
    assert suggestion["area"] is None


async def test_one_left_out_on_purpose_is_never_suggested(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A leak sensor older than the group's members was a choice, not a gap."""
    _add(home, freezer, "binary_sensor.leak_garage", "moisture")
    a = _add(home, freezer, "binary_sensor.leak_a", "moisture")
    b = _add(home, freezer, "binary_sensor.leak_b", "moisture")
    await _group(home, "Water Leak Sensors", [a, b])
    _add(home, freezer, "binary_sensor.leak_new", "moisture")

    assert _added(await async_home_suggestions(home)) == set()


async def test_one_left_out_when_the_group_was_made_is_never_suggested(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Newer than every member, but it existed when the user picked them."""
    a = _add(home, freezer, "binary_sensor.leak_a", "moisture")
    b = _add(home, freezer, "binary_sensor.leak_b", "moisture")
    _add(home, freezer, "binary_sensor.leak_garage", "moisture")
    freezer.tick(timedelta(minutes=1))
    await _group(home, "Water Leak Sensors", [a, b])

    assert _added(await async_home_suggestions(home)) == set()


async def test_another_kind_is_not_a_newcomer(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    a = _add(home, freezer, "binary_sensor.leak_a", "moisture")
    b = _add(home, freezer, "binary_sensor.leak_b", "moisture")
    await _group(home, "Water Leak Sensors", [a, b])
    _add(home, freezer, "binary_sensor.front_door", "door")

    assert _added(await async_home_suggestions(home)) == set()


async def test_an_area_collection_takes_a_new_entity_in_that_area(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    kitchen = ar.async_get(home).async_create("Kitchen").id
    _add(home, freezer, "light.hallway")
    a = _add(home, freezer, "light.kitchen_a", area_id=kitchen)
    b = _add(home, freezer, "light.kitchen_b", area_id=kitchen)
    group = await _group(home, "Kitchen Lights", [a, b])
    new = _add(home, freezer, "light.kitchen_new", area_id=kitchen)
    _add(home, freezer, "light.bedroom")

    [suggestion] = await async_home_suggestions(home)

    assert _added([suggestion]) == {(new, group)}
    # The panel explains it: these members, every one of the kind in Kitchen.
    assert [m["entity_id"] for m in suggestion["members"]] == [a, b]
    assert suggestion["area"] == "Kitchen"


async def test_an_automation_listing_every_one_of_a_kind_is_a_collection(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The same rule as a group: no group needed, and nothing domain-specific."""
    a = _add(home, freezer, "person.alice")
    b = _add(home, freezer, "person.bob")
    _write_automations(
        home,
        [
            {
                "id": "everyone_left",
                "alias": "Everyone left",
                "triggers": [{"trigger": "state", "entity_id": [a, b], "to": "not_home"}],
                "conditions": [
                    {"condition": "state", "entity_id": a, "state": "not_home"},
                    {"condition": "state", "entity_id": b, "state": "not_home"},
                ],
                "actions": [],
            }
        ],
    )
    new = _add(home, freezer, "person.carol")

    [suggestion] = _of_type(await async_home_suggestions(home), "add_to_collection")

    assert suggestion["entities"][0]["entity_id"] == new
    assert suggestion["collection"]["source"] == "automation"
    assert suggestion["collection"]["name"] == "Everyone left"


async def test_one_left_out_when_the_automation_was_written_is_never_suggested(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Paired before the rule was written, so not picked on purpose."""
    a = _add(home, freezer, "person.alice")
    b = _add(home, freezer, "person.bob")
    _add(home, freezer, "person.carol")
    _write_automations(
        home,
        [
            {
                "id": "both_left",
                "alias": "Both left",
                "triggers": [{"trigger": "state", "entity_id": [a, b], "to": "not_home"}],
                "actions": [],
            }
        ],
    )
    # The automation's entity registers when the rule is loaded, after carol.
    freezer.tick(timedelta(minutes=1))
    er.async_get(home).async_get_or_create(
        "automation", "automation", "both_left", suggested_object_id="both_left"
    )
    home.states.async_set("automation.both_left", "on", {"id": "both_left"})

    assert _added(await async_home_suggestions(home)) == set()


async def test_a_switch_without_a_device_class_is_no_kind(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Two light switches turned off at sunrise say nothing about the next
    switch paired, which could be a bridge's away mode."""
    a = _add(home, freezer, "switch.porch_light")
    b = _add(home, freezer, "switch.garden_light")
    _write_automations(
        home,
        [
            {
                "id": "sunrise",
                "alias": "Turn off exterior lights at sunrise",
                "triggers": [{"trigger": "sun", "event": "sunrise"}],
                "actions": [{"action": "switch.turn_off", "target": {"entity_id": [a, b]}}],
            }
        ],
    )
    _add(home, freezer, "switch.smart_bridge_smart_away")

    assert _added(await async_home_suggestions(home)) == set()


async def test_three_trigger_entities_of_one_kind_suggest_a_group(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    sensors = [_add(home, freezer, f"binary_sensor.leak_{n}", "moisture") for n in "abc"]
    _write_automations(home, [_leak_alert(*sensors)])

    [suggestion] = _of_type(await async_home_suggestions(home), "group_entities")

    assert [e["entity_id"] for e in suggestion["entities"]] == sensors
    assert suggestion["collection"]["name"] == "Water Leak Alert"


async def test_a_group_already_made_from_them_is_not_suggested_again(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Acted on once, even if the automation still lists them one by one."""
    sensors = [_add(home, freezer, f"binary_sensor.leak_{n}", "moisture") for n in "abc"]
    _write_automations(home, [_leak_alert(*sensors)])
    await _group(home, "Water Leak Sensors", sensors)

    suggestions = await async_home_suggestions(home)

    assert _of_type(suggestions, "group_entities") == []


async def test_a_larger_group_is_no_stand_in_for_the_triggers(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Its extra member would fire the automation too."""
    sensors = [_add(home, freezer, f"binary_sensor.leak_{n}", "moisture") for n in "abcd"]
    _write_automations(home, [_leak_alert(*sensors[:3])])
    await _group(home, "Water Leak Sensors", sensors)

    assert len(_of_type(await async_home_suggestions(home), "group_entities")) == 1


async def test_an_automation_suggestion_names_the_automation_to_edit(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    sensors = [_add(home, freezer, f"binary_sensor.leak_{n}", "moisture") for n in "abc"]
    _write_automations(home, [_leak_alert(*sensors)])

    [suggestion] = _of_type(await async_home_suggestions(home), "group_entities")

    assert suggestion["collection"]["automation_id"] == "leak_alert"


async def test_triggers_that_differ_beyond_their_entities_are_not_grouped(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """No one statistic keeps both "any above" and "any below"."""
    a, b, c = (_add(home, freezer, f"sensor.temp_{n}", "temperature") for n in "abc")
    _write_automations(
        home,
        [
            {
                "id": "temps",
                "alias": "Temperature alert",
                "triggers": [
                    {"trigger": "numeric_state", "entity_id": [a, b], "above": 30},
                    {"trigger": "numeric_state", "entity_id": c, "below": 10},
                ],
                "actions": [],
            }
        ],
    )

    assert _of_type(await async_home_suggestions(home), "group_entities") == []


async def test_each_trigger_configuration_is_its_own_group(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Same devices under two triggers: one group. Other devices: another."""
    hot = [_add(home, freezer, f"sensor.hot_{n}", "temperature") for n in "abc"]
    cold = [_add(home, freezer, f"sensor.cold_{n}", "temperature") for n in "abc"]
    _write_automations(
        home,
        [
            {
                "id": "temps",
                "alias": "Temperature alert",
                "triggers": [
                    {"trigger": "numeric_state", "entity_id": hot, "above": 30},
                    {"trigger": "numeric_state", "entity_id": hot, "above": 40},
                    {"trigger": "numeric_state", "entity_id": cold, "below": 10},
                ],
                "actions": [],
            }
        ],
    )

    groups = _of_type(await async_home_suggestions(home), "group_entities")

    assert sorted([e["entity_id"] for e in g["entities"]] for g in groups) == [cold, hot]


async def test_two_trigger_entities_are_not_worth_a_group(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    sensors = [_add(home, freezer, f"binary_sensor.leak_{n}", "moisture") for n in "ab"]
    _write_automations(home, [_leak_alert(*sensors)])

    assert _of_type(await async_home_suggestions(home), "group_entities") == []


async def test_a_group_entity_is_never_its_own_kinds_newcomer(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The group's own binary_sensor shares its members' device class."""
    a = _add(home, freezer, "binary_sensor.leak_a", "moisture")
    b = _add(home, freezer, "binary_sensor.leak_b", "moisture")
    _write_automations(home, [_leak_alert(a, b)])
    await _group(home, "Water Leak Sensors", [a, b])

    assert _added(await async_home_suggestions(home)) == set()


async def test_a_dismissed_suggestion_stays_dismissed(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    a = _add(home, freezer, "binary_sensor.leak_a", "moisture")
    b = _add(home, freezer, "binary_sensor.leak_b", "moisture")
    await _group(home, "Water Leak Sensors", [a, b])
    _add(home, freezer, "binary_sensor.leak_new", "moisture")
    [suggestion] = await async_home_suggestions(home)

    await async_dismiss_home_suggestion(home, suggestion["suggestion_id"])

    assert await async_home_suggestions(home) == []


async def test_a_new_entity_is_notified_once(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    a = _add(home, freezer, "binary_sensor.leak_a", "moisture")
    b = _add(home, freezer, "binary_sensor.leak_b", "moisture")
    await _group(home, "Water Leak Sensors", [a, b])
    _add(home, freezer, "binary_sensor.leak_new", "moisture")
    notifier = HomeSuggestionNotifier(home)

    await notifier.async_check()
    notifications = home.data["persistent_notification"]
    [notification] = notifications.values()
    assert "leak new → Water Leak Sensors" in notification["message"]

    notifications.clear()
    await notifier.async_check()
    assert notifications == {}


async def test_an_old_gap_is_shown_but_not_notified(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A first start must not announce every gap in the home at once."""
    a = _add(home, freezer, "binary_sensor.leak_a", "moisture")
    b = _add(home, freezer, "binary_sensor.leak_b", "moisture")
    await _group(home, "Water Leak Sensors", [a, b])
    _add(home, freezer, "binary_sensor.leak_new", "moisture")
    freezer.tick(timedelta(days=2))

    await HomeSuggestionNotifier(home).async_check()

    assert home.data.get("persistent_notification", {}) == {}
    assert len(await async_home_suggestions(home)) == 1


async def test_a_device_moved_into_a_room_schedules_a_check(home: HomeAssistant) -> None:
    """Its entities take the device's area, so they may now fit a collection."""
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(home)
    device = dr.async_get(home).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("test", "leak")}
    )
    kitchen = ar.async_get(home).async_create("Kitchen").id
    notifier = HomeSuggestionNotifier(home)
    notifier.async_start()
    try:
        dr.async_get(home).async_update_device(device.id, area_id=kitchen)
        await home.async_block_till_done()

        assert notifier._cancel_check is not None
    finally:
        notifier.async_stop()
