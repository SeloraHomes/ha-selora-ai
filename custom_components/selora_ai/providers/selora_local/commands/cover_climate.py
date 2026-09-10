"""Selora AI Local — deterministic cover / climate command handlers."""

from __future__ import annotations

import json
import re
from typing import Any

# Cover command override.
_SELORA_LOCAL_COVER_NOUN_RE = re.compile(
    r"\b(?:garage|curtain|curtains|blind|blinds|shade|shades|shutter|"
    r"shutters|drape|drapes|awning|awnings|cover|covers|gate|gates)\b",
    re.IGNORECASE,
)

# Close verbs (checked before open so "close" never reads as "open").
_SELORA_LOCAL_COVER_CLOSE_RE = re.compile(
    r"\b(?:close|closing|closed|shut|shutting|lower|lowering|drop|dropping)\b",
    re.IGNORECASE,
)

# Open verbs ("raise/lift/roll up" are the natural-language open verbs).
_SELORA_LOCAL_COVER_OPEN_RE = re.compile(
    r"\b(?:open|opening|opened|raise|raising|lift|lifting|roll\s+up)\b",
    re.IGNORECASE,
)

_SELORA_LOCAL_COVER_STOP_RE = re.compile(
    r"\b(?:stop|stopping|halt|pause)\b",
    re.IGNORECASE,
)

# CoverEntityFeature.SET_POSITION bit — a cover only honours ``set_cover_position`` when this flag is present in supported_features.
_SELORA_LOCAL_COVER_SET_POSITION_FEATURE = 4

# Thermostat/temperature signal for the climate command override.
_SELORA_LOCAL_CLIMATE_SIGNAL_RE = re.compile(
    r"\b(?:thermostat|heating|heater|heat|temperature|temp|climate|"
    r"warmer|cooler|colder|hotter|a/?c|air\s*con(?:ditioner|ditioning)?|"
    r"degrees?)\b",
    re.IGNORECASE,
)

# The NOUN subset of the climate signal — an explicit thermostat / heat / temperature reference, excluding the bare comparatives (warmer / cooler / …) and the unit word (degrees).
_SELORA_LOCAL_CLIMATE_NOUN_RE = re.compile(
    r"\b(?:thermostat|heating|heater|heat|temperature|temp|climate|"
    r"a/?c|air\s*con(?:ditioner|ditioning)?)\b",
    re.IGNORECASE,
)

# Immediate-conditional climate gate.
_SELORA_LOCAL_CLIMATE_CONDITION_RE = re.compile(
    r"\b(?P<dir>above|below|over|under|"
    r"greater\s+than|less\s+than|more\s+than|"
    r"hotter\s+than|colder\s+than|warmer\s+than|cooler\s+than|"
    r"higher\s+than|lower\s+than)\s+(?P<val>-?\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)

_SELORA_LOCAL_CLIMATE_CONDITION_ABOVE = frozenset(
    {"above", "over", "greater than", "more than", "hotter than", "warmer than", "higher than"}
)


class _CommandsCoverClimateMixin:
    """Selora AI Local — deterministic cover / climate command handlers (mixed into ``SeloraLocalProvider``)."""

    @staticmethod
    def _cover_position_pct(msg: str) -> int | None:
        """Recover an explicit target cover position (0-100) from a command turn ("set the blinds to 50%", "open the curtains to 30 percent")."""
        num = re.search(r"(\d{1,3})\s*(?:percent|%)", msg)
        if num is not None:
            return max(0, min(100, int(num.group(1))))
        return None

    def _cover_entities_in_named_area(self, msg: str, covers: list[Any]) -> list[str]:
        """Return cover entity_ids whose HA area name appears in ``msg``."""
        if not self._hass:
            return []
        try:
            from homeassistant.helpers import area_registry as ar
            from homeassistant.helpers import device_registry as dr
            from homeassistant.helpers import entity_registry as er
        except Exception:  # noqa: BLE001 — registries are best-effort here
            return []
        area_reg = ar.async_get(self._hass)
        ent_reg = er.async_get(self._hass)
        dev_reg = dr.async_get(self._hass)
        matched_area_ids: set[str] = set()
        for area in area_reg.async_list_areas():
            names: list[str] = [area.name]
            names.extend(getattr(area, "aliases", None) or ())
            for name in names:
                nl = str(name or "").strip().lower()
                if len(nl) >= 3 and re.search(rf"\b{re.escape(nl)}\b", msg):
                    matched_area_ids.add(area.id)
                    break
        if not matched_area_ids:
            return []
        result: list[str] = []
        for state in covers:
            entry = ent_reg.async_get(state.entity_id)
            area_id = entry.area_id if entry else None
            if not area_id and entry and entry.device_id:
                dev = dev_reg.async_get(entry.device_id)
                area_id = dev.area_id if dev else None
            if area_id and area_id in matched_area_ids:
                result.append(state.entity_id)
        return result

    def _resolve_cover_targets(self, msg: str, covers: list[Any]) -> list[tuple[str, str]]:
        """Resolve the cover target(s) of a command turn."""

        def _fname(state: Any) -> str:
            return (
                str((state.attributes or {}).get("friendly_name") or "").strip() or state.entity_id
            )

        # 1. Named-entity match (friendly_name / slug longest substring).
        best_eid, best_fname, _best_state, _best_len = self._match_named_state(msg, covers)
        if best_eid is not None:
            return [(best_eid, best_fname or best_eid)]

        # 2.
        area_eids = self._cover_entities_in_named_area(msg, covers)
        if area_eids:
            by_id = {s.entity_id: s for s in covers}
            return [(eid, _fname(by_id[eid])) for eid in area_eids if eid in by_id]

        # 3.
        def _stem(tok: str) -> str:
            return tok[:-1] if len(tok) > 3 and tok.endswith("s") else tok

        msg_tokens = {_stem(t) for t in re.split(r"[^a-z0-9]+", msg) if len(t) >= 3}
        best_overlap = 0
        for state in covers:
            name_tokens: set[str] = set()
            for src in (
                _fname(state).lower(),
                state.entity_id.split(".", 1)[-1].replace("_", " "),
            ):
                name_tokens |= {_stem(t) for t in src.split() if len(t) >= 3}
            overlap = len(name_tokens & msg_tokens)
            if overlap > best_overlap:
                best_overlap = overlap
                best_eid = state.entity_id
                best_fname = _fname(state)
        if best_overlap >= 1 and best_eid is not None:
            return [(best_eid, best_fname)]

        # 4.
        if len(covers) == 1:
            return [(covers[0].entity_id, _fname(covers[0]))]
        return []

    def _maybe_cover_envelope(self) -> str | None:
        """Deterministically build a ``cover`` command envelope when the current turn is a ``chat_command`` / ``chat_clarification`` that names a real cover and carries an open / close / position signal."""
        # Use the race-robust ``_current_chat_kind`` / ``_current_user_message`` accessors (ContextVar with an instance-attribute fallback) rather than the bare ContextVars: the conversion pass sometimes runs after the ContextVar has been reset (see the propagation-race note in ``set_chat_context``), which would silently skip this override.
        if self._current_chat_kind() in ("chat_automation", "chat_utilities"):
            return None
        raw = self._current_user_message() or ""
        msg = raw.lower().strip()
        if not msg:
            return None
        # A read-only status question ("is the garage door open?") is not a command even if it slips through on a command-kind turn.
        if "?" in msg or msg.split()[0] in ("is", "are", "was", "were", "did", "does"):
            return None
        if not _SELORA_LOCAL_COVER_NOUN_RE.search(msg):
            return None
        covers = self._command_domain_states("cover")
        if not covers:
            return None
        pct = self._cover_position_pct(msg)
        close = bool(_SELORA_LOCAL_COVER_CLOSE_RE.search(msg))
        open_ = bool(_SELORA_LOCAL_COVER_OPEN_RE.search(msg))
        stop = bool(_SELORA_LOCAL_COVER_STOP_RE.search(msg))
        if pct is None and not (close or open_ or stop):
            return None
        targets = self._resolve_cover_targets(msg, covers)
        if not targets:
            return None
        by_id = {s.entity_id: s for s in covers}

        def _supports_position(eid: str) -> bool:
            state = by_id.get(eid)
            if state is None:
                return False
            try:
                features = int((state.attributes or {}).get("supported_features") or 0)
            except (TypeError, ValueError):
                return False
            return bool(features & _SELORA_LOCAL_COVER_SET_POSITION_FEATURE)

        calls: list[dict[str, Any]] = []
        verb_word = ""
        for eid, _fname in targets:
            if pct is not None and _supports_position(eid):
                calls.append(
                    {
                        "service": "cover.set_cover_position",
                        "target": {"entity_id": eid},
                        "data": {"position": pct},
                    }
                )
                verb_word = f"Setting {{}} to {pct}%"
            elif pct is not None:
                # No SET_POSITION support: a non-zero position opens the cover, zero closes it (the device only honours open/close).
                if pct <= 0:
                    calls.append({"service": "cover.close_cover", "target": {"entity_id": eid}})
                    verb_word = "Closing {}"
                else:
                    calls.append({"service": "cover.open_cover", "target": {"entity_id": eid}})
                    verb_word = "Opening {}"
            elif close and not open_:
                calls.append({"service": "cover.close_cover", "target": {"entity_id": eid}})
                verb_word = "Closing {}"
            elif open_ and not close:
                calls.append({"service": "cover.open_cover", "target": {"entity_id": eid}})
                verb_word = "Opening {}"
            elif stop:
                calls.append({"service": "cover.stop_cover", "target": {"entity_id": eid}})
                verb_word = "Stopping {}"
            else:
                # Ambiguous (both open and close, or neither) — defer.
                return None
        if not calls:
            return None
        if len(targets) == 1:
            response = verb_word.format(targets[0][1]) + "."
        else:
            response = verb_word.format("the covers") + "."
        return json.dumps({"intent": "command", "response": response, "calls": calls})

    @staticmethod
    def _climate_degree_magnitude(msg: str) -> float | None:
        """Recover the size of a relative thermostat adjustment, in degrees."""
        word_numbers: dict[str, float] = {
            "a couple": 2.0,
            "a few": 3.0,
            "one": 1.0,
            "two": 2.0,
            "three": 3.0,
            "four": 4.0,
            "five": 5.0,
            "six": 6.0,
            "seven": 7.0,
            "eight": 8.0,
            "nine": 9.0,
            "ten": 10.0,
        }
        m = re.search(r"\bby\s+(\d+(?:\.\d+)?)\b", msg)
        if m is not None:
            return float(m.group(1))
        m = re.search(r"(\d+(?:\.\d+)?)\s*degrees?\b", msg)
        if m is not None:
            return float(m.group(1))
        for word, value in word_numbers.items():
            if re.search(rf"\b{re.escape(word)}\s+(?:of\s+)?degrees?\b", msg):
                return value
        for word, value in word_numbers.items():
            if re.search(rf"\b{re.escape(word)}\b", msg):
                return value
        m = re.search(r"\b(\d+(?:\.\d+)?)\b", msg)
        if m is not None:
            return float(m.group(1))
        return None

    def _maybe_climate_envelope(self) -> str | None:
        """Deterministically build a ``climate`` command envelope when the current turn is a ``chat_command`` thermostat set/adjust."""
        msg = self._command_message()
        if msg is None:
            return None
        # Only engage on a thermostat/temperature command turn.
        if not _SELORA_LOCAL_CLIMATE_SIGNAL_RE.search(msg):
            return None
        climate_states = self._filtered_domain_states("climate")
        if not climate_states:
            return None
        # Resolve the target climate entity.
        target_eid, target_fname, target_state, best_len = self._match_named_state(
            msg, climate_states
        )
        if target_eid is not None:
            target_fname = target_fname or target_eid
        if target_eid is None:
            # Room-name fallback: the turn may name a room ("the guest room") without the full device name ("guest room thermostat"), so the friendly_name / slug substring match above misses.
            room_hit = max(
                (room for room in self._PRESENCE_ROOM_WORDS if room in msg),
                key=len,
                default=None,
            )
            if room_hit is not None:
                room_slug = room_hit.replace(" ", "_")
                room_candidates = [
                    state
                    for state in climate_states
                    if room_hit in str((state.attributes or {}).get("friendly_name") or "").lower()
                    or room_slug in state.entity_id.split(".", 1)[-1]
                ]
                if len(room_candidates) == 1:
                    target_state = room_candidates[0]
                    target_eid = target_state.entity_id
                    target_fname = (
                        str((target_state.attributes or {}).get("friendly_name") or "").strip()
                        or target_eid
                    )
        if target_eid is None:
            if len(climate_states) != 1:
                return None
            target_state = climate_states[0]
            target_eid = target_state.entity_id
            target_fname = (
                str((target_state.attributes or {}).get("friendly_name") or "").strip()
                or target_eid
            )
        # Determine the setpoint.
        temperature: float | None = None
        abs_match = re.search(r"\bto\s+(\d+(?:\.\d+)?)\b", msg)
        if abs_match is not None:
            temperature = float(abs_match.group(1))
        else:
            warmer = re.search(
                r"\b(?:warmer|hotter|higher|warm\s+it\s+up|heat\s+it\s+up|"
                r"raise|increase|turn\s+\w+(?:\s+\w+)?\s+up|up)\b",
                msg,
            )
            cooler = re.search(
                r"\b(?:cooler|colder|cool\s+it\s+down|lower|decrease|reduce|"
                r"turn\s+\w+(?:\s+\w+)?\s+down|down)\b",
                msg,
            )
            if warmer is None and cooler is None:
                return None
            delta = self._climate_degree_magnitude(msg)
            # A bare comparative with NO explicit magnitude ("make it warmer") is genuinely ambiguous: the user named no specific thermostat (best_len == 0), gave no degree count, and anchored on a pronoun rather than a thermostat/temperature noun.
            if delta is None and best_len == 0 and not _SELORA_LOCAL_CLIMATE_NOUN_RE.search(msg):
                return None
            # Current setpoint as the baseline; fall back to a sensible room temperature when the entity exposes none so a relative turn always yields a concrete value.
            current: float | None = None
            attrs = (target_state.attributes or {}) if target_state is not None else {}
            for key in ("temperature", "target_temp_high", "current_temperature"):
                val = attrs.get(key)
                if isinstance(val, (int, float)):
                    current = float(val)
                    break
            if current is None:
                current = 21.0
            if delta is None:
                delta = 1.0
            sign = -1.0 if (cooler is not None and warmer is None) else 1.0
            temperature = current + sign * delta
        if temperature is None:
            return None
        # Immediate-conditional gate.
        cond = _SELORA_LOCAL_CLIMATE_CONDITION_RE.search(msg)
        if cond is not None:
            try:
                cond_threshold = float(cond.group("val"))
            except (TypeError, ValueError):
                cond_threshold = None
            if cond_threshold is not None:
                cond_attrs = (target_state.attributes or {}) if target_state is not None else {}
                measured: float | None = None
                for key in ("current_temperature", "temperature", "target_temp_high"):
                    mval = cond_attrs.get(key)
                    if isinstance(mval, (int, float)):
                        measured = float(mval)
                        break
                if measured is not None:
                    direction = " ".join(cond.group("dir").lower().split())
                    if direction in _SELORA_LOCAL_CLIMATE_CONDITION_ABOVE:
                        holds = measured > cond_threshold
                    else:
                        holds = measured < cond_threshold
                    if not holds:
                        r_text = (
                            f"{target_fname} is at {measured:g}°, which is not "
                            f"{direction} {cond_threshold:g}°, so I left the "
                            f"target temperature unchanged."
                        )
                        return json.dumps({"intent": "answer", "response": r_text})
        # Normalise to a clean integer when there's no fractional part.
        out_temp: float | int = (
            int(temperature) if float(temperature).is_integer() else round(temperature, 1)
        )
        call: dict[str, Any] = {
            "service": "climate.set_temperature",
            "target": {"entity_id": target_eid},
            "data": {"temperature": out_temp},
        }
        r_text = f"Set {target_fname} to {out_temp}°."
        return json.dumps({"intent": "command", "response": r_text, "calls": [call]})
