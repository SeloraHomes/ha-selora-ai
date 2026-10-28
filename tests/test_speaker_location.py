"""Selora AI Local is told where the Assist device the user speaks to sits.

The area and floor come from the registries (``conversation.speaker_location``)
and reach the user turn as a SPEAKER LOCATION block; the entity ranking keeps
the speaker's area inside the line cap.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai import _collect_entity_states
from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.conversation import SeloraConversationEntity, speaker_location
from custom_components.selora_ai.llm_client import LLMClient
from custom_components.selora_ai.llm_client.intent import (
    _build_unspecified_target_clarification,
    _filter_entities_by_keywords,
)
from custom_components.selora_ai.providers import create_provider


@pytest.fixture
def home(hass: HomeAssistant) -> dict[str, str]:
    """A kitchen speaker, a bedroom satellite upstairs, and a lamp in each room."""
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    upstairs = fr.async_get(hass).async_create("Upstairs")
    areas = ar.async_get(hass)
    kitchen = areas.async_create("Kitchen")
    bedroom = areas.async_create("Bedroom", floor_id=upstairs.floor_id)
    hallway = areas.async_create("Hallway", floor_id=upstairs.floor_id)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)

    def device(name: str, area_id: str | None) -> str:
        created = devices.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={("test", name)}
        )
        devices.async_update_device(created.id, area_id=area_id)
        return created.id

    ids = {
        "kitchen_speaker": device("kitchen_speaker", kitchen.id),
        "satellite_device": device("satellite", bedroom.id),
        "unplaced": device("unplaced", None),
    }
    satellite = entities.async_get_or_create(
        "assist_satellite", "test", "satellite", device_id=ids["satellite_device"]
    )
    moved = entities.async_get_or_create(
        "assist_satellite", "test", "moved", device_id=ids["satellite_device"]
    )
    entities.async_update_entity(moved.entity_id, area_id=hallway.id)
    ids["satellite"] = satellite.entity_id
    ids["moved_satellite"] = moved.entity_id
    for name, area_id in (("kitchen", kitchen.id), ("bedroom", bedroom.id)):
        lamp = entities.async_get_or_create("light", "test", f"{name}_lamp")
        entities.async_update_entity(lamp.entity_id, area_id=area_id)
        hass.states.async_set(lamp.entity_id, "off", {"friendly_name": "Lamp"})
        ids[f"{name}_lamp"] = lamp.entity_id
    return ids


async def test_a_device_gives_its_area(hass: HomeAssistant, home: dict[str, str]) -> None:
    assert speaker_location(hass, home["kitchen_speaker"]) == {"area_name": "Kitchen"}


async def test_an_area_on_a_floor_gives_both(hass: HomeAssistant, home: dict[str, str]) -> None:
    assert speaker_location(hass, home["satellite_device"]) == {
        "area_name": "Bedroom",
        "floor_name": "Upstairs",
    }


async def test_a_satellite_resolves_through_its_device(
    hass: HomeAssistant, home: dict[str, str]
) -> None:
    assert speaker_location(hass, None, home["satellite"]) == {
        "area_name": "Bedroom",
        "floor_name": "Upstairs",
    }


async def test_the_satellite_entitys_own_area_wins(
    hass: HomeAssistant, home: dict[str, str]
) -> None:
    location = speaker_location(hass, home["satellite_device"], home["moved_satellite"])
    assert location == {"area_name": "Hallway", "floor_name": "Upstairs"}


@pytest.mark.parametrize(
    ("device_id", "satellite_id"),
    [(None, None), ("unplaced", None), ("no-such-device", None), (None, "assist_satellite.gone")],
)
async def test_nothing_placed_gives_none(
    hass: HomeAssistant, home: dict[str, str], device_id: str | None, satellite_id: str | None
) -> None:
    assert speaker_location(hass, home.get(device_id or "", device_id), satellite_id) is None


def test_the_speakers_area_breaks_ties_inside_the_cap() -> None:
    entities: list[Any] = [
        {
            "entity_id": f"light.lamp_{i}",
            "state": "off",
            "attributes": {"friendly_name": "Lamp"},
            "area_name": "Bedroom" if i == 5 else "Office",
        }
        for i in range(6)
    ]
    ranked = _filter_entities_by_keywords(
        entities, {"lamp"}, cap=3, message="turn on the lamp", speaker_area="Bedroom"
    )
    assert ranked[0]["entity_id"] == "light.lamp_5"
    # No speaker: the original order.
    ranked = _filter_entities_by_keywords(entities, {"lamp"}, cap=3, message="turn on the lamp")
    assert [e["entity_id"] for e in ranked] == ["light.lamp_0", "light.lamp_1", "light.lamp_2"]


async def _assist(
    hass: HomeAssistant, text: str, *, device_id: str | None = None, satellite_id: str | None = None
) -> tuple[str | None, str]:
    """One Assist turn on Selora AI Local: the user turn the model got (None when it
    was never asked) and the spoken reply."""
    provider = create_provider("selora_local", hass)
    seen: dict[str, str] = {}

    async def send_request(*_args: Any, **_kwargs: Any) -> tuple[str | None, str | None]:
        seen["turn"] = provider._build_training_user_content()
        return None, "stubbed"

    provider.send_request = send_request  # type: ignore[method-assign]
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    hass.data[DOMAIN] = {"test_entry_id": {"llm": LLMClient(hass, provider)}}
    user_input = MagicMock(
        text=text,
        language="en",
        conversation_id="conv_001",
        device_id=device_id,
        satellite_id=satellite_id,
    )
    chat_log = MagicMock()
    chat_log.content = []
    with patch(
        "custom_components.selora_ai._collect_entity_states",
        return_value=_collect_entity_states(hass),
    ):
        result = await SeloraConversationEntity(hass, entry)._async_handle_message(
            user_input, chat_log
        )
    return seen.get("turn"), result.response.speech["plain"]["speech"]


async def test_without_a_speaker_a_bare_category_is_still_asked(
    hass: HomeAssistant, home: dict[str, str]
) -> None:
    turn, speech = await _assist(hass, "turn on the lamp")
    assert turn is None
    assert speech.startswith("Which light?")


def _located(entity_id: str, area: str) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "state": "off",
        "attributes": {"friendly_name": entity_id.split(".", 1)[1]},
        "area_name": area,
    }


def test_the_speakers_area_settles_a_bare_category() -> None:
    entities = [_located("light.lamp", "Bedroom"), _located("light.desk", "Kitchen")]
    assert (
        _build_unspecified_target_clarification(
            "turn on the light", entities, speaker_area="Bedroom"
        )
        is None
    )


@pytest.mark.parametrize(
    ("message", "entity_id"),
    [
        # The speaker's area holds no light.
        ("turn on the light", "light.elsewhere"),
        # A pronoun has no category for the area to settle.
        ("turn it on", "light.lamp"),
        # Never a lock, whatever the area holds.
        ("unlock it", "lock.door"),
        ("lock them", "lock.door"),
    ],
)
def test_the_speakers_area_settles_nothing_else(message: str, entity_id: str) -> None:
    area = "Kitchen" if entity_id == "light.elsewhere" else "Bedroom"
    entities = [_located(entity_id, area), _located(entity_id + "_2", area)]
    asked = _build_unspecified_target_clarification(message, entities, speaker_area="Bedroom")
    assert asked is not None
    assert asked["intent"] == "clarification"


async def test_assist_tells_the_model_where_the_user_is(
    hass: HomeAssistant, home: dict[str, str]
) -> None:
    turn, _ = await _assist(hass, "turn on the lamp", satellite_id=home["satellite"])

    assert turn is not None
    assert "SPEAKER LOCATION:\n  area=Bedroom; floor=Upstairs\n\nAVAILABLE ENTITIES:\n" in turn
    assert (
        f"  - entity_id={home['bedroom_lamp']}; state=off; friendly_name=Lamp; "
        "area=Bedroom; floor=Upstairs\n"
    ) in turn
    assert f"  - entity_id={home['kitchen_lamp']}; state=off; friendly_name=Lamp; area=Kitchen" in (
        turn
    )
    # The speaker's lamp ranks first among equals.
    assert turn.index(home["bedroom_lamp"]) < turn.index(home["kitchen_lamp"])
