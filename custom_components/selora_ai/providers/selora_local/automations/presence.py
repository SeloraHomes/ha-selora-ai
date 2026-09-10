"""Selora AI Local — presence-trigger automation-synthesis override handlers."""

from __future__ import annotations

import re
from typing import Any


class _AutomationsPresenceMixin:
    """Selora AI Local — presence-trigger automation-synthesis override handlers."""

    def _entity_name_grounded_in_prompt(self, eid: str | None, msg: str) -> bool:
        """True when *eid*'s friendly_name, slug, or slug-as-words appears in *msg* (case-insensitive) — the same grounding test the A5 bucket's ``target_friendly_name_in_prompt`` applies to every emitted entity_id."""
        if not eid:
            return False
        pl = msg.lower()
        slug = eid.split(".", 1)[1] if "." in eid else eid
        if slug.lower() in pl or slug.replace("_", " ").lower() in pl:
            return True
        state = self._hass.states.get(eid) if self._hass else None
        fname = str((state.attributes or {}).get("friendly_name") or "") if state else ""
        return bool(fname) and fname.lower() in pl

    def _maybe_presence_automation_envelope(self) -> str | None:
        """Deterministically build a presence-duration automation envelope for prompts like "turn off the lights when no motion is detected in the living room for 10 minutes" / "if nobody is in the office for 15 minutes, turn off the office light"."""
        msg = self._automation_prompt()
        if msg is None:
            return None
        # Require an explicit "for N <unit>" duration — this is what the LoRA misreads as a clock time and what the bucket's checks key on.
        dur = re.search(r"\bfor\s+(\d+)\s+(second|minute|hour)s?\b", msg)
        if dur is None:
            return None
        n = int(dur.group(1))
        unit = dur.group(2)
        if n <= 0:
            return None
        # Require a presence/occupancy phrase so we only intercept genuine presence-duration automations (not generic "for N minutes" delays).
        absence = bool(
            re.search(
                r"\b(no\s+motion|no\s+movement|nobody|no\s+one|noone|"
                r"empty|unoccupied|vacant|no\s+presence|not\s+detected|"
                r"no\s+activity|is\s+absent|away)\b",
                msg,
            )
        )
        presence_signal = absence or bool(
            re.search(
                r"\b(motion|presence|occupanc|someone|anyone|anybody|is\s+home)\b",
                msg,
            )
        )
        if not presence_signal:
            return None
        # Which room is the prompt about (if any)?
        room = next((w for w in self._PRESENCE_ROOM_WORDS if w in msg), "")
        # Resolve a REAL presence-class trigger entity.
        presence_eid, presence_domain = self._resolve_presence_entity(room)
        if presence_eid is None:
            return None
        # Resolve the REAL target device to actuate ("turn off the <subject>").
        action_eid, action_domain = self._resolve_presence_action_target(msg, room)
        if action_eid is None:
            return None
        turn_off = not bool(re.search(r"\bturn\s+on\b", msg)) or bool(
            re.search(r"\bturn\s+off\b", msg)
        )
        service = f"{action_domain}.{'turn_off' if turn_off else 'turn_on'}"
        # Absence prompts watch for the presence entity clearing; a person/device_tracker clears to "not_home", a binary sensor to "off".
        if presence_domain in ("person", "device_tracker"):
            to_state = "not_home" if absence else "home"
        else:
            to_state = "off" if absence else "on"
        for_field: dict[str, int] = {f"{unit}s": n}
        trigger = {
            "trigger": "state",
            "entity_id": presence_eid,
            "to": to_state,
            "for": for_field,
        }
        action = {
            "service": service,
            "target": {"entity_id": action_eid},
            "data": {},
        }
        unit_label = f"{n} {unit}{'s' if n != 1 else ''}"
        verb = "off" if turn_off else "on"
        automation: dict[str, Any] = {
            "alias": "Presence Auto Off"[:255],
            "description": (
                f"Turns {verb} {action_eid} when {presence_eid} stays "
                f"'{to_state}' for {unit_label}. Triggers on the presence "
                f"state change and waits the full duration before acting."
            ),
            "triggers": [trigger],
            "conditions": [],
            "actions": [action],
        }
        response = (
            f"This automation turns {verb} **{action_eid}** when "
            f"**{presence_eid}** has been '{to_state}' for **{unit_label}**. "
            f"It fires only after the state has held the whole duration. "
            f"Want me to also send a notification when it runs?"
        )
        return self._emit_automation_envelope(automation, response)

    def _maybe_plain_presence_automation_envelope(self) -> str | None:
        """Deterministically build a presence-``state`` automation envelope for the plain ``automation.presence`` class: "turn off all the lights when nobody is home" / "when everyone leaves, turn off the heat" / "turn off the lights when the house is empty" / "turn off the office light when the office is empty" / "when there's no motion in the living room, turn off the lights" / "turn off the bedroom fan when nobody is in the bedroom"."""
        msg = self._automation_prompt()
        if msg is None:
            return None
        # Require a presence/absence phrase.
        absence = bool(
            re.search(
                r"\b(no\s+motion|no\s+movement|nobody|no\s+one|noone|"
                r"everyone\s+leaves|everybody\s+leaves|everyone\s+is\s+out|"
                r"everybody\s+is\s+out|empty|unoccupied|vacant|no\s+presence|"
                r"not\s+detected|no\s+activity|is\s+absent|away)\b",
                msg,
            )
        )
        presence_signal = absence or bool(
            re.search(
                r"\b(motion|presence|occupanc|someone|anyone|anybody|is\s+home|"
                r"is\s+occupied)\b",
                msg,
            )
        )
        if not presence_signal:
            return None
        # Which room is the prompt about (if any)?
        room = next((w for w in self._PRESENCE_ROOM_WORDS if w in msg), "")
        # Resolve a REAL presence-class trigger entity (person/device_tracker/ motion-occupancy binary_sensor/presence group), preferring a room-name match.
        presence_eid, presence_domain = self._resolve_presence_entity(room)
        if presence_eid is None:
            return None
        # Resolve the REAL target device to actuate ("turn off the <subject>").
        action_eid, action_domain = self._resolve_presence_action_target(msg, room)
        if action_eid is None:
            return None
        turn_off = not bool(re.search(r"\bturn\s+on\b", msg)) or bool(
            re.search(r"\bturn\s+off\b", msg)
        )
        service = f"{action_domain}.{'turn_off' if turn_off else 'turn_on'}"
        # A person/device_tracker clears to "not_home"; a binary sensor / group to "off".
        if presence_domain in ("person", "device_tracker"):
            to_state = "not_home" if absence else "home"
        else:
            to_state = "off" if absence else "on"
        trigger: dict[str, Any] = {
            "trigger": "state",
            "entity_id": presence_eid,
            "to": to_state,
        }
        # Carry an optional "for N <unit>" duration if the prompt names one (the presence-duration override normally handles those first, but keep the rule correct if one slips through).
        dur = re.search(r"\bfor\s+(\d+)\s+(second|minute|hour)s?\b", msg)
        if dur is not None and int(dur.group(1)) > 0:
            trigger["for"] = {f"{dur.group(2)}s": int(dur.group(1))}
        action = {
            "service": service,
            "target": {"entity_id": action_eid},
            "data": {},
        }
        verb = "off" if turn_off else "on"
        automation: dict[str, Any] = {
            "alias": "Presence Auto Off"[:255],
            "description": (
                f"Turns {verb} {action_eid} when {presence_eid} changes to "
                f"'{to_state}'. Triggers on the presence state change."
            ),
            "triggers": [trigger],
            "conditions": [],
            "actions": [action],
        }
        response = (
            f"This automation turns {verb} **{action_eid}** when "
            f"**{presence_eid}** changes to '{to_state}'. It fires on the "
            f"presence state change. Want me to also add a short delay "
            f"before it acts?"
        )
        return self._emit_automation_envelope(automation, response)

    def _resolve_presence_entity(self, room: str) -> tuple[str | None, str]:
        """Pick a real presence-class entity for a presence-duration trigger, preferring genuine presence domains and a room-name match."""
        best: tuple[int, str, str] | None = None
        # Lower rank = better.
        domain_rank = {
            "person": 0,
            "device_tracker": 1,
            "binary_sensor": 2,
            "group": 3,
        }
        for domain, base_rank in domain_rank.items():
            for state in self._filtered_domain_states(domain):
                eid = state.entity_id
                attrs = state.attributes or {}
                fname = str(attrs.get("friendly_name") or "").lower()
                dclass = str(attrs.get("device_class") or "").lower()
                if domain == "binary_sensor":
                    if dclass not in ("motion", "occupancy", "presence") and not any(
                        k in eid or k in fname for k in ("motion", "presence", "occupanc")
                    ):
                        continue
                elif domain == "group" and not any(
                    k in eid or k in fname
                    for k in ("presence", "occupanc", "home", "people", "person")
                ):
                    continue
                slug_words = eid.split(".", 1)[-1].replace("_", " ")
                room_hit = bool(room) and (room in slug_words or room in fname)
                rank = base_rank * 2 + (0 if room_hit else 1)
                if best is None or rank < best[0]:
                    best = (rank, eid, domain)
        if best is None:
            return (None, "")
        return (best[1], best[2])

    def _resolve_presence_action_target(self, msg: str, room: str) -> tuple[str | None, str]:
        """Resolve the real entity the automation should actuate from the prompt's "turn off the <subject>" clause, preferring a room match."""
        # Map the subject noun to a controllable domain.
        if re.search(r"\bfans?\b", msg):
            domain = "fan"
        elif re.search(r"\blights?\b|\blamps?\b", msg):
            domain = "light"
        elif re.search(r"\b(tv|television|speaker|media)\b", msg):
            domain = "media_player"
        elif re.search(r"\b(plug|outlet|switch)\b", msg):
            domain = "switch"
        else:
            domain = "light"
        states = self._filtered_domain_states(domain)
        if not states:
            return (None, "")
        # Prefer an entity whose name matches the room; else fall back to the first (sorted) real entity of the domain so the action is always grounded in a real entity_id.
        room_match: str | None = None
        for state in sorted(states, key=lambda s: s.entity_id):
            eid = state.entity_id
            fname = str((state.attributes or {}).get("friendly_name") or "").lower()
            slug_words = eid.split(".", 1)[-1].replace("_", " ")
            if room and (room in slug_words or room in fname):
                room_match = eid
                break
        if room_match is not None:
            return (room_match, domain)
        fallback = sorted(states, key=lambda s: s.entity_id)[0].entity_id
        return (fallback, domain)
