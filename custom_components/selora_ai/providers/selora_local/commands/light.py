"""Selora AI Local — deterministic light command handlers."""

from __future__ import annotations

import json
import re
from typing import Any


class _CommandsLightMixin:
    """Selora AI Local — deterministic light command handlers (mixed into ``SeloraLocalProvider``)."""

    def _light_scope_targets(self, msg: str, lights: list[Any]) -> list[str]:
        """Return the light entity_ids whose area/floor is named in ``msg``."""
        area_ids = self._area_ids_for_named_scope(msg)
        if not area_ids or not self._hass:
            return []
        try:
            from homeassistant.helpers import device_registry as dr
            from homeassistant.helpers import entity_registry as er
        except Exception:  # noqa: BLE001 — registries are best-effort here
            return []
        ent_reg = er.async_get(self._hass)
        dev_reg = dr.async_get(self._hass)
        result: list[str] = []
        for state in lights:
            entry = ent_reg.async_get(state.entity_id)
            area_id = entry.area_id if entry else None
            if not area_id and entry and entry.device_id:
                dev = dev_reg.async_get(entry.device_id)
                area_id = dev.area_id if dev else None
            if area_id and area_id in area_ids:
                result.append(state.entity_id)
        return sorted(result)

    @staticmethod
    def _resolve_named_light(msg: str, lights: list[Any]) -> str | None:
        """Resolve a single light named in ``msg`` by friendly_name / slug."""
        target_eid: str | None = None
        best_len = 0
        for state in lights:
            fname = str((state.attributes or {}).get("friendly_name") or "").strip()
            fl = fname.lower()
            slug_words = state.entity_id.split(".", 1)[-1].replace("_", " ")
            candidates: list[str] = []
            if len(fl) >= 5 and " " in fl and fl in msg:
                candidates.append(fl)
            if len(slug_words) >= 5 and " " in slug_words and slug_words in msg:
                candidates.append(slug_words)
            for hit in candidates:
                if len(hit) > best_len:
                    best_len = len(hit)
                    target_eid = state.entity_id
        return target_eid

    @staticmethod
    def _light_brightness_pct(msg: str) -> int | None:
        """Recover a 0-100 brightness target from a light command turn."""
        if re.search(r"\b(max|maximum|maximal|highest|brightest|full|fully)\b", msg):
            return 100
        if re.search(r"\b(min|minimum|minimal|lowest|dimmest)\b", msg):
            return 1
        m = re.search(r"(\d{1,3})\s*(?:percent|pct|%)", msg)
        if m is not None:
            val = int(m.group(1))
            if 0 <= val <= 100:
                return val
        return None

    @staticmethod
    def _detect_light_rgb(msg: str) -> list[int] | None:
        """Map a colour word in ``msg`` to its RGB triple via HA's colour util."""
        color_words = (
            "red",
            "green",
            "blue",
            "white",
            "yellow",
            "orange",
            "purple",
            "pink",
            "magenta",
            "cyan",
            "lime",
            "teal",
            "indigo",
            "violet",
            "brown",
            "gold",
            "turquoise",
            "maroon",
            "navy",
            "olive",
        )
        for word in color_words:
            if re.search(rf"\b{word}\b", msg):
                try:
                    from homeassistant.util import color as color_util

                    rgb = color_util.color_name_to_rgb(word)
                    return [int(rgb[0]), int(rgb[1]), int(rgb[2])]
                except Exception:  # noqa: BLE001 — colour util is best-effort
                    return None
        return None

    def _colocated_light_for_entity(self, entity_id: str, lights: list[Any]) -> str | None:
        """Resolve the single light co-located with a mis-targeted entity."""
        area_id = self._entity_area_id(entity_id)
        if area_id:
            same_area = [
                s.entity_id for s in lights if self._entity_area_id(s.entity_id) == area_id
            ]
            if len(same_area) == 1:
                return same_area[0]
            if len(same_area) > 1:
                return None
        base = entity_id.split(".", 1)[-1]
        tokens = [t for t in base.split("_") if len(t) >= 4]
        if not tokens:
            return None
        matched: list[str] = []
        for s in lights:
            slug_tokens = set(s.entity_id.split(".", 1)[-1].split("_"))
            if any(t in slug_tokens for t in tokens):
                matched.append(s.entity_id)
        if len(matched) == 1:
            return matched[0]
        return None

    def _repoint_colocated_light_brightness(self, slim_calls: list[Any], msg: str) -> str | None:
        """Re-point a mis-targeted brightness/colour command onto a light."""
        pct = self._light_brightness_pct(msg)
        rgb = self._detect_light_rgb(msg)
        if pct is None and rgb is None:
            return None
        # Bail when the user explicitly named a non-light controllable domain so a genuine cover-position / fan-speed / volume percentage request is never hijacked onto a co-located light.
        if re.search(
            r"\b(curtain|curtains|cover|covers|blind|blinds|shade|shades|"
            r"drape|drapes|garage|door|fan|fans|speed|volume|media|music|"
            r"song|track|playlist|thermostat|temperature|temp|heat|cool|"
            r"climate|valve|valves|sprinkler|sprinklers|vacuum|lock|locks)\b",
            msg,
        ):
            return None
        lights = self._command_domain_states("light")
        if not lights:
            return None
        for c in slim_calls:
            if not isinstance(c, dict):
                continue
            eid = c.get("e")
            if not isinstance(eid, str) or "." not in eid:
                continue
            if eid.split(".", 1)[0] == "light":
                # The LoRA already targeted a light — defer to the normal path.
                return None
            target = self._colocated_light_for_entity(eid, lights)
            if target is None:
                continue
            if rgb is not None:
                data: dict[str, Any] = {"rgb_color": rgb}
                if pct is not None:
                    data["brightness_pct"] = pct
                resp = "Set the light colour."
            else:
                data = {"brightness_pct": pct}
                resp = f"Set brightness to {pct}%."
            return json.dumps(
                {
                    "intent": "command",
                    "response": resp,
                    "calls": [
                        {
                            "service": "light.turn_on",
                            "target": {"entity_id": target},
                            "data": data,
                        }
                    ],
                }
            )
        return None

    def _maybe_light_envelope(self) -> str | None:
        """Deterministically build a ``light`` command envelope when the current turn is a ``chat_command`` carrying a light signal."""
        # Use the race-robust ``_current_chat_kind`` / ``_current_user_message`` accessors (ContextVar with an instance-attribute fallback) rather than the bare ContextVars: the post-stream conversion pass frequently runs in a context that never saw ``set_chat_context`` (the documented propagation race), so reading ``self._chat_kind.get()`` / ``self._user_message_raw.get()`` directly returns ``None`` / "" and silently skips this override — exactly the failure that let the LoRA's mis-targeted bedroom brightness (``cover.bedroom``) and its single-light scoping of "activate all first floor lights" sail through.
        if self._current_chat_kind() != "chat_command":
            return None
        raw = self._current_user_message() or ""
        msg = raw.lower().strip()
        return self._resolve_light_command(msg, require_light_word=True)

    def _resolve_light_command(self, msg: str, *, require_light_word: bool) -> str | None:
        """Resolve a light command turn to a corrected command envelope, or ``None`` when the turn names no resolvable light scope / verb."""
        if not msg:
            return None
        # Light signal gate: a light/lamp noun or a brightness/dim verb.
        has_light_word = bool(
            re.search(
                r"\b(lights?|lamps?|lighting|brightness|dim|brighten|brighter|dimmer)\b",
                msg,
            )
        )
        if not has_light_word:
            if require_light_word:
                return None
            # No explicit light noun: only treat as a light command when a brightness / colour signal is present AND no other controllable domain is named, so a cover/fan/media/climate percentage request is never re-pointed at a co-located light.
            if self._light_brightness_pct(msg) is None and self._detect_light_rgb(msg) is None:
                return None
            if re.search(
                r"\b(curtain|curtains|cover|blind|blinds|shade|shades|drape|drapes|"
                r"garage|door|fan|speed|volume|media|music|song|track|playlist|"
                r"thermostat|temperature|temp|heat|cool|climate|valve|vacuum|lock)\b",
                msg,
            ):
                return None
        lights = self._command_domain_states("light")
        if not lights:
            return None
        # Resolve the target set: a named area/floor scope vs a single named light.
        parts = [p.strip() for p in re.split(r"\bthen\b", msg) if p.strip()]
        if len(parts) > 1:
            merged: list[dict[str, Any]] = []
            responses: list[str] = []
            for part in parts:
                clause = self._light_clause_calls(part, lights)
                if clause is None:
                    continue
                clause_calls, clause_resp = clause
                merged.extend(clause_calls)
                responses.append(clause_resp)
            if len(merged) >= 2 and len(responses) >= 2:
                return json.dumps(
                    {
                        "intent": "command",
                        "response": " ".join(responses),
                        "calls": merged,
                    }
                )
        # Single (non-compound) clause — the common case.
        clause = self._light_clause_calls(msg, lights)
        if clause is None:
            return None
        calls, resp = clause
        return json.dumps({"intent": "command", "response": resp, "calls": calls})

    def _light_clause_calls(
        self, msg: str, lights: list[Any]
    ) -> tuple[list[dict[str, Any]], str] | None:
        """Resolve one light clause (scope + verb + parametric data) to a ``(calls, response)`` pair, or ``None`` when the clause names no resolvable light scope or carries no recognisable verb."""
        scope_targets = self._light_scope_targets(msg, lights)
        specific = self._resolve_named_light(msg, lights)
        collective_or_plural = bool(re.search(r"\b(all|every|each|both)\b", msg)) or bool(
            re.search(r"\b(lights|lamps)\b", msg)
        )
        if scope_targets and collective_or_plural:
            targets = scope_targets
        elif specific is not None:
            targets = [specific]
        elif scope_targets:
            targets = scope_targets
        else:
            return None
        # Determine the action.
        rgb = self._detect_light_rgb(msg)
        pct = self._light_brightness_pct(msg)
        # On/off detection avoids the "on"/"off" prepositions inside phrases like "the lights on the first floor": a bare directional word only counts at the END of the message ("first floor lights on"); the mid-sentence forms must be an explicit verb ("turn on", "activate").
        off = bool(
            re.search(r"\b(turn\s+off|switch\s+off|shut\s+off|power\s+off|deactivate)\b", msg)
        ) or bool(re.search(r"\boff\b\s*[.!]?\s*$", msg))
        on = bool(re.search(r"\b(turn\s+on|switch\s+on|power\s+on|activate)\b", msg)) or bool(
            re.search(r"\bon\b\s*[.!]?\s*$", msg)
        )
        data: dict[str, Any] | None
        if rgb is not None:
            service = "light.turn_on"
            data = {"rgb_color": rgb}
            if pct is not None:
                data["brightness_pct"] = pct
            resp = "Set the light colour."
        elif pct is not None:
            service = "light.turn_on"
            data = {"brightness_pct": pct}
            resp = f"Set brightness to {pct}%."
        elif off and not on:
            service = "light.turn_off"
            data = None
            resp = "Turned off the lights." if len(targets) > 1 else "Turned off the light."
        elif on:
            service = "light.turn_on"
            data = None
            resp = "Turned on the lights." if len(targets) > 1 else "Turned on the light."
        else:
            return None
        # Mirror command_policy._MAX_TARGET_ENTITIES so each fanned-out call stays within the
        # per-call entity cap. It does NOT keep the TOTAL under _MAX_COMMAND_CALLS (5): three
        # targets per call means 15 lights is the ceiling, and a floor with more than that
        # builds an envelope apply_command_policy rejects outright as too many actions. The
        # policy has no area_id target to collapse them into, so raising the ceiling is a
        # policy change rather than something this handler can chunk its way around.
        chunk_size = 3
        calls: list[dict[str, Any]] = []
        for i in range(0, len(targets), chunk_size):
            chunk = targets[i : i + chunk_size]
            entity_ref: str | list[str] = chunk[0] if len(chunk) == 1 else list(chunk)
            call: dict[str, Any] = {
                "service": service,
                "target": {"entity_id": entity_ref},
            }
            if data:
                call["data"] = dict(data)
            calls.append(call)
        return calls, resp
