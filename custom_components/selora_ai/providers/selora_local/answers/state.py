"""Selora AI Local — deterministic state / inventory / measurement question handlers."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Inventory / count question detection
_CATEGORY_NOUN_PATTERN = r"lights?|switch(?:es)?|fans?|covers?|locks?|thermostats?"


# Selora AI Local — "missing domain" safety override for command turns.
_SELORA_LOCAL_MISSING_DOMAIN_CATEGORIES: tuple[
    tuple[str, re.Pattern[str], frozenset[str], frozenset[str]], ...
] = (
    (
        "sprinkler",
        re.compile(
            r"\b(?:sprinkler|sprinklers|irrigation|watering\s+system)\b",
            re.IGNORECASE,
        ),
        frozenset({"valve"}),
        frozenset({"sprinkler", "irrigation"}),
    ),
    (
        "security system",
        re.compile(
            r"\b(?:security\s+system|alarm\s+system|burglar\s+alarm|"
            r"(?:re)?arm|disarm)\b",
            re.IGNORECASE,
        ),
        frozenset({"alarm_control_panel"}),
        frozenset({"alarm", "security_system"}),
    ),
    (
        "doorbell",
        re.compile(r"\b(?:doorbell|door\s+bell|chime)\b", re.IGNORECASE),
        frozenset(),
        frozenset({"doorbell", "chime"}),
    ),
)


def _has_scope_qualifier(prompt: str) -> bool:
    """True if ``prompt`` contains a scope qualifier (area / floor / adjective) that the deterministic override can't honour."""
    msg = prompt.lower()
    for match in _SCOPE_QUALIFIER_RE.finditer(msg):
        tokens = match.group(0).split()
        # "in total" / "in all" / "in every <X>" — expand, not narrow.
        if len(tokens) >= 2 and tokens[0] == "in" and tokens[-1] in _WHOLE_HOME_TOKENS_AFTER_IN:
            continue
        # "all my lights", "all of my lights", "all the lights" — benign pronoun/article in the intermediate slot, not an area adjective.
        if (
            len(tokens) >= 3
            and tokens[0] in {"my", "the", "our", "all"}
            and tokens[1] in _BENIGN_INTERMEDIATE_AFTER_QUANTIFIER
        ):
            continue
        return True
    return False


def _detect_state_filter_question(
    prompt: str,
) -> tuple[str, str, str, str] | None:
    """Return ``(domain, target_state, plural_label, singular_label)`` if ``prompt`` is a "what <category> are <state>?" question with a recognised category and state."""
    if not prompt:
        return None
    # Scope-qualified prompts ("what lights are on in the kitchen?") can't be answered from the whole-home state machine without over-counting.
    if _has_scope_qualifier(prompt):
        return None
    m = _STATE_FILTER_QUESTION_RE.search(prompt.lower())
    if not m:
        return None
    raw_noun = m.group(1).lower()
    target_state = m.group(2).lower()
    resolved = _CATEGORY_NOUN_TO_DOMAIN_AND_SINGULAR.get(raw_noun)
    if resolved is None:
        return None
    domain, singular = resolved
    # Reject (domain, target_state) pairs whose natural word does not map to any HA state in this domain — otherwise the live-state comparison silently reports zero (e.g.
    if target_state not in _NATURAL_STATE_BY_DOMAIN.get(domain, {}):
        return None
    plural = _CATEGORY_SINGULAR_TO_PLURAL.get(singular, singular)
    return domain, target_state, plural, singular


def _detect_category_question(prompt: str) -> tuple[str, str, str] | None:
    """Return ``(domain, singular_label, plural_label)`` if ``prompt`` is an inventory / count question about a HA device category."""
    if not prompt:
        return None
    # Scope-qualified inventory prompts ("how many lights in the bedroom?", "list my kitchen lights") would over-answer with the whole home — defer to the LoRA.
    if _has_scope_qualifier(prompt):
        return None
    msg = prompt.lower()
    # Reject compound prompts that mention more than one device category ("Do I have lights and switches?").
    found_domains: set[str] = set()
    for raw in _CATEGORY_NOUN_RE.findall(msg):
        resolved_pair = _CATEGORY_NOUN_TO_DOMAIN_AND_SINGULAR.get(raw.lower())
        if resolved_pair is None:
            continue
        found_domains.add(resolved_pair[0])
        if len(found_domains) > 1:
            return None
    m = _CATEGORY_NOUN_RE.search(msg)
    if not m:
        return None
    raw_noun = m.group(1).lower()
    resolved = _CATEGORY_NOUN_TO_DOMAIN_AND_SINGULAR.get(raw_noun)
    if resolved is None:
        return None
    domain, singular_label = resolved
    # Derive a grammatical plural from the singular rather than echoing ``raw_noun`` — when the user typed a singular form ("list my switch") the echoed plural would be "switch" and the prose "You have 3 switch" reads broken.
    plural_label = _CATEGORY_SINGULAR_TO_PLURAL.get(singular_label, singular_label)
    if not _INVENTORY_SIGNAL_RE.search(msg):
        return None
    # Don't intercept "what lights are on?" — that's the state_filter bucket, handled by a different check that wants a subset of the domain, not the whole set.
    if _STATE_FILTER_SIGNAL_RE.search(msg) and not _HOW_MANY_PREFIX_RE.match(msg):
        return None
    return domain, singular_label, plural_label


def _detect_single_state_question(prompt: str) -> tuple[str, str] | None:
    """Return ``(subject, state_word)`` if ``prompt`` is a single-device state question ("is the front door closed?")."""
    if not prompt:
        return None
    m = _SINGLE_STATE_QUESTION_RE.match(prompt.strip())
    if m is None:
        return None
    subject = m.group(1).strip().lower()
    state_word = m.group(2).strip().lower()
    if not subject:
        return None
    if " and " in subject or " or " in subject or "," in subject:
        return None
    if subject in _BARE_CATEGORY_WORDS:
        return None
    return subject, state_word


def _is_pure_inventory_question(prompt: str) -> bool:
    """True when ``prompt`` is entirely an inventory query — short, no command verbs, no compound conjunctions, and at least one inventory grammar match."""
    if not prompt:
        return False
    msg = prompt.strip()
    # Cap at 12 words — anything longer is almost certainly compound or carries scope qualifiers we can't honour.
    if len(msg.split()) > 12:
        return False
    if _COMMAND_VERB_RE.search(msg):
        return False
    if _CONJUNCTION_RE.search(msg):
        return False
    return _INVENTORY_SIGNAL_RE.search(msg.lower()) is not None


def _safe_fname_for_prose(value: str) -> str:
    """Sanitise a friendly_name for inclusion in rendered chat prose."""
    from ....helpers import sanitize_untrusted_text

    safe = sanitize_untrusted_text(value)
    return safe.replace("[", "(").replace("]", ")").replace("`", "'")


_CATEGORY_NOUN_RE = re.compile(
    rf"\b({_CATEGORY_NOUN_PATTERN})\b",
    re.IGNORECASE,
)

# Plural-first lookup: maps the raw noun (singular OR plural) to (HA domain, prose singular).
_CATEGORY_NOUN_TO_DOMAIN_AND_SINGULAR: dict[str, tuple[str, str]] = {
    "lights": ("light", "light"),
    "light": ("light", "light"),
    "switches": ("switch", "switch"),
    "switch": ("switch", "switch"),
    "fans": ("fan", "fan"),
    "fan": ("fan", "fan"),
    "covers": ("cover", "cover"),
    "cover": ("cover", "cover"),
    "locks": ("lock", "lock"),
    "lock": ("lock", "lock"),
    "thermostats": ("climate", "thermostat"),
    "thermostat": ("climate", "thermostat"),
}

# Singular → grammatical plural for the answer prose.
_CATEGORY_SINGULAR_TO_PLURAL: dict[str, str] = {
    "light": "lights",
    "switch": "switches",
    "fan": "fans",
    "cover": "covers",
    "lock": "locks",
    "thermostat": "thermostats",
}

# Inventory verbs/phrases that mark a question as a category roll-call rather than a state filter ("what lights are on?") or a command ("turn off the lights").
_INVENTORY_SIGNAL_RE = re.compile(
    # "how many lights" followed by end-of-clause, "?", "do I have", or "are there" — rejects "how many lights should I turn on".
    rf"\bhow\s+many\s+(?:{_CATEGORY_NOUN_PATTERN})"
    r"(?:\s*[?.!]|\s*$|\s+(?:do\s+i\s+have|are\s+there)\b"
    r"|\s+are\s+(?:on|off|open|closed|locked|unlocked|running|playing)\b)"
    # "what <category> do I have" / "what <category> are there" — the documented primary phrasing for inventory questions.
    rf"|\bwhat\s+(?:{_CATEGORY_NOUN_PATTERN})\s+(?:do\s+i\s+have|are\s+there)\b"
    # "do I have [any] lights" — rejects "do I have to turn off ...".
    rf"|\bdo\s+i\s+have\s+(?:any\s+)?(?:{_CATEGORY_NOUN_PATTERN})\b"
    # "have I got [any] lights".
    rf"|\bhave\s+i\s+got\s+(?:any\s+)?(?:{_CATEGORY_NOUN_PATTERN})\b"
    # "list/show/tell [me] [<article slot>] <category>" — anchored at the start of the prompt so command-shaped sentences that happen to contain "show" later don't qualify.
    rf"|^\s*(?:list|show|tell)\s+(?:me\s+)?"
    rf"(?:my|the|our|all(?:\s+(?:of\s+)?(?:my|the|our))?)?\s*"
    rf"(?:{_CATEGORY_NOUN_PATTERN})\b",
    re.IGNORECASE,
)

_STATE_FILTER_SIGNAL_RE = re.compile(
    # "are/is on", "are/is locked", etc.
    r"\b(?:are|is)\s+"
    r"(?:on|off|running|playing|locked|unlocked|open|closed|home|away)\b"
    # "turned on" / "turned off" — passive form ("what lights do I have turned on?").
    r"|\bturned\s+(?:on|off)\b"
    # "that are on/off/...", "which are on/off/..." — relative-clause shape ("lights that are on", "doors which are locked").
    r"|\b(?:that|which)\s+are\s+"
    r"(?:on|off|running|playing|locked|unlocked|open|closed)\b"
    # Trailing state word with no verb ("do I have any lights on?", "any covers open?").
    r"|\b(?:on|off|open|closed|locked|unlocked|running)\s*[?.!]*\s*$",
    re.IGNORECASE,
)

# "what lights are on?" / "what switches are off?" style — a domain- specific live-state filter.
_STATE_FILTER_QUESTION_RE = re.compile(
    rf"\b(?:what\s+(?:are\s+)?|how\s+many\s+)({_CATEGORY_NOUN_PATTERN})\s+"
    r"(?:are\s+)?(?:currently\s+|right\s+now\s+|now\s+)?"
    r"(on|off|open|closed|locked|unlocked|running|playing)\b",
    re.IGNORECASE,
)

# Scope qualifier signals (area, floor, group, time-of-day).
_SCOPE_QUALIFIER_RE = re.compile(
    # "in [the/my/our/a] <token>" — covers "in the kitchen", "in my bedroom", "in our living room".
    r"\bin\s+(?:the\s+|my\s+|our\s+|a\s+)?[a-z]+\b"
    # Common stand-alone location qualifiers that don't take "in".
    r"|\b(?:upstairs|downstairs|outside|inside|outdoor|indoor)\b"
    # "kitchen lights" / "<adjective> <category>" — adjective directly in front of a category noun.
    rf"|\b(?:my|the|our|all)\s+[a-z]+\s+(?:{_CATEGORY_NOUN_PATTERN})\b",
    re.IGNORECASE,
)

# Words allowed in the intermediate slot of "<quantifier> X <category>" without counting as a scope adjective.
_BENIGN_INTERMEDIATE_AFTER_QUANTIFIER: frozenset[str] = frozenset({"of", "my", "the", "our"})

# Tokens that follow "in" but expand rather than narrow the scope ("in total", "in all", "in every room").
_WHOLE_HOME_TOKENS_AFTER_IN: frozenset[str] = frozenset(
    {"total", "all", "every", "fact", "general", "particular"}
)

# Per-domain mapping from the natural-language word the user typed to the set of HA state strings that count as a match.
_NATURAL_STATE_BY_DOMAIN: dict[str, dict[str, set[str]]] = {
    "light": {"on": {"on"}, "off": {"off"}},
    "switch": {"on": {"on"}, "off": {"off"}},
    "fan": {"on": {"on"}, "off": {"off"}, "running": {"on"}},
    "lock": {"locked": {"locked"}, "unlocked": {"unlocked"}},
    "cover": {"open": {"open"}, "closed": {"closed"}},
}

# "how many …" prefix — used to keep count questions ("how many lights are on?") on the inventory path even when they carry a state word.
_HOW_MANY_PREFIX_RE = re.compile(r"\s*how\s+many\b", re.IGNORECASE)

# Single-device state query ("is the front door closed?")
_SINGLE_STATE_QUESTION_RE = re.compile(
    r"^\s*is\s+(?:the\s+|a\s+|an\s+|my\s+|our\s+)?(.+?)\s+"
    r"(on|off|open|closed|locked|unlocked|running|playing|active|home|away)"
    r"\s*[?.!]*\s*$",
    re.IGNORECASE,
)

# Bare category words must NOT be treated as a single named device — "is the light on?" / "are my lights on?" are clarification / state- filter shapes, not a single-device lookup.
_BARE_CATEGORY_WORDS: frozenset[str] = frozenset(
    {
        "light",
        "lights",
        "switch",
        "switches",
        "fan",
        "fans",
        "cover",
        "covers",
        "lock",
        "locks",
        "door",
        "doors",
        "window",
        "windows",
        "blind",
        "blinds",
        "device",
        "devices",
        "thermostat",
        "thermostats",
    }
)

# State word → the domains to search when resolving a single-device state question to a REAL entity.
_STATE_WORD_TO_QUERY_DOMAINS: dict[str, tuple[str, ...]] = {
    "on": ("light", "switch", "fan", "media_player", "input_boolean", "climate"),
    "off": ("light", "switch", "fan", "media_player", "input_boolean", "climate"),
    "running": ("switch", "fan", "vacuum"),
    "playing": ("media_player",),
    "open": ("cover", "binary_sensor"),
    "closed": ("cover", "binary_sensor"),
    "locked": ("lock",),
    "unlocked": ("lock",),
    "active": ("binary_sensor", "switch"),
    "home": ("device_tracker", "person"),
    "away": ("device_tracker", "person"),
}

# Articles/pronouns/verbs to drop when deriving the device tokens from a single-state question's subject ("the kitchen plug" → {kitchen, plug}).
_SINGLE_STATE_SUBJECT_STOPWORDS: frozenset[str] = frozenset(
    # "in"/"of" are locative connectors in "the plug IN the kitchen" / "the plug OF the kitchen" — never meaningful device-name tokens — so dropping them lets a relocated subject ("plug in the kitchen" -> {plug, kitchen}) resolve to the same entity as "kitchen plug".
    {"the", "a", "an", "my", "our", "your", "is", "are", "s", "in", "of"}
)

# Verbs that signal a command/action turn rather than a question.
_COMMAND_VERB_RE = re.compile(
    r"\b(?:turn|set|dim|brighten|open|close|lock|unlock|"
    r"start|stop|enable|disable|pause|resume|toggle|run|trigger)\b",
    re.IGNORECASE,
)

# Conjunctions that suggest a compound prompt with multiple intents ("turn off the lights and tell me how many switches I have").
_CONJUNCTION_RE = re.compile(
    r"\b(?:and|but|then|also|plus)\b",
    re.IGNORECASE,
)


def _answer(r_text: str, ids: list[str]) -> str:
    """Emit the deterministic answer envelope shared by the state-filter, single-state, measurement-value, and category-inventory handlers."""
    return json.dumps(
        {
            "intent": "answer",
            "response": r_text,
            "r": r_text,
            "q": ids,
        }
    )


class _AnswersStateMixin:
    """Selora AI Local — deterministic state / inventory / measurement question handlers."""

    def _maybe_single_state_envelope(self) -> str | None:
        """If the current turn is a single-device state question ("is the kitchen plug on?"), return a JSON envelope answering it deterministically from ``hass.states``."""
        # Same per-turn gate as the state-filter override: ``_call_kind`` is reset to None at end-of-stream before this runs, so a chat_command turn that incidentally contains "is … on?" must not have its command envelope replaced by a stub answer.
        if self._chat_kind.get() != "chat_answer":
            return None
        return self._single_state_answer_envelope()

    def _single_state_answer_envelope(self) -> str | None:
        """Resolve a single-device state question ("is the kitchen plug on?") to a deterministic ``Yes/No, <name> is <state>.`` envelope from ``hass.states`` — WITHOUT the ``chat_answer`` gate."""
        detected = _detect_single_state_question(self._user_message_raw.get() or "")
        if detected is None:
            return None
        subject, state_word = detected
        domains = _STATE_WORD_TO_QUERY_DOMAINS.get(state_word)
        if not domains:
            return None
        subject_tokens = {
            t
            for t in re.split(r"[^a-z0-9]+", subject.lower())
            if t and t not in _SINGLE_STATE_SUBJECT_STOPWORDS
        }
        if not subject_tokens:
            return None

        # Resolve the named device: require EVERY subject token to appear in the candidate's friendly_name/slug (so "kitchen plug" never resolves to an unrelated "kitchen light"), and among the matches prefer the most specific one — fewest extra tokens.
        def _best_match(states_by_domain: list[tuple[str, Any]]) -> tuple[Any, str, int] | None:
            acc: tuple[Any, str, int] | None = None
            for domain, state in states_by_domain:
                fname = str((state.attributes or {}).get("friendly_name") or "")
                slug = state.entity_id.split(".", 1)[-1]
                cand_tokens = {t for t in re.split(r"[^a-z0-9]+", f"{fname} {slug}".lower()) if t}
                if not subject_tokens <= cand_tokens:
                    continue
                extra = len(cand_tokens - subject_tokens)
                if acc is None or extra < acc[2]:
                    acc = (state, domain, extra)
            return acc

        best = _best_match([(d, s) for d in domains for s in self._filtered_domain_states(d)])
        # Fallback: if the post-filter view yielded no token match (the named device was dropped by EntityFilter/exclude-label in this home), retry against raw ``hass.states`` so a single, unambiguously-named device is still answered deterministically rather than handed to the LoRA (which emits an unsubstitutable / truncated ``{domain.slug}`` placeholder).
        if best is None and self._hass is not None:
            raw: list[tuple[str, Any]] = []
            for s in self._hass.states.async_all():
                eid = s.entity_id
                if "." not in eid:
                    continue
                dom = eid.split(".", 1)[0]
                if dom in domains:
                    raw.append((dom, s))
            best = _best_match(raw)
        if best is None:
            return None
        state, domain, _ = best
        live = (state.state or "").lower()
        if not live or live in ("unknown", "unavailable"):
            return None
        # The asked word maps to the natural live state for every domain in ``_STATE_WORD_TO_QUERY_DOMAINS`` (state literally equals the word), so fall back to ``{state_word}`` when a domain has no explicit translation table entry.
        accepted = _NATURAL_STATE_BY_DOMAIN.get(domain, {}).get(state_word) or {state_word}
        fname = str((state.attributes or {}).get("friendly_name") or state.entity_id)
        safe_fname = _safe_fname_for_prose(fname)
        marker = f"\n[[entities:{state.entity_id}]]"
        if live in accepted:
            r_text = f"Yes, {safe_fname} is {state_word}.{marker}"
        else:
            r_text = f"No, {safe_fname} is {live}.{marker}"
        return _answer(r_text, [state.entity_id])
