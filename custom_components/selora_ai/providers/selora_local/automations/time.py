"""Selora AI Local — time/sun-trigger automation-synthesis override handlers."""

from __future__ import annotations

import re
from typing import Any


class _AutomationsTimeMixin:
    """Selora AI Local — time/sun-trigger automation-synthesis override handlers."""

    def _maybe_duration_automation_envelope(self) -> str | None:
        """Deterministically build a sustained-state ("for N minutes") auto-off automation for the ``edge.duration_vs_clock`` class: "turn off the porch light if it's been on for 30 minutes" / "if the bathroom fan runs for 20 minutes, turn it off" / "turn off the hallway light after it's been on for 10 minutes"."""
        msg = self._automation_prompt()
        if msg is None:
            return None
        # Require an explicit "for N <unit>" duration — this is exactly what the LoRA misreads as a clock time and what the bucket's checks key on.
        dur = re.search(r"\bfor\s+(\d+)\s+(second|minute|hour)s?\b", msg)
        if dur is None:
            return None
        n = int(dur.group(1))
        unit = dur.group(2)
        if n <= 0:
            return None
        # Require a sustained-on phrase: the device has been on / running / left on for the duration.
        sustained = bool(
            re.search(
                r"\b(been\s+on|stay(?:s|ed)?\s+on|left\s+on|still\s+on|"
                r"on\s+for|runs?\s+for|running\s+for|been\s+running)\b",
                msg,
            )
        )
        if not sustained:
            return None
        # Require an explicit turn-off verb — this class is "auto-off after N".
        if not re.search(r"\bturn\s+(?:it\s+|them\s+)?off\b", msg):
            return None
        room = next((w for w in self._PRESENCE_ROOM_WORDS if w in msg), "")
        # Resolve the REAL device to actuate ("turn off the <subject>").
        action_eid, action_domain = self._resolve_presence_action_target(msg, room)
        if action_eid is None:
            return None
        for_field: dict[str, int] = {f"{unit}s": n}
        trigger = {
            "trigger": "state",
            "entity_id": action_eid,
            "to": "on",
            "for": for_field,
        }
        action = {
            "service": f"{action_domain}.turn_off",
            "target": {"entity_id": action_eid},
            "data": {},
        }
        unit_label = f"{n} {unit}{'s' if n != 1 else ''}"
        automation: dict[str, Any] = {
            "alias": "Auto Off After Duration"[:255],
            "description": (
                f"Turns off {action_eid} after it has stayed 'on' for "
                f"{unit_label}. Triggers on the device's state holding 'on' "
                f"the full duration before acting — a state trigger with a "
                f"'for:' duration, not a clock time."
            ),
            "triggers": [trigger],
            "conditions": [],
            "actions": [action],
        }
        response = (
            f"This automation turns off **{action_eid}** once it has been "
            f"'on' for **{unit_label}**. It uses a state trigger with a "
            f"`for:` duration (not a time-of-day trigger), so it fires only "
            f"after the device has stayed on the whole time. Want me to also "
            f"send a notification when it runs?"
        )
        return self._emit_automation_envelope(automation, response)

    def _maybe_sun_automation_envelope(self) -> str | None:
        """Deterministically build a grounded sun-event automation for the ``automation.sun`` class: "turn on the kitchen light at sunset", "close the garage at sundown", "turn off all the lights at sunrise", "turn on the porch light 30 minutes before sunset", "close the blinds 15 minutes after sunrise"."""
        msg = self._automation_prompt()
        if msg is None:
            return None
        m = self._SUN_AUTOMATION_TRIGGER_RE.search(msg)
        if m is None:
            return None
        phrase = m.group(0)
        sun_event = "sunset" if re.search(r"sunset|sundown|dusk", phrase) else "sunrise"

        targets, domain = self._resolve_sun_action_targets(msg)
        if not targets:
            return None

        # Verb → service.
        off_verb = bool(
            re.search(
                r"\b(?:turn|switch|power)\s+(?:it\s+|them\s+|the\s+\w+\s+)?off\b"
                r"|\b(?:close|closing|lower|shut|stop|deactivate|disable)\b",
                msg,
            )
        )
        on_verb = bool(
            re.search(
                r"\b(?:turn|switch|power)\s+(?:it\s+|them\s+|the\s+\w+\s+)?on\b"
                r"|\b(?:open|opening|raise|activate|enable|start|run)\b",
                msg,
            )
        )
        if domain == "cover":
            is_off = off_verb and not on_verb
            service = "cover.close_cover" if is_off else "cover.open_cover"
            verb_label = "close" if is_off else "open"
        else:
            is_off = off_verb and not on_verb
            service = f"{domain}.turn_off" if is_off else f"{domain}.turn_on"
            verb_label = "turn off" if is_off else "turn on"

        trigger: dict[str, Any] = {"trigger": "sun", "event": sun_event}
        offset = self._parse_sun_offset(msg)
        if offset is not None:
            trigger["offset"] = offset

        entity_field: str | list[str] = targets if len(targets) > 1 else targets[0]
        action = {
            "service": service,
            "target": {"entity_id": entity_field},
            "data": {},
        }
        target_label = targets[0] if len(targets) == 1 else f"{len(targets)} device(s)"
        offset_label = ""
        if offset is not None:
            offset_label = f" (offset {offset})"
        automation: dict[str, Any] = {
            "alias": f"At {sun_event.title()} {verb_label.title()} {target_label}"[:255],
            "description": (
                f"Fires on the {sun_event} sun event{offset_label} and runs "
                f"{service} on {', '.join(targets)}. Uses a sun trigger with "
                f"event '{sun_event}', not a fixed clock time."
            ),
            "triggers": [trigger],
            "conditions": [],
            "actions": [action],
        }
        response = (
            f"This automation runs at **{sun_event}**{offset_label} and will "
            f"**{verb_label}** {', '.join('`' + t + '`' for t in targets)}. "
            f"It uses a sun trigger (event `{sun_event}`) so it follows the "
            f"actual {sun_event} time year-round. Want me to add a condition "
            f"(e.g. only on weekdays)?"
        )
        return self._emit_automation_envelope(automation, response)

    def _resolve_sun_action_targets(self, msg: str) -> tuple[list[str], str]:
        """Resolve the REAL entity_id(s) a sun automation should actuate from the prompt's target noun."""
        collective = bool(re.search(r"\b(?:all|every)\b", msg))
        room = next((w for w in self._PRESENCE_ROOM_WORDS if w in msg), "")
        for pattern, domain, keywords in self._SUN_TARGET_NOUNS:
            if not re.search(pattern, msg):
                continue
            states = sorted(self._filtered_domain_states(domain), key=lambda s: s.entity_id)
            if not states:
                return ([], "")

            def _name_parts(state: Any) -> str:
                eid = state.entity_id
                fname = str((state.attributes or {}).get("friendly_name") or "")
                slug_words = eid.split(".", 1)[-1].replace("_", " ")
                return f"{eid} {slug_words} {fname}".lower()

            if collective:
                return ([s.entity_id for s in states], domain)
            if room:
                room_hits = [s.entity_id for s in states if room in _name_parts(s)]
                if room_hits:
                    return (room_hits, domain)
            # Only match on keywords the PROMPT actually names — otherwise "close the blinds" would also grab ``cover.garage_door`` (the "garage"/"door" keywords live in the same domain bucket).
            prompt_keywords = [k for k in keywords if k in msg]
            if prompt_keywords:
                kw_hits = [
                    s.entity_id for s in states if any(k in _name_parts(s) for k in prompt_keywords)
                ]
                if kw_hits:
                    return (kw_hits, domain)
            # Noun matched a controllable domain (e.g.
            return ([states[0].entity_id], domain)
        return ([], "")

    def _parse_sun_offset(self, msg: str) -> str | None:
        """Parse a sun-relative offset ("15 minutes after sunrise", "30 minutes before sunset", "an hour before sunset", "half an hour after dusk") into HA's signed ``±HH:MM:SS`` offset string."""
        direction: str | None = None
        seconds = 0
        m = re.search(
            r"(\d+)\s*(second|sec|minute|min|hour|hr)s?\s+(before|after)\s+"
            r"(?:sunset|sundown|dusk|sunrise|sundawn|dawn)",
            msg,
        )
        if m is not None:
            n = int(m.group(1))
            unit = m.group(2)
            direction = m.group(3)
            if unit.startswith("h"):
                seconds = n * 3600
            elif unit.startswith("m"):
                seconds = n * 60
            else:
                seconds = n
        elif re.search(
            r"half\s+an\s+hour\s+(before|after)\s+"
            r"(?:sunset|sundown|dusk|sunrise|sundawn|dawn)",
            msg,
        ):
            seconds = 1800
            direction = "before" if re.search(r"half\s+an\s+hour\s+before", msg) else "after"
        elif re.search(
            r"an?\s+hour\s+(before|after)\s+"
            r"(?:sunset|sundown|dusk|sunrise|sundawn|dawn)",
            msg,
        ):
            seconds = 3600
            direction = "before" if re.search(r"an?\s+hour\s+before", msg) else "after"
        if direction is None or seconds <= 0:
            return None
        sign = "-" if direction == "before" else "+"
        hh = seconds // 3600
        mm = (seconds % 3600) // 60
        ss = seconds % 60
        return f"{sign}{hh:02d}:{mm:02d}:{ss:02d}"
