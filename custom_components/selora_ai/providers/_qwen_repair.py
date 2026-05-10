"""Qwen 2.5 1.5B output repair for Selora AI Local.

The Selora-trained Qwen 2.5 1.5B specialists drift in well-known ways at
``temperature=0`` — single-quoted strings, unquoted keys, trailing
commas, control chars in string values, markdown fences, unknown
``intent`` values, missing ``alias`` on automations, singular HA keys
(``trigger`` instead of ``triggers``), legacy HA shapes (``platform``
instead of ``trigger``). The integration is the only consumer of this
output, so the repair runs here before HA's automation validator sees
anything.

Originally lived in the management-host LoRA router; folded into the
provider after that process layer was retired.
"""

from __future__ import annotations

import json
import re
from typing import Any

ALLOWED_INTENTS: frozenset[str] = frozenset({"command", "automation", "answer", "clarification"})

# JSON literals that must NOT be quoted as keys when the unquoted-key
# repair pass runs.
_RESERVED_LITERALS: frozenset[str] = frozenset({"true", "false", "null"})

# Pull a `"response":"..."` substring out of malformed model output as a
# fallback. Handles backslash-escaped quotes inside the value.
_RESPONSE_FIELD_RE = re.compile(
    r'"response"\s*:\s*"((?:[^"\\]|\\.)*)"',
    re.DOTALL,
)


def coerce_to_answer(text: str) -> dict[str, Any]:
    """Wrap arbitrary model output as a valid `intent: answer` envelope.

    Tries to extract a `"response": "..."` substring; otherwise uses the
    bare text minus JSON debris. Keeps the chat bubble readable instead
    of showing raw `{"intent":"suggestion",...` to the user.
    """
    match = _RESPONSE_FIELD_RE.search(text)
    if match:
        try:
            response = json.loads(f'"{match.group(1)}"')
        except json.JSONDecodeError:
            response = match.group(1)
    else:
        cleaned = re.sub(r"^[\s{`]*", "", text)
        cleaned = re.sub(r'^"intent"\s*:\s*"[^"]*"\s*,?\s*', "", cleaned)
        cleaned = re.sub(r"[}`]*\s*$", "", cleaned).strip()
        response = (
            cleaned[:500] if cleaned else "I'm not sure how to help with that — could you rephrase?"
        )
    return {"intent": "answer", "response": str(response).strip()}


def extract_first_balanced_json_object(text: str) -> str | None:
    """Return the substring containing the first balanced ``{...}`` object,
    string-aware so braces inside JSON strings don't count.

    Defends against Qwen 1.5B emitting trailing junk (extra ``}``s, prose
    after the JSON, second JSON object). A naive ``find('{')`` to
    ``rfind('}')`` includes any trailing extras and breaks ``json.loads``.
    """
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape_next:
            escape_next = False
            continue
        if in_string:
            if ch == "\\":
                escape_next = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def repair_json_string_controls(text: str) -> str:
    """Single-pass repair of common Qwen 1.5B JSON drift modes:

    1. Escape literal newline/CR/tab chars inside string values
       (multi-line ``target`` strings on vague requests).
    2. Strip trailing commas before ``}`` or ``]``
       (Python/JS-style emission).
    3. Quote unquoted object keys
       (``alias:`` → ``"alias":``).
    4. Convert single-quoted string values to double-quoted
       (``'time'`` → ``"time"``).

    Single state-machine pass — tracks whether we're inside a ``"..."``
    or ``'...'`` string so structure is never confused for content.
    Cloud providers emit clean JSON and pass through untouched.
    """
    out: list[str] = []
    in_double = False
    in_single = False
    escape_next = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if escape_next:
            out.append(ch)
            escape_next = False
            i += 1
            continue
        if in_double:
            if ch == "\\":
                out.append(ch)
                escape_next = True
            elif ch == '"':
                out.append(ch)
                in_double = False
            elif ch in "\n\r\t":
                out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}[ch])
            else:
                out.append(ch)
            i += 1
            continue
        if in_single:
            if ch == "\\":
                out.append(ch)
                escape_next = True
            elif ch == "'":
                out.append('"')
                in_single = False
            elif ch == '"':
                out.append('\\"')
            elif ch in "\n\r\t":
                out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}[ch])
            else:
                out.append(ch)
            i += 1
            continue

        # Outside any string.
        if ch == '"':
            in_double = True
            out.append(ch)
            i += 1
            continue
        if ch == "'":
            in_single = True
            out.append('"')
            i += 1
            continue

        # Trailing comma elision: drop `,` if next non-whitespace is } or ].
        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1
                continue

        # Unquoted-key quoting: identifier followed by optional whitespace
        # and ':' is treated as an object key.
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            k = j
            while k < n and text[k] in " \t":
                k += 1
            if k < n and text[k] == ":":
                identifier = text[i:j]
                if identifier not in _RESERVED_LITERALS:
                    out.append('"')
                    out.append(identifier)
                    out.append('"')
                    i = j
                    continue
        out.append(ch)
        i += 1
    return "".join(out)


_TIME_FROM_PROSE_RE = re.compile(r"\b(\d{1,2}):(\d{2})\s*([AaPp][Mm])?\b")


def extract_time_from_prose(prose: str) -> str | None:
    """Extract HH:MM:SS (24h) from prose like '6:30 AM' or '14:00'.

    Used to recover the `at` field of a time trigger when the model emits
    `{"trigger": "time"}` but mentions the time in the surrounding
    description prose ("send a notification at 6:30 AM").
    """
    if not prose:
        return None
    m = _TIME_FROM_PROSE_RE.search(prose)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2))
    ampm = m.group(3)
    if ampm:
        ampm_l = ampm.lower()
        if ampm_l == "pm" and hour < 12:
            hour += 12
        elif ampm_l == "am" and hour == 12:
            hour = 0
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return f"{hour:02d}:{minute:02d}:00"


def normalize_automation_block(body: dict[str, Any]) -> None:
    """In-place coercion of an `intent: automation` envelope into the
    shape the integration's flowchart renderer + validator expect."""
    auto = body.get("automation")
    if not isinstance(auto, dict):
        auto = {}
        body["automation"] = auto

    # Migrate top-level keys (singular AND plural) into the automation block.
    for k in ("trigger", "triggers", "condition", "conditions", "action", "actions"):
        if k in body and k not in auto:
            auto[k] = body.pop(k)

    # Coerce singular → plural inside the automation block.
    for sing, plur in [
        ("trigger", "triggers"),
        ("condition", "conditions"),
        ("action", "actions"),
    ]:
        if sing in auto and plur not in auto:
            v = auto.pop(sing)
            auto[plur] = v if isinstance(v, list) else [v]

    # triggers/conditions/actions must be lists.
    for k in ("triggers", "conditions", "actions"):
        v = auto.get(k)
        if v is None:
            auto[k] = []
        elif isinstance(v, dict):
            auto[k] = [v]
        elif not isinstance(v, list):
            auto[k] = []

    # Synthesize alias if missing — the flowchart card title needs it.
    if not auto.get("alias"):
        desc = body.get("description") or auto.get("description") or "Automation"
        words = str(desc).split()[:4]
        auto["alias"] = " ".join(w.strip(".,!?;:'\"") for w in words) or "Automation"

    # Migrate `platform` → `trigger` on each trigger entry (HA 2024+).
    # Also coerce `to_state`/`from_state` → `to`/`from` (HA-canonical state-trigger keys).
    # Also unwrap single-element list trigger values (`{"trigger": ["time"]}`
    # → `{"trigger": "time"}`) and recover missing `at` for time triggers from
    # the surrounding description prose.
    prose_for_time = " ".join(
        s for s in (body.get("description"), auto.get("description")) if isinstance(s, str)
    )
    fixed_triggers: list[dict[str, Any]] = []
    for t in auto.get("triggers", []):
        if isinstance(t, dict):
            if "trigger_type" in t and "trigger" not in t:
                t["trigger"] = t.pop("trigger_type")
            if "platform" in t and "trigger" not in t:
                t = {"trigger": t.pop("platform"), **t}
            if "to_state" in t and "to" not in t:
                t["to"] = t.pop("to_state")
            if "from_state" in t and "from" not in t:
                t["from"] = t.pop("from_state")
            for old_key in ("start_time", "at_time", "event_time"):
                if old_key in t and "at" not in t:
                    t["at"] = t.pop(old_key)
            trig_val = t.get("trigger")
            if isinstance(trig_val, list) and len(trig_val) >= 1:
                first = trig_val[0]
                if isinstance(first, str):
                    t["trigger"] = first
            if t.get("trigger") == "time" and "at" not in t:
                extracted = extract_time_from_prose(prose_for_time)
                if extracted:
                    t["at"] = extracted
            # HA validator requires HH:MM:SS for time-trigger `at`. Model
            # often emits HH:MM (no seconds) — pad to seconds to satisfy.
            at_val = t.get("at")
            if isinstance(at_val, str) and len(at_val) == 5 and at_val[2] == ":":
                t["at"] = at_val + ":00"
            fixed_triggers.append(t)

    # The model occasionally jams condition/action items into the triggers
    # array. Sort entries back into their proper arrays.
    sorted_triggers: list[dict[str, Any]] = []
    spillover_conditions: list[dict[str, Any]] = []
    spillover_actions: list[dict[str, Any]] = []
    for t in fixed_triggers:
        if not isinstance(t, dict):
            continue
        if "trigger" in t or "platform" in t:
            sorted_triggers.append(t)
        elif "condition" in t:
            spillover_conditions.append(t)
        elif "action" in t or "service" in t:
            spillover_actions.append(t)
    auto["triggers"] = sorted_triggers
    if spillover_conditions:
        existing = auto.get("conditions")
        auto["conditions"] = (
            list(existing) if isinstance(existing, list) else []
        ) + spillover_conditions
    if spillover_actions:
        existing = auto.get("actions")
        auto["actions"] = (list(existing) if isinstance(existing, list) else []) + spillover_actions

    # Normalize action targets — HA expects `target: {"entity_id": [...]}`.
    entity_id_re = re.compile(r"\b([a-z_]+\.[a-z_0-9]+)\b")
    fixed_actions: list[dict[str, Any]] = []
    for a in auto.get("actions", []):
        if not isinstance(a, dict):
            continue
        if "target_entity_id" in a and "target" not in a:
            tid = a.pop("target_entity_id")
            if isinstance(tid, str):
                a["target"] = {"entity_id": [tid]} if tid else {}
            elif isinstance(tid, list):
                a["target"] = {"entity_id": [s for s in tid if isinstance(s, str)]}
        # Drop placeholder entity_ids the model fabricates with <…> brackets.
        if isinstance(a.get("target"), dict):
            eid = a["target"].get("entity_id")
            if isinstance(eid, str) and eid.startswith("<") and eid.endswith(">"):
                a["target"]["entity_id"] = []
            elif isinstance(eid, list):
                a["target"]["entity_id"] = [
                    e
                    for e in eid
                    if isinstance(e, str) and not (e.startswith("<") and e.endswith(">"))
                ]
        target = a.get("target")
        if isinstance(target, dict):
            eid = target.get("entity_id")
            if isinstance(eid, str):
                target["entity_id"] = [eid]
            elif isinstance(eid, list):
                cleaned = []
                for item in eid:
                    if isinstance(item, str):
                        if entity_id_re.fullmatch(item):
                            cleaned.append(item)
                        else:
                            cleaned.extend(entity_id_re.findall(item))
                target["entity_id"] = cleaned
        elif isinstance(target, list):
            cleaned = []
            for item in target:
                if isinstance(item, str):
                    cleaned.extend(entity_id_re.findall(item))
                elif isinstance(item, dict):
                    sub = item.get("entity_id")
                    if isinstance(sub, str):
                        cleaned.append(sub)
                    elif isinstance(sub, list):
                        cleaned.extend(s for s in sub if isinstance(s, str))
            a["target"] = {"entity_id": cleaned}
        elif isinstance(target, str):
            ids = entity_id_re.findall(target)
            a["target"] = {"entity_id": ids}
        fixed_actions.append(a)
    auto["actions"] = fixed_actions


def normalize_response_content(content: str) -> str:
    """Defend the integration from every drift mode the trained Qwen 1.5B
    model hits at temperature 0:

    - Markdown fences around the JSON
    - Unknown ``intent`` values (model invents `suggestion`, `Automation`)
    - Singular HA keys / missing alias / nested-flat structure
    - Unparseable garbage — wrapped as a clean ``intent: answer`` envelope
      so the chat bubble shows readable text instead of raw JSON.

    Input/output are the raw ``content`` string from
    ``choices[0].message.content``. The repair is idempotent — clean
    JSON-envelope responses pass through with at most a re-serialization.
    """
    raw = content.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    balanced = extract_first_balanced_json_object(raw) or raw

    body: dict[str, Any] | None
    try:
        parsed = json.loads(balanced)
        body = parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        # Qwen 1.5B drift: control chars in string values, trailing
        # commas, unquoted object keys. Repair the balanced extract,
        # then fall back to repairing the original raw.
        try:
            parsed = json.loads(repair_json_string_controls(balanced))
            body = parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            try:
                parsed = json.loads(repair_json_string_controls(raw))
                body = parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                body = None

    if body is None:
        body = coerce_to_answer(raw)
    else:
        raw_intent = body.get("intent")
        if isinstance(raw_intent, str):
            body["intent"] = raw_intent.strip().lower()

        if body.get("intent") not in ALLOWED_INTENTS:
            response = body.get("response")
            if not isinstance(response, str) or not response.strip():
                response = coerce_to_answer(raw)["response"]
            body = {"intent": "answer", "response": response.strip()}

    if body.get("intent") == "automation":
        normalize_automation_block(body)

    return json.dumps(body, separators=(",", ":"))
