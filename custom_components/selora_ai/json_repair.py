"""Structural JSON salvage shared across the integration.

Weak or cheap LLMs (Qwen 1.5B locally, gateway-routed models on the
cloud path) drift from strict JSON in a small, well-known set of ways:
single-quoted strings, unquoted keys, trailing
commas, literal control chars inside string values, prose/extra braces
around the payload. A single ``json.loads`` dies on any of these and the
caller loses the whole response.

These helpers have no provider/LLM-client dependency, so both the
providers layer (``providers/_qwen_repair``, ``providers/selora_local``)
and the parsing layer (``llm_client/parsers``) can salvage output without
importing each other. Clean JSON passes through untouched.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

# JSON literals that must NOT be quoted as keys when the unquoted-key
# repair pass runs.
_RESERVED_LITERALS: frozenset[str] = frozenset({"true", "false", "null"})

# Keys that carry an envelope's CONTENT — something to say
# (``response``, slim ``r``/``q``) or something to do (``calls``,
# ``automation``, ``scene``, slim ``c``). A reply the user could act on
# has at least one.
_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {"calls", "automation", "scene", "response", "c", "r", "q"}
)

# Keys that mark an object as one of OUR envelopes rather than an
# incidental object the model happened to print. ``intent`` names the
# shape without carrying anything, which is why it is ranked below the
# payload keys and not merged with them.
_ENVELOPE_KEYS: frozenset[str] = _PAYLOAD_KEYS | frozenset({"intent"})


# Prose that introduces an object as an ILLUSTRATION rather than as the
# model's answer. A decoy carrying only ``intent`` is already outranked by
# content, but an example that quotes a whole command carries ``calls`` and
# outranks nothing — so ``Example: {"calls": [...]} Result: {...}`` would
# execute the example. The command prompt itself is full of ``c``-array
# examples, and a small model echoing its prompt before answering is the
# ordinary failure here, not a contrived one.
#
# ``[^{}]*$`` keeps the marker in the unbroken run of prose immediately
# before this object, so a marker used earlier in the reply, or one sitting
# inside a previous object, does not demote a later real envelope.
_EXAMPLE_INTRO_RE = re.compile(
    r"(?:for\s+example|for\s+instance|example|e\.g\.|such\s+as|like\s+this|format)"
    r"\b[^{}]*$",
    re.IGNORECASE,
)


def _iter_balanced_json_objects(text: str) -> Iterator[str]:
    """Yield each balanced ``{...}`` object in ``text``, left to right,
    string-aware so braces inside JSON strings don't count.

    Scanning resumes after each object it closes, never inside one it
    could not close. That asymmetry is deliberate: an object whose
    closing brace never arrived is a TRUNCATED reply, and restarting at
    the next ``{`` inside it would hand back an inner fragment — the
    first service call of a command the model never finished emitting —
    as though the model had committed to it. A truncated envelope must
    stay unparseable, so the scan stops at the first unterminated object.
    """
    pos = 0
    while True:
        start = text.find("{", pos)
        if start < 0:
            return
        depth = 0
        in_string = False
        escape_next = False
        closed_at = -1
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
                    closed_at = i
                    break
        if closed_at < 0:
            return
        yield text[start : closed_at + 1]
        pos = closed_at + 1


def extract_first_balanced_json_object(text: str) -> str | None:
    """Return the substring containing the first balanced ``{...}`` object,
    string-aware so braces inside JSON strings don't count.

    Defends against models emitting trailing junk (extra ``}``s, prose
    after the JSON, a second JSON object). A naive ``find('{')`` to
    ``rfind('}')`` includes any trailing extras and breaks ``json.loads``.
    """
    return next(_iter_balanced_json_objects(text), None)


def json_object_candidates(text: str) -> list[str]:
    """The substrings worth trying as the model's JSON object, best first.

    Balanced ``{...}`` objects lead, in the order the model wrote them,
    because the naive first-brace-to-last-brace slice every parser used
    to take is unbalanced the moment the model appends anything
    containing a brace — a stray ``}``, a second envelope, a sentence of
    prose. That slice then fails ``json.loads`` and a perfectly good
    command envelope is thrown away in favour of whatever text salvage
    can scrape out of it.

    EVERY balanced object is offered, not just the first, because the
    first one is often not the model's answer: a reply that opens by
    quoting the format back (``Format: {"intent":"answer"} then ...``),
    or by writing a template expression (``use {{ states('sensor.x') }}``),
    puts a decoy ahead of the real envelope. The caller picks between
    them; this function only refuses to hide the later ones.

    The naive slice follows as a fallback rather than being replaced: an
    envelope the token cap cut off before its closing brace has no
    balanced object at all, and callers have repair passes that still
    recover it from the wider slice.

    Empty when the text holds no ``{...}`` to try — either there is no
    ``{``, or no ``}`` follows it. The caller has nothing to parse.
    """
    candidates: list[str] = list(_iter_balanced_json_objects(text))
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        naive = text[start : end + 1]
        if naive not in candidates:
            candidates.append(naive)
    return candidates


def loads_first_json_object(
    text: str,
    loads: Callable[[str], Any] = json.loads,
) -> dict[str, Any] | None:
    """Parse the model's JSON envelope out of a response, brace-aware.

    The one implementation behind every envelope-parsing call site, so
    "which object did we pick, and what happens when none of them parse"
    has a single answer rather than one per caller.

    Candidates are ranked, then document order breaks ties within a
    rank. An object that carries CONTENT wins outright — a ``response``
    or slim ``r``/``q`` to show, or ``calls``/``automation``/``scene``/
    slim ``c`` to run. Next comes an object that merely names the shape
    (``intent`` alone). Anything else that parsed is a last resort.

    Taking the first object that merely parses is not enough: a reply
    opening with ``{}`` or with the format quoted back at us gives a
    decoy that parses perfectly, and accepting it turns a command turn
    into an empty success — the user is told it worked and nothing ran.
    Nor is "first object holding any envelope key" enough, because the
    commonest decoy IS an envelope key — ``Format: {"intent":"answer"}
    then <the real envelope>`` — and a bare intent tag with nothing in
    it is not a reply the model meant to send. Ranking content above the
    tag lets the real envelope behind the decoy win.

    A decoy that quotes a whole COMMAND carries content, so content alone
    does not separate it either: ``Example: {"calls": [...]} Result: {...}``
    would run the example. An object the model introduced as an
    illustration is ranked below one it did not, while still beating a
    bare tag — quoting the only envelope in the reply is how a small model
    often phrases a real answer, so it must stay usable.

    Returns ``None`` when the text carries no object at all, and raises
    the last ``JSONDecodeError`` when candidates existed but none parsed,
    so a caller can tell "the model wrote no JSON" apart from "the model
    wrote broken JSON" and route each to its own salvage. Only ever
    returns a ``dict``: every candidate starts at a ``{``, so anything
    that parses is an object.
    """
    last_error: json.JSONDecodeError | None = None
    quoted: dict[str, Any] | None = None
    tagged: dict[str, Any] | None = None
    plain: dict[str, Any] | None = None
    for candidate in json_object_candidates(text):
        try:
            parsed = loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if not isinstance(parsed, dict):
            continue
        if _PAYLOAD_KEYS & parsed.keys():
            # An object the model introduced as an example is still a real
            # payload and still beats a bare tag, but it must lose to any
            # payload the model did not frame that way.
            offset = text.find(candidate)
            if offset > 0 and _EXAMPLE_INTRO_RE.search(text[:offset]):
                if quoted is None:
                    quoted = parsed
                continue
            return parsed
        if _ENVELOPE_KEYS & parsed.keys():
            if tagged is None:
                tagged = parsed
            continue
        if plain is None:
            plain = parsed
    for ranked in (quoted, tagged, plain):
        if ranked is not None:
            return ranked
    if last_error is not None:
        raise last_error
    return None


def repair_json_string_controls(text: str) -> str:
    """Single-pass repair of common weak-model JSON drift modes:

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
    Models that already emit clean JSON pass through untouched.
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
