"""Trigger-side need pinning in the low-context entity selector.

An automation NAMES its target ("turn on the hallway lights") but only
DESCRIBES its trigger ("when motion is detected"). The trigger sensor
therefore carries none of the request's words, scores 0 against them, and
sorts past the cap — so the model is asked to write a trigger against an
entity it was never shown.

These tests assert through the REAL two-stage path a request takes:
``_filter_entities_by_keywords`` in ``LLMClient`` (cap 60), then the
provider's ``_format_entities_block`` (cap 25 for chat_automation). A
selector that is a no-op at the second stage passes when exercised in
isolation and fails here, which is the point.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from custom_components.selora_ai.llm_client.intent import (
    _filter_entities_by_keywords,
    _low_context_keywords,
)
from custom_components.selora_ai.providers import create_provider

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.selora_ai.providers.selora_local import SeloraLocalProvider
    from custom_components.selora_ai.types import EntitySnapshot


def _local_provider(hass: HomeAssistant) -> SeloraLocalProvider:
    return create_provider("selora_local", hass)  # type: ignore[return-value]


def _e(eid: str, fname: str, area: str = "", device_class: str = "") -> EntitySnapshot:
    attrs: dict = {"friendly_name": fname}
    if device_class:
        attrs["device_class"] = device_class
    return {"entity_id": eid, "state": "off", "attributes": attrs, "area_name": area}


def _home() -> list[EntitySnapshot]:
    """A plausible install: two areas of lights, the area-named diagnostic
    sensors every integration adds, and one presence sensor per area."""
    ents: list[EntitySnapshot] = []
    for area in ("Kitchen", "Hallway"):
        slug = area.lower()
        for i in range(30):
            ents.append(_e(f"light.{slug}_spot_{i}", f"{area} Spot {i}", area))
        for i in range(30):
            ents.append(_e(f"sensor.{slug}_sensor_{i}", f"{area} Sensor {i}", area))
    # Named for the room, not for what it senses — the realistic case.
    ents.append(_e("binary_sensor.hallway", "Hallway", "Hallway", device_class="motion"))
    return ents


def _one_area_home(domain: str = "light") -> list[EntitySnapshot]:
    """One area: ``domain`` entities and the area-named sensors beside them.

    ``_home()`` cannot observe an interleave. For a Kitchen request the Hallway
    lights still outscore the Kitchen sensors (11 vs 9), so the first stage
    hands the provider sixty lights and no sensor can reach the block whatever
    the second stage does — the assertion would hold on a selector that
    interleaved freely. Here every entity shares the named area, so the sensors
    do reach the second stage and the assertion can fail.
    """
    ents: list[EntitySnapshot] = []
    for i in range(30):
        ents.append(_e(f"{domain}.kitchen_{i}", f"Kitchen {domain.title()} {i}", "Kitchen"))
    for i in range(30):
        ents.append(_e(f"sensor.kitchen_sensor_{i}", f"Kitchen Sensor {i}", "Kitchen"))
    return ents


def _two_need_home() -> list[EntitySnapshot]:
    """A request with TWO unnamed needs: the motion sensors carry the request's
    area word and so survive the ranking unaided, while the light sensors carry
    none of its words and reach the model only if something pins them."""
    ents: list[EntitySnapshot] = []
    for i in range(30):
        ents.append(_e(f"light.hallway_spot_{i}", f"Hallway Spot {i}", "Hallway"))
    for i in range(6):
        ents.append(
            _e(f"binary_sensor.m{i}", f"Hallway Motion {i}", "Hallway", device_class="motion")
        )
    for i in range(3):
        ents.append(_e(f"sensor.lux{i}", f"Lux {i}", device_class="illuminance"))
    return ents


def _nothing_matches_home() -> list[EntitySnapshot]:
    """A home where no entity carries a word from the request: the motion
    sensor is named for its node id and the A/C for its floor. Every entity
    scores 0, so the keyword ranking has nothing to rank."""
    ents: list[EntitySnapshot] = [_e(f"light.fixture_{i}", f"Fixture {i}") for i in range(60)]
    ents.append(_e("binary_sensor.node_7", "Node 7", device_class="motion"))
    ents.append(_e("climate.main_floor", "Main Floor"))
    return ents


def _two_key_home() -> list[EntitySnapshot]:
    """Covers carrying ``device_class=door`` satisfy the cover DOMAIN need and
    the door CLASS need at once, so each sits in two need buckets."""
    ents: list[EntitySnapshot] = []
    for i in range(40):
        ents.append(_e(f"light.garage_spot_{i}", f"Garage Spot {i}", "Garage"))
    for i in range(4):
        ents.append(_e(f"cover.g{i}", f"Door {i}", "Garage", device_class="door"))
    ents.append(_e("binary_sensor.attic_win", "Contact", "Attic", device_class="window"))
    return ents


def _rendered(
    hass: HomeAssistant,
    message: str,
    kind: str,
    home: list[EntitySnapshot] | None = None,
) -> list[str]:
    """Entity ids in the block the model actually receives for ``message``."""
    selected = _filter_entities_by_keywords(
        _home() if home is None else home,
        _low_context_keywords(message),
        cap=60,
        message=message,
    )
    provider = _local_provider(hass)
    provider.set_call_kind(kind)
    block = provider._format_entities_block(selected)
    return [
        ln.split("entity_id=", 1)[1].split(";", 1)[0]
        for ln in block.splitlines()
        if ln.startswith("- entity_id=")
    ]


def test_single_domain_request_is_not_interleaved(hass: HomeAssistant) -> None:
    """A request naming lights and nothing else spends the whole window on
    lights. Reserving slots for a domain the request never asked for is the
    regression this pinning must not reintroduce."""
    ids = _rendered(
        hass,
        "at sunset turn on all the kitchen lights",
        "chat_automation",
        home=_one_area_home(),
    )
    assert len(ids) == 25
    assert {i.split(".", 1)[0] for i in ids} == {"light"}


def test_domain_need_request_is_not_interleaved(hass: HomeAssistant) -> None:
    """The same guard for a request whose category word DOES resolve a need.
    "lights" is in neither need table, so the test above cannot see a need
    table entry pulling an unasked-for domain into the window; "fans" is in
    ``_DOMAIN_NEED_TOKENS`` and can."""
    ids = _rendered(
        hass,
        "at sunset turn on all the kitchen fans",
        "chat_automation",
        home=_one_area_home("fan"),
    )
    assert len(ids) == 25
    assert {i.split(".", 1)[0] for i in ids} == {"fan"}


def test_trigger_sensor_survives_the_cap(hass: HomeAssistant) -> None:
    """The motion sensor the automation triggers on reaches the model even
    though the request never names it and it scores 0 on the request words."""
    ids = _rendered(
        hass,
        "when motion is detected in the hallway turn on the hallway lights",
        "chat_automation",
    )
    assert "binary_sensor.hallway" in ids


def test_pinning_does_not_grow_the_block(hass: HomeAssistant) -> None:
    """Pinned entities count against the cap; the prompt never grows."""
    ids = _rendered(
        hass,
        "when motion is detected in the hallway turn on the hallway lights",
        "chat_automation",
    )
    assert len(ids) == 25
    assert len(set(ids)) == len(ids)


def test_target_lights_still_dominate_the_window(hass: HomeAssistant) -> None:
    """Pinning reserves a few slots, not half the block — the request's own
    target stays the bulk of what the model sees."""
    ids = _rendered(
        hass,
        "when motion is detected in the hallway turn on the hallway lights",
        "chat_automation",
    )
    lights = [i for i in ids if i.startswith("light.")]
    assert len(lights) >= 18


def test_selection_is_deterministic(hass: HomeAssistant) -> None:
    """Same request, same block — a shifting entity prefix takes the LoRA
    out of the format it was trained against."""
    msg = "when motion is detected in the hallway turn on the hallway lights"
    assert _rendered(hass, msg, "chat_automation") == _rendered(hass, msg, "chat_automation")


def test_a_second_need_is_not_starved_by_the_first(hass: HomeAssistant) -> None:
    """Two needs, one bound. The motion sensors are named for the request's area
    and score on its words, so the ranking keeps them regardless; the light
    sensors score 0 and are why the bound exists. Spending every reserved slot
    best-first hands all four to the sensors that needed none and drops the
    others from the block entirely."""
    ids = _rendered(
        hass,
        "when motion is detected and the illuminance is low turn on the hallway lights",
        "chat_automation",
        home=_two_need_home(),
    )
    assert any(i.startswith("sensor.lux") for i in ids), ids
    assert any(i.startswith("binary_sensor.m") for i in ids), ids
    assert len(ids) == 25


def test_one_entity_cannot_spend_two_pinned_slots(hass: HomeAssistant) -> None:
    """A cover carrying ``device_class=door`` sits in the cover-domain bucket
    AND the door-class bucket. Counted once per bucket it takes two of the four
    reserved slots for one entity, and the window sensor the automation actually
    triggers on — which scores 0 on the request's words — is left out."""
    ids = _rendered(
        hass,
        "when the window opens close the garage door and turn on the garage lights",
        "chat_automation",
        home=_two_key_home(),
    )
    assert "binary_sensor.attic_win" in ids, ids
    assert len([i for i in ids if i.startswith("cover.")]) >= 2, ids


def test_needs_are_pinned_when_nothing_matched(hass: HomeAssistant) -> None:
    """A need is resolved from the REQUEST, not from what matched, so the case
    where nothing matched is the one it is needed for. "the A/C" resolves the
    climate domain off the raw message — the tokenizer drops "ac" as ≤2 chars,
    which is why that regex exists — and "motion" resolves the presence
    cluster. Resolving needs only where something scored discards both and
    hands the model a domain-ordered slice holding neither the trigger nor the
    target the request named outright."""
    ids = _rendered(
        hass,
        "when motion is detected turn on the A/C",
        "chat_automation",
        home=_nothing_matches_home(),
    )
    assert "binary_sensor.node_7" in ids, ids
    assert "climate.main_floor" in ids, ids
    assert len(ids) == 25


def test_unmatched_request_with_no_need_keeps_the_plain_fallback(
    hass: HomeAssistant,
) -> None:
    """The other half: a request resolving no need must still get exactly the
    domain-ordered fallback, unreordered."""
    ids = _rendered(hass, "do the spaceship thing", "chat_automation", home=_nothing_matches_home())
    assert ids == [f"light.fixture_{i}" for i in range(25)]
