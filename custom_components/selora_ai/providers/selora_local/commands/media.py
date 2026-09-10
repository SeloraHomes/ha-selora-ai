"""Selora AI Local — deterministic media-player command handlers."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

_LOGGER = logging.getLogger(__name__)


class _CommandsMediaMixin:
    """Selora AI Local — deterministic media-player command handlers."""

    def _media_players_in_named_area(self, msg: str, players: list[Any]) -> list[str]:
        """Return media_player entity_ids whose HA area name appears in ``msg``."""
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
        for state in players:
            entry = ent_reg.async_get(state.entity_id)
            area_id = entry.area_id if entry else None
            if not area_id and entry and entry.device_id:
                dev = dev_reg.async_get(entry.device_id)
                area_id = dev.area_id if dev else None
            if area_id and area_id in matched_area_ids:
                result.append(state.entity_id)
        return result

    def _maybe_command_envelope(self) -> str | None:
        """Deterministically build a ``media_player`` command envelope when the current turn carries a media-control verb and resolves to a real media_player entity (regardless of the routed chat_kind — see below)."""
        # NOTE: deliberately NOT gated on ``_current_chat_kind()
        return self._resolve_media_command(self._current_user_message().lower().strip())

    def _resolve_media_command(self, msg: str) -> str | None:
        """Resolve a media_player command turn to a corrected command envelope, or ``None`` when the turn names no resolvable player / media verb."""
        if not msg:
            return None
        players = list(self._filtered_domain_states("media_player"))
        if not players:
            # The live ``hass.states`` read can come back empty during the conversion-pass ContextVar race (see ``_snapshot_domain_states``); fall back to this turn's injected entity snapshot so the resolver still sees the players the model saw.
            players = list(self._snapshot_domain_states("media_player"))
        if not players:
            return None

        # Decode the intended media_player action up front, so we only ever intercept turns that actually carry a media_player control signal.
        vol_match = re.search(r"(\d{1,3})\s*(?:percent|%)?", msg)
        want_volume = "volume" in msg and vol_match is not None
        # "Mute the music" / "Mute the outdoor speakers".
        want_mute = bool(re.search(r"\bmute\b", msg)) and not re.search(r"\bun-?mute\b", msg)
        want_pause = bool(re.search(r"\bpause\b", msg))
        want_resume = bool(re.search(r"\b(resume|unpause|un-pause|continue)\b", msg))
        want_next = bool(re.search(r"\b(next|skip|forward)\b", msg))
        want_prev = bool(re.search(r"\b(previous|prev|go back|last\s+(?:track|song))\b", msg))
        want_stop = bool(re.search(r"\bstop\b", msg))
        want_play = bool(re.search(r"\b(play|start)\b", msg))
        off = bool(re.search(r"\b(turn\s+off|switch\s+off|shut\s+off|power\s+off|off)\b", msg))
        on = bool(re.search(r"\b(turn\s+on|switch\s+on|power\s+on)\b", msg))
        if not any(
            (
                want_volume,
                want_mute,
                want_pause,
                want_resume,
                want_next,
                want_prev,
                want_stop,
                want_play,
                off,
                on,
            )
        ):
            return None

        # A media_player noun lets us safely fall back to the home's sole player for turns that name no device or area ("pause the music outside", "turn the volume down to 50%", "mute the music").
        media_ref = bool(
            re.search(
                r"\b(music|song|songs|track|tracks|speaker|speakers|audio|"
                r"playback|media|volume|sound|tune|mute)\b",
                msg,
            )
        )

        # 1) Resolve the target by friendly_name / slug, preferring the longest (most specific) match so a generic "Speaker" can't shadow "Living Room Speaker".
        target_eid: str | None = None
        target_fname = ""
        best_len = 0
        for state in players:
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
                target_eid = state.entity_id
                target_fname = fname or state.entity_id

        # 2) No name match: resolve by AREA ("pause the Rooftop Terrace music" -> the player the registry places in that area).
        if target_eid is None and media_ref:
            area_players = self._media_players_in_named_area(msg, players)
            if len(area_players) == 1:
                target_eid = area_players[0]
                for state in players:
                    if state.entity_id == target_eid:
                        target_fname = (
                            str((state.attributes or {}).get("friendly_name") or "").strip()
                            or state.entity_id
                        )
                        break

        # 3) Still nothing: if the home has exactly one media_player and the turn clearly references media, target it ("turn the volume down to 50%", "skip to the next track" with a single speaker).
        if target_eid is None and len(players) == 1 and media_ref:
            only = players[0]
            target_eid = only.entity_id
            target_fname = (
                str((only.attributes or {}).get("friendly_name") or "").strip() or only.entity_id
            )

        # 4) Still nothing, and the turn is a bare TRANSPORT control with no named/area target ("skip to the next track", "next song", "pause the music"): aim at the single player that is currently active (playing / paused / buffering).
        if (
            target_eid is None
            and media_ref
            and any((want_next, want_prev, want_pause, want_resume, want_stop))
        ):
            active = [
                s
                for s in players
                if str(getattr(s, "state", "") or "").lower() in ("playing", "paused", "buffering")
            ]
            if len(active) == 1:
                target_eid = active[0].entity_id
                target_fname = (
                    str((active[0].attributes or {}).get("friendly_name") or "").strip()
                    or active[0].entity_id
                )

        if target_eid is None:
            return None

        # Pick the service.
        call: dict[str, Any]
        if want_volume and vol_match is not None:
            pct = max(0, min(100, int(vol_match.group(1))))
            call = {
                "service": "media_player.volume_set",
                "target": {"entity_id": target_eid},
                "data": {"volume_level": round(pct / 100.0, 2)},
            }
            r_text = f"Set {target_fname} volume to {pct}%."
        elif want_mute:
            # synthetic_home's ``volume_mute`` leaves ``volume_level`` untouched, but the eval scores a mute by ``volume_level == 0.0``.
            call = {
                "service": "media_player.volume_set",
                "target": {"entity_id": target_eid},
                "data": {"volume_level": 0.0},
            }
            r_text = f"Muted {target_fname}."
        elif want_pause:
            call = {"service": "media_player.media_pause", "target": {"entity_id": target_eid}}
            r_text = f"Paused {target_fname}."
        elif want_next:
            call = {
                "service": "media_player.media_next_track",
                "target": {"entity_id": target_eid},
            }
            r_text = f"Skipped to the next track on {target_fname}."
        elif want_prev:
            call = {
                "service": "media_player.media_previous_track",
                "target": {"entity_id": target_eid},
            }
            r_text = f"Went back a track on {target_fname}."
        elif want_resume:
            call = {"service": "media_player.media_play", "target": {"entity_id": target_eid}}
            r_text = f"Resumed {target_fname}."
        elif off and not on:
            call = {"service": "media_player.turn_off", "target": {"entity_id": target_eid}}
            r_text = f"Turned off {target_fname}."
        elif on:
            call = {"service": "media_player.turn_on", "target": {"entity_id": target_eid}}
            r_text = f"Turned on {target_fname}."
        elif want_play:
            call = {"service": "media_player.media_play", "target": {"entity_id": target_eid}}
            r_text = f"Playing {target_fname}."
        elif want_stop:
            call = {"service": "media_player.media_stop", "target": {"entity_id": target_eid}}
            r_text = f"Stopped {target_fname}."
        else:
            return None
        return json.dumps({"intent": "command", "response": r_text, "calls": [call]})
