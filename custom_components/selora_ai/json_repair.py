"""Structural JSON salvage shared across the integration.

Weak or cheap LLMs (Qwen 1.5B locally, gateway-routed models on the
cloud path) drift from strict JSON in a small, well-known set of ways:
single-quoted strings, unquoted keys, trailing
commas, literal control chars inside string values, prose/extra braces
around the payload. A single ``json.loads`` dies on any of these and the
caller loses the whole response.

These helpers are pure string transforms with no provider/LLM-client
dependency, so both the providers layer (``providers/_qwen_repair``) and
the parsing layer (``llm_client/parsers``) can salvage output without
importing each other. Clean JSON passes through untouched.
"""

from __future__ import annotations

# JSON literals that must NOT be quoted as keys when the unquoted-key
# repair pass runs.
_RESERVED_LITERALS: frozenset[str] = frozenset({"true", "false", "null"})


def extract_first_balanced_json_object(text: str) -> str | None:
    """Return the substring containing the first balanced ``{...}`` object,
    string-aware so braces inside JSON strings don't count.

    Defends against models emitting trailing junk (extra ``}``s, prose
    after the JSON, a second JSON object). A naive ``find('{')`` to
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
