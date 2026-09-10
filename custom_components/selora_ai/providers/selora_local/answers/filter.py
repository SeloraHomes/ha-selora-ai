"""Selora AI Local — deterministic live-state filtering + domain-readout question detection.

Split out of ``local_answers_state`` in pass 2. Holds the shared
``_filtered_domain_states`` live-state primitive, the "what <category> are
<state>?" filter envelope, and the per-domain readout detectors (media_player /
valve / weather / measurement / sensor) consumed by ``local_answers_domain``.
The core scope/category/single-state detection tables still live in
``local_answers_state`` and are imported below.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

from .state import (
    _COMMAND_VERB_RE,
    _NATURAL_STATE_BY_DOMAIN,
    _answer,
    _detect_state_filter_question,
    _has_scope_qualifier,
    _safe_fname_for_prose,
)


def _detect_media_player_state_question(prompt: str) -> str | None:
    """Return the room-qualifier phrase for a media_player playback-state question ("is the living room media player playing, paused, or stopped?"), or ``None`` when ``prompt`` isn't that shape."""
    if not prompt:
        return None
    stripped = prompt.strip()
    if not _MEDIA_PLAYER_PLAYBACK_WORD_RE.search(stripped):
        return None
    m = _MEDIA_PLAYER_STATE_QUESTION_RE.match(stripped)
    if m is None:
        return None
    return m.group(1).strip().lower()


def _detect_measurement_value_question(prompt: str) -> set[str] | None:
    """Return the salient subject tokens if ``prompt`` is a numeric sensor-value question ("what is the battery level of the motion sensor?"), else ``None``."""
    if not prompt:
        return None
    text = prompt.strip()
    if not _MEASUREMENT_VALUE_RE.search(text):
        return None
    tokens = [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]
    if not any(t in _MEASUREMENT_VALUE_KEYWORDS for t in tokens):
        return None
    subject = {t for t in tokens if t not in _MEASUREMENT_QUESTION_STOPWORDS}
    if not (subject & _MEASUREMENT_VALUE_KEYWORDS):
        return None
    return subject


def _detect_polar_valve_state_question(prompt: str) -> str | None:
    """Return the asked-for state word ("on"/"off"/"open"/"closed"/…) if ``prompt`` is a yes/no question about valve/sprinkler state."""
    if not prompt:
        return None
    if _has_scope_qualifier(prompt):
        return None
    m = _POLAR_VALVE_STATE_QUESTION_RE.search(prompt.lower())
    if m is None:
        return None
    target = m.group(1).lower()
    if target not in _VALVE_NATURAL_STATE:
        return None
    return target


def _detect_weather_question(prompt: str) -> bool:
    """Return ``True`` when ``prompt`` is a weather/forecast question."""
    if not prompt:
        return False
    low = prompt.strip().lower()
    if _WEATHER_QUESTION_TERMS_RE.search(low) is None:
        return False
    if "?" in low:
        return True
    first = re.split(r"[^a-z0-9]+", low, maxsplit=1)[0]
    return first in _WEATHER_QUESTION_OPENERS


def _detect_sensor_value_question(prompt: str) -> str | None:
    """Return the measurement word (``"temperature"`` / ``"humidity"``) if ``prompt`` asks for a sensor reading."""
    if not prompt:
        return None
    msg = prompt.lower()
    # Commands ("set the temperature to 22") are not read-only queries.
    if _COMMAND_VERB_RE.search(msg):
        return None
    is_question = "?" in msg or msg.lstrip().startswith(("what", "how"))
    if not is_question:
        return None
    if _SENSOR_HUMIDITY_RE.search(msg):
        return "humidity"
    if _SENSOR_TEMPERATURE_RE.search(msg):
        return "temperature"
    return None


def _sensor_prompt_room_tokens(prompt: str) -> list[str]:
    """Room/qualifier tokens left after stripping the measurement and grammar words — e.g."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", prompt.lower()) if t]
    return [t for t in tokens if t not in _SENSOR_PROMPT_STOPWORDS]


# Media-player playback-state question
_MEDIA_PLAYER_STATE_QUESTION_RE = re.compile(
    r"^\s*is\s+(?:the\s+|a\s+|an\s+|my\s+|our\s+)?(.*?)\bmedia[ _]player\b",
    re.IGNORECASE,
)

_MEDIA_PLAYER_PLAYBACK_WORD_RE = re.compile(
    r"\b(playing|paused|stopped|idle|buffering)\b", re.IGNORECASE
)

# Map a media_player live HA state to the playback word the question framed it in ("playing, paused, or stopped?").
_MEDIA_PLAYER_STATE_PROSE: dict[str, str] = {
    "playing": "playing",
    "paused": "paused",
    "buffering": "playing",
    "idle": "stopped",
    "off": "stopped",
    "standby": "stopped",
    "on": "playing",
}

# Generic device nouns dropped when deriving the room qualifier from a media-player question's subject ("living room media player" → {living, room}); the bare "media player" then carries no qualifier tokens.
_MEDIA_PLAYER_SUBJECT_STOPWORDS: frozenset[str] = frozenset(
    {"the", "a", "an", "my", "our", "your", "media", "player", "speaker", "tv"}
)

# Measurement-value question ("what is the battery level of X?")
_MEASUREMENT_VALUE_KEYWORDS = frozenset(
    {
        "battery",
        "temperature",
        "humidity",
        "brightness",
        "illuminance",
        "luminance",
        "power",
        "energy",
        "voltage",
        "pressure",
        "moisture",
        "co2",
        "pm25",
        "signal",
    }
)

_MEASUREMENT_QUESTION_STOPWORDS = frozenset(
    {
        "what",
        "whats",
        "is",
        "are",
        "the",
        "of",
        "a",
        "an",
        "my",
        "our",
        "your",
        "how",
        "much",
        "level",
        "levels",
        "value",
        "reading",
        "currently",
        "current",
        "right",
        "now",
        "please",
        "tell",
        "show",
        "give",
        "me",
        "for",
        "on",
        "at",
        "do",
        "i",
        "have",
        "sensor",
        "sensors",
        "device",
        "s",
    }
)

_MEASUREMENT_VALUE_RE = re.compile(
    r"^\s*(?:what(?:'?s| is| are)?|how\s+much\s+is|tell\s+me|show\s+me|give\s+me)\b",
    re.IGNORECASE,
)

# Polar valve/sprinkler state question ("are the sprinklers on?")
_POLAR_VALVE_STATE_QUESTION_RE = re.compile(
    r"\b(?:are|is)\s+(?:the\s+|my\s+|our\s+|a\s+|an\s+|any\s+|all\s+(?:of\s+)?"
    r"(?:the\s+|my\s+|our\s+)?)?"
    r"(?:sprinklers?|valves?|irrigation(?:\s+system)?|watering\s+system)\s+"
    r"(?:currently\s+|still\s+)?(on|off|open|closed|running|active)\b",
    re.IGNORECASE,
)

# Natural word the user typed → the set of HA valve states that count as a match.
_VALVE_NATURAL_STATE: dict[str, set[str]] = {
    "on": {"open"},
    "open": {"open"},
    "running": {"open"},
    "active": {"open"},
    "off": {"closed"},
    "closed": {"closed"},
}

# Raw HA valve state → the natural word used in the rendered prose.
_VALVE_STATE_PROSE: dict[str, str] = {
    "open": "open",
    "closed": "closed",
    "opening": "opening",
    "closing": "closing",
}

# Weather question detection (deterministic answer)
_WEATHER_QUESTION_TERMS_RE = re.compile(
    r"\b(weather|forecast|sunny|cloudy|overcast|rain|rainy|raining|"
    r"snow|snowy|snowing|storm|stormy|sunshine|fog|foggy|windy|hail|"
    r"drizzle|pouring|sleet)\b",
    re.IGNORECASE,
)

_WEATHER_QUESTION_OPENERS = frozenset(
    {
        "is",
        "are",
        "was",
        "will",
        "what",
        "whats",
        "how",
        "hows",
        "does",
        "do",
        "tell",
        "any",
        "should",
    }
)

# Raw HA weather condition state → readable prose.
_WEATHER_CONDITION_PROSE: dict[str, str] = {
    "clear-night": "clear",
    "cloudy": "cloudy",
    "exceptional": "exceptional",
    "fog": "foggy",
    "hail": "hailing",
    "lightning": "stormy",
    "lightning-rainy": "stormy with rain",
    "partlycloudy": "partly cloudy",
    "pouring": "pouring rain",
    "rainy": "rainy",
    "snowy": "snowy",
    "snowy-rainy": "snowy and rainy",
    "sunny": "sunny",
    "windy": "windy",
    "windy-variant": "windy",
}

# Sensor-value query ("how warm is it?", "what's the humidity?")
_SENSOR_TEMPERATURE_RE = re.compile(
    r"\b(?:temperature|temp|how\s+(?:warm|hot|cold))\b|\b(?:warm|hot|cold)\b",
    re.IGNORECASE,
)

_SENSOR_HUMIDITY_RE = re.compile(r"\bhumid(?:ity)?\b", re.IGNORECASE)

# Words to drop when deriving the room qualifier / placeholder slug from a sensor-value prompt.
_SENSOR_PROMPT_STOPWORDS: frozenset[str] = frozenset(
    {
        "what",
        "whats",
        "what's",
        "is",
        "the",
        "in",
        "how",
        "warm",
        "hot",
        "cold",
        "temperature",
        "temp",
        "humidity",
        "humid",
        "a",
        "an",
        "my",
        "our",
        "it",
        "s",
        "do",
        "i",
        "have",
        "right",
        "now",
        "currently",
        "of",
        "are",
        "and",
    }
)


class _AnswersFilterMixin:
    """Selora AI Local — live-state filtering + "what <category> are <state>?" answers."""

    def _filtered_domain_states(self, domain: str) -> list[Any]:
        """Return ``hass.states`` entries for ``domain`` with the same filtering the rest of the integration applies before showing entities to the model or the user."""
        if not self._hass:
            return []
        all_states = self._hass.states.async_all()
        domain_states = [
            s for s in all_states if "." in s.entity_id and s.entity_id.split(".", 1)[0] == domain
        ]
        if not domain_states:
            return []
        from ....entity_filter import EntityFilter, resolve_ignored_entity_ids

        ignored = resolve_ignored_entity_ids(self._hass)
        ef = EntityFilter(self._hass, [s.entity_id for s in domain_states])
        return [
            s for s in domain_states if s.entity_id not in ignored and ef.is_active(s.entity_id)
        ]

    def _snapshot_domain_states(self, domain: str) -> list[Any]:
        """State-like fallback for ``_filtered_domain_states`` built from the per-turn entity snapshot injected via ``set_chat_context``."""
        result: list[Any] = []
        for e in self._current_entities():
            if not isinstance(e, dict):
                continue
            eid = e.get("entity_id", "")
            if not (isinstance(eid, str) and "." in eid and eid.split(".", 1)[0] == domain):
                continue
            attrs = e.get("attributes") if isinstance(e.get("attributes"), dict) else {}
            result.append(
                SimpleNamespace(
                    entity_id=eid,
                    attributes=attrs,
                    state=e.get("state"),
                )
            )
        return result

    def _maybe_state_filter_envelope(self) -> str | None:
        """If the current user turn is a "what <category> are <state>?" question, return a JSON envelope answering it deterministically from ``hass.states``."""
        # Gate on the per-turn snapshot, not the live ``_call_kind``.
        if self._chat_kind.get() != "chat_answer":
            return None
        detected = _detect_state_filter_question(self._user_message_raw.get() or "")
        if detected is None:
            return None
        domain, target_state, label_plural, label_singular = detected
        # Use LIVE ``hass.states`` to compute the matching set — NOT a frozen baseline.
        states = self._filtered_domain_states(domain)
        accepted_states = _NATURAL_STATE_BY_DOMAIN.get(domain, {}).get(target_state, set())
        matched: list[tuple[str, str]] = []
        for state in states:
            eid = state.entity_id
            # Skip entities whose live state is unknown/unavailable/ empty — including them in ``q`` would always over-count.
            live_state = (state.state or "").lower()
            if not live_state or live_state in ("unknown", "unavailable"):
                continue
            if live_state not in accepted_states:
                continue
            fname = (state.attributes or {}).get("friendly_name") or eid
            matched.append((eid, _safe_fname_for_prose(str(fname))))
        matched.sort(key=lambda p: p[0])
        ids = [eid for eid, _ in matched]
        marker = f"\n[[entities:{','.join(ids)}]]" if ids else ""
        if not ids:
            r_text = f"No {label_plural} are currently {target_state} — 0 of those match right now."
        elif len(ids) == 1:
            r_text = f"1 {label_singular} is {target_state}: {matched[0][1]}.{marker}"
        else:
            names = ", ".join(fname for _, fname in matched[:-1])
            names = f"{names}, and {matched[-1][1]}"
            r_text = f"{len(ids)} {label_plural} are {target_state}: {names}.{marker}"
        return _answer(r_text, ids)
