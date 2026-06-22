"""Tests for the cloud entity selector (`_filter_cloud_entities`).

Covers the need-aware pinning + bounded top-K used to keep the entities a
request needs inside the cloud prompt on large installs, and in particular
the regression where the cap-fill loop selected leftover entities in raw
input order instead of by relevance rank.
"""

from __future__ import annotations

from custom_components.selora_ai.llm_client.intent import (
    _CLOUD_MAX_ENTITIES,
    _filter_cloud_entities,
    _low_context_keywords,
)
from custom_components.selora_ai.types import EntitySnapshot


def _switch(i: int) -> EntitySnapshot:
    return {"entity_id": f"switch.dummy_{i}", "attributes": {"friendly_name": f"Dummy {i}"}}


def test_fill_uses_relevance_rank_not_input_order() -> None:
    """A high-scoring match at a high input index must survive the cap even
    when a pinned need also fires and earlier unrelated entities exist."""
    entities: list[EntitySnapshot] = [_switch(i) for i in range(_CLOUD_MAX_ENTITIES)]
    # Pinned need (fan domain) — always kept.
    entities.append(
        {"entity_id": "fan.office", "attributes": {"friendly_name": "Office Fan"}, "area_name": "office"}
    )
    # Highly relevant to the request but sitting at the very end of the input.
    entities.append(
        {"entity_id": "light.office", "attributes": {"friendly_name": "Office Light"}, "area_name": "office"}
    )

    selected = _filter_cloud_entities(
        entities, _low_context_keywords("turn on the office fan and office light"), cap=_CLOUD_MAX_ENTITIES
    )
    ids = [e["entity_id"] for e in selected]

    assert len(ids) == _CLOUD_MAX_ENTITIES
    assert "fan.office" in ids  # pinned domain need
    assert "light.office" in ids  # relevance-ranked, not dropped for low-index dummies


def test_pinned_need_survives_over_same_class_diagnostics() -> None:
    """The room sensor wins its pin slot over many same-class CPU sensors."""
    entities: list[EntitySnapshot] = [
        {
            "entity_id": f"sensor.cpu_temp_{i}",
            "attributes": {"friendly_name": f"CPU Temperature {i}", "device_class": "temperature"},
        }
        for i in range(50)
    ]
    entities.append(
        {
            "entity_id": "sensor.living_room_temp",
            "attributes": {"friendly_name": "Living Room Temperature", "device_class": "temperature"},
            "area_name": "living room",
        }
    )

    selected = _filter_cloud_entities(
        entities, _low_context_keywords("is it warm in the living room?"), cap=10
    )
    ids = [e["entity_id"] for e in selected]
    assert "sensor.living_room_temp" in ids


def test_no_need_returns_keyword_ranking() -> None:
    """With no semantic need the cap is decided by keyword ranking alone."""
    entities: list[EntitySnapshot] = [_switch(i) for i in range(20)]
    entities.append({"entity_id": "light.porch", "attributes": {"friendly_name": "Porch Light"}})

    selected = _filter_cloud_entities(entities, _low_context_keywords("turn on the porch light"), cap=5)
    assert selected[0]["entity_id"] == "light.porch"


def test_cap_zero_yields_empty() -> None:
    assert _filter_cloud_entities([_switch(0)], {"dummy"}, cap=0) == []


def test_cloud_budget_is_500() -> None:
    """The cloud entity budget is the long-standing 500, not a tighter cap."""
    assert _CLOUD_MAX_ENTITIES == 500


def test_ac_phrasing_pins_climate_via_message() -> None:
    """"turn on the AC" — 'ac' is dropped by the tokenizer, so the climate
    entity must be pinned via the raw-message scan, surviving past the cap."""
    entities: list[EntitySnapshot] = [_switch(i) for i in range(30)]
    entities.append(
        {"entity_id": "climate.living_room", "attributes": {"friendly_name": "Living Room"}}
    )
    msg = "turn on the AC"
    selected = _filter_cloud_entities(
        entities, _low_context_keywords(msg), cap=5, message=msg
    )
    assert "climate.living_room" in [e["entity_id"] for e in selected]


def test_air_conditioner_phrasing_pins_climate() -> None:
    entities: list[EntitySnapshot] = [_switch(i) for i in range(30)]
    entities.append(
        {"entity_id": "climate.bedroom", "attributes": {"friendly_name": "Bedroom"}}
    )
    msg = "switch on the air conditioner"
    selected = _filter_cloud_entities(
        entities, _low_context_keywords(msg), cap=5, message=msg
    )
    assert "climate.bedroom" in [e["entity_id"] for e in selected]


def test_dotted_ac_phrasing_pins_climate() -> None:
    """The dotted "A.C." spelling must also pin climate (trailing period
    leaves no \\b before the space — needs the (?!\\w) boundary)."""
    for msg in ("turn on the A.C.", "turn on the A.C. please", "set the A.C to cool"):
        entities: list[EntitySnapshot] = [_switch(i) for i in range(30)]
        entities.append(
            {"entity_id": "climate.den", "attributes": {"friendly_name": "Den"}}
        )
        selected = _filter_cloud_entities(
            entities, _low_context_keywords(msg), cap=5, message=msg
        )
        assert "climate.den" in [e["entity_id"] for e in selected], msg
