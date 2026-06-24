"""Shared lexical normalization and fuzzy-matching primitives.

Entity/area/automation resolution across the integration matches
natural-language input against candidate strings (friendly names,
slugs, aliases, areas). Historically each call site rolled its own
``str.lower()`` + substring/term-count scoring, which mishandles
accented and full-width Unicode (the shipped fr/de/es/it locales) and
has no tolerance for typos or reordered words.

This module centralizes two primitives, applied identically to user
input and candidates for a fair comparison:

- :func:`normalize` — NFKC normalization, casefold, punctuation/underscore
  stripping, whitespace collapse.
- :func:`fuzzy_ratio` — order-insensitive token-set similarity in
  ``[0.0, 1.0]`` via RapidFuzz, with a difflib fallback when RapidFuzz
  is unavailable (kept soft so the package imports in minimal envs).

Scoring weights live here as tunable module constants rather than being
scattered across call sites.
"""

from __future__ import annotations

import re
import unicodedata

try:
    from rapidfuzz import fuzz as _rf_fuzz

    _HAVE_RAPIDFUZZ = True
except ImportError:  # pragma: no cover - platform-dependent import guard
    _rf_fuzz = None
    _HAVE_RAPIDFUZZ = False

# Drop anything that is neither a Unicode word char nor whitespace, plus
# underscores (slug separators carry no lexical meaning in NL input).
_STRIP_RE = re.compile(r"[^\w\s]|_", re.UNICODE)
_WS_RE = re.compile(r"\s+")

# ── selora_search_entities ranking (mcp_server._tool_search_entities) ──
# Final rank = SEARCH_W_TERM_RATIO * (fraction of query terms present)
#            + SEARCH_W_FUZZY * token-set fuzzy ratio against the haystack.
# A candidate with no literal term hits is kept only when its fuzzy ratio
# clears SEARCH_FUZZY_FLOOR (catches typos like "kitchne" → "kitchen").
SEARCH_W_TERM_RATIO = 0.6
SEARCH_W_FUZZY = 0.4
SEARCH_FUZZY_FLOOR = 0.7

# ── last-ditch keyword entity picker (parsers._prompt_keyword_best_entity) ──
KW_W_OVERLAP = 1.0  # weight on (shared content tokens / prompt tokens)
KW_W_FUZZY = 0.5  # weight on best token-set fuzzy ratio (fname vs slug)
KW_W_HINT = 0.3  # bonus when the candidate domain matches domain_hint
KW_HELPER_PENALTY = 0.2  # penalty for helper-class (non-real-device) domains
KW_FUZZY_FLOOR = 0.6  # min fuzzy ratio to consider a zero-overlap candidate
# Required score gap between the top candidate and the next distinct one.
# Below this the match is treated as ambiguous and refused (falls through
# to clarification) rather than guessed by sort order.
KW_MIN_MARGIN = 0.08


def normalize(text: str) -> str:
    """NFKC-normalize, casefold, strip punctuation/underscores, collapse
    whitespace.

    ``casefold`` (not ``lower``) is used so Unicode case folding is
    correct (e.g. German ``ß`` → ``ss``). Underscores become spaces so
    entity slugs (``living_room``) tokenize like natural language.
    """
    if not text:
        return ""
    norm = unicodedata.normalize("NFKC", text).casefold()
    norm = _STRIP_RE.sub(" ", norm)
    return _WS_RE.sub(" ", norm).strip()


def _difflib_ratio(query: str, candidate: str) -> float:
    """Order-insensitive similarity fallback used when RapidFuzz is absent.

    Approximates RapidFuzz ``token_set_ratio``: tokenizes both inputs,
    compares the sorted token sets (so reordered words score the same)
    and keeps a sequence-level comparison so typos still rank high. Keeps
    the integration functional on platforms where the RapidFuzz wheel
    fails to install.
    """
    from difflib import SequenceMatcher

    q_tokens = sorted(query.split())
    c_tokens = sorted(candidate.split())
    sorted_ratio = SequenceMatcher(None, " ".join(q_tokens), " ".join(c_tokens)).ratio()
    raw_ratio = SequenceMatcher(None, query, candidate).ratio()
    return max(sorted_ratio, raw_ratio)


def fuzzy_ratio(query: str, candidate: str) -> float:
    """Order-insensitive lexical similarity in ``[0.0, 1.0]``.

    Uses RapidFuzz ``token_set_ratio`` so reordered/partial inputs score
    high ("kitchen light" vs "Light, Kitchen"). Inputs should already be
    :func:`normalize`-d. Falls back to :func:`_difflib_ratio` when
    RapidFuzz is not installed.
    """
    if not query or not candidate:
        return 0.0
    if _HAVE_RAPIDFUZZ:
        return _rf_fuzz.token_set_ratio(query, candidate) / 100.0
    return _difflib_ratio(query, candidate)
