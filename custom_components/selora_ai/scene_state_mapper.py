"""Scene state mapper -- domain-specific state validation and default inference.

Maps scene intents to appropriate target states per entity domain.
Validates that entity state attributes match what each domain supports,
and fills in reasonable defaults for common scene keywords.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from .entity_capabilities import SCENE_CAPABLE_DOMAINS

_LOGGER = logging.getLogger(__name__)

# Matches the entity-id pattern used by scene_utils.validate_scene_payload():
# domain = letters/underscores (first char) then letters/digits/underscores;
# object_id = letters/digits/underscores/hyphens.
# Input is lowercased before matching so mixed-case LLM output is accepted.
_ENTITY_ID_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z0-9][a-z0-9_-]*$")

# Expected (element_type, length, per-element (min, max)) for color list attributes.
_COLOR_LIST_SPECS: dict[str, tuple[type, int, tuple[float, float]]] = {
    "rgb_color": (int, 3, (0, 255)),
    "rgbw_color": (int, 4, (0, 255)),
    "rgbww_color": (int, 5, (0, 255)),
    "hs_color": (float, 2, (0.0, 360.0)),  # hue 0-360, sat 0-100 — clamped per-element below
    "xy_color": (float, 2, (0.0, 1.0)),
}
# hs_color has different ranges per element; handled specially in validation.
_HS_COLOR_RANGES: tuple[tuple[float, float], tuple[float, float]] = (
    (0.0, 360.0),  # hue
    (0.0, 100.0),  # saturation
)

# ``light/reproduce_state.py`` reads ``color_mode`` to pick WHICH saved colour
# to send; without it, it falls back to the first attribute present in
# COLOR_GROUP order. A snapshot carrying both ``hs_color`` and
# ``color_temp_kelvin`` therefore reproduces the wrong one once the mode is
# gone. This mirrors that module's COLOR_MODE_TO_ATTRIBUTE: the modes it maps,
# and the attribute each one needs. Modes absent from it (``onoff``,
# ``brightness``, ``unknown``) carry no colour and need no entry.
# Modes with no colour to send (reproduce_state maps none of these, so it
# sends no colour attribute at all) -- valid, and kept as-is.
_COLOURLESS_MODES: frozenset[str] = frozenset({"onoff", "brightness", "unknown"})
_COLOR_MODE_ATTRIBUTE: dict[str, str] = {
    "color_temp": "color_temp_kelvin",
    "hs": "hs_color",
    "rgb": "rgb_color",
    "rgbw": "rgbw_color",
    "rgbww": "rgbww_color",
    "white": "brightness",
    "xy": "xy_color",
}

# Percentage spellings a model reaches for when it means "half brightness".
# HA reproduces ``brightness`` on 0-255 and reads nothing else, so a percentage
# left under any of these is dropped on activation while the proposal card
# still previews it -- the scene looks right and does nothing.
BRIGHTNESS_PCT_KEYS: tuple[str, ...] = (
    "brightness_pct",
    "brightness_percent",
    "brightness_percentage",
)

# Allowed state values per domain.  Prevents cross-domain confusion like
# a light with state "open" or a cover with state "on".
_DOMAIN_VALID_STATES: dict[str, frozenset[str]] = {
    "light": frozenset({"on", "off"}),
    "switch": frozenset({"on", "off"}),
    "media_player": frozenset({"on", "off", "playing", "paused", "idle", "standby", "buffering"}),
    "climate": frozenset({"off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"}),
    "fan": frozenset({"on", "off"}),
    "cover": frozenset({"open", "closed", "opening", "closing"}),
}

# Allowed values for specific non-state string attributes.
_VALID_HVAC_MODES = frozenset({"off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"})

# Allowed state attributes per domain.  Keys are attribute names,
# values are the expected Python type (used for coercion).
# Every key here is one ``homeassistant.components.<domain>.reproduce_state``
# reads off the saved State. An attribute outside the schema is dropped, so a
# key missing from this table is a scene setting the user asked for and HA
# never applies -- check the domain's reproduce_state module before trimming
# one, not the service signature, which is a wider set.
DOMAIN_STATE_SCHEMAS: dict[str, dict[str, type]] = {
    "light": {
        "state": str,
        "brightness": int,
        "effect": str,
        "color_mode": str,
        "color_temp": int,  # legacy mireds; core reproduces kelvin only
        "color_temp_kelvin": int,
        "rgb_color": list,
        "rgbw_color": list,
        "rgbww_color": list,
        "hs_color": list,
        "xy_color": list,
    },
    "switch": {
        "state": str,
    },
    "media_player": {
        "state": str,
        "volume_level": float,
        "is_volume_muted": bool,
        "source": str,
        "sound_mode": str,
        "media_content_type": str,
        "media_content_id": str,
    },
    "climate": {
        "state": str,
        "temperature": float,
        "target_temperature": float,  # alias used by ENTITY_SNAPSHOT_ATTRS
        "target_temp_high": float,
        "target_temp_low": float,
        "humidity": float,
        "hvac_mode": str,
        "preset_mode": str,
        "fan_mode": str,
        "swing_mode": str,
        "swing_horizontal_mode": str,
    },
    "fan": {
        "state": str,
        "percentage": int,
        "preset_mode": str,
        "oscillating": bool,
        "direction": str,
    },
    "cover": {
        "state": str,
        "current_position": int,  # HA scene snapshot attribute name
        "position": int,  # service-call alias — normalized to current_position
        "current_tilt_position": int,
        "tilt_position": int,  # service-call alias — normalized to current_tilt_position
    },
}

# Ensure domain schemas stay in sync with the entity_capabilities module.
assert set(DOMAIN_STATE_SCHEMAS) == SCENE_CAPABLE_DOMAINS, (
    f"DOMAIN_STATE_SCHEMAS keys {set(DOMAIN_STATE_SCHEMAS)} != "
    f"SCENE_CAPABLE_DOMAINS {SCENE_CAPABLE_DOMAINS}"
)

# Common scene intent keywords -> domain-specific state defaults.
# These fill in attributes the LLM omitted.
SCENE_INTENT_PRESETS: dict[str, dict[str, dict[str, Any]]] = {
    "cozy": {
        "light": {"state": "on", "brightness": 51, "color_temp": 400},
        "cover": {"current_position": 30},
    },
    "bright": {
        "light": {"state": "on", "brightness": 255, "color_temp": 250},
    },
    "movie": {
        "light": {"state": "on", "brightness": 25},
        "cover": {"current_position": 0},
        "media_player": {"state": "on"},
    },
    "sleep": {
        "light": {"state": "off"},
        "cover": {"current_position": 0},
    },
    "morning": {
        "light": {"state": "on", "brightness": 200, "color_temp": 300},
        "cover": {"current_position": 100},
    },
    "night": {
        "light": {"state": "on", "brightness": 25},
        "cover": {"current_position": 0},
    },
    "relax": {
        "light": {"state": "on", "brightness": 80, "color_temp": 370},
    },
    "work": {
        "light": {"state": "on", "brightness": 255, "color_temp": 230},
    },
}

BRIGHTNESS_RANGE = (0, 255)
# No color_temp clamp — HA lights have per-entity min/max mireds (e.g. 153-500
# for Hue, up to 588+ for warmer bulbs).  We only reject non-positive values;
# HA enforces the entity-specific range when the scene is applied.
_COLOR_TEMP_MIN = 1
PERCENTAGE_RANGE = (0, 100)
POSITION_RANGE = (0, 100)
_VOLUME_RANGE = (0.0, 1.0)

# Attributes that are IDENTIFIERS, not display text. A media-source URI or a
# signed stream URL routinely passes 200 characters, and a truncated one is not
# a shorter identifier -- it is one ``play_media`` cannot resolve. Bounded
# separately, and REFUSED past the bound rather than trimmed.
_IDENTIFIER_ATTRS: frozenset[str] = frozenset({"media_content_id"})
_MAX_IDENTIFIER_LEN = 2048

# Guard rails
# Bounds the YAML a single scene can write. Set where a whole-house "Good
# Night" still fits: those routinely pass 50 lights, switches and covers, and
# refusing one is refusing the scene a homeowner is most likely to ask for.
# One constant for both gates -- ``validate_scene_security`` runs at SAVE and
# this one at propose, so a cap raised here alone gets a scene proposed, its
# card accepted, and the write refused.
MAX_SCENE_ENTITIES = 250
_MAX_STATE_VALUE_LEN = 200  # max length for string state values


def coerce_value(value: Any, expected_type: type) -> Any:
    """Coerce a value to the expected type, or return None on failure.

    Booleans are rejected for numeric types (``isinstance(True, int)`` is
    true in Python, but ``brightness: true`` is a malformed payload, not 1).

    For ``list`` types, only actual lists/tuples are accepted — string
    coercion (e.g. ``list("abc")``) would silently produce garbage so we
    reject it instead.
    """
    # Reject bools for int/float — they pass isinstance but are not
    # meaningful numeric scene values.
    if isinstance(value, bool) and expected_type in (int, float):
        return None
    # ``bool(value)`` is truthiness, and every non-empty string is truthy, so
    # a model quoting "false" would mute the speaker it meant to unmute.
    # Parse the spellings outright and refuse the rest.
    if expected_type is bool and not isinstance(value, bool):
        if isinstance(value, int):
            return value == 1 if value in (0, 1) else None
        if isinstance(value, str):
            return _BOOL_STRINGS.get(value.strip().casefold())
        return None
    if isinstance(value, expected_type):
        # Reject non-finite floats even if already the right type
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    # list("string") iterates chars — never what we want.  Accept tuples only.
    if expected_type is list:
        if isinstance(value, tuple):
            return list(value)
        return None
    # Reject bools for str — str(True) produces "True" which is garbage for
    # source, hvac_mode, preset_mode, etc.  Boolean states are handled
    # separately before coercion via the on/off special case.
    # Also reject containers — str([...]) produces garbage like "['on']".
    if expected_type is str and isinstance(value, (bool, list, tuple, dict, set)):
        return None
    try:
        result = expected_type(value)
    except (
        TypeError,
        ValueError,
        OverflowError,
    ):
        # ``OverflowError``: an unbounded Python int -- ``json.loads`` builds
        # one from any run of digits -- has no float to convert to.
        return None
    # Reject non-finite floats (nan, inf) — not meaningful HA targets.
    if isinstance(result, float) and not math.isfinite(result):
        return None
    return result


# Spellings a model writes for a boolean attribute (``is_volume_muted``,
# ``oscillating``). Anything outside this table is refused rather than guessed.
_BOOL_STRINGS: dict[str, bool] = {
    "true": True,
    "false": False,
    "yes": True,
    "no": False,
    "on": True,
    "off": False,
    "1": True,
    "0": False,
}


def _strip_percent(value: Any) -> Any:
    """Drop a trailing ``%`` so ``"50%"`` coerces like ``50``.

    Models quote the percentage back as the user typed it. Everything else
    about the value is ``coerce_value``'s problem.
    """
    if isinstance(value, str):
        return value.strip().rstrip("%")
    return value


def clamp(value: int | float, min_val: int | float, max_val: int | float) -> int | float:
    """Clamp a value to a range."""
    return max(min_val, min(max_val, value))


def validate_entity_states(
    entities: dict[str, dict[str, Any]],
) -> tuple[bool, str, dict[str, dict[str, Any]] | None]:
    """Validate and normalize entity state data against domain schemas.

    Includes security guard rails: entity count limit, entity ID format
    validation, and string value length capping.

    Returns (is_valid, reason, normalized_entities | None).
    """
    if not isinstance(entities, dict):
        return False, f"Entities payload must be a dict, got {type(entities).__name__}", None
    if len(entities) > MAX_SCENE_ENTITIES:
        return False, f"Scene exceeds maximum of {MAX_SCENE_ENTITIES} entities", None
    normalized: dict[str, dict[str, Any]] = {}

    for raw_entity_id, state_data in entities.items():
        # Reject non-string keys (e.g. integer keys from malformed JSON)
        if not isinstance(raw_entity_id, str):
            return (
                False,
                f"Entity ID must be a string, got {type(raw_entity_id).__name__}",
                None,
            )
        # Reject non-dict payloads (e.g. null, bare strings) before
        # iterating — malformed LLM output should not raise.
        if not isinstance(state_data, dict):
            return (
                False,
                f"State data for {raw_entity_id!r} must be a dict, got {type(state_data).__name__}",
                None,
            )

        # Lowercase so mixed-case LLM output is accepted (matches scene_utils)
        entity_id = raw_entity_id.lower()
        if not _ENTITY_ID_RE.match(entity_id):
            return False, f"Invalid entity_id format: {entity_id!r}", None

        if entity_id in normalized:
            return (
                False,
                f"Duplicate entity ID after case-folding: {entity_id!r}",
                None,
            )

        domain = entity_id.split(".")[0]
        schema = DOMAIN_STATE_SCHEMAS.get(domain)

        if schema is None:
            return (
                False,
                f"Entity {entity_id} belongs to unsupported domain '{domain}'. "
                f"Scene-capable domains: {', '.join(sorted(DOMAIN_STATE_SCHEMAS))}",
                None,
            )

        # Detect conflicting snapshot/target aliases before iteration — the
        # LLM may emit both with different values; whichever is iterated
        # last would silently win.  Coerce first so "50" vs 50 is not a
        # false conflict.  Only check aliases relevant to this domain;
        # stray keys on other domains are dropped as unsupported later.
        _DOMAIN_ALIASES: dict[str, list[tuple[str, str, type, tuple[float, float] | None]]] = {
            "cover": [
                ("current_position", "position", int, POSITION_RANGE),
                ("current_tilt_position", "tilt_position", int, POSITION_RANGE),
            ],
            "climate": [("temperature", "target_temperature", float, None)],
        }
        for canonical, alias, coerce_type, clamp_range in _DOMAIN_ALIASES.get(domain, []):
            if canonical in state_data and alias in state_data:
                canon_val = coerce_value(state_data[canonical], coerce_type)
                alias_val = coerce_value(state_data[alias], coerce_type)
                # Clamp before comparing so out-of-range duplicates that
                # normalize to the same value are not a false conflict.
                if canon_val is not None and alias_val is not None and clamp_range:
                    canon_val = coerce_type(clamp(canon_val, *clamp_range))
                    alias_val = coerce_type(clamp(alias_val, *clamp_range))
                if canon_val is not None and alias_val is not None and canon_val != alias_val:
                    return (
                        False,
                        f"Conflicting {canonical} and {alias} for {entity_id}",
                        None,
                    )

        # A light's brightness is the one attribute whose UNIT is ambiguous in
        # the payload: 0-255 is what HA stores, 0-100 is what the user said. A
        # percentage key declares the unit outright, so it wins over a
        # co-present bare ``brightness`` rather than being dropped beside it.
        # Folded in before the schema walk, which would otherwise drop the key.
        if domain == "light":
            pct_key = next((k for k in BRIGHTNESS_PCT_KEYS if k in state_data), None)
            if pct_key is not None:
                pct = coerce_value(_strip_percent(state_data[pct_key]), float)
                if pct is None:
                    return (
                        False,
                        f"Cannot coerce {pct_key}={state_data[pct_key]!r} for {entity_id}",
                        None,
                    )
                state_data = {k: v for k, v in state_data.items() if k not in BRIGHTNESS_PCT_KEYS}
                state_data["brightness"] = round(clamp(pct, *PERCENTAGE_RANGE) / 100 * 255)

        # A ``color_mode`` whose attribute is missing is worse than none:
        # reproduce_state logs and RETURNS, leaving the light untouched
        # entirely. Dropping the mode falls back to the colour that is
        # actually there, which is what the scene was asking for.
        if domain == "light" and state_data.get("color_mode") is not None:
            mode = str(state_data["color_mode"]).strip().casefold()
            needed = _COLOR_MODE_ATTRIBUTE.get(mode)
            # HA compares the mode by exact lowercase value, so "RGB" reads as
            # unrecognised and takes neither the mapped branch nor the
            # fallback -- no colour is sent at all. Recognised modes are stored
            # folded; anything else is dropped so the fallback runs.
            if mode not in _COLOURLESS_MODES and needed is None:
                _LOGGER.debug("Dropping unknown color_mode %r for %s", mode, entity_id)
                state_data = {k: v for k, v in state_data.items() if k != "color_mode"}
            elif needed is not None and state_data.get(needed) is None:
                # Worse than no mode: reproduce_state logs and RETURNS, leaving
                # the light untouched. Falling back to the colour that is
                # actually there is what the scene was asking for.
                _LOGGER.debug(
                    "Dropping color_mode %s for %s: no %s to reproduce it with",
                    mode,
                    entity_id,
                    needed,
                )
                state_data = {k: v for k, v in state_data.items() if k != "color_mode"}
            else:
                state_data = {**state_data, "color_mode": mode}

        clean: dict[str, Any] = {}
        for attr, value in state_data.items():
            # ``str(None)`` is "None", which reaches the service as a literal.
            # reproduce_state skips a None attribute, so dropping it here says
            # the same thing. ``state`` is required and falls through to the
            # missing-state check rather than being silently dropped.
            if value is None and attr != "state":
                _LOGGER.debug("Dropping null attribute %s for %s", attr, entity_id)
                continue
            # Normalize service-call aliases to canonical HA scene attributes
            if attr == "position":
                attr = "current_position"
            elif attr == "tilt_position":
                attr = "current_tilt_position"
            elif attr == "target_temperature":
                attr = "temperature"

            if attr not in schema:
                _LOGGER.debug("Ignoring unsupported attribute %s for domain %s", attr, domain)
                continue

            # HA scene states are always strings — coerce bools before
            # generic str() which would produce "True"/"False".
            # Covers use open/closed; other domains use on/off.
            if attr == "state" and isinstance(value, bool):
                if domain == "cover":
                    value = "open" if value else "closed"
                else:
                    value = "on" if value else "off"

            coerced = coerce_value(value, schema[attr])
            if coerced is None:
                return (
                    False,
                    f"Cannot coerce {attr}={value!r} to {schema[attr].__name__} for {entity_id}",
                    None,
                )
            # Validate color list shape, element types, and ranges
            if attr in _COLOR_LIST_SPECS and isinstance(coerced, list):
                elem_type, expected_len, default_range = _COLOR_LIST_SPECS[attr]
                if len(coerced) != expected_len:
                    return (
                        False,
                        f"{attr} must have {expected_len} elements, got {len(coerced)} for {entity_id}",
                        None,
                    )
                # Reject booleans inside color lists — same rationale as
                # scalar numeric fields: int(True) is 1 but not meaningful.
                if any(isinstance(v, bool) for v in coerced):
                    return (
                        False,
                        f"{attr} elements must be {elem_type.__name__}, not bool, for {entity_id}",
                        None,
                    )
                try:
                    coerced = [elem_type(v) for v in coerced]
                except (
                    TypeError,
                    ValueError,
                ):
                    return (
                        False,
                        f"{attr} elements must be {elem_type.__name__} for {entity_id}",
                        None,
                    )
                # Reject non-finite values (NaN, Inf) in color elements
                if any(isinstance(v, float) and not math.isfinite(v) for v in coerced):
                    return (
                        False,
                        f"{attr} contains non-finite value for {entity_id}",
                        None,
                    )
                # Clamp color elements to valid ranges
                if attr == "hs_color":
                    coerced = [
                        float(clamp(coerced[i], *_HS_COLOR_RANGES[i])) for i in range(len(coerced))
                    ]
                else:
                    lo, hi = default_range
                    coerced = [elem_type(clamp(v, lo, hi)) for v in coerced]
            # Cap string values to prevent oversized payloads
            if isinstance(coerced, str):
                if attr in _IDENTIFIER_ATTRS:
                    if len(coerced) > _MAX_IDENTIFIER_LEN:
                        return (
                            False,
                            f"{attr} exceeds {_MAX_IDENTIFIER_LEN} characters for {entity_id}",
                            None,
                        )
                elif len(coerced) > _MAX_STATE_VALUE_LEN:
                    coerced = coerced[:_MAX_STATE_VALUE_LEN]
            clean[attr] = coerced

        # Apply range clamping for known numeric attributes
        if "brightness" in clean:
            clean["brightness"] = int(clamp(clean["brightness"], *BRIGHTNESS_RANGE))
        if "color_temp" in clean:
            clean["color_temp"] = max(_COLOR_TEMP_MIN, int(clean["color_temp"]))
        if "color_temp_kelvin" in clean:
            clean["color_temp_kelvin"] = max(_COLOR_TEMP_MIN, int(clean["color_temp_kelvin"]))
        if "percentage" in clean:
            clean["percentage"] = int(clamp(clean["percentage"], *PERCENTAGE_RANGE))
        if "current_position" in clean:
            clean["current_position"] = int(clamp(clean["current_position"], *POSITION_RANGE))
        if "current_tilt_position" in clean:
            clean["current_tilt_position"] = int(
                clamp(clean["current_tilt_position"], *POSITION_RANGE)
            )
        if "volume_level" in clean:
            clean["volume_level"] = float(clamp(clean["volume_level"], *_VOLUME_RANGE))

        # ``media_player/reproduce_state.py`` calls play_media only when BOTH
        # are present, so one alone is inert: the scene reports success and
        # resumes whatever was loaded before. The caller asked for specific
        # media, so refuse rather than drop the half that was given.
        media_pair = [a for a in ("media_content_type", "media_content_id") if clean.get(a)]
        if len(media_pair) == 1:
            missing = (
                "media_content_id"
                if media_pair[0] == "media_content_type"
                else "media_content_type"
            )
            return (
                False,
                f"{media_pair[0]} needs {missing} beside it for {entity_id} — "
                "Home Assistant plays media only when both are set",
                None,
            )
        # A blank one is absent: it cannot name media, and keeping it would
        # make the pair look complete while play_media gets nothing to open.
        for attr in ("media_content_type", "media_content_id"):
            if attr in clean and not clean[attr]:
                del clean[attr]

        if "state" not in clean:
            return False, f"Entity {entity_id} missing required 'state' attribute", None

        # Normalize state to lowercase — LLMs may emit "ON", "Closed", etc.
        clean["state"] = clean["state"].lower()

        # Validate state value against per-domain allow-list
        valid_states = _DOMAIN_VALID_STATES.get(domain)
        if valid_states is not None and clean["state"] not in valid_states:
            return (
                False,
                f"Invalid state {clean['state']!r} for {domain} entity {entity_id}",
                None,
            )

        # Normalize and validate hvac_mode against known climate modes
        if "hvac_mode" in clean:
            clean["hvac_mode"] = clean["hvac_mode"].lower()
        if "hvac_mode" in clean and clean["hvac_mode"] not in _VALID_HVAC_MODES:
            return (
                False,
                f"Invalid hvac_mode {clean['hvac_mode']!r} for {entity_id}",
                None,
            )

        # Reject contradictory cover state/position — unlike lights (where
        # HA stores brightness as a resume value for off entities), covers
        # have no resume concept: state="closed" + current_position=100 is
        # genuinely nonsensical.
        if domain == "cover" and "current_position" in clean:
            pos = clean["current_position"]
            state = clean["state"]
            if state == "closed" and pos > 0:
                return (
                    False,
                    f"Contradictory state 'closed' with current_position={pos} for {entity_id}",
                    None,
                )
            if state == "open" and pos == 0:
                return (
                    False,
                    f"Contradictory state 'open' with current_position=0 for {entity_id}",
                    None,
                )

        normalized[entity_id] = clean

    return True, "valid", normalized


def apply_default_states(
    entities: dict[str, dict[str, Any]],
    intent_hint: str,
) -> dict[str, dict[str, Any]]:
    """Fill in missing state attributes based on the scene intent keyword.

    For each entity, if the LLM only returned {"state": "on"} without
    domain-specific attributes, look up the intent_hint in
    SCENE_INTENT_PRESETS and apply relevant defaults.

    Does NOT override attributes the LLM already specified.
    """
    lower_hint = intent_hint.lower()

    # Find which preset keyword matches (whole-word only so e.g. "network
    # reset" does not accidentally match "work").
    preset: dict[str, dict[str, Any]] = {}
    for keyword, defaults in SCENE_INTENT_PRESETS.items():
        if re.search(rf"\b{re.escape(keyword)}\b", lower_hint):
            preset = defaults
            break

    _OFF_STATES = frozenset({"off", "closed"})

    result: dict[str, dict[str, Any]] = {}
    for entity_id, state_data in entities.items():
        # Skip malformed entries — validate_entity_states will reject them.
        if not isinstance(entity_id, str) or not isinstance(state_data, dict):
            result[entity_id] = state_data
            continue

        domain = entity_id.lower().split(".")[0]

        # Don't inject brightness/position/etc. defaults into entities
        # the LLM explicitly set to off — that creates contradictory state.
        # Boolean False is also an off state (validated as such downstream).
        entity_state = state_data.get("state")
        if entity_state is False or (
            isinstance(entity_state, str) and entity_state.lower() in _OFF_STATES
        ):
            result[entity_id] = state_data
            continue

        domain_defaults = dict(preset.get(domain, {})) if preset else {}

        # If the LLM already supplied a position (via either alias),
        # drop the preset's current_position to avoid alias conflicts
        # that validate_entity_states would reject.
        llm_has_position = "current_position" in state_data or "position" in state_data
        if domain == "cover" and llm_has_position:
            domain_defaults.pop("current_position", None)

        # Merge: LLM values take precedence, defaults fill gaps
        merged = {**domain_defaults, **state_data}

        # Reconcile cover state with position so the result is consistent
        # for validate_entity_states.  Three cases:
        # 1. No state from LLM — infer from position.
        # 2. Preset injected position + LLM state is open/closed and
        #    contradicts the position — fix it.
        # 3. LLM state is transitional (opening/closing) — preserve it,
        #    these are valid with any position.
        if domain == "cover":
            has_pos = "current_position" in merged or "position" in merged
            preset_injected_pos = "current_position" in domain_defaults and not llm_has_position
            if has_pos:
                raw_pos = merged.get("current_position", merged.get("position"))
                # Coerce and clamp so "0" (string) and -10 (out-of-range)
                # compare correctly before state inference.
                coerced_pos = coerce_value(raw_pos, int)
                if coerced_pos is not None:
                    pos = int(clamp(coerced_pos, *POSITION_RANGE))
                else:
                    pos = raw_pos
                inferred = "closed" if pos == 0 else "open"
                llm_state = state_data.get("state")
                if llm_state is None:
                    # Case 1: no state — infer from position
                    merged["state"] = inferred
                elif (
                    preset_injected_pos
                    and isinstance(llm_state, str)
                    and llm_state.lower() in ("open", "closed")
                ):
                    # Case 2: preset position contradicts open/closed — fix it
                    merged["state"] = inferred

        result[entity_id] = merged

    return result
