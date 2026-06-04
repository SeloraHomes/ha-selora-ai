"""Tests for the cloud-path entity relevance filter in _build_chat_messages.

The previous behaviour formatted every actionable entity from every
interesting domain, then truncated the resulting list at 500 entries
with a literal ``"(truncated to 500 entities)"`` marker. On a
1300-entity install (HA Green) the marker leaked into the assistant's
reply — Anthony's repro (2026-06-03):

    "The analysis was cut short because the AVAILABLE ENTITIES list
     was truncated — it shows 500 entities but has a (truncated to
     500 entities) note, meaning I didn't get to see your whole
     house fan entity, your outside temperature sensors, your met.no
     weather entities, or your indoor temperature sensors..."

The fix: keyword-rank the eligible pool with the same
``_filter_entities_by_keywords`` the local path uses, cap at
``_CLOUD_MAX_ENTITIES`` (200), and drop the marker entirely — the
model has the tool registry to look up anything specific that didn't
survive ranking.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.selora_ai.llm_client.client import LLMClient
from custom_components.selora_ai.llm_client.intent import _CLOUD_MAX_ENTITIES

# Anthony's exact prompt from his trial (whole-house-fan automation).
ANTHONY_PROMPT = (
    "Okay, now I want to create an automation that turn on the whole "
    "house fan when x number of windows are open and when the outside "
    "of the house is cooler than the inside. This action will turn "
    "off the AC if on and turn on the whole house fan which will "
    "remain on until the inside temp is equal to the outside temp or "
    "if the temp is equal to the cool setting on the thermostat. Lets "
    "do this: I want to rely on the inside temp sensors, the outside "
    "temp sensors and met.no weather (ignoring any that are wildly out "
    "of range of the predicted temp from Met.no), and pressure sensors. "
    "The automation will turn on the whole house fan when the outside "
    "temp is lower than the inside temp and will turn off when either "
    "the outside temp and inside temp equalizes or the inside temp hits "
    "the previously set cool temp on my ecobee or 70 degrees. The fan "
    "will only turn on if there are more than 50% of the windows open "
    "and will turn on low. If more than 80% of the windows are open, "
    "it will turn on high. If it detects we are negatively pressurizing "
    "the house, turn the fan speed lower (high to low, low to off, off "
    "stays off). Should this be 1 automation or two? I am hoping 1 "
    "automation but this one is complicated"
)


def _entity(eid: str, fname: str = "", area: str = "") -> dict:
    return {
        "entity_id": eid,
        "state": "on",
        "attributes": {"friendly_name": fname or eid},
        "area_name": area,
    }


def _synthetic_home() -> list[dict]:
    """A 1300-entity profile matching the trial user's HA Green install:
    lots of light + switch + sensor noise around a small core of the
    entities Anthony actually mentioned in his prompt."""
    pool: list[dict] = []
    # Entities Anthony explicitly named in the prompt.
    pool.append(_entity("fan.whole_house_fan", "Whole House Fan"))
    pool.append(_entity("climate.ecobee", "Ecobee"))
    pool.append(_entity("weather.met_no", "Met.no"))
    # Windows × 15 (whole-home coverage)
    for room in (
        "living_room",
        "kitchen",
        "main_bedroom",
        "guest_bedroom",
        "downstairs_bedroom",
        "upstairs_bathroom",
        "downstairs_bathroom",
        "breakfast_nook",
        "family_room",
        "office",
        "garage",
        "basement",
        "attic",
        "den",
        "mudroom",
    ):
        pool.append(_entity(f"binary_sensor.{room}_window", f"{room.title()} Window"))
    # Indoor temperature sensors × 12
    for room in (
        "main_bedroom",
        "guest_bedroom",
        "downstairs_bedroom",
        "upstairs_bathroom",
        "downstairs_bathroom",
        "living_room",
        "kitchen",
        "family_room",
        "office",
        "main_floor",
        "lower_level",
        "upstairs",
    ):
        pool.append(_entity(f"sensor.{room}_temperature", f"{room.title()} Temperature"))
    # Outside / weather sensors × 8
    for loc in (
        "front_yard",
        "backyard",
        "east_side",
        "main_floor_weather",
        "upstairs_weather",
        "downstairs_weather",
        "outside",
        "patio",
    ):
        pool.append(
            _entity(f"sensor.{loc}_temperature", f"{loc.replace('_', ' ').title()} Temperature")
        )
    # Atmospheric pressure sensors × 6 (negative-pressure detection)
    for loc in (
        "main_floor",
        "upstairs",
        "downstairs",
        "front_yard",
        "backyard",
        "east_side",
    ):
        pool.append(
            _entity(
                f"sensor.{loc}_atmospheric_pressure",
                f"{loc.replace('_', ' ').title()} Atmospheric Pressure",
            )
        )
    # NOISE — the long tail of a 1300-entity home that should NOT be
    # surfaced for a fan-automation request.
    for i in range(200):
        pool.append(_entity(f"light.bedroom_lamp_{i}", f"Bedroom Lamp {i}"))
    for i in range(150):
        pool.append(_entity(f"light.kitchen_lamp_{i}", f"Kitchen Lamp {i}"))
    for i in range(150):
        pool.append(_entity(f"switch.outlet_{i}", f"Outlet {i}"))
    for i in range(200):
        pool.append(_entity(f"sensor.battery_level_{i}", f"Battery {i}"))
    for i in range(200):
        pool.append(_entity(f"sensor.signal_strength_{i}", f"Signal Strength {i}"))
    for i in range(100):
        pool.append(_entity(f"media_player.speaker_{i}", f"Speaker {i}"))
    for i in range(250):
        pool.append(_entity(f"sensor.diagnostic_{i}", f"Diagnostic {i}"))
    return pool


def _make_client() -> LLMClient:
    """LLMClient with a stub provider — the messages helper doesn't touch
    the provider, only its config."""
    client = LLMClient.__new__(LLMClient)
    client._provider = MagicMock()
    client._provider.is_low_context = False
    client._max_suggestions = 5
    client._lookback_days = 7
    client._max_tokens_per_kind = {}
    return client


def test_anthony_prompt_keeps_every_category_he_mentioned() -> None:
    """The whole-house-fan prompt names: fan, windows, temp sensors
    (inside + outside), met.no, pressure, ecobee, thermostat. After
    ranking, the surviving entity block must include at least one
    representative from every mentioned category — otherwise the model
    will tool-call its way through 10 lookups before giving up (which
    is exactly what Anthony saw)."""
    client = _make_client()
    pool = _synthetic_home()
    assert len(pool) > 1200, "synthetic home should exercise the 200-cap path"

    messages = client._build_chat_messages(
        ANTHONY_PROMPT,
        pool,
        existing_automations=None,
        history=None,
    )
    user_content = messages[-1]["content"]
    available = user_content.split("AVAILABLE ENTITIES:")[1]

    # Every category Anthony named must be visible to the model.
    assert "fan.whole_house_fan" in available, "named fan must survive ranking"
    assert "climate.ecobee" in available, "named thermostat must survive"
    assert "weather.met_no" in available, "named weather entity must survive"
    assert "_window" in available, "no window survived — model can't reason about open count"
    assert "_temperature" in available, "no temperature survived — model has no temp data"
    assert "_atmospheric_pressure" in available, (
        "no pressure sensor survived — model can't honor negative-pressure rule"
    )


def test_anthony_prompt_drops_unrelated_noise() -> None:
    """The 1000+ unrelated lights / outlets / battery sensors / speakers
    should NOT survive the ranking — they're what was crowding the
    500-truncation buffer."""
    client = _make_client()
    pool = _synthetic_home()
    messages = client._build_chat_messages(
        ANTHONY_PROMPT,
        pool,
        existing_automations=None,
        history=None,
    )
    available = messages[-1]["content"].split("AVAILABLE ENTITIES:")[1]

    # Bedroom / kitchen lamps weren't mentioned — must NOT crowd the cap.
    assert "bedroom_lamp_0" not in available
    assert "kitchen_lamp_0" not in available
    # Battery / signal-strength diagnostic noise has no place in a fan automation.
    assert "battery_level_" not in available
    assert "signal_strength_" not in available
    # Speakers aren't mentioned.
    assert "media_player.speaker_" not in available


def test_truncation_marker_removed() -> None:
    """The previous behaviour appended ``"(truncated to 500 entities)"``
    to the entity block; the model then reported that exact phrase back
    to the user as "the analysis was cut short". The marker must not
    appear anywhere in the prompt — the model has tool calls for
    anything that didn't survive ranking."""
    client = _make_client()
    pool = _synthetic_home()
    messages = client._build_chat_messages(
        ANTHONY_PROMPT,
        pool,
        existing_automations=None,
        history=None,
    )
    full_prompt = messages[-1]["content"]
    assert "(truncated to" not in full_prompt
    assert "truncated to 500" not in full_prompt


def test_entity_block_bounded_by_cap() -> None:
    """Total entity lines must be at most ``_CLOUD_MAX_ENTITIES``."""
    client = _make_client()
    pool = _synthetic_home()
    messages = client._build_chat_messages(
        ANTHONY_PROMPT,
        pool,
        existing_automations=None,
        history=None,
    )
    available = messages[-1]["content"].split("AVAILABLE ENTITIES:")[1]
    # Each entity is one "  - ..." line; count them.
    entity_lines = [ln for ln in available.split("\n") if ln.startswith("  - ")]
    assert len(entity_lines) <= _CLOUD_MAX_ENTITIES


def test_small_home_unaffected() -> None:
    """A home with fewer than the cap of eligible entities never enters
    the ranking branch — every actionable entity passes through, in
    insertion order."""
    client = _make_client()
    pool = [
        _entity("light.lamp_a", "Lamp A"),
        _entity("light.lamp_b", "Lamp B"),
        _entity("switch.fan", "Fan Switch"),
    ]
    messages = client._build_chat_messages(
        "turn on lamp a",
        pool,
        existing_automations=None,
        history=None,
    )
    available = messages[-1]["content"].split("AVAILABLE ENTITIES:")[1]
    assert "light.lamp_a" in available
    assert "light.lamp_b" in available
    assert "switch.fan" in available


def test_pure_greeting_falls_back_to_first_n() -> None:
    """A prompt with no usable keywords (all stopwords or a greeting)
    falls back to the first N eligible entities — better than an empty
    AVAILABLE ENTITIES block, the model can still tool-call for
    anything specific."""
    client = _make_client()
    pool = _synthetic_home()
    messages = client._build_chat_messages(
        "Hello",
        pool,
        existing_automations=None,
        history=None,
    )
    available = messages[-1]["content"].split("AVAILABLE ENTITIES:")[1]
    entity_lines = [ln for ln in available.split("\n") if ln.startswith("  - ")]
    # Capped, but not empty.
    assert 0 < len(entity_lines) <= _CLOUD_MAX_ENTITIES
