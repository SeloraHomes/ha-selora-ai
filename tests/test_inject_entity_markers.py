"""Unit tests for _inject_entity_markers — the deterministic post-processor
that splices `[[entities:…]]` markers into plain-prose LLM answers so the
chat renderer builds tile cards even when the model ignores marker rules.
"""

from __future__ import annotations

from custom_components.selora_ai import _inject_entity_markers


def _ent(eid: str, friendly_name: str) -> dict:
    return {
        "entity_id": eid,
        "state": "off",
        "attributes": {"friendly_name": friendly_name},
    }


def test_friendly_name_match_appends_marker():
    text = "Yes, you have a garage door in your setup.\n\n**Garage Door Status:** Closed"
    entities = [_ent("cover.garage_door", "Garage Door")]
    out = _inject_entity_markers(text, entities)
    assert out.endswith("[[entities:cover.garage_door]]")


def test_already_marked_entity_is_skipped():
    text = "Status:\n[[entity:cover.garage_door|Garage Door]]"
    entities = [_ent("cover.garage_door", "Garage Door")]
    out = _inject_entity_markers(text, entities)
    assert out == text  # already marked, no append


def test_longest_name_wins_over_substring():
    text = "The Garage Door is closed."
    entities = [
        _ent("cover.door", "Door"),
        _ent("cover.garage_door", "Garage Door"),
    ]
    out = _inject_entity_markers(text, entities)
    assert "[[entities:cover.garage_door]]" in out
    assert "cover.door" not in out.split("[[entities:")[1]


def test_word_boundary_avoids_substring_false_positive():
    # "tv" is part of "television" — without word-boundary check this
    # would wrongly mark media_player.tv whenever the prose talks about
    # televisions in general.
    text = "Televisions are great."
    entities = [_ent("media_player.tv", "TV")]
    out = _inject_entity_markers(text, entities)
    assert out == text


def test_short_friendly_name_is_ignored():
    # 3-char friendly_names are too risky (false positives in prose).
    text = "The cat is sleeping."
    entities = [_ent("sensor.cat_motion", "Cat")]
    out = _inject_entity_markers(text, entities)
    assert out == text


def test_raw_entity_id_in_prose_is_matched():
    text = "The cover.garage_door entity reports closed."
    entities = [_ent("cover.garage_door", "Garage Door")]
    out = _inject_entity_markers(text, entities)
    assert "[[entities:cover.garage_door]]" in out


def test_multiple_devices_combine_into_single_marker():
    text = "Kitchen Lights are on and Office Lights are off."
    entities = [
        _ent("light.kitchen", "Kitchen Lights"),
        _ent("light.office", "Office Lights"),
    ]
    out = _inject_entity_markers(text, entities)
    assert "[[entities:light.kitchen,light.office]]" in out


def test_caps_at_marker_limit():
    text = " ".join(f"Light{i}" for i in range(20))
    entities = [_ent(f"light.l{i}", f"Light{i}") for i in range(20)]
    out = _inject_entity_markers(text, entities)
    marker = out.split("[[entities:")[1].rstrip("]]")
    assert len(marker.split(",")) == 12


def test_empty_inputs_passthrough():
    assert _inject_entity_markers("", [_ent("light.x", "Light X")]) == ""
    assert _inject_entity_markers("hello", []) == "hello"


def test_bullet_list_replaced_inline_with_marker():
    # Exact failure shape from the user's screenshot. The "Lights (5 on):"
    # header AND the bullet list should be replaced *in place* by a
    # single marker, so the tile grid lands between the lead-in and the
    # follow-up sentence — not at the bottom of the bubble.
    text = """The following lights are currently on in your setup:

**Lights** (5 on):
- **Ceiling Lights** — on (brightness: 180)
- **Kitchen Lights** — on (brightness: 180)
- **Office RGBW Lights** — on (brightness: 180)
- **Living Room RGBWW Lights** — on (brightness: 180)
- **Entrance Color + White Lights** — on (brightness: 180)

If you need to control any of these lights, just let me know!"""
    entities = [
        _ent("light.ceiling", "Ceiling Lights"),
        _ent("light.kitchen", "Kitchen Lights"),
        _ent("light.office_rgbw", "Office RGBW Lights"),
        _ent("light.living_room_rgbww", "Living Room RGBWW Lights"),
        _ent("light.entrance", "Entrance Color + White Lights"),
    ]
    out = _inject_entity_markers(text, entities)
    # Header gone (bold around just one word must still be stripped).
    assert "(5 on):" not in out
    assert "brightness: 180" not in out
    assert "Ceiling Lights" not in out
    # Inline placement: marker must appear BEFORE the follow-up sentence.
    marker = (
        "[[entities:light.ceiling,light.kitchen,light.office_rgbw,"
        "light.living_room_rgbww,light.entrance]]"
    )
    assert marker in out
    assert out.index(marker) < out.index(
        "If you need to control any of these lights"
    )


def test_bullet_without_state_hint_stripped():
    text = "Devices:\n- Kitchen Lights\n- Office Lights"
    entities = [
        _ent("light.kitchen", "Kitchen Lights"),
        _ent("light.office", "Office Lights"),
    ]
    out = _inject_entity_markers(text, entities)
    assert "- Kitchen Lights" not in out
    assert "- Office Lights" not in out
    assert "[[entities:light.kitchen,light.office]]" in out


def test_non_entity_bullet_is_preserved():
    # Bullets that aren't entity references must survive untouched.
    text = "Steps:\n- First, open the app\n- Then tap settings"
    entities = [_ent("light.kitchen", "Kitchen Lights")]
    out = _inject_entity_markers(text, entities)
    assert out == text  # no entity mentioned, nothing changed


def test_count_header_kept_when_bullets_partially_match():
    # If some bullets in the run aren't entity-only, the header stays
    # because the list still has real content. Strip only the bullets we
    # consumed; the user-facing list still needs its title.
    text = """Devices (2):
- Kitchen Lights
- This bullet is prose explaining something."""
    entities = [_ent("light.kitchen", "Kitchen Lights")]
    out = _inject_entity_markers(text, entities)
    assert "Devices (2):" in out
    assert "This bullet is prose" in out
    assert "- Kitchen Lights" not in out
    assert "[[entities:light.kitchen]]" in out


def test_area_grouped_markers_do_not_trigger_prose_pass():
    # The LLM emitted markers properly with area sub-headings, the
    # Assist-style format. The post-processor must trust that output:
    # the area words ("Kitchen", "Living Room") are headers, not
    # references to media_player.kitchen / media_player.living_room.
    text = """Five lights are currently on:

### Kitchen
[[entity:light.kitchen_lights|Kitchen Lights]]

### Living Room
[[entity:light.living_room_rgbww|Living Room RGBWW Lights]]"""
    entities = [
        _ent("light.kitchen_lights", "Kitchen Lights"),
        _ent("light.living_room_rgbww", "Living Room RGBWW Lights"),
        # The dangerous ambiguous-name siblings that previously got
        # false-positive matched against the area headers.
        _ent("media_player.kitchen", "Kitchen"),
        _ent("media_player.living_room", "Living Room"),
    ]
    out = _inject_entity_markers(text, entities)
    assert "media_player.kitchen" not in out
    assert "media_player.living_room" not in out
    # The original markers are untouched.
    assert "[[entity:light.kitchen_lights|Kitchen Lights]]" in out
    assert "[[entity:light.living_room_rgbww|Living Room RGBWW Lights]]" in out


def test_bullet_run_capture_blocks_prose_false_positives():
    # When the LLM emits a clean bullet list, trust the capture and
    # don't also prose-scan the area-name lead-in for siblings with
    # the same friendly_name.
    text = """Lights in Kitchen and Living Room are on:

- **Kitchen Lights** — on
- **Living Room RGBWW Lights** — on"""
    entities = [
        _ent("light.kitchen_lights", "Kitchen Lights"),
        _ent("light.living_room_rgbww", "Living Room RGBWW Lights"),
        _ent("media_player.kitchen", "Kitchen"),
        _ent("media_player.living_room", "Living Room"),
    ]
    out = _inject_entity_markers(text, entities)
    assert "media_player.kitchen" not in out
    assert "media_player.living_room" not in out
    assert "[[entities:light.kitchen_lights,light.living_room_rgbww]]" in out


def test_bullets_plus_trailing_marker_collapsed_inline():
    # Exact failure shape captured from the user's dev console: the LLM
    # emitted BOTH a friendly_name bullet list AND a trailing marker.
    # The bullets must go, the LLM's marker must move inline so tiles
    # land between the lead-in and the follow-up sentence.
    text = """The following lights are currently on in your setup:

**Lights** (6 on):
- **Ceiling Lights** — on (brightness: 180)
- **Kitchen Lights** — on (brightness: 180)
- **Office RGBW Lights** — on (brightness: 180)
- **Living Room RGBWW Lights** — on (brightness: 180)
- **Entrance Color + White Lights** — on (brightness: 180)
- **Decorative Lights** — on

If you would like to manage any of these lights, just let me know!

[[entities:light.ceiling_lights,light.kitchen_lights,light.office_rgbw_lights,light.living_room_rgbww_lights,light.entrance_color_white_lights,switch.decorative_lights]]"""
    entities = [
        _ent("light.ceiling_lights", "Ceiling Lights"),
        _ent("light.kitchen_lights", "Kitchen Lights"),
        _ent("light.office_rgbw_lights", "Office RGBW Lights"),
        _ent("light.living_room_rgbww_lights", "Living Room RGBWW Lights"),
        _ent("light.entrance_color_white_lights", "Entrance Color + White Lights"),
        _ent("switch.decorative_lights", "Decorative Lights"),
        # Sibling speaker entities — must NOT get pulled in by the
        # fallback prose scan (which shouldn't even run here).
        _ent("media_player.kitchen", "Kitchen"),
        _ent("media_player.living_room", "Living Room"),
    ]
    out = _inject_entity_markers(text, entities)
    # Bullets and header gone, no media_players false-matched.
    assert "(6 on):" not in out
    assert "brightness: 180" not in out
    assert "Ceiling Lights" not in out
    assert "media_player.kitchen" not in out
    assert "media_player.living_room" not in out
    # Marker lands inline (before the follow-up sentence), only ONE
    # marker block survives — the LLM's trailing one was moved up.
    assert out.count("[[entities:") == 1
    marker_idx = out.index("[[entities:")
    followup_idx = out.index("If you would like to manage any of these lights")
    assert marker_idx < followup_idx


def test_status_section_replaced_inline():
    # Single-entity Q&A response shape: the LLM emits a sub-heading
    # describing one device's state, followed by "Label: value" bullets.
    # The whole section should collapse to one inline marker so the
    # tile renders between the lead-in and the follow-up sentence.
    text = """Yes, you have a garage door in your setup.

**Garage Door Status:**
- **Status:** Closed

If you need to control the garage door or check anything else, just let me know!"""
    entities = [_ent("cover.garage_door", "Garage Door")]
    out = _inject_entity_markers(text, entities)
    assert "Garage Door Status:" not in out
    assert "**Status:** Closed" not in out
    assert "- **Status:**" not in out
    assert out.count("[[entit") == 1
    marker_idx = out.index("[[entit")
    assert out.index("Yes, you have a garage door") < marker_idx
    assert marker_idx < out.index("If you need to control")


def test_trailing_marker_repositioned_after_lead_in():
    # LLM emitted the marker correctly but placed it after the
    # follow-up sentence — tiles end up at the bottom of the bubble.
    # Move the marker up to right after the paragraph that mentions
    # the entity by friendly_name.
    text = """Yes, you have a garage door in your setup.

If you need to control the garage door or check anything else, just let me know!

[[entity:cover.garage_door|Garage Door]]"""
    entities = [_ent("cover.garage_door", "Garage Door")]
    out = _inject_entity_markers(text, entities)
    assert out.count("[[entit") == 1
    marker_idx = out.index("[[entit")
    lead_in_idx = out.index("Yes, you have a garage door")
    followup_idx = out.index("If you need to control")
    assert lead_in_idx < marker_idx < followup_idx


def test_inserted_marker_has_symmetric_blank_lines():
    # Asymmetric spacing (no blank before, one blank after) made the
    # gap above/below the tile visibly uneven. The output must wrap
    # every inserted marker with one blank line on each side.
    text = """Yes, you have a garage door in your setup.

If you need to control the garage door or check anything else, just let me know!

[[entity:cover.garage_door|Garage Door]]"""
    entities = [_ent("cover.garage_door", "Garage Door")]
    out = _inject_entity_markers(text, entities)
    assert "\n\n[[entities:cover.garage_door]]\n\n" in out


def test_multiple_trailing_markers_merge_into_one_insertion():
    # The LLM emitted two separate single-entity trailing markers
    # AFTER the follow-up sentence. Both target the same paragraph for
    # repositioning, so the insertion slot collides; the second
    # marker's assignment used to overwrite the first, silently
    # dropping that entity's tile. Merge instead of overwrite.
    text = """Kitchen Lights and Office Lights are both on.

Anything else I can help with?

[[entity:light.kitchen|Kitchen Lights]]
[[entity:light.office|Office Lights]]"""
    entities = [
        _ent("light.kitchen", "Kitchen Lights"),
        _ent("light.office", "Office Lights"),
    ]
    out = _inject_entity_markers(text, entities)
    # Both entity_ids survive — neither tile dropped.
    assert "light.kitchen" in out
    assert "light.office" in out
    # The original `[[entity:…|…]]` lines at the bottom are stripped.
    assert "[[entity:light.kitchen|" not in out
    assert "[[entity:light.office|" not in out
    # Both ids land in a single merged marker, so the tile grid is one
    # row instead of two stacked single-tile grids.
    assert out.count("[[entit") == 1


def test_trailing_marker_left_alone_when_already_inline():
    # The LLM already placed the marker right after the lead-in. Don't
    # move it — that would be churn and could break grouped layouts.
    text = """Yes, you have a garage door.

[[entity:cover.garage_door|Garage Door]]

Anything else?"""
    entities = [_ent("cover.garage_door", "Garage Door")]
    out = _inject_entity_markers(text, entities)
    assert out == text


def test_hyphen_minus_state_separator_captured():
    # Some LLMs print "- Kitchen Lights - on" with a plain hyphen-minus
    # as the state separator instead of em/en-dash. The bullet pass
    # must still recognize this so the row isn't left as prose.
    text = "Lights on:\n- Kitchen Lights - on\n- Office Lights - on"
    entities = [
        _ent("light.kitchen", "Kitchen Lights"),
        _ent("light.office", "Office Lights"),
    ]
    out = _inject_entity_markers(text, entities)
    assert "- Kitchen Lights" not in out
    assert "- Office Lights" not in out
    assert "[[entities:light.kitchen,light.office]]" in out


def test_hyphen_inside_friendly_name_not_treated_as_separator():
    # Friendly names with internal hyphens ("A-frame House") must not
    # get truncated at the first `-`. Hyphen-minus is a separator only
    # when surrounded by whitespace.
    text = "Status:\n- A-frame House — present"
    entities = [_ent("binary_sensor.a_frame", "A-frame House")]
    out = _inject_entity_markers(text, entities)
    assert "- A-frame House" not in out
    assert "[[entities:binary_sensor.a_frame]]" in out


def test_raw_entity_id_bullet_with_underscore_captured():
    # When the LLM prints the raw entity_id instead of the friendly_name
    # ("- light.kitchen_lights — on"), the bullet pass must still match
    # — the underscore in the object_id used to terminate the name
    # capture, leaving the row as untouched prose.
    text = "Lights on:\n- light.kitchen_lights — on\n- light.office_rgbw — on"
    entities = [
        _ent("light.kitchen_lights", "Kitchen Lights"),
        _ent("light.office_rgbw", "Office RGBW Lights"),
    ]
    out = _inject_entity_markers(text, entities)
    assert "- light.kitchen_lights" not in out
    assert "- light.office_rgbw" not in out
    assert (
        "[[entities:light.kitchen_lights,light.office_rgbw]]" in out
    )


def test_state_info_bullets_stripped_near_marker():
    # Exact shape from the heat-pump screenshot: prose intro,
    # contiguous `- **Attr:** value` bullets, follow-up, trailing
    # marker. The bullets duplicate what the tile shows live and
    # must be stripped wholesale — no whitelist of attribute names.
    text = """The status of your heat pump is currently set to **heat**.

- **Current Temperature:** 25.0 °C

- **Target Temperature:** 20.0 °C

If you need to adjust the settings, just let me know!

[[entity:climate.heat_pump|HeatPump]]"""
    entities = [_ent("climate.heat_pump", "HeatPump")]
    out = _inject_entity_markers(text, entities)
    assert "**Current Temperature:**" not in out
    assert "**Target Temperature:**" not in out
    assert "25.0 °C" not in out
    assert "20.0 °C" not in out
    # Prose intro and follow-up survive; one marker remains.
    assert "currently set to **heat**" in out
    assert "If you need to adjust" in out
    assert out.count("[[entit") == 1


def test_camelcase_friendly_name_matches_spaced_prose():
    # LLM-natural prose splits "HeatPump" into "heat pump". Without
    # CamelCase-aware matching, the bullet-name lookup and marker
    # repositioning both miss this entity and the tile stays at the
    # bottom of the bubble while the bullet list survives.
    text = """The status of your heat pump is set to heat.

If you need anything else, just let me know!

[[entity:climate.heat_pump|HeatPump]]"""
    entities = [_ent("climate.heat_pump", "HeatPump")]
    out = _inject_entity_markers(text, entities)
    # Marker now repositioned between the lead-in and follow-up,
    # not lingering at the end.
    marker_idx = out.index("[[entities:climate.heat_pump")
    lead_idx = out.index("The status of your heat pump")
    followup_idx = out.index("If you need anything else")
    assert lead_idx < marker_idx < followup_idx


def test_state_info_bullets_kept_when_no_marker_present():
    # Without any device marker in the response, the same shape
    # might be legitimate prose (capability lists, FAQ-style
    # bullets). Leave it alone.
    text = """Selora can help with:
- Lights: turn on/off, adjust brightness
- Climate: set temperature, change mode"""
    entities = [_ent("light.kitchen", "Kitchen Lights")]
    out = _inject_entity_markers(text, entities)
    assert out == text


def test_status_section_with_unknown_name_kept():
    # If the heading mentions something that isn't a known friendly_name
    # ("System Status:"), leave the section untouched — we can't safely
    # attribute it to any entity.
    text = """**System Status:**
- **CPU:** 40%
- **Memory:** 60%"""
    entities = [_ent("cover.garage_door", "Garage Door")]
    out = _inject_entity_markers(text, entities)
    assert out == text  # nothing matched, nothing stripped


def test_overlapping_friendly_names_dedupe():
    # Same entity mentioned twice: marker should appear once.
    text = "Garage Door is closed. The Garage Door has been closed since 9am."
    entities = [_ent("cover.garage_door", "Garage Door")]
    out = _inject_entity_markers(text, entities)
    marker_count = out.count("[[entities:")
    assert marker_count == 1
    assert "[[entities:cover.garage_door]]" in out
