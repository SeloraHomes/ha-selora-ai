"""Selora AI Local — deterministic vacuum / fan command handlers."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

_LOGGER = logging.getLogger(__name__)


# Vacuum service canonicalisation: a verb the model invented, or one HA deprecated,
# rewritten to the service that actually does the job.
#
# ``stop`` is deliberately NOT here. ``vacuum.stop`` is a real Home Assistant service
# and halting mid-room is a different physical act from driving back to the dock --
# rewriting it sends a robot home when the user asked it to stand still. The
# deterministic handlers below emit ``return_to_base`` directly where that IS the
# intent, so nothing here depends on the rewrite.
_SELORA_LOCAL_VACUUM_SERVICE_CANON: dict[str, str] = {
    "turn_on": "start",
    "clean_spot": "start",
    "start_pause": "start",
    "resume": "start",
    # HA deprecated vacuum.turn_off for the platforms that still accept it, and
    # "turn the vacuum off" reads as "stop cleaning and go home".
    "turn_off": "return_to_base",
    "return_home": "return_to_base",
}


def _canonicalize_vacuum_service(service: str) -> str:
    """Rewrite a ``vacuum.<verb>`` service to a supported equivalent."""
    if not service.startswith("vacuum."):
        return service
    verb = service.split(".", 1)[1]
    mapped = _SELORA_LOCAL_VACUUM_SERVICE_CANON.get(verb)
    if mapped is None:
        return service
    return f"vacuum.{mapped}"


class _CommandsVacuumFanMixin:
    """Selora AI Local — deterministic vacuum / fan command handlers."""

    def _maybe_vacuum_envelope(self) -> str | None:
        """Deterministically build a ``vacuum`` command envelope when the current turn names a real vacuum entity and carries a vacuum action verb (any kind except ``chat_automation``; see the gate note below for why this is no longer restricted to ``chat_command``)."""
        # NOT gated on ``chat_command`` (mirroring the media override ``_maybe_command_envelope``, which is deliberately ungated for the same reason): the v0.4.8 router frequently MIS-BUCKETS a short vacuum imperative ("Stop vacuuming", "Turn on the vacuum", "Please start cleaning") as ``chat_answer`` rather than ``chat_command``.
        if self._current_chat_kind() == "chat_automation":
            return None
        return self._resolve_vacuum_command(self._current_user_message().lower().strip())

    def _resolve_vacuum_command(self, msg: str) -> str | None:
        """ContextVar-independent vacuum command resolver."""
        if not msg:
            return None
        # State-question / interrogative guard: a mis-routed COMMAND is imperative ("stop vacuuming"); a genuine answer turn ("is the vacuum running?", "where is the roomba?") must stay an answer.
        if msg.endswith("?") or re.match(
            r"(is|are|was|were|does|do|did|can|could|will|would|has|have|"
            r"what|where|when|why|how|which|who|tell|show|list)\b",
            msg,
        ):
            return None
        vacuums = list(self._filtered_domain_states("vacuum"))
        if not vacuums:
            # Live ``hass.states`` read came back empty (or ``EntityFilter`` / the Selora exclude label hid the robot from the model-facing inventory) — fall back to this turn's injected entity snapshot so a lone/named vacuum still resolves under the documented conversion-pass state-read race.
            vacuums = list(self._snapshot_domain_states("vacuum"))
        if not vacuums and self._hass is not None:
            # Both the filtered inventory AND the per-turn snapshot can be empty for a vacuum that synthetic_home registers state-only: ``EntityFilter`` drops an entity with no registry entry, and the keyword entity filter that builds the model-facing snapshot drops the robot for verb-only phrasings ("Stop vacuuming", "Turn on the vacuum") because the friendly_name / slug ("Roborock Downstairs" / "roborock_downstairs") carries no "vacuum" token to match.
            vacuums = [
                s
                for s in self._hass.states.async_all()
                if "." in s.entity_id and s.entity_id.split(".", 1)[0] == "vacuum"
            ]
        if not vacuums:
            return None
        # Resolve the target vacuum by friendly_name / slug match, preferring the longest (most specific) hit so a generic "vacuum" can't shadow "Roborock Downstairs".
        target_eid: str | None = None
        target_fname = ""
        best_len = 0
        for state in vacuums:
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
        # A vacuum reference is either an explicit robot noun OR a cleaning/sweeping/mopping verb.
        references_vacuum = bool(
            re.search(
                r"\b(vacuum|vacuuming|roomba|roborock|robot|"
                r"clean|cleaning|sweep|sweeping|mop|mopping)\b",
                msg,
            )
        )
        if target_eid is None:
            # No name match — fall back to the lone vacuum, but only when the message clearly refers to a vacuum so we never hijack a turn that just happens to carry a start/stop verb for another domain.
            if len(vacuums) == 1 and references_vacuum:
                lone = vacuums[0]
                target_eid = lone.entity_id
                target_fname = (
                    str((lone.attributes or {}).get("friendly_name") or "").strip()
                    or lone.entity_id
                )
            else:
                return None
        elif not references_vacuum:
            return None
        # Map the verb to the canonical service.
        if (
            (
                re.search(r"\b(return|back|go|send)\b", msg)
                and re.search(r"\b(base|dock|home|charge|charging)\b", msg)
            )
            or "to base" in msg
            or "to dock" in msg
        ):
            service = "vacuum.return_to_base"
            r_text = f"Sending {target_fname} back to its dock."
        elif "spot" in msg:
            # ``clean_spot`` is not advertised by a StateVacuum (nor the synthetic_home vacuum), so it executes as a no-op; ``start`` produces the intended ``cleaning`` activity.
            service = "vacuum.start"
            r_text = f"Spot-cleaning with {target_fname}."
        elif re.search(r"\bpause\b", msg):
            service = "vacuum.pause"
            r_text = f"Paused {target_fname}."
        elif re.search(
            r"\b(stop|halt|turn\s+off|switch\s+off|shut\s+off|power\s+off|"
            r"finish|done|cease|quit)\b",
            msg,
        ):
            service = "vacuum.return_to_base"
            r_text = f"Sending {target_fname} back to its dock."
        elif re.search(
            r"\b(start|clean|cleaning|begin|resume|run|go|"
            r"turn\s+on|switch\s+on|power\s+on|vacuum)\b",
            msg,
        ):
            service = "vacuum.start"
            r_text = f"Started {target_fname}."
        else:
            return None
        call: dict[str, Any] = {
            "service": service,
            "target": {"entity_id": target_eid},
        }
        return json.dumps({"intent": "command", "response": r_text, "calls": [call]})

    def _recover_vacuum_command_from_raw(self, raw: str) -> str | None:
        """Recover a vacuum command from raw LoRA output that failed to parse as JSON — truncated mid-envelope (clipped inside the ``entity_id``) or single-quote drift the structural repair could not close."""
        if not raw or "vacuum." not in raw:
            return None
        # The closing quote of the service value is intentionally NOT required: the model's truncation point is nondeterministic across runs and can land exactly on (or just before) the closing quote (``{"c":[{"s":"vacuum.stop``), in which case requiring the trailing quote would drop an otherwise-complete, recoverable verb.
        svc_match = re.search(r"['\"]s['\"]\s*:\s*['\"](vacuum\.[a-z_]+)", raw)
        if svc_match is None:
            return None
        service = _canonicalize_vacuum_service(svc_match.group(1))
        # Only act on a slug that resolves to a supported StateVacuum verb; a truncated/garbled service that maps to nothing recognised is left to the existing salvage path rather than executed on a guess.
        if service not in {
            "vacuum.start",
            "vacuum.return_to_base",
            "vacuum.pause",
        }:
            return None
        vacuums = list(self._filtered_domain_states("vacuum"))
        if not vacuums and self._hass is not None:
            # ``_filtered_domain_states`` drops entities the Selora exclude label / EntityFilter hides from the MODEL, but a recoverable command already names a concrete ``vacuum.*`` target, so the robot is a legitimate command target even when it is filtered out of the model-facing inventory.
            vacuums = [
                s
                for s in self._hass.states.async_all()
                if "." in s.entity_id and s.entity_id.split(".", 1)[0] == "vacuum"
            ]
        if not vacuums:
            # ``self._hass`` itself can be unavailable during the conversion pass (the same ContextVar-propagation race that empties the ``_current_*`` reads).
            vacuums = list(self._snapshot_domain_states("vacuum"))
        if not vacuums:
            return None
        # Resolve the target.
        target_eid: str | None = None
        target_fname = ""
        ent_match = re.search(r"['\"]e['\"]\s*:\s*['\"](vacuum\.[a-z_]*)", raw)
        captured = ent_match.group(1) if ent_match else ""
        if captured:
            for state in vacuums:
                if state.entity_id.startswith(captured):
                    target_eid = state.entity_id
                    target_fname = (
                        str((state.attributes or {}).get("friendly_name") or "").strip()
                        or state.entity_id
                    )
                    break
        if target_eid is None and len(vacuums) == 1:
            lone = vacuums[0]
            target_eid = lone.entity_id
            target_fname = (
                str((lone.attributes or {}).get("friendly_name") or "").strip() or lone.entity_id
            )
        if target_eid is None:
            return None
        r_text = {
            "vacuum.start": f"Started {target_fname}.",
            "vacuum.return_to_base": f"Sending {target_fname} back to its dock.",
            "vacuum.pause": f"Paused {target_fname}.",
        }[service]
        call: dict[str, Any] = {
            "service": service,
            "target": {"entity_id": target_eid},
        }
        return json.dumps({"intent": "command", "response": r_text, "calls": [call]})

    @staticmethod
    def _fan_speed_percentage(msg: str) -> int | None:
        """Recover a fan speed from a command turn as a 0-100 percentage."""
        num = re.search(r"(\d{1,3})\s*(?:percent|%)", msg)
        if num is not None:
            return max(0, min(100, int(num.group(1))))
        if re.search(r"\bhigh\b", msg):
            return 100
        if re.search(r"\bmedium\b", msg):
            return 66
        if re.search(r"\blow\b", msg):
            return 33
        return None

    @staticmethod
    def _fan_missing_entity_id(msg: str) -> str | None:
        """Build a ``fan.<name>_fan`` entity_id for a fan the user named but the home doesn't have (e.g."""
        stop = frozenset(
            {
                "the",
                "a",
                "an",
                "my",
                "our",
                "your",
                "to",
                "at",
                "in",
                "on",
                "off",
                "up",
                "down",
                "please",
                "can",
                "you",
                "set",
                "turn",
                "switch",
                "start",
                "stop",
                "run",
                "power",
                "shut",
                "speed",
                "make",
                "put",
                "get",
            }
        )
        match = re.search(r"([a-z][a-z ]*?)\bfans?\b", msg)
        if match is None:
            return None
        words = [w for w in match.group(1).split() if w and w not in stop]
        if not words:
            return None
        return f"fan.{'_'.join(words)}_fan"

    def _fan_entities_in_named_area(self, msg: str, fans: list[Any]) -> list[str]:
        """Return fan entity_ids whose HA area name appears in ``msg``."""
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
        for state in fans:
            entry = ent_reg.async_get(state.entity_id)
            area_id = entry.area_id if entry else None
            if not area_id and entry and entry.device_id:
                dev = dev_reg.async_get(entry.device_id)
                area_id = dev.area_id if dev else None
            if area_id and area_id in matched_area_ids:
                result.append(state.entity_id)
        return result

    def _maybe_fan_envelope(self) -> str | None:
        """Deterministically build a ``fan`` command envelope when the current turn is a ``chat_command`` that names a real fan entity (or the whole fan collection)."""
        # Fire on command turns AND on clarification turns.
        kind = self._current_chat_kind()
        if kind not in ("chat_command", "chat_clarification"):
            return None
        raw = self._current_user_message() or ""
        msg = raw.lower().strip()
        if not msg or not re.search(r"\bfans?\b", msg):
            return None
        fans = self._filtered_domain_states("fan")
        if not fans:
            # Live ``hass.states`` read came back empty — fall back to this turn's injected entity snapshot so a lone/named fan still resolves under the documented conversion-pass state-read race.
            fans = self._snapshot_domain_states("fan")
        if not fans:
            return None
        off = bool(re.search(r"\b(turn\s+off|switch\s+off|shut\s+off|power\s+off|stop|off)\b", msg))
        on = bool(re.search(r"\b(turn\s+on|switch\s+on|power\s+on|on|start|run)\b", msg))
        pct = self._fan_speed_percentage(msg)
        collective = bool(re.search(r"\b(all|every)\b", msg))
        # Collective on/off across every fan in the home.
        if collective and (on or off) and pct is None:
            turn_off = off and not on
            service = "fan.turn_off" if turn_off else "fan.turn_on"
            calls = [{"service": service, "target": {"entity_id": s.entity_id}} for s in fans]
            verb = "Turned off" if turn_off else "Turned on"
            return json.dumps(
                {"intent": "command", "response": f"{verb} all fans.", "calls": calls}
            )
        # Resolve the single named fan (longest friendly_name / slug match, so a generic "Fan" can't shadow "Living Room Fan").
        target_eid: str | None = None
        target_fname = ""
        best_len = 0
        for state in fans:
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
        if target_eid is None and (on or off or pct is not None):
            # No friendly-name / slug match: resolve by AREA.
            area_fans = self._fan_entities_in_named_area(msg, fans)
            if area_fans:
                if pct is not None:
                    area_calls: list[dict[str, Any]] = [
                        {
                            "service": "fan.set_percentage",
                            "target": {"entity_id": eid},
                            "data": {"percentage": pct},
                        }
                        for eid in area_fans
                    ]
                    area_resp = f"Set fan speed to {pct}%."
                elif off and not on:
                    area_calls = [
                        {"service": "fan.turn_off", "target": {"entity_id": eid}}
                        for eid in area_fans
                    ]
                    area_resp = (
                        "Turned off the fan." if len(area_fans) == 1 else "Turned off the fans."
                    )
                else:
                    area_calls = [
                        {"service": "fan.turn_on", "target": {"entity_id": eid}}
                        for eid in area_fans
                    ]
                    area_resp = (
                        "Turned on the fan." if len(area_fans) == 1 else "Turned on the fans."
                    )
                return json.dumps({"intent": "command", "response": area_resp, "calls": area_calls})
        if target_eid is None:
            # Exactly one fan in the home: a fan-control turn we couldn't pin to a named fan or to a registry area still has only one possible target, so resolve to it rather than deferring to the LoRA (which clarifies "Which fan?" for a bare "turn on the fan") or fabricating a missing-fan envelope ("I couldn't find that fan" for "living room fans off" when the lone fan's registry area is not linked).
            if (on or off or pct is not None) and len(fans) == 1:
                only = fans[0]
                only_eid = only.entity_id
                only_fname = (
                    str((only.attributes or {}).get("friendly_name") or "").strip() or only_eid
                )
                if pct is not None:
                    solo_call: dict[str, Any] = {
                        "service": "fan.set_percentage",
                        "target": {"entity_id": only_eid},
                        "data": {"percentage": pct},
                    }
                    solo_resp = f"Set {only_fname} speed to {pct}%."
                elif off and not on:
                    solo_call = {
                        "service": "fan.turn_off",
                        "target": {"entity_id": only_eid},
                    }
                    solo_resp = f"Turned off {only_fname}."
                else:
                    solo_call = {
                        "service": "fan.turn_on",
                        "target": {"entity_id": only_eid},
                    }
                    solo_resp = f"Turned on {only_fname}."
                return json.dumps(
                    {"intent": "command", "response": solo_resp, "calls": [solo_call]}
                )
            # The user named a fan the home doesn't have ("the living room fan" / "the office fan" when only the bedroom fan exists).
            if kind != "chat_command":
                return None
            if not (on or off or pct is not None):
                return None
            missing_eid = self._fan_missing_entity_id(msg)
            if missing_eid is None:
                return None
            if pct is not None:
                miss_call: dict[str, Any] = {
                    "service": "fan.set_percentage",
                    "target": {"entity_id": missing_eid},
                    "data": {"percentage": pct},
                }
            elif off and not on:
                miss_call = {
                    "service": "fan.turn_off",
                    "target": {"entity_id": missing_eid},
                }
            else:
                miss_call = {
                    "service": "fan.turn_on",
                    "target": {"entity_id": missing_eid},
                }
            return json.dumps(
                {
                    "intent": "command",
                    "response": "I couldn't find that fan in your home.",
                    "calls": [miss_call],
                }
            )
        # A speed/percentage set takes priority over a plain on/off.
        if pct is not None:
            call: dict[str, Any] = {
                "service": "fan.set_percentage",
                "target": {"entity_id": target_eid},
                "data": {"percentage": pct},
            }
            return json.dumps(
                {
                    "intent": "command",
                    "response": f"Set {target_fname} speed to {pct}%.",
                    "calls": [call],
                }
            )
        if off and not on:
            call = {"service": "fan.turn_off", "target": {"entity_id": target_eid}}
            return json.dumps(
                {"intent": "command", "response": f"Turned off {target_fname}.", "calls": [call]}
            )
        if on:
            call = {"service": "fan.turn_on", "target": {"entity_id": target_eid}}
            return json.dumps(
                {"intent": "command", "response": f"Turned on {target_fname}.", "calls": [call]}
            )
        return None
