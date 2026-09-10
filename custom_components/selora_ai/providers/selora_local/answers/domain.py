"""Selora AI Local — deterministic per-domain readout question handlers.

Split out of ``local_answers_state`` — the shared detection helpers, regexes and
lookup tables still live there and are imported below.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .filter import (
    _MEDIA_PLAYER_STATE_PROSE,
    _MEDIA_PLAYER_SUBJECT_STOPWORDS,
    _VALVE_NATURAL_STATE,
    _VALVE_STATE_PROSE,
    _WEATHER_CONDITION_PROSE,
    _detect_measurement_value_question,
    _detect_media_player_state_question,
    _detect_polar_valve_state_question,
    _detect_weather_question,
)
from .state import (
    _answer,
    _safe_fname_for_prose,
)


class _AnswersDomainMixin:
    """Selora AI Local — per-domain readouts (media_player / valve / weather / measurement)."""

    def _maybe_media_player_state_envelope(self) -> str | None:
        """Answer a media_player playback-state question ("is the living room media player playing, paused, or stopped?") deterministically from ``hass.states``."""
        # ContextVar-independent gate. The original ``
        if self._chat_kind.get() in (
            "chat_command",
            "chat_automation",
            "command",
            "chat",
            "suggestions",
            "chat_tool_round",
        ):
            return None
        qualifier = _detect_media_player_state_question(self._user_message_raw.get() or "")
        if qualifier is None:
            return None
        qualifier_tokens = {
            t
            for t in re.split(r"[^a-z0-9]+", qualifier)
            if t and t not in _MEDIA_PLAYER_SUBJECT_STOPWORDS
        }
        # Area fallback: the qualifier ("living room") may name an HA AREA rather than appear in the player's friendly_name/slug — a Living Room player is often named "Roku"/"Soundbar"/"Theater" with no room token, so name/slug matching alone would find nothing and we'd defer to the LoRA (the {media_id} placeholder bug).
        area_ids: set[str] = (
            self._area_ids_for_named_scope((self._user_message_raw.get() or "").lower())
            if qualifier_tokens
            else set()
        )
        ent_reg: Any = None
        dev_reg: Any = None
        if area_ids:
            try:
                from homeassistant.helpers import device_registry as dr
                from homeassistant.helpers import entity_registry as er

                ent_reg = er.async_get(self._hass)
                dev_reg = dr.async_get(self._hass)
            except Exception:  # noqa: BLE001 — registries are best-effort here
                area_ids = set()

        def _entity_area(eid: str) -> str | None:
            entry = ent_reg.async_get(eid) if ent_reg is not None else None
            aid = entry.area_id if entry else None
            if not aid and entry and entry.device_id and dev_reg is not None:
                dev = dev_reg.async_get(entry.device_id)
                aid = dev.area_id if dev else None
            return aid

        candidates: list[tuple[Any, int]] = []  # (state, extra-token count)
        live_players: list[Any] = []
        for state in self._filtered_domain_states("media_player"):
            live = (state.state or "").lower()
            if not live or live in ("unknown", "unavailable"):
                continue
            live_players.append(state)
            if qualifier_tokens:
                fname = str((state.attributes or {}).get("friendly_name") or "")
                slug = state.entity_id.split(".", 1)[-1]
                cand_tokens = {t for t in re.split(r"[^a-z0-9]+", f"{fname} {slug}".lower()) if t}
                if qualifier_tokens <= cand_tokens:
                    extra = len(cand_tokens - qualifier_tokens)
                elif area_ids and _entity_area(state.entity_id) in area_ids:
                    # Area-scoped match: rank below every name match so a name-resolved player is always preferred.
                    extra = 1000 + len(cand_tokens)
                else:
                    continue
            else:
                extra = 0
            candidates.append((state, extra))
        # Single-player fallback: the room qualifier ("living room") may match the sole player by neither friendly_name/slug (it is named "Samsung") nor a resolvable registry area (the synthetic_home device→area link is not always visible at conversion time).
        if qualifier_tokens and not candidates and len(live_players) == 1:
            candidates.append((live_players[0], 0))
        if not candidates:
            return None

        def _prose(live: str) -> str:
            return _MEDIA_PLAYER_STATE_PROSE.get(live, live)

        if qualifier_tokens:
            # Named player: pick the most specific match (fewest extra tokens) so "living room media player" never resolves to an unrelated player.
            candidates.sort(key=lambda p: p[1])
            state = candidates[0][0]
            live = (state.state or "").lower()
            fname = str((state.attributes or {}).get("friendly_name") or state.entity_id)
            safe_fname = _safe_fname_for_prose(fname)
            marker = f"\n[[entities:{state.entity_id}]]"
            r_text = f"The {safe_fname} is currently {_prose(live)}.{marker}"
            return json.dumps(
                {
                    "intent": "answer",
                    "response": r_text,
                    "r": r_text,
                    "q": [state.entity_id],
                }
            )
        # Bare "the media player": when exactly one player exists, report it directly; otherwise enumerate each player and its state so the reply still names the live playback condition for all of them.
        states = sorted((s for s, _ in candidates), key=lambda s: s.entity_id)
        if len(states) == 1:
            state = states[0]
            live = (state.state or "").lower()
            fname = str((state.attributes or {}).get("friendly_name") or state.entity_id)
            safe_fname = _safe_fname_for_prose(fname)
            marker = f"\n[[entities:{state.entity_id}]]"
            r_text = f"The {safe_fname} is currently {_prose(live)}.{marker}"
            return json.dumps(
                {
                    "intent": "answer",
                    "response": r_text,
                    "r": r_text,
                    "q": [state.entity_id],
                }
            )
        parts: list[str] = []
        ids: list[str] = []
        for state in states:
            live = (state.state or "").lower()
            fname = str((state.attributes or {}).get("friendly_name") or state.entity_id)
            parts.append(f"{_safe_fname_for_prose(fname)} is {_prose(live)}")
            ids.append(state.entity_id)
        marker = f"\n[[entities:{','.join(ids)}]]"
        r_text = "; ".join(parts) + f".{marker}"
        return json.dumps(
            {
                "intent": "answer",
                "response": r_text,
                "r": r_text,
                "q": ids,
            }
        )

    def _maybe_polar_valve_state_envelope(self) -> str | None:
        """Answer a yes/no valve/sprinkler state question ("are the sprinklers on?") deterministically from ``hass.states``."""
        # Same per-turn gate as the other answer overrides: ``_call_kind`` is reset to None at end-of-stream before this runs, so a chat_command turn that incidentally contains "are … on?" must not have its command envelope replaced by a stub answer.
        if self._chat_kind.get() != "chat_answer":
            return None
        target = _detect_polar_valve_state_question(self._user_message_raw.get() or "")
        if target is None:
            return None
        accepted = _VALVE_NATURAL_STATE.get(target)
        if not accepted:
            return None
        entries: list[tuple[str, str, str]] = []  # (entity_id, friendly, live)
        for state in self._filtered_domain_states("valve"):
            live = (state.state or "").lower()
            if not live or live in ("unknown", "unavailable"):
                continue
            fname = str((state.attributes or {}).get("friendly_name") or state.entity_id)
            entries.append((state.entity_id, _safe_fname_for_prose(fname), live))
        if not entries:
            return None
        entries.sort(key=lambda p: p[0])
        ids = [eid for eid, _, _ in entries]
        marker = f"\n[[entities:{','.join(ids)}]]"
        matched = [e for e in entries if e[2] in accepted]
        state_words = sorted({_VALVE_STATE_PROSE.get(e[2], e[2]) for e in entries})
        if not matched:
            # None are in the asked-for state → a clear "No", plus the live state word(s) so the prose names the actual condition.
            if len(entries) == 1:
                r_text = f"No — {entries[0][1]} is {state_words[0]}, not {target}.{marker}"
            else:
                r_text = (
                    f"No — none of the {len(entries)} valves are {target}; "
                    f"they're {', '.join(state_words)}.{marker}"
                )
        elif len(matched) == len(entries):
            if len(entries) == 1:
                r_text = f"Yes — {entries[0][1]} is {state_words[0]}.{marker}"
            else:
                r_text = f"Yes — all {len(entries)} valves are {target}.{marker}"
        else:
            on_names = ", ".join(e[1] for e in matched)
            r_text = (
                f"Partly — {len(matched)} of {len(entries)} valves are {target}: "
                f"{on_names}.{marker}"
            )
        return json.dumps(
            {
                "intent": "answer",
                "response": r_text,
                "r": r_text,
                "q": ids,
            }
        )

    def _maybe_weather_question_envelope(self) -> str | None:
        """Answer a weather/forecast question ("Is today sunny or cloudy?") deterministically from the live ``weather.*`` entity state."""
        # Read via ``_current_user_message`` (ContextVar with instance-mirror fallback), NOT the bare ``_user_message_raw`` ContextVar: the non-streaming ``convert_response_text`` → ``_convert_slim_shape`` pass runs in a context that never saw ``set_chat_context``'s ``set``, so the raw ContextVar reads back empty and the weather detector silently no-ops — the turn then falls through to the LoRA refusal ("I can only answer questions about your home.").
        if not _detect_weather_question(self._current_user_message()):
            return None
        states = self._filtered_domain_states("weather")
        if not states and self._hass is not None:
            states = [
                s
                for s in self._hass.states.async_all()
                if "." in s.entity_id and s.entity_id.split(".", 1)[0] == "weather"
            ]
        chosen: Any | None = None
        for s in states:
            live = (s.state or "").lower()
            if live and live not in ("unknown", "unavailable"):
                chosen = s
                break
        if chosen is None:
            return None
        live = (chosen.state or "").lower()
        prose = _WEATHER_CONDITION_PROSE.get(live, live.replace("-", " ").replace("_", " "))
        marker = f"\n[[entities:{chosen.entity_id}]]"
        r_text = f"It's currently {prose}.{marker}"
        return json.dumps(
            {
                "intent": "answer",
                "response": r_text,
                "r": r_text,
                "q": [chosen.entity_id],
            }
        )

    def _maybe_measurement_value_envelope(self) -> str | None:
        """Answer a numeric sensor-value question ("what is the battery level of the motion sensor?") deterministically from the live sensor state + unit."""
        if self._chat_kind.get() != "chat_answer":
            return None
        subject_tokens = _detect_measurement_value_question(self._user_message_raw.get() or "")
        if not subject_tokens:
            return None
        # Resolve from the exact list the model saw: every subject token must appear in the candidate's friendly_name/slug, and among matches prefer the most specific one (fewest extra tokens), mirroring ``_maybe_single_state_envelope``.
        best: tuple[dict[str, Any], int] | None = None
        for e in self._entities_for_lora.get() or []:
            if not isinstance(e, dict):
                continue
            eid = str(e.get("entity_id") or "")
            if not eid.startswith("sensor."):
                continue
            attrs = e.get("attributes") or {}
            fname = str(attrs.get("friendly_name") or eid)
            slug = eid.split(".", 1)[-1]
            cand_tokens = {t for t in re.split(r"[^a-z0-9]+", f"{fname} {slug}".lower()) if t}
            if not subject_tokens <= cand_tokens:
                continue
            extra = len(cand_tokens - subject_tokens)
            if best is None or extra < best[1]:
                best = (e, extra)
        if best is None:
            return None
        entity = best[0]
        attrs = entity.get("attributes") or {}
        raw_state = str(entity.get("state") or "").strip()
        if not raw_state or raw_state.lower() in ("unknown", "unavailable"):
            return None
        unit = str(attrs.get("unit_of_measurement") or "").strip()
        if unit:
            sep = "" if unit in ("%", "°", "°C", "°F") else " "
            value = f"{raw_state}{sep}{unit}"
        else:
            value = raw_state
        eid = str(entity.get("entity_id"))
        fname = str(attrs.get("friendly_name") or eid)
        safe_fname = _safe_fname_for_prose(fname)
        marker = f"\n[[entities:{eid}]]"
        r_text = f"The {safe_fname} is {value}.{marker}"
        return _answer(r_text, [eid])
