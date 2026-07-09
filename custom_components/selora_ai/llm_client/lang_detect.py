"""Lightweight conversational-language detection for the shipped locales.

The chat panel sends the user's HA *UI* locale as the request language,
which can differ from the language the user actually types in. The LLM
prose naturally mirrors the typed language, but the deterministic
command-confirmation builder (and the reply-language directive) keyed off
the panel locale — so a French command on an English-UI install came back
with an English confirmation.

This detector resolves the language from the message itself, over the
shipped conversational locales (fr/de/es/it), with no external dependency.
It scores the normalized tokens against per-language high-signal marker
sets and returns a base code only when there is a clear, unambiguous
winner; otherwise it returns ``None`` and the caller falls back to the
panel locale. English carries no marker set — an English message scores
zero everywhere and falls back, which is correct.

Markers are split into two tiers so a single hit is trusted only when it
carries real signal:

- **Strong** markers (``_STRONG_MARKERS``): imperative command verbs and
  accented interrogatives ("apaga", "éteins", "accendi", "welche"). None
  is a plausible English word, so a lone hit is unambiguous — this keeps
  short verb-plus-entity commands like "apaga el ventilador" detected
  even when the device name and article are not in the marker list.
- **Weak** markers: bare articles and plain nouns ("camera", "die",
  "los", "che") that collide with English words. A lone weak hit is far
  more likely a false friend than real signal, so it needs a second hit
  to corroborate before it can flip an English conversation.
"""

from __future__ import annotations

from ..lexical import normalize

# Distinctive, high-signal tokens per locale: articles, common verbs,
# interrogatives, accented forms. Chosen to minimize cross-language
# collision — ultra-common shared Romance words ("la", "el") are omitted
# in favour of verbs/interrogatives that disambiguate es vs it.
_MARKERS: dict[str, frozenset[str]] = {
    "fr": frozenset(
        {
            "le",
            "les",
            "des",
            "une",
            "aux",
            "est",
            "sont",
            "dans",
            "avec",
            "pour",
            "mais",
            "donc",
            "très",
            "être",
            "fais",
            "mets",
            "quelles",
            "quel",
            "quelle",
            "quels",
            "allume",
            "allumes",
            "allumée",
            "allumées",
            "éteins",
            "éteint",
            "éteintes",
            "lumière",
            "lumières",
            "chambre",
            "cuisine",
            "salon",
            "ferme",
            "ouvre",
            "ceci",
            "cela",
        }
    ),
    "de": frozenset(
        {
            "das",
            "die",
            "der",
            "ein",
            "eine",
            "und",
            "oder",
            "ist",
            "sind",
            "nicht",
            "mit",
            "auf",
            "aus",
            "welche",
            "welcher",
            "welches",
            "mach",
            "schalte",
            "licht",
            "lichter",
            "zimmer",
            "küche",
            "wohnzimmer",
            "schlafzimmer",
            "bitte",
            "kannst",
            "wie",
            "ich",
            "den",
            "dem",
        }
    ),
    "es": frozenset(
        {
            "los",
            "las",
            "unos",
            "unas",
            "del",
            "está",
            "están",
            "qué",
            "cuál",
            "cuáles",
            "enciende",
            "apaga",
            "luz",
            "luces",
            "cocina",
            "salón",
            "dormitorio",
            "habitación",
            "pero",
            "muy",
            "hace",
            "pon",
            "encendidas",
            "encendido",
            "apagado",
            "para",
            "por",
        }
    ),
    "it": frozenset(
        {
            "gli",
            "uno",
            "della",
            "dello",
            "sono",
            "quale",
            "quali",
            "accendi",
            "spegni",
            "luce",
            "luci",
            "cucina",
            "salotto",
            "camera",
            "cameretta",
            "molto",
            "fai",
            "metti",
            "questa",
            "questo",
            "queste",
            "accese",
            "acceso",
            "spento",
            "spenta",
            "che",
        }
    ),
}


# High-signal subset of ``_MARKERS``: command verbs and accented
# interrogatives that carry no English false-friend risk. A lone hit here
# is enough to detect (see module docstring). Must stay a subset of
# ``_MARKERS`` — asserted below so the two can't drift apart.
_STRONG_MARKERS: dict[str, frozenset[str]] = {
    "fr": frozenset(
        {
            "allume",
            "allumes",
            "allumée",
            "allumées",
            "éteins",
            "éteint",
            "éteintes",
            "ferme",
            "ouvre",
            "fais",
            "mets",
            "quelles",
            "quel",
            "quelle",
            "quels",
            "très",
        }
    ),
    "de": frozenset(
        {
            "schalte",
            "mach",
            "kannst",
            "welche",
            "welcher",
            "welches",
            "lichter",
            "küche",
            "wohnzimmer",
            "schlafzimmer",
        }
    ),
    "es": frozenset(
        {
            "enciende",
            "apaga",
            "para",  # imperative "stop" ("para la música"); not English prose
            "pon",
            "encendidas",
            "encendido",
            "apagado",
            "está",
            "están",
            "qué",
            "cuál",
            "cuáles",
            "salón",
        }
    ),
    "it": frozenset(
        {
            "accendi",
            "spegni",
            "fai",
            "metti",
            "quale",
            "quali",
            "accese",
            "acceso",
            "spento",
            "spenta",
            "salotto",
            "cameretta",
        }
    ),
}

assert all(_STRONG_MARKERS[lang] <= _MARKERS[lang] for lang in _STRONG_MARKERS), (
    "_STRONG_MARKERS must be a subset of _MARKERS"
)

# Distinct hits a weak-only match needs before it is trusted. One weak
# hit is indistinguishable from an English false friend (see docstring).
_MIN_WEAK_SCORE = 2


def detect_language(text: str) -> str | None:
    """Return the base locale code (``fr``/``de``/``es``/``it``) the message
    is written in, or ``None`` when undetected or ambiguous.

    The top-scoring locale wins only when its evidence beats a lone false
    friend: either one **strong** marker hit (an unambiguous command verb
    or accented interrogative) or at least ``_MIN_WEAK_SCORE`` total hits.
    Locales rank by (strong hits, total hits) so a strong command verb
    outranks an incidental weak collision; a tie on that key returns
    ``None`` so the caller keeps the panel locale rather than guessing.
    """
    if not text:
        return None
    tokens = set(normalize(text).split())
    if not tokens:
        return None
    # (strong hits, total hits) per locale — strong hits dominate ranking.
    scores = {
        lang: (
            len(tokens & _STRONG_MARKERS.get(lang, frozenset())),
            len(tokens & markers),
        )
        for lang, markers in _MARKERS.items()
    }
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_lang, (best_strong, best_total) = ranked[0]
    if best_total == 0:
        return None
    if len(ranked) > 1 and ranked[1][1] == (best_strong, best_total):
        return None  # tie — ambiguous
    if best_strong == 0 and best_total < _MIN_WEAK_SCORE:
        return None  # lone weak hit — likely an English false friend
    return best_lang


def resolve_reply_language(
    message: str,
    panel_language: str | None,
    fallback: str | None,
) -> str | None:
    """Resolve the language to reply/confirm in.

    Prefers the language detected from ``message`` (so a French command on
    an English-UI install gets a French confirmation), then the panel
    locale, then the HA config fallback.
    """
    return detect_language(message) or panel_language or fallback
