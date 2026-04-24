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

_LOGGER = logging.getLogger(__name__)

# Matches the entity-id pattern used by scene_utils.validate_scene_payload():
# domain = letters/underscores (first char) then letters/digits/underscores;
# object_id = letters/digits/underscores/hyphens.
# Input is lowercased before matching so mixed-case LLM output is accepted.
_ENTITY_ID_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z0-9][a-z0-9_-]*$")

# Expected (element_type, length, per-element (min, max)) for color list attributes.
_COLOR_LIST_SPECS: dict[str, tuple[type, int, tuple[float, float]]] = {
    "rgb_color": (int, 3, (0, 255)),
    "hs_color": (float, 2, (0.0, 360.0)),  # hue 0-360, sat 0-100 — clamped per-element below
    "xy_color": (float, 2, (0.0, 1.0)),
}
# hs_color has different ranges per element; handled specially in validation.
_HS_COLOR_RANGES: tuple[tuple[float, float], tuple[float, float]] = (
    (0.0, 360.0),  # hue
    (0.0, 100.0),  # saturation
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
DOMAIN_STATE_SCHEMAS: dict[str, dict[str, type]] = {
    "light": {
        "state": str,
        "brightness": int,
        "color_temp": int,
        "rgb_color": list,
        "hs_color": list,
        "xy_color": list,
    },
    "switch": {
        "state": str,
    },
    "media_player": {
        "state": str,
        "volume_level": float,
        "source": str,
    },
    "climate": {
        "state": str,
        "temperature": float,
        "target_temperature": float,  # alias used by ENTITY_SNAPSHOT_ATTRS
        "hvac_mode": str,
        "preset_mode": str,
    },
    "fan": {
        "state": str,
        "percentage": int,
        "preset_mode": str,
    },
    "cover": {
        "state": str,
        "current_position": int,  # HA scene snapshot attribute name
        "position": int,  # service-call alias — normalized to current_position
    },
}

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

_BRIGHTNESS_RANGE = (0, 255)
# No color_temp clamp — HA lights have per-entity min/max mireds (e.g. 153-500
# for Hue, up to 588+ for warmer bulbs).  We only reject non-positive values;
# HA enforces the entity-specific range when the scene is applied.
_COLOR_TEMP_MIN = 1
_PERCENTAGE_RANGE = (0, 100)
_POSITION_RANGE = (0, 100)
_VOLUME_RANGE = (0.0, 1.0)

# Guard rails
_MAX_SCENE_ENTITIES = 50  # reject scenes with more entities than this
_MAX_STATE_VALUE_LEN = 200  # max length for string state values


def _coerce_value(value: Any, expected_type: type) -> Any:
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
    except (TypeError, ValueError):
        return None
    # Reject non-finite floats (nan, inf) — not meaningful HA targets.
    if isinstance(result, float) and not math.isfinite(result):
        return None
    return result


def _clamp(value: int | float, min_val: int | float, max_val: int | float) -> int | float:
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
    if len(entities) > _MAX_SCENE_ENTITIES:
        return False, f"Scene exceeds maximum of {_MAX_SCENE_ENTITIES} entities", None
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
            # Unknown domain -- pass through but still require 'state' and
            # normalize it to a string (matches scene_utils expectations).
            if "state" not in state_data:
                return False, f"Entity {entity_id} missing required 'state' attribute", None
            passthrough = dict(state_data)
            raw_state = passthrough["state"]
            if isinstance(raw_state, bool):
                passthrough["state"] = "on" if raw_state else "off"
            elif isinstance(raw_state, (int, float)):
                passthrough["state"] = str(raw_state)
            elif not isinstance(raw_state, str):
                return (
                    False,
                    f"Invalid state value for {entity_id}: expected a string, bool, or number",
                    None,
                )
            # Apply the same string-length cap as known domains
            for key, val in passthrough.items():
                if isinstance(val, str) and len(val) > _MAX_STATE_VALUE_LEN:
                    passthrough[key] = val[:_MAX_STATE_VALUE_LEN]
            normalized[entity_id] = passthrough
            continue

        # Detect conflicting snapshot/target aliases before iteration — the
        # LLM may emit both with different values; whichever is iterated
        # last would silently win.  Coerce first so "50" vs 50 is not a
        # false conflict.  Only check aliases relevant to this domain;
        # stray keys on other domains are dropped as unsupported later.
        _DOMAIN_ALIASES: dict[str, list[tuple[str, str, type, tuple[float, float] | None]]] = {
            "cover": [("current_position", "position", int, _POSITION_RANGE)],
            "climate": [("temperature", "target_temperature", float, None)],
        }
        for canonical, alias, coerce_type, clamp_range in _DOMAIN_ALIASES.get(domain, []):
            if canonical in state_data and alias in state_data:
                canon_val = _coerce_value(state_data[canonical], coerce_type)
                alias_val = _coerce_value(state_data[alias], coerce_type)
                # Clamp before comparing so out-of-range duplicates that
                # normalize to the same value are not a false conflict.
                if canon_val is not None and alias_val is not None and clamp_range:
                    canon_val = coerce_type(_clamp(canon_val, *clamp_range))
                    alias_val = coerce_type(_clamp(alias_val, *clamp_range))
                if canon_val is not None and alias_val is not None and canon_val != alias_val:
                    return (
                        False,
                        f"Conflicting {canonical} and {alias} for {entity_id}",
                        None,
                    )

        clean: dict[str, Any] = {}
        for attr, value in state_data.items():
            # Normalize service-call aliases to canonical HA scene attributes
            if attr == "position":
                attr = "current_position"
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

            coerced = _coerce_value(value, schema[attr])
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
                except (TypeError, ValueError):
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
                        float(_clamp(coerced[i], *_HS_COLOR_RANGES[i])) for i in range(len(coerced))
                    ]
                else:
                    lo, hi = default_range
                    coerced = [elem_type(_clamp(v, lo, hi)) for v in coerced]
            # Cap string values to prevent oversized payloads
            if isinstance(coerced, str) and len(coerced) > _MAX_STATE_VALUE_LEN:
                coerced = coerced[:_MAX_STATE_VALUE_LEN]
            clean[attr] = coerced

        # Apply range clamping for known numeric attributes
        if "brightness" in clean:
            clean["brightness"] = int(_clamp(clean["brightness"], *_BRIGHTNESS_RANGE))
        if "color_temp" in clean:
            clean["color_temp"] = max(_COLOR_TEMP_MIN, int(clean["color_temp"]))
        if "percentage" in clean:
            clean["percentage"] = int(_clamp(clean["percentage"], *_PERCENTAGE_RANGE))
        if "current_position" in clean:
            clean["current_position"] = int(_clamp(clean["current_position"], *_POSITION_RANGE))
        if "volume_level" in clean:
            clean["volume_level"] = float(_clamp(clean["volume_level"], *_VOLUME_RANGE))

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
                coerced_pos = _coerce_value(raw_pos, int)
                if coerced_pos is not None:
                    pos = int(_clamp(coerced_pos, *_POSITION_RANGE))
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
