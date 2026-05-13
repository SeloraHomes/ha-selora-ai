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


def _classify_chat_intent(user_message: str) -> str:
    """Cheap regex pre-classifier for low-context LoRA routing.

    Returns one of ``command`` / ``automation`` / ``answer`` /
    ``clarification``. Used only when ``provider.is_low_context`` —
    cloud providers self-classify in their long system prompt instead.
    """
    msg = user_message.lower().strip()
    if not msg:
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


def _filter_entities_by_keywords(
    entities: list[EntitySnapshot],
    keywords: set[str],
    *,
    cap: int,
) -> list[EntitySnapshot]:
    """Keep entities whose id, friendly_name, or area mentions any keyword."""
    if not keywords:
        return []
    kept: list[EntitySnapshot] = []
    for e in entities:
        haystack = " ".join(
            [
                e.get("entity_id", ""),
                str(e.get("attributes", {}).get("friendly_name", "")),
                e.get("area_name", "") or "",
            ]
        ).lower()
        if any(kw in haystack for kw in keywords):
            kept.append(e)
            if len(kept) >= cap:
                break
    return kept
