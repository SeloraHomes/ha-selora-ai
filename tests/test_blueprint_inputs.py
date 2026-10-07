"""A blueprint automation's inputs are checked before it is written.

Home Assistant checks a ``use_blueprint`` automation only at reload, and only
that required inputs are present: a misspelled input, a value of the wrong
shape or an entity that does not exist were all reported valid and written.
These run on Home Assistant's own automation and blueprint setup, with the
blueprint file in the test's config directory.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import patch
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
import yaml

from custom_components.selora_ai.automation_utils import async_create_automation
from custom_components.selora_ai.helpers import caller_scope
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import TOOL_VALIDATE_AUTOMATION

from .chat_harness import ChatHarness

PATH = "selora/motion_light.yaml"
BLUEPRINT = """\
blueprint:
  name: Motion light
  domain: automation
  input:
    motion:
      name: Motion sensor
      selector:
        entity:
          filter: {domain: binary_sensor}
          exclude_entities: [binary_sensor.garage_motion]
    light_target:
      name: Light
      selector: {target: {entity: {domain: light}}}
    wait:
      name: Wait
      default: 120
      selector: {number: {min: 0, max: 3600}}
    night_light:
      name: Night light
      default: ""
      selector: {entity: {filter: {domain: light}}}
    chime:
      name: Chime
      default: media_player.kitchen
      selector: {entity: {filter: {domain: media_player}}}
triggers:
  - trigger: state
    entity_id: !input motion
    to: "on"
actions:
  - action: light.turn_on
    target: !input light_target
  - delay: !input wait
"""


async def _install(hass: HomeAssistant) -> HomeAssistant:
    """The blueprint store reads the config dir it was set up on."""
    path = pathlib.Path(hass.config.config_dir) / "blueprints" / "automation" / PATH
    path.parent.mkdir(parents=True)
    path.write_text(BLUEPRINT)
    # A write reloads automations, which reads it.
    (pathlib.Path(hass.config.config_dir) / "configuration.yaml").touch()
    assert await async_setup_component(hass, "automation", {})
    hass.states.async_set("binary_sensor.hall_motion", "off")
    hass.states.async_set("light.hall", "off")
    hass.states.async_set("media_player.hall", "off")
    return hass


@pytest.fixture
async def blueprints(hass: HomeAssistant) -> HomeAssistant:
    return await _install(hass)


def _automation(**inputs: Any) -> dict[str, Any]:
    return {"alias": "Hall motion", "use_blueprint": {"path": PATH, "input": inputs}}


async def _validate(
    hass: HomeAssistant, automation: dict[str, Any], *, is_admin: bool = True
) -> dict[str, Any]:
    with caller_scope(is_admin):
        return await mcp_dispatch._get_tool_handlers()[TOOL_VALIDATE_AUTOMATION](
            hass, {"yaml": yaml.safe_dump(automation)}
        )


GOOD = {
    "motion": "binary_sensor.hall_motion",
    "light_target": {"entity_id": "light.hall"},
    "chime": "media_player.hall",
}


async def test_inputs_that_fit_are_valid(blueprints: HomeAssistant) -> None:
    result = await _validate(blueprints, _automation(**GOOD))

    assert result["valid"] is True, result["errors"]


async def test_an_undeclared_input_is_named_with_what_it_takes(blueprints: HomeAssistant) -> None:
    result = await _validate(blueprints, _automation(**GOOD, not_a_real_input=1))

    (error,) = result["errors"]
    assert "not_a_real_input" in error
    assert "light_target (required)" in error
    assert "wait" in error


async def test_a_missing_required_input_is_refused(blueprints: HomeAssistant) -> None:
    result = await _validate(
        blueprints, _automation(motion="binary_sensor.hall_motion", chime="media_player.hall")
    )

    assert result["valid"] is False
    assert "light_target" in result["errors"][0]


async def test_a_value_outside_its_selector_is_refused(blueprints: HomeAssistant) -> None:
    result = await _validate(blueprints, _automation(**GOOD, wait=99999))

    assert result["valid"] is False
    assert "wait" in result["errors"][0]


@pytest.mark.parametrize(
    "inputs",
    [
        {**GOOD, "motion": "binary_sensor.nowhere"},
        {**GOOD, "light_target": {"entity_id": ["light.hall", "light.nowhere"]}},
    ],
)
async def test_an_entity_that_does_not_exist_is_refused(
    blueprints: HomeAssistant, inputs: dict[str, Any]
) -> None:
    result = await _validate(blueprints, _automation(**inputs))

    assert result["valid"] is False
    assert "unknown entity_id" in result["errors"][0]
    assert "nowhere" in result["errors"][0]


async def test_nothing_is_written_for_inputs_that_would_not_work(
    blueprints: HomeAssistant,
) -> None:
    result = await async_create_automation(blueprints, _automation(**GOOD, wait=-5))

    assert result["success"] is False
    assert not (pathlib.Path(blueprints.config.config_dir) / "automations.yaml").exists()


async def test_chat_hands_a_bad_input_back_to_the_model(hass: HomeAssistant) -> None:
    """The parser's validator cannot load the blueprint, so without this the
    user met the error at Accept instead of the model fixing it."""
    # Created first: the harness moves the config dir.
    harness = await ChatHarness.create(hass)
    await _install(hass)
    proposal = _automation(**GOOD, not_a_real_input=1)
    corrected = {
        "intent": "automation",
        "response": "Fixed.",
        "automation": _automation(**GOOD),
        "automation_yaml": "alias: Hall motion\n",
    }

    turn = await harness.stream(
        "motion light for the hall",
        chunks=f"Here it is.\n\n```automation\n{json.dumps(proposal)}\n```",
        retry_reply=corrected,
    )

    assert len(turn.architect_calls) == 2
    assert "not_a_real_input" in turn.architect_calls[1]["user_message"]
    assert turn.done["automation"]["use_blueprint"]["input"] == GOOD


async def test_an_entity_the_selector_filters_out_is_refused(blueprints: HomeAssistant) -> None:
    """It exists and fits the shape; the blueprint would still act on a switch."""
    blueprints.states.async_set("switch.kettle", "off")

    result = await _validate(
        blueprints, _automation(**{**GOOD, "light_target": {"entity_id": "switch.kettle"}})
    )

    assert result["valid"] is False
    assert "switch.kettle" in result["errors"][0]


async def test_a_path_with_spaces_is_refused(blueprints: HomeAssistant) -> None:
    """Checked trimmed and written as given, it passed and broke at reload."""
    automation = {"alias": "Hall", "use_blueprint": {"path": f" {PATH} ", "input": GOOD}}

    result = await _validate(blueprints, automation)

    assert result["valid"] is False
    assert "spaces" in result["errors"][0]


async def test_an_excluded_entity_is_refused(blueprints: HomeAssistant) -> None:
    """HA's own selector enforces exclude_entities (and include_entities, as an
    allowlist): the frontend picker never offers them."""
    blueprints.states.async_set("binary_sensor.garage_motion", "off")

    result = await _validate(
        blueprints, _automation(**{**GOOD, "motion": "binary_sensor.garage_motion"})
    )

    assert result["valid"] is False
    assert "motion" in result["errors"][0]


async def test_a_store_that_cannot_read_lets_the_write_through(blueprints: HomeAssistant) -> None:
    """As the path probe does: a store failure is not evidence of bad inputs."""
    store = blueprints.data["blueprint"]["automation"]
    with patch.object(store, "async_get_blueprint", side_effect=OSError("disk")):
        from custom_components.selora_ai.blueprint_inputs import async_blueprint_error

        assert await async_blueprint_error(blueprints, {"path": PATH, "input": GOOD}) is None


async def test_a_default_naming_a_missing_entity_is_refused(blueprints: HomeAssistant) -> None:
    """Left out, `chime` runs on media_player.kitchen, which this home lacks —
    the automation would act on nothing. An empty default means "none" and
    passes."""
    inputs = {k: v for k, v in GOOD.items() if k != "chime"}

    result = await _validate(blueprints, _automation(**inputs))

    assert result["valid"] is False
    assert "media_player.kitchen" in result["errors"][0]
    assert "explicitly" in result["errors"][0]


async def test_a_target_of_all_is_not_an_unknown_entity(blueprints: HomeAssistant) -> None:
    result = await _validate(
        blueprints, _automation(**{**GOOD, "light_target": {"entity_id": "all"}})
    )

    assert result["valid"] is True, result["errors"]


def test_an_unregistered_entity_matches_its_integration_filter(hass: HomeAssistant) -> None:
    """A YAML entity has no registry entry; the entity sources still say which
    integration it belongs to."""
    from custom_components.selora_ai.blueprint_inputs import _matches

    hass.states.async_set("light.porch", "off")
    with patch(
        "custom_components.selora_ai.blueprint_inputs.entity_sources",
        return_value={"light.porch": {"domain": "hue"}},
    ):
        assert _matches(hass, "light.porch", {"integration": "hue"})
        assert not _matches(hass, "light.porch", {"integration": "zha"})


async def test_a_statement_template_is_not_an_unknown_entity(blueprints: HomeAssistant) -> None:
    template = "{% if is_state('sun.sun', 'below_horizon') %}light.hall{% endif %}"

    result = await _validate(
        blueprints, _automation(**{**GOOD, "light_target": {"entity_id": template}})
    )

    assert result["valid"] is True, result["errors"]


async def test_a_registry_uuid_is_checked_as_its_entity(blueprints: HomeAssistant) -> None:
    from homeassistant.helpers import entity_registry as er

    entry = er.async_get(blueprints).async_get_or_create("light", "demo", "hall-2")
    blueprints.states.async_set(entry.entity_id, "off")

    result = await _validate(
        blueprints, _automation(**{**GOOD, "light_target": {"entity_id": entry.id}})
    )

    assert result["valid"] is True, result["errors"]


def test_unit_and_device_filters_are_enforced(hass: HomeAssistant) -> None:
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.selora_ai.blueprint_inputs import _matches

    config_entry = MockConfigEntry(domain="hue")
    config_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={("hue", "1")}, manufacturer="Signify"
    )
    entry = er.async_get(hass).async_get_or_create(
        "sensor", "hue", "temp-1", device_id=device.id, unit_of_measurement="°C"
    )

    assert _matches(hass, entry.entity_id, {"unit_of_measurement": "°C"})
    assert not _matches(hass, entry.entity_id, {"unit_of_measurement": "W"})
    assert _matches(hass, entry.entity_id, {"device": {"integration": "hue"}})
    assert not _matches(hass, entry.entity_id, {"device": {"manufacturer": "IKEA"}})


async def test_comma_separated_targets_are_read_as_several(blueprints: HomeAssistant) -> None:
    blueprints.states.async_set("light.landing", "off")

    good = await _validate(
        blueprints,
        _automation(**{**GOOD, "light_target": {"entity_id": "light.hall, light.landing"}}),
    )
    bad = await _validate(
        blueprints,
        _automation(**{**GOOD, "light_target": {"entity_id": "light.hall, light.nowhere"}}),
    )

    assert good["valid"] is True, good["errors"]
    assert "light.nowhere" in bad["errors"][0]


async def test_a_read_only_caller_learns_nothing_about_the_blueprint(
    blueprints: HomeAssistant,
) -> None:
    """Blueprint contents are admin-only, as in Home Assistant; a refusal
    naming the inputs a blueprint takes would hand them out."""
    result = await _validate(blueprints, _automation(**GOOD, not_a_real_input=1), is_admin=False)

    assert result["valid"] is True
    # `night_light` is declared and was not sent: only the blueprint knows it.
    assert "night_light" not in str(result)
    assert "admin" in result["note"]


async def test_a_non_text_input_name_is_refused_not_raised(blueprints: HomeAssistant) -> None:
    automation = _automation(**GOOD)
    automation["use_blueprint"]["input"][1] = "x"

    result = await _validate(blueprints, automation)

    assert result["valid"] is False
    assert "text" in result["errors"][0]
