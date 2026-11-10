"""A bare category with one candidate acts on it; with several, it asks.

``_build_unspecified_target_clarification`` answered "Turn on the fan" in a
one-fan home with ``"Which fan?\\n\\n- Bedroom Fan"`` before the provider
ran, so the lone-fan rule in ``commands/vacuum_fan.py`` never saw the turn.
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN202
import json
from typing import Any

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai import _collect_entity_states
from custom_components.selora_ai.llm_client import LLMClient
from custom_components.selora_ai.llm_client.client import _pre_provider_short_circuit
from custom_components.selora_ai.llm_client.intent import (
    _build_unspecified_target_clarification,
)
from custom_components.selora_ai.providers import create_provider

# A model that declines every turn: whatever acts on the fan is the
# deterministic layer, not the model.
_DECLINING_REPLY = json.dumps({"r": "This responder never acts.", "q": []})


def _fan(entity_id: str, name: str, area: str = "") -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "state": "off",
        "attributes": {"friendly_name": name},
        "area_name": area,
    }


class TestTheClarificationBuilder:
    @pytest.mark.parametrize("message", ["Turn on the fan", "Turn off the fan"])
    def test_one_fan_is_not_asked_about(self, message: str) -> None:
        entities = [_fan("fan.bedroom_fan", "Bedroom Fan", "Bedroom 1")]
        assert _build_unspecified_target_clarification(message, entities) is None
        assert _pre_provider_short_circuit(message, entities) is None

    def test_two_fans_are_asked_about(self) -> None:
        entities = [
            _fan("fan.bedroom_fan", "Bedroom Fan"),
            _fan("fan.office_fan", "Office Fan"),
        ]
        asked = _pre_provider_short_circuit("Turn on the fan", entities)
        assert asked is not None
        assert asked["intent"] == "clarification"
        assert asked["o"] == ["Bedroom Fan", "Office Fan"]
        assert asked["response"] == "Which fan?\n\n- Bedroom Fan\n- Office Fan"

    def test_one_fan_listed_twice_is_one_candidate(self) -> None:
        fan = _fan("fan.bedroom_fan", "Bedroom Fan")
        assert _build_unspecified_target_clarification("Turn on the fan", [fan, fan]) is None

    def test_same_named_fans_are_told_apart_by_area(self) -> None:
        entities = [
            _fan("fan.ceiling_1", "Ceiling Fan", "Bedroom"),
            _fan("fan.ceiling_2", "Ceiling Fan", "Office"),
        ]
        asked = _build_unspecified_target_clarification("Turn on the fan", entities)
        assert asked is not None
        assert asked["o"] == ["Ceiling Fan (Bedroom)", "Ceiling Fan (Office)"]

    @pytest.mark.parametrize(("area_1", "area_2"), [("", ""), ("Bedroom", "Bedroom")])
    def test_fans_the_area_cannot_tell_apart_are_asked_by_entity_id(
        self, area_1: str, area_2: str
    ) -> None:
        entities = [
            _fan("fan.ceiling_1", "Ceiling Fan", area_1),
            _fan("fan.ceiling_2", "Ceiling Fan", area_2),
        ]
        asked = _build_unspecified_target_clarification("Turn on the fan", entities)
        assert asked is not None
        assert asked["o"] == ["Ceiling Fan (fan.ceiling_1)", "Ceiling Fan (fan.ceiling_2)"]

    def test_an_unnamed_fan_still_counts(self) -> None:
        entities = [
            _fan("fan.bedroom_fan", "Bedroom Fan"),
            {"entity_id": "fan.attic", "state": "off", "attributes": {}},
        ]
        asked = _build_unspecified_target_clarification("Turn on the fan", entities)
        assert asked is not None
        assert asked["o"] == ["Bedroom Fan", "fan.attic"]

    def test_one_light_is_not_asked_about_either(self) -> None:
        light = {"entity_id": "light.lamp", "attributes": {"friendly_name": "Lamp"}}
        assert _build_unspecified_target_clarification("turn off the light", [light]) is None

    @pytest.mark.parametrize(
        ("message", "entity"),
        [
            ("turn it off", _fan("fan.bedroom_fan", "Bedroom Fan")),
            ("unlock it", {"entity_id": "lock.front", "attributes": {"friendly_name": "Front"}}),
            ("turn on the device", _fan("fan.bedroom_fan", "Bedroom Fan")),
        ],
    )
    def test_no_named_category_confirms_the_lone_device(self, message: str, entity: dict) -> None:
        """A pronoun or "the device" named no category: confirm, don't act."""
        asked = _pre_provider_short_circuit(message, [entity])
        assert asked is not None
        name = entity["attributes"]["friendly_name"]
        assert asked == {
            "intent": "clarification",
            "response": f"Did you mean {name}?",
            "o": [name],
        }


@pytest.fixture
def home5_fan(hass) -> str:
    """home5-cn's lone fan: a device and its entity both named "Bedroom Fan"."""
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    bedroom = ar.async_get(hass).async_create("bedroom_1")
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("test", "bedroom_fan")},
        name="Bedroom Fan",
        model="SmartMi Fan",
    )
    devices.async_update_device(device.id, area_id=bedroom.id)
    fan = er.async_get(hass).async_get_or_create(
        "fan", "test", "bedroom_fan", suggested_object_id="bedroom_fan", device_id=device.id
    )
    hass.states.async_set(
        fan.entity_id,
        "off",
        {"friendly_name": "Bedroom Fan", "supported_features": 3},
    )
    return fan.entity_id


class TestAChatTurn:
    @pytest.mark.parametrize(
        ("message", "service"),
        [("Turn on the fan", "fan.turn_on"), ("Turn off the fan", "fan.turn_off")],
    )
    async def test_the_lone_fan_is_acted_on(
        self, hass, home5_fan: str, message: str, service: str
    ) -> None:
        provider = create_provider("selora_local", hass)
        asked: list[str | None] = []

        async def send_request(*_args: Any, **_kwargs: Any) -> tuple[str | None, str | None]:
            asked.append(provider._call_kind.get())
            return provider._convert_slim_shape(_DECLINING_REPLY), None

        provider.send_request = send_request  # type: ignore[method-assign]
        result = await LLMClient(hass, provider).architect_chat(
            message, entities=_collect_entity_states(hass)
        )
        assert asked, "the turn never reached the provider"
        assert result["intent"] == "command", result
        assert [(c["service"], c["target"]) for c in result["calls"]] == [
            (service, {"entity_id": home5_fan})
        ]
