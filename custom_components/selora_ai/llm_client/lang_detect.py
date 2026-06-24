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


def detect_language(text: str) -> str | None:
    """Return the base locale code (``fr``/``de``/``es``/``it``) the message
    is written in, or ``None`` when undetected or ambiguous.

    A clear winner requires a non-zero score that strictly beats every
    other locale; a tie returns ``None`` so the caller keeps the panel
    locale rather than guessing.
    """
    if not text:
        return None
    tokens = set(normalize(text).split())
    if not tokens:
        return None
    scores = {lang: len(tokens & markers) for lang, markers in _MARKERS.items()}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_lang, best_score = ranked[0]
    if best_score == 0:
        return None
    if len(ranked) > 1 and ranked[1][1] == best_score:
        return None  # tie — ambiguous
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
