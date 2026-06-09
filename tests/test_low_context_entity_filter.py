"""Tests for the low-context entity filter — relevance ranking + fallback.

Closes #104 ("Trim prompt context for local Selora model"). The filter
runs whenever the active LLM provider is low-context (Selora Local
add-on, max_seq=1024) and chooses which entities to render into the
AVAILABLE ENTITIES block of the trained system prompt.

Without ranking, a 200-entity install with 30 lights would surface the
15 lights with the smallest entity_id first — almost never the one the
user actually meant. Without a fallback, vague messages like "turn it
off" produced an empty AVAILABLE ENTITIES block and the LoRA would
hallucinate entity_ids or echo a prior automation.
"""

from __future__ import annotations

from custom_components.selora_ai.llm_client.intent import (
    _fallback_low_context_entities,
    _filter_entities_by_keywords,
    _low_context_keywords,
    _score_entity_against_keywords,
)


def _entity(eid: str, fname: str = "", area: str = "") -> dict:
    """Tiny EntitySnapshot factory for tests — only the fields the filter reads."""
    return {
        "entity_id": eid,
        "attributes": {"friendly_name": fname},
        "area_name": area,
    }


def test_exact_name_token_beats_substring() -> None:
    """`porch` as a friendly_name word outranks `porch` as a substring of a longer word."""
    porch_light = _entity("light.porch", "Porch Light")
    porchsetto = _entity("light.porchsetto", "Porchsetto Lamp")  # substring only
    kept = _filter_entities_by_keywords([porchsetto, porch_light], {"porch"}, cap=10)
    assert kept[0]["entity_id"] == "light.porch"


def test_more_matched_keywords_outranks_single_match() -> None:
    """Two-keyword hits beat one-keyword hits regardless of source order."""
    one_match = _entity("light.kitchen", "Kitchen Light")  # matches "light"
    two_match = _entity("light.porch", "Porch Light")  # matches "light" + "porch"
    kept = _filter_entities_by_keywords([one_match, two_match], {"porch", "light"}, cap=10)
    assert [e["entity_id"] for e in kept[:2]] == ["light.porch", "light.kitchen"]


def test_named_match_outranks_domain_only() -> None:
    """Keyword `light` should not let every `light.*` entity outrank a `switch.*`
    whose name actually contains `light`."""
    plain_light = _entity("light.x", "Lamp")  # only domain matches "light"
    named_match = _entity("switch.porch_light", "Porch Light Switch")
    kept = _filter_entities_by_keywords([plain_light, named_match], {"light"}, cap=10)
    assert kept[0] == named_match
    # Domain-only match is still kept so commands like "turn on the fan"
    # can reach `fan.bedroom` when no other keyword identifies it.
    assert plain_light in kept


def test_domain_keyword_keeps_unnamed_entity() -> None:
    """`fan.bedroom` with friendly_name "Bedroom" must score for keyword "fan" —
    otherwise "turn on the fan" misses the only fan in a larger home."""
    fan = _entity("fan.bedroom", "Bedroom")
    others = [_entity(f"light.area_{i}", f"Area {i} Light") for i in range(20)]
    kept = _filter_entities_by_keywords([*others, fan], {"fan"}, cap=15)
    assert fan in kept


def test_area_match_keeps_unnamed_entity() -> None:
    """An entity with no name but a matching area still scores — area is a weaker
    signal than name/id but better than zero."""
    in_porch = _entity("sensor.motion_42", area="porch")
    kept = _filter_entities_by_keywords([in_porch], {"porch"}, cap=10)
    assert kept == [in_porch]


def test_no_keywords_returns_fallback() -> None:
    """Empty keyword set (message was all stopwords) → controllable surface, not empty."""
    pool = [
        _entity("sensor.temp_a", "Temp A"),
        _entity("light.kitchen", "Kitchen"),
        _entity("switch.fan", "Fan"),
    ]
    kept = _filter_entities_by_keywords(pool, set(), cap=10)
    # sensor entities are NOT in the fallback domain list — only controllable surface
    assert all(e["entity_id"].split(".", 1)[0] != "sensor" for e in kept)
    assert {e["entity_id"] for e in kept} == {"light.kitchen", "switch.fan"}


def test_no_keyword_matches_falls_back() -> None:
    """User asks about something we don't have → still surface controllable entities."""
    pool = [
        _entity("light.kitchen", "Kitchen Light"),
        _entity("switch.fan", "Fan"),
    ]
    # 'spaceship' matches nothing — fallback should still hand back the lights/switches
    kept = _filter_entities_by_keywords(pool, {"spaceship"}, cap=10)
    assert {e["entity_id"] for e in kept} == {"light.kitchen", "switch.fan"}


def test_cap_enforced_after_ranking() -> None:
    """The cap applies AFTER ranking, not during the scan."""
    pool = [_entity(f"light.area_{i}", f"Area {i} Light") for i in range(20)]
    kept = _filter_entities_by_keywords(pool, {"light"}, cap=5)
    assert len(kept) == 5


def test_ties_broken_by_input_order_for_determinism() -> None:
    """Same prompt → same entity list every call (LoRA prefix-cache stays warm)."""
    a = _entity("light.a", "Bedroom Light")
    b = _entity("light.b", "Bedroom Light")  # same score
    c = _entity("light.c", "Bedroom Light")
    pool = [a, b, c]
    kept1 = _filter_entities_by_keywords(pool, {"bedroom"}, cap=2)
    kept2 = _filter_entities_by_keywords(pool, {"bedroom"}, cap=2)
    assert kept1 == kept2 == [a, b]


def test_fallback_prefers_controllable_domains_in_order() -> None:
    """Lights ahead of switches ahead of covers — matches user-facing relevance."""
    pool = [
        _entity("cover.shade", "Shade"),
        _entity("switch.fan", "Fan"),
        _entity("light.lamp", "Lamp"),
    ]
    kept = _fallback_low_context_entities(pool, cap=3)
    assert [e["entity_id"] for e in kept] == ["light.lamp", "switch.fan", "cover.shade"]


def test_fallback_skips_uncontrollable_domains() -> None:
    """sensor / binary_sensor / device_tracker have nothing for the user to do."""
    pool = [
        _entity("sensor.temp", "Temp"),
        _entity("binary_sensor.motion", "Motion"),
        _entity("device_tracker.phone", "Phone"),
        _entity("light.lamp", "Lamp"),
    ]
    kept = _fallback_low_context_entities(pool, cap=10)
    assert [e["entity_id"] for e in kept] == ["light.lamp"]


def test_score_zero_for_unrelated_entity() -> None:
    """Direct unit test on the scoring fn — sanity for the weights."""
    e = _entity("light.kitchen", "Kitchen Light", area="kitchen")
    assert _score_entity_against_keywords(e, {"bathroom"}) == 0


def test_score_combines_all_signals() -> None:
    """Friendly-name token + entity_id + area should sum (kitchen × 3 signals)."""
    e = _entity("switch.kitchen_lamp", "Kitchen Lamp", area="Kitchen")
    # kitchen: fname token (5) + eid_local "kitchen_lamp" contains "kitchen" (3) + area (1) = 9
    assert _score_entity_against_keywords(e, {"kitchen"}) == 9


def test_low_context_keywords_drops_stopwords() -> None:
    """Sanity that the keyword extractor still strips fillers — relevance hinges on it."""
    assert _low_context_keywords("turn on the porch light please") == {"porch", "light"}


def test_low_context_keywords_drops_action_verbs() -> None:
    """Action verbs ('turn', 'set', 'make') would otherwise boost unrelated entities like
    `media_player.turntable` via entity_id substring hits."""
    assert _low_context_keywords("set make get turn show tell give let put") == set()
