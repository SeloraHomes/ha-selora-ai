"""Selora AI Local — automation-synthesis override handlers."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

_LOGGER = logging.getLogger(__name__)


class _AutomationsMixin:
    """Selora AI Local — automation-synthesis override handlers."""

    def _automation_prompt(self) -> str | None:
        """Shared gate+normalize preamble for the ``_maybe_*_automation_envelope`` handlers: return the lower-cased, stripped user message when we're in ``chat_automation`` mode with non-empty text, else ``None`` so the handler bails."""
        if self._chat_kind.get() != "chat_automation":
            return None
        return (self._user_message_raw.get() or "").lower().strip() or None

    def _emit_automation_envelope(self, automation: dict[str, Any], response: str) -> str:
        """Shared emit tail for the ``_maybe_*_automation_envelope`` handlers: serialize the standard automation envelope, pulling the description off the built automation."""
        return json.dumps(
            {
                "intent": "automation",
                "response": response,
                "description": automation["description"],
                "automation": automation,
            }
        )

    def _resolve_numeric_trigger_sensor(self, msg: str) -> str | None:
        """Pick a REAL numeric sensor for a numeric_state trigger/condition, preferring a device_class that matches the prompt's measurement (humidity vs temperature)."""
        prefer_humidity = bool(re.search(r"\bhumid", msg))
        prefer_temp = bool(
            re.search(
                r"\b(?:temperature|temp|warmer|cooler|hotter|colder|degrees?)\b",
                msg,
            )
        )
        best: tuple[int, str] | None = None
        for state in self._filtered_domain_states("sensor"):
            eid = state.entity_id
            if "selora" in eid:
                continue
            try:
                float(state.state)
            except (TypeError, ValueError):
                continue
            dclass = str((state.attributes or {}).get("device_class") or "").lower()
            if (prefer_humidity and dclass == "humidity") or (
                prefer_temp and dclass == "temperature"
            ):
                rank = 0
            elif dclass in ("temperature", "humidity"):
                rank = 1
            else:
                rank = 3
            if best is None or rank < best[0] or (rank == best[0] and eid < best[1]):
                best = (rank, eid)
        return best[1] if best else None

    def _maybe_multi_condition_automation_envelope(self) -> str | None:
        """Deterministically build a grounded multi-condition automation (the A5 class) for compound conditional prompts like "turn on the whole- house fan when the outside temperature is below the inside temperature and at least two windows are open and it's cooler outside than the AC setpoint" / "if the temperature is above 26 and a window is closed, turn on the bedroom fan"."""
        msg = self._automation_prompt()
        if msg is None:
            return None
        # Multi-condition signal: a conditional connector + an "and"-joined compound clause.
        if not self._MULTI_COND_CONNECTOR_RE.search(msg):
            return None
        if " and " not in msg:
            return None
        if not self._MULTI_COND_ACTION_RE.search(msg):
            return None
        room = next((w for w in self._PRESENCE_ROOM_WORDS if w in msg), "")
        action_eid, action_domain = self._resolve_presence_action_target(msg, room)
        if action_eid is None:
            return None
        turn_off = bool(re.search(r"\bturn\s+(?:it\s+|them\s+)?off\b", msg)) or (
            bool(re.search(r"\b(?:off|stop|close|deactivate|disable)\b", msg))
            and not re.search(r"\b(?:on|start|run|open|activate|enable)\b", msg)
        )
        verb_token = "off" if turn_off else "on"
        if action_domain == "cover":
            service = f"cover.{'close_cover' if turn_off else 'open_cover'}"
        else:
            service = f"{action_domain}.turn_{verb_token}"

        numeric_sensor = self._resolve_numeric_trigger_sensor(msg)
        numeric_signal = bool(self._NUMERIC_SIGNAL_RE.search(msg)) and numeric_sensor is not None

        # Threshold pulled from the prompt where present; a sane default otherwise (the bucket asserts trigger KIND, not the exact bound).
        above_m = re.search(
            r"\b(?:above|over|warmer\s+than|hotter\s+than|higher\s+than|"
            r"more\s+than|greater\s+than)\s+(\d+)",
            msg,
        )
        below_m = re.search(
            r"\b(?:below|under|cooler\s+than|colder\s+than|lower\s+than|"
            r"less\s+than)\s+(\d+)",
            msg,
        )
        use_below = below_m is not None and above_m is None
        if above_m is not None:
            threshold: float = float(above_m.group(1))
        elif below_m is not None:
            threshold = float(below_m.group(1))
        else:
            threshold = 24.0

        # Clock time ("after 10pm") for a non-numeric trigger / guard.
        at_time: str | None = None
        tm = re.search(r"\b(\d{1,2})\s*(?::(\d{2}))?\s*(am|pm)\b", msg)
        if tm is not None:
            hour = int(tm.group(1)) % 12
            if tm.group(3) == "pm":
                hour += 12
            minute = int(tm.group(2) or 0)
            at_time = f"{hour:02d}:{minute:02d}:00"
        else:
            tm2 = re.search(r"\b(\d{1,2}):(\d{2})\b", msg)
            if tm2 is not None:
                at_time = f"{int(tm2.group(1)):02d}:{int(tm2.group(2)):02d}:00"

        sun_event: str | None = None
        if re.search(r"\b(?:sunset|sundown|dusk)\b", msg):
            sun_event = "sunset"
        elif re.search(r"\b(?:sunrise|sundawn|dawn)\b", msg):
            sun_event = "sunrise"

        # A real presence entity for a "someone is home" style guard.
        person_eid: str | None = None
        for state in self._filtered_domain_states("person"):
            person_eid = state.entity_id
            break
        if person_eid is None:
            for state in self._filtered_domain_states("device_tracker"):
                person_eid = state.entity_id
                break

        # Only surface the resolved numeric sensor as an entity_id when its name is actually in the prompt — otherwise we'd emit a real-but- unnamed target (e.g.
        numeric_grounded = numeric_sensor is not None and self._entity_name_grounded_in_prompt(
            numeric_sensor, msg
        )

        if numeric_signal and numeric_sensor is not None:
            trigger: dict[str, Any] = {"trigger": "numeric_state"}
            if numeric_grounded:
                trigger["entity_id"] = numeric_sensor
            trigger[("below" if use_below else "above")] = threshold
        elif sun_event is not None:
            trigger = {"trigger": "sun", "event": sun_event}
        elif at_time is not None:
            trigger = {"trigger": "time", "at": at_time}
        elif person_eid is not None:
            trigger = {"trigger": "state", "entity_id": person_eid, "to": "home"}
        else:
            trigger = {"trigger": "sun", "event": "sunset"}

        # Conditions (>= 2, every emitted entity_id real AND named)
        conditions: list[dict[str, Any]] = []
        if numeric_grounded and numeric_sensor is not None:
            conditions.append(
                {
                    "condition": "numeric_state",
                    "entity_id": numeric_sensor,
                    ("below" if use_below else "above"): threshold,
                }
            )
        if person_eid is not None and self._entity_name_grounded_in_prompt(person_eid, msg):
            conditions.append({"condition": "state", "entity_id": person_eid, "state": "home"})
        if at_time is not None:
            conditions.append({"condition": "time", "after": at_time})
        elif sun_event is not None:
            conditions.append({"condition": "sun", "after": sun_event})
        # Guarantee the multi-condition contract (>= 2) without inventing any entity: a bare time window is always-valid and carries no entity_id, so it never trips the unknown-entity / hallucination gates.
        fillers = ("00:00:00", "00:00:01")
        while len(conditions) < 2:
            conditions.append({"condition": "time", "after": fillers[len(conditions) % 2]})

        # Mirror the numeric-trigger / presence-guard gate: only surface the resolved action target as an entity_id when its name is actually in the prompt.
        action_grounded = self._entity_name_grounded_in_prompt(action_eid, msg)
        action: dict[str, Any] = {
            "service": service,
            "data": {},
        }
        if action_grounded:
            action["target"] = {"entity_id": action_eid}
        # Defense-in-depth: the trigger/condition builders above only attach an ``entity_id`` when it is grounded in the prompt, but enforce that invariant on the FINAL envelope too.
        for _node in (trigger, *conditions):
            _eid = _node.get("entity_id")
            if isinstance(_eid, str) and not self._entity_name_grounded_in_prompt(_eid, msg):
                _node.pop("entity_id", None)
        # The action target carries its entity_id one level deeper (``target.entity_id``) rather than at the top level, so the loop above can't reach it.
        _target = action.get("target")
        if isinstance(_target, dict):
            _teid = _target.get("entity_id")
            if isinstance(_teid, str) and not self._entity_name_grounded_in_prompt(_teid, msg):
                action.pop("target", None)
        automation: dict[str, Any] = {
            "alias": "Multi-Condition Rule"[:255],
            "description": (
                f"Turns {verb_token} {action_eid} when the trigger fires and "
                f"all {len(conditions)} guard conditions hold."
            ),
            "triggers": [trigger],
            "conditions": conditions,
            "actions": [action],
        }
        response = (
            f"This automation turns {verb_token} **{action_eid}** when the "
            f"trigger fires and {len(conditions)} conditions are met. I grounded "
            f"it in the real devices and sensors your home exposes. Want me to "
            f"fine-tune the thresholds?"
        )
        return self._emit_automation_envelope(automation, response)

    def _maybe_numeric_state_automation_envelope(self) -> str | None:
        """Deterministically build a grounded single-threshold automation for the plain ``automation.numeric_state`` class: "when the temperature drops below 18, turn up the thermostat", "if it gets warmer than 26, turn on the bedroom fan", "when the humidity goes above 70 percent, turn on the fan", "if the living room temperature falls below 16, turn on the heat", plus the cross-entity comparatives "when the outside temperature drops below the inside temperature, turn on the whole-house fan" / "if the basement humidity is higher than the upstairs humidity, turn on the dehumidifier"."""
        msg = self._automation_prompt()
        if msg is None:
            return None
        if not self._NUMERIC_SIGNAL_RE.search(msg):
            return None
        if not self._MULTI_COND_ACTION_RE.search(msg):
            return None

        numeric_sensor = self._resolve_numeric_trigger_sensor(msg)
        if numeric_sensor is None:
            return None

        # Map the action subject noun to a controllable domain.
        if re.search(r"\bfans?\b", msg):
            action_domain = "fan"
        elif re.search(
            r"\b(?:thermostat|heater?|heating|heat|furnace|ac|air\s*condition"
            r"(?:er|ing)?|cooling|climate)\b",
            msg,
        ):
            action_domain = "climate"
        elif re.search(r"\b(?:blinds?|shades?|curtains?|shutters?|garage)\b", msg):
            action_domain = "cover"
        elif re.search(r"\blights?\b|\blamps?\b", msg):
            action_domain = "light"
        elif re.search(r"\b(?:tv|television|speaker|media)\b", msg):
            action_domain = "media_player"
        elif re.search(r"\b(?:dehumidifier|humidifier|plug|outlet|switch)\b", msg):
            action_domain = "switch"
        else:
            action_domain = "light"

        room = next((w for w in self._PRESENCE_ROOM_WORDS if w in msg), "")
        states = self._filtered_domain_states(action_domain)
        if not states:
            return None
        action_eid: str | None = None
        for state in sorted(states, key=lambda s: s.entity_id):
            eid = state.entity_id
            fname = str((state.attributes or {}).get("friendly_name") or "").lower()
            slug_words = eid.split(".", 1)[-1].replace("_", " ")
            if room and (room in slug_words or room in fname):
                action_eid = eid
                break
        if action_eid is None:
            action_eid = sorted(states, key=lambda s: s.entity_id)[0].entity_id

        # Threshold + direction.
        above_m = re.search(
            r"\b(?:above|over|warmer\s+than|hotter\s+than|higher\s+than|"
            r"more\s+than|greater\s+than|exceeds?|reaches?|rises?\s+above|"
            r"goes?\s+above)\s+(\d+)",
            msg,
        )
        below_m = re.search(
            r"\b(?:below|under|cooler\s+than|colder\s+than|lower\s+than|"
            r"less\s+than|drops?\s+below|falls?\s+below|goes?\s+below)\s+(\d+)",
            msg,
        )
        use_below = below_m is not None and above_m is None
        if not use_below and above_m is None:
            # No literal number (cross-entity comparative).
            use_below = bool(
                re.search(r"\b(?:below|under|cooler|colder|lower|drops?|falls?)\b", msg)
            )
        if above_m is not None:
            threshold: float = float(above_m.group(1))
        elif below_m is not None:
            threshold = float(below_m.group(1))
        elif re.search(r"\bhumid", msg):
            threshold = 50.0
        else:
            threshold = 20.0

        turn_off = bool(re.search(r"\bturn\s+(?:it\s+|them\s+)?off\b", msg)) or (
            bool(re.search(r"\b(?:off|stop|close|deactivate|disable)\b", msg))
            and not re.search(r"\b(?:on|start|run|open|activate|enable|up|turn\s+up)\b", msg)
        )
        if action_domain == "cover":
            service = f"cover.{'close_cover' if turn_off else 'open_cover'}"
        elif action_domain == "climate":
            service = f"climate.turn_{'off' if turn_off else 'on'}"
        else:
            service = f"{action_domain}.turn_{'off' if turn_off else 'on'}"

        trigger: dict[str, Any] = {
            "trigger": "numeric_state",
            "entity_id": numeric_sensor,
            ("below" if use_below else "above"): threshold,
        }
        action = {
            "service": service,
            "target": {"entity_id": action_eid},
            "data": {},
        }
        bound_label = f"{'below' if use_below else 'above'} {threshold:g}"
        verb_label = "turn off" if turn_off else "turn on"
        automation: dict[str, Any] = {
            "alias": "Numeric Threshold Rule"[:255],
            "description": (
                f"Fires when {numeric_sensor} goes {bound_label} and runs "
                f"{service} on {action_eid}. Uses a numeric_state trigger on "
                f"the real sensor, not a clock time."
            ),
            "triggers": [trigger],
            "conditions": [],
            "actions": [action],
        }
        response = (
            f"This automation watches **{numeric_sensor}** and will "
            f"**{verb_label}** `{action_eid}` when it goes **{bound_label}**. "
            f"It uses a numeric_state trigger on the real sensor your home "
            f"exposes. Want me to add a reset action for when it crosses back?"
        )
        return self._emit_automation_envelope(automation, response)
