"""Cheap regex heuristics for chat intent classification and entity filtering.

Used by the low-context provider path (e.g. SeloraLocal add-on, max_seq=1024)
to pre-classify intent before the LLM call, and to filter the AVAILABLE
ENTITIES list down to the ones the message might be about. Cloud providers
self-classify in their long system prompt instead.
"""

from __future__ import annotations

import re

from ..types import EntitySnapshot

# Aggressive caps used when the provider has a tight context window
# (provider.is_low_context). Small enough to fit the system prompt + a
# trimmed entity list inside ~700 tokens.
_LOW_CONTEXT_MAX_ENTITIES = 15

_CLOUD_MAX_ENTITIES = 200
_LOW_CONTEXT_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "to",
        "of",
        "in",
        "on",
        "at",
        "for",
        "and",
        "or",
        "but",
        "with",
        "from",
        "by",
        "my",
        "your",
        "i",
        "me",
        "you",
        "we",
        "us",
        "they",
        "them",
        "it",
        "do",
        "does",
        "did",
        "don",
        "doesn",
        "didn",
        "can",
        "could",
        "would",
        "should",
        "will",
        "shall",
        "may",
        "might",
        "have",
        "has",
        "had",
        "please",
        "thanks",
        "thank",
        "hi",
        "hey",
        "hello",
        "what",
        "where",
        "when",
        "how",
        "why",
        "which",
        "who",
        "that",
        "this",
        "those",
        "these",
        "as",
        "if",
        "then",
        "now",
        "just",
    }
)


def _low_context_keywords(user_message: str) -> set[str]:
    """Extract content tokens from a user message for entity filtering."""
    out: set[str] = set()
    for raw in re.split(r"[^a-z0-9]+", user_message.lower()):
        if len(raw) > 2 and raw not in _LOW_CONTEXT_STOPWORDS:
            out.add(raw)
    return out


# Pre-classifier patterns for low-context chat routing. Cheap regex
# heuristics — good enough to pick the right LoRA specialist BEFORE we
# call it. Order matters: automation patterns are checked before command
# verbs because rules like "every morning turn on the lights" contain
# both. Anything unrecognised falls through to "command" (default LoRA).
_AUTOMATION_PATTERNS = (
    re.compile(r"\b(automate|automation|schedule|schedul(ed|ing))\b"),
    re.compile(
        r"\bevery\s+(day|morning|night|evening|afternoon|hour|minute|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"weekday|weekend)\b",
    ),
    re.compile(r"\b(when|whenever|if)\b.{0,40}\b(then|do|turn|start|stop|set|send|notify|alert)\b"),
    re.compile(r"\b(at|after|before)\s+\d"),
    re.compile(r"\bremind me\b"),
    re.compile(r"\bcreate (an?|the)?\s*automation\b"),
)
_QUESTION_OPENER = re.compile(
    r"^(what|where|when|why|how|which|who|is|are|was|were|do|does|did|"
    r"can|could|should|will|would|tell me|show me|list|give me)\b"
)
_GREETING_OPENER = re.compile(
    r"^(hi|hello|hey|yo|sup|thanks|thank you|cheers|"
    r"good (morning|evening|night|afternoon))\b"
)
# Greeting + optional emoji / punctuation, nothing else. Used to short-
# circuit "hello" / "thanks" / "good morning :)" with a canned reply
# before we ever build a system prompt — the LLM consistently ignores
# the small-talk rule and dumps a status report instead.
_PURE_GREETING = re.compile(
    r"^\s*(hi|hello|hey|yo|sup|thanks|thank you|thx|cheers|"
    r"good (morning|evening|night|afternoon))"
    # Optional vocative addressing the assistant by name — "hello selora",
    # "hi selora ai", "thanks ai". Without this the LLM still gets the
    # message and hallucinates an automation update from prior history.
    r"(\s+(selora(\s+ai)?|ai|assistant))?"
    # Trailing whitespace, common punctuation/smileys, and emoji from
    # the dingbats / symbols / pictographs blocks plus the variation
    # selector. Anything alphanumeric ends the match — actual content
    # routes to the LLM.
    r"[\s!.,?:;()’‘☀-➿️\U0001F300-\U0001FAFF]*$",
    re.IGNORECASE,
)


def _is_pure_greeting(message: str) -> bool:
    """Return True when ``message`` is just a greeting/thanks with no request."""
    if not message:
        return False
    text = message.strip()
    if not text or len(text) > 40:
        return False
    return bool(_PURE_GREETING.match(text))


_COMMAND_VERB = re.compile(
    r"\b(turn|switch|toggle|set|start|stop|play|pause|resume|open|close|"
    r"lock|unlock|dim|brighten|increase|decrease|raise|lower|"
    r"activate|deactivate|enable|disable|run|trigger)\b"
)

# Meta-questions about capabilities ("suggest an automation", "what
# automations can you create", "tell me about scenes") need to route
# to the answer specialist — the automation/command specialists try
# to *do* the thing, not describe it. Without this gate, "Cool, can
# you suggest an automation for me?" goes to the automation LoRA,
# which produces a hallucinated automation JSON the validator rejects
# with "each trigger must include a platform". The gate is checked
# before _AUTOMATION_PATTERNS so legitimate "every morning turn on …"
# requests still reach the automation specialist.
_META_QUESTION = re.compile(
    r"\b(suggest|recommend|propose|examples?)\b"
    r"|\b(tell|show|describe|explain)\s+(me|us)\b"
    r"|\b(what|which)\s+(kinds?|types?|examples?|automations?|commands?|scenes?|things)\b",
    re.IGNORECASE,
)


# Strict predicate for the cloud automation-spinner sentinel. The broad
# ``_classify_chat_intent`` returns ``automation`` for one-shot delayed
# commands too ("after 5 min", "at 11 PM", "remind me in 10 min") because
# the automation LoRA also handles delayed_command on the low-context
# path. Emitting the ```automation fence for those turns leaves the panel
# stuck on "Building automation..." when the stream returns a
# ``delayed_command`` block instead.
_DEFINITE_AUTOMATION = re.compile(
    r"\b(automate|automation|schedule[ds]?|scheduling)\b"
    r"|\bevery\s+(day|morning|night|evening|afternoon|hour|minute|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekday|weekend)\b"
    r"|\b(when|whenever|if)\b.{0,40}\b(then|do|turn|start|stop|set|send|notify|alert)\b"
    r"|\bcreate (an?|the)?\s*automation\b",
    re.IGNORECASE,
)


def _is_definite_automation(user_message: str) -> bool:
    """True only for recurring/conditional automation phrasing.

    Excludes one-shot delayed commands which still route to the
    automation LoRA via ``_classify_chat_intent`` but return a
    ``delayed_command`` block.
    """
    return bool(_DEFINITE_AUTOMATION.search(user_message))


def _classify_chat_intent(user_message: str) -> str:
    """Cheap regex pre-classifier for low-context LoRA routing.

    Returns one of ``command`` / ``automation`` / ``answer`` /
    ``clarification``. Used only when ``provider.is_low_context`` —
    cloud providers self-classify in their long system prompt instead.
    """
    msg = user_message.lower().strip()
    if not msg:
        return "answer"
    if _META_QUESTION.search(msg):
        return "answer"
    for pat in _AUTOMATION_PATTERNS:
        if pat.search(msg):
            return "automation"
    if _GREETING_OPENER.match(msg):
        return "answer"
    if _QUESTION_OPENER.match(msg):
        return "answer"
    if _COMMAND_VERB.search(msg):
        return "command"
    return "command"


# Controllable surface preferred when the user message has no usable
# keywords or no entity matched any of them ("turn it off", "what's
# on?"). Without a fallback we'd hand the low-context LoRA an empty
# AVAILABLE ENTITIES block — it then hallucinates entity_ids or echoes
# the prior automation. Order tracks user-facing relevance: lights
# first because they're the most-controlled surface in a typical home.
_LOW_CONTEXT_FALLBACK_DOMAINS: tuple[str, ...] = (
    "light",
    "switch",
    "cover",
    "lock",
    "climate",
    "media_player",
    "fan",
    "vacuum",
    "scene",
)

# Per-keyword scoring weights for relevance ranking. Calibrated so a
# single exact-word friendly_name match (5) outranks any number of
# substring hits in unrelated fields, and an entity_id match (3) beats
# a generic area match (1).
_SCORE_FNAME_TOKEN_HIT = 5
_SCORE_ENTITY_ID_HIT = 3
_SCORE_FNAME_SUBSTRING_HIT = 2
_SCORE_AREA_HIT = 1


def _score_entity_against_keywords(entity: EntitySnapshot, keywords: set[str]) -> int:
    """Relevance score: higher when the entity matches more keywords
    more strongly. Used to rank candidates so the most likely-intended
    ones survive the low-context cap.

    Without this, ``_filter_entities_by_keywords`` returned the first N
    string-contains matches in entity order. For a request like "turn
    on the porch light" on a 200-entity install with 30 entities whose
    name contains "light", that meant the 15 lights with the smallest
    entity_id won the cap — not the *porch* light. Scoring fixes the
    selection without raising the cap.
    """
    if not keywords:
        return 0
    eid = entity.get("entity_id", "").lower()
    # Strip the leading domain so "light.kitchen" doesn't match keyword
    # "light" via the domain prefix — the user almost never means "any
    # light", they mean a specific one.
    eid_local = eid.split(".", 1)[-1] if "." in eid else eid
    fname = str(entity.get("attributes", {}).get("friendly_name", "")).lower()
    fname_tokens = set(re.split(r"[^a-z0-9]+", fname)) - {""}
    area = (entity.get("area_name") or "").lower()

    score = 0
    for kw in keywords:
        if kw in fname_tokens:
            score += _SCORE_FNAME_TOKEN_HIT
        elif kw in fname:
            score += _SCORE_FNAME_SUBSTRING_HIT
        if kw in eid_local:
            score += _SCORE_ENTITY_ID_HIT
        if area and kw in area:
            score += _SCORE_AREA_HIT
    return score


def _fallback_low_context_entities(
    entities: list[EntitySnapshot],
    *,
    cap: int,
) -> list[EntitySnapshot]:
    """Return up to ``cap`` controllable entities, prioritised by domain.

    Used when keyword filtering produced no hits — better to give the
    LoRA *some* context than an empty AVAILABLE ENTITIES block.
    """
    if cap <= 0:
        return []
    by_domain: dict[str, list[EntitySnapshot]] = {d: [] for d in _LOW_CONTEXT_FALLBACK_DOMAINS}
    for e in entities:
        eid = e.get("entity_id", "")
        if "." not in eid:
            continue
        domain = eid.split(".", 1)[0]
        if domain in by_domain:
            by_domain[domain].append(e)
    out: list[EntitySnapshot] = []
    for domain in _LOW_CONTEXT_FALLBACK_DOMAINS:
        for e in by_domain[domain]:
            out.append(e)
            if len(out) >= cap:
                return out
    return out


def _filter_entities_by_keywords(
    entities: list[EntitySnapshot],
    keywords: set[str],
    *,
    cap: int,
) -> list[EntitySnapshot]:
    """Rank entities by relevance to the user's keywords, return top ``cap``.

    Falls back to a small slice of controllable entities (lights,
    switches, covers, etc.) when the user message has no content
    keywords OR no entity matched any of them — handing the LoRA an
    empty AVAILABLE ENTITIES block makes it hallucinate entity_ids or
    echo the prior automation, so a small canonical surface is
    strictly better than nothing.
    """
    if keywords:
        scored: list[tuple[int, int, EntitySnapshot]] = []
        for idx, e in enumerate(entities):
            s = _score_entity_against_keywords(e, keywords)
            if s > 0:
                scored.append((s, idx, e))
        if scored:
            # Higher score first; original index as the deterministic
            # tiebreaker so the same prompt always produces the same
            # entity list (no flaky training-format prefix).
            scored.sort(key=lambda t: (-t[0], t[1]))
            return [e for _, _, e in scored[:cap]]
    return _fallback_low_context_entities(entities, cap=cap)
