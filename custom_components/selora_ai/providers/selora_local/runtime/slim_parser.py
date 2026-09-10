"""Selora AI Local — slim-shape parser: converts LoRA slim output into the command/automation/answer envelope."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ....json_repair import loads_first_json_object
from ..commands.vacuum_fan import _canonicalize_vacuum_service
from ..utilities.rag import _selora_local_ground_citations

_LOGGER = logging.getLogger(__name__)


# Selora AI Local — substitution pattern for {entity_id} placeholders in the answer specialist's slim ``r`` field.
_SELORA_LOCAL_PLACEHOLDER_RE = re.compile(r"\{([a-z_][a-z0-9_]*\.[a-z0-9_]+)\}")


def _selora_local_extract_visible(raw: str) -> str:
    """From a (possibly partial) slim JSON response, return the decoded user-facing text seen so far."""
    earliest = -1
    marker_used = ""
    for marker in _SELORA_LOCAL_VISIBLE_VALUE_KEYS:
        idx = raw.find(marker)
        if idx >= 0 and (earliest < 0 or idx < earliest):
            earliest = idx
            marker_used = marker
    if earliest < 0:
        return ""
    return _selora_local_decode_json_partial(raw[earliest + len(marker_used) :])


# Selora AI Local — keys whose string value is the user-facing text we want to stream visibly (everything else in the slim JSON is metadata the panel doesn't render).
_SELORA_LOCAL_VISIBLE_VALUE_KEYS: tuple[str, ...] = (
    '"response":"',
    '"r":"',
    '"q":"',
)


def _selora_local_decode_json_partial(raw: str) -> str:
    """Decode a JSON string content (chars *after* the opening ``"`` and *before* the closing unescaped ``"``) tolerating partial input."""
    out: list[str] = []
    i = 0
    n = len(raw)
    escape_map = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}
    while i < n:
        c = raw[i]
        if c == "\\":
            if i + 1 >= n:
                break
            esc = raw[i + 1]
            if esc in escape_map:
                out.append(escape_map[esc])
                i += 2
                continue
            if esc == "u" and i + 6 <= n:
                try:
                    out.append(chr(int(raw[i + 2 : i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            out.append(esc)
            i += 2
            continue
        if c == '"':
            break
        out.append(c)
        i += 1
    return "".join(out)


# Sentinel for "candidates existed but none parsed", kept distinct from the ``None``
# ``loads_first_json_object`` returns for "the model wrote no object at all". The two
# route to different salvage and collapsing them loses that split.
_PARSE_FAILED = object()


class _SlimParserMixin:
    """Selora AI Local — slim-shape parser: converts LoRA slim output into the command/automation/answer envelope."""

    def _convert_slim_shape(self, text: str) -> str:
        """Convert a slim v0.4.2 LoRA output to the {intent, response, calls/automation/scene} envelope LLMClient._parse_architect_response expects."""
        # Deterministic overrides. Each ``_maybe_*`` returns a ready-made envelope
        # for a recognised question / command / automation class, or ``None`` to
        # defer to the JSON-parse branches below. The tuple order is the dispatch
        # priority and MUST be preserved (inventory/state questions before command
        # overrides before automation overrides). On the first hit, clear the raw
        # user message — a follow-up turn that skips ``set_chat_context`` would
        # otherwise re-trigger the same deterministic override — and return it.
        for _override_fn in (
            self._maybe_calendar_question_envelope,  # inventory / count question
            self._maybe_state_filter_envelope,
            self._maybe_category_inventory_envelope,
            self._maybe_todo_question_envelope,  # to-do / task-list question
            self._maybe_single_state_envelope,  # single-device state ("is the kitchen plug on?")
            self._maybe_media_player_state_envelope,  # playback state (playing/paused/stopped)
            self._maybe_polar_valve_state_envelope,  # valve/sprinkler state ("are the sprinklers on?")
            self._maybe_weather_question_envelope,  # weather/forecast ("is today sunny?")
            self._maybe_measurement_value_envelope,  # numeric sensor value ("battery level?")
            self._maybe_missing_domain_clarification,  # "missing domain" safety override
            self._maybe_todo_command_envelope,  # to-do / shopping-list command
            self._maybe_light_envelope,  # light command
            self._maybe_command_envelope,  # media_player command
            self._maybe_fan_envelope,  # fan command
            self._maybe_vacuum_envelope,  # vacuum command
            self._maybe_scene_envelope,  # scene activation
            self._maybe_input_boolean_envelope,  # input_boolean command
            self._maybe_cover_envelope,  # cover command
            self._maybe_climate_envelope,  # climate (thermostat) command
            self._maybe_presence_automation_envelope,  # presence-duration automation
            self._maybe_duration_automation_envelope,  # sustained-state ("for N minutes") automation
            self._maybe_multi_condition_automation_envelope,  # multi-condition automation (A5 class)
            self._maybe_numeric_state_automation_envelope,  # single-threshold numeric_state automation
            self._maybe_sun_automation_envelope,  # sun-event automation
            self._maybe_plain_presence_automation_envelope,  # plain-presence automation
        ):
            _override = _override_fn()
            if _override is not None:
                self._user_message_raw.set("")
                return _override
        stripped = text.strip()
        if not stripped:
            return text
        # Find the JSON envelope. Tolerate leading prose, and crop on BALANCED braces: a
        # stray ``}`` after the envelope (or a second object, or prose containing one)
        # makes a first-brace-to-last-brace slice unbalanced, and the whole command is
        # then thrown away in favour of visible-text salvage. Shared with the two
        # llm_client parsers so all three agree on which object is the model's answer.
        try:
            data = loads_first_json_object(stripped)
        except json.JSONDecodeError:
            data = _PARSE_FAILED
        if data is None:
            # No usable JSON envelope at all.
            vacuum_recovered = self._recover_vacuum_command_from_raw(stripped)
            if vacuum_recovered is not None:
                return vacuum_recovered
            visible = _selora_local_extract_visible(stripped)
            if visible:
                util_fallback = self._utilities_fallback_envelope(visible)
                if util_fallback is not None:
                    return util_fallback
                # A truncated single-state answer ("The kitchen plug is {switch.kitchen_appliance_pl…") loses its closing brace, so the {entity_id} placeholder never resolves and the live state word never reaches the reply.
                single_state = self._single_state_answer_envelope()
                if single_state is not None:
                    return single_state
                # The single-state override above needs ``_user_message_raw``, which is frequently empty here (ContextVar propagation race).
                salvaged = self._resolve_truncated_placeholder(visible)
                if salvaged is not None:
                    return json.dumps({"intent": "answer", "response": salvaged, "r": salvaged})
                return json.dumps({"intent": "answer", "response": visible})
            return text
        if data is _PARSE_FAILED:
            # Candidates existed but none of them parsed.
            from ..._qwen_repair import (
                normalize_response_content,
                repair_json_string_controls,
            )

            data = None
            # The drift repair works on a raw slice, so it still gets the naive crop --
            # anything balanced would already have parsed above.
            _start = stripped.find("{")
            _end = stripped.rfind("}")
            _slice = stripped[_start : _end + 1] if _start >= 0 and _end > _start else stripped
            try:
                repaired = json.loads(repair_json_string_controls(_slice))
            except (json.JSONDecodeError, ValueError):
                repaired = None
            if isinstance(repaired, dict):
                # Recovered a complete envelope from drift — fall through to the slim shape branches below with the repaired data.
                data = repaired
            else:
                # Genuinely truncated / unrecoverable.
                vacuum_recovered = self._recover_vacuum_command_from_raw(stripped)
                if vacuum_recovered is not None:
                    return vacuum_recovered
                visible = _selora_local_extract_visible(stripped)
                if visible:
                    util_fallback = self._utilities_fallback_envelope(visible)
                    if util_fallback is not None:
                        return util_fallback
                    # As above: a single-state answer clipped mid-placeholder is answerable deterministically from hass.states.
                    single_state = self._single_state_answer_envelope()
                    if single_state is not None:
                        return single_state
                    # As above: ``_user_message_raw`` may be empty here, so salvage the clipped placeholder from the prose itself.
                    salvaged = self._resolve_truncated_placeholder(visible)
                    if salvaged is not None:
                        return json.dumps({"intent": "answer", "response": salvaged, "r": salvaged})
                    return json.dumps({"intent": "answer", "response": visible})
                return normalize_response_content(text)
        if not isinstance(data, dict):
            return text
        # Slim automation shape: {"r": "<sentence>", "a": {...}}.
        #
        # Checked before every branch below because the slim automation envelope carries
        # no ``intent``, no ``automation`` and no top-level ``c``, so the others read it
        # as a plain answer and the whole automation -- trigger, conditions, actions --
        # is dropped on the floor without an error. An envelope that already names its
        # own intent, or carries a full-word ``automation``/``scene``/``calls`` key, is
        # left to the enveloped path: reading an ``a`` key off an answer envelope would
        # turn an answer into an automation.
        slim_automation = data.get("a")
        if (
            isinstance(slim_automation, dict)
            and slim_automation
            and not data.keys() & {"intent", "automation", "scene", "calls"}
        ):
            from ...slim_automation import SlimAutomationError, expand_slim_automation

            response_text = data.get("r", "") or ""
            try:
                automation = expand_slim_automation(slim_automation, response_text)
            except SlimAutomationError as exc:
                # A block that did not fully expand hides its steps from every automation
                # gate. Refuse it and fall through to the answer branch rather than write
                # an automation nobody checked.
                _LOGGER.warning("Slim automation block refused: %s", exc)
            else:
                return json.dumps(
                    {
                        "intent": "automation",
                        "response": response_text,
                        "automation": automation,
                    }
                )
        # v0.4.8 utilities/RAG turn: coerce ANY parsed shape into a grounded {r, src} utilities envelope BEFORE the enveloped-passthrough and the command/clarification/answer branches below.
        if (self._chat_kind.get() == "chat_utilities" or "src" in data) and "c" not in data:
            advice_raw: str | None = None
            for _util_key in ("r", "response", "q"):
                _util_val = data.get(_util_key)
                if isinstance(_util_val, str) and _util_val.strip():
                    advice_raw = _util_val
                    break
            if advice_raw is not None:
                resolved_advice = _SELORA_LOCAL_PLACEHOLDER_RE.sub(
                    lambda m: self._resolve_state_placeholder(m.group(1)),
                    advice_raw,
                )
                coerced_env: dict[str, Any] = {
                    "intent": "answer",
                    "response": resolved_advice,
                    "r": resolved_advice,
                }
                q_field = data.get("q")
                if isinstance(q_field, list):
                    coerced_env["q"] = self._filter_known_entities(
                        [str(x) for x in q_field if isinstance(x, str)]
                    )
                src_field = data.get("src")
                if isinstance(src_field, str):
                    coerced_src = [src_field] if src_field.strip() else []
                elif isinstance(src_field, list):
                    coerced_src = [str(s) for s in src_field if isinstance(s, str) and s.strip()]
                else:
                    coerced_src = []
                # Keep only model-cited ids that exist in the corpus; when none are valid, backfill grounded citations.
                coerced_env["src"] = _selora_local_ground_citations(coerced_src, resolved_advice)
                return json.dumps(coerced_env)
        # Deterministic to-do correction for the FULLY ENVELOPED command shape.
        if data.get("intent") == "command" or (
            "calls" in data and "automation" not in data and "scene" not in data
        ):
            todo_fix = self._resolve_todo_add_envelope()
            if todo_fix is not None:
                return todo_fix
        # Already enveloped (automation specialist or older verbose output).
        if "intent" in data or "automation" in data or "scene" in data or "calls" in data:
            # An already-enveloped COMMAND can carry the same mis-targeted brightness the slim ``c``-branch repairs below: the LoRA routes "set bedroom to 50%" onto ``cover.bedroom`` (or an invalid ``switch.set_brightness``).
            calls_field = data.get("calls")
            if isinstance(calls_field, list) and calls_field:
                # An already-enveloped vacuum command carries the same unsupported verbs the slim ``c``-branch canonicalises below (``vacuum.stop`` -> idle-in-place leaving ``cleaning``; ``vacuum.turn_on`` -> not a StateVacuum service, no-op leaving ``off``).
                for _call in calls_field:
                    if isinstance(_call, dict) and isinstance(_call.get("service"), str):
                        _call["service"] = _canonicalize_vacuum_service(_call["service"])
                # Same ContextVar-race-proof message read as the slim branch.
                env_msg = self._current_user_message().lower().strip()
                if env_msg and (
                    self._light_brightness_pct(env_msg) is not None
                    or self._detect_light_rgb(env_msg) is not None
                ):
                    slim_like = [
                        {"e": c.get("target", {}).get("entity_id")}
                        for c in calls_field
                        if isinstance(c, dict)
                        and isinstance(c.get("target"), dict)
                        and isinstance(c["target"].get("entity_id"), str)
                    ]
                    repoint_fix = self._repoint_colocated_light_brightness(slim_like, env_msg)
                    if repoint_fix is not None:
                        return repoint_fix
            from ..._qwen_repair import normalize_response_content

            return normalize_response_content(json.dumps(data))
        # Slim command shape: {"c": [...], "r": "..."}
        if isinstance(data.get("c"), list):
            # Deterministic light correction (brightness / colour / compound only).
            light_msg = self._current_user_message().lower().strip()
            # A plain on/off light command that names a real HA area/floor scope ("activate all first floor lights", "first floor lights on", "shut off the upstairs lights") must ALSO go through the deterministic resolver: the LoRA fans a floor request out to one call per light and trips the per-turn max-calls safety cap (or mis-targets a single light), whereas ``_resolve_light_command`` resolves the scope by registry membership and chunks the targets within the per-call / max-calls caps.
            names_light_scope = bool(re.search(r"\b(lights?|lamps?)\b", light_msg)) and bool(
                self._area_ids_for_named_scope(light_msg)
            )
            if light_msg and (
                len(re.split(r"\bthen\b", light_msg)) > 1
                or self._light_brightness_pct(light_msg) is not None
                or self._detect_light_rgb(light_msg) is not None
                or names_light_scope
            ):
                light_fix = self._resolve_light_command(light_msg, require_light_word=False)
                if light_fix is not None:
                    return light_fix
                # Fallback: the area/name resolution above came up empty (registry area name differs from the spoken scope, or entities carry no area), but the LoRA's own mis-targeted entity — a co-located ``cover.bedroom`` curtain or an invalid ``switch.set_brightness`` — still reveals the device cluster the user meant.
                repoint_fix = self._repoint_colocated_light_brightness(data["c"], light_msg)
                if repoint_fix is not None:
                    return repoint_fix
            # Deterministic to-do correction.
            todo_fix = self._resolve_todo_add_envelope()
            if todo_fix is not None:
                return todo_fix
            # Deterministic media_player correction.
            if any(
                isinstance(c, dict)
                and isinstance(c.get("e"), str)
                and c["e"].startswith("media_player.")
                for c in data["c"]
            ):
                media_fix = self._resolve_media_command(
                    self._current_user_message().lower().strip()
                )
                if media_fix is not None:
                    return media_fix
            calls: list[dict[str, Any]] = []
            for c in data["c"]:
                if not isinstance(c, dict):
                    continue
                svc = c.get("s") or ""
                eid = c.get("e") or ""
                if not svc or not eid:
                    continue
                # Remap unsupported vacuum service slugs (turn_on / clean_spot / stop) to the StateVacuum service set that actually drives the device-state the request intends.
                svc = _canonicalize_vacuum_service(svc)
                call: dict[str, Any] = {
                    "service": svc,
                    "target": {"entity_id": eid},
                }
                if isinstance(c.get("d"), dict):
                    call["data"] = c["d"]
                calls.append(call)
            return json.dumps(
                {
                    "intent": "command",
                    "response": data.get("r", "") or "",
                    "calls": calls,
                }
            )
        # Slim clarification shape: {"q": "<question>", "o": [...]}
        if isinstance(data.get("q"), str):
            question = data["q"]
            options = data.get("o") or data.get("options")
            response_text = question
            if isinstance(options, list) and options:
                rendered = ", ".join(str(o) for o in options)
                response_text = f"{question}\n[options: {rendered}]"
            return json.dumps({"intent": "answer", "response": response_text})
        # Slim utilities/RAG shape: {"r": "<advice>", "q?": [...], "src": [ids]}.
        if isinstance(data.get("r"), str) and (
            "src" in data or self._chat_kind.get() == "chat_utilities"
        ):
            template = data["r"]

            def _sub_util(match: re.Match[str]) -> str:
                return self._resolve_state_placeholder(match.group(1))

            resolved = _SELORA_LOCAL_PLACEHOLDER_RE.sub(_sub_util, template)
            util_env: dict[str, Any] = {
                "intent": "answer",
                "response": resolved,
                "r": resolved,
            }
            q_field = data.get("q")
            if isinstance(q_field, list):
                util_env["q"] = self._filter_known_entities(
                    [str(x) for x in q_field if isinstance(x, str)]
                )
            src_field = data.get("src")
            if isinstance(src_field, str):
                src_list = [src_field] if src_field.strip() else []
            elif isinstance(src_field, list):
                src_list = [str(s) for s in src_field if isinstance(s, str) and s.strip()]
            else:
                src_list = []
            # Citation grounding.
            util_env["src"] = _selora_local_ground_citations(src_list, resolved)
            return json.dumps(util_env)
        # Slim answer shape: {"r": "...", "q": [<entity_ids>]}
        if isinstance(data.get("r"), str):
            # Deterministic media_player command recovery, ContextVar-independent.
            media_recovery = self._resolve_media_command(
                self._current_user_message().lower().strip()
            )
            if media_recovery is not None:
                return media_recovery
            # Deterministic vacuum command recovery, ContextVar-independent.
            vacuum_recovery = self._resolve_vacuum_command(
                self._current_user_message().lower().strip()
            )
            if vacuum_recovery is not None:
                return vacuum_recovery
            # Deterministic single-state answer first.
            single_state = self._single_state_answer_envelope()
            if single_state is not None:
                return single_state
            template = data["r"]

            # Resolve {entity_id} placeholders against live state.
            def _sub(match: re.Match[str]) -> str:
                return self._resolve_state_placeholder(match.group(1))

            resolved = _SELORA_LOCAL_PLACEHOLDER_RE.sub(_sub, template)

            # Generic slim answer: keep ``r`` (response text) and ``q`` (entity list) on the envelope so downstream behavioural checks that inspect those fields (response_uses_placeholder, category enumeration, state-filter) still see the model's original slim output.
            envelope: dict[str, Any] = {
                "intent": "answer",
                "response": resolved,
                "r": resolved,
            }
            # Drop hallucinated entity_ids from ``q``.
            real_q: list[str] = []
            q_field = data.get("q")
            if isinstance(q_field, list):
                for x in q_field:
                    if not isinstance(x, str):
                        continue
                    if self._hass is not None and self._hass.states.get(x) is not None:
                        real_q.append(x)
            if real_q:
                envelope["q"] = real_q
            else:
                # No real entity survived ``q``.
                backfill = self._backfill_answer_marker(resolved)
                if backfill:
                    resolved = f"{resolved}{backfill}"
                    envelope["response"] = resolved
                    envelope["r"] = resolved
            return json.dumps(envelope)
        return text

    def _backfill_answer_marker(self, text: str) -> str:
        """Return a trailing ``[[entities:…]]`` tile marker (or ``""``) for an answer that surfaced no real entity."""
        if self._hass is None or "[[entit" in text:
            return ""
        prompt = (self._user_message_raw.get() or "").lower()
        if not prompt:
            return ""
        ids: list[str] = []
        if re.search(
            r"\b(?:temperature|humidity|humid|warm(?:er)?|cold(?:er)?|hot|cool|degrees?)\b",
            prompt,
        ):
            for state in self._filtered_domain_states("sensor"):
                eid = state.entity_id
                fname = str((state.attributes or {}).get("friendly_name") or "")
                haystack = f"{eid} {fname}".lower()
                if any(k in haystack for k in ("temp", "humid", "climate", "thermo")):
                    ids.append(eid)
        elif re.search(
            r"\b(?:door|doors|lock|locks|garage|gate|window|windows|blind|blinds|shade|shades|curtain|curtains)\b",
            prompt,
        ):
            for dom in ("cover", "lock"):
                ids.extend(s.entity_id for s in self._filtered_domain_states(dom))
        ids = sorted(set(ids))
        if not ids:
            return ""
        return f"\n[[entities:{','.join(ids)}]]"
