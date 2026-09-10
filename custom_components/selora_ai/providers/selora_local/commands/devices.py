"""Selora AI Local — deterministic light / cover / climate / scene / input_boolean command handlers."""

from __future__ import annotations

import difflib
import json
import logging
import re
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Selora AI Local — scene-activation phrasing normalisation.
_SELORA_LOCAL_SCENE_VERB_RE = re.compile(
    r"^\s*(?:please\s+)?(?:can\s+you\s+|could\s+you\s+|would\s+you\s+)?"
    r"(?:activate|run|start|set|enable|trigger|launch|switch\s+to|"
    r"go\s+to|put\s+on|turn\s+on)\b",
    re.IGNORECASE,
)
_SELORA_LOCAL_SCENE_NOISE_RE = re.compile(
    r"\b(?:the|a|an|to|scene|scenes|mode|please)\b",
    re.IGNORECASE,
)
# Concrete non-scene device-domain nouns.
_SELORA_LOCAL_NON_SCENE_DEVICE_RE = re.compile(
    r"\b(?:lights?|lamps?|switch(?:es)?|plugs?|outlets?|fans?|"
    r"lock(?:s|ed)?|covers?|blinds?|shades?|curtains?|garage|doors?|"
    r"speakers?|tv|television|thermostats?|vacuums?|valves?|sprinklers?)\b",
    re.IGNORECASE,
)


# Selora AI Local — on/off verb detectors for the deterministic input_boolean command override.
_SELORA_LOCAL_INPUT_BOOLEAN_OFF_RE = re.compile(
    r"\b(?:turn(?:ed)?\s+off|switch(?:ed)?\s+off|shut\s+off|power\s+off|"
    r"disable[ds]?|deactivate[ds]?|off)\b",
    re.IGNORECASE,
)

_SELORA_LOCAL_INPUT_BOOLEAN_ON_RE = re.compile(
    r"\b(?:turn(?:ed)?\s+on|switch(?:ed)?\s+on|power\s+on|"
    r"enable[ds]?|activate[ds]?|on)\b",
    re.IGNORECASE,
)

# Real-device domains whose entities the input_boolean override must defer to: a template light/fan/etc.
_SELORA_LOCAL_INPUT_BOOLEAN_DEFER_DOMAINS: tuple[str, ...] = (
    "light",
    "switch",
    "fan",
    "cover",
    "climate",
    "media_player",
    "lock",
    "vacuum",
)


class _CommandsDevicesMixin:
    """Selora AI Local — deterministic scene / input_boolean command handlers, plus shared command helpers (mixed into ``SeloraLocalProvider``)."""

    def _command_message(self) -> str | None:
        """Return the lower-cased, stripped user message when the current turn is a plain ``chat_command``, else ``None``.

        Shared gate for the scene / input_boolean / climate overrides: they read the bare ``_chat_kind`` / ``_user_message_raw`` ContextVars (unlike the light / cover overrides, which use the race-robust accessors) and bail on a non-command or empty turn.
        """
        if self._chat_kind.get() != "chat_command":
            return None
        msg = (self._user_message_raw.get() or "").lower().strip()
        return msg or None

    def _command_domain_states(self, domain: str) -> list[Any]:
        """Live ``_filtered_domain_states`` for ``domain`` with the per-turn snapshot as a fallback.

        The live ``hass.states`` read can come back empty during the conversion-pass ContextVar race, so the deterministic light / cover overrides fall back to the snapshot injected via ``set_chat_context`` before giving up — without it the LoRA's mis-targeted output leaks through unmodified.
        """
        states = list(self._filtered_domain_states(domain))
        if not states:
            states = list(self._snapshot_domain_states(domain))
        return states

    @staticmethod
    def _match_named_state(msg: str, states: list[Any]) -> tuple[str | None, str, Any, int]:
        """Resolve the state whose friendly_name or de-slugged entity name is the longest (most specific) ``>= 4``-char substring of ``msg``.

        Returns ``(entity_id, friendly_name, state, match_len)`` for the best match, or ``(None, "", None, 0)`` when nothing matches. Shared by the input_boolean / cover / climate target resolvers; the light resolver keeps its stricter multi-word variant.
        """
        best_eid: str | None = None
        best_fname = ""
        best_state: Any = None
        best_len = 0
        for state in states:
            fname = str((state.attributes or {}).get("friendly_name") or "").strip()
            fl = fname.lower()
            slug_words = state.entity_id.split(".", 1)[-1].replace("_", " ")
            hit = ""
            if len(fl) >= 4 and fl in msg:
                hit = fl
            elif len(slug_words) >= 4 and slug_words in msg:
                hit = slug_words
            if hit and len(hit) > best_len:
                best_len = len(hit)
                best_eid = state.entity_id
                best_fname = fname
                best_state = state
        return best_eid, best_fname, best_state, best_len

    def _area_ids_for_named_scope(self, msg: str) -> set[str]:
        """Return the registry area-ids named (directly or via floor) in ``msg``."""
        if not self._hass:
            return set()
        try:
            from homeassistant.helpers import area_registry as ar
        except Exception:  # noqa: BLE001 — registries are best-effort here
            return set()
        area_reg = ar.async_get(self._hass)
        areas = list(area_reg.async_list_areas())
        matched: set[str] = set()
        for area in areas:
            names: list[str] = [area.name]
            names.extend(getattr(area, "aliases", None) or ())
            for name in names:
                nl = str(name or "").strip().lower()
                if len(nl) >= 3 and re.search(rf"\b{re.escape(nl)}\b", msg):
                    matched.add(area.id)
                    break
        # Floor name/alias -> every area on that floor.
        try:
            from homeassistant.helpers import floor_registry as fr

            floor_reg = fr.async_get(self._hass)
            floors = list(floor_reg.async_list_floors())
        except Exception:  # noqa: BLE001 — floor registry is best-effort here
            floors = []
        matched_floor_ids: set[str] = set()
        for floor in floors:
            fnames: list[str] = [getattr(floor, "name", "") or ""]
            fnames.extend(getattr(floor, "aliases", None) or ())
            for name in fnames:
                nl = str(name or "").strip().lower()
                if len(nl) >= 3 and re.search(rf"\b{re.escape(nl)}\b", msg):
                    fid = getattr(floor, "floor_id", None)
                    if fid:
                        matched_floor_ids.add(fid)
                    break
        if matched_floor_ids:
            for area in areas:
                if getattr(area, "floor_id", None) in matched_floor_ids:
                    matched.add(area.id)
        return matched

    def _entity_area_id(self, entity_id: str) -> str | None:
        """Return the registry area-id for ``entity_id`` (its own area, falling back to its device's area), or ``None`` when unknown."""
        if not self._hass:
            return None
        try:
            from homeassistant.helpers import device_registry as dr
            from homeassistant.helpers import entity_registry as er
        except Exception:  # noqa: BLE001 — registries are best-effort here
            return None
        ent_reg = er.async_get(self._hass)
        entry = ent_reg.async_get(entity_id)
        if entry is None:
            return None
        if entry.area_id:
            return entry.area_id
        if entry.device_id:
            dev = dr.async_get(self._hass).async_get(entry.device_id)
            return dev.area_id if dev else None
        return None

    def _maybe_scene_envelope(self) -> str | None:
        """Deterministically build a ``scene.turn_on`` command envelope when the current turn is a ``chat_command`` that asks to activate a scene."""
        msg = self._command_message()
        if msg is None:
            return None
        scenes = self._filtered_domain_states("scene")
        if not scenes:
            return None
        # Don't hijack a plain device command that names a concrete non-scene domain ("turn on the living room light", "turn off the bedroom fan").
        if "scene" not in msg and _SELORA_LOCAL_NON_SCENE_DEVICE_RE.search(msg):
            return None
        # Recover the scene name the user asked for: drop the leading activation verb, then the residual articles / "scene" / "to".
        query = _SELORA_LOCAL_SCENE_VERB_RE.sub(" ", msg)
        query = _SELORA_LOCAL_SCENE_NOISE_RE.sub(" ", query)
        query = re.sub(r"\s+", " ", query).strip()
        mentions_scene = "scene" in msg
        # Rank real scenes by exact name substring first (longest wins), then by difflib similarity to the recovered query.
        best_eid: str | None = None
        best_fname = ""
        best_substr = 0
        best_ratio = 0.0
        for state in scenes:
            eid = state.entity_id
            fname = str((state.attributes or {}).get("friendly_name") or "").strip()
            fl = fname.lower()
            slug_words = eid.split(".", 1)[-1].replace("_", " ").lower()
            substr = 0
            if query and len(fl) >= 4 and fl in query:
                substr = len(fl)
            elif query and len(slug_words) >= 4 and slug_words in query:
                substr = len(slug_words)
            ratio = 0.0
            if query:
                ratio = max(
                    difflib.SequenceMatcher(None, query, fl).ratio(),
                    difflib.SequenceMatcher(None, query, slug_words).ratio(),
                )
            if (substr, ratio) > (best_substr, best_ratio):
                best_substr = substr
                best_ratio = ratio
                best_eid = eid
                best_fname = fname or eid
        if best_eid is None:
            return None
        # Only intercept on a real scene signal: the literal word "scene" in the request, an exact name substring, or a strong similarity to a real scene name.
        if not (mentions_scene or best_substr > 0 or best_ratio >= 0.5):
            return None
        # A pure character-ratio match (no literal "scene", no name substring) is only trustworthy when the recovered query and the matched scene actually share a significant WORD.
        if not mentions_scene and best_substr == 0:
            scene_tokens = {
                t
                for t in re.split(
                    r"[^a-z0-9]+",
                    f"{best_fname} {best_eid.split('.', 1)[-1]}".lower(),
                )
                if len(t) >= 3
            }
            query_tokens = {t for t in re.split(r"[^a-z0-9]+", query) if len(t) >= 3}
            if not (scene_tokens & query_tokens):
                return None
        call: dict[str, Any] = {
            "service": "scene.turn_on",
            "target": {"entity_id": best_eid},
        }
        r_text = f"Activating the {best_fname} scene."
        return json.dumps({"intent": "command", "response": r_text, "calls": [call]})

    def _maybe_input_boolean_envelope(self) -> str | None:
        """Deterministically build an ``input_boolean`` command envelope when the current turn is a ``chat_command`` that names a real input_boolean helper."""
        msg = self._command_message()
        if msg is None:
            return None
        # Defer when the prompt names a real controllable device.
        for real_domain in _SELORA_LOCAL_INPUT_BOOLEAN_DEFER_DOMAINS:
            for state in self._filtered_domain_states(real_domain):
                fname = str((state.attributes or {}).get("friendly_name") or "").strip().lower()
                slug_words = state.entity_id.split(".", 1)[-1].replace("_", " ")
                if (len(fname) >= 4 and fname in msg) or (
                    len(slug_words) >= 4 and slug_words in msg
                ):
                    return None
        # Resolve the target input_boolean by friendly_name / slug match, preferring the longest (most specific) match so a generic "mode" can't shadow "guest mode".
        target_eid, target_fname, _target_state, _best_len = self._match_named_state(
            msg, self._filtered_domain_states("input_boolean")
        )
        if target_eid is None:
            return None
        target_fname = target_fname or target_eid
        # Detect the on/off verb.
        off = bool(_SELORA_LOCAL_INPUT_BOOLEAN_OFF_RE.search(msg))
        on = bool(_SELORA_LOCAL_INPUT_BOOLEAN_ON_RE.search(msg))
        if off and not on:
            call: dict[str, Any] = {
                "service": "input_boolean.turn_off",
                "target": {"entity_id": target_eid},
            }
            r_text = f"Turned off {target_fname}."
        elif on and not off:
            call = {
                "service": "input_boolean.turn_on",
                "target": {"entity_id": target_eid},
            }
            r_text = f"Turned on {target_fname}."
        else:
            # Ambiguous (both/neither verb) — let the LoRA handle it.
            return None
        return json.dumps({"intent": "command", "response": r_text, "calls": [call]})

    # Rooms we recognise when grounding a presence-duration automation to a specific area.
    _PRESENCE_ROOM_WORDS: tuple[str, ...] = (
        "living room",
        "dining room",
        "guest room",
        "laundry room",
        "bedroom",
        "bathroom",
        "basement",
        "hallway",
        "kitchen",
        "office",
        "garage",
        "porch",
        "study",
        "attic",
        "den",
    )

    # Comparative / numeric-threshold phrasing that implies a numeric_state trigger ("warmer than", "above 26", "humidity over 65 percent", "below the inside temperature").
    _NUMERIC_SIGNAL_RE: re.Pattern[str] = re.compile(
        r"\b(?:temperature|temp|humidity|humid|degrees?|percent|setpoint|"
        r"set\s*point|thermostat)\b"
        r"|\b(?:above|below|over|under|less\s+than|greater\s+than|more\s+than)"
        r"\s+\d"
        r"|\b(?:warmer|cooler|hotter|colder|higher|lower)\b[^.?!]{0,30}\bthan\b"
        r"|°",
        re.IGNORECASE,
    )
    _MULTI_COND_CONNECTOR_RE: re.Pattern[str] = re.compile(
        r"\b(?:when|whenever|while|if|after|once)\b", re.IGNORECASE
    )
    _MULTI_COND_ACTION_RE: re.Pattern[str] = re.compile(
        r"\b(?:turn|switch|start|stop|run|activate|deactivate|enable|disable|"
        r"open|close|set|toggle|dim|brighten)\b",
        re.IGNORECASE,
    )
    # Sun-event TRIGGER phrasing — the prompt schedules the action ON a sun event ("at sunset", "every sunrise", "by dusk") or relative to one ("15 minutes after sunrise", "an hour before sunset", "half an hour after dusk").
    _SUN_AUTOMATION_TRIGGER_RE: re.Pattern[str] = re.compile(
        r"\b(?:at|every|each|on|by|come|around)\s+"
        r"(?:sunset|sundown|dusk|sunrise|sundawn|dawn)\b"
        r"|\b\d+\s*(?:second|sec|minute|min|hour|hr)s?\s+(?:before|after)\s+"
        r"(?:sunset|sundown|dusk|sunrise|sundawn|dawn)\b"
        r"|\b(?:an?\s+hour|half\s+an\s+hour)\s+(?:before|after)\s+"
        r"(?:sunset|sundown|dusk|sunrise|sundawn|dawn)\b",
        re.IGNORECASE,
    )
    # Noun → (controllable domain, name-keyword set).
    _SUN_TARGET_NOUNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        (
            r"\b(?:blinds?|shades?|curtains?|shutters?|awnings?|garage|gates?|"
            r"covers?)\b",
            "cover",
            ("blind", "shade", "curtain", "shutter", "awning", "garage", "gate", "cover", "door"),
        ),
        (r"\b(?:lights?|lamps?)\b", "light", ("light", "lamp")),
        (r"\b(?:fans?)\b", "fan", ("fan",)),
        (
            r"\b(?:tv|television|speakers?|media\s+player)\b",
            "media_player",
            ("tv", "television", "speaker", "media"),
        ),
        (
            r"\b(?:plugs?|outlets?|switch(?:es)?)\b",
            "switch",
            ("switch", "plug", "outlet"),
        ),
    )
