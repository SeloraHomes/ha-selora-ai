"""Deterministic ground-truth for "what <category> are <state>?" questions.

On the cloud path the LLM is left to filter the entity list by domain AND
live state when the user asks "which lights are on?". It does this
unreliably — it counts wrong, includes wrong-domain devices (fans in a
"lights off" answer), and selects a different set in French than in
English because the entity names are English.

This module computes the answer set in code instead. It detects the
question multilingually (category word + state word + an interrogative
cue), resolves the matching entity_ids from the snapshot's live state,
and emits a GROUND TRUTH constraint block for the prompt. The model still
phrases the reply in the user's language, but the *set* and the *count*
are fixed by code — identical across locales, correct by domain.

Detection is intentionally conservative: it fires only on interrogative
status questions, never on imperative commands ("turn off the lights"),
so it cannot hijack a command turn.
"""

from __future__ import annotations

from ..lexical import normalize
from ..types import EntitySnapshot
from .intent import _CATEGORY_KEYWORD_TO_DOMAIN

# Interrogative openers across the shipped conversational locales. One of
# these must be present for a message to count as a status *question*
# rather than a command.
_INTERROGATIVES: frozenset[str] = frozenset(
    {
        "what",
        "which",  # en
        "quel",
        "quelle",
        "quels",
        "quelles",  # fr
        "welche",
        "welcher",
        "welches",  # de
        "qué",
        "que",
        "cuál",
        "cuáles",
        "cuales",  # es
        "quale",
        "quali",  # it
    }
)

# Natural-language state word -> canonical target state, across locales.
# Past participles / adjectives ("allumées", "encendidas") — NOT the bare
# imperative verbs ("allume", "enciende"), so a command never matches.
_STATE_WORDS: dict[str, str] = {
    # on
    "on": "on",
    "allumé": "on",
    "allumée": "on",
    "allumés": "on",
    "allumées": "on",
    "encendido": "on",
    "encendida": "on",
    "encendidos": "on",
    "encendidas": "on",
    "acceso": "on",
    "accesa": "on",
    "accesi": "on",
    "accese": "on",
    "eingeschaltet": "on",
    "an": "on",
    # off
    "off": "off",
    "éteint": "off",
    "éteinte": "off",
    "éteints": "off",
    "éteintes": "off",
    "apagado": "off",
    "apagada": "off",
    "apagados": "off",
    "apagadas": "off",
    "spento": "off",
    "spenta": "off",
    "spenti": "off",
    "spente": "off",
    "ausgeschaltet": "off",
    # open / closed (covers)
    "open": "open",
    "ouvert": "open",
    "ouverte": "open",
    "ouverts": "open",
    "ouvertes": "open",
    "abierto": "open",
    "abierta": "open",
    "abiertos": "open",
    "abiertas": "open",
    "aperto": "open",
    "aperta": "open",
    "aperti": "open",
    "aperte": "open",
    "offen": "open",
    "closed": "closed",
    "fermé": "closed",
    "fermée": "closed",
    "fermés": "closed",
    "fermées": "closed",
    "cerrado": "closed",
    "cerrada": "closed",
    "cerrados": "closed",
    "cerradas": "closed",
    "chiuso": "closed",
    "chiusa": "closed",
    "chiusi": "closed",
    "chiuse": "closed",
    "geschlossen": "closed",
    # locked / unlocked (locks)
    "locked": "locked",
    "verrouillé": "locked",
    "verrouillée": "locked",
    "verrouillées": "locked",
    "verschlossen": "locked",
    "abgeschlossen": "locked",
    "unlocked": "unlocked",
    "déverrouillé": "unlocked",
    "déverrouillée": "unlocked",
}

# Per-domain mapping from a canonical target state to the set of HA state
# strings that count as a match. A (domain, state) pair absent here means
# the question is not answerable deterministically for that domain — the
# detector bails so we never report e.g. a "running" light.
_DOMAIN_STATES: dict[str, dict[str, set[str]]] = {
    "light": {"on": {"on"}, "off": {"off"}},
    "switch": {"on": {"on"}, "off": {"off"}},
    "fan": {"on": {"on"}, "off": {"off"}},
    "cover": {"open": {"open"}, "closed": {"closed"}},
    "lock": {"locked": {"locked"}, "unlocked": {"unlocked"}},
}

# States that never count (would always over-count if included).
_DEAD_STATES: frozenset[str] = frozenset({"", "unknown", "unavailable"})


def detect_state_filter(message: str) -> tuple[str, str] | None:
    """Return ``(domain, target_state)`` when *message* is an interrogative
    status question with a recognised category and a domain-compatible
    state word; ``None`` otherwise.

    Requires an interrogative cue so imperative commands ("éteins les
    lumières") never match.
    """
    if not message:
        return None
    # Iterate the ordered token *list*, not a set: when a question names
    # two categories ("which lights and covers are open?") we must pick the
    # first one the user mentioned, deterministically — set iteration order
    # would pin an arbitrary domain.
    tokens = normalize(message).split()
    if not tokens or set(tokens).isdisjoint(_INTERROGATIVES):
        return None
    domain: str | None = None
    for tok in tokens:
        d = _CATEGORY_KEYWORD_TO_DOMAIN.get(tok)
        if d in _DOMAIN_STATES:
            domain = d
            break
    if domain is None:
        return None
    states_for_domain = _DOMAIN_STATES[domain]
    for tok in tokens:
        target = _STATE_WORDS.get(tok)
        if target and target in states_for_domain:
            return (domain, target)
    return None


def matching_entity_ids(
    entities: list[EntitySnapshot], domain: str, target_state: str
) -> list[str]:
    """Entity_ids in *entities* of *domain* whose live state matches
    *target_state*, sorted for determinism."""
    accepted = _DOMAIN_STATES.get(domain, {}).get(target_state, set())
    out: list[str] = []
    for e in entities:
        eid = e.get("entity_id", "")
        if "." not in eid or eid.split(".", 1)[0] != domain:
            continue
        live = str(e.get("state", "")).lower()
        if live in _DEAD_STATES or live not in accepted:
            continue
        out.append(eid)
    out.sort()
    return out


def ground_truth_block(entities: list[EntitySnapshot], message: str) -> str | None:
    """Build a GROUND TRUTH constraint block for a status question, or
    ``None`` when *message* is not a (deterministically answerable) status
    question.

    The block pins the exact entity_ids and count so the model cannot
    miscount, include wrong-domain devices, or diverge between locales.
    """
    detected = detect_state_filter(message)
    if detected is None:
        return None
    domain, target_state = detected
    ids = matching_entity_ids(entities, domain, target_state)
    count = len(ids)
    id_list = ", ".join(ids) if ids else "(none)"
    singular = count == 1
    noun = f"{domain} entity" if singular else f"{domain} entities"
    verb = "is" if singular else "are"
    these = "this 1 entity_id" if singular else f"these {count} entity_ids"
    return (
        "\n\nGROUND TRUTH (authoritative, computed from live state — trust this "
        "over your own filtering):\n"
        f"Exactly {count} {noun} {verb} '{target_state}': {id_list}.\n"
        f"Your answer MUST reference EXACTLY {these} in `[[entities:…]]` "
        "marker(s) and nothing else — do NOT include devices of any other "
        "domain (no fans/switches in a lights answer) and do NOT add or drop "
        f"any. The opening count word MUST be {count}. If the count is 0, say "
        "so plainly and emit no marker.\n"
    )
